# Current Utility Construction Hazard Score — Specification

**Status:** Draft v0.7 (graceful missing-data handling; timestamp freshness and site-procedure logic out of scope)

## 1. Purpose

Estimate a **current environmental hazard index** for an outdoor electric utility construction site, with a transparent breakdown of the main contributing hazards. The score describes modeled environmental severity—not a probability of injury or site closure, and not a substitute for job-specific safety decisions.

**Scope:** Transmission/distribution work, substations, excavation, heavy equipment, elevated work. Current observations only; a separate forecast model will be specified later.

## 2. Input contract

| Field | Type | Units | Use |
|---|---|---|---|
| `site_id` | string | — | Identify site |
| `observed_at` | ISO 8601 datetime | — | Metadata only; no freshness checking |
| `temp_f` | number | °F | Context and validation; avoid counting heat twice |
| `heat_index_f` | number | °F | Primary heat input |
| `precip_24h_in` | number | inches in previous 24h | Ground hazard |
| `soil_moisture` | number | m³/m³, volumetric water content, top 0–1 cm (`soil_moisture_0_to_1cm`) | Ground hazard via estimated surface saturation (assumed porosity 0.45) |
| `weather_code` | integer | Open-Meteo WMO interpretation code | Future storm/other-weather risk |
| `wind_mph` | number | mph | Sustained/mean wind (confirm source averaging period) |
| `wind_gust_mph` | number | mph | Gust wind |
| `humidity_pct` | number | % | Context and validation; avoid counting heat twice |

**Missing-data policy (MVP):** All environmental measurements are optional and may be `null` or missing. Treat `None`, non-finite numbers, malformed numbers, negative rain/wind, out-of-range fractions/percentages, and unknown WMO codes as *unavailable*, not as actual zero hazard. Try to do this check in few lines of code. Do not crash or substitute fabricated weather values. Calculate available component scores only and exclude missing components from aggregation rather than reporting them as observed-safe. Do **not** renormalize remaining weights; the resulting index is based on incomplete evidence and can understate hazards. If no usable hazard input exists, return `{"hazard_score": null, "top_factors": []}`. Keep public output exactly those two fields; retain `reason` inside each factor. `site_id` and `observed_at` are metadata and do not affect risk; **ignore freshness**. For wind, use whichever of sustained or gust reading is available. For ground, when only rain is available use `100 * 0.3*p`; when only soil moisture is available use `100 * 0.5*m`; add the `0.2*p*m` interaction **only if both** are available. Missing inputs are never called safe in the explanation. With missing measurements, the result is a partial hazard index, not an all-clear.

Validate `soil_moisture` as raw volumetric fraction (e.g., `0.30`, not 30); convert to estimated surface saturation using porosity `0.45`. The top 0–1 cm soil layer does not measure subsurface bearing capacity.

## 3. Outputs

Keep the **public scoring result** minimal: `hazard_score` and `top_factors`. The `site_id` and observation time belong to the surrounding site record/request, not the scoring result. Internal components and input diagnostics can be computed without adding extra fields to the public result.

```json
{
  "hazard_score": 100,
  "top_factors": [
    {
      "factor": "Thunderstorm",
      "risk_score": 100,
      "contribution": 100.0,
      "reason": "Thunderstorm indicated by WMO code 95. Suspend exposed outdoor utility work and verify conditions on site."
    }
  ]
}
```

This is an **illustrative** scenario, not a measured site result. With `severe_weather=100`, the aggregate is 100, the thunderstorm leave-one-out contribution depends on the other hazard scores (the illustrative `100.0` above assumes all other hazards score zero); other factors would have zero leave-one-out contribution due to saturation. `risk_score` and `contribution` are points, **not probabilities**.

**No separate stop-work flag.** Severe-weather safety instructions appear in the relevant `top_factors[].reason`. A thunderstorm hazard must be included in the top factors regardless of rank. A WMO code alone cannot establish that lightning is absent or that work can safely resume.

## 4. Component hazard scores

Each component returns a value on **0–100**. These numerical score mappings are provisional design assumptions, not OSHA-issued quantitative risk scores.

### 4.1 Heat hazard

Use `heat_index_f` as the primary measurement. **Piecewise linear interpolation** between these anchors:

| Heat index (°F) | Heat hazard (0–100) |
|---:|---:|
| 70 | 0 |
| 80 | 10 |
| 90 | 25 |
| 103 | 65 |
| 115 | 85 |
| 125 | 100 |

