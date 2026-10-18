"""Emailed status reports for project managers, written by Gemini.

Gathers what the backend knows about a set of sites (latest conditions, hazard scores, active
alerts, recent crew hazard reports, FEMA flood risk), asks Gemini to summarize it for a project
manager, and emails the result. Each manager gets one report covering every site they manage
(site_managers); extra addresses get a report on the requested sites. Recipients with the same
sites share one Gemini call.

Without GEMINI_API_KEY, or if Gemini fails, the email carries a plain built-in summary instead,
so reports still go out.
"""

import asyncio
import json
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.services.email import DeliveryResult, OutgoingEmail, send_emails

logger = logging.getLogger("sitesentinel.reports")

GEMINI_TIMEOUT = httpx.Timeout(90.0, connect=10.0)
GEMINI_RETRIES = 3  # timeouts, 429 and 5xx only
REPORTS_PER_SITE = 10  # most recent crew hazard reports included per site

SYSTEM_PROMPT = """\
You write short status reports for project managers of utility job sites (power plants,
substations, solar farms). You are given JSON describing their sites: the latest weather
conditions, today's and upcoming hazard scores (0 to 10, higher is more dangerous) with their top
factors, active alerts ("stop_work" is the most severe), recent crew hazard reports and FEMA flood
risk.

Write the body of an email in plain text (no Markdown, no asterisks or pound signs):
1. Start with two or three sentences on the overall picture.
2. Then "Needs attention": the sites with stop-work alerts, warnings, hazard scores of 6 or more,
   or recent crew reports, most urgent first, one short paragraph each saying what is happening
   and why it matters.
3. Then "All clear": a single line listing the remaining sites by name, if any.
Use only the data given; never invent numbers, sites or events. Say when data is missing (e.g. no
recent conditions). Use US units as given. Keep it under 400 words. Do not add a greeting or a
sign-off."""

SITES_QUERY = text("""
    SELECT s.id, s.name, s.utility, s.plant_type, s.county, s.state, s.timezone, s.near_river,
           r.fema_mapped, r.zone_at_site, r.site_in_sfha, r.nearest_sfha_mi
    FROM sites s
    LEFT JOIN site_flood_risk r ON r.site_id = s.id
    WHERE s.id = ANY(:ids)
    ORDER BY s.name
""")
CONDITIONS_QUERY = text("""
    SELECT DISTINCT ON (site_id) site_id, observed_at, temp_f, heat_index_f, precip_24h_in,
           soil_moisture, wind_mph, wind_gust_mph, humidity_pct
    FROM conditions
    WHERE site_id = ANY(:ids)
    ORDER BY site_id, observed_at DESC
""")
SCORES_QUERY = text("""
    SELECT site_id, score_date, score, is_forecast, top_factors
    FROM hazard_scores
    WHERE site_id = ANY(:ids) AND score_date >= CURRENT_DATE
    ORDER BY site_id, score_date
""")
ALERTS_QUERY = text("""
    SELECT site_id, type, severity, message, started_at
    FROM alerts
    WHERE site_id = ANY(:ids) AND cleared_at IS NULL
    ORDER BY site_id, started_at DESC
""")
REPORTS_QUERY = text("""
    SELECT site_id, hazard_type, description, reported_by, created_at
    FROM (
        SELECT *, row_number() OVER (PARTITION BY site_id ORDER BY created_at DESC) AS n
        FROM hazard_reports
        WHERE site_id = ANY(:ids) AND created_at >= now() - make_interval(days => :days)
    ) recent
    WHERE n <= :per_site
    ORDER BY site_id, created_at DESC
""")
MANAGERS_QUERY = text("""
    SELECT email, site_id FROM site_managers
    WHERE CAST(:ids AS text[]) IS NULL OR site_id = ANY(:ids)
    ORDER BY email, site_id
""")
ALL_SITES_QUERY = text("SELECT id FROM sites ORDER BY id")


class GeminiError(Exception):
    pass


@dataclass
class Report:
    site_ids: list[str]
    recipients: list[str]
    subject: str
    body: str
    summary_source: str  # 'gemini' | 'fallback'
    summary_error: str | None = None
    deliveries: list[DeliveryResult] = field(default_factory=list)


def _row(mapping: Any) -> dict[str, Any]:
    return {k: v for k, v in dict(mapping).items() if k != "site_id"}


