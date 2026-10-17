# Proposals for the backend and frontend owners

Nothing here is implemented. The model code takes plain objects with the names below.

## 1. Persist the missing weather fields (migration 0007)
`conditions` lacks columns the estimate reads. Add nullable columns: `humidity_pct`,
`wind_gust_mph`, `weather_code` (smallint, WMO). `precip_24h_in` and `wind_mph` already exist.
`CurrentConditions` already returns `humidity_pct`, `heat_index_f` and `weather_code`. A heat
index is already stored as `heat_index_f`, so the WBGT route is optional.

## 2. Crew and labor inputs on `sites` (or a `site_crews` table)
Add `num_crews` (int), `crew_size` (int), `hours_worked` (numeric, hours per day),
`work_start_hour` (smallint 0-23, site-local; null means 7), `labor_rate` (numeric, USD per
hour), and `labor_rate_basis` ("loaded" or "base"). Expose them in the sites API and the site form.
Open question for the product owner: is the entered rate the base wage or the fully loaded cost?
If `work_start_hour` should allow half hours, make it a time of day instead.

## 3. `cost_estimates` table
`site_id, date, is_forecast, factor ("heat" | "cold" | "ice"),
lost_worker_hours_{low,central,high}, labor_cost_usd_{low,central,high}, assumptions jsonb`.
`assumptions` is filled from `LossEvent.assumptions` (workers, hours_worked, work_start_hour,
labor_rate, labor_rate_basis, defaulted, confidence, metric, status) and the curve `source`. The UI
should show these beside every estimate, plus the label "labor-cost equivalent, not lost profit".
Compute with `productivity.summarize(events, crew)`.

## 4. Next-day briefing
Attach the tomorrow estimate to the payload from `build_briefing()` in
`backend/app/services/briefings.py`: central value with the low-high range, the confidence
("low": apparent temperature used as a heat index, worst hour of the day) and the assumptions.

## 5. Hazard factors
`productivity.hazard_factors(events, crew, period="today")` returns `top_factors` entries
`{"factor": "heat_productivity" | "cold_productivity" | "ice_productivity", "weight": 0-1}` where
weight is the crew-hours-weighted central fraction lost. The hazard-score owner decides how to fold
it into the 0-10 score (for example as a capped additive term).

## 6. Frontend labels
`factorLabel()` in `sitesentinel/lib/risk.ts` has no entries for these factors. Add:
`heat_productivity` -> "Heat slowdown", `cold_productivity` -> "Cold slowdown",
`ice_productivity` -> "Ice and snow slowdown".

## 7. Dependency
The package needs `pyyaml` in `model/pyproject.toml`.
