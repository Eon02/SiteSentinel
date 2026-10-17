import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from productivity.estimate import (  # noqa: E402
    LossEvent,
    cold_loss,
    estimate,
    foster_loss,
    hazard_factors,
    load_config,
    piecewise,
    resolve_crew,
    summarize,
)
from productivity.heat_metrics import (  # noqa: E402
    f_to_c,
    foster_pwc_percent,
    vapour_pressure_hpa,
    wbgt_shade_c,
    wind_chill_f,
)

CFG = load_config()
CHI = ZoneInfo("America/Chicago")
CREW = {"num_crews": 2, "crew_size": 10, "hours_worked": 8, "work_start_hour": 7, "labor_rate": 40}


@dataclass
class Cond:
    """Mirrors CurrentConditions (backend/app/services/open_meteo.py) plus precip_24h_in."""

    observed_at: datetime
    temp_f: float | None = None
    humidity_pct: float | None = None
    heat_index_f: float | None = None
    feels_like_f: float | None = None
    precip_in: float | None = None
    soil_moisture: float | None = None
    wind_mph: float | None = None
    wind_gust_mph: float | None = None
    cape_j_kg: float | None = None
    weather_code: int | None = None
    precip_24h_in: float | None = None


@dataclass
class Day:
    """Mirrors DailyForecast."""

    date: date
    temp_max_f: float | None = None
    feels_like_max_f: float | None = None
    precip_in: float | None = None
    precip_probability_pct: float | None = None
    wind_gust_max_mph: float | None = None
    weather_code: int | None = None


def at(hour: int, day: int = 15) -> datetime:
    return datetime(2026, 7, day, hour, tzinfo=CHI).astimezone(UTC)


def site(tz: str | None = "America/Chicago") -> dict:
    return {"id": 1, "plant_type": "Solar", "timezone": tz}


def hot(hour: int = 12, hi_f: float = 108.0) -> Cond:
    return Cond(observed_at=at(hour), temp_f=96, humidity_pct=60, heat_index_f=hi_f)


def frac(events, scenario="central", factor="heat") -> float:
    return next(
        (e.fraction_lost for e in events if e.scenario == scenario and e.factor == factor), 0.0
    )


def test_foster_equations_match_paper_values():
    # Foster et al. 2021 Table 3 pooled heat index form: 100 / (1 + (55.47 / HI) ** -2.90)
    assert foster_pwc_percent(40.0, 55.47, -2.90) == pytest.approx(100 / (1 + (40 / 55.47) ** 2.9))
    assert foster_pwc_percent(40.0, 55.47, -2.90) == pytest.approx(72.1, abs=0.2)
    # WBGT 40 C is outside the fitted 12-36 range in the 2022 table, but the pooled 2021 WBGT
    # equation (33.63, -6.33) is valid to 40 and gives roughly the paper's 78% loss.
    assert 100 - foster_pwc_percent(40.0, 33.63, -6.33) == pytest.approx(75.0, abs=1.0)
    # solar correction (Foster 2022, high clothing, heat index) at its peak: 11.34 points at 44.02 C
    corr = {"amplitude": 11.34, "mu": 44.02, "sigma": 18.05}
    shade = foster_pwc_percent(44.02, 55.79, -2.68)
    assert shade - foster_pwc_percent(44.02, 55.79, -2.68, corr, 1.0) == pytest.approx(11.34)
    assert foster_pwc_percent(44.02, 55.79, -2.68, corr, 0.5) == pytest.approx(shade - 5.67)


def test_wbgt_shade_formula():
    # saturation vapour pressure at 30 C is about 42.4 hPa
    assert vapour_pressure_hpa(30.0, 100.0) == pytest.approx(42.4, abs=0.3)
    # 0.567 * 30 + 0.393 * 21.15 + 3.94 (e = 50% of saturation)
    assert wbgt_shade_c(86.0, 50.0) == pytest.approx(29.26, abs=0.1)
    assert wbgt_shade_c(86.0, 80.0) > wbgt_shade_c(86.0, 40.0)


def test_wind_chill_matches_nws_chart():
    assert wind_chill_f(0, 10) == pytest.approx(-16, abs=0.5)
    assert wind_chill_f(0, 15) == pytest.approx(-19, abs=0.5)
    assert wind_chill_f(60, 20) == 60  # outside the formula's range
    assert wind_chill_f(20, 3) == 20


def test_heat_zero_below_onset_and_nonnegative():
    curve = CFG["curves"]["heat"]["curves"]["foster_hi_coverall"]
    for sun in (0.0, 0.5, 1.0):
        assert foster_loss(curve["onset_c"], curve, sun) == 0.0
        assert foster_loss(f_to_c(70), curve, sun) == 0.0
    ev = estimate(site(), hot(hi_f=75.0), None, CREW, CFG)
    assert frac(ev) == 0.0


