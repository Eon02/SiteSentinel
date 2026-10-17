"""Flood-after-heavy-rain warnings.

A site is at flood risk when either:
  1. NWS has a flood product in effect there (Flood/Flash Flood Warning, Watch or Advisory,
     Coastal Flood, ...), or
  2. it has had heavy rain in the past 24 hours (default 1 inch or more) AND it is exposed
     to flooding: near a river, inside a FEMA high-risk zone (SFHA), or within 0.5 miles of one.

Assessed sites get a row in `alerts` (type 'flood'); sites no longer at risk have their
active flood alert cleared.

Warnings are texted to the workers attached to each at-risk site (`site_workers`), plus any
extra numbers given, only on request through POST /alerts/flood/sms. The automated check
(FLOOD_CHECKS_ENABLED) records and clears alerts but never sends anything.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import text

from app.config import Settings
from app.db import SessionLocal
from app.services.briefings import SiteInfo, load_sites
from app.services.nws import ActiveAlert, NwsError, fetch_active_alerts
from app.services.open_meteo import OpenMeteoError, WeatherReport, fetch_weather
from app.services.sms import DeliveryResult, OutgoingSms, send_sms

DEFAULT_RAIN_THRESHOLD_IN = 1.0
SFHA_NEARBY_MI = 0.5
SMS_MAX_CHARS = 320  # two SMS segments
# Curly quotes and dashes would switch a text to UCS-2, cutting each segment to 70 characters.
GSM_REPLACEMENTS = {0x2018: "'", 0x2019: "'", 0x201C: '"', 0x201D: '"', 0x2013: "-", 0x2014: "-"}
CONCURRENT_SITES = 5

logger = logging.getLogger("sitesentinel.flood")


@dataclass
class FloodAssessment:
    site: SiteInfo
    at_risk: bool
    reasons: list[str]
    precip_24h_in: float | None
    precip_tomorrow_in: float | None
    soil_moisture: float | None
    nws_flood_alerts: list[ActiveAlert]
    source_errors: dict[str, str] = field(default_factory=dict)
    alert_id: int | None = None
    newly_raised: bool = False  # this check opened the site's flood alert (it wasn't active)

    @property
    def complete(self) -> bool:
        """Every source answered, so a 'not at risk' result can be trusted."""
        return not self.source_errors


def is_flood_alert(alert: ActiveAlert) -> bool:
    return "flood" in alert.event.lower()


def assess(
    site: SiteInfo,
    weather: WeatherReport | None,
    nws_alerts: list[ActiveAlert] | None,
    rain_threshold_in: float,
    tomorrow: str | None = None,
) -> FloodAssessment:
    """Apply the flood rules to one site's data. Pure function; no I/O."""
    current = weather.current if weather else None
    rain = current.precip_24h_in if current else None
    soil = current.soil_moisture if current else None
    rain_tomorrow = None
    if weather and tomorrow:
        day = next((d for d in weather.daily if d.date.isoformat() == tomorrow), None)
        rain_tomorrow = day.precip_in if day else None

    flood_alerts = [a for a in nws_alerts or [] if is_flood_alert(a)]

    flood = site.flood
    exposure = []
    if site.near_river:
        exposure.append("the site is near a river")
    if flood.get("site_in_sfha"):
        exposure.append(
            f"the site is inside FEMA flood zone {flood.get('zone_at_site')} "
            "(1% annual chance floodplain)"
        )
    elif flood.get("nearest_sfha_mi") is not None and flood["nearest_sfha_mi"] <= SFHA_NEARBY_MI:
        exposure.append(f"a FEMA high-risk flood zone is {flood['nearest_sfha_mi']} mi away")

    reasons = [f"NWS {a.event} in effect" for a in flood_alerts]
    heavy_rain = rain is not None and rain >= rain_threshold_in
    if heavy_rain and exposure:
        reasons.append(f"{rain:.2f} in of rain in the past 24 hours and " + " and ".join(exposure))

    return FloodAssessment(
        site=site,
        at_risk=bool(reasons),
        reasons=reasons,
        precip_24h_in=rain,
        precip_tomorrow_in=rain_tomorrow,
        soil_moisture=soil,
        nws_flood_alerts=flood_alerts,
    )


