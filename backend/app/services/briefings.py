"""Next-day safety briefings.

For each site, combines the Open-Meteo forecast, the NWS forecast and active alerts, and the
FEMA flood summary into one JSON payload, then POSTs it to BRIEFING_WEBHOOK_URL.

- Scheduled: once per site per day, at BRIEFING_HOUR (default 18:00) in the site's own timezone,
  covering the next local day. A background loop checks every BRIEFING_CHECK_INTERVAL_S seconds,
  so briefings go out within a few minutes of 6 PM, and missed ones are caught up until midnight.
- Manual: send_now() sends immediately (POST /briefings/run) without affecting the scheduled one.
"""

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import text

from app.config import Settings, get_settings
from app.db import SessionLocal
from app.services.nws import NwsError, fetch_forecast
from app.services.open_meteo import OpenMeteoError, fetch_weather

logger = logging.getLogger("sitesentinel.briefings")

MAX_ATTEMPTS = 3  # scheduled sends retried on later checks until this many attempts
STALE_SENDING = timedelta(minutes=15)  # a 'sending' row older than this was abandoned by a crash
CONCURRENT_SITES = 5

SITES_QUERY = """
    SELECT s.id, s.name, s.utility, s.plant_type, s.county, s.state, s.timezone, s.near_river,
           ST_Y(s.geom) AS latitude, ST_X(s.geom) AS longitude,
           r.fema_mapped, r.zone_at_site, r.zone_subtype_at_site, r.site_in_sfha,
           r.nearest_sfha_mi, r.radius_mi, r.loaded_at
    FROM sites s
    JOIN site_flood_risk r ON r.site_id = s.id
"""


@dataclass
class SiteInfo:
    id: str
    name: str
    utility: str | None
    plant_type: str | None
    county: str | None
    state: str | None
    timezone: str
    near_river: bool | None
    latitude: float
    longitude: float
    flood: dict[str, Any]


@dataclass
class BriefingResult:
    site_id: str
    briefing_date: date
    status: str  # 'sent' | 'failed' | 'skipped'
    error: str | None
    briefing_id: int | None = None
    payload: dict[str, Any] | None = None


def next_day(now: datetime, tz: str) -> date:
    """The day a briefing generated at `now` covers: tomorrow in the site's timezone."""
    return now.astimezone(ZoneInfo(tz)).date() + timedelta(days=1)


def scheduled_briefing_date(now: datetime, tz: str, hour: int) -> date | None:
    """The date a scheduled briefing is due for, or None before `hour` site-local time."""
    local = now.astimezone(ZoneInfo(tz))
    return local.date() + timedelta(days=1) if local.hour >= hour else None


async def load_sites(site_ids: list[str] | None = None) -> list[SiteInfo]:
    query = SITES_QUERY + (" WHERE s.id = ANY(:ids)" if site_ids else "") + " ORDER BY s.id"
    async with SessionLocal() as session:
        rows = (await session.execute(text(query), {"ids": site_ids})).mappings().all()
    return [
        SiteInfo(
            **{k: row[k] for k in SiteInfo.__dataclass_fields__ if k != "flood"},
            flood={
                "fema_mapped": row["fema_mapped"],
                "zone_at_site": row["zone_at_site"],
                "zone_subtype_at_site": row["zone_subtype_at_site"],
                "site_in_sfha": row["site_in_sfha"],
                "nearest_sfha_mi": _float(row["nearest_sfha_mi"]),
                "radius_mi": row["radius_mi"],
                "loaded_at": row["loaded_at"].isoformat() if row["loaded_at"] else None,
            },
        )
        for row in rows
    ]


async def build_briefing(
    client: httpx.AsyncClient, settings: Settings, site: SiteInfo, briefing_date: date
) -> dict[str, Any]:
    """Gather every source for one site. A failing source is reported, not fatal."""
    open_meteo, nws = await asyncio.gather(
        fetch_weather(client, settings.open_meteo_url, site.latitude, site.longitude),
        fetch_forecast(
            client,
            settings.nws_url,
            settings.nws_user_agent,
            site.latitude,
            site.longitude,
            briefing_date,
        ),
        return_exceptions=True,
    )

    source_errors: dict[str, str] = {}
    weather: dict[str, Any] = {"open_meteo": None, "nws": None}
    if isinstance(open_meteo, OpenMeteoError):
        source_errors["open_meteo"] = str(open_meteo)
    elif isinstance(open_meteo, BaseException):
        raise open_meteo
    else:
        day = next((d for d in open_meteo.daily if d.date == briefing_date), None)
        weather["open_meteo"] = {
            "current": open_meteo.current.model_dump(mode="json"),
            "forecast": day.model_dump(mode="json") if day else None,
        }
    if isinstance(nws, NwsError):
        source_errors["nws"] = str(nws)
    elif isinstance(nws, BaseException):
        raise nws
    else:
        weather["nws"] = nws.model_dump(mode="json")

    return {
        "type": "next_day_briefing",
        "briefing_date": briefing_date.isoformat(),
        "generated_at": datetime.now(UTC).isoformat(),
        "site": {
            "id": site.id,
            "name": site.name,
            "utility": site.utility,
            "plant_type": site.plant_type,
            "county": site.county,
            "state": site.state,
            "timezone": site.timezone,
            "near_river": site.near_river,
            "latitude": site.latitude,
            "longitude": site.longitude,
        },
        "weather": weather,
        "flood": site.flood,
        "source_errors": source_errors,
    }


