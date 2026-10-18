// ALL data access lives here. Pages and API routes never touch db.ts or the mock JSON directly.
// USE_MOCK=true reads the JSON in data/mock (writes stay in server memory); otherwise Postgres.
import { connection } from 'next/server';
import { getSql } from './db';
import type { Alert, AlertType, Conditions, CrewMember, HazardFactor, HazardReport, NewReport, Site, SiteDetail } from './types';
import mockSites from '@/data/mock/sites.json';
import mockAlerts from '@/data/mock/alerts.json';
import mockReports from '@/data/mock/reports.json';
import mockCrew from '@/data/mock/crew.json';

const isMock = () => process.env.USE_MOCK !== 'false';
export const usingMockData = isMock;

const LIGHTNING_MESSAGE = 'Lightning within 10 miles. Stop work.';

// ---------- Mock store ----------

type MockSite = Omit<Site, 'activeAlertCount' | 'forecastTopFactors'> & {
  forecastTopFactors?: HazardFactor[];
  conditions?: Conditions;
};
type MockAlert = Omit<Alert, 'siteName'>;
type MockCrew = CrewMember & { siteId: string };
type MockStore = { sites: MockSite[]; alerts: MockAlert[]; reports: HazardReport[]; crew: MockCrew[] };

// Kept on globalThis so every route in the same server process shares one copy.
const globalForMock = globalThis as unknown as { mockStore?: MockStore };

function store(): MockStore {
  if (!globalForMock.mockStore) {
    globalForMock.mockStore = structuredClone({
      sites: mockSites as unknown as MockSite[],
      alerts: mockAlerts as unknown as MockAlert[],
      reports: mockReports as unknown as HazardReport[],
      crew: mockCrew as MockCrew[],
    });
  }
  return globalForMock.mockStore;
}

function mockSiteName(siteId: string) {
  return store().sites.find((s) => s.id === siteId)?.name ?? siteId;
}

function mockToSite({ conditions: _conditions, ...s }: MockSite): Site {
  void _conditions;
  const activeAlertCount = store().alerts.filter((a) => a.siteId === s.id && !a.clearedAt).length;
  return { ...s, forecastTopFactors: s.forecastTopFactors ?? [], activeAlertCount };
}

function mockToAlert(a: MockAlert): Alert {
  return { ...a, siteName: mockSiteName(a.siteId) };
}

const newestFirst = <T extends { startedAt?: string; createdAt?: string }>(a: T, b: T) =>
  (b.startedAt ?? b.createdAt ?? '').localeCompare(a.startedAt ?? a.createdAt ?? '');

// ---------- Postgres row mapping ----------

type Row = Record<string, unknown>;

const iso = (v: unknown) => (v instanceof Date ? v.toISOString() : v == null ? undefined : String(v));
const num = (v: unknown) => (v == null ? undefined : Number(v));
const str = (v: unknown) => (v == null ? undefined : String(v));

function rowToSite(r: Row): Site {
  return {
    id: String(r.id),
    name: String(r.name),
    utility: str(r.utility),
    plantType: str(r.plant_type),
    county: str(r.county),
    state: str(r.state),
    timezone: String(r.timezone),
    nearRiver: Boolean(r.near_river),
    lat: Number(r.lat),
    lon: Number(r.lon),
    score: Number(r.score ?? 0),
    forecastScore: num(r.forecast_score),
    topFactors: (r.top_factors as HazardFactor[] | null) ?? [],
    forecastTopFactors: (r.forecast_top_factors as HazardFactor[] | null) ?? [],
    activeAlertCount: Number(r.active_alert_count ?? 0),
  };
}

function rowToAlert(r: Row): Alert {
  return {
    id: Number(r.id),
    siteId: String(r.site_id),
    siteName: String(r.site_name ?? r.site_id),
    type: r.type as AlertType,
    severity: r.severity as Alert['severity'],
    message: String(r.message),
    startedAt: iso(r.started_at)!,
    lastStrikeAt: iso(r.last_strike_at),
    clearedAt: iso(r.cleared_at),
  };
}

function rowToReport(r: Row): HazardReport {
  return {
    id: Number(r.id),
    siteId: String(r.site_id),
    hazardType: String(r.hazard_type),
    description: str(r.description),
    photoUrl: str(r.photo_url),
    reportedBy: str(r.reported_by),
    lat: Number(r.lat),
    lon: Number(r.lon),
    createdAt: iso(r.created_at)!,
  };
}

