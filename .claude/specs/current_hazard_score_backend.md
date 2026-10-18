# Current Hazard Score — Backend Integration Specification

**Status:** Draft v0.2. Decisions locked: 0–10 score, keep the old row on a null score, module named `hazard_model.py`. Implemented.
**Depends on:** [`current_hazard_score.md`](current_hazard_score.md), the model spec (v0.7). That spec is the source of truth for the math. This one covers only where the model lives, how it's called, and what gets written.

## 1. Goal

Each time the conditions worker records current weather for a site, also compute that site's **current hazard score**. Upsert it into `hazard_scores` as today's non-forecast row. The frontend already reads today's `hazard_scores` row for each site (`lib/data.ts`), so the live score and factors appear without a new endpoint or communication channel.

## 2. Scope

**In scope**
- New module `backend/app/services/hazard_model.py` implementing the model from `current_hazard_score.md` §4–§7.
- Calling it from `record_conditions()` in `backend/app/services/conditions.py`, as a side effect after conditions are stored.
- Upserting the result into `hazard_scores`.
- Tests.

**Out of scope**
- Tomorrow's (forecast) score.
- Schema migrations. Use the table as it is; see §6.
- Frontend changes.
- Changing what the `conditions` table stores.

## 3. New module: `backend/app/services/hazard_model.py`

Import as `from app.services.hazard_model import current_hazard_score`.

Implement §7 and §7.1 of the model spec faithfully, cleaned up to match the backend style (module docstring, type hints, constants at the top):

- Constants: `HEAT_POINTS`, `WIND_POINTS`, `RAIN_POINTS`, `SATURATION_POINTS`, `POROSITY = 0.45`, `SEVERE_WEATHER_SCORES`, `THUNDERSTORM_CODES`, `WEIGHTS`.
- Pure functions: `interpolate_clamped`, `heat_risk`, `wind_risk_details` / `wind_risk`, `estimate_saturation`, `ground_risk_details` / `ground_risk`, `severe_weather_risk`, `aggregate_risk`, `rank_top_factors`.
- Public entry point:

```python
def current_hazard_score(reading: Mapping[str, Any]) -> dict[str, Any]:
    """{"hazard_score": float | None, "top_factors": list[dict]}. Never raises on bad input."""
```

Requirements:
1. **Pure and synchronous.** No I/O, DB, logging or settings access, so it's trivially unit-testable.
2. **Input keys** (all optional): `heat_index_f`, `wind_mph`, `wind_gust_mph`, `precip_24h_in`, `soil_moisture`, `weather_code`. `site_id`, `observed_at`, `temp_f` and `humidity_pct` may be present and are ignored for scoring, per the model spec §2.
3. **Validation follows model spec §2 and the `valid_number` helper in §7.1.** `None`, bools, non-numeric strings, NaN/±inf, negative rain or wind, `soil_moisture` outside [0, 1], non-integer or unknown WMO codes all count as *unavailable*. Missing components are left out of aggregation and weights are **not** renormalized.
4. **No usable input** returns `{"hazard_score": None, "top_factors": []}`.
5. **Output shape is exactly** `hazard_score` (0–100, rounded to 2 decimals) and `top_factors`: at most 3 items, each `{factor, risk_score, contribution, reason}`. Thunderstorm comes first when the code is in `{95, 96, 97, 99}`.
6. **Hardening beyond the reference code.**
   - `reason` strings must not render `None` (for example, "Heat index None°F"). Use "unavailable" as the reference code does for wind and ground.
   - `rank_top_factors` must tolerate `None` scores and a `None` weather code.
   - `estimate_saturation` may still raise for out-of-range values internally, but `current_hazard_score` must validate first so it never does.

## 4. Integration in `conditions.py`

### 4.1 Build the model input from the `WeatherReport`, not the `conditions` row

There are two deliberate differences between what's stored and what the model needs:

| Field | `conditions` row | Model expects | Use |
|---|---|---|---|
| `soil_moisture` | 0–1 saturation, already converted (`volumetric / 0.5`, `SATURATED_VWC`) | **raw volumetric m³/m³**, which the model converts with porosity 0.45 | `report.current.soil_moisture` (raw) |
| `weather_code` | not stored | WMO code | `report.current.weather_code` |

Passing the stored `soil_moisture` would double-convert it and inflate the ground score. Add a helper next to `condition_row`:

