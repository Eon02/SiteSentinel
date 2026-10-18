'use client';

import { setOnline } from './connectivity';
import { newClientId, STORES, withStore } from './offlineDb';
import { normalizePhone } from './phone';
import { requestRefresh } from './usePolling';

// Crew members added from a site page. Each is saved in IndexedDB first, then sent to the
// server (POST /api/sites/[id]/crew, which writes the backend's site_workers table). Without
// signal it waits on the device and syncs automatically, like crew reports. Once synced, the
// device keeps a copy (status 'synced') alongside the server's.

const STORE = STORES.crew;
const LOCK = 'sitesentinel-crew';
const CHANGE_EVENT = 'sitesentinel:crew';
const CHANNEL = 'sitesentinel-crew';
const ADD_WAIT_MS = 8000;
const RETRY_INTERVAL_MS = 20_000;

export type LocalCrewMember = {
  clientId: string;
  siteId: string;
  phone: string; // E.164
  name?: string;
  role?: string;
  createdAt: number;
  status: 'pending' | 'synced' | 'failed'; // failed = the server rejected it
  serverId?: number;
  lastError?: string;
};

export type AddCrewResult =
  | { status: 'synced'; member: LocalCrewMember }
  | { status: 'pending'; member: LocalCrewMember } // saved on the device; syncs when there's signal
  | { status: 'rejected'; error: string };

const withCrew = <T,>(mode: IDBTransactionMode, fn: (store: IDBObjectStore) => IDBRequest<T> | void) =>
  withStore<T>(STORE, mode, fn);
const listAll = async () => (await withCrew<LocalCrewMember[]>('readonly', (s) => s.getAll())) ?? [];
const getOne = (clientId: string) =>
  withCrew<LocalCrewMember>('readonly', (s) => s.get(clientId) as IDBRequest<LocalCrewMember>);
const putOne = (m: LocalCrewMember) => withCrew('readwrite', (s) => void s.put(m));
const deleteOne = (clientId: string) => withCrew('readwrite', (s) => void s.delete(clientId));

export async function listLocalCrew(siteId: string): Promise<LocalCrewMember[]> {
  return (await listAll()).filter((m) => m.siteId === siteId).sort((a, b) => a.createdAt - b.createdAt);
}

function notifyChange() {
  window.dispatchEvent(new Event(CHANGE_EVENT));
  try {
    const bc = new BroadcastChannel(CHANNEL);
    bc.postMessage('changed');
    bc.close();
  } catch {
    // other tabs catch up on their next sync
  }
}

export function onCrewChange(listener: () => void): () => void {
  window.addEventListener(CHANGE_EVENT, listener);
  let bc: BroadcastChannel | undefined;
  try {
    bc = new BroadcastChannel(CHANNEL);
    bc.onmessage = listener;
  } catch {
    // ignore
  }
  return () => {
    window.removeEventListener(CHANGE_EVENT, listener);
    bc?.close();
  };
}

type Outcome =
  | { outcome: 'synced'; serverId: number }
  | { outcome: 'offline' }
  | { outcome: 'retry' }
  | { outcome: 'failed'; error: string };

async function send(m: LocalCrewMember): Promise<Outcome> {
  let res: Response;
  try {
    res = await fetch(`/api/sites/${encodeURIComponent(m.siteId)}/crew`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ phone: m.phone, name: m.name, role: m.role }),
    });
  } catch {
    return { outcome: 'offline' };
  }
  if (res.ok) return { outcome: 'synced', serverId: (await res.json()).id };
  if (res.status >= 500 || res.status === 408 || res.status === 429) return { outcome: 'retry' };
  const json = await res.json().catch(() => ({}));
  return { outcome: 'failed', error: json.error ?? `Rejected (HTTP ${res.status})` };
}

let inFlight: Promise<void> | null = null;