function rowToCrew(r: Row): CrewMember {
  return { id: Number(r.id), phone: String(r.phone), name: str(r.name), role: str(r.role) };
}

// Supervisors first, then crew leads, then everyone else; named people before unnamed.
const ROLE_ORDER = ['supervisor', 'crew lead', 'foreman'];
function byRole(a: CrewMember, b: CrewMember): number {
  const rank = (c: CrewMember) => {
    const i = ROLE_ORDER.indexOf((c.role ?? '').toLowerCase());
    return i === -1 ? ROLE_ORDER.length : i;
  };
  return rank(a) - rank(b) || (a.name ? 0 : 1) - (b.name ? 0 : 1) || (a.name ?? '').localeCompare(b.name ?? '') || a.id - b.id;
}

function rowToConditions(r: Row): Conditions {
  return {
    observedAt: iso(r.observed_at)!,
    tempF: num(r.temp_f),
    heatIndexF: num(r.heat_index_f),
    precip24hIn: num(r.precip_24h_in),
    soilMoisture: num(r.soil_moisture),
    windMph: num(r.wind_mph),
  };
}

async function selectSites(siteId?: string): Promise<Site[]> {
  const sql = getSql();
  const rows = await sql`
    SELECT s.id, s.name, s.utility, s.plant_type, s.county, s.state,
           s.timezone, s.near_river,
           ST_Y(s.geom) AS lat, ST_X(s.geom) AS lon,
           COALESCE(t.score, 0) AS score, t.top_factors,
           f.score AS forecast_score, f.top_factors AS forecast_top_factors,
           (SELECT count(*) FROM alerts a
             WHERE a.site_id = s.id AND a.cleared_at IS NULL)::int AS active_alert_count
    FROM sites s
    LEFT JOIN hazard_scores t ON t.site_id = s.id AND t.score_date = current_date
    LEFT JOIN hazard_scores f ON f.site_id = s.id AND f.score_date = current_date + 1
    ${siteId ? sql`WHERE s.id = ${siteId}` : sql``}
    ORDER BY score DESC`;
  return rows.map(rowToSite);
}

const ALERT_COLUMNS = 'a.id, a.site_id, s.name AS site_name, a.type, a.severity, a.message, a.started_at, a.last_strike_at, a.cleared_at';

// ---------- Public API ----------

export async function getSites(): Promise<Site[]> {
  await connection();
  if (isMock()) {
    return store().sites.map(mockToSite).sort((a, b) => b.score - a.score);
  }
  return selectSites();
}

export async function getSite(id: string): Promise<SiteDetail | null> {
  await connection();
  if (isMock()) {
    const s = store().sites.find((x) => x.id === id);
    if (!s) return null;
    return {
      site: mockToSite(s),
      conditions: s.conditions,
      alerts: store().alerts.filter((a) => a.siteId === id && !a.clearedAt).map(mockToAlert).sort(newestFirst),
      reports: store().reports.filter((r) => r.siteId === id).sort(newestFirst),
      crew: store().crew
        .filter((c) => c.siteId === id)
        .map((c) => ({ id: c.id, phone: c.phone, name: c.name, role: c.role }))
        .sort(byRole),
    };
  }

  const sql = getSql();
  const [site] = await selectSites(id);
  if (!site) return null;
  const [conditionRows, alertRows, reportRows, crewRows] = await Promise.all([
    sql`SELECT * FROM conditions WHERE site_id = ${id} ORDER BY observed_at DESC LIMIT 1`,
    sql`SELECT ${sql.unsafe(ALERT_COLUMNS)} FROM alerts a JOIN sites s ON s.id = a.site_id
        WHERE a.site_id = ${id} AND a.cleared_at IS NULL ORDER BY a.started_at DESC`,
    sql`SELECT id, site_id, hazard_type, description, photo_url, reported_by,
               ST_Y(geom) AS lat, ST_X(geom) AS lon, created_at
        FROM hazard_reports WHERE site_id = ${id} ORDER BY created_at DESC LIMIT 50`,
    sql`SELECT id, name, role, phone FROM site_workers WHERE site_id = ${id}`,
  ]);
  return {
    site,
    conditions: conditionRows[0] ? rowToConditions(conditionRows[0]) : undefined,
    alerts: alertRows.map(rowToAlert),
    reports: reportRows.map(rowToReport),
    crew: crewRows.map(rowToCrew).sort(byRole),
  };
}