def test_heat_curves_monotonic_and_scenarios_ordered():
    heat = CFG["curves"]["heat"]
    for name, sc in heat["scenarios"].items():
        curve = heat["curves"][sc["curve"]]
        prev = {s: 0.0 for s in ("low", "central", "high")}
        for tenth in range(150, 700):  # 15.0 to 69.9 C (past the valid range: clamped)
            x = tenth / 10
            vals = {}
            for s in prev:
                p = sc[s]
                if "sun_adjustment_c" in p:
                    vals[s] = foster_loss(x + p["sun_adjustment_c"], curve)
                else:
                    vals[s] = foster_loss(x, curve, p["sun_scale"], p["metric_offset_c"])
                assert vals[s] >= prev[s] - 1e-9, (name, s, x)
            assert vals["low"] <= vals["central"] <= vals["high"], (name, x)
            prev = vals


def test_cold_curves_monotonic_and_ordered():
    cfg = CFG["curves"]["cold"]["wind_chill"]
    assert cold_loss(45.0, cfg, "high") == 0.0
    last = {s: 0.0 for s in ("low", "central", "high")}
    for wc in range(40, -41, -1):
        vals = {s: cold_loss(wc, cfg, s) for s in last}
        assert all(vals[s] >= last[s] for s in vals)
        assert vals["low"] <= vals["central"] <= vals["high"]
        last = vals
    assert piecewise(-50, [[-20, 0.5], [0, 0.2]]) == 0.5


def test_worked_example_from_the_doc():
    start = datetime(2026, 7, 15, 7, tzinfo=CHI)
    e = LossEvent(1, start, start.replace(hour=11), "slowdown", 0.15, "central", "heat", "x", "y")
    out = summarize([e], CREW, CFG)
    assert out["central"]["lost_worker_hours"] == pytest.approx(12.0)  # 20 x 4 h x 15%
    assert out["central"]["labor_cost_usd"] == pytest.approx(480.0)  # at $40/h
    assert out["low"]["labor_cost_usd"] == 0.0


def test_workers_is_num_crews_times_crew_size():
    c = resolve_crew({**CREW, "num_crews": 3, "crew_size": 7}, CFG["defaults"])
    assert c.workers == 21


def test_null_work_start_hour_defaults_to_seven_and_is_recorded():
    ev = estimate(site(), hot(), None, {**CREW, "work_start_hour": None}, CFG)
    assert ev
    assert ev[0].start.hour == 7
    assert "work_start_hour" in ev[0].assumptions["defaulted"]


def test_null_crew_inputs_fall_back_and_are_recorded():
    ev = estimate(site(), hot(), None, None, CFG)
    d = CFG["defaults"]["crew"]
    assert ev[0].assumptions["workers"] == d["num_crews"] * d["crew_size"]
    assert set(ev[0].assumptions["defaulted"]) == {
        "num_crews", "crew_size", "hours_worked", "work_start_hour", "labor_rate",
    }  # fmt: skip
    assert estimate(site(), hot(), None, CREW, CFG)[0].assumptions["defaulted"] == []


def test_weather_outside_work_window_loses_nothing():
    assert estimate(site(), hot(hour=5), None, CREW, CFG) == []  # before 7 AM
    assert estimate(site(), hot(hour=15), None, CREW, CFG) == []  # window is 7 AM to 3 PM
    assert estimate(site(), hot(hour=14), None, CREW, CFG)
    # a 5 AM start moves the window so the 5 AM observation counts
    assert estimate(site(), hot(hour=5), None, {**CREW, "work_start_hour": 5}, CFG)


def test_hours_lost_per_day_never_exceed_hours_worked():
    ev = estimate(site(), hot(hi_f=140.0), None, {**CREW, "hours_worked": 30}, CFG)
    out = summarize(ev, {**CREW, "hours_worked": 30}, CFG)
    assert (ev[0].end - ev[0].start).total_seconds() <= 24 * 3600
    assert out["high"]["lost_worker_hours"] <= 20 * 24
    # overlapping factors on one day combine, and still cap at the full day
    start = datetime(2026, 7, 15, 7, tzinfo=CHI)
    a = LossEvent(1, start, start.replace(hour=15), "slowdown", 0.9, "high", "heat", "x", "y")
    b = LossEvent(1, start, start.replace(hour=15), "slowdown", 0.9, "high", "cold", "x", "y")
    assert summarize([a, b], CREW, CFG)["high"]["lost_worker_hours"] <= 20 * 8


def test_labor_rate_basis_applies_load_factor():
    start = datetime(2026, 7, 15, 7, tzinfo=CHI)
    e = LossEvent(1, start, start.replace(hour=11), "slowdown", 0.5, "central", "heat", "x", "y")
    loaded = summarize([e], CREW, CFG)["central"]["labor_cost_usd"]
    base = summarize([e], {**CREW, "labor_rate_basis": "base"}, CFG)["central"]["labor_cost_usd"]
    assert base == pytest.approx(
        loaded * CFG["defaults"]["crew"]["base_wage_load_factor"], abs=0.01
    )