// Send every waiting crew member to the server. Stops at the first network failure.
export function syncCrew(): Promise<void> {
  const run = async () => {
    let synced = 0;
    for (const m of (await listAll()).sort((a, b) => a.createdAt - b.createdAt)) {
      if (m.status !== 'pending') continue;
      const result = await send(m);
      if (result.outcome === 'synced') {
        await putOne({ ...m, status: 'synced', serverId: result.serverId, lastError: undefined });
        synced++;
        setOnline(true);
      } else if (result.outcome === 'failed') {
        await putOne({ ...m, status: 'failed', lastError: result.error });
      } else {
        if (result.outcome === 'offline') setOnline(false);
        break;
      }
    }
    if (synced > 0) requestRefresh();
    notifyChange();
  };
  if (typeof navigator !== 'undefined' && navigator.locks) {
    return navigator.locks.request(LOCK, run).then(() => undefined);
  }
  inFlight ??= run().finally(() => {
    inFlight = null;
  });
  return inFlight;
}

// Save a new crew member on the device, then send it. Resolves within ~8 s on a weak signal.
export async function addCrew(
  siteId: string,
  input: { name?: string; role?: string; phone: string },
): Promise<AddCrewResult> {
  const phone = normalizePhone(input.phone);
  if (!phone) return { status: 'rejected', error: 'Enter a valid phone number, e.g. (205) 555-0123' };
  const duplicate = (await listLocalCrew(siteId)).find((m) => m.phone === phone && m.status !== 'failed');
  if (duplicate) return { status: 'rejected', error: 'That number is already on this site’s crew.' };

  const member: LocalCrewMember = {
    clientId: newClientId(),
    siteId,
    phone,
    name: input.name?.trim() || undefined,
    role: input.role?.trim() || undefined,
    createdAt: Date.now(),
    status: 'pending',
  };
  await putOne(member);
  notifyChange();

  const attempt = (async (): Promise<AddCrewResult> => {
    await syncCrew();
    const after = await getOne(member.clientId);
    if (after?.status === 'synced') return { status: 'synced', member: after };
    if (after?.status === 'failed') {
      await deleteOne(member.clientId);
      notifyChange();
      return { status: 'rejected', error: after.lastError ?? 'The server rejected this crew member.' };
    }
    return { status: 'pending', member };
  })();
  const timeout = new Promise<AddCrewResult>((resolve) =>
    setTimeout(() => resolve({ status: 'pending', member }), ADD_WAIT_MS),
  );
  return Promise.race([attempt, timeout]);
}

// Remove a crew member. Members already on the server need signal; ones still waiting on this
// device are simply dropped.
export async function removeCrew(siteId: string, target: { serverId?: number; clientId?: string; phone: string }) {
  if (target.serverId !== undefined) {
    let res: Response;
    try {
      res = await fetch(`/api/sites/${encodeURIComponent(siteId)}/crew/${target.serverId}`, { method: 'DELETE' });
    } catch {
      setOnline(false);
      throw new Error('Removing crew needs signal. Try again when you’re connected.');
    }
    if (!res.ok && res.status !== 404) {
      const json = await res.json().catch(() => ({}));
      throw new Error(json.error ?? `Could not remove (HTTP ${res.status})`);
    }
  }
  for (const m of await listLocalCrew(siteId)) {
    if (m.clientId === target.clientId || m.phone === target.phone) await deleteOne(m.clientId);
  }
  notifyChange();
  requestRefresh();
}

export async function retryCrew(clientId: string) {
  const m = await getOne(clientId);
  if (!m) return;
  await putOne({ ...m, status: 'pending', lastError: undefined });
  notifyChange();
  await syncCrew();
}

// Keep syncing while the app is open (on reconnect, when reopened, every 20 s while any wait).
export function startCrewAutoSync(): () => void {
  const sync = () => void syncCrew().catch(() => undefined);
  const onVisible = () => !document.hidden && sync();
  const timer = setInterval(async () => {
    if (document.hidden) return;
    if ((await listAll().catch(() => [])).some((m) => m.status === 'pending')) sync();
  }, RETRY_INTERVAL_MS);
  sync();
  window.addEventListener('online', sync);
  document.addEventListener('visibilitychange', onVisible);
  return () => {
    clearInterval(timer);
    window.removeEventListener('online', sync);
    document.removeEventListener('visibilitychange', onVisible);
  };
}
