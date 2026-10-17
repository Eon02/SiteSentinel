# Plan: site-closure thresholds (wind, rain/flood, hail, lightning, icing)

## Context

The injury analysis (`model/modelreadme.md`) found **no detectable injury link** for wind,
hail, lightning or thunderstorm wind. So we don't model injury risk for these. Instead we copy
the real-world rules contractors and regulators use to stop work, and turn them into alerts.
**These thresholds rest on regulations and standards, not on our data.** The plan and the code
say so.

**Assumptions for this branch (decided):**
- Sites are EIA-860M power plants **under construction**, and the work we care about is
  **outdoor construction work** (OSHA 1926).
- **`stop_work` means the whole site.** Rules that only restrict one kind of equipment (cranes,
  aerial lifts, scaffolds) produce a site-level **`warning`** naming the restricted work, e.g.
  "Suspend crane and aerial-lift work: gusts 32 mph exceed the 28 mph limit". This fits the
  existing `alerts` schema, which has no activity column.
- **`wind_gust_mph` is a separate input** from `wind_mph` (already returned by the Open-Meteo
  service, not yet stored in `conditions`).
- Lost profit from closing a site is out of scope here; it goes in a later, separate cost
  model. This branch only reports how many hours the site is stopped.

## Inputs (names from the shared table below)

- Site: `id`, `plant_type`, `timezone`, `near_river`
- Now: `wind_mph`, `wind_gust_mph`, `precip_24h_in`, `soil_moisture`, `temp_f`, `weather_code`,
  `cape_j_kg`, lightning (shape not defined yet)
- Tomorrow (daily only): `wind_gust_max_mph`, `precip_in`, `precip_probability_pct`,
  `weather_code`
- NWS active warnings: `ActiveAlert.event` from `fetch_active_alerts`
- Crew and labor: only `hours_worked` (hours per day) and `work_start_hour` (default 7) are needed here, to clip stoppage hours to the workday
  schedule; the rest pass through to the cost model.
- Output goes to `alerts`: `type`, `severity` (`warning` | `stop_work`), `message`,
  `started_at`, `last_strike_at`, `cleared_at`. The frontend's `AlertType`
  (`sitesentinel/lib/types.ts`) is `lightning | flood | heat | wildfire | storm`, so wind, hail
  and icing alerts map to `storm` unless a new type is added.

## Shared contract (identical in both branch plans)

- **Output record `LossEvent`**, emitted by each branch and consumed by the later cost model:
  `{site_id, start, end, activity, kind: "slowdown"|"stoppage", fraction_lost (0–1; 1.0 for
  stoppage), scenario: "low"|"central"|"high", factor (e.g. "heat", "wind"),
  rule_or_curve_id, source}`.
  Use `activity = "site"` for whole-site events. Outdoor construction is the default activity.
- Workers on site = `num_crews` × `crew_size` (`num_crews` is the number of crews; `crew_size`
  is the size of each crew). Lost worker-hours = workers × hours lost × fraction_lost.
  `hours_worked` is hours **per day**, and `work_start_hour` is the hour the workday starts
  (site-local, 0–23; **an input, defaulting to 7 when null**). The workday window is
  `work_start_hour` for `hours_worked` hours. Only weather inside that window counts, and hours
  lost per day never exceed `hours_worked`. Dollars = lost worker-hours × `labor_rate`.
  **This is a labor-cost equivalent, not lost profit.** Profit (work made up later, overtime,
  schedule delay, equipment and overhead) belongs to the later cost model.
