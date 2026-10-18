"""Current hazard score: a 0-100 environmental severity index for one site's current weather.

Implements .claude/specs/current_hazard_score.md. current_hazard_score() is pure: it scores
whatever readings are usable, treats missing or invalid ones as unavailable (never as safe), and
never raises on bad input. All curves and weights are prototype assumptions, not OSHA thresholds.
"""

import math
from bisect import bisect_right
from collections.abc import Callable, Mapping
from typing import Any

HEAT_POINTS = [(70, 0), (80, 10), (90, 25), (103, 65), (115, 85), (125, 100)]
WIND_POINTS = [(0, 0), (13, 5), (19, 15), (25, 30), (32, 55), (39, 75), (47, 90), (55, 100)]
RAIN_POINTS = [(0, 0), (0.1, 10), (0.5, 35), (1.0, 65), (2.0, 90), (3.0, 100)]
SATURATION_POINTS = [(0.0, 0), (0.25, 10), (0.50, 30), (0.75, 60), (0.90, 80), (1.0, 100)]

POROSITY = 0.45  # assumed mineral-soil porosity, applied to volumetric soil moisture

# WMO weather code -> severe-weather score. Codes not listed here are unavailable, not zero.
SEVERE_WEATHER_SCORES = {
    0: 0, 1: 0, 2: 0, 3: 0,
    45: 20, 48: 35,
    51: 15, 53: 25, 55: 35, 56: 50, 57: 60,
    61: 20, 63: 35, 65: 55, 66: 70, 67: 85,
    71: 30, 73: 55, 75: 80, 77: 35,
    80: 25, 81: 40, 82: 60, 85: 55, 86: 75,
    95: 100, 96: 100, 97: 100, 99: 100,
}  # fmt: skip
THUNDERSTORM_CODES = {95, 96, 97, 99}

WEIGHTS = {"heat": 0.30, "wind": 0.30, "ground": 0.20, "severe_weather": 0.40}
FORECAST_COMPOUND_BONUS = 0.5  # share of the other components' compound hazard added to tomorrow's
MAX_FACTORS = 3


def interpolate_clamped(value: float, points: list[tuple[float, float]]) -> float:
    if value <= points[0][0]:
        return float(points[0][1])
    if value >= points[-1][0]:
        return float(points[-1][1])
    right = bisect_right([x for x, _ in points], value)
    xa, ya = points[right - 1]
    xb, yb = points[right]
    return float(ya + (value - xa) / (xb - xa) * (yb - ya))


def heat_risk(heat_index_f: float) -> float:
    return interpolate_clamped(heat_index_f, HEAT_POINTS)


def wind_risk(wind_mph: float | None, wind_gust_mph: float | None) -> float | None:
    """The higher of the sustained and gust scores, so correlated winds count once."""
    scores = [
        interpolate_clamped(speed, WIND_POINTS)
        for speed in (wind_mph, wind_gust_mph)
        if speed is not None
    ]
    return max(scores) if scores else None


def estimate_saturation(soil_moisture: float) -> float:
    """Volumetric water content (m³/m³) as estimated surface saturation, 0-1."""
    if not 0 <= soil_moisture <= 1:
        raise ValueError("Expected volumetric soil water content between 0 and 1")
    return min(1.0, max(0.0, soil_moisture / POROSITY))


def ground_risk(precip_24h_in: float | None, soil_moisture: float | None) -> float | None:
    """0.3 rain + 0.5 wetness (+ 0.2 interaction when both are known), in points out of 100."""
    if precip_24h_in is None and soil_moisture is None:
        return None
    p = interpolate_clamped(precip_24h_in, RAIN_POINTS) / 100 if precip_24h_in is not None else None
    m = (
        interpolate_clamped(estimate_saturation(soil_moisture), SATURATION_POINTS) / 100
        if soil_moisture is not None
        else None
    )
    interaction = 20 * p * m if p is not None and m is not None else 0
    return (30 * (p or 0)) + (50 * (m or 0)) + interaction


