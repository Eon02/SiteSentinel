/* SiteSentinel service worker: offline-first caching and background upload of queued reports.
 *
 * Caching
 *   /_next/static/*, /icons/*  cache-first (file names are content-hashed)
 *   pages (navigations)         network-first with a timeout, then the cached page, then /offline
 *   RSC payloads (client nav)   network-first, then cache
 *   GET /api/*                  network-first with a timeout, then the last saved response,
 *                               marked with an `x-sitesentinel-offline: 1` header so the UI can say
 *                               it is showing saved data
 *   map tiles and photos        cache-first, capped in size
 *
 * Report queue
 *   lib/reportQueue.ts saves reports to IndexedDB. Where Background Sync exists (Chrome/Android),
 *   the 'report-queue' sync event uploads them here even if the app is closed. The page and this
 *   worker share the IndexedDB layout and a Web Lock, so a report is never uploaded twice at once.
 *
 * Bump VERSION when changing this file's caching rules; old caches are deleted on activate.
 */

const VERSION = 'v1';
const CACHES = {
  pages: `ss-pages-${VERSION}`,
  static: `ss-static-${VERSION}`,
  data: `ss-data-${VERSION}`,
  tiles: `ss-tiles-${VERSION}`,
  images: `ss-images-${VERSION}`,
};
const PRECACHE_PAGES = ['/', '/report', '/offline'];
const OFFLINE_HEADER = 'x-sitesentinel-offline';
const NETWORK_TIMEOUT_MS = { page: 5000, data: 6000 };
const MAX_ENTRIES = { tiles: 2000, images: 200 };
const TILE_HOSTS = ['tiles.openfreemap.org'];

// Shared with lib/reportQueue.ts; keep in sync.
const QUEUE_DB = 'sitesentinel';
const QUEUE_DB_VERSION = 1;
const QUEUE_STORE = 'reportQueue';
const QUEUE_LOCK = 'sitesentinel-report-queue';
const SYNC_TAG = 'report-queue';

self.addEventListener('install', (event) => {
  event.waitUntil(precache().then(() => self.skipWaiting()));
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    (async () => {
      const keep = new Set(Object.values(CACHES));
      for (const name of await caches.keys()) {
        if (name.startsWith('ss-') && !keep.has(name)) await caches.delete(name);
      }
      await self.clients.claim();
    })(),
  );
});

// Cache the offline-critical pages and the JS/CSS they reference, so /report works offline even
// if the crew member never opened it while in signal.
async function precache() {
  const pages = await caches.open(CACHES.pages);
  const assets = new Set();
  for (const path of PRECACHE_PAGES) {
    try {
      const res = await fetch(path, { cache: 'no-store' });
      if (!res.ok) continue;
      const html = await res.clone().text();
      await pages.put(path, res);
      for (const match of html.matchAll(/\/_next\/static\/[^"'\s)\\]+/g)) assets.add(match[0]);
    } catch {
      // Offline during install: pages get cached as they're visited instead.
    }
  }
  const staticCache = await caches.open(CACHES.static);
  await Promise.all([...assets].map((url) => staticCache.add(url).catch(() => undefined)));
}

self.addEventListener('message', (event) => {
  const msg = event.data || {};
  // The page sends the assets it loaded before this worker took control.
  if (msg.type === 'CACHE_URLS' && Array.isArray(msg.urls)) {
    event.waitUntil(
      caches.open(CACHES.static).then((cache) =>
        Promise.all(
          msg.urls
            .filter((u) => isSameOrigin(u) && new URL(u).pathname.startsWith('/_next/static/'))
            .map((u) => cache.match(u).then((hit) => hit || cache.add(u).catch(() => undefined))),
        ),
      ),
    );
  }
  if (msg.type === 'FLUSH_QUEUE') event.waitUntil(flushQueue());
});

self.addEventListener('fetch', (event) => {
  const { request } = event;
  if (request.method !== 'GET') return; // report uploads go straight to the network
  const url = new URL(request.url);

  if (url.origin !== self.location.origin) {
    if (TILE_HOSTS.includes(url.hostname)) {
      event.respondWith(cacheFirst(request, CACHES.tiles, MAX_ENTRIES.tiles));
    } else if (request.destination === 'image') {
      event.respondWith(cacheFirst(request, CACHES.images, MAX_ENTRIES.images));
    }
    return;
  }

  if (url.pathname === '/sw.js') return;
  if (url.pathname.startsWith('/_next/static/') || url.pathname.startsWith('/icons/')) {
    event.respondWith(cacheFirst(request, CACHES.static));
  } else if (url.pathname.startsWith('/api/')) {
    event.respondWith(dataNetworkFirst(event, request));
  } else if (request.mode === 'navigate') {
    event.respondWith(pageNetworkFirst(event, request, url));
  } else if (request.headers.get('RSC') === '1') {
    event.respondWith(rscNetworkFirst(request));
  } else if (url.pathname.startsWith('/_next/')) {
    return; // other framework internals (e.g. HMR in dev): leave alone
  } else {
    event.respondWith(staleWhileRevalidate(event, request, CACHES.static));
  }
});

// --- strategies ---------------------------------------------------------------------------

async function cacheFirst(request, cacheName, maxEntries) {
  const cache = await caches.open(cacheName);
  const hit = await cache.match(request);
  if (hit) return hit;
  const res = await fetch(request);
  if (res.ok || res.type === 'opaque') {
    await cache.put(request, res.clone());
    if (maxEntries) trim(cache, maxEntries);
  }
  return res;
}

async function staleWhileRevalidate(event, request, cacheName) {
  const cache = await caches.open(cacheName);
  const hit = await cache.match(request);
  const update = fetch(request)
    .then((res) => {
      if (res.ok) cache.put(request, res.clone());
      return res;
    })
    .catch(() => undefined);
  if (hit) {
    event.waitUntil(update);
    return hit;
  }
  return (await update) || Response.error();
}

// Pages: fresh from the network when possible. On a weak signal, fall back to the cached page
// after a few seconds (and keep updating the cache in the background).
async function pageNetworkFirst(event, request, url) {
  const cache = await caches.open(CACHES.pages);
  const key = url.pathname;
  const network = fetch(request).then(async (res) => {
    if (res.ok && res.type === 'basic') await cache.put(key, res.clone());
    return res;
  });
  event.waitUntil(network.catch(() => undefined));
  const fallback = async () => (await cache.match(key)) || (await cache.match('/offline'));
  try {
    const res = await withTimeout(network, NETWORK_TIMEOUT_MS.page);
    return res;
  } catch {
    return (await fallback()) || Response.error();
  }
}

async function rscNetworkFirst(request) {
  const cache = await caches.open(CACHES.pages);
  try {
    const res = await fetch(request);
    if (res.ok) await cache.put(request, res.clone());
    return res;
  } catch {
    // No cached payload: the failed fetch makes Next do a full page load, which the
    // navigation handler above answers from the cache.
    return (await cache.match(request, { ignoreVary: true })) || Response.error();
  }
}

async function dataNetworkFirst(event, request) {
  const cache = await caches.open(CACHES.data);
  const network = fetch(request).then(async (res) => {
    if (res.ok) await cache.put(request, res.clone());
    return res;
  });
  event.waitUntil(network.catch(() => undefined));
  try {
    return await withTimeout(network, NETWORK_TIMEOUT_MS.data);
  } catch {
    const hit = await cache.match(request);
    if (!hit) {
      return new Response(JSON.stringify({ error: 'Offline and no saved copy of this data' }), {
        status: 503,
        headers: { 'content-type': 'application/json', [OFFLINE_HEADER]: '1' },
      });
    }
    const headers = new Headers(hit.headers);
    headers.set(OFFLINE_HEADER, '1');
    return new Response(hit.body, { status: hit.status, statusText: hit.statusText, headers });
  }
}

function withTimeout(promise, ms) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('timeout')), ms);
    promise.then(
      (value) => (clearTimeout(timer), resolve(value)),
      (error) => (clearTimeout(timer), reject(error)),
    );
  });
}