- **Field names come from the backend**, not from `model/datamodel.md`. Checked against `main`
  at 160f74c (`backend/database/migrations/`, `backend/app/models.py`,
  `backend/app/services/open_meteo.py` and `nws.py`). Where the two differ, the backend wins:

  | Concept | Name to use | Where it exists today |
  |---|---|---|
  | Site | `sites.id`, `plant_type` (free text, e.g. "Solar", "Natural Gas"), `timezone`, `near_river`, `geom` | DB table `sites` |
  | Temperature, heat index | `temp_f`, `heat_index_f` | DB `conditions` and the Open-Meteo service. The service computes `heat_index_f` from temperature and humidity (NWS formula) |
  | Humidity | **`humidity_pct`** (earlier docs said `humidity`) | service only (`CurrentConditions`); **not a `conditions` column** |
  | Wind | `wind_mph` (10 m, sustained), **`wind_gust_mph`**; daily `wind_gust_max_mph` | `wind_mph` in DB and service; gusts in the service only |
  | Rain | `precip_24h_in`; also `precip_in` (current 15 min); daily `precip_in`, `precip_probability_pct` | `precip_24h_in` in DB; the rest in the service only |
  | Soil moisture | `soil_moisture` | DB and service. Units are m³/m³ in the top 1 cm (about 0 to 0.5), not the 0–1 in `datamodel.md`. The flood service treats ≥ 0.4 as saturated |
  | Weather type | `weather_code` (WMO), `cape_j_kg` | service only |
  | Precipitation type | **derived from `weather_code`** (decided: no separate `precip_type` field) | service only. Freezing drizzle 56–57, freezing rain 66–67, snow 71–77 and 85–86, thunderstorm with hail 96 and 99 |
  | Lightning | shape not defined yet | no feed. `alerts` has `type = 'lightning'` and `last_strike_at`; the frontend demo uses 10 miles and a 30-minute window |
  | NWS warnings | `ActiveAlert.event`, `severity`, `onset`, `ends` | `fetch_active_alerts` in `services/nws.py`: a real source for severe-thunderstorm, tornado and high-wind warnings |
  | Crew and labor | **`num_crews`** (number of crews), **`crew_size`** (workers per crew), **`hours_worked`** (hours per day), **`work_start_hour`** (site-local start hour, default 7), **`labor_rate`** | not in the schema, API or frontend yet; these are the proposed names. `site_workers` holds contact emails, not headcount |
  | Forecast | `daily` (today and tomorrow): `temp_max_f`, `feels_like_max_f`, `precip_in`, `precip_probability_pct`, `wind_gust_max_mph`, `weather_code` | service only. **Decided: use only what's there; no hourly request.** The daily forecast has no humidity or sustained wind |

  **Still missing:** WBGT/solar radiation, snow/ice depth, a lightning feed. Hourly forecasts are
  not being added.
  Each plan says how it copes. To confirm: is `labor_rate` the base wage or the fully loaded
  cost (wages + benefits + overhead)?
- **Persistence is a backend change.** The `conditions` table lacks `humidity_pct`,
  `wind_gust_mph`, `weather_code` and the new fields. Model code takes a plain object with the
  names above (the `CurrentConditions` shape), so it doesn't depend on the table. Adding the
  columns (a proposed migration `0007`) is for the backend owner.
- **Crew, labor and start time come from the inputs.** Defaults exist only as a fallback when
  a field is null (the start hour defaults to 7; for the others, tests and demos), and every estimate records which values were defaulted.
- **Each branch owns its own package** (`model/productivity/`, `model/closure/`) with config
  YAML and tests. `LossEvent` is defined in each package for now and unified when the cost
  model is built, to avoid merge conflicts.
- **Honesty labels:** assumed curves are "illustrative, pending calibration"; thresholds cite
  their source.

## Scope

- **In:** lightning, wind (sustained and gusts), heavy rain/flooding (including excavations
  after rain), hail/severe storms, icing, and heat **only where a rule actually mandates
  stopping work**.
- **Out:** heat *slowdown* (productivity branch); dollar amounts (later cost model).

## Steps