For input `x` between anchors `(x_a, r_a)` and `(x_b, r_b)`:

\[
r(x)=r_a+\frac{x-x_a}{x_b-x_a}(r_b-r_a).
\]

Below 70°F clamp to 0, above 125°F clamp to 100. Example: at 95°F, score = `25 + (95 - 90) / (103 - 90) * (65 - 25) ≈ 40.38`.

**Research grounding:** The NWS/OSHA heat-index caution/danger categories motivate where anchors are placed; the assigned scores are *our own policy/modeling assumptions*. Heat index omits work intensity, radiant heat, acclimatization, PPE, etc. WBGT is generally more appropriate for occupational heat exposure if available.

### 4.2 Wind hazard

Start with Beaufort-inspired anchors, with proposed (not official) score mapping:

| Wind speed (mph) | Wind hazard (0–100) |
|---:|---:|
| 0 | 0 |
| 13 | 5 |
| 19 | 15 |
| 25 | 30 |
| 32 | 55 |
| 39 | 75 |
| 47 | 90 |
| 55 | 100 |

Use piecewise linear interpolation for `wind_mph` and `wind_gust_mph` separately. For this MVP, apply the **same curve** to gusts as a conservative shortcut, **not** as an assertion that the Beaufort scale classifies gusts:

\[
r_{wind}=\max(f(wind\_mph),f(wind\_gust\_mph)).
\]

Keep `sustained_score`, `gust_score`, and `dominant_input` as diagnostics, but expose **one** wind component score. This prevents double-counting correlated wind measurements.

**Safety interpretation:** OSHA does not prescribe one universal wind limit for all utility construction. Wind score alone is not a mandatory full-site closure threshold; this MVP does not implement task-specific or site-procedure rules.

### 4.3 Ground and water hazard — provisional surface-wetness proxy

Inputs: 24-hour precipitation `P = precip_24h_in` (inches) and volumetric surface soil water content `M = soil_moisture_0_to_1cm` (m³/m³). Open-Meteo reports water volume / total soil volume in the top 0–1 cm, **not percent saturation**.

For the hackathon MVP, **assume constant soil porosity `φ = 0.45`** (45% void space), as a rough mineral-soil default. Convert volumetric water content to estimated surface saturation:

\[
S=\min\left(1,\max\left(0,\frac{M}{0.45}\right)\right).
\]

For example, `M = 0.30` corresponds to `S ≈ 0.667` (66.7% estimated saturation); `M = 0.40` corresponds to `S ≈ 0.889`. This assumption is configurable and is **not calibrated to site soil**; clipping at 1 masks readings above the assumed porosity, so report a quality warning if `M > 0.45`.

**Physical interpretation:** High surface volumetric water content may indicate a wet or muddy top layer, especially after recent precipitation. It cannot by itself prove ponding, subgrade bearing strength, crane outrigger stability, excavation stability, or road trafficability. Soil texture, porosity, depth profile, drainage, slope, and site preparation all matter. Accordingly, the ground score is a **screening proxy**, not a go/no-go engineering assessment.

**Precipitation curve `p(P)` (normalized 0–1)** (illustrative, not OSHA thresholds):

| 24h precipitation (in) | Rain subscore (0–100) |
|---:|---:|
| 0 | 0 |
| 0.10 | 10 |
| 0.50 | 35 |
| 1.00 | 65 |
| 2.00 | 90 |
| 3.00 | 100 |

**Estimated surface saturation curve `m(S)` (normalized 0–1):** The saturation anchor values and scores below are **illustrative**, not OSHA thresholds, measured loss-of-traction thresholds, or bearing-capacity thresholds. They use assumed porosity, not a soil survey.

| Estimated surface saturation `S` (fraction of pore space) | Wetness subscore (0–100) |
|---:|---:|
| 0.00 | 0 |
| 0.25 | 10 |
| 0.50 | 30 |
| 0.75 | 60 |
| 0.90 | 80 |
| 1.00 | 100 |

Interpolate linearly, divide subscores by 100, and use the existing model:

\[
\boxed{r_G=100(0.3p+0.5m+0.2pm)}
\]

