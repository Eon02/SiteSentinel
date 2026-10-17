# Plan: productivity weighting (heat and cold/ice)

## Context

The injury analysis (`model/modelreadme.md`) found heat associated with more severe injuries
(OR 1.21 overall, **1.63 for outdoor industries**) and winter weather too (OR 1.15). Heat and ice
also slow work even when nobody is hurt, which costs money in lost productivity. This branch
estimates that loss and its labor-cost equivalent. Starting doc: `model/heat-productivity-costs.md`.

**Assumptions for this branch (decided):**
- Sites are EIA-860M power plants **under construction**. The work we care about is **outdoor
  construction work**: heavy workload, in the sun, construction labor rates (NAICS 23 / 2371).
- Output is **both**: (a) lost worker-hours and a labor-cost $ estimate, and (b) a factor in
  `hazard_scores.top_factors`.
- **Humidity is an input** (`humidity_pct`, already returned by the Open-Meteo service), so the
  heat metric is computed from temperature + humidity directly.

The injury findings are motivation, not inputs. The productivity-loss percentage comes from
published research, or from a clearly labelled illustrative curve until it's calibrated.

## Inputs (names from the shared table below)

- Site: `id`, `plant_type`, `timezone`
- Now: `temp_f`, `humidity_pct`, `heat_index_f`, `wind_mph` (for wind chill), `precip_24h_in`,
  `weather_code` (ice/snow)
- Tomorrow (daily only): `temp_max_f`, `feels_like_max_f`, `weather_code`
- Crew and labor: `num_crews`, `crew_size`, `hours_worked` (per day), `work_start_hour`
  (default 7), `labor_rate`

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

- **In:** heat slowdown (main), cold/ice slowdown (secondary).
- **Out:** wind, rain, hail, lightning; any full stop-work condition (closure branch); the 0–10
  hazard-score formula; lost profit.

## Steps

1. **Research and record sources** in `model/productivity/SOURCES.md`. For each source: heat
   metric (WBGT / heat index / UTCI), workload, population, function form, and loss at key
   points. Leads, **all to verify**:
   - Heat: ILO *Working on a Warmer Planet* (2019) and the Kjellstrom/Hothaps work-capacity
     functions it uses; Foster et al. 2021 physical work capacity model (*Int J
     Biometeorology*); Dunne et al. 2013 (*Nature Climate Change*); NIOSH 2016 heat criteria
     document (workload categories).
   - Cold/ice: construction cold-weather productivity factors (Koehn & Brown 1985, ASCE;
     electrical/mechanical contractor association tables).
   - Fix the citations in `heat-productivity-costs.md`: the links carry chatgpt `utm` tags, and
     the MMWR link is labelled as NIOSH's heat-assessment method but is a report on worker heat
     deaths.
2. **Heat metric from temperature + humidity.**
   - Compute a WBGT estimate from `temp_f` and `humidity_pct` with a documented approximation, e.g.
     the simplified shade formula (Australian BoM / ACSM). Add a configurable **full-sun
     adjustment** (+ a few °C), because outdoor construction is in the sun and the inputs have no
     solar radiation. Record the formula and adjustment in `SOURCES.md`.
   - Match each curve's metric: never apply a WBGT curve to air temperature or to heat index.
     If a chosen curve uses heat index, use `heat_index_f` directly: the backend already
     computes it (`heat_index_f()` in `backend/app/services/open_meteo.py`), so don't re-derive
     it.
   - If solar radiation or measured WBGT is added later, switch to it.
3. **Loss curves as config** in `model/productivity/curves.yaml`.
   - Piecewise-linear `metric → fraction_lost` for **heavy outdoor work** (the default). Low /
     central / high variants, each with a source.
   - Until a published curve is in, ship one labelled "illustrative, pending calibration".
4. **Cold/ice.**
   - Cold slowdown curve on temperature, using wind chill from `temp_f` + `wind_mph` (NWS
     formula).
   - Ice/snow slowdown keyed on `weather_code` (freezing drizzle 56–57, freezing rain 66–67,
     snow 71–77 and 85–86) together with `temp_f` and `precip_24h_in`. Decided: `weather_code`
     is enough; there is no separate precipitation-type field.
   - Severe icing as a full stop is the closure branch's job.
