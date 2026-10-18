import asyncio
import copy
import json
from typing import Any

import httpx
import pytest

from app.services import conditions
from app.services.conditions import (
    UPSERT_FORECAST_SCORE,
    UPSERT_HAZARD_SCORE,
    SiteLocation,
    condition_row,
    forecast_input,
    forecast_row,
    hazard_input,
    hazard_row,
    record_conditions,
    soil_saturation,
)
from app.services.open_meteo import _parse
from tests.test_weather import SAMPLE

# SAMPLE has weather code 95 (thunderstorm). This variant is a mild, clear day.
CLEAR = copy.deepcopy(SAMPLE)
CLEAR["current"].update({"weather_code": 0, "wind_gusts_10m": 33.75, "temperature_2m": 80.0})
CLEAR["hourly"]["precipitation"] = [0.0] * 25
CLEAR["hourly"]["soil_moisture_0_to_1cm"] = [0.405] * 25

# Every reading the hazard model uses is missing.
NO_READINGS = copy.deepcopy(SAMPLE)
NO_READINGS["current"].update(
    {
        "temperature_2m": None,
        "relative_humidity_2m": None,
        "weather_code": None,
        "wind_speed_10m": None,
        "wind_gusts_10m": None,
    }
)
NO_READINGS["hourly"]["precipitation"] = [None] * 25
NO_READINGS["hourly"]["soil_moisture_0_to_1cm"] = [None] * 25

# Open-Meteo returned today but no tomorrow.
NO_TOMORROW = copy.deepcopy(SAMPLE)
NO_TOMORROW["daily"] = {k: v[:1] for k, v in SAMPLE["daily"].items()}

# Tomorrow is there, but none of the readings the forecast model uses.
NO_FORECAST_READINGS = copy.deepcopy(SAMPLE)
for _key in NO_FORECAST_READINGS["daily"]:
    if _key != "time":
        NO_FORECAST_READINGS["daily"][_key] = [SAMPLE["daily"][_key][0], None]


class Recorded:
    """What record_conditions() would have written, with the database stubbed out."""

    def __init__(self) -> None:
        self.conditions: list[dict[str, Any]] = []
        self.hazards: list[dict[str, Any]] = []
        self.forecasts: list[dict[str, Any]] = []
        self.result = conditions.RecordResult()


def run_record(
    monkeypatch: pytest.MonkeyPatch,
    bodies: dict[str, dict[str, Any]],
    *,
    hazard_write_error: Exception | None = None,
    forecast_write_error: Exception | None = None,
) -> Recorded:
    """Record one site per `bodies` entry, answering each site's request with its body."""
    recorded = Recorded()
    sites = [SiteLocation(site_id, float(i), 0.0) for i, site_id in enumerate(bodies, start=1)]

    async def fake_load_sites(site_ids: list[str] | None = None) -> list[SiteLocation]:
        return sites

    async def fake_store_rows(rows: list[dict[str, Any]]) -> None:
        recorded.conditions.extend(rows)

    async def fake_store_hazard_scores(rows: list[dict[str, Any]]) -> None:
        if hazard_write_error:
            raise hazard_write_error
        recorded.hazards.extend(rows)

    async def fake_store_forecast_scores(rows: list[dict[str, Any]]) -> None:
        if forecast_write_error:
            raise forecast_write_error
        recorded.forecasts.extend(rows)

    monkeypatch.setattr(conditions, "load_sites", fake_load_sites)
    monkeypatch.setattr(conditions, "store_rows", fake_store_rows)
    monkeypatch.setattr(conditions, "store_hazard_scores", fake_store_hazard_scores)
    monkeypatch.setattr(conditions, "store_forecast_scores", fake_store_forecast_scores)

    def handler(request: httpx.Request) -> httpx.Response:
        site = sites[int(float(request.url.params["latitude"])) - 1]
        return httpx.Response(200, json=bodies[site.id])

    async def run() -> conditions.RecordResult:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await record_conditions(client)

    recorded.result = asyncio.run(run())
    return recorded


