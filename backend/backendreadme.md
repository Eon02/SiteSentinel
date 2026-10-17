# SiteSentinel Backend

FastAPI backend on a shared [Supabase](https://supabase.com) Postgres database with PostGIS. Python dependencies
are managed with [uv](https://docs.astral.sh/uv/), so the only thing you need installed is **uv**. It downloads
the correct Python version (3.13) for you.

## Prerequisites

- uv: `powershell -c "irm https://astral.sh/uv/install.ps1 | iex"` (Windows) or
  `curl -LsSf https://astral.sh/uv/install.sh | sh` (macOS/Linux)
- The team's Supabase connection string (ask whoever owns the Supabase project)

## First-time setup

All commands run from the `backend/` folder.

```bash
uv sync                  # creates .venv with the pinned Python + packages
cp .env.example .env     # then paste the Supabase connection string into DATABASE_URL
```

To find the connection string: Supabase dashboard → **Connect** (top bar) → **Session pooler** → copy the URI and
replace `[YOUR-PASSWORD]` with the database password. Use the session pooler rather than the direct connection:
the direct connection is IPv6-only on the free tier and fails on many home and campus networks.

## Running the API

```bash
uv run fastapi dev app/main.py
```

- API: http://localhost:8000
- Interactive docs: http://localhost:8000/docs
- Health checks: `GET /health` (app) and `GET /health/db` (database + PostGIS version)

## Sites

`POST /sites` adds a job site. Coordinates are stored as a PostGIS point.

```json
{
  "id": "6045",
  "name": "Turkey Point",
  "utility": "Florida Power & Light Co",
  "plant_type": "Nuclear",
  "county": "Miami-Dade",
  "state": "FL",
  "timezone": "America/New_York",
  "near_river": false,
  "latitude": 25.4356,
  "longitude": -80.3308
}
```

- Required: `id` (EIA plant code), `name`, `timezone`, `latitude`, `longitude`. The rest are optional;
  `near_river` defaults to `false`.
- `timezone` must be an IANA name (e.g. `America/Chicago`); it drives the 6 PM local briefing. `state` must be
  a two-letter code.
- Returns `201` with the site, `409` if the id already exists, `422` for invalid fields.
- After adding sites, run `uv run python -m scripts.load_flood_zones` to load their flood zones.

## Weather (Open-Meteo)

`GET /weather?lat=25.9&lon=-80.9` returns weather for any coordinate from [Open-Meteo](https://open-meteo.com)
(free, no API key for non-commercial use). Units are °F, mph and inches; times and dates are local to the
coordinate.

- `current`: temperature, humidity, NWS heat index, feels-like temperature, wind and gusts, rain in the past
  24 hours, top-layer soil moisture (m³/m³), CAPE (thunderstorm energy) and the WMO weather code (95-99 =
  thunderstorm). These map onto the `conditions` table.
- `daily`: today and tomorrow, with max temperature and feels-like, rain total and probability, max gusts and
  weather code, for the next-day briefing.

Invalid coordinates return 422; if Open-Meteo is down or rejects the request, the API returns 502. Python code
can call `app.services.open_meteo.fetch_weather` directly. The base URL is configurable with `OPEN_METEO_URL`
(e.g. for Open-Meteo's paid commercial endpoint).

## Conditions worker

Records each site's current weather into the `conditions` table, which the frontend shows on the dashboard and
site pages.

```bash
uv run python -m scripts.record_conditions                    # every site, once
uv run python -m scripts.record_conditions --site 6145        # specific sites (repeatable)
```

To record continuously, set `CONDITIONS_ENABLED=true` on the one deployed instance that should do it. The API then
records every `CONDITIONS_INTERVAL_S` seconds (default 900, Open-Meteo's 15-minute update interval). Running the
script from cron works too.

- One row per site per Open-Meteo observation time. Re-running within the same 15 minutes updates that row.
- A site whose weather can't be fetched is reported; the rest are still stored.
- `soil_moisture` is stored as a **0-1 saturation fraction**, not Open-Meteo's raw volumetric value (m³/m³, 0 to
  ~0.5 in the top 1 cm). The worker divides by 0.5 (typical saturated soil) and caps at 1, so 0.38 m³/m³ is
  stored as 0.76. The `/weather` endpoint and briefings still report the raw volumetric value.

## Flood zones (FEMA NFHL)

FEMA flood hazard zones from the [National Flood Hazard Layer](https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer)
(layer 28, "Flood Hazard Zones") are stored in PostGIS for every site:

```bash
uv run python -m scripts.load_flood_zones                     # all sites
uv run python -m scripts.load_flood_zones --site 6145         # specific sites (repeatable)
uv run python -m scripts.load_flood_zones --radius-mi 5       # wider area (default 2 miles)
```

- Each zone polygon is clipped to a circle of `--radius-mi` around the site before storing. FEMA polygons can
  cover a whole county (one Memphis zone alone has 74,000 vertices), so storing them whole would quickly fill the
  free tier. Two sites at 2 miles take about 200 kB.
- Re-running replaces a site's zones. Run it after adding sites; FEMA maps change rarely.
- FEMA's server is slow and sometimes stalls, so the loader retries and allows up to 2 minutes per request. A
  site that still fails is reported, and the script exits with an error.

Reading the data:

- `flood_zones`: one row per zone per site, with `fld_zone` (`A`/`AE`/`AH`/`AO`/`VE` = high risk, `X` = moderate
  or minimal, `D` = undetermined), `zone_subtype`, `sfha` (Special Flood Hazard Area, 1% annual chance), and base
  flood elevation / depth when FEMA has them.
- `flood_zone_loads`: when each site was loaded, the radius used, and how many zones were found. A site with a
  load row and zero zones has no FEMA map coverage.
- `site_flood_risk` (view): per site, the zone at the site point, whether the site is in an SFHA, the
  distance in miles to the nearest SFHA, and whether FEMA has mapped the area. Useful for the flood alert and
  the hazard model: `SELECT * FROM site_flood_risk WHERE site_in_sfha OR nearest_sfha_mi < 0.5;`

  | `fema_mapped` | `site_in_sfha` | Meaning |
  | --- | --- | --- |
  | `null` | `null` | Flood zones not loaded yet; run the loader |
  | `false` | `null` | FEMA has no flood map here (common in rural Alaska); risk is unknown, not low |
  | `true` | `true` / `false` | Site is / isn't in a 1% annual chance flood zone |

  `nearest_sfha_mi` is null when there is no SFHA within the radius *or* no map; check `fema_mapped` to tell
  them apart.

## Next-day briefings (6 PM)

Every evening at 6 PM in each site's own timezone, the API builds a next-day safety briefing per site and
POSTs it as JSON to `BRIEFING_WEBHOOK_URL`. Each briefing combines:

- **Open-Meteo**: current conditions and tomorrow's forecast (max temperature and feels-like, rain total and
  probability, max gusts, weather code).
- **National Weather Service** ([api.weather.gov](https://www.weather.gov/documentation/services-web-api)):
  tomorrow's forecast periods with the forecaster's text, plus any active NWS alerts (flood warnings, heat
  advisories, severe thunderstorm warnings).
- **FEMA flood zones**: the site's `site_flood_risk` row.

If one source is down, the briefing still goes out without it, and `source_errors` says which source failed and why.

**Turning it on.** Set these in `.env` (or the host's environment) on the one instance that should send:

| Variable | Default | Purpose |
| --- | --- | --- |
| `BRIEFINGS_ENABLED` | `false` | Runs the scheduler in the API process |
| `BRIEFING_WEBHOOK_URL` | unset | Destination for the JSON POST; unset = stored but not sent (`skipped`) |
| `BRIEFING_WEBHOOK_TOKEN` | unset | Sent as `Authorization: Bearer <token>` |
| `BRIEFING_HOUR` | `18` | Site-local hour briefings go out |
| `BRIEFING_CHECK_INTERVAL_S` | `300` | How often the scheduler checks which sites are due |

Keep it off on dev machines. If several instances do run it, each briefing is still sent only once (the database
enforces one scheduled briefing per site per day). But a dev machine without the webhook URL could claim a
briefing and mark it `skipped`, and then it never reaches the real webhook.

**How scheduling works.** Every check, any site whose local time is past 6 PM and has no scheduled briefing for
tomorrow gets one. Briefings missed while the server was down are caught up until local midnight. A failed send
(webhook down or erroring) is retried on later checks, up to 3 attempts.

**Sending right away.** `POST /briefings/run` builds and sends briefings immediately, regardless of the time:

```bash
curl -X POST localhost:8000/briefings/run                     # every site
curl -X POST localhost:8000/briefings/run -H 'content-type: application/json'      -d '{"site_ids": ["1"], "include_payload": true}'       # one site, return the JSON too
```

These are recorded as `manual` and don't replace the scheduled 6 PM briefing. It works even when
`BRIEFINGS_ENABLED` is false.

**History.** Every briefing is stored in the `briefings` table with its full payload, `trigger`
(`scheduled`/`manual`), `status` (`sent`/`failed`/`skipped`), attempts and last error.

## Everyday commands

| Task | Command |
| --- | --- |
| Add a package | `uv add <package>` (commit `pyproject.toml` and `uv.lock`) |
| Add a dev-only package | `uv add --dev <package>` |
| Sync after pulling | `uv sync` |
| Apply database migrations | `uv run python -m database.migrate` |
| Load FEMA flood zones | `uv run python -m scripts.load_flood_zones` |
| Record current conditions | `uv run python -m scripts.record_conditions` |
| Run tests | `uv run pytest` |
| Lint / format | `uv run ruff check .` / `uv run ruff format .` |

Never `pip install` into the project; always use `uv add` so `uv.lock` stays the single source of truth.

## Database

Everyone shares one Supabase database, so schema changes go through migration files instead of being made by
hand in the dashboard.

- `database/migrations/*.sql` are applied in filename order by `uv run python -m database.migrate`. Each file runs
  once, in a transaction, and is recorded in `public.schema_migrations`.
- To change the schema, add a new file with the next number (e.g. `0006_add_wildfire_table.sql`), run the migrate
  command, and commit the file. Never edit a migration that has already been applied; add a new one instead.
- `0001_enable_postgis.sql` enables PostGIS in Supabase's `extensions` schema.
- `0002_create_core_tables.sql` creates the core tables, with spatial indexes on the `geom` columns:

| Table | Holds | Written by |
| --- | --- | --- |
| `sites` | Job sites from EIA-860M (location, timezone, near-river flag) | seed script |
| `conditions` | Weather observations per site and time | worker |
| `hazard_scores` | ML hazard index (0-10) per site per day, today and forecast | model |
| `alerts` | Active (`cleared_at` null) and past alerts | worker, demo endpoint |
| `hazard_reports` | Crew-pinned hazards with optional photo | frontend, via the API |

- `0003_create_flood_zones.sql` adds `flood_zones`, `flood_zone_loads` and the `site_flood_risk` view (see
  [Flood zones](#flood-zones-fema-nfhl)).
- `0004_flood_risk_unknown_when_unmapped.sql` makes `site_flood_risk` report unknown (null) instead of
  "not in SFHA" for unmapped or unloaded sites, and adds `fema_mapped`.
- `0005_create_briefings.sql` adds the `briefings` table (see [Next-day briefings](#next-day-briefings-6-pm)).
- `0006_document_soil_saturation.sql` documents `conditions.soil_moisture` as a 0-1 saturation fraction (see
  [Conditions worker](#conditions-worker)).

Row Level Security is on for every table with no policies, so Supabase's public REST API (the anon key) can't
read or write them. The backend connects as the database owner and isn't affected. If the frontend should talk
to Supabase directly, add RLS policies in a new migration.

ORM models for these tables live in `app/models.py`. The SQL migrations are the source of truth: change the schema
in a new migration first, then update the matching model. Spatial columns use `geoalchemy2.Geometry` with
SRID 4326 (longitude/latitude).

### Creating the Supabase project (one person, once)

1. Sign up at supabase.com and create a project on the free plan. Save the database password.
2. Share the session pooler connection string with the team (privately, not in git).
3. Run `uv run python -m database.migrate` once to create the tables.

Free-tier projects pause after about a week without activity; restore them from the dashboard.

## Layout

```
backend/
├── app/
│   ├── main.py        # FastAPI app, CORS, router registration
│   ├── config.py      # settings loaded from .env
│   ├── db.py          # async SQLAlchemy engine, Base, SessionDep
│   ├── models.py      # ORM models matching the migrations
│   ├── http.py        # shared outbound HTTP client
│   ├── services/      # data clients (open_meteo, nws, fema_nfhl), briefings, conditions worker
│   └── routers/       # one module per API area
├── database/
│   ├── migrate.py     # applies migrations/ to DATABASE_URL
│   └── migrations/    # numbered SQL files, applied in order
├── scripts/
│   ├── load_flood_zones.py  # FEMA flood zones -> PostGIS
│   └── record_conditions.py # current weather -> conditions table
├── tests/
├── pyproject.toml     # dependencies (managed by uv)
└── uv.lock            # exact versions, committed
```
