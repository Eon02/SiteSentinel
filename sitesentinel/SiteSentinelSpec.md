# SiteSentinel Frontend Spec

Oct 17, 2026 · @Haley

## 1. Overview

Build the web frontend for SiteSentinel: a Next.js app that shows every utility job site on a map ranked by hazard level, raises live safety alerts, and lets crew members report hazards with a photo from their phone. It must work in mock mode first, then switch to the real Postgres/PostGIS database with a single environment variable.

**Users**

- **Supervisors** on a laptop: see which sites need attention first and why.
- **Crew members** on a phone in the field: see their site's status and report hazards.

**Constraints**

- 24-hour hackathon, one frontend developer. Favor simple, working code over abstraction.
- The backend team owns the database and a Python worker that writes weather conditions, alerts, and ML hazard scores into Postgres. The frontend only reads those tables, plus writes crew hazard reports.
- The database schema below is an assumption. Field names may change; keep al
- l database access in one file so changes are cheap.

**How to use this spec with Claude Code**

Save this spec as `SPEC.md` in the repo root. Ask Claude Code to build one milestone at a time from section 8, and to run `npm run build` after each one. Do not ask for the whole app in one prompt.

## 2. Tech stack and setup

| Concern | Choice | Notes |
| --- | --- | --- |
| Framework | Next.js (App Router) + TypeScript | API routes live in `app/api/*/route.ts` |
| Styling | Tailwind CSS | No component library needed |
| Map | `maplibre-gl` + `react-map-gl` | Tiles: `https://tiles.openfreemap.org/styles/liberty` (no key) |
| Database client | `postgres` (postgres.js) | Server-side only |
| Validation | `zod` | Validate the hazard report form and API input |
| Photo storage | `@vercel/blob` | Save only the returned URL in Postgres |
| Icons | `lucide-react` | Optional |
| Hosting | Vercel | Deploy early; camera and location need HTTPS |

**Setup commands**

```bash
npx create-next-app@latest sitesentinel --typescript --tailwind --app --eslint
cd sitesentinel
npm install maplibre-gl react-map-gl postgres zod @vercel/blob lucide-react
```

**Environment variables** (`.env.local`, never prefixed with `NEXT_PUBLIC_`)

```bash
USE_MOCK=true                 # true = read/write JSON mock data, false = Postgres
DATABASE_URL=postgres://...   # from the backend team
BLOB_READ_WRITE_TOKEN=...     # from Vercel Blob; when missing, photos are stored as data URLs in mock mode
```

**Folder structure**

```
app/
  page.tsx                  # supervisor dashboard
  site/[id]/page.tsx        # site detail
  report/page.tsx           # crew hazard report (mobile)
  demo/page.tsx             # demo control panel
  api/sites/route.ts
  api/sites/[id]/route.ts
  api/alerts/route.ts
  api/reports/route.ts
  api/demo/strike/route.ts
components/
  SiteMap.tsx  SiteList.tsx  RiskBadge.tsx  AlertBanner.tsx
  LightningCountdown.tsx  FactorBars.tsx  ReportForm.tsx
lib/
  types.ts                  # shared TypeScript types
  db.ts                     # postgres.js client
  data.ts                   # ALL data access; switches on USE_MOCK
  risk.ts                   # score -> color/label helpers
  usePolling.ts             # client polling hook
data/mock/
  sites.json  alerts.json  reports.json
```

## 3. Data model

These tables are the agreed contract with the backend. **Confirm the names with the backend team before switching off mock mode.** All geometry is PostGIS `geometry(Point, 4326)` (longitude, latitude).