@pytest.mark.parametrize(
    ("volumetric", "expected"),
    [(None, None), (0.0, 0.0), (0.2, 0.4), (0.45, 0.9), (0.5, 1.0), (0.62, 1.0), (-0.01, 0.0)],
)
def test_soil_saturation(volumetric: float | None, expected: float | None) -> None:
    assert soil_saturation(volumetric) == expected


def test_condition_row_maps_current_weather() -> None:
    row = condition_row("6045", _parse(SAMPLE))

    assert row["site_id"] == "6045"
    assert row["observed_at"].isoformat() == "2026-08-14T06:45:00-04:00"
    assert row["temp_f"] == 90.0
    assert row["heat_index_f"] == pytest.approx(100, abs=1)
    assert row["precip_24h_in"] == 2.4
    assert row["soil_moisture"] == 0.9  # 0.45 m³/m³ volumetric -> 90% saturated
    assert row["wind_mph"] == 8.0
    assert row["wind_gust_mph"] == 22.0
    assert row["humidity_pct"] == 60


def test_record_conditions_stores_good_sites_and_reports_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sites = [SiteLocation("ok", 25.9, -80.9), SiteLocation("bad", 0.0, 0.0)]
    stored: list[dict[str, Any]] = []

    async def fake_load_sites(site_ids: list[str] | None = None) -> list[SiteLocation]:
        return sites

    async def fake_store_rows(rows: list[dict[str, Any]]) -> None:
        stored.extend(rows)

    async def fake_store_scores(rows: list[dict[str, Any]]) -> None:
        pass

    monkeypatch.setattr(conditions, "load_sites", fake_load_sites)
    monkeypatch.setattr(conditions, "store_rows", fake_store_rows)
    monkeypatch.setattr(conditions, "store_hazard_scores", fake_store_scores)
    monkeypatch.setattr(conditions, "store_forecast_scores", fake_store_scores)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params["latitude"] == "0.0":
            return httpx.Response(400, json={"error": True, "reason": "No data"})
        return httpx.Response(200, json=SAMPLE)

    async def run() -> conditions.RecordResult:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await record_conditions(client)

    result = asyncio.run(run())

    assert result.stored == 1
    assert [r["site_id"] for r in stored] == ["ok"]
    assert list(result.failed) == ["bad"]
    assert "No data" in result.failed["bad"]


def test_hazard_input_passes_raw_soil_moisture_and_weather_code() -> None:
    report = _parse(SAMPLE)
    reading = hazard_input(report)

    # The conditions row stores saturation (0.45 / 0.5 = 0.9); the model needs the raw 0.45.
    assert reading["soil_moisture"] == 0.45
    assert reading["soil_moisture"] != condition_row("6045", report)["soil_moisture"]
    assert reading["weather_code"] == 95
    assert reading["precip_24h_in"] == 2.4
    assert reading["wind_mph"] == 8.0
    assert reading["wind_gust_mph"] == 22.0
    assert reading["heat_index_f"] == report.current.heat_index_f


def test_hazard_row_stores_a_thunderstorm_as_10_with_thunderstorm_first() -> None:
    row = hazard_row("6045", _parse(SAMPLE))

    assert row is not None
    assert row["site_id"] == "6045"
    assert row["score"] == 10.0
    factors = json.loads(row["top_factors"])
    assert factors[0]["factor"] == "Thunderstorm"
    assert factors[0]["weight"] == 10.0
    assert max(factors, key=lambda f: f["weight"]) is factors[0]


