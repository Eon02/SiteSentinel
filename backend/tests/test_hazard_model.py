import json
import math
from typing import Any

import pytest

from app.services.hazard_model import (
    aggregate_risk,
    current_hazard_score,
    estimate_saturation,
    ground_risk,
    heat_risk,
    severe_weather_risk,
    tomorrow_hazard_score,
    wind_risk,
)

EMPTY = {"hazard_score": None, "top_factors": []}


def test_heat_risk() -> None:
    assert heat_risk(95) == pytest.approx(25 + (5 / 13) * 40)
    assert heat_risk(65) == 0
    assert heat_risk(140) == 100


def test_wind_risk_uses_the_worse_of_sustained_and_gust() -> None:
    assert wind_risk(18, 32) == 55
    assert wind_risk(0, 0) == 0
    assert wind_risk(60, 65) == 100
    assert wind_risk(None, 32) == 55
    assert wind_risk(None, None) is None


def test_ground_risk() -> None:
    assert ground_risk(0, 0) == 0
    assert ground_risk(3, 0.45) == 100
    assert ground_risk(0.5, 0.405) == pytest.approx(0.3 * 35 + 0.5 * 80 + 0.2 * 35 * 80 / 100)
    assert estimate_saturation(0.30) == pytest.approx(2 / 3)
    assert ground_risk(None, None) is None


def test_ground_risk_with_one_input_skips_the_interaction() -> None:
    assert ground_risk(0.5, None) == pytest.approx(0.3 * 35)
    assert ground_risk(None, 0.405) == pytest.approx(0.5 * 80)


def test_severe_weather_risk() -> None:
    assert severe_weather_risk(95) == 100
    assert severe_weather_risk(0) == 0
    assert severe_weather_risk(999) is None
    assert severe_weather_risk(None) is None


def test_aggregate_does_not_renormalize_missing_components() -> None:
    assert aggregate_risk({"heat": None, "wind": None}) is None
    assert aggregate_risk({"heat": 50, "wind": None}) == 50  # a lone component is its own score


def test_worked_example() -> None:
    # heat 70, wind 60, ground 40, severe 0: m = 0.7, C = 1 - 0.4^0.3 * 0.6^0.2 -> 74.71
    scores = {"heat": 70, "wind": 60, "ground": 40, "severe_weather": 0}
    assert aggregate_risk(scores) == pytest.approx(74.71, abs=0.01)

    # The same scenario from raw readings: heat index 106 -> 70, gust 33.75 -> 60 (the lighter
    # sustained wind loses), soil moisture 0.405 -> wetness 80 -> ground 40, clear sky -> 0.
    result = current_hazard_score(
        {
            "heat_index_f": 106,
            "wind_mph": 10,
            "wind_gust_mph": 33.75,
            "soil_moisture": 0.405,
            "weather_code": 0,
        }
    )
    assert result["hazard_score"] == pytest.approx(74.71, abs=0.01)
    assert [(f["factor"], f["risk_score"]) for f in result["top_factors"]] == [
        ("heat", 70.0),
        ("wind", 60.0),
        ("ground", 40.0),
    ]


def test_leave_one_out_contributions_match_the_spec() -> None:
    from app.services.hazard_model import rank_top_factors

    scores = {"heat": 70, "wind": 60, "ground": 40, "severe_weather": 0}
    reasons = dict.fromkeys(scores, "r")
    ranked = rank_top_factors(scores, 0, reasons)

    assert [(f["factor"], f["risk_score"], f["contribution"]) for f in ranked] == [
        ("heat", 70.0, 12.77),
        ("wind", 60.0, 3.26),
        ("ground", 40.0, 1.11),
    ]


def test_thunderstorm_dominates_and_comes_first() -> None:
    result = current_hazard_score(
        {"weather_code": 95, "heat_index_f": 120, "wind_gust_mph": 30, "precip_24h_in": 1}
    )

    assert result["hazard_score"] == 100
    first = result["top_factors"][0]
    assert first["factor"] == "Thunderstorm"
    assert "WMO code 95" in first["reason"]
    assert len(result["top_factors"]) == 3
    # Saturation zeroes the other contributions, but they're still listed, ranked by score.
    assert [f["factor"] for f in result["top_factors"][1:]] == ["heat", "wind"]


def test_missing_data() -> None:
    assert current_hazard_score({}) == EMPTY
    assert current_hazard_score({"heat_index_f": 95})["hazard_score"] is not None
    assert current_hazard_score({"wind_mph": None, "wind_gust_mph": 32})["hazard_score"] is not None
    assert current_hazard_score({"precip_24h_in": 0.5, "soil_moisture": None})["hazard_score"]
    assert current_hazard_score({"weather_code": 95})["hazard_score"] == 100
    assert current_hazard_score({"weather_code": 999}) == EMPTY