1. **Research and record rules** in `model/closure/rules.yaml`. One entry per rule with: `id`,
   `hazard`, `metric` (which input), `threshold`, `scope` (`site` or a named equipment/work
   type), `severity`, `resume_condition`, `source_type` (regulation | consensus standard |
   manufacturer | agency guidance | company policy), `citation`, `jurisdiction`. Leads, **all
   to verify**:
   - **Lightning:** NWS gives the 30-minute wait after the last lightning or thunder. The
     10-mile radius is **not NWS**; it comes from USACE EM 385-1-1 (§16.I.03), read through a
     reproduction (the edition is unknown and the primary PDF was not opened). Whole site →
     `stop_work`.
   - **Wind, scaffolds:** OSHA 29 CFR 1926.451(f)(12): no scaffold work in storms or high wind
     unless a competent person approves and fall protection is used.
   - **Wind, cranes:** OSHA 1926 Subpart CC defers to manufacturer wind limits (usually on
     gusts).
   - **Wind, aerial work platforms:** the "ANSI A92: 12.5 m/s ≈ 28 mph" figure is unconfirmed
     (the standard is paywalled; 12.5 m/s appeared only in European EN 280 material). It ships
     as company policy.
   - **Wind, solar module handling:** manufacturer installation manuals (panels act as sails).
   - **Wind, general:** USACE EM 385-1-1 (explicit weather limits; a strong source for
     whole-site wind thresholds).
   - **Rain/excavations:** OSHA 1926.651 (no work in excavations with accumulating water;
     inspection after rainstorms). A `warning`, not a site stop.
   - **Concrete:** ACI 305/306 hot/cold weather placement (a material constraint, not a safety
     one: a `warning` for pours).
   - **Heat:** Cal/OSHA §3395 (80 °F and 95 °F triggers). Check whether any rule actually
     mandates stopping, and the status of the federal OSHA heat rule. If no rule mandates a
     stop, heat stays in the productivity branch.
2. **Severity mapping (site-level).**
   - `stop_work` (whole site): rules that stop all outdoor work, e.g. lightning within range,
     a site-wide wind limit, a tornado or severe-thunderstorm warning. **Flooding is never
     escalated** (decided): it keeps its existing `warning` severity (see step 3).
   - `warning`: rules that restrict specific work (cranes, lifts, scaffolds, excavations,
     pours) or are early warnings below a stop threshold. The message names the restricted
     work.
   - Several rules firing at once → the site gets the highest severity, and the message lists
     every reason.
