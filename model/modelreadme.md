# SiteSentinel — Model

## Goal

Find out whether severe weather is associated with **severe workplace injuries**. If it is,
work out which kinds of weather matter, by how much, and for which industries. The aim is to
learn which weather conditions should raise a job-site hazard flag in SiteSentinel.

## Data

| Source | Location | What it is |
|---|---|---|
| OSHA Severe Injury Reports | `January2015toNovember2025.csv` | ~106k employer-reported injuries (hospitalization, amputation, loss of eye), Jan 2015 to Nov 2025. Each has a date, lat/lon, NAICS code, narrative, and OIICS event/nature/source codes. `UPA` is the unique key (`ID` has 5 duplicates). |
| NOAA Storm Events | `storm_event_details/` | ~712k storm events, 2015–2025, one CSV per year. Format documented in `Storm-Data-Bulk-csv-Format.pdf`. |
| | `storm_event_locations/`, `storm_event_fatalities/` | Not used; only 2015 is downloaded, and more years aren't needed for this analysis (see below). |
| NWS zone/county polygons (historical) | `reference/ugcs_<year>.geojson` (gitignored, downloaded) | One snapshot per year (valid July 1), from the Iowa Environmental Mesonet UGC API. |

### Why the locations and fatalities files aren't used

Checked against the 2015 files:

- **Locations** only has rows for events that already have begin/end coordinates (thunderstorm
  wind, hail, flood, tornado, lightning; 99.4% of 2015 point events). It adds coordinates for
  **none** of the zone-based events (heat, winter, cold, high wind), which are the ones that lack
  them; the historical zone polygons cover those instead. Most events get 1–2 points (median 1,
  max 8), mostly repeating begin/end, with a few extra points along tornado paths or around
  flood areas. Point events show no effect at any radius, so the extra precision is unlikely to
  change the results. It would only be worth downloading 2016–2025 (~5 MB/year) for a stricter
  point-event analysis.
- **Fatalities** has one row per storm death among the **general public** (age, sex, where they
  died; 695 in 2015). It can't be linked to OSHA injuries. Per-event death counts are already in
  the details files (`DEATHS_DIRECT`/`DEATHS_INDIRECT`). Only 9 of the 2015 deaths were
  "Heavy Equipment/Construction"; most were in vehicles (248), outdoors (130), or in water (99).
  This could inform SiteSentinel's alert wording, but it isn't evidence about worker injuries.

## Approach and key decisions

1. **Spatial match, not a state-level join.** Each injury is linked to storm events near it.
   Matching on state and day alone would be too coarse.
2. **Two kinds of storm events need two matching methods.**
   - *Point events* (tornado, hail, thunderstorm wind, flood, lightning) have lat/lon. These
     are matched with a **haversine BallTree**: the event must be active that day, and the
     result is the minimum distance from the injury per group. Radii tested: 10 / 25 / 50 km.
   - *Zone/county events* (heat, cold, winter, high wind, tropical, smoke, fog, dust), ~43% of
     events, have **no coordinates**, only an NWS zone/county code (UGC, e.g. `ILZ013`). These
     are matched **point-in-polygon**: the injury lies inside the event's zone.
3. **Polygons rather than zone centroids.** Exploration showed that a centroid radius either
   misses 11% of injuries that fall inside the zone (at 25 km) or links each injury to about 5
   neighbouring zones that didn't have the event (at 50 km). Point-in-polygon is exact and
   takes under a second.
4. **Historical zone polygons.** NWS renumbers zones over time. The current NWS files matched
   only ~89% of 2015–2020 zone events. Yearly IEM snapshots match **99.8%**. If a code is
   missing from its own year's snapshot, the nearest year that has it is used.
5. **Exposure window.** An injury day counts as exposed if it falls between the event's begin
   date and one day after its end date. The extra day covers cleanup and aftermath.
6. **Study design: time-stratified case-crossover.** OSHA data has no denominator (worker-days),
   so each injury is compared with *itself*. The injury day is compared with the other days in
   the same month that fall on the same weekday, at the same location. This controls for
   location, industry, season, weekday, and long-term trends by design.
7. **Model: conditional logistic regression** (statsmodels `ConditionalLogit`), stratified by
   injury, with all weather groups fitted together. Odds ratio > 1 means more severe injuries
   on exposed days. Fitted for all injuries, outdoor industries (NAICS 11, 21, 22, 23, 48–49)
   and indoor industries, at each radius.
