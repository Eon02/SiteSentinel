import asyncio
import json
from datetime import UTC, date, datetime

import httpx
import pytest

from app.config import Settings
from app.services.briefings import (
    SiteInfo,
    build_briefing,
    deliver,
    scheduled_briefing_date,
)
from app.services.nws import NwsError, fetch_forecast
from tests.test_weather import SAMPLE as OPEN_METEO_SAMPLE

SETTINGS = Settings(
    database_url="postgresql://test:test@localhost/test",
    open_meteo_url="https://meteo.test/v1/forecast",
    nws_url="https://nws.test",
    briefing_webhook_url="https://hooks.test/briefing",
)

SITE = SiteInfo(
    id="1",
    name="Sand Point",
    utility="Sand Point Generating, LLC",
    plant_type="Petroleum Liquids",
    county="Aleutians East",
    state="AK",
    timezone="America/Anchorage",
    near_river=False,
    latitude=55.339722,
    longitude=-160.4972,
    flood={"fema_mapped": False, "site_in_sfha": None},
)

NWS_POINTS = {
    "properties": {"gridId": "ALU", "forecast": "https://nws.test/gridpoints/ALU/634,96/forecast"}
}


def nws_period(name: str, start: str, day: bool, temp: float, unit: str = "F") -> dict:
    return {
        "name": name,
        "startTime": start,
        "endTime": start,
        "isDaytime": day,
        "temperature": temp,
        "temperatureUnit": unit,
        "probabilityOfPrecipitation": {"value": 80},
        "windSpeed": "15 to 20 mph",
        "windDirection": "SW",
        "shortForecast": "Light Rain",
        "detailedForecast": "Rain. High near 50.",
    }


NWS_FORECAST = {
    "properties": {
        "periods": [
            nws_period("Tonight", "2026-08-14T18:00:00-08:00", False, 42),
            nws_period("Saturday", "2026-08-15T06:00:00-08:00", True, 50),
            nws_period("Saturday Night", "2026-08-15T18:00:00-08:00", False, 10, "C"),
            nws_period("Sunday", "2026-08-16T06:00:00-08:00", True, 52),
        ]
    }
}
NWS_ALERTS = {
    "features": [
        {
            "properties": {
                "event": "Flood Warning",
                "severity": "Severe",
                "urgency": "Immediate",
                "headline": "Flood Warning until 6 PM",
                "instruction": "Move to higher ground.",
                "onset": "2026-08-14T12:00:00-08:00",
                "ends": None,
            }
        }
    ]
}


def mock_client(routes: dict[str, httpx.Response]) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        for prefix, response in routes.items():
            if str(request.url).startswith(prefix):
                return response
        raise AssertionError(f"Unexpected request {request.url}")

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


NWS_ROUTES = {
    "https://nws.test/points/": httpx.Response(200, json=NWS_POINTS),
    "https://nws.test/gridpoints/": httpx.Response(200, json=NWS_FORECAST),
    "https://nws.test/alerts/active": httpx.Response(200, json=NWS_ALERTS),
}


@pytest.mark.parametrize(
    ("now_utc", "tz", "expected"),
    [
        # 17:59 in Chicago (CDT, UTC-5): not yet due
        (datetime(2026, 8, 14, 22, 59, tzinfo=UTC), "America/Chicago", None),
        # 18:00 in Chicago: due, covering tomorrow
        (datetime(2026, 8, 14, 23, 0, tzinfo=UTC), "America/Chicago", date(2026, 8, 15)),
        # Already Aug 15 in UTC, but 7 PM Aug 14 in Anchorage: due, covering Aug 15 (not Aug 16)
        (datetime(2026, 8, 15, 3, 0, tzinfo=UTC), "America/Anchorage", date(2026, 8, 15)),
        (datetime(2026, 8, 15, 2, 0, tzinfo=UTC), "America/New_York", date(2026, 8, 15)),
        # 23:30 local still catches up; after midnight it isn't due until 6 PM again
        (datetime(2026, 8, 15, 3, 30, tzinfo=UTC), "America/New_York", date(2026, 8, 15)),
        (datetime(2026, 8, 15, 4, 30, tzinfo=UTC), "America/New_York", None),
    ],
)
def test_scheduled_briefing_date(now_utc: datetime, tz: str, expected: date | None) -> None:
    assert scheduled_briefing_date(now_utc, tz, 18) == expected