3. **Map rules to inputs.**
   - Wind: sustained limits use `wind_mph`; gust limits (cranes, lifts, panels) use
     `wind_gust_mph` (tomorrow: `wind_gust_max_mph`). Each rule in `rules.yaml` says which one
     it uses.
   - **Rain/flood is already implemented.** `assess()` in
     `backend/app/services/flood_warnings.py` flags a site when NWS has a flood product in
     effect, or when `precip_24h_in` is at least 1.0 in **and** the site is near a river or in
     or within 0.5 mi of a FEMA flood zone. It writes `type = 'flood'`, `severity = 'warning'`,
     with at most one active flood alert per site (unique index `alerts_one_active_flood`).
     Don't reimplement it; the closure engine consumes its result. **Decided: flooding is
     never escalated to `stop_work`;** it stays a `warning`. Rules about water in excavations
     (OSHA 1926.651) are `warning`-level too.
   - Lightning: the new lightning input (strike distance and time) against the stop radius;
     write the most recent strike to `alerts.last_strike_at` and clear 30 min after it. The
     frontend demo already uses 10 miles and 30 minutes (`LIGHTNING_WINDOW_MS` in
     `sitesentinel/lib/risk.ts`, the message in `sitesentinel/lib/data.ts`). Keep one
     configured value that both use, and check it against the NWS guidance.
   - Hail and severe storms: NWS warnings by `ActiveAlert.event` (e.g. "Severe Thunderstorm
     Warning", "Tornado Warning", "High Wind Warning") from `fetch_active_alerts`, plus
     `weather_code` 96 and 99 (thunderstorm with hail) and high `cape_j_kg`. This replaces the
     earlier "no hail field" gap.
   - Icing: `weather_code` 56–57 and 66–67 (freezing drizzle / rain) with `temp_f`. Decided:
     `weather_code` is enough; there is no separate precipitation-type field.
4. **Engine** in `model/closure/evaluate.py`:
   - `evaluate(site, conditions, active_alerts) -> SiteDecision`, where `SiteDecision =
     {severity: none|warning|stop_work, reasons: [{rule_id, hazard, value, threshold,
     restricted_work}], resume_condition}`.
   - Hysteresis: resume only after the metric stays below the threshold for a configured
     period (lightning: 30 min after the last strike), to avoid flapping.
   - `to_alerts(decision)` maps the decision to the existing `alerts` columns (`type`,
     `severity`, `message`, `last_strike_at`, `cleared_at`), following the same
     insert-or-update pattern `record_alerts()` uses for flood alerts.
   - `to_loss_events(decision, forecast)`: `stop_work` produces a `stoppage` LossEvent with
     `activity = "site"`, `fraction_lost = 1.0`, and estimated hours (lightning: until the last
     strike + 30 min; wind: until the forecast drops below the limit; daily forecasts give
     coarse whole or partial days, since only daily forecast values exist), clipped to the workday window
     (`work_start_hour` for `hours_worked` hours; see the shared contract). Hours outside the window cost nothing. Warnings produce no LossEvent for now; restricting cranes
     or lifts without stopping the site is a partial loss the cost model can add later.
5. **Notes for the later cost model** (record, don't build): closed-day cost = `num_crews` ×
   `crew_size` × stopped hours × `labor_rate` + fixed daily costs (equipment rental,
   supervision, overhead) − work made up later. Crew and labor now arrive as inputs; fixed daily
   costs and schedule slack are still missing.

## Files

- `model/closure/rules.yaml`, `evaluate.py`, `SOURCES.md`
- `model/tests/test_closure.py`

## Verification

- Unit tests at each threshold boundary (just below / at / above), for gust vs. sustained wind
  selection, for highest-severity-wins when several rules fire, and for resume/hysteresis
  timing.
- A null `work_start_hour` falls back to 7.
- Stoppage hours outside the workday window are dropped, and stoppage hours per day never
  exceed `hours_worked`.
- A test that every rule in `rules.yaml` has `source_type` and `citation`.
- A test that a flood condition never produces `stop_work`.
- Replay a synthetic storm day (gusts rising past the lift limit → `warning`; then a lightning
  strike within range → site `stop_work`; then 30 min with no strikes → cleared). Confirm the
  alerts produced and the stoppage hours.
- Spot-check 2–3 historical NOAA events near sample sites from `model/cache/storms.parquet`.
- Fixtures are built from the `CurrentConditions` / `DailyForecast` / `ActiveAlert` shapes in
  `backend/app/services/open_meteo.py` and `nws.py`.

## Open questions

- Shape of the lightning input (strike distance vs. raw strike coordinates, how often it
  updates).
- Whether `work_start_hour` should allow minutes (e.g. 7:30) instead of whole hours.
- Whether to add alert types for wind, hail and icing, or keep mapping them to `storm`.
- **Answered:** no regulation or standard found sets a site-wide wind limit. Numbers exist only
  per activity (personnel hoisting 20 mph, crane limits, manufacturer limits). So wind alone
  gives a `warning` naming the restricted work, and a site stops for wind only through an NWS
  tornado or severe-thunderstorm warning, labelled company policy. Whether to set a company
  site-wide wind number is a business decision.

## Status

Implemented in `model/closure/` (24 tests): `rules.yaml` (15 rules with citations and source
types), `evaluate.py`, `SOURCES.md`, `PROPOSALS.md`. Rules that are not backed by a primary
source are labelled company policy.