5. **Engine** in `model/productivity/estimate.py`:
   - `estimate(site, conditions, daily, crew) -> list[LossEvent]`, where `crew` = `num_crews`,
     `crew_size`, `hours_worked` (per day), `work_start_hour`, `labor_rate` from the inputs. Counts only hours
     inside the workday window (see the shared contract), in `sites.timezone`.
   - **Forecast limits (decided: use what's there, no hourly request).**
     - Today: the current observation is treated as representative of the work window.
     - Tomorrow: only daily values exist, and there is no humidity, so the WBGT calculation
       can't be run. Use `feels_like_max_f` (Open-Meteo apparent temperature, which includes
       humidity, wind and sun) as the heat metric, as a **worst-hour upper bound**. Its curve
       is a documented approximation (apparent temperature treated like a heat index) and is
       labelled lower-confidence. Don't reuse a WBGT curve on it.
     - Tomorrow's cold/ice estimate uses `weather_code` and `temp_max_f`.
   - `summarize(events, crew) -> {lost_worker_hours, labor_cost_usd}` per scenario.
   - Fallback defaults in `model/productivity/defaults.yaml`, used only when an input is null
     (tests and demos). The labor-rate fallback comes from BLS "Employer Costs for Employee
     Compensation" for construction (**verify the figure**). If `labor_rate` turns out to be
     the base wage, apply a configurable load factor for benefits and overhead.
6. **Hazard factor.** Map the day's central `fraction_lost` (weighted by crew-hours) to a
   `top_factors` entry, e.g. `{"factor": "heat_productivity", "weight": ...}` (the
   `HazardFactor` shape in `sitesentinel/lib/types.ts`). `factorLabel()` in
   `sitesentinel/lib/risk.ts` has no entry for it and would fall back to "Heat productivity";
   propose explicit labels for the new factors. Document how the hazard-score owner should
   combine it; the 0–10 formula is out of scope here.
7. **Proposed storage (proposal for the backend owner, not built here):** a `cost_estimates`
   table (`site_id, date, is_forecast, factor, lost_worker_hours_{low,central,high},
   labor_cost_usd_{low,central,high}, assumptions jsonb`). The UI should show the assumptions
   beside every estimate: workers, hours, rate, curve source. The next-day briefing payload
   (`build_briefing()` in `backend/app/services/briefings.py`) is another natural place to
   attach the estimate.

## Files

- `model/productivity/SOURCES.md`, `curves.yaml`, `defaults.yaml`, `heat_metrics.py`,
  `estimate.py`
- `model/tests/test_productivity.py`

## Verification

- Unit tests:
  - zero loss below the curve threshold
  - curves are monotonic, and low ≤ central ≤ high
  - the doc's worked example: 20 workers × 4 h × 15% = 12 worker-hours → $480 at $40/h
    (e.g. `crew_size` 10 × `num_crews` 2)
  - workers = `num_crews` × `crew_size`
  - a null `work_start_hour` falls back to 7, and the estimate records that it did
  - weather outside the workday window (`work_start_hour` for `hours_worked` hours) loses
    nothing, and lost hours per day never exceed `hours_worked`
  - null crew inputs fall back to defaults, and the estimate records that it did
  - the WBGT estimate matches published reference values for the chosen formula
  - fixtures are built from the `CurrentConditions` / `DailyForecast` shapes in
    `backend/app/services/open_meteo.py`
- A synthetic hot day and an icy day for 2–3 sample sites produce sensible LossEvents.
- Curve values match the cited sources at their published points.

## Open questions

- Names of the crew and labor inputs, which don't exist in the backend yet, and whether
  `labor_rate` is the base wage or the fully loaded cost.
- Whether `work_start_hour` should allow minutes (e.g. 7:30) instead of whole hours.
- Which curve to use for apparent temperature (`feels_like_max_f`) on tomorrow's estimate, and
  how much to widen the low/high scenarios to reflect that it's an approximation.
- Acclimatization (the first days of a heat wave are worse), as a later refinement.
