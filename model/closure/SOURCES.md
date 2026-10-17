# Sources for rules.yaml

Thresholds here rest on regulations, standards and agency guidance, not on our data: the injury
analysis (`model/modelreadme.md`) found no detectable injury link for wind, hail or lightning.
Research date: 2026-10-17. Status: **verified** (read primary text), **partial** (number or
wording from a secondary copy, or the number is ours), **unverified** (labelled company policy).

## Verified (primary text read)

| Rule | Source | Wording / finding |
|---|---|---|
| `scaffold_*` storm/wind | 29 CFR 1926.451(f)(12), eCFR | "Work on or from scaffolds is prohibited during storms or high winds unless a competent person has determined that it is safe for employees to be on the scaffold and those employees are protected by a personal fall arrest system or wind screens." No numeric limit. |
| `personnel_hoist_wind_20mph` | 29 CFR 1926.1431(k)(8)(i), osha.gov | "When wind speed (sustained or gusts) exceeds 20 mph at the personnel platform, a qualified person must determine if" it is safe; if not, the lift must not begin or must be terminated. The only numeric wind figure found in OSHA 1926 Subpart CC, and it applies to hoisting people only. |
| (no rule) crane wind | 29 CFR 1926.1417(n), osha.gov | Competent person "must adjust the equipment and/or operations to address the effect of wind, ice, and snow". No number. |
| `excavation_water` | 29 CFR 1926.651(h)(1), (k)(1), osha.gov | No work in excavations with accumulated or accumulating water unless adequate precautions are taken; "Inspections shall also be made after every rainstorm or other hazard increasing occurrence." No number for "rainstorm". |
| `lightning_*` 30 min | weather.gov/safety/lightning-safety-overview | "wait 30 minutes after the last lightning or thunder before going back outside"; "When Thunder Roars, Go Indoors!". NWS gives **no distance**. |

## Partial

- `lightning_within_range` (10 mi, 30 min) and `crane_wind_20mph`: USACE EM 385-1-1 Sec. 16.I.03
  and 16.I.01, read from OSHAcademy's reproduction of the manual. The edition is not stated and
  the official USACE PDF was not opened. EM 385-1-1 binds USACE contracts only. Quoted: crane
  operations cease above 20 mph and the lift supervisor evaluates; lightning within 10 miles
  stops work until 30 minutes after the last thunder or flash.
- 30-minute rule also appears in OSHA/NOAA FS-3863, "Lightning Safety When Working Outdoors"
  (read via a search summary and mirrors, not the OSHA PDF).
- `scaffold_icing`: 1926.451(f)(8) (no work on scaffolds covered with snow, ice or slippery
  material except to remove it) was located by a summarising fetch, not read verbatim.
- `concrete_cold_weather`: ACI 306R cold weather concreting, 40 F, from secondary summaries
  (ACI documents are paywalled). The older edition used a multi-day average definition.
  ACI 305 hot weather: its 95 F figure is a limit on concrete temperature, not air temperature,
  so no air-temperature rule was added.
- `flood_risk`: the 1.0 in threshold is the backend's own setting.
- `thunderstorm_now`: using a thunderstorm weather code as a proxy for thunder is our mapping.

## Unverified (company policy, explicitly labelled)

- `nws_tornado_warning`, `nws_severe_thunderstorm_warning` as site `stop_work`: no OSHA or
  consensus text found that mandates stopping. NWS defines a severe thunderstorm warning as
  gusts >= 58 mph or hail >= 1 in.
- `nws_high_wind_warning`: NWS numeric High Wind criteria were not confirmed.
- `aerial_lift_wind_28mph`: the plan's "ANSI A92: 12.5 m/s ~ 28 mph" could not be confirmed
  (A92.20 is paywalled). 12.5 m/s was found only in European EN 280 material. Real limits are
  per machine (manual and data plate).
- `hail_thunderstorm_code`: no regulation found for hail.
- Hysteresis holds of 30 min on wind rules are illustrative, not sourced.

## Dropped

- **Solar module handling wind limits** (manufacturer manuals): no manual read; a number would be
  invented.
- **High CAPE** as a trigger: no cited threshold.
- **Heat:** Cal/OSHA 8 CCR 3395 triggers are 80 F (shade must be present) and 95 F (high-heat
  procedures). It requires no work stoppage; the closest provisions concern rest after symptoms.
  The federal OSHA heat rule (proposed 2024-08-30) is still a proposal as of mid-2026, and the
  heat National Emphasis Program status was unclear in sources. No rule mandates a stop, so heat
  stays in the productivity branch.

## Answer to the open question: site-wide wind limit

No regulation or standard found sets a site-wide wind limit. Numbers exist only per activity:
personnel hoisting 20 mph (OSHA 1926.1431), cranes 20 mph (EM 385-1-1, USACE contracts only),
and manufacturer limits for lifts and panels. Scaffolds and crane operations otherwise defer to
a competent person. So there is **no site-wide wind `stop_work` rule**; wind alone yields a
`warning` naming the restricted work. Sites stop for wind only through NWS Tornado or Severe
Thunderstorm Warnings, which are labelled company policy.