8. **Validation and placebo.**
   - Injuries that OSHA itself codes as weather-caused should respond strongly to the matching
     group: heat illness → heat, cold injury/frostbite → cold, slip on ice/snow → winter.
   - Placebo: shift all storm dates by 364 days, which keeps the weekday. Odds ratios should
     be about 1.

Dropped from analysis: marine events, drought (lasts for months, so no daily signal), and other
ungrouped event types. The `EVENT_TYPE` → group mapping is in `analysis/config.py`.

## Files

| Path | Purpose |
|---|---|
| `pyproject.toml`, `uv.lock` | `uv` project: pandas, pyarrow, geopandas, scikit-learn, statsmodels, matplotlib, duckdb; dev: pytest, ruff |
| `analysis/config.py` | Paths, radii, exposure window, event-type → group mapping, outdoor NAICS |
| `analysis/fetch_data.py` | Downloads NOAA Storm Events CSVs (newest revision per year) into `storm_event_*/` |
| `analysis/fetch_zones.py` | Downloads yearly UGC polygon snapshots to `reference/` |
| `analysis/load.py` | Cleans OSHA + NOAA data → `cache/osha.parquet`, `cache/storms.parquet` |
| `analysis/exposure.py` | Builds the case-crossover table (injury × reference day × exposure) → `cache/exposure.parquet`; `--placebo-shift-days 364` → `cache/exposure_placebo.parquet` |
| `analysis/model.py` | Conditional logit fits → `out/odds_ratios.csv` (`--placebo` → `out/odds_ratios_placebo.csv`) |
| `analysis/report.py` | Forest-plot PNGs → `out/forest_*.png` |
| `tests/test_analysis.py` | Unit tests: damage parsing, reference days, day expansion, point distance matching, zone + point exposure merge |
| `cache/`, `reference/` | Generated, gitignored |
| `January*.csv`, `storm_event_*/` | Raw source data, gitignored; see "Getting the data" |

## Getting the data

The raw data (~750 MB) is not in git. These steps are written so a coding agent (or a person)
can follow them exactly. All paths are relative to `model/`; run everything from `model/`.

**Expected layout when done:**

```
model/
├── January2015toNovember2025.csv                          # OSHA, ~55 MB, ~106k rows
├── storm_event_details/
│   └── StormEvents_details-ftp_v1.0_d<YYYY>_c<YYYYMMDD>.csv   # one per year 2015–2025, ~50–75 MB each
└── reference/
    └── ugcs_<YYYY>.geojson                                  # one per year 2015–2025, ~25–30 MB each
```

`storm_event_locations/` and `storm_event_fatalities/` are optional and not used by the analysis.

### Step 1: environment

```sh
cd model
uv sync
```

### Step 2: NOAA Storm Events (scripted)

```sh
uv run python -I analysis/fetch_data.py            # details only (required), ~2-5 min
```

- Source: NCEI bulk CSVs, https://www.ncei.noaa.gov/pub/data/swdi/stormevents/csvfiles/
  The script downloads the `.csv.gz` files and decompresses them into the folders above.
- The `cYYYYMMDD` part of the filename is NOAA's revision date and changes when NOAA updates a
  year. The script picks the newest revision. Years that already have a file are skipped;
  `--refresh` replaces them with the newest revision.
- **There must be exactly one file per year.** Two revisions of the same year duplicate events,
  and `load.py` stops with an `EVENT_ID` uniqueness assertion.
- Optional tables: `--tables details locations fatalities`.
- Results may differ slightly from those in this README if NOAA has revised a year since the
  revisions used here: details 2015–2016, 2018–2021, 2023: `c20260323`; 2017: `c20260519`;
  2022: `c20260625`; 2024: `c20260728`; 2025: `c20260819`.

### Step 3: OSHA Severe Injury Reports (manual download)

OSHA's website blocks scripted downloads (HTTP 403 for `curl`/`urllib`), so **an agent should
not try to fetch this file. Ask the user to download it** and save it into `model/`:

1. Open https://www.osha.gov/severe-injury-reports in a browser.
2. Download the severe injury reports data file (CSV; Excel also works if saved as CSV) covering
   January 2015 to the latest month available.
3. If it's zipped, unzip it. Save the CSV as `model/January2015toNovember2025.csv`, or pass a
   different path: `uv run python -I analysis/load.py <path-to-osha.csv>`.
   (`analysis/config.py` `OSHA_CSV` sets the default.)

