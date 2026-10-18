# Tomorrow Utility Construction Hazard Score — Specification

**Status:** Draft v0.3 — aggressive forecast precipitation sensitivity; deterministic daily forecast model based on current hazard index v0.7.

## 1. Purpose and limitations

Estimate a **tomorrow environmental hazard index** (0–100) for outdoor electric utility construction from Open-Meteo daily forecasts. The public result is exactly `hazard_score` and `top_factors`. It is **not** a calibrated probability of closure, an hourly safety assessment, or an automatic stop-work decision. A forecast day's highest apparent temperature, maximum wind gust, and most severe WMO condition may occur at different times; this model conservatively summarizes daily hazards rather than claiming they co-occur.

## 2. Input contract

| Field | Type | Use |
|---|---|---|
| `date` | `YYYY-MM-DD` | Metadata only; no freshness checks |
| `temp_max_f` | °F | Context only (do not double-count heat) |
| `feels_like_max_f` | °F | Primary **apparent-temperature** heat proxy |
| `precip_in` | inches/day | Daily total precipitation; rainfall/wetness exposure proxy |
| `precip_probability_pct` | 0–100% | Context/uncertainty only; do **not** multiply precipitation total by this maximum probability |
| `wind_gust_max_mph` | mph | Primary wind hazard; peak gust at 10 m |
| `weather_code` | integer | Most severe WMO-coded weather condition in forecast day |

Open-Meteo `apparent_temperature_max` is *apparent temperature*, **not necessarily heat index**. Reusing the heat-index curve is a stated MVP approximation. `precipitation_sum` may include rain, showers, and snowfall water equivalent: do not call all `precip_in` liquid rain in explanations. `precipitation_probability_max` is the **maximum** forecast probability in the day, not the probability of the whole day's aggregate precipitation and not a confidence level for the daily total. Daily WMO codes identify the most severe coded condition; they do not establish storm timing, a local lightning strike, or duration.

## 3. Public output and missing data

```json
{
  "hazard_score": 72.5,
  "top_factors": [
    {"factor": "heat", "risk_score": 85.0, "contribution": 21.34, "reason": "Forecast peak apparent temperature is 115°F; heat stress may affect strenuous outdoor work."}
  ]
}
```

*Illustrative only; values are not computed from a common scenario.* Each factor has `factor`, `risk_score`, `contribution`, `reason`. Maximum 3 factors; put `Thunderstorm` first if forecast WMO code 95/96/97/99. If every hazard measurement is missing or invalid, return `{"hazard_score": null, "top_factors": []}`. Missing inputs are excluded without weight renormalization and are not asserted safe; a partial result can understate risk. Treat non-finite/malformed inputs, boolean numbers, negative precipitation/wind, probability outside 0–100, and unknown or nonintegral weather codes as unavailable. No freshness checks, no site procedure flags, no separate stop-work flag.

## 4. Component hazard functions (0–100)

### 4.1 Forecast heat

Re-use **current** heat breakpoints, but feed in `feels_like_max_f`, not measured heat index:

| Apparent temperature °F | 70 | 80 | 90 | 103 | 115 | 125 |
|---|---:|---:|---:|---:|---:|---:|
| Score | 0 | 10 | 25 | 65 | 85 | 100 |

Linearly interpolate between anchors, clamp outside. Label output a *forecast apparent-temperature hazard proxy* (not OSHA heat index/WBGT).

### 4.2 Forecast wind

Use existing Beaufort-inspired gust curve with `wind_gust_max_mph` (an intentionally conservative shortcut; Beaufort formally describes sustained winds):

| Gust mph | 0 | 13 | 19 | 25 | 32 | 39 | 47 | 55 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Score | 0 | 5 | 15 | 30 | 55 | 75 | 90 | 100 |

Linear interpolation and clamping. No sustained wind available. Equipment-specific restrictions are not evaluated.

### 4.3 Forecast precipitation / ground-conditions proxy

Forecast has **no soil moisture**. Do **not** invent tomorrow's surface saturation or imply the current ground function is fully calculated. Use the same precipitation breakpoints:

