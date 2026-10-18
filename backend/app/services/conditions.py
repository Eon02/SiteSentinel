"""Conditions worker: records current Open-Meteo weather for every site in `conditions`.

- Background: when CONDITIONS_ENABLED is true, the API runs conditions_loop(), recording every
  CONDITIONS_INTERVAL_S seconds (default 15 minutes, Open-Meteo's update interval).
- One-off: `uv run python -m scripts.record_conditions` (also usable from cron).

Each run inserts one row per site keyed by Open-Meteo's observation time, so re-running within
the same 15-minute interval updates that row instead of adding a duplicate.

As a side effect, each run also scores every site with the hazard models and upserts
`hazard_scores`: today's row from the current weather (is_forecast false, the live score) and
tomorrow's row from the daily forecast (is_forecast true). The frontend shows both. Scoring never
blocks recording: conditions are stored first, and a scoring or hazard-write failure is reported
in the result instead of raised.
"""

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

import httpx
from sqlalchemy import text

from app.config import get_settings
from app.db import SessionLocal
from app.services.hazard_model import current_hazard_score, tomorrow_hazard_score
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
      site_id, observed_at, temp_f, heat_index_f, precip_24h_in, soil_moisture, wind_mph,
      wind_gust_mph, humidity_pct
    )
    VALUES (
      :site_id, :observed_at, :temp_f, :heat_index_f, :precip_24h_in, :soil_moisture, :wind_mph,
      :wind_gust_mph, :humidity_pct
    )
    ON CONFLICT (site_id, observed_at) DO UPDATE SET
      temp_f = EXCLUDED.temp_f,
      heat_index_f = EXCLUDED.heat_index_f,
      precip_24h_in = EXCLUDED.precip_24h_in,
      soil_moisture = EXCLUDED.soil_moisture,
      wind_mph = EXCLUDED.wind_mph,
      wind_gust_mph = EXCLUDED.wind_gust_mph,
      humidity_pct = EXCLUDED.humidity_pct
    """
)

# score_date is the database's date, which is what the frontend asks for (current_date).
UPSERT_HAZARD_SCORE = text(
    """
    INSERT INTO hazard_scores (site_id, score_date, score, is_forecast, top_factors)
    VALUES (:site_id, CURRENT_DATE, :score, false, CAST(:top_factors AS jsonb))
    ON CONFLICT (site_id, score_date) DO UPDATE SET
      score = EXCLUDED.score,
      is_forecast = false,
      top_factors = EXCLUDED.top_factors
    """
)

# Tomorrow's row, for the date after the one the frontend calls today. When that date arrives, the
# live upsert above overwrites it with the observed score. The WHERE keeps a forecast from ever
# replacing an observed row (e.g. a run just after midnight UTC).
UPSERT_FORECAST_SCORE = text(
    """
    INSERT INTO hazard_scores (site_id, score_date, score, is_forecast, top_factors)
    VALUES (:site_id, CURRENT_DATE + 1, :score, true, CAST(:top_factors AS jsonb))
    ON CONFLICT (site_id, score_date) DO UPDATE SET
      score = EXCLUDED.score,
      top_factors = EXCLUDED.top_factors
    WHERE hazard_scores.is_forecast
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
    failed: dict[str, str] = field(default_factory=dict)  # site id -> fetch error
    scored: int = 0
    score_skipped: list[str] = field(default_factory=list)  # site ids with no usable model input
    score_failed: dict[str, str] = field(default_factory=dict)  # site id -> scoring/write error
    forecast_scored: int = 0
    forecast_skipped: list[str] = field(default_factory=list)  # no usable forecast for tomorrow
    forecast_failed: dict[str, str] = field(default_factory=dict)  # site id -> scoring/write error


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
        "wind_gust_mph": current.wind_gust_mph,
        "humidity_pct": current.humidity_pct,
    }


def hazard_input(report: WeatherReport) -> dict[str, Any]:
    """The readings the hazard model scores.

    Built from the report rather than the conditions row: the model wants raw volumetric soil
    moisture (the row holds the converted 0-1 saturation) and the WMO weather code (not stored).
    """
    current = report.current
    return {
        "heat_index_f": current.heat_index_f,
        "wind_mph": current.wind_mph,
        "wind_gust_mph": current.wind_gust_mph,
        "precip_24h_in": current.precip_24h_in,
        "soil_moisture": current.soil_moisture,
        "weather_code": current.weather_code,
    }


def forecast_input(report: WeatherReport) -> dict[str, Any] | None:
    """Tomorrow's daily forecast for the hazard model, or None if Open-Meteo didn't return it.

    "Tomorrow" is the day after the observation's date, which is site-local because Open-Meteo
    reports times in the coordinate's own timezone.
    """
    tomorrow = report.current.observed_at.date() + timedelta(days=1)
    day = next((d for d in report.daily if d.date == tomorrow), None)
    return day.model_dump() if day else None


def table_row(site_id: str, result: dict[str, Any]) -> dict[str, Any] | None:
    """A model result as `hazard_scores` values, or None when it has no score.

    The model's 0-100 index is stored on the table's 0-10 scale. Each factor gets a `weight`
    (its own 0-10 score) because that is what the frontend ranks and sizes bars by; the model's
    leave-one-out `contribution` can be 0 for real hazards when another one saturates the score.
    """
    if result["hazard_score"] is None:
        return None
    factors = [
        {**factor, "weight": round(factor["risk_score"] / 10, 3)}
        for factor in result["top_factors"]
    ]
    return {
        "site_id": site_id,
        "score": round(result["hazard_score"] / 10, 2),
        "top_factors": json.dumps(factors),
    }


def hazard_row(site_id: str, report: WeatherReport) -> dict[str, Any] | None:
    """The live `hazard_scores` values for current weather, or None with no usable input."""
    return table_row(site_id, current_hazard_score(hazard_input(report)))