async def deliver(
    client: httpx.AsyncClient, settings: Settings, payload: dict[str, Any]
) -> tuple[str, str | None]:
    """POST the payload to the webhook. Returns (status, error)."""
    if not settings.briefing_webhook_url:
        return "skipped", "BRIEFING_WEBHOOK_URL is not set"
    headers = {}
    if settings.briefing_webhook_token:
        headers["Authorization"] = f"Bearer {settings.briefing_webhook_token}"
    try:
        response = await client.post(
            settings.briefing_webhook_url, json=payload, headers=headers, timeout=30.0
        )
    except httpx.HTTPError as exc:
        return "failed", f"Webhook unreachable: {exc!r}"
    if response.is_error:
        return "failed", f"Webhook returned HTTP {response.status_code}"
    return "sent", None


async def _build_and_deliver(
    client: httpx.AsyncClient, settings: Settings, site: SiteInfo, briefing_date: date
) -> tuple[str, str | None, dict[str, Any] | None]:
    try:
        payload = await build_briefing(client, settings, site, briefing_date)
    except Exception as exc:  # unexpected bug in a source; record it rather than lose the run
        logger.exception("Building briefing for site %s failed", site.id)
        return "failed", f"Could not build briefing: {exc!r}", None
    status, error = await deliver(client, settings, payload)
    return status, error, payload


async def _record(
    briefing_id: int, status: str, error: str | None, payload: dict[str, Any] | None
) -> None:
    async with SessionLocal() as session, session.begin():
        await session.execute(
            text(
                """
                UPDATE briefings
                SET status = :status, last_error = :error, payload = CAST(:payload AS jsonb),
                    updated_at = now(), sent_at = CASE WHEN :status = 'sent' THEN now() END
                WHERE id = :id
                """
            ),
            {"id": briefing_id, "status": status, "error": error, "payload": _json(payload)},
        )


async def send_now(
    client: httpx.AsyncClient, site_ids: list[str] | None = None
) -> list[BriefingResult]:
    """Build and send briefings immediately, recorded as trigger='manual'."""
    settings = get_settings()
    sites = await load_sites(site_ids)
    limit = asyncio.Semaphore(CONCURRENT_SITES)
    now = datetime.now(UTC)

    async def one(site: SiteInfo) -> BriefingResult:
        briefing_date = next_day(now, site.timezone)
        async with limit:
            status, error, payload = await _build_and_deliver(client, settings, site, briefing_date)
        async with SessionLocal() as session, session.begin():
            briefing_id = await session.scalar(
                text(
                    """
                    INSERT INTO briefings
                      (site_id, briefing_date, trigger, status, last_error, payload, sent_at)
                    VALUES (:site_id, :date, 'manual', :status, :error, CAST(:payload AS jsonb),
                            CASE WHEN :status = 'sent' THEN now() END)
                    RETURNING id
                    """
                ),
                {
                    "site_id": site.id,
                    "date": briefing_date,
                    "status": status,
                    "error": error,
                    "payload": _json(payload),
                },
            )
        return BriefingResult(site.id, briefing_date, status, error, briefing_id, payload)

    return list(await asyncio.gather(*(one(site) for site in sites)))


async def run_due_briefings(
    client: httpx.AsyncClient, now: datetime | None = None
) -> list[BriefingResult]:
    """Send every scheduled briefing that is due and not yet sent. Safe to run concurrently."""
    settings = get_settings()
    now = now or datetime.now(UTC)
    due = [
        (site, d)
        for site in await load_sites()
        if (d := scheduled_briefing_date(now, site.timezone, settings.briefing_hour))
    ]
    if not due:
        return []

    limit = asyncio.Semaphore(CONCURRENT_SITES)

    async def one(site: SiteInfo, briefing_date: date) -> BriefingResult | None:
        briefing_id = await _claim(site.id, briefing_date)
        if briefing_id is None:
            return None  # already sent, given up on, or being sent by another process
        async with limit:
            status, error, payload = await _build_and_deliver(client, settings, site, briefing_date)
        await _record(briefing_id, status, error, payload)
        if status == "sent":
            logger.info("Sent briefing for site %s (%s)", site.id, briefing_date)
        else:
            logger.warning("Briefing for site %s %s: %s", site.id, status, error)
        return BriefingResult(site.id, briefing_date, status, error, briefing_id)

    results = await asyncio.gather(*(one(site, d) for site, d in due))
    return [r for r in results if r is not None]


async def _claim(site_id: str, briefing_date: date) -> int | None:
    """Atomically take ownership of a site's scheduled briefing. Returns its id, or None."""
    async with SessionLocal() as session, session.begin():
        return await session.scalar(
            text(
                """
                INSERT INTO briefings (site_id, briefing_date, trigger, status)
                VALUES (:site_id, :date, 'scheduled', 'sending')
                ON CONFLICT (site_id, briefing_date) WHERE trigger = 'scheduled' DO UPDATE
                  SET status = 'sending', attempts = briefings.attempts + 1, updated_at = now()
                  WHERE (briefings.status = 'failed' AND briefings.attempts < :max_attempts)
                     OR (briefings.status = 'sending'
                         AND briefings.updated_at < now() - CAST(:stale AS interval))
                RETURNING id
                """
            ),
            {
                "site_id": site_id,
                "date": briefing_date,
                "max_attempts": MAX_ATTEMPTS,
                "stale": STALE_SENDING,
            },
        )


async def scheduler_loop(client: httpx.AsyncClient) -> None:
    """Background task started by the app when BRIEFINGS_ENABLED is true."""
    interval = get_settings().briefing_check_interval_s
    logger.info("Briefing scheduler running; checking every %ss", interval)
    while True:
        try:
            await run_due_briefings(client)
        except Exception:
            logger.exception("Briefing check failed; retrying next interval")
        await asyncio.sleep(interval)


def _json(payload: dict[str, Any] | None) -> str | None:
    return None if payload is None else json.dumps(payload)


def _float(value: Any) -> float | None:
    return None if value is None else float(value)