def test_hazard_row_puts_score_and_weights_on_the_frontends_0_to_10_scale() -> None:
    # Warm, gusty (33.75 mph), wet soil, no rain, clear sky: a moderate score, no thunderstorm.
    row = hazard_row("6045", _parse(CLEAR))

    assert row is not None
    assert 0 < row["score"] < 10
    factors = json.loads(row["top_factors"])
    assert factors, "expected at least one factor"
    for factor in factors:
        assert set(factor) == {"factor", "risk_score", "contribution", "reason", "weight"}
        assert factor["weight"] > 0
        assert factor["weight"] == pytest.approx(factor["risk_score"] / 10, abs=0.001)


def test_hazard_row_is_none_without_usable_readings() -> None:
    assert hazard_row("6045", _parse(NO_READINGS)) is None


def test_hazard_write_is_an_upsert_of_todays_non_forecast_row() -> None:
    sql = " ".join(str(UPSERT_HAZARD_SCORE).split())

    assert "VALUES (:site_id, CURRENT_DATE, :score, false, CAST(:top_factors AS jsonb))" in sql
    assert "ON CONFLICT (site_id, score_date) DO UPDATE SET" in sql
    assert "is_forecast = false" in sql


def test_record_conditions_scores_each_site_and_writes_hazard_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded = run_record(monkeypatch, {"storm": SAMPLE, "calm": CLEAR})

    assert recorded.result.stored == 2
    assert recorded.result.scored == 2
    assert {h["site_id"]: h["score"] for h in recorded.hazards}["storm"] == 10.0
    assert not recorded.result.score_skipped
    assert not recorded.result.score_failed


def test_record_conditions_skips_scoring_a_site_with_no_usable_readings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded = run_record(monkeypatch, {"blank": NO_READINGS, "calm": CLEAR})

    # Conditions are stored for both; only the scorable site gets a hazard row, so the blank
    # site keeps whatever row it already had instead of being written a fake zero.
    assert [r["site_id"] for r in recorded.conditions] == ["blank", "calm"]
    assert [h["site_id"] for h in recorded.hazards] == ["calm"]
    assert recorded.result.score_skipped == ["blank"]
    assert recorded.result.scored == 1