| Daily precipitation (in) | 0 | 0.10 | 0.50 | 1.00 | 2.00 | 3.00 |
|---|---:|---:|---:|---:|---:|---:|
| Rain subscore | 0 | 10 | 35 | 65 | 90 | 100 |

For the hackathon, **aggressively flag heavy forecast precipitation**, even though this may increase false alarms. The **forecast ground proxy** uses the full precipitation subscore, rather than the current model’s reduced rain-only branch:

\[
r_{G,forecast} = \operatorname{RainSubscore}(precip\_in).
\]

It ranges **0–100**: 1.0 inch yields component score **65**, 2.0 inches yields **90**, and 3.0+ inches yields **100**. Because the aggregator saturates when any component equals 100, **3.0+ inches forecasts an overall hazard score of 100**, even with otherwise favorable weather. This is an intentionally conservative *screening signal*, **not** a claim that OSHA mandates full-site closure or that rain will necessarily make work impossible. It can overstate shutdown risk when sites have effective drainage, protected access, or non-ground-disturbing work. The ground hazard weight remains 0.20; do **not** multiply the rain subscore by precipitation probability again. The daily total forecast already models an amount; the **maximum** precipitation probability is not a defensible second multiplier. Include max forecast probability in `reason` when valid, only as context. When `precip_in` is missing but probability is present, precipitation *score remains unavailable*: probability alone supplies no modeled daily quantity in this MVP.

**Trade-off / explicit policy note:** This forecast-only change prioritizes **sensitivity over specificity**: heavy-rain forecasts should be flagged aggressively even at the cost of **false positives**. Tomorrow’s forecast index and today’s observed index are **not directly comparable** because their ground components use different functions and different information. This is a prototype assumption to validate against real disruption/closure data. Do not interpret high scores as mandatory closure.

### 4.4 Forecast severe weather

Reuse **exactly the same WMO lookup table** as the current spec:

- `0,1,2,3`: 0 each
- `45:20, 48:35`
- `51:15, 53:25, 55:35, 56:50, 57:60`
- `61:20, 63:35, 65:55, 66:70, 67:85`
- `71:30, 73:55, 75:80, 77:35`
- `80:25, 81:40, 82:60, 85:55, 86:75`
- **`95,96,97,99`: 100** (thunderstorm-compatible codes; 97 retained for compatibility with current model although provider code lists may vary)

If a forecast WMO code is a thunderstorm code, the **daily** overall risk is 100 and the first top factor is `Thunderstorm`, with text recommending that supervisors plan to suspend exposed work **if thunderstorms develop** and check timing/actual conditions. Forecast WMO code is not proof of lightning over the site. Code is daily most-severe condition rather than current weather. Unknown code -> missing, not safe.

## 5. Aggregate and explain — v2 max hazard + compound bonus

Use the same v2 aggregation as the current model: among *available* component scores `r_i=s_i/100`, select dominant `k` with maximum score; use a stable tie order of heat, wind, ground, severe_weather. Define `m=r_k` and:

\[
C=1-\prod_{i\in A,\ i\ne k}(1-r_i)^{w_i}
\]
\[
\boxed{R=100[m+0.5(1-m)C]}
\]

| Heat | Wind | Ground | Severe weather |
|---:|---:|---:|---:|
| 0.30 | 0.30 | 0.20 | 0.40 |

This preserves the highest component score, then adds half the compound bonus from the others. If only one component is present, the overall equals its score; missing components are excluded without renormalization. If none are available, return `null`. Any 100 component (including a thunderstorm forecast or ≥3 inches forecast rain under our deliberately aggressive rain curve) produces overall 100. These are **heuristic scores**, not closure probabilities.

Calculate top factors with the same leave-one-out attribution as before, but **recompute with the new aggregation** after setting each hazard score to zero. Contributions are not additive shares; rank by contribution, then component score, with thunderstorm first. Keep the exact public shape `hazard_score`, `top_factors` and each factor's `reason`.