def severe_weather_risk(weather_code: int | None) -> float | None:
    if weather_code is None or weather_code not in SEVERE_WEATHER_SCORES:
        return None
    return float(SEVERE_WEATHER_SCORES[weather_code])


def aggregate_risk(scores: Mapping[str, float | None]) -> float | None:
    """Weighted combination of the available components; missing ones are not renormalized."""
    available = {k: v for k, v in scores.items() if k in WEIGHTS and v is not None}
    if not available:
        return None
    # Clamp so float error can never push a base below 0 (a negative ** fraction is complex).
    survive = (
        (1 - min(max(score / 100, 0.0), 1.0)) ** WEIGHTS[name] for name, score in available.items()
    )
    return 100 * (1 - math.prod(survive))


def aggregate_forecast_risk(scores: Mapping[str, float | None]) -> float | None:
    """Tomorrow's index: the worst component plus half of the others' compound bonus.

    R = 100 * (m + 0.5 * (1 - m) * C), where m is the highest component score (ties go to the
    first of heat, wind, ground, severe weather) and C combines the rest like aggregate_risk.
    A lone component is its own score; missing ones are not renormalized.
    """
    available = {
        name: min(max(score / 100, 0.0), 1.0)
        for name in WEIGHTS
        if (score := scores.get(name)) is not None
    }
    if not available:
        return None
    dominant = max(available, key=available.__getitem__)
    m = available[dominant]
    compound = 1 - math.prod(
        (1 - r) ** WEIGHTS[name] for name, r in available.items() if name != dominant
    )
    return 100 * (m + FORECAST_COMPOUND_BONUS * (1 - m) * compound)


def rank_top_factors(
    scores: Mapping[str, float | None],
    weather_code: int | None,
    reasons: Mapping[str, str],
    limit: int = MAX_FACTORS,
    aggregate: Callable[[Mapping[str, float | None]], float | None] = aggregate_risk,
) -> list[dict[str, Any]]:
    """Components with a positive score, ranked by leave-one-out contribution.

    A thunderstorm always comes first. When a saturated score zeroes the other contributions,
    the component score breaks the tie, so those hazards are still listed. `aggregate` is the
    index the contributions are measured against.
    """
    total = aggregate(scores)
    if total is None:
        return []
    storm = weather_code in THUNDERSTORM_CODES
    candidates = []
    for name, score in scores.items():
        if score is None or score <= 0:
            continue
        impact = max(0.0, total - (aggregate({**scores, name: 0}) or 0.0))
        thunderstorm = name == "severe_weather" and storm
        candidates.append(
            (
                (thunderstorm, impact, score),
                {
                    "factor": "Thunderstorm" if thunderstorm else name,
                    "risk_score": round(score, 2),
                    "contribution": round(impact, 2),
                    "reason": reasons[name],
                },
            )
        )
    candidates.sort(key=lambda c: c[0], reverse=True)
    return [factor for _, factor in candidates[:limit]]


def _number(value: Any, low: float = -math.inf, high: float = math.inf) -> float | None:
    """`value` as a finite float within [low, high]; anything else is unavailable (None)."""
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) and low <= number <= high else None


def _show(value: float | None, unit: str = "") -> str:
    return "unavailable" if value is None else f"{value:g}{unit}"


