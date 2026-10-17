'use client';

import { setOnline } from './connectivity';
import { requestRefresh } from './usePolling';

// Offline queue for crew hazard reports.
//
// Every report is saved to IndexedDB (photo included) before it's uploaded, so nothing is lost
// without signal. Queued reports upload automatically: right away, when the connection comes
// back, when the app is reopened, every 20 s while any are waiting, and (Chrome/Android) via
// Background Sync in public/sw.js even if the app is closed. Each report carries a clientId the
// server deduplicates on, so a retry after a dropped connection never creates a second report.
//
// The IndexedDB layout and lock name are shared with public/sw.js; keep them in sync.

const DB_NAME = 'sitesentinel';
const DB_VERSION = 1;
const STORE = 'reportQueue';
const LOCK = 'sitesentinel-report-queue';
const SYNC_TAG = 'report-queue';
const CHANGE_EVENT = 'sitesentinel:queue';
const CHANNEL = 'sitesentinel-queue';
const SUBMIT_WAIT_MS = 8000; // after this, tell the user it's saved and keep uploading in the background
const RETRY_INTERVAL_MS = 20_000;

export type QueuedReport = {
  clientId: string;
  createdAt: number;
  siteId: string;
  hazardType: string;
  description: string;
  reportedBy: string;
  lat: number;
  lon: number;
  photo: Blob | null;
  status: 'pending' | 'failed'; // failed = the server rejected it; needs a person to retry or discard
  lastError?: string;
};

export type ReportInput = Omit<QueuedReport, 'clientId' | 'createdAt' | 'status' | 'lastError'>;

export type SubmitResult =
  | { status: 'sent'; clientId: string }
  | { status: 'queued'; clientId: string } // saved on the device, will upload when it can
  | { status: 'rejected'; error: string }; // the server refused it (e.g. invalid input)

// --- IndexedDB ------------------------------------------------------------------------------