async function trim(cache, maxEntries) {
  const keys = await cache.keys();
  for (let i = 0; i < keys.length - maxEntries; i++) await cache.delete(keys[i]);
}

function isSameOrigin(u) {
  try {
    return new URL(u, self.location.origin).origin === self.location.origin;
  } catch {
    return false;
  }
}

// --- report queue (Background Sync) ---------------------------------------------------------

self.addEventListener('sync', (event) => {
  if (event.tag === SYNC_TAG) event.waitUntil(flushQueue({ throwIfPending: true }));
});

function openQueue() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(QUEUE_DB, QUEUE_DB_VERSION);
    req.onupgradeneeded = () => {
      if (!req.result.objectStoreNames.contains(QUEUE_STORE)) {
        req.result.createObjectStore(QUEUE_STORE, { keyPath: 'clientId' });
      }
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

function tx(db, mode, fn) {
  return new Promise((resolve, reject) => {
    const t = db.transaction(QUEUE_STORE, mode);
    const result = fn(t.objectStore(QUEUE_STORE));
    t.oncomplete = () => resolve(result && 'result' in result ? result.result : undefined);
    t.onerror = () => reject(t.error);
  });
}

async function uploadReport(record) {
  const body = new FormData();
  for (const key of ['clientId', 'siteId', 'hazardType', 'description', 'reportedBy']) {
    body.set(key, record[key] ?? '');
  }
  body.set('lat', String(record.lat));
  body.set('lon', String(record.lon));
  if (record.photo) body.set('photo', record.photo, 'hazard.jpg');
  let res;
  try {
    res = await fetch('/api/reports', { method: 'POST', body });
  } catch {
    return { outcome: 'retry' };
  }
  if (res.ok) return { outcome: 'sent' };
  if (res.status >= 500 || res.status === 408 || res.status === 429) return { outcome: 'retry' };
  const json = await res.json().catch(() => ({}));
  return { outcome: 'failed', error: json.error || `Upload rejected (HTTP ${res.status})` };
}

async function flushQueue({ throwIfPending = false } = {}) {
  const run = async () => {
    const db = await openQueue();
    const records = (await tx(db, 'readonly', (s) => s.getAll())) || [];
    let sent = 0;
    let pending = 0;
    for (const record of records.sort((a, b) => a.createdAt - b.createdAt)) {
      if (record.status !== 'pending') continue;
      const { outcome, error } = await uploadReport(record);
      if (outcome === 'sent') {
        await tx(db, 'readwrite', (s) => s.delete(record.clientId));
        sent++;
      } else if (outcome === 'failed') {
        await tx(db, 'readwrite', (s) => s.put({ ...record, status: 'failed', lastError: error }));
      } else {
        pending = records.filter((r) => r.status === 'pending').length - sent;
        break; // still no signal: stop and try again later
      }
    }
    db.close();
    const clients = await self.clients.matchAll({ includeUncontrolled: true });
    for (const client of clients) client.postMessage({ type: 'QUEUE_CHANGED', sent });
    return pending;
  };
  const pending =
    self.navigator && self.navigator.locks
      ? await self.navigator.locks.request(QUEUE_LOCK, run)
      : await run();
  // Rejecting tells the browser to retry this sync later with backoff.
  if (throwIfPending && pending > 0) throw new Error(`${pending} report(s) still waiting for signal`);
}