```sql
-- Job sites from EIA-860M (50+ rows)
CREATE TABLE sites (
  id           text PRIMARY KEY,        -- EIA plant code as text
  name         text NOT NULL,
  utility      text,
  plant_type   text,                    -- e.g. 'Solar', 'Natural Gas'
  county       text,
  state        text,
  timezone     text NOT NULL,           -- IANA, e.g. 'America/New_York'
  near_river   boolean DEFAULT false,
  geom         geometry(Point, 4326) NOT NULL
);

-- Latest weather per site, written by the worker
CREATE TABLE conditions (
  site_id        text REFERENCES sites(id),
  observed_at    timestamptz NOT NULL,
  temp_f         real,
  heat_index_f   real,
  precip_24h_in  real,
  soil_moisture  real,                  -- 0 to 1
  wind_mph       real,
  PRIMARY KEY (site_id, observed_at)
);

-- ML hazard index, today and tomorrow (forecast)
CREATE TABLE hazard_scores (
  site_id      text REFERENCES sites(id),
  score_date   date NOT NULL,
  score        real NOT NULL,           -- 0 to 10
  is_forecast  boolean NOT NULL,
  top_factors  jsonb NOT NULL,          -- [{"factor":"heat","weight":0.4}, ...]
  PRIMARY KEY (site_id, score_date)
);

-- Active and past alerts, written by the worker and the demo endpoint
CREATE TABLE alerts (
  id              bigserial PRIMARY KEY,
  site_id         text REFERENCES sites(id),
  type            text NOT NULL,        -- 'lightning' | 'flood' | 'heat' | 'wildfire' | 'storm'
  severity        text NOT NULL,        -- 'warning' | 'stop_work'
  message         text NOT NULL,
  started_at      timestamptz NOT NULL DEFAULT now(),
  last_strike_at  timestamptz,          -- lightning only
  cleared_at      timestamptz           -- null = active
);

-- Crew hazard reports, written by the frontend
CREATE TABLE hazard_reports (
  id           bigserial PRIMARY KEY,
  site_id      text REFERENCES sites(id),
  hazard_type  text NOT NULL,           -- see list in section 6
  description  text,
  photo_url    text,
  reported_by  text,
  geom         geometry(Point, 4326) NOT NULL,
  created_at   timestamptz NOT NULL DEFAULT now()
);
```

**TypeScript types** (`lib/types.ts`)

```ts
export type HazardFactor = { factor: string; weight: number };

export type Site = {
  id: string; name: string; utility?: string; plantType?: string;
  county?: string; state?: string; timezone: string; nearRiver: boolean;
  lat: number; lon: number;
  score: number;               // today's score, 0-10
  forecastScore?: number;      // tomorrow's score
  topFactors: HazardFactor[];
  activeAlertCount: number;
};

export type Conditions = {
  observedAt: string; tempF?: number; heatIndexF?: number;
  precip24hIn?: number; soilMoisture?: number; windMph?: number;
};

export type AlertType = 'lightning' | 'flood' | 'heat' | 'wildfire' | 'storm';

export type Alert = {
  id: number; siteId: string; siteName: string; type: AlertType;
  severity: 'warning' | 'stop_work'; message: string;
  startedAt: string; lastStrikeAt?: string; clearedAt?: string;
};

export type HazardReport = {
  id: number; siteId: string; hazardType: string; description?: string;
  photoUrl?: string; reportedBy?: string; lat: number; lon: number; createdAt: string;
};
```

## 4. Mock mode and data access

Every page and API route gets data only through `lib/data.ts`. When `USE_MOCK=true`, it reads and writes the JSON files in `data/mock/`; otherwise it queries Postgres. Pages never import `db.ts` or the JSON files directly.

**Functions `lib/data.ts` must export**

```text
getSites(): Promise<Site[]>                          // sorted by score, highest first
getSite(id: string): Promise<{
  site: Site; conditions?: Conditions;
  alerts: Alert[]; reports: HazardReport[];
} | null>
getActiveAlerts(): Promise<Alert[]>                  // cleared_at is null
createReport(input: NewReport): Promise<HazardReport>
recordStrike(siteId: string): Promise<Alert>         // demo: create or extend a lightning alert
clearAlert(id: number): Promise<void>
```

**Mock data requirements**

- `sites.json`: 12 sites with real-looking coordinates across the U.S., including at least one site in the Florida Everglades with a score of 8 or higher and one Mississippi River site with `nearRiver: true`. Spread scores from 1 to 9 so all colors appear.
- `alerts.json`: one active heat warning and one active flood warning.
- `reports.json`: two reports, one of them an alligator sighting at the Everglades site.
- In mock mode, writes (new reports, strikes) may live in memory on the server. Losing them on restart is acceptable.

## 5. API routes

All routes return JSON, run on the Node runtime (`export const runtime = 'nodejs'`), and set `export const dynamic = 'force-dynamic'` so data is never cached.