def current_hazard_score(reading: Mapping[str, Any]) -> dict[str, Any]:
    """Score one site's current weather.

    Reads heat_index_f, wind_mph, wind_gust_mph, precip_24h_in, soil_moisture (raw volumetric
    m³/m³) and weather_code (WMO); other keys are ignored. Returns
    {"hazard_score": 0-100 or None, "top_factors": [...]}; with no usable input, the score is
    None and the factors empty. Never raises.
    """
    heat_index = _number(reading.get("heat_index_f"))
    sustained = _number(reading.get("wind_mph"), low=0)
    gust = _number(reading.get("wind_gust_mph"), low=0)
    rain = _number(reading.get("precip_24h_in"), low=0)
    moisture = _number(reading.get("soil_moisture"), low=0, high=1)
    raw_code = _number(reading.get("weather_code"))
    code = int(raw_code) if raw_code is not None and raw_code.is_integer() else None

    scores = {
        "heat": heat_risk(heat_index) if heat_index is not None else None,
        "wind": wind_risk(sustained, gust),
        "ground": ground_risk(rain, moisture),
        "severe_weather": severe_weather_risk(code),
    }
    wet_note = " Moisture exceeds the assumed soil porosity." if (moisture or 0) > POROSITY else ""
    reasons = {
        "heat": f"Heat index {_show(heat_index, '°F')}; elevated heat can increase heat stress.",
        "wind": (
            f"Wind {_show(sustained, ' mph')}; gusts {_show(gust, ' mph')}; "
            "high winds affect elevated work and lifting."
        ),
        "ground": (
            f"Rain {_show(rain, ' in/24h')}; surface soil moisture {_show(moisture, ' m³/m³')}; "
            f"ground score is a wetness proxy.{wet_note}"
        ),
        "severe_weather": (
            f"Thunderstorm indicated by WMO code {code}; suspend exposed outdoor work and "
            "have a supervisor verify conditions on site."
            if code in THUNDERSTORM_CODES
            else f"WMO code {code} indicates potentially hazardous weather."
        ),
    }
    total = aggregate_risk(scores)
    return {
        "hazard_score": round(total, 2) if total is not None else None,
        "top_factors": rank_top_factors(scores, code, reasons),
    }


def tomorrow_hazard_score(forecast: Mapping[str, Any]) -> dict[str, Any]:
    """Score one site's daily forecast for tomorrow (.claude/specs/forecast_hazard_score.md).

    Reads feels_like_max_f, wind_gust_max_mph, precip_in, precip_probability_pct and
    weather_code; other keys are ignored. Returns {"hazard_score": 0-100 or None, "top_factors":
    [...]}. There is no soil moisture tomorrow, so ground is the full rain subscore: 3 in or more
    scores 100. The max precipitation probability is context for the reason text only, and alone
    it produces no score. Never raises.
    """
    feels = _number(forecast.get("feels_like_max_f"))
    gust = _number(forecast.get("wind_gust_max_mph"), low=0)
    precip = _number(forecast.get("precip_in"), low=0)
    chance = _number(forecast.get("precip_probability_pct"), low=0, high=100)
    raw_code = _number(forecast.get("weather_code"))
    code = int(raw_code) if raw_code is not None and raw_code.is_integer() else None

    scores = {
        "heat": heat_risk(feels) if feels is not None else None,
        "wind": interpolate_clamped(gust, WIND_POINTS) if gust is not None else None,
        "ground": interpolate_clamped(precip, RAIN_POINTS) if precip is not None else None,
        "severe_weather": severe_weather_risk(code),
    }
    chance_note = f"; maximum precipitation probability {chance:g}%" if chance is not None else ""
    reasons = {
        "heat": (
            f"Forecast maximum apparent temperature {_show(feels, '°F')}; "
            "strenuous outdoor work may face heat stress."
        ),
        "wind": (
            f"Forecast maximum wind gust {_show(gust, ' mph')}; "
            "consider lifting and elevated-work exposure."
        ),
        "ground": (
            f"Forecast daily precipitation {_show(precip, ' in')}{chance_note}; conservative "
            "heavy-precipitation alert for possible access, excavation and equipment disruption; "
            "soil moisture is unavailable."
        ),
        "severe_weather": (
            f"Thunderstorm forecast (daily WMO code {code}); plan to suspend exposed work if "
            "storms develop and verify timing and conditions on site."
            if code in THUNDERSTORM_CODES
            else f"Daily WMO code {code} indicates potentially hazardous weather conditions."
        ),
    }
    total = aggregate_forecast_risk(scores)
    return {
        "hazard_score": round(total, 2) if total is not None else None,
        "top_factors": rank_top_factors(scores, code, reasons, aggregate=aggregate_forecast_risk),
    }
