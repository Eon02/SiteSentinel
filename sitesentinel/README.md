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
```

## Pages and API

| Path | What |
| --- | --- |
| `/` | Dashboard: map, ranked list, summary strip |
| `/site/[id]` | Site detail: scores, factors, conditions, alerts, crew reports |
| `/report?site=<id>` | Crew hazard report (photo, GPS, draggable pin) |
| `/demo` | Lightning strike, demo speed, clear alerts, briefing placeholder |
| `GET /api/sites`, `GET /api/sites/[id]`, `GET /api/alerts` | Read APIs |
| `GET /api/reports`, `POST /api/reports` | Recent crew reports for the map; create a report (multipart) |
| `POST /api/demo/strike`, `POST /api/demo/clear`, `POST /api/alerts/[id]/clear` | Demo and alert controls |

All data access goes through `lib/data.ts`. Cache Components is enabled, so route handlers don't export
`dynamic`/`runtime`; `lib/data.ts` calls `connection()` to keep every response uncached.