```python
def hazard_input(report: WeatherReport) -> dict[str, Any]:
    current = report.current
    return {
        "heat_index_f": current.heat_index_f,
        "wind_mph": current.wind_mph,
        "wind_gust_mph": current.wind_gust_mph,
        "precip_24h_in": current.precip_24h_in,
        "soil_moisture": current.soil_moisture,  # raw volumetric, NOT soil_saturation()
        "weather_code": current.weather_code,
    }
```

### 4.2 Flow inside `record_conditions()`

1. Fetch the weather per site, unchanged.
2. For each successful fetch, keep both `condition_row(site.id, report)` and the hazard result from `current_hazard_score(hazard_input(report))`. Wrap the call in `try/except Exception` as a safety net despite §3.1. On failure, record `score_failed[site.id]` and keep going.
3. Store the conditions rows with `store_rows(rows)`, unchanged and first. **Scoring must never stop conditions from being stored.**
4. Upsert hazard rows in a **separate transaction** after step 3, using a new `store_hazard_scores(rows)`. If that write fails, log the error and put every affected site in `score_failed`. Don't re-raise, because the conditions write already succeeded.
5. Skip sites whose `hazard_score` is `None` (the column is `NOT NULL`). Count them in `score_skipped` and leave any existing row for today untouched, so the last good score stays on screen. Don't write a fake zero and don't delete the row.

### 4.3 `RecordResult` additions

```python
@dataclass
class RecordResult:
    stored: int = 0
    failed: dict[str, str] = field(default_factory=dict)        # site id -> fetch error
    scored: int = 0
    score_skipped: list[str] = field(default_factory=list)      # no usable model input
    score_failed: dict[str, str] = field(default_factory=dict)  # site id -> scoring/write error
```

`conditions_loop()` logs `scored`, plus a warning for each `score_failed` entry. `scripts/record_conditions.py` should print the new fields if it already prints the result.

## 5. Database write

`hazard_scores` already exists (`0002_create_core_tables.sql`). Primary key is `(site_id, score_date)` and every column is `NOT NULL`.

```sql
INSERT INTO hazard_scores (site_id, score_date, score, is_forecast, top_factors)
VALUES (:site_id, CURRENT_DATE, :score, false, CAST(:top_factors AS jsonb))
ON CONFLICT (site_id, score_date) DO UPDATE SET
  score = EXCLUDED.score,
  is_forecast = false,
  top_factors = EXCLUDED.top_factors
```

- **`score_date = CURRENT_DATE` (database date).** This matches the frontend query exactly (`score_date = current_date` in `lib/data.ts`). Each 15-minute run overwrites today's row, so the row always holds the latest score. See §8 for timezones.
- **`is_forecast = false`.** If a future forecast job wrote today's row the day before, this run replaces it with the observed score. That's the intended behavior.
- **`top_factors`** is serialized with `json.dumps`, as `briefings._json` does. Use `[]` when the model returns no factors.

## 6. Mapping model output to the table (what fits and what's dropped)

| Model output | Column | Rule |
|---|---|---|
| `hazard_score` (0–100) | `score` (`real`, **0–10**) | Store `round(hazard_score / 10, 2)`. |
| `top_factors[]` | `top_factors` (`jsonb`) | Store each item as `{factor, weight, risk_score, contribution, reason}`, in the model's order. `weight = round(risk_score / 10, 3)` (0–10; three decimals so a small but positive `risk_score` never rounds to a weight of 0). The other keys ride along in the JSONB and the frontend ignores them for now. |
| `factor` values | — | Stored as returned: `heat`, `wind`, `ground`, `severe_weather`, `Thunderstorm`. |
| Internal diagnostics (wind `dominant_input`, ground `data_quality_flags`, saturation, and so on) | — | Not returned by `current_hazard_score`, not stored. |

Nothing in the model output needs a new column.

### 6.1 Frontend contract (verified against `sitesentinel/`; no frontend changes needed)