function openDb(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, DB_VERSION);
    req.onupgradeneeded = () => {
      if (!req.result.objectStoreNames.contains(STORE)) {
        req.result.createObjectStore(STORE, { keyPath: 'clientId' });
      }
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

async function withStore<T>(
  mode: IDBTransactionMode,
  fn: (store: IDBObjectStore) => IDBRequest<T> | void,
): Promise<T | undefined> {
  const db = await openDb();
  try {
    return await new Promise<T | undefined>((resolve, reject) => {
      const tx = db.transaction(STORE, mode);
      const req = fn(tx.objectStore(STORE));
      tx.oncomplete = () => resolve(req ? req.result : undefined);
      tx.onerror = () => reject(tx.error);
      tx.onabort = () => reject(tx.error);
    });
  } finally {
    db.close();
  }
}

export async function listQueued(): Promise<QueuedReport[]> {
  const all = (await withStore<QueuedReport[]>('readonly', (s) => s.getAll())) ?? [];
  return all.sort((a, b) => a.createdAt - b.createdAt);
}

const getQueued = (clientId: string) =>
  withStore<QueuedReport>('readonly', (s) => s.get(clientId) as IDBRequest<QueuedReport>);
const putQueued = (record: QueuedReport) => withStore('readwrite', (s) => void s.put(record));
const deleteQueued = (clientId: string) => withStore('readwrite', (s) => void s.delete(clientId));

// --- change notifications -------------------------------------------------------------------

function notifyChange() {
  window.dispatchEvent(new Event(CHANGE_EVENT));
  try {
    const bc = new BroadcastChannel(CHANNEL);
    bc.postMessage('changed');
    bc.close();
  } catch {
    // other tabs catch up on their next flush
  }
}

// Calls `listener` whenever the queue changes in this tab, another tab, or the service worker.
export function onQueueChange(listener: () => void): () => void {
  window.addEventListener(CHANGE_EVENT, listener);
  let bc: BroadcastChannel | undefined;
  try {
    bc = new BroadcastChannel(CHANNEL);
    bc.onmessage = listener;
  } catch {
    // ignore
  }
  const onWorkerMessage = (e: MessageEvent) => {
    if (e.data?.type === 'QUEUE_CHANGED') {
      listener();
      if (e.data.sent > 0) requestRefresh();
    }
  };
  navigator.serviceWorker?.addEventListener('message', onWorkerMessage);
  return () => {
    window.removeEventListener(CHANGE_EVENT, listener);
    bc?.close();
    navigator.serviceWorker?.removeEventListener('message', onWorkerMessage);
  };
}

// --- uploading ------------------------------------------------------------------------------

type Outcome = { outcome: 'sent' } | { outcome: 'offline' } | { outcome: 'retry' } | { outcome: 'failed'; error: string };

async function upload(record: QueuedReport): Promise<Outcome> {
  const body = new FormData();
  body.set('clientId', record.clientId);
  body.set('siteId', record.siteId);
  body.set('hazardType', record.hazardType);
  body.set('description', record.description);
  body.set('reportedBy', record.reportedBy);
  body.set('lat', String(record.lat));
  body.set('lon', String(record.lon));
  if (record.photo) body.set('photo', record.photo, 'hazard.jpg');

  let res: Response;
  try {
    res = await fetch('/api/reports', { method: 'POST', body });
  } catch {
    return { outcome: 'offline' };
  }
  if (res.ok) return { outcome: 'sent' };
  if (res.status >= 500 || res.status === 408 || res.status === 429) return { outcome: 'retry' };
  const json = await res.json().catch(() => ({}));
  return { outcome: 'failed', error: json.error ?? `Upload rejected (HTTP ${res.status})` };
}

let inFlight: Promise<void> | null = null; // fallback when Web Locks aren't available

// Upload every pending report, oldest first. Stops at the first network failure (no signal).
export function flushQueue(): Promise<void> {
  const run = async () => {
    let sent = 0;
    for (const record of await listQueued()) {
      if (record.status !== 'pending') continue;
      const result = await upload(record);
      if (result.outcome === 'sent') {
        await deleteQueued(record.clientId);
        sent++;
        setOnline(true);
      } else if (result.outcome === 'failed') {
        await putQueued({ ...record, status: 'failed', lastError: result.error });
      } else {
        if (result.outcome === 'offline') setOnline(false);
        break;
      }
    }
    if (sent > 0) requestRefresh();
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

// Ask the service worker to upload the queue when signal returns, even if the app is closed.
// Background Sync is Chrome/Android only; elsewhere the app's own retries cover it.
function requestBackgroundSync() {
  navigator.serviceWorker
    ?.getRegistration()
    .then((reg) => (reg as (ServiceWorkerRegistration & { sync?: { register(tag: string): Promise<void> } }) | undefined)?.sync?.register(SYNC_TAG))
    .catch(() => undefined);
}

// Save a report on the device, then try to upload it. Resolves within ~8 s even on a weak signal.
export async function submitReport(input: ReportInput): Promise<SubmitResult> {
  const record: QueuedReport = { ...input, clientId: newClientId(), createdAt: Date.now(), status: 'pending' };
  try {
    await putQueued(record);
  } catch {
    return submitWithoutQueue(record); // IndexedDB unavailable (e.g. some private modes)
  }
  notifyChange();
  requestBackgroundSync();

  const attempt = (async (): Promise<SubmitResult> => {
    await flushQueue();
    const after = await getQueued(record.clientId);
    if (!after) return { status: 'sent', clientId: record.clientId };
    if (after.status === 'failed') {
      // Rejected (e.g. invalid input): hand it back to the form to fix instead of queueing it.
      await deleteQueued(record.clientId);
      notifyChange();
      return { status: 'rejected', error: after.lastError ?? 'The server rejected this report.' };
    }
    return { status: 'queued', clientId: record.clientId };
  })();
  const timeout = new Promise<SubmitResult>((resolve) =>
    setTimeout(() => resolve({ status: 'queued', clientId: record.clientId }), SUBMIT_WAIT_MS),
  );
  return Promise.race([attempt, timeout]);
}

async function submitWithoutQueue(record: QueuedReport): Promise<SubmitResult> {
  const result = await upload(record);
  if (result.outcome === 'sent') {
    requestRefresh();
    return { status: 'sent', clientId: record.clientId };
  }
  if (result.outcome === 'failed') return { status: 'rejected', error: result.error };
  return { status: 'rejected', error: 'No signal, and this browser can’t save reports offline. Try again in signal.' };
}

export async function retryQueued(clientId: string) {
  const record = await getQueued(clientId);
  if (!record) return;
  await putQueued({ ...record, status: 'pending', lastError: undefined });
  notifyChange();
  await flushQueue();
}

export async function discardQueued(clientId: string) {
  await deleteQueued(clientId);
  notifyChange();
}

// Keep uploading in the background while the app is open. Returns a cleanup function.
export function startAutoFlush(): () => void {
  const flush = () => void flushQueue().catch(() => undefined);
  const onVisible = () => !document.hidden && flush();
  const timer = setInterval(async () => {
    if (document.hidden) return;
    const pending = (await listQueued().catch(() => [])).some((r) => r.status === 'pending');
    if (pending) flush();
  }, RETRY_INTERVAL_MS);

  flush();
  window.addEventListener('online', flush);
  document.addEventListener('visibilitychange', onVisible);
  return () => {
    clearInterval(timer);
    window.removeEventListener('online', flush);
    document.removeEventListener('visibilitychange', onVisible);
  };
}

// crypto.randomUUID needs HTTPS; getRandomValues works everywhere (e.g. phones on the dev LAN).
function newClientId(): string {
  if (typeof crypto.randomUUID === 'function') return crypto.randomUUID();
  const b = crypto.getRandomValues(new Uint8Array(16));
  b[6] = (b[6] & 0x0f) | 0x40;
  b[8] = (b[8] & 0x3f) | 0x80;
  const hex = Array.from(b, (x) => x.toString(16).padStart(2, '0')).join('');
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}