async def assess_sites(
    client: httpx.AsyncClient,
    settings: Settings,
    sites: list[SiteInfo],
    rain_threshold_in: float,
) -> list[FloodAssessment]:
    limit = asyncio.Semaphore(CONCURRENT_SITES)

    async def one(site: SiteInfo) -> FloodAssessment:
        async with limit:
            weather, nws = await asyncio.gather(
                fetch_weather(client, settings.open_meteo_url, site.latitude, site.longitude),
                fetch_active_alerts(
                    client,
                    settings.nws_url,
                    settings.nws_user_agent,
                    site.latitude,
                    site.longitude,
                ),
                return_exceptions=True,
            )
        errors = {}
        if isinstance(weather, OpenMeteoError):
            errors["open_meteo"], weather = str(weather), None
        elif isinstance(weather, BaseException):
            raise weather
        if isinstance(nws, NwsError):
            errors["nws"], nws = str(nws), None
        elif isinstance(nws, BaseException):
            raise nws
        tomorrow = datetime.now(UTC).astimezone(ZoneInfo(site.timezone)) + timedelta(days=1)
        result = assess(site, weather, nws, rain_threshold_in, tomorrow.date().isoformat())
        result.source_errors = errors
        return result

    return list(await asyncio.gather(*(one(site) for site in sites)))


async def record_alerts(assessments: list[FloodAssessment]) -> None:
    """Open or refresh a flood alert for each at-risk site; clear it for sites that recovered.

    Sets `alert_id`, and `newly_raised` when the alert didn't exist yet. The unique index
    alerts_one_active_flood makes this safe when several instances check at once.
    """
    async with SessionLocal() as session, session.begin():
        for a in assessments:
            if a.at_risk:
                row = (
                    await session.execute(
                        text(
                            """
                            INSERT INTO alerts (site_id, type, severity, message)
                            VALUES (:site_id, 'flood', 'warning', :message)
                            ON CONFLICT (site_id) WHERE type = 'flood' AND cleared_at IS NULL
                            DO UPDATE SET message = EXCLUDED.message
                            RETURNING id, (xmax = 0) AS inserted
                            """
                        ),
                        {"site_id": a.site.id, "message": "Flood risk: " + "; ".join(a.reasons)},
                    )
                ).one()
                a.alert_id, a.newly_raised = row.id, row.inserted
            elif a.complete:
                # Only clear on a full assessment, so an outage doesn't cancel a real warning.
                await session.execute(
                    text(
                        """
                        UPDATE alerts SET cleared_at = now()
                        WHERE site_id = :site_id AND type = 'flood' AND cleared_at IS NULL
                        """
                    ),
                    {"site_id": a.site.id},
                )


async def load_site_workers(site_ids: list[str]) -> dict[str, list[str]]:
    """Phone numbers (E.164) of the workers attached to each site."""
    if not site_ids:
        return {}
    async with SessionLocal() as session:
        rows = await session.execute(
            text("SELECT site_id, phone FROM site_workers WHERE site_id = ANY(:ids) ORDER BY id"),
            {"ids": site_ids},
        )
    workers: dict[str, list[str]] = {}
    for site_id, phone in rows:
        workers.setdefault(site_id, []).append(phone)
    return workers


def recipients(*groups: list[str]) -> list[str]:
    """Merge number lists in order, dropping duplicates."""
    seen: dict[str, str] = {}
    for group in groups:
        for value in group:
            seen.setdefault(value.strip().lower(), value.strip())
    return list(seen.values())


@dataclass
class FloodCheckResult:
    assessment: FloodAssessment
    deliveries: list[DeliveryResult]