**Limitation:** Selecting one maximum with unequal weights can introduce small discontinuities and local non-monotonic behavior near ties, even with deterministic tie-breaking. This should be sensitivity-tested before operational use.

Our prototype interpretation bands are below 50: routine monitoring; 50–<85: investigate; 85–100: strong recommendation to review suspension of affected operations. Not calibrated safety thresholds.

## 6. Python reference implementation

The standalone code below mirrors current v0.7 while changing forecast-specific inputs and rainfall handling.

```python
from bisect import bisect_right
from math import isfinite, prod

HEAT_POINTS = [(70, 0), (80, 10), (90, 25), (103, 65), (115, 85), (125, 100)]
WIND_POINTS = [(0, 0), (13, 5), (19, 15), (25, 30), (32, 55), (39, 75), (47, 90), (55, 100)]
RAIN_POINTS = [(0, 0), (0.1, 10), (0.5, 35), (1.0, 65), (2.0, 90), (3.0, 100)]
WEIGHTS = {"heat": 0.30, "wind": 0.30, "ground": 0.20, "severe_weather": 0.40}
THUNDERSTORM_CODES = {95, 96, 97, 99}
WEATHER = {
    0: 0, 1: 0, 2: 0, 3: 0, 45: 20, 48: 35,
    51: 15, 53: 25, 55: 35, 56: 50, 57: 60,
    61: 20, 63: 35, 65: 55, 66: 70, 67: 85,
    71: 30, 73: 55, 75: 80, 77: 35,
    80: 25, 81: 40, 82: 60, 85: 55, 86: 75,
    95: 100, 96: 100, 97: 100, 99: 100,
}


def valid_number(data, key, low=None, high=None):
    raw = data.get(key)
    if raw is None or isinstance(raw, bool):
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if not isfinite(value) or (low is not None and value < low) or (high is not None and value > high):
        return None
    return value


def interp(x, points):
    if x <= points[0][0]: return float(points[0][1])
    if x >= points[-1][0]: return float(points[-1][1])
    j = bisect_right([v for v, _ in points], x)
    xa, ya = points[j - 1]
    xb, yb = points[j]
    return ya + (x - xa) / (xb - xa) * (yb - ya)


LAMBDA = 0.5  # compound bonus strength

def aggregate(scores):
    available = {k: scores[k] / 100 for k in WEIGHTS if scores.get(k) is not None}
    if not available:
        return None
    dominant = max(available, key=available.get)  # stable tie-breaking
    m = available[dominant]
    compound = 1 - prod(
        (1 - r) ** WEIGHTS[name]
        for name, r in available.items() if name != dominant
    )
    return 100 * (m + LAMBDA * (1 - m) * compound)


def tomorrow_hazard_score(forecast: dict) -> dict:
    feels = valid_number(forecast, 'feels_like_max_f')
    gust = valid_number(forecast, 'wind_gust_max_mph', low=0)
    precip = valid_number(forecast, 'precip_in', low=0)
    chance = valid_number(forecast, 'precip_probability_pct', low=0, high=100)
    raw_code = valid_number(forecast, 'weather_code')
    code = int(raw_code) if raw_code is not None and raw_code.is_integer() else None

    scores = {
        'heat': interp(feels, HEAT_POINTS) if feels is not None else None,
        'wind': interp(gust, WIND_POINTS) if gust is not None else None,
        'ground': interp(precip, RAIN_POINTS) if precip is not None else None,
        'severe_weather': float(WEATHER[code]) if code in WEATHER else None,
    }
    total = aggregate(scores)
    if total is None:
        return {'hazard_score': None, 'top_factors': []}

    probability_text = (f'; maximum precipitation probability {chance:g}%'
                        if chance is not None else '')
    reasons = {
        'heat': f'Forecast maximum apparent temperature {feels:g}°F; strenuous outdoor work may face heat stress.' if feels is not None else '',
        'wind': f'Forecast maximum wind gust {gust:g} mph; consider lifting and elevated-work exposure.' if gust is not None else '',
        'ground': f'Forecast daily precipitation {precip:g} in{probability_text}; conservative heavy-rain alert for possible access, excavation, and equipment disruption; soil moisture is unavailable.' if precip is not None else '',
        'severe_weather': (
            f'Thunderstorm forecast (daily WMO code {code}); plan to suspend exposed work if storms occur and verify conditions.'
            if code in THUNDERSTORM_CODES else
            f'Daily WMO code {code} indicates potentially hazardous weather conditions.'
        ) if code in WEATHER else '',
    }
    factors = []
    for name, score in scores.items():
        if score is None or score <= 0:
            continue
        without = dict(scores, **{name: 0})
        impact = max(0, total - aggregate(without))
        thunder = name == 'severe_weather' and code in THUNDERSTORM_CODES
        factors.append({
            'factor': 'Thunderstorm' if thunder else name,
            'risk_score': round(score, 2),
            'contribution': round(impact, 2),
            'reason': reasons[name],
            '_sort': (thunder, impact, score),
        })
    factors.sort(key=lambda item: item['_sort'], reverse=True)
    return {
        'hazard_score': round(total, 2),
        'top_factors': [{k: v for k, v in item.items() if k != '_sort'} for item in factors[:3]],
    }
```