To check the file, the header row must include these columns used by `load.py`: `UPA`,
`EventDate` (`m/d/YYYY`), `State`, `Latitude`, `Longitude`, `Primary NAICS`, `Hospitalized`,
`Amputation`, `Final Narrative`, `EventTitle`, `NatureTitle`, `SourceTitle`. The file used here
had 105,996 rows from 2015-01-01 to 2025-11-30. A newer file with more months works, but its
results will differ from this README.

### Step 4: NWS zone polygons (scripted)

```sh
uv run python -I analysis/fetch_zones.py           # ~5-20 min, ~300 MB, from the IEM API
```

Files that already exist are skipped. Re-run it if a download was interrupted (partial files
end in `.part` and are ignored).

### Step 5: verify

```sh
uv run python -I analysis/load.py
```

This should print ~711,827 storm events from 11 files, and 105,996 OSHA reports (105,921 kept
after dropping invalid coordinates). It writes `cache/storms.parquet` and `cache/osha.parquet`.

## How to run

Once the data is in place (see above):

```sh
cd model
uv run python -I analysis/load.py                                   # ~1 min
uv run python -I analysis/exposure.py                               # ~1 min
uv run python -I analysis/exposure.py --placebo-shift-days 364
uv run python -I analysis/model.py                                  # ~7 min
uv run python -I analysis/model.py --placebo
uv run python -I analysis/report.py --radius 25
uv run pytest -q
```

## Results

All numbers are odds ratios (OR) with 95% confidence intervals from the conditional logit.
OR 1.21 means about 21% more severe injuries on exposed days than on comparable unexposed days
at the same place. Full table: `out/odds_ratios.csv`. Charts: `out/forest_*_25km.png`.

### Validation: the matching works

Injuries that OSHA itself codes as weather-caused line up strongly with the matching NOAA
events (`out/forest_validation_25km.png`):

| OSHA-coded injuries | Matched weather group | OR (95% CI) | Informative injuries |
|---|---|---|---|
| Exposure to environmental heat | heat | **3.26** (2.50–4.27) | 262 |
| Slip/fall with ice, snow, sleet, or frozen ground in narrative | winter | **3.49** (2.97–4.11) | 625 |
| Exposure to environmental cold / frostbite | cold | **21.6** (4.8–97.3) | 13 (very few; imprecise) |

### Main results (point events at 25 km; zone events by polygon)

| Weather group | All injuries | Outdoor industries | Indoor industries |
|---|---|---|---|
| **Heat / excessive heat** | **1.21** (1.12–1.32) | **1.63** (1.42–1.87) | 1.02 (0.92–1.14) |
| **Winter storm / ice / snow** | **1.15** (1.09–1.22) | 1.09 (0.98–1.22) | **1.17** (1.10–1.25) |
| **Tropical storm / hurricane** | **0.67** (0.54–0.83) | **0.53** (0.36–0.79) | **0.75** (0.59–0.97) |
| Flood / heavy rain | 1.00 (0.94–1.05) | **0.85** (0.78–0.94) | 1.06 (1.00–1.13) |
| Dense fog | 1.21 (0.92–1.59) | 1.21 (0.74–1.96) | 1.25 (0.90–1.75) |
| Wildfire / smoke | 1.14 (0.86–1.52) | 1.11 (0.72–1.73) | 1.18 (0.81–1.73) |
| Hail | 1.03 (0.97–1.10) | 1.11 (1.00–1.24) | 1.00 (0.93–1.08) |
| High / strong wind | 1.02 (0.93–1.13) | 1.08 (0.91–1.28) | 0.99 (0.88–1.11) |
| Thunderstorm wind | 1.00 (0.96–1.05) | 1.00 (0.92–1.08) | 1.00 (0.95–1.06) |
| Cold / wind chill | 0.92 (0.80–1.04) | 0.89 (0.70–1.13) | 0.93 (0.80–1.08) |
| Tornado | 0.94 (0.84–1.07) | 0.85 (0.68–1.06) | 0.98 (0.85–1.13) |
| Lightning | 0.89 (0.78–1.02) | **0.79** (0.63–0.99)* | 0.96 (0.81–1.13) |
| Dust storm | 1.00 (0.45–2.22) | — | — |

Bold = 95% CI excludes 1. Dashes mean too few exposed cases to estimate. \* Not stable across
radii; see sensitivity below.

### Interpretation

- **Heat is the clearest hazard.** Outdoor workers have ~63% more severe injuries on
  heat-event days. Indoor industries show no effect, which is the expected pattern if the link
  is real.
