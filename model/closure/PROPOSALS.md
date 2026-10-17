# Proposed changes outside `model/closure/` (not implemented)

For the backend, frontend and model owners.

1. **Alert types.** `alerts.type` and the frontend `AlertType` are `lightning | flood | heat |
   wildfire | storm`. Wind, hail and icing map to `storm` today (`Rule.alert_type`). Add `wind`,
   `hail`, `icing` if the UI should tell them apart. The engine emits at most one alert per
   (site, type), matching the unique-active-alert pattern of `alerts_one_active_flood`; a
   similar partial unique index is needed per new type.
2. **`conditions` columns (migration `0007`).** Persist `wind_gust_mph`, `weather_code`,
   `humidity_pct`, `cape_j_kg` so decisions can be replayed and hysteresis state recovered.
3. **Hysteresis state.** `SiteDecision.last_triggered` (rule id to datetime) must be stored
   between runs, for example a JSONB column on `sites` or a small table, and fed back as
   `previous`. Without it only the lightning rule (which uses strike times) holds.
4. **Lightning feed.** The engine takes `Strike(distance_mi, at)` records. A real feed (for
   example a commercial or NOAA GLM-derived source) should deliver strikes within the radius,
   polled every 1 to 5 minutes. Until then `thunderstorm_now` uses weather codes 95, 96, 99 as
   a proxy.
5. **One lightning constant.** The radius (10 mi) and hold (30 min) are in
   `closure/rules.yaml` settings. The frontend duplicates them (`LIGHTNING_WINDOW_MS` in
   `sitesentinel/lib/risk.ts`, "within 10 miles" in `sitesentinel/lib/data.ts`). Serve them from
   one backend config. NWS itself gives only the 30 minutes; the 10 mi comes from EM 385-1-1.
6. **Crew and workday inputs.** `hours_worked` and `work_start_hour` (null defaults to 7) need
   columns or an API field; `Workday` carries them and reports defaults on each `LossEvent`.
7. **Dependency.** `pyyaml` is needed by `closure/` and is not in `model/pyproject.toml`.
8. **Shared `LossEvent`.** `closure.evaluate.LossEvent` adds one optional field, `defaults_used`,
   to the shared contract; unify when the cost model is built.
9. **Decision needed on site-wide wind.** No regulation sets one (see SOURCES.md). If the
   business wants a wind stop, pick a number and label it company policy.