| Method | Path | Returns | Used by |
| --- | --- | --- | --- |
| GET | `/api/sites` | `Site[]`, highest score first | Dashboard |
| GET | `/api/sites/[id]` | `{ site, conditions, alerts, reports }` or 404 | Site detail |
| GET | `/api/alerts` | Active `Alert[]`, newest first | Alert banner (polled) |
| POST | `/api/reports` | Created `HazardReport` | Report form |
| POST | `/api/demo/strike` | Updated lightning `Alert` | Demo panel |
| POST | `/api/alerts/[id]/clear` | `{ ok: true }` | Countdown reaching zero, demo panel |

**`POST /api/reports`** accepts `multipart/form-data` with fields `siteId`, `hazardType`, `description`, `reportedBy`, `lat`, `lon`, and an optional `photo` file (max 5 MB, images only). Validate with zod. Upload the photo to Vercel Blob, then insert the row with the photo URL.

**`POST /api/demo/strike`** takes `{ siteId }`. If the site has an active lightning alert, set `last_strike_at = now()`. Otherwise create one with `severity = 'stop_work'` and message `Lightning within 10 miles. Stop work.`

**Key SQL for `getSites()`**

```sql
SELECT s.id, s.name, s.utility, s.plant_type, s.county, s.state,
       s.timezone, s.near_river,
       ST_Y(s.geom) AS lat, ST_X(s.geom) AS lon,
       COALESCE(t.score, 0) AS score, t.top_factors,
       f.score AS forecast_score,
       (SELECT count(*) FROM alerts a
         WHERE a.site_id = s.id AND a.cleared_at IS NULL) AS active_alert_count
FROM sites s
LEFT JOIN hazard_scores t ON t.site_id = s.id AND t.score_date = current_date
LEFT JOIN hazard_scores f ON f.site_id = s.id AND f.score_date = current_date + 1
ORDER BY score DESC;
```

The map component converts `Site[]` into a GeoJSON `FeatureCollection` on the client, so the API stays plain JSON.

## 6. Pages and components

**Global layout**

- Header with "SiteSentinel" (navy `#16213E` and orange `#E8590C`, matching the challenge branding), links to Dashboard and Report Hazard, and the `AlertBanner`.
- `AlertBanner` polls `/api/alerts` and shows a red bar for each `stop_work` alert and an amber bar for each warning, with the site name. Clicking it opens that site. Lightning alerts include the `LightningCountdown`.

**Dashboard (`/`)**

- Desktop: map on the left (about 65% width), ranked site list on the right. Phone: map on top (50vh), list below.
- `SiteMap`: one circle per site, colored by score (section 7), radius 6 to 12 px growing with score. Sites with an active stop-work alert pulse. Hovering shows a popup with name, score, and top factor. Clicking selects the site and flies the map to it. Crew reports appear as small pins with an icon by hazard type.
- `SiteList`: rank number, site name, state, `RiskBadge` (score plus label), tomorrow's forecast score with an up or down arrow, and an alert icon when alerts are active. Includes a search box and a filter for "active alerts only". Clicking a row selects it on the map; a "View site" button opens the detail page.
- Summary strip above the list: number of sites, number at high risk (score 7 or above), number of active stop-work alerts.

**Site detail (`/site/[id]`)**

- Name, utility, plant type, county, state, and local time at the site.
- Large `RiskBadge` for today and tomorrow.
- `FactorBars`: horizontal bars for `topFactors`, labeled in plain words ("Heat", "Lightning", "Soil saturation").
- Current conditions cards: temperature, heat index, 24-hour rain, soil moisture, wind.
- Active alerts list, with the lightning countdown when present.
- Small map centered on the site with crew reports pinned, and a list of reports with photo thumbnails, newest first.
- Button: "Report a hazard here", linking to `/report?site=<id>`.

**Crew hazard report (`/report`)** — designed for phones first

1. Site picker, preselected from `?site=` or set to the nearest site from the phone's location.
2. Hazard type as large tap buttons: Wildlife, Mud / stuck vehicle, Standing water, Downed line, Heat illness, Fire / smoke, Other.
3. Photo: `<input type="file" accept="image/*" capture="environment">` with a preview. Resize to max 1600 px wide on the client before upload.
4. Location from `navigator.geolocation`, shown on a small map; the user can drag the pin. If location is denied, default to the site's coordinates.
5. Optional description and name.
6. Submit shows a success screen with "Report another" and "View site" buttons. Disable the button while uploading.

**Demo panel (`/demo`)** — for the live presentation

