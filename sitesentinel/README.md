# SiteSentinel frontend

Next.js 16 app built from [`SiteSentinelSpec.md`](./SiteSentinelSpec.md): a supervisor dashboard (map + ranked sites),
site detail pages, a phone-first crew hazard report form, and a `/demo` control panel.

## Run

```bash
npm install
npm run dev        # http://localhost:3000 (mock mode by default)
npm run build      # production build
```

`predev`/`prebuild` copy MapLibre's worker into `public/` (MapLibre v6 can't find it once bundled).

## Environment (`.env.local`)

```bash
USE_MOCK=true                 # default; anything but "false" uses data/mock/*.json (writes live in memory)
DATABASE_URL=postgres://...   # used when USE_MOCK=false
BLOB_READ_WRITE_TOKEN=...     # optional; without it, photos are stored as data URLs
BACKEND_URL=http://localhost:8000   # FastAPI backend, used to text crews (default shown)
```

## Assigned crew

Each site page has an **Assigned crew** section listing the workers attached to the site (the backend's
`site_workers` table: name, role, phone, with tap-to-call). Flood warning texts go to these numbers.

**Manage crew** opens a panel to add a crew member (name, phone, optional role) and shows **Remove from site** on each
card. A new crew member is saved in IndexedDB on the device (`lib/crewStore.ts`) and sent to
`POST /api/sites/[id]/crew`; without signal it waits on the device, marked "syncs when there's signal", and syncs
automatically like crew reports. A synced copy stays on the device. Phone numbers accept common US formats (or `+`
international) and are stored as E.164 (`lib/phone.ts`); a number already on the site isn't added twice. Removing
someone (`DELETE /api/sites/[id]/crew/[crewId]`) needs signal. Mock mode has sample crew in `data/mock/crew.json`.

**Force SMS Update** texts every crew member assigned to the site right away, after a confirmation.
`POST /api/sites/[id]/sms` forwards it to the backend (`POST /sites/{id}/sms`, which uses Twilio), so Twilio credentials
stay on the server; set `BACKEND_URL` in `.env.local` if the backend isn't at `http://localhost:8000`. It should
eventually send a proper status message for the site (TODO in `components/CrewSection.tsx`); for now it sends a generic
status line, and on the Twilio free trial (`TWILIO_BODY_TEMPLATE` set in the backend) every text is Twilio's predefined
template. Each send costs Twilio credit per crew member and needs signal. Mock mode sends nothing.

## Pages and API

| Path | What |
| --- | --- |
| `/` | Dashboard: map, ranked list, summary strip |
| `/site/[id]` | Site detail: scores, factors, conditions, alerts, crew reports |
| `/report?site=<id>` | Crew hazard report (photo, GPS, draggable pin) |
| `/demo` | Lightning strike, demo speed, clear alerts, briefing placeholder |
| `GET /api/sites`, `GET /api/sites/[id]`, `GET /api/alerts` | Read APIs |
| `GET /api/reports`, `POST /api/reports` | Recent crew reports for the map; create a report (multipart) |
| `POST /api/sites/[id]/crew`, `DELETE /api/sites/[id]/crew/[crewId]` | Add / remove a site's assigned crew |
| `POST /api/sites/[id]/sms` | Text a message to the site's crew (via the backend) |
| `POST /api/demo/strike`, `POST /api/demo/clear`, `POST /api/alerts/[id]/clear` | Demo and alert controls |

All data access goes through `lib/data.ts`. Cache Components is enabled, so route handlers don't export
`dynamic`/`runtime`; `lib/data.ts` calls `connection()` to keep every response uncached.

## Offline use (PWA)

The app installs to a phone's home screen (`app/manifest.ts`, icons in `public/icons/`) and works without signal.
The service worker (`public/sw.js`) is registered only in production builds (`npm run build && npm start`, or the
deployed site); `npm run dev` skips it so cached code never gets in the way while developing.

- **App code** is cached in full on first visit: after `next build`, `scripts/sw-manifest.mjs` (npm `postbuild`) lists
  every file of the build in `public/sw-manifest.json` (git-ignored, about 7 MB), and the worker caches all of it,
  including chunks loaded only on demand such as the report form's map. It re-checks on every app load, so a new
  deploy is picked up and the old build's files are dropped.
- **Pages:** `/`, `/report` and `/offline` are cached right away, so crews can report hazards offline even if they
  never opened the form in signal; other pages are cached as they're visited. A page never opened shows `/offline`.
- **Data** (`GET /api/*`) comes from the network when possible (after 6 s on a weak signal it falls back) and
  otherwise from the last saved copy. Those responses carry `x-sitesentinel-offline: 1`; `usePolling` then reports
  `offline: true` and the existing "Offline, retrying…" notes show. Everything refetches as soon as the connection
  returns.
- **Map tiles and photos** already viewed are cached (capped at 2000 tiles and 200 images). Areas never viewed show
  a plain map background offline; pins still work.
- **Crew reports** go through `lib/reportQueue.ts`: each report (photo included) is saved in IndexedDB first, then
  uploaded. Without signal the form says "Report saved", a strip under the header shows how many are waiting, and
  they upload automatically when the connection returns, when the app is reopened, every 20 s while any wait, and on
  Chrome/Android through Background Sync even if the app is closed. A report the server rejects (e.g. invalid input)
  stays on the device with Retry and Discard buttons.
- **No duplicates:** every report carries a `clientId` UUID. `POST /api/reports` returns the existing report for a
  `clientId` it has already saved (HTTP 200, photo not re-uploaded), so a retry after a lost response is harmless. In
  Postgres mode this uses `hazard_reports.client_id` (backend migration 0010).

Service workers need HTTPS or `localhost`, so phones opening the dev server by LAN IP (`http://192.168...`) get the
normal online app without offline support. Test offline behavior on the deployed HTTPS site, or locally in Chrome
DevTools (Application > Service workers, Network > Offline). After changing the caching rules in `public/sw.js`, bump
its `VERSION` so old caches are cleared.
