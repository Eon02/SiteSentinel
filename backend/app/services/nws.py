"""Client for the National Weather Service API (https://www.weather.gov/documentation/services-web-api).

Free and keyless, but NWS requires a User-Agent identifying the app. US locations only
(including Alaska, Hawaii and territories); other points return 404.
"""

from datetime import date, datetime
from typing import Any

import httpx
from pydantic import BaseModel

HEADERS = {"Accept": "application/geo+json"}


class NwsError(Exception):
    """NWS could not be reached or has no data for the location."""


class ForecastPeriod(BaseModel):
    name: str  # e.g. "Sunday", "Sunday Night"
    start_time: datetime
    end_time: datetime
    is_daytime: bool
    temperature_f: float | None
    precip_probability_pct: float | None
    wind: str | None  # e.g. "15 to 20 mph SW"
    short_forecast: str | None
    detailed_forecast: str | None


class ActiveAlert(BaseModel):
    event: str  # e.g. "Flood Warning", "Heat Advisory", "Severe Thunderstorm Warning"
    severity: str | None  # Extreme | Severe | Moderate | Minor | Unknown
    urgency: str | None
    headline: str | None
    instruction: str | None
    onset: datetime | None
    ends: datetime | None


class NwsForecast(BaseModel):
    office: str  # NWS forecast office, e.g. "ALU"
    periods: list[ForecastPeriod]  # periods that start on the requested day
    active_alerts: list[ActiveAlert]


async def fetch_forecast(
    client: httpx.AsyncClient,
    base_url: str,
    user_agent: str,
    latitude: float,
    longitude: float,
    day: date,
) -> NwsForecast:
    """Forecast periods starting on `day` (site-local) plus currently active alerts."""
    base = base_url.rstrip("/")
    headers = {**HEADERS, "User-Agent": user_agent}
    point = f"{latitude:.4f},{longitude:.4f}"  # NWS rejects more than 4 decimals

    points = await _get(client, f"{base}/points/{point}", headers)
    props = points["properties"]
    forecast = await _get(client, props["forecast"], headers)
    alerts = await _get(client, f"{base}/alerts/active", headers, {"point": point})

    periods = [_period(p) for p in forecast["properties"]["periods"]]
    return NwsForecast(
        office=props.get("gridId", ""),
        periods=[p for p in periods if p.start_time.date() == day],
        active_alerts=[_alert(f["properties"]) for f in alerts.get("features", [])],
    )


async def _get(
    client: httpx.AsyncClient,
    url: str,
    headers: dict[str, str],
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        response = await client.get(url, headers=headers, params=params, timeout=30.0)
        data = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise NwsError(f"Could not reach NWS: {exc}") from exc
    if response.is_error:
        raise NwsError(f"NWS returned HTTP {response.status_code}: {data.get('detail', '')}")
    return data


def _period(raw: dict[str, Any]) -> ForecastPeriod:
    wind = " ".join(filter(None, [raw.get("windSpeed"), raw.get("windDirection")])) or None
    return ForecastPeriod(
        name=raw["name"],
        start_time=raw["startTime"],
        end_time=raw["endTime"],
        is_daytime=raw["isDaytime"],
        temperature_f=_fahrenheit(raw.get("temperature"), raw.get("temperatureUnit")),
        precip_probability_pct=(raw.get("probabilityOfPrecipitation") or {}).get("value"),
        wind=wind,
        short_forecast=raw.get("shortForecast"),
        detailed_forecast=raw.get("detailedForecast"),
    )


def _fahrenheit(value: float | None, unit: str | None) -> float | None:
    if value is None or unit != "C":
        return value
    return round(value * 9 / 5 + 32, 1)


def _alert(props: dict[str, Any]) -> ActiveAlert:
    return ActiveAlert(
        event=props["event"],
        severity=props.get("severity"),
        urgency=props.get("urgency"),
        headline=props.get("headline"),
        instruction=props.get("instruction"),
        onset=props.get("onset"),
        ends=props.get("ends"),
    )