Here `p = rain_score / 100` and `m = saturation_wetness_score / 100`. The interaction models rain falling on an already wet surface. Scores are monotonic and bounded 0–100. Diagnostics: `rain_score`, `moisture_score`, `interaction_points = 20*p*m`, raw `soil_moisture`, `assumed_porosity=0.45`, `estimated_surface_saturation=S`, and `soil_moisture_depth="0-1cm"`. Rank *ground* as one top-level contributor; show rain, saturation, and their interaction as underlying reasons, not independent top-level factors.

**Critical interpretation:** OSHA 29 CFR 1926.651(k)(1) requires competent-person excavation inspections after every rainstorm when worker exposure is reasonably anticipated; §1926.651(h) restricts work with accumulating water without adequate precautions. Neither regulation establishes rainfall or surface volumetric-water-content shutdown thresholds. The MVP does not assess verified standing water, subsurface strength, or task-specific operating limits.

### 4.4 Severe weather hazard — WMO lookup

Input: `weather_code` from Open-Meteo (categorical integer; **do not interpolate** between codes). Each score is an **illustrative modeling assumption**; WMO codes are weather descriptions, not OSHA construction safety thresholds. Do not treat precipitation-related categories as precise rainfall intensity at the jobsite, especially if underlying coding is daily rather than instantaneous.

| WMO code(s) | Interpretation | Severe-weather hazard score |
|---|---|---:|
| 0, 1, 2, 3 | Clear/cloudy | 0 |
| 45, 48 | Fog, depositing rime fog | 20, 35 |
| 51, 53, 55 | Drizzle slight/moderate/dense | 15, 25, 35 |
| 56, 57 | Freezing drizzle light/dense | 50, 60 |
| 61, 63, 65 | Rain slight/moderate/heavy | 20, 35, 55 |
| 66, 67 | Freezing rain light/heavy | 70, 85 |
| 71, 73, 75 | Snowfall slight/moderate/heavy | 30, 55, 80 |
| 77 | Snow grains | 35 |
| 80, 81, 82 | Rain showers slight/moderate/violent | 25, 40, 60 |
| 85, 86 | Snow showers slight/heavy | 55, 75 |
| **95, 96, 97, 99** | **Thunderstorms, with possible hail** | **100** |

**Thunderstorm dominance rule:** If `weather_code` is in `{95, 96, 97, 99}`, set the final `hazard_score` to **100** and include `Thunderstorm` first in `top_factors`, with a reason recommending immediate suspension of exposed work and verification by a human supervisor. Do **not** add a boolean flag. The model is deliberately conservative; a weather code does **not** prove lightning occurred within the last 30 minutes or establish safe resumption. NWS guidance recommends waiting at least 30 minutes after the last thunder before resuming exposed activity; this MVP does not implement reopening logic.

Rain/snow/fog codes are included for **immediate operating conditions** (visibility, exposure, icing); separate `precip_24h_in` and surface soil saturation capture **accumulated ground wetness**. Note possible overlap when interpreting contributions.

Invalid/unknown WMO codes are treated as **unavailable**, not zero hazard; other components can still be scored.

## 5. Aggregation — version 1 weights locked for prototype

Let component scores `s_i` be on 0–100, with `r_i = s_i / 100`.

\[
\boxed{R=100\left(1-\prod_{i\in\{heat,wind,ground,severe\}}(1-r_i)^{w_i}\right)}
\]

| Component | Weight |
|---|---:|
| Heat | 0.30 |
| Wind | 0.30 |
| Ground | 0.20 |
| Severe weather | 0.40 |

These weights are **subjective prototype importance parameters**, not probabilities, regression coefficients, or OSHA-sanctioned risk weights. Their sum (1.20) does *not* need to equal 1. All scores are bounded to 0–100, and raising any one input hazard cannot lower the overall score. A full component score of 100 forces the composite to 100. Thunderstorm codes `{95, 96, 97, 99}` score 100 and thus dominate without a separate boolean flag. Explanations stay in `top_factors[].reason`; no site-specific procedure checks.

**Worked example:** heat 70, wind 60, ground 40, severe weather 0 produces:

\[
R=100\left[1-(0.30)^{0.30}(0.40)^{0.30}(0.60)^{0.20}(1.00)^{0.40}\right]\approx\boxed{52.20}
\]

Not a 52.20% chance of closure. This is a heuristic environmental severity index.

## 6. Top contributing factors — required

Rank **hazard categories** (heat, wind, ground, severe weather), not raw sensor fields. Define the *leave-one-hazard-out* contribution:

\[
\Delta_i=R(s_1,\ldots,s_n)-R(s_1,\ldots,s_i=0,\ldots,s_n)
\]