def forecast_row(site_id: str, report: WeatherReport) -> dict[str, Any] | None:
    """Tomorrow's `hazard_scores` values, or None with no forecast day or no usable input."""
    forecast = forecast_input(report)
    return table_row(site_id, tomorrow_hazard_score(forecast)) if forecast else None


async def load_sites(site_ids: list[str] | None = None) -> list[SiteLocation]:
    query = SITES_QUERY + (" WHERE id = ANY(:ids)" if site_ids else "") + " ORDER BY id"
    async with SessionLocal() as session:
        rows = (await session.execute(text(query), {"ids": site_ids})).all()
    return [SiteLocation(id=r.id, lat=r.lat, lon=r.lon) for r in rows]


async def store_rows(rows: list[dict[str, Any]]) -> None:
    async with SessionLocal() as session, session.begin():
        await session.execute(UPSERT_CONDITION, rows)


async def store_hazard_scores(rows: list[dict[str, Any]]) -> None:
    async with SessionLocal() as session, session.begin():
        await session.execute(UPSERT_HAZARD_SCORE, rows)


async def store_forecast_scores(rows: list[dict[str, Any]]) -> None:
    async with SessionLocal() as session, session.begin():
        await session.execute(UPSERT_FORECAST_SCORE, rows)


def score_site(
    build: Callable[[str, WeatherReport], dict[str, Any] | None],
    site_id: str,
    report: WeatherReport,
    skipped: list[str],
    failed: dict[str, str],
) -> dict[str, Any] | None:
    """Build a site's hazard row. No usable input is noted in `skipped`, and an error in `failed`.

    The models shouldn't raise, but scoring must never stop conditions from being recorded.
    """
    try:
        row = build(site_id, report)
    except Exception as exc:
        logger.exception("Scoring site %s failed", site_id)
        failed[site_id] = str(exc) or type(exc).__name__
        return None
    if row is None:
        skipped.append(site_id)
    return row


async def write_scores(
    rows: list[dict[str, Any]],
    store: Callable[[list[dict[str, Any]]], Awaitable[None]],
    failed: dict[str, str],
) -> int:
    """Write hazard rows in their own transaction; a failure is noted in `failed`, not raised."""
    if not rows:
        return 0
    try:
        await store(rows)
    except Exception as exc:  # conditions are already stored; report instead of raising
        logger.exception("Writing hazard scores failed")
        for row in rows:
            failed[row["site_id"]] = str(exc) or type(exc).__name__
        return 0
    return len(rows)


async def record_conditions(
    client: httpx.AsyncClient, site_ids: list[str] | None = None
) -> RecordResult:
    """Fetch and store current conditions for the given sites (every site when None).

    A site whose weather can't be fetched is reported in `failed`; the others are still stored.
    Each stored site is then scored twice and upserted: today's live score from the current
    weather, and tomorrow's forecast score from the daily forecast. A site with no usable model
    input (`score_skipped`, `forecast_skipped`) keeps its existing row; scoring and write errors
    land in `score_failed` / `forecast_failed` and never stop conditions from being recorded.
    """
    sites = await load_sites(site_ids)
    base_url = get_settings().open_meteo_url
    limit = asyncio.Semaphore(CONCURRENT_SITES)
    result = RecordResult()

    Row = dict[str, Any] | None

    async def fetch(site: SiteLocation) -> tuple[dict[str, Any], Row, Row] | None:
        try:
            async with limit:
                report = await fetch_weather(client, base_url, site.lat, site.lon)
        except OpenMeteoError as exc:
            result.failed[site.id] = str(exc)
            return None
        hazard = score_site(hazard_row, site.id, report, result.score_skipped, result.score_failed)
        forecast = score_site(
            forecast_row, site.id, report, result.forecast_skipped, result.forecast_failed
        )
        return condition_row(site.id, report), hazard, forecast

    fetched = [item for item in await asyncio.gather(*(fetch(s) for s in sites)) if item]
    rows = [row for row, _, _ in fetched]
    if rows:
        await store_rows(rows)
    result.stored = len(rows)

    # Each score set is written in its own transaction, so one failing can't undo the others.
    result.scored = await write_scores(
        [h for _, h, _ in fetched if h], store_hazard_scores, result.score_failed
    )
    result.forecast_scored = await write_scores(
        [f for _, _, f in fetched if f], store_forecast_scores, result.forecast_failed
    )
    return result


async def conditions_loop(client: httpx.AsyncClient) -> None:
    """Background task started by the app when CONDITIONS_ENABLED is true."""
    interval = get_settings().conditions_interval_s
    logger.info("Conditions worker running; recording every %ss", interval)
    while True:
        try:
            result = await record_conditions(client)
            logger.info(
                "Recorded conditions for %s sites, scored %s, forecast %s",
                result.stored,
                result.scored,
                result.forecast_scored,
            )
            for site_id, error in result.failed.items():
                logger.warning("Conditions for site %s failed: %s", site_id, error)
            if result.score_skipped:
                logger.info(
                    "No usable weather to score %s sites: %s",
                    len(result.score_skipped),
                    ", ".join(result.score_skipped),
                )
            for site_id, error in result.score_failed.items():
                logger.warning("Hazard score for site %s failed: %s", site_id, error)
            if result.forecast_skipped:
                logger.info(
                    "No usable forecast to score %s sites: %s",
                    len(result.forecast_skipped),
                    ", ".join(result.forecast_skipped),
                )
            for site_id, error in result.forecast_failed.items():
                logger.warning("Forecast score for site %s failed: %s", site_id, error)
        except Exception:
            logger.exception("Conditions run failed; retrying next interval")
        await asyncio.sleep(interval)