- Site dropdown and a "Lightning strike" button that calls `/api/demo/strike`.
- "Demo speed" toggle that makes the 30-minute countdown run 60x faster (30 seconds). Store it in `localStorage` (wrapped in try/catch) so all tabs in the same browser share it.
- "Clear all alerts" button to reset between rehearsals.
- "Send briefing now" button. It is a placeholder until the backend provides an endpoint; show a toast saying "Briefing sent".

## 7. Behavior rules

**Risk colors and labels** (`lib/risk.ts`; use everywhere)

| Score | Label | Color |
| --- | --- | --- |
| 0 to 2.9 | Low | Green `#2F9E44` |
| 3 to 5.9 | Moderate | Yellow `#F2C200` |
| 6 to 7.9 | High | Orange `#E8590C` |
| 8 to 10 | Extreme | Red `#C92A2A` |

Always show the number next to the color, so color is never the only signal. Round scores to one decimal place.

**Lightning countdown**

- Remaining time = 30 minutes minus (now minus `lastStrikeAt`). Recompute every second in the browser with `setInterval`; do not poll the server for it.
- Display `MM:SS` in large red text with the label "Stop work. All clear in".
- A new strike updates `lastStrikeAt` through polling, which restarts the countdown. Flash the banner briefly when that happens.
- At zero, show a green "All clear" state for 10 seconds and call `/api/alerts/[id]/clear`.
- With demo speed on, use 30 seconds instead of 30 minutes.

**Data refresh**

- `usePolling(url, intervalMs)` hook: fetch immediately, then every interval; pause when the tab is hidden.
- Alerts: every 5 seconds. Sites list and map: every 30 seconds. Site detail: every 15 seconds.
- After a successful report, refetch immediately instead of waiting.

**Mobile and accessibility**

- Works at 375 px wide with no horizontal scrolling. Tap targets at least 44 px tall.
- High contrast for outdoor use in sunlight: dark text on light backgrounds, no light gray text.
- Every icon button has an `aria-label`.

**Errors and empty states**

- If an API call fails, keep showing the last data with a small "Offline, retrying" note.
- Show skeleton placeholders while loading and friendly text when lists are empty ("No active alerts").

**Map gotcha**

- MapLibre needs the browser. Mark map components with `"use client"` and load them with `next/dynamic` using `{ ssr: false }`. Import `maplibre-gl/dist/maplibre-gl.css`.

## 8. Build order and acceptance criteria

Build one milestone per Claude Code prompt. Each milestone must pass `npm run build` and work in mock mode before starting the next.

1. **Scaffold** (hour 0 to 1): project, folders, `lib/types.ts`, `lib/risk.ts`, mock JSON files, `lib/data.ts` in mock mode, deployed to Vercel.
   - Done when the deployed URL loads and `/api/sites` returns the 12 mock sites.
2. **Dashboard map and list** (hours 1 to 4).
   - Done when all sites show as colored circles, the list is sorted by score, and clicking a row flies the map to that site.
3. **Site detail page** (hours 4 to 6).
   - Done when the Everglades site shows its score, factors, conditions, alerts, and the alligator report.
4. **Crew hazard report** (hours 6 to 9).
   - Done when a report submitted from a real phone, with a photo, appears on the dashboard map within 30 seconds.
5. **Alerts and lightning countdown** (hours 9 to 12).
   - Done when pressing "Lightning strike" on `/demo` shows a red banner on the dashboard within 5 seconds, a second press restarts the countdown, and with demo speed on it reaches "All clear" in 30 seconds.
6. **Switch to Postgres** (hour 12, with the backend team).
   - Done when `USE_MOCK=false` shows the real 50+ sites and live scores, and reports save to `hazard_reports`.
7. **Polish** (hours 16 to 20): loading states, empty states, phone layout check, header branding.

**Out of scope**

- User accounts and login. Use a free-text "Your name" field instead.
- Native push notifications, SMS, and offline mode.
- Editing or deleting reports, and admin screens beyond `/demo`.
- Generating the 6 PM briefing. The backend owns it; the frontend only has the demo button.

**Open questions for the backend team**

- [ ] Where is Postgres hosted, and is it reachable from Vercel?
- [ ] Are the table and column names in section 3 final?
- [ ] What exact keys will `top_factors` use?
- [ ] Will the backend provide a "send briefing now" endpoint?