- **Winter weather raises severe injuries by ~15%.** The effect is slightly larger in "indoor"
  industries. Those workers are still outdoors for parking lots, deliveries, and commutes on
  the job, and the slip-on-ice validation subset backs this up.
- **Hurricanes and outdoor-industry flooding show *fewer* injuries.** This most likely reflects
  work stopping (sites closed, crews stood down), not safer conditions. For SiteSentinel, read
  these as "fewer people exposed," not "safe."
- **Point events (tornado, hail, lightning, thunderstorm wind) show no reliable effect.** Their
  estimates change direction across radii (e.g. outdoor hail: 0.85 at 10 km, 1.11 at 25 km,
  1.04 at 50 km), which looks like noise. These events are brief and very local; a daily,
  ~10–50 km resolution is probably too coarse to detect them.
- Fog and wildfire smoke point upward (~1.2), but the confidence intervals include 1. More data
  or a finer design would be needed.

### Placebo check: passed

With all storm dates shifted by 364 days (`out/odds_ratios_placebo.csv`,
`out/forest_placebo_25km.png`), every confidence interval includes 1:

| | Actual dates | Placebo (dates +364 days) |
|---|---|---|
| Heat, all injuries | 1.21 (1.12–1.32) | 1.01 (0.92–1.11) |
| Heat, outdoor industries | 1.63 (1.42–1.87) | 1.02 (0.87–1.20) |
| Winter, all injuries | 1.15 (1.09–1.22) | 1.03 (0.97–1.09) |
| Tropical, all injuries | 0.67 (0.54–0.83) | 0.96 (0.79–1.17) |
| Heat-illness validation | 3.26 (2.50–4.27) | 0.90 (0.62–1.31) |
| Slip-on-ice/snow validation | 3.49 (2.97–4.11) | 1.20 (0.95–1.52) |

The associations disappear when the weather dates are wrong, so they come from the actual
weather days, not from the study design. The cold-injury validation can't be estimated under
the placebo (too few of the 37 cases line up with shifted cold days).

### Sensitivity across radii

Zone-event estimates are identical at 10, 25, and 50 km because they use polygons. For point
events, the number of exposed injuries grows with radius (e.g. flood: 779 at 10 km, 2,057 at
25 km, 4,036 at 50 km). Their ORs stay close to 1 at every radius. The one consistent signal is
outdoor flood below 1 (0.80–0.87). Outdoor lightning is below 1 only at 25 km (0.79), not at
10 or 50 km, so treat it as noise.

## Status

- Done: data loading, zone polygons, exposure tables, real and placebo models, charts, unit
  tests (5 passing). One tornado match was checked by hand against the raw NOAA and OSHA rows.
- Bug fixed: a few hail, tornado, and thunderstorm-wind events have no coordinates and are
  matched by zone instead. Their zone column used to overwrite the distance column for the same
  group in `model.design()`, which dropped thunderstorm wind from the first real run and hail
  and tornado from the first placebo run. Exposure now counts if *either* match applies
  (covered by `test_design_combines_zone_and_point_exposure_for_same_group`). After refitting,
  heat and winter estimates were unchanged to two decimals; thunderstorm wind is 1.00.

### Possible next steps

- Add heat intensity (Heat vs. Excessive Heat) and lagged days (injury 1–3 days after) to see
  whether the effect grows with severity or persists.
- Break out outdoor industries further, e.g. construction (23) vs. agriculture (11) vs.
  transportation (48–49).
- Stricter definition for point events (same day, ≤10 km, with damage reported) to see whether
  a real signal is hiding in the noise.

## Productivity weighting and site-closure rules

Two follow-on components, built from the plans in `plans/` (which hold the full design and the
decisions behind it). They don't use the injury results as inputs; the injury analysis is only
the motivation. Both target **outdoor construction work** at plants under construction.

| Component | Files | What it does |
|---|---|---|
| Productivity weighting | `productivity/` (`estimate.py`, `heat_metrics.py`, `curves.yaml`, `defaults.yaml`, `SOURCES.md`, `PROPOSALS.md`) | Hourly productivity loss from heat and cold/ice for today and tomorrow, as low/central/high `slowdown` `LossEvent`s, plus lost worker-hours and a labor-cost equivalent (`summarize`) and `top_factors` entries (`hazard_factors`) |
| Site-closure rules | `closure/` (`evaluate.py`, `rules.yaml`, `SOURCES.md`, `PROPOSALS.md`) | 15 cited stop-work rules → a site-level `none` / `warning` / `stop_work` decision, `alerts` records, and `stoppage` `LossEvent`s |
| Tests | `tests/test_productivity.py`, `tests/test_closure.py` | `uv run pytest -q` (48 pass, 1 skipped: it imports the backend weather model, which needs `pydantic`) |