async def gather_site_data(
    session: AsyncSession, site_ids: list[str], days: int
) -> list[dict[str, Any]]:
    """Everything a report says about each site, one dict per site, ordered by name."""
    ids = {"ids": site_ids}
    sites = [dict(r) for r in (await session.execute(SITES_QUERY, ids)).mappings()]
    by_site: dict[str, dict[str, Any]] = {}
    for site in sites:
        site.update(latest_conditions=None, hazard_scores=[], active_alerts=[], crew_reports=[])
        by_site[site["id"]] = site
    for r in (await session.execute(CONDITIONS_QUERY, ids)).mappings():
        by_site[r["site_id"]]["latest_conditions"] = _row(r)
    for r in (await session.execute(SCORES_QUERY, ids)).mappings():
        by_site[r["site_id"]]["hazard_scores"].append(_row(r))
    for r in (await session.execute(ALERTS_QUERY, ids)).mappings():
        by_site[r["site_id"]]["active_alerts"].append(_row(r))
    params = {**ids, "days": days, "per_site": REPORTS_PER_SITE}
    for r in (await session.execute(REPORTS_QUERY, params)).mappings():
        by_site[r["site_id"]]["crew_reports"].append(_row(r))
    return sites


async def summarize_with_gemini(
    client: httpx.AsyncClient, settings: Settings, sites: list[dict[str, Any]], days: int
) -> str:
    """Plain-text report body written by Gemini. Raises GeminiError."""
    if not settings.gemini_api_key:
        raise GeminiError("Gemini is not configured (set GEMINI_API_KEY)")
    data = {
        "generated_at": datetime.now(UTC).isoformat(timespec="minutes"),
        "crew_reports_cover_last_days": days,
        "sites": sites,
    }
    payload = {
        "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": [{"role": "user", "parts": [{"text": json.dumps(data, default=str)}]}],
        "generationConfig": {"temperature": 0.3},
    }
    url = f"{settings.gemini_api_url.rstrip('/')}/models/{settings.gemini_model}:generateContent"
    headers = {"x-goog-api-key": settings.gemini_api_key}
    for attempt in range(GEMINI_RETRIES):
        try:
            response = await client.post(url, json=payload, headers=headers, timeout=GEMINI_TIMEOUT)
        except httpx.TransportError as exc:
            error = f"Gemini request failed: {exc!r}"
        else:
            if response.status_code == 429 or response.status_code >= 500:
                error = f"Gemini returned HTTP {response.status_code}"
            elif response.status_code != 200:
                raise GeminiError(
                    f"Gemini returned HTTP {response.status_code}: {_detail(response)}"
                )
            else:
                return _text(response)
        if attempt < GEMINI_RETRIES - 1:
            await asyncio.sleep(2**attempt)
    raise GeminiError(error)


def _detail(response: httpx.Response) -> str:
    try:
        return response.json()["error"]["message"]
    except (ValueError, KeyError, TypeError):
        return response.text[:200]


def _text(response: httpx.Response) -> str:
    try:
        candidate = response.json()["candidates"][0]
        parts = candidate["content"]["parts"]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise GeminiError(f"Gemini returned no text: {response.text[:200]}") from exc
    body = "".join(p.get("text", "") for p in parts if not p.get("thought")).strip()
    if not body:
        raise GeminiError(f"Gemini returned an empty report ({candidate.get('finishReason')})")
    return body


def fallback_summary(sites: list[dict[str, Any]]) -> str:
    """Plain summary used when Gemini isn't available."""
    lines = []
    for site in sites:
        place = ", ".join(p for p in (site.get("county"), site.get("state")) if p)
        lines.append(f"{site['name']}{f' ({place})' if place else ''}")
        for alert in site["active_alerts"]:
            label = "STOP WORK" if alert["severity"] == "stop_work" else "Warning"
            lines.append(f"  - {label} ({alert['type']}): {alert['message']}")
        for score in site["hazard_scores"][:2]:
            kind = "forecast" if score["is_forecast"] else "today"
            lines.append(
                f"  - Hazard score {score['score']:.1f}/10 on {score['score_date']} ({kind})"
            )
        c = site["latest_conditions"]
        if c:
            lines.append(
                f"  - Conditions at {c['observed_at']:%Y-%m-%d %H:%M} UTC: {_num(c['temp_f'])}F, "
                f"heat index {_num(c['heat_index_f'])}F, wind {_num(c['wind_mph'])} mph "
                f"(gusts {_num(c['wind_gust_mph'])}), "
                f"rain past 24h {_num(c['precip_24h_in'], 2)} in"
            )
        else:
            lines.append("  - No recorded conditions")
        if site["crew_reports"]:
            types = ", ".join(sorted({r["hazard_type"] for r in site["crew_reports"]}))
            lines.append(f"  - {len(site['crew_reports'])} recent crew report(s): {types}")
        if site.get("site_in_sfha"):
            lines.append(f"  - In a FEMA flood hazard area (zone {site.get('zone_at_site')})")
        lines.append("")
    return "\n".join(lines).strip()