`contribution` is the reduction in overall index points when that category is removed. Values are nonnegative but are **not additive shares** and generally do not sum to the total. For the worked example:

| Factor | Component score | Leave-one-out impact (index points) |
|---|---:|---:|
| Heat | 70 | 20.79 |
| Wind | 60 | 15.12 |
| Ground | 40 | 5.14 |
| Severe weather | 0 | 0.00 |

Sort descending by `contribution`; return up to 3 with positive component scores (a non-thunderstorm contribution can be zero if another hazard saturates the total score). Each item has `factor`, `risk_score`, `contribution`, `reason`. Explain the relevant measurements (heat index, gust/sustained wind, rainfall and surface wetness, WMO code) in `reason`. When thunderstorm code is present, **always put Thunderstorm first**, even if impact rankings tie at a saturated score. When any component is 100, other components may have zero leave-one-out impact due to score saturation; this does **not** mean those conditions are safe. In particular, use the top three largest *component scores* as a secondary tie-breaker for relevant hazards when an overall score of 100 suppresses their measured contributions. For the normal non-saturated case, hide factors with zero component scores. No separate stop-work flag.

**Example public output for the worked scenario:**

```json
{
  "hazard_score": 52.2,
  "top_factors": [
    {"factor": "heat", "risk_score": 70.0, "contribution": 20.79, "reason": "Elevated heat index raises heat-stress concerns."},
    {"factor": "wind", "risk_score": 60.0, "contribution": 15.12, "reason": "Wind conditions warrant reassessing lifting and elevated work."},
    {"factor": "ground", "risk_score": 40.0, "contribution": 5.14, "reason": "Rain and surface wetness may affect equipment access."}
  ]
}
```

The sample reasons are generic; production reasons should include the original measured values. Do not equate the heuristic index with an observed shutdown probability.

## 7. Python reference implementation (defined components)

```python
from bisect import bisect_right

HEAT_POINTS = [(70, 0), (80, 10), (90, 25), (103, 65), (115, 85), (125, 100)]
WIND_POINTS = [(0, 0), (13, 5), (19, 15), (25, 30), (32, 55), (39, 75), (47, 90), (55, 100)]


def interpolate_clamped(value: float, points: list[tuple[float, float]]) -> float:
    if value <= points[0][0]:
        return float(points[0][1])
    if value >= points[-1][0]:
        return float(points[-1][1])
    xs = [x for x, _ in points]
    right = bisect_right(xs, value)
    xa, ya = points[right - 1]
    xb, yb = points[right]
    return float(ya + (value - xa) / (xb - xa) * (yb - ya))


def heat_risk(heat_index_f: float) -> float:
    return interpolate_clamped(heat_index_f, HEAT_POINTS)


def wind_risk_details(wind_mph: float | None, wind_gust_mph: float | None) -> dict:
    sustained = interpolate_clamped(wind_mph, WIND_POINTS) if wind_mph is not None else None
    gust = interpolate_clamped(wind_gust_mph, WIND_POINTS) if wind_gust_mph is not None else None
    if sustained is None and gust is None:
        return {"score": None, "sustained_score": None, "gust_score": None, "dominant_input": None}
    choices = {name: score for name, score in (("wind_mph", sustained), ("wind_gust_mph", gust)) if score is not None}
    dominant = max(choices, key=choices.get)
    return {
        "score": choices[dominant],
        "sustained_score": sustained,
        "gust_score": gust,
        "dominant_input": dominant,
    }


def wind_risk(wind_mph: float | None, wind_gust_mph: float | None) -> float | None:
    return wind_risk_details(wind_mph, wind_gust_mph)["score"]


RAIN_POINTS = [(0, 0), (0.1, 10), (0.5, 35), (1.0, 65), (2.0, 90), (3.0, 100)]
POROSITY = 0.45  # assumed mineral-soil porosity; configurable
SATURATION_POINTS = [(0.0, 0), (0.25, 10), (0.50, 30), (0.75, 60), (0.90, 80), (1.0, 100)]


def estimate_saturation(soil_moisture: float) -> float:
    if not 0 <= soil_moisture <= 1:
        raise ValueError("Expected volumetric soil water content between 0 and 1")
    return min(1.0, max(0.0, soil_moisture / POROSITY))


def ground_risk_details(precip_24h_in: float | None, soil_moisture: float | None) -> dict:
    if precip_24h_in is None and soil_moisture is None:
        return {"score": None}
    rain_score = interpolate_clamped(precip_24h_in, RAIN_POINTS) if precip_24h_in is not None else None
    saturation = estimate_saturation(soil_moisture) if soil_moisture is not None else None
    moisture_score = interpolate_clamped(saturation, SATURATION_POINTS) if saturation is not None else None
    p = rain_score / 100 if rain_score is not None else None
    m = moisture_score / 100 if moisture_score is not None else None
    rain_points = 30*p if p is not None else 0
    moisture_points = 50*m if m is not None else 0
    interaction_points = 20*p*m if p is not None and m is not None else 0
    return {
        "score": rain_points + moisture_points + interaction_points,
        "rain_score": rain_score,
        "moisture_score": moisture_score,
        "interaction_points": interaction_points,
        "soil_moisture_depth": "0-1cm",
        "assumed_porosity": POROSITY,
        "estimated_surface_saturation": saturation,
        "data_quality_flags": ["moisture_above_assumed_porosity"] if soil_moisture is not None and soil_moisture > POROSITY else [],
    }


def ground_risk(precip_24h_in: float | None, soil_moisture: float | None) -> float | None:
    return ground_risk_details(precip_24h_in, soil_moisture)["score"]

SEVERE_WEATHER_SCORES = {
    0: 0, 1: 0, 2: 0, 3: 0,
    45: 20, 48: 35,
    51: 15, 53: 25, 55: 35, 56: 50, 57: 60,
    61: 20, 63: 35, 65: 55, 66: 70, 67: 85,
    71: 30, 73: 55, 75: 80, 77: 35,
    80: 25, 81: 40, 82: 60, 85: 55, 86: 75,
    95: 100, 96: 100, 97: 100, 99: 100,
}
THUNDERSTORM_CODES = {95, 96, 97, 99}


def severe_weather_risk(weather_code: int | None) -> float | None:
    score = SEVERE_WEATHER_SCORES.get(weather_code)
    return float(score) if score is not None else None
```