| Frontend code | What it does with the row | What this spec guarantees |
|---|---|---|
| `lib/data.ts` `selectSites()` | `COALESCE(t.score, 0)` from `hazard_scores` where `score_date = current_date`; `top_factors` cast to `HazardFactor[]` | Row keyed on `CURRENT_DATE`; `top_factors` is always a JSON array (possibly `[]`) |
| `lib/types.ts` | `score: number // 0-10`, `HazardFactor = { factor: string; weight: number }` | `score` in [0, 10]; each factor has a string `factor` and a numeric `weight` |
| `lib/risk.ts` `riskLevel()` | Low < 3 ≤ Moderate < 6 ≤ High < 8 ≤ Extreme | 0–10 scale. A thunderstorm (model 100) gives 10.0, "Extreme". The worked example (74.71, model v0.8) gives 7.47, "High". |
| `lib/risk.ts` `formatScore()` | Rounds to one decimal | Two-decimal storage is fine |
| `lib/risk.ts` `factorLabel()` | Known keys get nice labels, others are auto-capitalized | `heat` → "Heat", `wind` → "High wind", `ground` → "Ground", `severe_weather` → "Severe weather", `Thunderstorm` → "Thunderstorm" |
| `components/FactorBars.tsx` ("Why this score") | Sorts by `weight` desc; label shows `weight / Σweight` as %, bar width is `weight / max`; `key={f.factor}`; empty list shows "No hazard factors reported today." | `weight` > 0 for every stored factor (the model only lists positive component scores); `factor` is unique per row |
| `components/SiteList.tsx`, `SiteMap.tsx` | Top 3 by `weight`; map popup shows `topFactors[0]` | A thunderstorm has `risk_score` 100, so it has the highest weight and stays first. Ties sort stably, so the model's order holds. |

**Why `weight` comes from `risk_score` and not `contribution`:** under saturation, the leave-one-out `contribution` drops to 0 for every factor except the one that saturates the score (model spec §6). For example, a thunderstorm plus high heat would show Heat as 0% in "Why this score", which reads as safe. The model spec explicitly forbids that. `risk_score` is always positive for listed factors and still orders them sensibly. `contribution` stays in the JSONB for later use.

**Known limitation, not addressed here:** a site with no row for today (never scored, or every run so far had no usable input) shows as `0.0 Low` because of `COALESCE(t.score, 0)`. Fixing that is a frontend change.

## 7. Tests

**`backend/tests/test_hazard_model.py`** (new, pure unit tests, no DB):
- Every reference assertion in model spec §7 ("Reference tests") and §7.2 ("Missing-data examples").
- The worked example (heat 70, wind 60, ground 40, severe 0) gives `aggregate_risk ≈ 74.71` with leave-one-out contributions ≈ 12.77, 3.26, 1.11 (model v0.8; v0.7 gave 52.20).
- Thunderstorm (code 95) plus other hazards: score 100, Thunderstorm first.
- Invalid inputs are treated as unavailable without raising: `"abc"`, `float("nan")`, `float("inf")`, `True`, `-1` wind, `1.5` soil moisture, `95.5` and `999` weather codes.
- No reason string contains `"None"`.

**`backend/tests/test_conditions.py`** (extend, following the existing mocking style):
- `hazard_input()` passes **raw** volumetric soil moisture and `weather_code`. Assert it differs from `condition_row()["soil_moisture"]`.
- `record_conditions()` writes one `hazard_scores` row per scored site with `is_forecast = false`, `score` in [0, 10] (equal to `hazard_score / 10`), and `top_factors` items whose `weight == risk_score / 10` and is > 0.
- Thunderstorm input stores `score == 10.0` with `Thunderstorm` as the highest-weight factor.
- A site with no usable input is skipped (`score_skipped`) and an existing row for today is left unchanged.
- If the model raises or the hazard write fails, conditions are still stored and the error lands in `score_failed`.
- The hazard write is an upsert of today's non-forecast row (asserted on the SQL text, since the unit tests run without a database): a second run on the same day updates the row instead of inserting a duplicate.

## 8. Decisions and notes

**Decided**
- Score is stored on 0–10 (`hazard_score / 10`), matching the frontend (§6.1).
- A `None` score keeps the existing row; nothing is written or deleted.
- The module is `app/services/hazard_model.py`.

**Notes**
- **Which "today".** `CURRENT_DATE` is the database's date (UTC on Supabase), which matches the frontend query. For US sites after about 7–8 PM local time, the row lands on the next UTC date. That's harmless for a live score, because the frontend asks for the same date. Moving to site-local dates would need a frontend change too.
- **Frontend visibility.** The frontend uses mock data unless `USE_MOCK=false`, so it must point at Postgres to show these scores.