def test_scoring_error_does_not_block_recording_conditions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def explode(reading: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("model bug")

    monkeypatch.setattr(conditions, "current_hazard_score", explode)
    recorded = run_record(monkeypatch, {"a": SAMPLE, "b": CLEAR})

    assert recorded.result.stored == 2
    assert len(recorded.conditions) == 2
    assert recorded.hazards == []
    assert recorded.result.score_failed == {"a": "model bug", "b": "model bug"}
    assert recorded.result.scored == 0


def test_hazard_write_error_does_not_undo_stored_conditions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded = run_record(
        monkeypatch, {"a": SAMPLE, "b": CLEAR}, hazard_write_error=ConnectionError("db down")
    )

    assert recorded.result.stored == 2
    assert len(recorded.conditions) == 2
    assert recorded.result.scored == 0
    assert recorded.result.score_failed == {"a": "db down", "b": "db down"}


# --- Tomorrow's forecast score -------------------------------------------------------------


def test_forecast_input_is_the_site_local_day_after_the_observation() -> None:
    forecast = forecast_input(_parse(SAMPLE))  # observed 2026-08-14 -> tomorrow is 08-15

    assert forecast is not None
    assert forecast["date"].isoformat() == "2026-08-15"
    assert forecast["feels_like_max_f"] == 113.0
    assert forecast["precip_in"] == 0.4
    assert forecast["precip_probability_pct"] == 60
    assert forecast["wind_gust_max_mph"] == 20.0
    assert forecast["weather_code"] == 80


def test_forecast_input_is_none_without_a_tomorrow_entry() -> None:
    assert forecast_input(_parse(NO_TOMORROW)) is None
    assert forecast_row("6045", _parse(NO_TOMORROW)) is None


def test_forecast_row_is_on_the_0_to_10_scale_with_positive_weights() -> None:
    row = forecast_row("6045", _parse(SAMPLE))

    assert row is not None
    assert row["site_id"] == "6045"
    assert 0 < row["score"] < 10
    factors = json.loads(row["top_factors"])
    assert [f["factor"] for f in factors][0] == "heat"  # apparent temperature 113 dominates
    for factor in factors:
        assert set(factor) == {"factor", "risk_score", "contribution", "reason", "weight"}
        assert factor["weight"] > 0
        assert factor["weight"] == pytest.approx(factor["risk_score"] / 10, abs=0.001)
        assert factor["reason"].startswith(("Forecast", "Daily"))


def test_forecast_row_is_none_without_usable_readings() -> None:
    assert forecast_row("6045", _parse(NO_FORECAST_READINGS)) is None


def test_forecast_write_is_an_upsert_that_never_replaces_an_observed_row() -> None:
    sql = " ".join(str(UPSERT_FORECAST_SCORE).split())

    assert "VALUES (:site_id, CURRENT_DATE + 1, :score, true, CAST(:top_factors AS jsonb))" in sql
    assert "ON CONFLICT (site_id, score_date) DO UPDATE SET" in sql
    assert sql.endswith("WHERE hazard_scores.is_forecast")
    assert "is_forecast =" not in sql  # an update leaves the flag as the forecast it already is


def test_record_conditions_writes_today_and_tomorrow_for_each_site(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded = run_record(monkeypatch, {"a": SAMPLE, "b": CLEAR})

    result = recorded.result
    assert (result.stored, result.scored, result.forecast_scored) == (2, 2, 2)
    assert [r["site_id"] for r in recorded.forecasts] == ["a", "b"]
    # Today's thunderstorm is 10.0; tomorrow's score comes from the daily forecast, not it.
    assert {h["site_id"]: h["score"] for h in recorded.hazards}["a"] == 10.0
    assert {f["site_id"]: f["score"] for f in recorded.forecasts}["a"] < 10
    assert not (result.forecast_skipped or result.forecast_failed)


def test_a_site_without_tomorrows_forecast_still_gets_its_live_score(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded = run_record(monkeypatch, {"x": NO_TOMORROW, "y": NO_FORECAST_READINGS})

    assert recorded.result.scored == 2
    assert recorded.result.forecast_scored == 0
    assert recorded.result.forecast_skipped == ["x", "y"]  # their forecast rows are left alone
    assert recorded.forecasts == []


def test_a_site_without_current_readings_still_gets_its_forecast(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded = run_record(monkeypatch, {"blank": NO_READINGS})

    assert recorded.result.score_skipped == ["blank"]
    assert [f["site_id"] for f in recorded.forecasts] == ["blank"]


def test_forecast_model_error_does_not_block_conditions_or_live_scores(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def explode(forecast: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("forecast bug")

    monkeypatch.setattr(conditions, "tomorrow_hazard_score", explode)
    recorded = run_record(monkeypatch, {"a": SAMPLE, "b": CLEAR})

    assert recorded.result.stored == 2
    assert recorded.result.scored == 2
    assert recorded.result.forecast_failed == {"a": "forecast bug", "b": "forecast bug"}
    assert recorded.forecasts == []


def test_forecast_write_error_does_not_undo_conditions_or_live_scores(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded = run_record(
        monkeypatch, {"a": SAMPLE, "b": CLEAR}, forecast_write_error=ConnectionError("db down")
    )

    assert recorded.result.stored == 2
    assert len(recorded.conditions) == len(recorded.hazards) == 2
    assert recorded.result.scored == 2
    assert recorded.result.forecast_scored == 0
    assert recorded.result.forecast_failed == {"a": "db down", "b": "db down"}


def test_live_write_error_does_not_stop_the_forecast_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded = run_record(monkeypatch, {"a": SAMPLE}, hazard_write_error=ConnectionError("db down"))

    assert recorded.result.score_failed == {"a": "db down"}
    assert recorded.result.forecast_scored == 1
    assert [f["site_id"] for f in recorded.forecasts] == ["a"]
