"""Conditions worker: records current Open-Meteo weather for every site in `conditions`.

- Background: when CONDITIONS_ENABLED is true, the API runs conditions_loop(), recording every
  CONDITIONS_INTERVAL_S seconds (default 15 minutes, Open-Meteo's update interval).
- One-off: `uv run python -m scripts.record_conditions` (also usable from cron).

Each run inserts one row per site keyed by Open-Meteo's observation time, so re-running within
the same 15-minute interval updates that row instead of adding a duplicate.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

import httpx
from sqlalchemy import text

from app.config import get_settings
from app.db import SessionLocal
from app.services.open_meteo import OpenMeteoError, WeatherReport, fetch_weather

logger = logging.getLogger("sitesentinel.conditions")

CONCURRENT_SITES = 5

# Open-Meteo reports volumetric soil moisture (m³ water per m³ soil) for the top 1 cm. The
# conditions table, the hazard model and the frontend all use a 0-1 saturation fraction
# instead. Typical mineral soils hold at most ~0.5 m³/m³ when saturated (their porosity), and
# Open-Meteo's values top out around there, so saturation is volumetric / 0.5, capped at 1.
SATURATED_VWC = 0.5

SITES_QUERY = "SELECT id, ST_Y(geom) AS lat, ST_X(geom) AS lon FROM sites"

UPSERT_CONDITION = text(
    """
    INSERT INTO conditions (
      site_id, observed_at, temp_f, heat_index_f, precip_24h_in, soil_moisture, wind_mph
    )
    VALUES (
      :site_id, :observed_at, :temp_f, :heat_index_f, :precip_24h_in, :soil_moisture, :wind_mph
    )
    ON CONFLICT (site_id, observed_at) DO UPDATE SET
      temp_f = EXCLUDED.temp_f,
      heat_index_f = EXCLUDED.heat_index_f,
      precip_24h_in = EXCLUDED.precip_24h_in,
      soil_moisture = EXCLUDED.soil_moisture,
      wind_mph = EXCLUDED.wind_mph
    """
)


@dataclass
class SiteLocation:
    id: str
    lat: float
    lon: float


@dataclass
class RecordResult:
    stored: int = 0
    failed: dict[str, str] = field(default_factory=dict)  # site id -> error


def soil_saturation(volumetric: float | None) -> float | None:
    """Volumetric soil moisture (m³/m³) as a 0-1 saturation fraction."""
    if volumetric is None:
        return None
    return round(min(max(volumetric / SATURATED_VWC, 0.0), 1.0), 3)


def condition_row(site_id: str, report: WeatherReport) -> dict[str, Any]:
    current = report.current
    return {
        "site_id": site_id,
        "observed_at": current.observed_at,
        "temp_f": current.temp_f,
        "heat_index_f": current.heat_index_f,
        "precip_24h_in": current.precip_24h_in,
        "soil_moisture": soil_saturation(current.soil_moisture),
        "wind_mph": current.wind_mph,
    }


async def load_sites(site_ids: list[str] | None = None) -> list[SiteLocation]:
    query = SITES_QUERY + (" WHERE id = ANY(:ids)" if site_ids else "") + " ORDER BY id"
    async with SessionLocal() as session:
        rows = (await session.execute(text(query), {"ids": site_ids})).all()
    return [SiteLocation(id=r.id, lat=r.lat, lon=r.lon) for r in rows]


async def store_rows(rows: list[dict[str, Any]]) -> None:
    async with SessionLocal() as session, session.begin():
        await session.execute(UPSERT_CONDITION, rows)


async def record_conditions(
    client: httpx.AsyncClient, site_ids: list[str] | None = None
) -> RecordResult:
    """Fetch and store current conditions for the given sites (every site when None).

    A site whose weather can't be fetched is reported in `failed`; the others are still stored.
    """
    sites = await load_sites(site_ids)
    base_url = get_settings().open_meteo_url
    limit = asyncio.Semaphore(CONCURRENT_SITES)
    result = RecordResult()

    async def fetch(site: SiteLocation) -> dict[str, Any] | None:
        try:
            async with limit:
                report = await fetch_weather(client, base_url, site.lat, site.lon)
        except OpenMeteoError as exc:
            result.failed[site.id] = str(exc)
            return None
        return condition_row(site.id, report)

    rows = [row for row in await asyncio.gather(*(fetch(s) for s in sites)) if row]
    if rows:
        await store_rows(rows)
    result.stored = len(rows)
    return result


async def conditions_loop(client: httpx.AsyncClient) -> None:
    """Background task started by the app when CONDITIONS_ENABLED is true."""
    interval = get_settings().conditions_interval_s
    logger.info("Conditions worker running; recording every %ss", interval)
    while True:
        try:
            result = await record_conditions(client)
            logger.info("Recorded conditions for %s sites", result.stored)
            for site_id, error in result.failed.items():
                logger.warning("Conditions for site %s failed: %s", site_id, error)
        except Exception:
            logger.exception("Conditions run failed; retrying next interval")
        await asyncio.sleep(interval)
