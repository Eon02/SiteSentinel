"""Client for the Open-Meteo forecast API (https://open-meteo.com/en/docs).

No API key is needed for non-commercial use. All values are returned in US units
(°F, mph, inches) and dates are local to the coordinate's timezone.
"""

from datetime import date, datetime, timedelta, timezone

import httpx
from pydantic import BaseModel

CURRENT_FIELDS = [
    "temperature_2m",
    "relative_humidity_2m",
    "apparent_temperature",
    "precipitation",
    "weather_code",
    "wind_speed_10m",
    "wind_gusts_10m",
]
HOURLY_FIELDS = ["precipitation", "soil_moisture_0_to_1cm", "cape"]
DAILY_FIELDS = [
    "temperature_2m_max",
    "apparent_temperature_max",
    "precipitation_sum",
    "precipitation_probability_max",
    "wind_gusts_10m_max",
    "weather_code",
]


class OpenMeteoError(Exception):
    """Open-Meteo could not be reached or rejected the request."""


class CurrentConditions(BaseModel):
    observed_at: datetime
    temp_f: float | None
    humidity_pct: float | None
    heat_index_f: float | None  # NWS heat index, the measure OSHA heat guidance uses
    feels_like_f: float | None  # Open-Meteo apparent temperature (includes wind and sun)
    precip_in: float | None  # precipitation in the current 15-minute interval
    precip_24h_in: float | None
    soil_moisture: float | None  # volumetric, m³/m³ in the top 1 cm (0 to ~0.5)
    wind_mph: float | None
    wind_gust_mph: float | None
    cape_j_kg: float | None  # convective energy; above ~1000 J/kg favors thunderstorms
    weather_code: int | None  # WMO code; 95-99 are thunderstorms


class DailyForecast(BaseModel):
    date: date
    temp_max_f: float | None
    feels_like_max_f: float | None
    precip_in: float | None
    precip_probability_pct: float | None
    wind_gust_max_mph: float | None
    weather_code: int | None


class WeatherReport(BaseModel):
    latitude: float  # Open-Meteo snaps to its nearest grid cell
    longitude: float
    elevation_m: float | None
    timezone: str
    current: CurrentConditions
    daily: list[DailyForecast]  # today first, then tomorrow


def heat_index_f(temp_f: float | None, humidity_pct: float | None) -> float | None:
    """NWS heat index (Rothfusz regression with NWS adjustments)."""
    if temp_f is None or humidity_pct is None:
        return None
    t, rh = temp_f, humidity_pct
    simple = 0.5 * (t + 61.0 + (t - 68.0) * 1.2 + rh * 0.094)
    if (simple + t) / 2 < 80:
        return round(simple, 1)
    hi = (
        -42.379
        + 2.04901523 * t
        + 10.14333127 * rh
        - 0.22475541 * t * rh
        - 0.00683783 * t * t
        - 0.05481717 * rh * rh
        + 0.00122874 * t * t * rh
        + 0.00085282 * t * rh * rh
        - 0.00000199 * t * t * rh * rh
    )
    if rh < 13 and 80 <= t <= 112:
        hi -= ((13 - rh) / 4) * ((17 - abs(t - 95)) / 17) ** 0.5
    elif rh > 85 and 80 <= t <= 87:
        hi += ((rh - 85) / 10) * ((87 - t) / 5)
    return round(hi, 1)


async def fetch_weather(
    client: httpx.AsyncClient, base_url: str, latitude: float, longitude: float
) -> WeatherReport:
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "current": ",".join(CURRENT_FIELDS),
        "hourly": ",".join(HOURLY_FIELDS),
        "daily": ",".join(DAILY_FIELDS),
        # Each hourly value covers the hour ending at its timestamp, so the last 24 values
        # (ending at the current hour) span the past 24 hours.
        "past_hours": 24,
        "forecast_hours": 1,
        "forecast_days": 2,
        "temperature_unit": "fahrenheit",
        "wind_speed_unit": "mph",
        "precipitation_unit": "inch",
        "timezone": "auto",
    }
    try:
        response = await client.get(base_url, params=params)
        data = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise OpenMeteoError(f"Could not reach Open-Meteo: {exc}") from exc
    if response.is_error or data.get("error"):
        reason = data.get("reason", f"HTTP {response.status_code}")
        raise OpenMeteoError(f"Open-Meteo rejected the request: {reason}")
    return _parse(data)


def _parse(data: dict) -> WeatherReport:
    tz = timezone(timedelta(seconds=data["utc_offset_seconds"]))
    current = data["current"]
    hourly = data["hourly"]
    daily = data["daily"]

    precip_hours = [p for p in hourly["precipitation"][-24:] if p is not None]
    temp, humidity = current.get("temperature_2m"), current.get("relative_humidity_2m")

    return WeatherReport(
        latitude=data["latitude"],
        longitude=data["longitude"],
        elevation_m=data.get("elevation"),
        timezone=data["timezone"],
        current=CurrentConditions(
            observed_at=datetime.fromisoformat(current["time"]).replace(tzinfo=tz),
            temp_f=temp,
            humidity_pct=humidity,
            heat_index_f=heat_index_f(temp, humidity),
            feels_like_f=current.get("apparent_temperature"),
            precip_in=current.get("precipitation"),
            precip_24h_in=round(sum(precip_hours), 3) if precip_hours else None,
            soil_moisture=hourly["soil_moisture_0_to_1cm"][-1],
            wind_mph=current.get("wind_speed_10m"),
            wind_gust_mph=current.get("wind_gusts_10m"),
            cape_j_kg=hourly["cape"][-1],
            weather_code=current.get("weather_code"),
        ),
        daily=[
            DailyForecast(
                date=day,
                temp_max_f=daily["temperature_2m_max"][i],
                feels_like_max_f=daily["apparent_temperature_max"][i],
                precip_in=daily["precipitation_sum"][i],
                precip_probability_pct=daily["precipitation_probability_max"][i],
                wind_gust_max_mph=daily["wind_gusts_10m_max"][i],
                weather_code=daily["weather_code"][i],
            )
            for i, day in enumerate(daily["time"])
        ],
    )