export async function getActiveAlerts(): Promise<Alert[]> {
  await connection();
  if (isMock()) {
    return store().alerts.filter((a) => !a.clearedAt).map(mockToAlert).sort(newestFirst);
  }
  const sql = getSql();
  const rows = await sql`
    SELECT ${sql.unsafe(ALERT_COLUMNS)} FROM alerts a JOIN sites s ON s.id = a.site_id
    WHERE a.cleared_at IS NULL ORDER BY a.started_at DESC`;
  return rows.map(rowToAlert);
}

// Recent crew reports across all sites, for the dashboard map.
export async function getRecentReports(): Promise<HazardReport[]> {
  await connection();
  if (isMock()) {
    return [...store().reports].sort(newestFirst);
  }
  const sql = getSql();
  const rows = await sql`
    SELECT id, site_id, hazard_type, description, photo_url, reported_by,
           ST_Y(geom) AS lat, ST_X(geom) AS lon, created_at
    FROM hazard_reports WHERE created_at > now() - interval '7 days'
    ORDER BY created_at DESC LIMIT 200`;
  return rows.map(rowToReport);
}

export async function siteExists(id: string): Promise<boolean> {
  if (isMock()) return store().sites.some((s) => s.id === id);
  const sql = getSql();
  const rows = await sql`SELECT 1 FROM sites WHERE id = ${id}`;
  return rows.length > 0;
}

const REPORT_COLUMNS = `id, site_id, hazard_type, description, photo_url, reported_by,
  ST_Y(geom) AS lat, ST_X(geom) AS lon, created_at`;

// A report already saved under this device-generated id (an offline-queue retry), if any.
export async function findReportByClientId(clientId: string): Promise<HazardReport | null> {
  await connection();
  if (isMock()) {
    const hit = store().reports.find((r) => (r as HazardReport & { clientId?: string }).clientId === clientId);
    return hit ?? null;
  }
  const sql = getSql();
  const [row] = await sql`
    SELECT ${sql.unsafe(REPORT_COLUMNS)} FROM hazard_reports WHERE client_id = ${clientId}::uuid`;
  return row ? rowToReport(row) : null;
}

// Saves a report. With a clientId, saving the same report twice returns the first one instead
// of creating a duplicate (the offline queue retries after dropped connections).
export async function createReport(input: NewReport): Promise<HazardReport> {
  await connection();
  if (isMock()) {
    if (input.clientId) {
      const existing = await findReportByClientId(input.clientId);
      if (existing) return existing;
    }
    const reports = store().reports;
    const report: HazardReport = {
      ...input,
      id: Math.max(0, ...reports.map((r) => r.id)) + 1,
      createdAt: new Date().toISOString(),
    };
    reports.push(report);
    return report;
  }
  const sql = getSql();
  const [row] = await sql`
    INSERT INTO hazard_reports (site_id, hazard_type, description, photo_url, reported_by, geom, client_id)
    VALUES (${input.siteId}, ${input.hazardType}, ${input.description ?? null}, ${input.photoUrl ?? null},
            ${input.reportedBy ?? null}, ST_SetSRID(ST_MakePoint(${input.lon}, ${input.lat}), 4326),
            ${input.clientId ?? null}::uuid)
    ON CONFLICT (client_id) DO NOTHING
    RETURNING ${sql.unsafe(REPORT_COLUMNS)}`;
  if (row) return rowToReport(row);
  return (await findReportByClientId(input.clientId!))!; // lost a race with the same clientId
}

// Demo: create a lightning stop-work alert, or restart the clock on the active one.
export async function recordStrike(siteId: string): Promise<Alert> {
  await connection();
  const now = new Date().toISOString();
  if (isMock()) {
    const alerts = store().alerts;
    let alert = alerts.find((a) => a.siteId === siteId && a.type === 'lightning' && !a.clearedAt);
    if (alert) {
      alert.lastStrikeAt = now;
    } else {
      alert = {
        id: Math.max(0, ...alerts.map((a) => a.id)) + 1,
        siteId,
        type: 'lightning',
        severity: 'stop_work',
        message: LIGHTNING_MESSAGE,
        startedAt: now,
        lastStrikeAt: now,
      };
      alerts.push(alert);
    }
    return mockToAlert(alert);
  }

  const sql = getSql();
  const updated = await sql`
    UPDATE alerts SET last_strike_at = now()
    WHERE id = (SELECT id FROM alerts WHERE site_id = ${siteId} AND type = 'lightning'
                AND cleared_at IS NULL ORDER BY started_at DESC LIMIT 1)
    RETURNING id`;
  const id = updated[0]?.id ?? (await sql`
    INSERT INTO alerts (site_id, type, severity, message, last_strike_at)
    VALUES (${siteId}, 'lightning', 'stop_work', ${LIGHTNING_MESSAGE}, now())
    RETURNING id`)[0].id;
  const [row] = await sql`
    SELECT ${sql.unsafe(ALERT_COLUMNS)} FROM alerts a JOIN sites s ON s.id = a.site_id WHERE a.id = ${id}`;
  return rowToAlert(row);
}