### 6.1 Reference tests

```python
assert tomorrow_hazard_score({}) == {'hazard_score': None, 'top_factors': []}
assert tomorrow_hazard_score({'feels_like_max_f': 95})['hazard_score'] is not None
assert tomorrow_hazard_score({'wind_gust_max_mph': 32})['hazard_score'] is not None
assert abs(tomorrow_hazard_score({'precip_in': 1})['top_factors'][0]['risk_score'] - 65) < 1e-8
assert tomorrow_hazard_score({'precip_in': 3})['hazard_score'] == 100
assert tomorrow_hazard_score({'precip_in': 3})['top_factors'][0]['factor'] == 'ground'
assert tomorrow_hazard_score({'precip_probability_pct': 90})['hazard_score'] is None
assert tomorrow_hazard_score({'weather_code': 95})['hazard_score'] == 100
assert tomorrow_hazard_score({'weather_code': 95})['top_factors'][0]['factor'] == 'Thunderstorm'
assert tomorrow_hazard_score({'weather_code': 999})['hazard_score'] is None
assert tomorrow_hazard_score({'wind_gust_max_mph': -3})['hazard_score'] is None
assert tomorrow_hazard_score({'precip_in': float('nan')})['hazard_score'] is None
```

## 7. Assumptions and next decisions

1. Validate the heat-risk curve on **apparent temperature**; it was initially chosen for measured heat index, which is not always the same quantity.
2. **Decision locked for MVP:** Forecast precipitation uses the **full rainfall subscore** (`ground=rain_score`) to aggressively flag heavy rain, accepting more false positives. Keep this explicit in presentations; adjust based on site-disruption evidence.
3. We have no hourly forecast timing; maxima and most-severe code may not occur during the work shift.
4. No soil moisture tomorrow; rainfall-only model cannot estimate ground saturation. Despite aggressive rain scoring, it can still miss waterlogged sites following previous days of rain.
5. Rainfall severity and WMO rain intensity can overlap; explanations should not claim statistically independent contributions.
6. Validate assumptions and weights against real operations or closure labels; no claim of a calibrated closure probability.

## 8. Sources

- [Open-Meteo Forecast API](https://open-meteo.com/en/docs): daily aggregates, apparent temperature max, daily precipitation sum, precipitation probability max, peak wind gusts, and daily weather code.
- [NWS Beaufort Scale](https://www.weather.gov/mfl/beaufort): basis of wind-speed anchors, formally sustained wind.
- [OSHA Heat](https://www.osha.gov/heat-exposure): contextual heat guidance; work-related heat risk is not fully captured by apparent temperature.
- [OSHA Electric Power Transmission and Distribution](https://www.osha.gov/laws-regs/regulations/standardnumber/1926/1926.964): activity-specific adverse-weather precautions, not universal numeric wind/heat shutdown points.
- Current Utility Construction Hazard Score Specification v0.7: inherited curves, aggregation weights, scoring policy, and attribution method.
