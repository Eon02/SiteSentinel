from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from app.config import get_settings
from app.http import HttpClientDep
from app.services.open_meteo import OpenMeteoError, WeatherReport, fetch_weather

router = APIRouter(prefix="/weather", tags=["weather"])


@router.get("")
async def weather_at(
    client: HttpClientDep,
    lat: Annotated[float, Query(ge=-90, le=90, description="Latitude in decimal degrees")],
    lon: Annotated[float, Query(ge=-180, le=180, description="Longitude in decimal degrees")],
) -> WeatherReport:
    """Current conditions, past-24h rain, and today/tomorrow forecast for a coordinate."""
    try:
        return await fetch_weather(client, get_settings().open_meteo_url, lat, lon)
    except OpenMeteoError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