### 7.1 Overall score and attribution

```python
from math import prod

WEIGHTS = {"heat": 0.30, "wind": 0.30, "ground": 0.20, "severe_weather": 0.40}


def aggregate_risk(scores: dict[str, float | None]) -> float | None:
    available = {k: v for k, v in scores.items() if k in WEIGHTS and v is not None}
    if not available:
        return None
    return 100 * (1 - prod(
        (1 - score / 100) ** WEIGHTS[name]
        for name, score in available.items()
    ))


def rank_top_factors(scores: dict[str, float], weather_code: int,
                     reasons: dict[str, str], limit: int = 3) -> list[dict]:
    total = aggregate_risk(scores)
    if total is None:
        return []
    candidates = []
    for name, score in scores.items():
        if score is None or score <= 0:
            continue
        without = {**scores, name: 0}
        impact = max(0.0, total - aggregate_risk(without))
        candidates.append({
            "factor": "Thunderstorm" if name == "severe_weather" and weather_code in THUNDERSTORM_CODES else name,
            "risk_score": round(score, 2),
            "contribution": round(impact, 2),
            "reason": reasons[name],
            "_raw_impact": impact,
            "_priority": name == "severe_weather" and weather_code in THUNDERSTORM_CODES,
        })
    # Thunderstorms first; score breaks ties when saturated score makes impacts zero.
    candidates.sort(key=lambda x: (x["_priority"], x["_raw_impact"], x["risk_score"]), reverse=True)
    return [{k: v for k, v in item.items() if not k.startswith("_")}
            for item in candidates[:limit]]


def current_hazard_score(reading: dict) -> dict:
    from math import isfinite

    def valid_number(key: str, *, low=None, high=None):
        raw = reading.get(key)
        if raw is None or isinstance(raw, bool):
            return None
        try:
            value = float(raw)
        except (ValueError, TypeError):
            return None
        if not isfinite(value) or (low is not None and value < low) or (high is not None and value > high):
            return None
        return value

    heat_index = valid_number("heat_index_f")
    sustained = valid_number("wind_mph", low=0)
    gust = valid_number("wind_gust_mph", low=0)
    rain = valid_number("precip_24h_in", low=0)
    moisture = valid_number("soil_moisture", low=0, high=1)
    raw_code = valid_number("weather_code")
    code = int(raw_code) if raw_code is not None and raw_code.is_integer() else None
    heat = heat_risk(heat_index) if heat_index is not None else None
    wind = wind_risk(sustained, gust)
    ground = ground_risk(rain, moisture)
    severe = severe_weather_risk(code)
    scores = {"heat": heat, "wind": wind, "ground": ground, "severe_weather": severe}
    reasons = {
        "heat": f"Heat index {heat_index}°F; elevated heat can increase heat stress.",
        "wind": f"Wind {sustained if sustained is not None else 'unavailable'} mph; gusts {gust if gust is not None else 'unavailable'} mph; high winds affect elevated work and lifting.",
        "ground": f"Rain {rain if rain is not None else 'unavailable'} in/24h; surface soil moisture {moisture if moisture is not None else 'unavailable'} m³/m³; ground score is a wetness proxy.",
        "severe_weather": (
            f"Thunderstorm indicated by WMO code {code}; suspend exposed outdoor work pending human verification."
            if code in THUNDERSTORM_CODES
            else f"WMO code {code} indicates potentially hazardous weather."
        ),
    }
    total = aggregate_risk(scores)
    return {
        "hazard_score": round(total, 2) if total is not None else None,
        "top_factors": rank_top_factors(scores, code, reasons),
    }

```