def _num(value: float | None, digits: int = 0) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


async def report_recipients(
    session: AsyncSession, site_ids: list[str] | None, emails: list[str], notify_managers: bool
) -> dict[frozenset[str], list[str]]:
    """Group recipients by the sites their report covers: {site ids: [emails]}.

    Managers get the sites they manage (limited to `site_ids` when given); extra `emails` get
    `site_ids`, or every site.
    """
    sites_for: dict[str, set[str]] = defaultdict(set)
    if notify_managers:
        rows = await session.execute(MANAGERS_QUERY, {"ids": site_ids})
        for email, site_id in rows:
            sites_for[email].add(site_id)
    if emails:
        requested = set(site_ids) if site_ids else set(await session.scalars(ALL_SITES_QUERY))
        for email in emails:
            sites_for[email] |= requested
    groups: dict[frozenset[str], list[str]] = defaultdict(list)
    for email, ids in sites_for.items():
        if ids:
            groups[frozenset(ids)].append(email)
    return groups


async def build_report(
    session: AsyncSession,
    client: httpx.AsyncClient,
    settings: Settings,
    site_ids: frozenset[str],
    recipients: list[str],
    days: int,
) -> Report:
    sites = await gather_site_data(session, sorted(site_ids), days)
    try:
        body, source, error = (
            await summarize_with_gemini(client, settings, sites, days),
            "gemini",
            None,
        )
    except GeminiError as exc:
        logger.warning("Report summary fell back to the built-in one: %s", exc)
        body, source, error = fallback_summary(sites), "fallback", str(exc)
    today = datetime.now(UTC).strftime("%b %d, %Y").replace(" 0", " ")
    if len(sites) == 1:
        subject = f"SiteSentinel report: {sites[0]['name']} - {today}"
    else:
        subject = f"SiteSentinel report: {len(sites)} sites - {today}"
    footer = (
        "\n\n--\nSiteSentinel status report"
        + (
            " (summary written by Gemini; check the app before acting on it)"
            if source == "gemini"
            else ""
        )
        + f"\nSites: {', '.join(s['name'] for s in sites)}"
    )
    return Report(
        site_ids=[s["id"] for s in sites],
        recipients=sorted(recipients),
        subject=subject,
        body=body + footer,
        summary_source=source,
        summary_error=error,
    )


async def send_reports(
    session: AsyncSession,
    client: httpx.AsyncClient,
    settings: Settings,
    *,
    site_ids: list[str] | None,
    emails: list[str],
    notify_managers: bool,
    days: int,
    dry_run: bool,
) -> list[Report]:
    """Build and (unless dry_run) email the reports. With dry_run and nobody to send to, returns
    one preview report for `site_ids` (or every site)."""
    groups = await report_recipients(session, site_ids, emails, notify_managers)
    if not groups and dry_run:
        ids = site_ids or list(await session.scalars(ALL_SITES_QUERY))
        groups = {frozenset(ids): []} if ids else {}

    # One group at a time: a database session can't run queries concurrently, and it keeps
    # Gemini's free-tier rate limit happy.
    reports = []
    for ids, recipients in groups.items():
        reports.append(await build_report(session, client, settings, ids, recipients, days))

    if not dry_run:
        outgoing = [OutgoingEmail(to, r.subject, r.body) for r in reports for to in r.recipients]
        results = iter(await send_emails(settings, outgoing))
        for report in reports:
            report.deliveries = [next(results) for _ in report.recipients]
    return reports