def test_nws_forecast_keeps_requested_day_only() -> None:
    async def go():
        async with mock_client(NWS_ROUTES) as client:
            return await fetch_forecast(
                client, "https://nws.test", "UA", 55.339722, -160.4972, date(2026, 8, 15)
            )

    forecast = asyncio.run(go())

    assert forecast.office == "ALU"
    assert [p.name for p in forecast.periods] == ["Saturday", "Saturday Night"]
    assert forecast.periods[1].temperature_f == 50.0  # 10 °C converted
    assert forecast.periods[0].wind == "15 to 20 mph SW"
    assert forecast.active_alerts[0].event == "Flood Warning"


def test_nws_outside_us_raises() -> None:
    routes = {"https://nws.test/points/": httpx.Response(404, json={"detail": "Invalid point"})}

    async def go():
        async with mock_client(routes) as client:
            await fetch_forecast(client, "https://nws.test", "UA", 51.5, -0.12, date(2026, 8, 15))

    with pytest.raises(NwsError, match="404"):
        asyncio.run(go())


def test_build_briefing_combines_sources() -> None:
    routes = {**NWS_ROUTES, "https://meteo.test/": httpx.Response(200, json=OPEN_METEO_SAMPLE)}

    async def go():
        async with mock_client(routes) as client:
            return await build_briefing(client, SETTINGS, SITE, date(2026, 8, 15))

    payload = asyncio.run(go())

    assert payload["briefing_date"] == "2026-08-15"
    assert payload["site"]["timezone"] == "America/Anchorage"
    assert payload["weather"]["open_meteo"]["forecast"]["date"] == "2026-08-15"
    assert payload["weather"]["open_meteo"]["current"]["precip_24h_in"] == 2.4
    assert len(payload["weather"]["nws"]["periods"]) == 2
    assert payload["flood"]["fema_mapped"] is False
    assert payload["source_errors"] == {}
    json.dumps(payload)  # must be JSON-serializable for the webhook and the jsonb column


def test_build_briefing_reports_failed_source() -> None:
    routes = {**NWS_ROUTES, "https://meteo.test/": httpx.Response(500, json={"reason": "down"})}

    async def go():
        async with mock_client(routes) as client:
            return await build_briefing(client, SETTINGS, SITE, date(2026, 8, 15))

    payload = asyncio.run(go())

    assert payload["weather"]["open_meteo"] is None
    assert "open_meteo" in payload["source_errors"]
    assert payload["weather"]["nws"] is not None  # other sources still delivered


def run_deliver(settings: Settings, response: httpx.Response) -> tuple[tuple, list]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return response

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await deliver(client, settings, {"hello": "world"})

    return asyncio.run(go()), seen


def test_deliver_posts_json_with_token() -> None:
    settings = SETTINGS.model_copy(update={"briefing_webhook_token": "s3cret"})
    result, seen = run_deliver(settings, httpx.Response(204))

    assert result == ("sent", None)
    assert seen[0].method == "POST"
    assert str(seen[0].url) == "https://hooks.test/briefing"
    assert seen[0].headers["Authorization"] == "Bearer s3cret"
    assert json.loads(seen[0].content) == {"hello": "world"}


def test_deliver_reports_webhook_error() -> None:
    result, _ = run_deliver(SETTINGS, httpx.Response(500))
    assert result == ("failed", "Webhook returned HTTP 500")


def test_deliver_skips_without_url() -> None:
    settings = SETTINGS.model_copy(update={"briefing_webhook_url": None})
    result, seen = run_deliver(settings, httpx.Response(200))
    assert result[0] == "skipped"
    assert not seen