Reference tests to implement next:

```python
assert heat_risk(95) == 25 + (5 / 13) * 40
assert heat_risk(65) == 0
assert heat_risk(140) == 100
assert wind_risk(18, 32) == 55
assert wind_risk(0, 0) == 0
assert wind_risk(60, 65) == 100
assert ground_risk(0, 0) == 0
assert ground_risk(3, 0.45) == 100
assert abs(estimate_saturation(0.30) - 2/3) < 1e-8
assert abs(ground_risk(0.5, 0.405) - (0.3*35 + 0.5*80 + 0.2*35*80/100)) < 1e-8
assert severe_weather_risk(95) == 100
assert severe_weather_risk(0) == 0
```

### 7.2 Missing-data examples

```python
assert current_hazard_score({}) == {"hazard_score": None, "top_factors": []}
assert current_hazard_score({"heat_index_f": 95})["hazard_score"] is not None
assert current_hazard_score({"wind_mph": None, "wind_gust_mph": 32})["hazard_score"] is not None
assert current_hazard_score({"precip_24h_in": 0.5, "soil_moisture": None})["hazard_score"] is not None
assert current_hazard_score({"weather_code": 95})["hazard_score"] == 100
assert current_hazard_score({"weather_code": 999})["hazard_score"] is None
```

## 8. Open decisions

1. Validate assumed porosity `0.45` and provisional estimated-saturation breakpoints against local soil texture and site observations. Surface saturation is **estimated**, not directly measured, and cannot establish subgrade strength. Sensitivity-test assumptions.
2. Sensitivity-test selected aggregation weights (0.30/0.30/0.20/0.40) with representative site scenarios; recalibrate as evidence emerges.
3. Keep descriptive explanations and general safety implications in `top_factors[].reason` (no flags); do not implement site-specific procedure checks in this MVP.
4. Missing/null/invalid-data policy is implemented; freshness checks are intentionally out of scope for the MVP.
5. Validate risk curves against any available site closure observations; do not claim measured closure probability without labels and calibration.

## 9. Sources and interpretation notes

- [OSHA — Heat](https://www.osha.gov/heat-exposure): heat prevention, heat index limitations, WBGT context.
- [NWS — Beaufort Wind Scale](https://www.weather.gov/mfl/beaufort): weather-based wind classifications; *sustained* wind, not gust operating limits.
- [OSHA — Cranes and Derricks in Construction](https://www.osha.gov/laws-regs/regulations/standardnumber/1926/subpartCC): equipment- and operation-specific duties.
- [OSHA — Electric Power Transmission and Distribution](https://www.osha.gov/laws-regs/regulations/standardnumber/1926/1926.964): activity-specific adverse-weather precautions.
- [OSHA — Excavations](https://www.osha.gov/laws-regs/regulations/standardnumber/1926/1926.651): inspections after rainstorms, accumulating water.
- [Open-Meteo — Weather Forecast API documentation](https://open-meteo.com/en/docs): soil_moisture_0_to_1cm is volumetric water content in m³/m³, 0–1 cm depth; WMO weather codes.

All numeric hazard scores and future aggregation weights must be described as **prototype assumptions** rather than OSHA-mandated risk metrics.