Inputs use the backend's field names (`humidity_pct`, `wind_gust_mph`, `weather_code`, ...; see
the table in either plan). Crew and labor inputs (`num_crews`, `crew_size`, `hours_worked` per
day, `work_start_hour` default 7, `labor_rate`) don't exist in the backend yet; null values fall
back to documented defaults and each result records which ones it used.

### Decisions

- `stop_work` means the **whole site**. Rules that restrict one kind of work (cranes, lifts,
  scaffolds, excavations) are site-level `warning`s that name the work.
- Flooding is never escalated; it stays a `warning` (the backend already raises flood alerts).
- `weather_code` is the only precipitation-type source. No hourly forecast is requested, so
  tomorrow's numbers come from daily values and are upper bounds.
- Wind thresholds: **no regulation or standard found sets a site-wide wind limit**; numbers exist
  only per activity. A site stops for wind only through an NWS tornado or severe-thunderstorm
  warning, which is labelled company policy.

### What is verified and what isn't

- **Heat curve:** published (Foster et al. 2022, Int J Biometeorol 66:175, heat-index equation
  with its solar correction), applied to `heat_index_f`. The "high clothing coverage" curve is
  used, which gives somewhat higher losses than lighter work clothing. Implied loss for heavy
  outdoor work: about 4% at 85 °F, 12% at 95 °F, 20% at 105 °F, 27% at 115 °F. A discrepancy in
  Foster et al. 2021 (its abstract says 10% loss at WBGT 18 °C; its equations give about 2%) is
  unresolved and noted in `productivity/SOURCES.md`.
- **Illustrative, pending calibration:** all cold and ice/snow numbers (the Koehn & Brown source
  was not reachable), the sun scaling across scenarios, the WBGT sun adjustment, onset
  thresholds, and tomorrow's widening.
- **Labor rate:** BLS ECEC, Construction, June 2026: $51.96 total compensation, $36.13 wages
  (load factor 1.438). The unit ("per hour worked") is assumed, not read.
- **Closure rules:** verified from primary text: OSHA 1926.451(f)(12), 1926.1431(k)(8)(i)
  (personnel hoisting above 20 mph), 1926.1417(n), 1926.651; NWS 30-minute lightning wait. The
  10-mile lightning radius comes from USACE EM 385-1-1 read through a reproduction, **not from
  NWS**. The ANSI A92 "28 mph" aerial-lift limit is unconfirmed and ships as company policy, as do
  the NWS tornado / severe-thunderstorm / high-wind stops and the hail stop. Heat has no
  mandatory-stop rule (California §3395 has triggers only; the federal rule is still a
  proposal), so heat stays in the productivity component.
- `thunderstorm_now` (weather codes 95, 96, 99) stops the site as a stand-in for lightning until
  a real lightning feed exists, so any forecast thunderstorm closes the site.

### Using the two together

Both emit `LossEvent`s with the same core fields (`site_id`, `start`, `end`, `kind`,
`fraction_lost`, `scenario`, `factor`, `rule_or_curve_id`, `source`, `activity`). Each adds its
own optional metadata (`assumptions` in productivity, `defaults_used` in closure) and each package
defines its own class; unify them when the cost model is built. A later cost model must not
double count: hours when the site is stopped shouldn't also be charged as a slowdown. Lost
profit (work made up later, overtime, schedule delay, equipment and overhead) is not modelled;
these are labor-cost equivalents only.

Proposed backend and frontend changes (a `conditions` migration for the new columns, crew and
labor columns, a `cost_estimates` table, new alert types, a lightning feed, persisting
hysteresis state) are in each package's `PROPOSALS.md`. None are implemented.

## Caveats

- OSHA covers only *severe* injuries, and reporting differs between federal and state-plan
  states. The case-crossover design handles this for within-injury comparisons.
- Storm reports depend on population and spotter coverage. Correlation is not causation: an
  exposed injury may be unrelated to the weather (e.g. an indoor press-brake amputation near a
  tornado).
- IEM polygons are simplified, so injuries close to a zone boundary may be misassigned. OSHA
  coordinates may be the employer's address rather than the incident site.