export async function clearAlert(id: number): Promise<void> {
  await connection();
  if (isMock()) {
    const alert = store().alerts.find((a) => a.id === id);
    if (alert && !alert.clearedAt) alert.clearedAt = new Date().toISOString();
    return;
  }
  const sql = getSql();
  await sql`UPDATE alerts SET cleared_at = now() WHERE id = ${id} AND cleared_at IS NULL`;
}

// Demo reset between rehearsals. Mock mode restores the seed alerts; Postgres clears every active alert
// (the worker re-raises any weather alerts that still apply).
export async function clearAllAlerts(): Promise<void> {
  await connection();
  if (isMock()) {
    store().alerts = structuredClone(mockAlerts as unknown as MockAlert[]);
    return;
  }
  const sql = getSql();
  await sql`UPDATE alerts SET cleared_at = now() WHERE cleared_at IS NULL`;
}

// --- crew (backend table site_workers) ---------------------------------------------------

export type NewCrewMember = { phone: string; name?: string; role?: string };

// Adds a crew member to a site. A phone already on the site returns that member instead of a
// duplicate, so an offline-queued add that is retried is harmless. `created` says which.
export async function addCrewMember(
  siteId: string,
  input: NewCrewMember,
): Promise<{ member: CrewMember; created: boolean }> {
  await connection();
  if (isMock()) {
    const crew = store().crew;
    const existing = crew.find((c) => c.siteId === siteId && c.phone === input.phone);
    if (existing) return { member: { id: existing.id, phone: existing.phone, name: existing.name, role: existing.role }, created: false };
    const member = { ...input, id: Math.max(0, ...crew.map((c) => c.id)) + 1 };
    crew.push({ ...member, siteId });
    return { member, created: true };
  }
  const sql = getSql();
  const [row] = await sql`
    INSERT INTO site_workers (site_id, phone, name, role)
    VALUES (${siteId}, ${input.phone}, ${input.name ?? null}, ${input.role ?? null})
    ON CONFLICT (site_id, phone) WHERE phone IS NOT NULL DO NOTHING
    RETURNING id, name, role, phone`;
  if (row) return { member: rowToCrew(row), created: true };
  const [existing] = await sql`
    SELECT id, name, role, phone FROM site_workers WHERE site_id = ${siteId} AND phone = ${input.phone}`;
  return { member: rowToCrew(existing), created: false };
}

// Removes a crew member from a site. Returns false if they weren't on it.
export async function removeCrewMember(siteId: string, crewId: number): Promise<boolean> {
  await connection();
  if (isMock()) {
    const crew = store().crew;
    const i = crew.findIndex((c) => c.siteId === siteId && c.id === crewId);
    if (i === -1) return false;
    crew.splice(i, 1);
    return true;
  }
  const sql = getSql();
  const rows = await sql`DELETE FROM site_workers WHERE site_id = ${siteId} AND id = ${crewId} RETURNING id`;
  return rows.length > 0;
}

// Just a site's coordinates and timezone (for the weather forecast); null if it doesn't exist.
export async function getSiteLocation(id: string): Promise<{ lat: number; lon: number; timezone: string } | null> {
  await connection();
  if (isMock()) {
    const s = store().sites.find((x) => x.id === id);
    return s ? { lat: s.lat, lon: s.lon, timezone: s.timezone } : null;
  }
  const sql = getSql();
  const [row] = await sql`SELECT ST_Y(geom) AS lat, ST_X(geom) AS lon, timezone FROM sites WHERE id = ${id}`;
  return row ? { lat: Number(row.lat), lon: Number(row.lon), timezone: String(row.timezone) } : null;
}