async def run_flood_check(
    client: httpx.AsyncClient,
    settings: Settings,
    *,
    site_ids: list[str] | None = None,
    rain_threshold_in: float = DEFAULT_RAIN_THRESHOLD_IN,
    send_texts: bool = False,
    extra_phones: list[str] | None = None,
    notify_site_workers: bool = True,
) -> list[FloodCheckResult]:
    """Assess sites and record alerts; with `send_texts`, text a warning for at-risk sites.

    Texts go to `extra_phones` (E.164) plus, if `notify_site_workers`, each at-risk site's
    workers. Without `send_texts` nothing is sent.
    """
    sites = await load_sites(site_ids)
    assessments = await assess_sites(client, settings, sites, rain_threshold_in)
    await record_alerts(assessments)

    texts: list[tuple[str, OutgoingSms]] = []
    if send_texts:
        at_risk = [a for a in assessments if a.at_risk]
        workers = (
            await load_site_workers([a.site.id for a in at_risk]) if notify_site_workers else {}
        )
        texts = [
            (a.site.id, warning_sms(a, to))
            for a in at_risk
            for to in recipients(extra_phones or [], workers.get(a.site.id, []))
        ]
    results = await send_sms(client, settings, [m for _, m in texts])

    by_site: dict[str, list[DeliveryResult]] = {a.site.id: [] for a in assessments}
    for (site_id, _), delivery in zip(texts, results, strict=True):
        by_site[site_id].append(delivery)
    return [FloodCheckResult(a, by_site[a.site.id]) for a in assessments]


async def flood_check_loop(client: httpx.AsyncClient, settings: Settings) -> None:
    """Background task started by the app when FLOOD_CHECKS_ENABLED is true.

    Keeps flood alerts in the `alerts` table current (for the dashboard). Sends no messages.
    """
    interval = settings.flood_check_interval_s
    logger.info("Flood checks running every %ss (alerts only, no messages)", interval)
    while True:
        try:
            results = await run_flood_check(
                client, settings, rain_threshold_in=settings.flood_rain_threshold_in
            )
            for r in results:
                if r.assessment.newly_raised:
                    logger.info("Flood alert raised for site %s", r.assessment.site.id)
        except Exception:
            logger.exception("Flood check failed; retrying next interval")
        await asyncio.sleep(interval)


def warning_sms(assessment: FloodAssessment, to: str) -> OutgoingSms:
    """A short flood warning (at most two SMS segments, plain GSM characters)."""
    site = assessment.site
    flood = site.flood
    if assessment.nws_flood_alerts:
        why = [f"NWS {a.event} in effect" for a in assessment.nws_flood_alerts]
    else:
        why = []
    rain = assessment.precip_24h_in
    if rain is not None and any("rain in the past 24 hours" in r for r in assessment.reasons):
        exposure = (
            "site near river"
            if site.near_river
            else f"FEMA flood zone {flood.get('zone_at_site')}"
            if flood.get("site_in_sfha")
            else f"FEMA flood zone {flood.get('nearest_sfha_mi')} mi away"
        )
        why.append(f"{rain:.1f} in rain in 24h, {exposure}")
    place = f"{site.name} ({site.state})" if site.state else site.name
    action = "Avoid flooded roads; keep equipment on high ground."
    link = f"Map: https://maps.google.com/?q={site.latitude},{site.longitude}"

    body = f"FLOOD RISK: {place}. {'. '.join(why)}. {action} {link}"
    while len(body) > SMS_MAX_CHARS and len(why) > 1:  # drop extra reasons before anything else
        why.pop()
        body = f"FLOOD RISK: {place}. {'. '.join(why)}. {action} {link}"
    if len(body) > SMS_MAX_CHARS:
        body = f"FLOOD RISK: {place}. {action} {link}"
    return OutgoingSms(to=to, body=_gsm(body)[:SMS_MAX_CHARS])


def _gsm(text: str) -> str:
    """Replace characters outside the basic SMS alphabet, which would halve the segment size."""
    return text.translate(GSM_REPLACEMENTS)
