import httpx
import pytest
from fastapi.testclient import TestClient

from app.http import get_http_client
from app.main import app
from app.services.open_meteo import heat_index_f


@pytest.fixture
def no_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    async def no_sleep(_: float) -> None:
        pass

    monkeypatch.setattr("app.services.open_meteo.asyncio.sleep", no_sleep)


# Trimmed Open-Meteo response; hourly arrays carry 25 values (24 past hours + current hour).
SAMPLE = {
    "latitude": 25.88878,
    "longitude": -80.88919,
    "utc_offset_seconds": -14400,
    "timezone": "America/New_York",
    "elevation": 2.0,
    "current": {
        "time": "2026-08-14T06:45",
        "temperature_2m": 90.0,
        "relative_humidity_2m": 60,
        "apparent_temperature": 104.0,
        "precipitation": 0.0,
        "weather_code": 95,
        "wind_speed_10m": 8.0,
        "wind_gusts_10m": 22.0,
    },
    "hourly": {
        "precipitation": [5.0] + [0.1] * 24,
        "soil_moisture_0_to_1cm": [0.3] * 24 + [0.45],
        "cape": [500.0] * 24 + [2400.0],
    },
    "daily": {
        "time": ["2026-08-14", "2026-08-15"],
        "temperature_2m_max": [94.0, 96.0],
        "apparent_temperature_max": [110.0, 113.0],
        "precipitation_sum": [1.2, 0.4],
        "precipitation_probability_max": [80, 60],
        "wind_gusts_10m_max": [35.0, 20.0],
        "weather_code": [95, 80],
    },
}


def client_returning(status: int, body: dict) -> TestClient:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(status, json=body)

    mock = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app.dependency_overrides[get_http_client] = lambda: mock
    test_client = TestClient(app)
    test_client.requests = requests  # type: ignore[attr-defined]
    return test_client


@pytest.fixture(autouse=True)
def clear_overrides():
    yield
    app.dependency_overrides.clear()


def test_weather_parses_report() -> None:
    client = client_returning(200, SAMPLE)
    response = client.get("/weather", params={"lat": 25.9, "lon": -80.9})

    assert response.status_code == 200
    body = response.json()
    current = body["current"]
    assert current["observed_at"] == "2026-08-14T06:45:00-04:00"
    assert current["precip_24h_in"] == 2.4  # last 24 hourly values only
    assert current["soil_moisture"] == 0.45
    assert current["cape_j_kg"] == 2400.0
    assert current["heat_index_f"] == pytest.approx(100, abs=1)
    assert [d["date"] for d in body["daily"]] == ["2026-08-14", "2026-08-15"]
    assert body["daily"][1]["feels_like_max_f"] == 113.0

    sent = client.requests[0].url.params  # type: ignore[attr-defined]
    assert sent["temperature_unit"] == "fahrenheit"
    assert sent["latitude"] == "25.9"


def test_weather_upstream_error_returns_502() -> None:
    client = client_returning(400, {"error": True, "reason": "Latitude must be in range"})
    response = client.get("/weather", params={"lat": 25.9, "lon": -80.9})

    assert response.status_code == 502
    assert "Latitude must be in range" in response.json()["detail"]


def test_weather_rejects_out_of_range_coordinates() -> None:
    client = client_returning(200, SAMPLE)
    assert client.get("/weather", params={"lat": 95, "lon": 0}).status_code == 422
    assert client.get("/weather", params={"lat": 0, "lon": -181}).status_code == 422


@pytest.mark.parametrize(
    ("temp_f", "humidity", "expected"),
    [(90, 60, 100), (96, 65, 121), (100, 40, 109), (70, 50, 69)],  # NWS heat index chart
)
def test_heat_index_matches_nws_chart(temp_f: float, humidity: float, expected: float) -> None:
    assert heat_index_f(temp_f, humidity) == pytest.approx(expected, abs=1.5)


def test_weather_retries_timeouts(no_backoff: None) -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) < 3:
            raise httpx.ReadTimeout("slow", request=request)
        return httpx.Response(200, json=SAMPLE)

    app.dependency_overrides[get_http_client] = lambda: httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    )
    response = TestClient(app).get("/weather", params={"lat": 25.9, "lon": -80.9})

    assert response.status_code == 200
    assert len(calls) == 3


def test_weather_gives_up_after_retries(no_backoff: None) -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        raise httpx.ReadTimeout("", request=request)

    app.dependency_overrides[get_http_client] = lambda: httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    )
    response = TestClient(app).get("/weather", params={"lat": 25.9, "lon": -80.9})

    assert response.status_code == 502
    assert len(calls) == 3
    assert "ReadTimeout" in response.json()["detail"]  # never a blank error message


def test_weather_rejected_request_is_not_retried() -> None:
    client = client_returning(400, {"error": True, "reason": "Bad"})
    client.get("/weather", params={"lat": 25.9, "lon": -80.9})
    assert len(client.requests) == 1  # type: ignore[attr-defined]
