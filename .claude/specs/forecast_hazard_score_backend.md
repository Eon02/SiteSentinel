# Tomorrow's Hazard Score — Backend and Site Card Integration Specification

**Status:** Implemented. Written from the implementation plan; where the code differs, the code wins.

## Context

The live hazard score now lands in `hazard_scores` (today's row, `is_forecast=false`) on every conditions run, and the frontend shows it. The frontend already reads a forecast too: `lib/data.ts` `selectSites()` joins `hazard_scores` for `score_date = current_date + 1` and shows it as "Tmrw 4.2 ↑" (`Forecast` in `components/SiteList.tsx`) and as a "Tomorrow" badge on the site page. Nothing writes that row yet, and the forecast row's `top_factors` is never selected.

**Goal / exit criteria:** tomorrow's score **and its top reasons** appear in every site card, less prominently than today's score.

**Decisions:**
- The forecast uses the **v2 aggregation** from `.claude/specs/forecast_hazard_score.md` §5. The live model stays on noisy-OR.
- The forecast is computed in the **conditions worker** (every 15 min), reusing the Open-Meteo daily forecast it already fetches. No new API calls.
- UI: keep the header "Tmrw 4.6 ↑" **and** add a small grey "Tomorrow: Ground, High wind" line on the collapsed card. The expanded content is unchanged.

Model math is defined in `.claude/specs/forecast_hazard_score.md` (v0.3). Work happens on `avillate/new-model`.

## Step 0: Spec housekeeping (done)
- This file lives alongside `current_hazard_score_backend.md`.
- `forecast_hazard_score.md` §5 now says v2 is forecast-only and the current model uses the weighted noisy-OR.

## Step 1: Model, in `backend/app/services/hazard_model.py` (extend, don't duplicate)

Add `tomorrow_hazard_score(forecast: Mapping[str, Any]) -> dict` (pure, never raises), reusing what's already there:
- `_number` (validation; probability `low=0, high=100`), `_show`, `interpolate_clamped`
- `HEAT_POINTS` fed `feels_like_max_f`; `WIND_POINTS` fed `wind_gust_max_mph`
- `RAIN_POINTS`, where **ground = the full rain subscore** (no soil moisture; 3+ in → 100)
- `severe_weather_risk`, `SEVERE_WEATHER_SCORES`, `THUNDERSTORM_CODES`, `WEIGHTS`

New pieces:
- `aggregate_forecast_risk(scores)`, the v2 formula: dominant `m` (tie order heat, wind, ground, severe_weather, i.e. `WEIGHTS` order), `C = 1 − Π_{i≠k}(1−r_i)^{w_i}`, `R = 100[m + 0.5(1−m)C]`. Clamp each `r` to [0, 1]. `None` if nothing is available.
- **Generalize `rank_top_factors`** with an `aggregate=aggregate_risk` parameter, so the forecast reuses the same leave-one-out ranking with thunderstorm first. The live behavior is unchanged.
- Forecast reasons per spec §6:
  - Every reason is worded as a forecast.
  - The precipitation reason adds the max probability as context only, says "precipitation" rather than "rain", and notes that soil moisture is unavailable.
  - The thunderstorm reason says to "plan to suspend exposed work if storms develop".
  - No reason ever contains "None".
- Probability alone does not produce a score.

## Step 2: Integration, in `backend/app/services/conditions.py`

Same shape as the live score (`hazard_input` / `hazard_row` / `store_hazard_scores`):
- `forecast_input(report)`: the `report.daily` entry whose `date == report.current.observed_at.date() + 1`, which is the site-local tomorrow. Map its `DailyForecast` fields directly; the names already match the spec's input contract. Return `None` if that day is absent.
- Factor the 0–10 conversion out of `hazard_row` into a shared helper (`score / 10`, rounded to 2 decimals; `weight = risk_score / 10`, rounded to 3 decimals). Then add `forecast_row(site_id, report)` using `tomorrow_hazard_score`.
- New `UPSERT_FORECAST_SCORE`, which never overwrites an observed row:
  ```sql
  INSERT INTO hazard_scores (site_id, score_date, score, is_forecast, top_factors)
  VALUES (:site_id, CURRENT_DATE + 1, :score, true, CAST(:top_factors AS jsonb))
  ON CONFLICT (site_id, score_date) DO UPDATE SET
    score = EXCLUDED.score, top_factors = EXCLUDED.top_factors
  WHERE hazard_scores.is_forecast
  ```
  `CURRENT_DATE + 1` is the date the frontend asks for. When the date rolls over, the live upsert overwrites this row with `is_forecast=false`, as designed.
- New `store_forecast_scores(rows)`, written in its own transaction after the conditions and live-score writes, with the same isolation rules:
  - A model exception per site goes into `forecast_failed`.
  - A write failure marks all of those sites as failed and doesn't raise.
  - No usable input, or no tomorrow entry, goes into `forecast_skipped` and leaves the existing row alone.
- `RecordResult` gains `forecast_scored`, `forecast_skipped` and `forecast_failed`. `conditions_loop()` logs them.
- `backend/scripts/record_conditions.py` prints them and adds `forecast_failed` to the exit-code count.

**Date note (documented, not "fixed"):** between about 8 PM local time and local midnight, `CURRENT_DATE + 1` in UTC is two local days out. For those hours, the "Tmrw" row holds the site-local next day's forecast. That's what a user expects to see in the evening.

## Step 3: Frontend (read `sitesentinel/AGENTS.md`: check `node_modules/next/dist/docs/` before writing code)

- `sitesentinel/lib/types.ts`: add `forecastTopFactors: HazardFactor[]` to `Site`.
- `sitesentinel/lib/data.ts`:
  - `selectSites()` also selects `f.top_factors AS forecast_top_factors`.
  - `rowToSite` maps it, using `[]` when it's null.
  - `mockToSite` defaults it to `[]`.
- `sitesentinel/data/mock/sites.json`: add `forecastTopFactors` to a few sites so mock mode shows the new line.
- `sitesentinel/components/SiteList.tsx`, in `SiteRow` under the existing state / `RiskBadge` / `Forecast` row:
  - Add a small muted line: `Tomorrow: {top 3 forecast factors by weight, via factorLabel}`.
  - Style: `text-sm text-slate-600`, a normal-weight sibling span inside the same button, so it doesn't affect the click or expand behavior.
  - Render it only when `forecastTopFactors.length > 0`.
  - Keep `Forecast` ("Tmrw 4.6 ↑") as is.
  - Leave `SiteSummary` (expanded) and `SiteDetail.tsx` untouched.
- Reuse `factorLabel` (`lib/risk.ts`), the same sort-by-`weight` pattern as `SiteSummary`, and `formatScore`.

## Step 4: Tests

`backend/tests/test_hazard_model.py` (add):
- Spec §6.1 reference assertions.
- v2 math. For example, heat 70 and wind 60 give `100(0.7 + 0.5·0.3·(1−0.4^0.3)) ≈ 73.6`, and a single component equals its own score.
- 1 in of precipitation gives ground 65. 3 in gives 100 with ground first.
- A thunderstorm gives 100 with Thunderstorm first.
- Probability alone gives `None`.
- Invalid inputs (NaN, bool, negative gust, probability 150, code 95.5 or 999) are unavailable.
- No reason contains "None"; the precipitation reason includes the probability only when it's valid.
- The existing live-model tests stay green, which guards the `rank_top_factors` refactor.

`backend/tests/test_conditions.py` (extend the `run_record` helper to stub `store_forecast_scores`):
- `forecast_input` picks SAMPLE's tomorrow (feels-like 113, precip 0.4, probability 60, gust 20, code 80).
- A missing tomorrow entry goes into `forecast_skipped`.
- The forecast row is on the 0–10 scale with positive weights.
- The upsert SQL text contains `CURRENT_DATE + 1`, `true` and `WHERE hazard_scores.is_forecast`.
- `record_conditions` writes forecast rows.
- A forecast model error or forecast write failure leaves conditions and live scores intact.

## Verification

1. Backend, from `backend/`: `uv run pytest -q`, `uv run ruff check .`, and `uv run ruff format --check`.
2. Frontend, from `sitesentinel/`: `npm run lint` and `npx tsc --noEmit`.
3. With your OK, since it writes to the DB in `DATABASE_URL`: `uv run python -m scripts.record_conditions --site <id>` should print "Forecast scores: 1 saved". A read-only query should then confirm a `CURRENT_DATE + 1` row with `is_forecast = true`.
4. In the UI (`USE_MOCK=false`), each card shows "Tmrw X.X ↑/↓" plus the grey "Tomorrow: …" line, with today's badge and expanded content unchanged. With `USE_MOCK=true`, the mock sites show the line too.
5. Commit in two steps, as last time: the model and its tests, then the integration, frontend and spec.
