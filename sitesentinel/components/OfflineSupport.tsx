'use client';

import { useCallback, useEffect, useState } from 'react';
import { CloudUpload, LoaderCircle } from 'lucide-react';
import { useOnline } from '@/lib/connectivity';
import { hazardLabel } from '@/lib/types';
import {
  discardQueued,
  flushQueue,
  listQueued,
  onQueueChange,
  retryQueued,
  startAutoFlush,
  type QueuedReport,
} from '@/lib/reportQueue';

// Registers the service worker (production builds only: in `next dev` it would cache stale
// code), keeps queued reports uploading, and shows which reports are still on this device.
export default function OfflineSupport() {
  const online = useOnline();
  const [queued, setQueued] = useState<QueuedReport[]>([]);
  const [sending, setSending] = useState(false);

  const refresh = useCallback(() => {
    listQueued().then(setQueued).catch(() => setQueued([]));
  }, []);

  useEffect(() => {
    registerServiceWorker();
    refresh();
    const stopListening = onQueueChange(refresh);
    const stopFlushing = startAutoFlush();
    return () => {
      stopListening();
      stopFlushing();
    };
  }, [refresh]);

  const pending = queued.filter((r) => r.status === 'pending');
  const failed = queued.filter((r) => r.status === 'failed');
  if (!pending.length && !failed.length) return null;

  async function sendNow() {
    setSending(true);
    try {
      await flushQueue();
    } finally {
      setSending(false);
      refresh();
    }
  }

  return (
    <div className="flex flex-col bg-slate-800 text-white" role="status">
      {pending.length > 0 && (
        <div className="flex flex-wrap items-center justify-between gap-x-3 px-4 py-1 text-sm font-semibold">
          <span className="flex items-center gap-2">
            <CloudUpload size={18} aria-hidden />
            {pending.length} report{pending.length === 1 ? '' : 's'} saved on this device.{' '}
            {online ? 'Uploading…' : 'Will upload when you have signal.'}
          </span>
          <button
            type="button"
            onClick={sendNow}
            disabled={sending}
            className="flex min-h-11 items-center gap-1 px-2 underline disabled:opacity-60"
          >
            {sending && <LoaderCircle size={16} className="animate-spin" aria-hidden />}
            Send now
          </button>
        </div>
      )}
      {failed.map((r) => (
        <div key={r.clientId} className="flex flex-wrap items-center justify-between gap-x-3 bg-[#C92A2A] px-4 py-1 text-sm font-semibold">
          <span>
            {hazardLabel(r.hazardType)} report couldn&apos;t be sent: {r.lastError}
          </span>
          <span className="flex gap-1">
            <button type="button" onClick={() => void retryQueued(r.clientId)} className="min-h-11 px-2 underline">
              Retry
            </button>
            <button type="button" onClick={() => void discardQueued(r.clientId)} className="min-h-11 px-2 underline">
              Discard
            </button>
          </span>
        </div>
      ))}
    </div>
  );
}

function registerServiceWorker() {
  if (process.env.NODE_ENV !== 'production' || !('serviceWorker' in navigator)) return;
  navigator.serviceWorker
    .register('/sw.js', { scope: '/', updateViaCache: 'none' })
    .then(async () => {
      const reg = await navigator.serviceWorker.ready;
      // Hand over the scripts and styles this page loaded before the worker was in control, so
      // the first visit is enough to make the app work offline.
      const urls = performance
        .getEntriesByType('resource')
        .map((e) => e.name)
        .filter((u) => u.startsWith(location.origin));
      reg.active?.postMessage({ type: 'CACHE_URLS', urls });
    })
    .catch(() => undefined); // e.g. plain-HTTP LAN address: no service worker, app still works online
}