def test_hot_day_produces_ordered_scenarios_for_sample_sites():
    for tz, hi in (
        ("America/Chicago", 112.0),
        ("America/Phoenix", 118.0),
        ("America/New_York", 100.0),
    ):
        cond = Cond(
            observed_at=datetime(2026, 7, 15, 12, tzinfo=ZoneInfo(tz)),
            temp_f=100, humidity_pct=50, heat_index_f=hi,
        )  # fmt: skip
        ev = estimate(site(tz), cond, None, CREW, CFG)
        lows, cen, high = (frac(ev, s) for s in ("low", "central", "high"))
        assert 0 < lows <= cen <= high < 1
        assert all(e.factor == "heat" and e.source for e in ev)
        assert ev[0].start.tzinfo is not None and ev[0].start.hour == 7
    assert frac(estimate(site(), hot(hi_f=125), None, CREW, CFG)) > frac(
        estimate(site(), hot(hi_f=100), None, CREW, CFG)
    )


def test_wbgt_route_uses_estimated_wbgt_and_sun_adjustment():
    cfg = {"curves": CFG["curves"], "defaults": {**CFG["defaults"], "heat": {"metric": "wbgt"}}}
    ev = estimate(site(), hot(), None, CREW, cfg)
    assert 0 < frac(ev, "low") <= frac(ev, "central") <= frac(ev, "high")
    assert "wbgt_c" in ev[0].assumptions["metric"]
    # a WBGT curve is never fed heat index
    assert all("heat_index_f" not in e.assumptions["metric"] for e in ev)
    no_inputs = Cond(observed_at=at(12), heat_index_f=110.0)
    assert estimate(site(), no_inputs, None, CREW, cfg) == []


def test_icy_day_and_freezing_rain():
    tz = ZoneInfo("America/Chicago")
    cond = Cond(
        observed_at=datetime(2026, 1, 15, 10, tzinfo=tz), temp_f=29, humidity_pct=90,
        wind_mph=5, weather_code=67, precip_24h_in=0.3,
    )  # fmt: skip
    ev = estimate(site(), cond, None, CREW, CFG)
    assert frac(ev, factor="ice") == pytest.approx(0.5)  # 0.40 * 1.25 accumulation
    assert frac(ev, "low", "ice") <= frac(ev, "central", "ice") <= frac(ev, "high", "ice")
    assert hazard_factors(ev, CREW, CFG)[0]["factor"] == "ice_productivity"
    # snow with temperatures well above freezing does not match
    warm = Cond(observed_at=cond.observed_at, temp_f=45, weather_code=73)
    assert frac(estimate(site(), warm, None, CREW, CFG), factor="ice") == 0.0


def test_cold_uses_wind_chill_not_air_temperature():
    tz = ZoneInfo("America/Chicago")
    t = datetime(2026, 1, 15, 10, tzinfo=tz)
    calm = estimate(site(), Cond(observed_at=t, temp_f=15, wind_mph=0), None, CREW, CFG)
    windy = estimate(site(), Cond(observed_at=t, temp_f=15, wind_mph=25), None, CREW, CFG)
    assert frac(windy, factor="cold") > frac(calm, factor="cold") > 0
    assert windy[0].assumptions["metric"]["wind_chill_f"] < 15


def test_tomorrow_uses_feels_like_and_is_marked_low_confidence():
    days = [
        Day(date(2026, 7, 15), temp_max_f=95, feels_like_max_f=100),
        Day(date(2026, 7, 16), temp_max_f=98, feels_like_max_f=112, weather_code=1),
    ]
    quiet = Cond(observed_at=at(20), temp_f=80, humidity_pct=40, heat_index_f=79)
    ev = estimate(site(), quiet, days, CREW, CFG)  # observation is outside the window
    assert ev and all(e.start.date() == date(2026, 7, 16) for e in ev)
    assert all(e.assumptions["confidence"] == "low" for e in ev)
    assert all("feels_like_max_f" in e.assumptions["metric"] for e in ev)
    assert frac(ev, "low") <= frac(ev, "central") <= frac(ev, "high")
    # tomorrow's cold/ice uses weather_code and temp_max_f
    snow = [days[0], Day(date(2026, 1, 16), temp_max_f=30, feels_like_max_f=25, weather_code=75)]
    ev = estimate(site(), quiet, snow, CREW, CFG)
    assert frac(ev, factor="ice") > 0


def test_hazard_factors_weighted_by_crew_hours():
    ev = estimate(site(), hot(), None, CREW, CFG)
    top = hazard_factors(ev, CREW, CFG)
    assert top == [{"factor": "heat_productivity", "weight": frac(ev)}]
    assert hazard_factors(ev, CREW, CFG, period="tomorrow") == []


def test_estimate_accepts_backend_models_when_available():
    pytest.importorskip("pydantic")
    backend = Path(__file__).resolve().parents[2] / "backend"
    sys.path.insert(0, str(backend))
    try:
        from app.services.open_meteo import CurrentConditions
    except Exception:
        pytest.skip("backend not importable here")
    finally:
        sys.path.remove(str(backend))
    cond = CurrentConditions(
        observed_at=at(12), temp_f=96, humidity_pct=60, heat_index_f=108, feels_like_f=None,
        precip_in=None, soil_moisture=None, wind_mph=None, wind_gust_mph=None, cape_j_kg=None,
        weather_code=None,
    )  # fmt: skip
    assert estimate(site(), cond, None, CREW, CFG)
