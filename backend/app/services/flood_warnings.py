"""Flood-after-heavy-rain warnings.

A site is at flood risk when either:
  1. NWS has a flood product in effect there (Flood/Flash Flood Warning, Watch or Advisory,
     Coastal Flood, ...), or
  2. it has had heavy rain in the past 24 hours (default 1 inch or more) AND it is exposed
     to flooding: near a river, inside a FEMA high-risk zone (SFHA), or within 0.5 miles of one.

Assessed sites get a row in `alerts` (type 'flood'); sites no longer at risk have their
active flood alert cleared.
"""

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import text

from app.config import Settings
from app.db import SessionLocal
from app.services.briefings import SiteInfo
from app.services.email import OutgoingEmail
from app.services.nws import ActiveAlert, NwsError, fetch_active_alerts
from app.services.open_meteo import OpenMeteoError, WeatherReport, fetch_weather

DEFAULT_RAIN_THRESHOLD_IN = 1.0
SFHA_NEARBY_MI = 0.5
SATURATED_SOIL = 0.4  # m³/m³ in the top 1 cm; above this the ground takes little more water
CONCURRENT_SITES = 5


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
    """Open or refresh a flood alert for each at-risk site; clear it for sites that recovered."""
    async with SessionLocal() as session, session.begin():
        for a in assessments:
            if a.at_risk:
                message = "Flood risk: " + "; ".join(a.reasons)
                a.alert_id = await session.scalar(
                    text(
                        """
                        UPDATE alerts SET message = :message
                        WHERE site_id = :site_id AND type = 'flood' AND cleared_at IS NULL
                        RETURNING id
                        """
                    ),
                    {"site_id": a.site.id, "message": message},
                )
                if a.alert_id is None:
                    a.alert_id = await session.scalar(
                        text(
                            """
                            INSERT INTO alerts (site_id, type, severity, message)
                            VALUES (:site_id, 'flood', 'warning', :message)
                            RETURNING id
                            """
                        ),
                        {"site_id": a.site.id, "message": message},
                    )
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


def warning_email(assessment: FloodAssessment, to: str) -> OutgoingEmail:
    site = assessment.site
    local_now = datetime.now(UTC).astimezone(ZoneInfo(site.timezone))
    place = ", ".join(filter(None, [site.county, site.state]))
    lines = [
        f"FLOOD RISK WARNING - {site.name}",
        f"Site {site.id}{f' ({place})' if place else ''}",
        f"Issued {local_now:%a %b %d, %I:%M %p %Z}",
        "",
        "Why:",
        *[f"  - {r}" for r in assessment.reasons],
        "",
        "Conditions:",
        f"  - Rain, past 24 hours: {_inches(assessment.precip_24h_in)}",
        f"  - Rain forecast tomorrow: {_inches(assessment.precip_tomorrow_in)}",
    ]
    if assessment.soil_moisture is not None:
        saturated = " (saturated)" if assessment.soil_moisture >= SATURATED_SOIL else ""
        lines.append(f"  - Soil moisture: {assessment.soil_moisture:.2f} m3/m3{saturated}")
    for alert in assessment.nws_flood_alerts:
        lines += ["", f"NWS {alert.event} ({alert.severity or 'unknown severity'}):"]
        if alert.headline:
            lines.append(f"  {alert.headline}")
        if alert.instruction:
            lines.append(f"  {alert.instruction.strip()}")
    lines += [
        "",
        "Before work starts:",
        "  - Check access roads and crossings; never drive through standing or moving water.",
        "  - Expect soft ground: keep heavy equipment off saturated soil and mats near water.",
        "  - Stage vehicles and equipment on high ground with a clear exit route.",
        "  - Confirm the go/no-go call with your supervisor.",
        "",
        f"Map: https://www.google.com/maps?q={site.latitude},{site.longitude}",
        "",
        "-- SiteSentinel",
    ]
    return OutgoingEmail(
        to=to,
        subject=f"FLOOD RISK WARNING: {site.name}{f' ({site.state})' if site.state else ''}",
        body="\n".join(lines),
    )


def _inches(value: float | None) -> str:
    return "unavailable" if value is None else f"{value:.2f} in"