def test_missing_components_are_not_called_safe() -> None:
    result = current_hazard_score({"heat_index_f": 95, "wind_gust_mph": None})

    assert [f["factor"] for f in result["top_factors"]] == ["heat"]
    assert result["hazard_score"] == pytest.approx(40.38, abs=0.01)  # heat alone, not renormalized


@pytest.mark.parametrize(
    "bad",
    [
        {"heat_index_f": "abc"},
        {"heat_index_f": float("nan")},
        {"heat_index_f": float("inf")},
        {"heat_index_f": True},
        {"heat_index_f": [95]},
        {"heat_index_f": 10**400},
        {"wind_mph": -1},
        {"wind_gust_mph": -5},
        {"precip_24h_in": -0.1},
        {"soil_moisture": 1.5},
        {"soil_moisture": -0.1},
        {"weather_code": 95.5},
        {"weather_code": 999},
        {"weather_code": "storm"},
        {"weather_code": True},
    ],
)
def test_invalid_input_is_unavailable_and_never_raises(bad: dict[str, Any]) -> None:
    assert current_hazard_score(bad) == EMPTY


def test_invalid_input_does_not_hide_valid_input() -> None:
    result = current_hazard_score({"heat_index_f": "abc", "wind_gust_mph": 32, "weather_code": 999})

    assert result["hazard_score"] == 55  # the wind score alone
    assert [f["factor"] for f in result["top_factors"]] == ["wind"]


def test_numeric_strings_are_accepted() -> None:
    assert current_hazard_score({"heat_index_f": "95"}) == current_hazard_score(
        {"heat_index_f": 95}
    )


def test_extra_keys_are_ignored() -> None:
    reading = {
        "site_id": "6045",
        "observed_at": "2026-08-14T06:45",
        "temp_f": 99,
        "humidity_pct": 1,
    }
    assert current_hazard_score(reading) == EMPTY


def test_scores_stay_in_range_and_output_is_json_safe() -> None:
    extremes = {
        "heat_index_f": 500,
        "wind_mph": 500,
        "wind_gust_mph": 500,
        "precip_24h_in": 500,
        "soil_moisture": 1,
        "weather_code": 99,
    }
    result = current_hazard_score(extremes)

    assert result["hazard_score"] == 100
    assert all(0 < f["risk_score"] <= 100 for f in result["top_factors"])
    assert not math.isnan(result["hazard_score"])
    assert json.loads(json.dumps(result)) == result


@pytest.mark.parametrize(
    "reading",
    [
        {"heat_index_f": 95},
        {"wind_gust_mph": 32},
        {"precip_24h_in": 0.5},
        {"soil_moisture": 0.3},
        {"weather_code": 65},
        {"weather_code": 95},
        {"soil_moisture": 0.6 * 0.45, "precip_24h_in": 2, "weather_code": 63},
    ],
)
def test_reasons_never_mention_none(reading: dict[str, Any]) -> None:
    for factor in current_hazard_score(reading)["top_factors"]:
        assert "None" not in factor["reason"]


def test_ground_reason_warns_when_moisture_exceeds_porosity() -> None:
    result = current_hazard_score({"soil_moisture": 0.6})

    assert "exceeds the assumed soil porosity" in result["top_factors"][0]["reason"]


# --- Tomorrow's forecast score -------------------------------------------------------------


def test_aggregate_keeps_the_worst_component_and_adds_half_the_compound() -> None:
    scores = {"heat": 70, "wind": 60, "ground": None, "severe_weather": None}
    compound = 1 - (1 - 0.6) ** 0.30  # wind is the only other component
    assert aggregate_risk(scores) == pytest.approx(100 * (0.7 + 0.5 * 0.3 * compound))


def test_aggregate_of_one_component_is_that_component() -> None:
    assert aggregate_risk({"wind": 42.5}) == pytest.approx(42.5)
    assert aggregate_risk({"heat": None, "wind": None}) is None
    assert aggregate_risk({"heat": 100, "wind": 10}) == 100


def test_aggregate_breaks_ties_in_a_stable_order() -> None:
    # Equal scores: whichever is dominant, the result is the same and deterministic.
    scores = {"wind": 50, "heat": 50, "ground": 50, "severe_weather": 50}
    assert aggregate_risk(scores) == aggregate_risk(dict(reversed(scores.items())))


