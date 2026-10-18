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
    assert aggregate_risk({"heat": 50, "wind": None}) == pytest.approx(100 * (1 - 0.5**0.30))


def test_worked_example() -> None:
    # heat 70, wind 60, ground 40, severe 0 -> 52.20, from the model spec
    scores = {"heat": 70, "wind": 60, "ground": 40, "severe_weather": 0}
    assert aggregate_risk(scores) == pytest.approx(52.20, abs=0.01)

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
    assert result["hazard_score"] == pytest.approx(52.20, abs=0.01)
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
        ("heat", 70.0, 20.79),
        ("wind", 60.0, 15.12),
        ("ground", 40.0, 5.14),
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
    assert result["hazard_score"] == pytest.approx(100 * (1 - (1 - 0.4038) ** 0.30), abs=0.05)


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

    assert result["hazard_score"] == pytest.approx(100 * (1 - 0.45**0.30), abs=0.01)
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