def test_forecast_reference_assertions() -> None:
    assert tomorrow_hazard_score({}) == EMPTY
    assert tomorrow_hazard_score({"feels_like_max_f": 95})["hazard_score"] is not None
    assert tomorrow_hazard_score({"wind_gust_max_mph": 32})["hazard_score"] is not None
    assert tomorrow_hazard_score({"precip_in": 1})["top_factors"][0]["risk_score"] == 65
    assert tomorrow_hazard_score({"precip_in": 3})["hazard_score"] == 100
    assert tomorrow_hazard_score({"precip_in": 3})["top_factors"][0]["factor"] == "ground"
    assert tomorrow_hazard_score({"precip_probability_pct": 90}) == EMPTY
    assert tomorrow_hazard_score({"weather_code": 95})["hazard_score"] == 100
    assert tomorrow_hazard_score({"weather_code": 95})["top_factors"][0]["factor"] == "Thunderstorm"
    assert tomorrow_hazard_score({"weather_code": 999}) == EMPTY
    assert tomorrow_hazard_score({"wind_gust_max_mph": -3}) == EMPTY
    assert tomorrow_hazard_score({"precip_in": float("nan")}) == EMPTY


def test_forecast_ground_is_the_full_rain_subscore_with_no_soil_moisture() -> None:
    assert tomorrow_hazard_score({"precip_in": 0.5})["hazard_score"] == 35
    assert tomorrow_hazard_score({"precip_in": 2})["hazard_score"] == 90
    assert tomorrow_hazard_score({"precip_in": 0}) == {"hazard_score": 0.0, "top_factors": []}


def test_forecast_thunderstorm_dominates_and_lists_the_other_hazards() -> None:
    result = tomorrow_hazard_score(
        {"weather_code": 96, "feels_like_max_f": 115, "wind_gust_max_mph": 32, "precip_in": 0.5}
    )

    assert result["hazard_score"] == 100
    assert [f["factor"] for f in result["top_factors"]] == ["Thunderstorm", "heat", "wind"]
    assert "plan to suspend exposed work" in result["top_factors"][0]["reason"]


def test_forecast_probability_is_context_only() -> None:
    plain = tomorrow_hazard_score({"precip_in": 1})
    with_chance = tomorrow_hazard_score({"precip_in": 1, "precip_probability_pct": 90})

    assert with_chance["hazard_score"] == plain["hazard_score"] == 65  # not scaled by 90%
    assert "maximum precipitation probability 90%" in with_chance["top_factors"][0]["reason"]
    assert "probability" not in plain["top_factors"][0]["reason"]
    invalid = tomorrow_hazard_score({"precip_in": 1, "precip_probability_pct": 150})
    assert "probability" not in invalid["top_factors"][0]["reason"]
    assert invalid["hazard_score"] == 65


def test_forecast_ignores_the_actual_date_and_other_keys() -> None:
    forecast = {"date": "2026-08-15", "temp_max_f": 140, "precip_in": 1}
    assert tomorrow_hazard_score(forecast) == tomorrow_hazard_score({"precip_in": 1})


@pytest.mark.parametrize(
    "bad",
    [
        {"feels_like_max_f": "abc"},
        {"feels_like_max_f": True},
        {"feels_like_max_f": float("inf")},
        {"wind_gust_max_mph": -1},
        {"precip_in": -0.1},
        {"weather_code": 95.5},
        {"weather_code": 999},
    ],
)
def test_forecast_invalid_input_is_unavailable_and_never_raises(bad: dict[str, Any]) -> None:
    assert tomorrow_hazard_score(bad) == EMPTY


@pytest.mark.parametrize(
    "forecast",
    [
        {"feels_like_max_f": 95},
        {"wind_gust_max_mph": 32},
        {"precip_in": 1},
        {"precip_in": 1, "precip_probability_pct": 80},
        {"weather_code": 65},
        {"weather_code": 95},
        {"feels_like_max_f": 100, "wind_gust_max_mph": 40, "precip_in": 2, "weather_code": 63},
    ],
)
def test_forecast_reasons_never_mention_none(forecast: dict[str, Any]) -> None:
    result = tomorrow_hazard_score(forecast)
    assert result["top_factors"]
    for factor in result["top_factors"]:
        assert "None" not in factor["reason"]
        assert factor["reason"].startswith(("Forecast", "Thunderstorm forecast", "Daily"))


def test_forecast_output_is_json_safe_and_bounded() -> None:
    result = tomorrow_hazard_score(
        {
            "feels_like_max_f": 500,
            "wind_gust_max_mph": 500,
            "precip_in": 500,
            "weather_code": 99,
        }
    )

    assert result["hazard_score"] == 100
    assert len(result["top_factors"]) == 3
    assert json.loads(json.dumps(result)) == result
