'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { setOnline } from './connectivity';

const REFRESH_EVENT = 'sitesentinel:refresh';
const CHANNEL = 'sitesentinel';
const OFFLINE_HEADER = 'x-sitesentinel-offline'; // set by public/sw.js on cached responses

// Ask every polling hook (in this tab and other tabs) to refetch now, e.g. after a new report.
export function requestRefresh() {
  window.dispatchEvent(new Event(REFRESH_EVENT));
  try {
    const bc = new BroadcastChannel(CHANNEL);
    bc.postMessage(REFRESH_EVENT);
    bc.close();
  } catch {
    // BroadcastChannel unsupported: same-tab refresh still works.
  }
}

export type Polled<T> = {
  data: T | undefined;
  loading: boolean;      // true until the first response arrives
  offline: boolean;      // no signal: `data` is the last good value (in memory or the offline cache)
  notFound: boolean;     // server answered 404
  refetch: () => void;
};

type State<T> = { url: string | null; data?: T; done: boolean; offline: boolean; notFound: boolean };

const initial = <T,>(url: string | null): State<T> => ({ url, done: false, offline: false, notFound: false });

// Fetch immediately, then every `intervalMs`; pauses while the tab is hidden, and fetches again
// as soon as the connection comes back.
export function usePolling<T>(url: string | null, intervalMs: number): Polled<T> {
  const [state, setState] = useState<State<T>>(() => initial(url));
  const loadRef = useRef<() => void>(undefined);

  useEffect(() => {
    if (!url) return;
    let cancelled = false;
    let timer: ReturnType<typeof setInterval> | undefined;

    // Results for a previous url are dropped; state for a new url starts fresh.
    const update = (patch: Partial<State<T>>) =>
      setState((prev) => ({ ...(prev.url === url ? prev : initial<T>(url)), ...patch, done: true }));

    const load = async () => {
      try {
        const res = await fetch(url, { cache: 'no-store' });
        if (cancelled) return;
        // The service worker answers from its cache when there's no signal and flags it.
        const fromCache = res.headers.get(OFFLINE_HEADER) === '1';
        setOnline(!fromCache);
        if (res.status === 404) return update({ notFound: true, offline: fromCache });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = (await res.json()) as T;
        if (!cancelled) update({ data, notFound: false, offline: fromCache });
      } catch (e) {
        if (e instanceof TypeError) setOnline(false); // network failure
        if (!cancelled) update({ offline: true });
      }
    };
    loadRef.current = () => void load();

    const stop = () => {
      if (timer) clearInterval(timer);
      timer = undefined;
    };
    const start = () => {
      stop();
      void load();
      timer = setInterval(load, intervalMs);
    };
    const onVisibility = () => (document.hidden ? stop() : start());
    const onRefresh = () => void load();

    let bc: BroadcastChannel | undefined;
    try {
      bc = new BroadcastChannel(CHANNEL);
      bc.onmessage = (e) => e.data === REFRESH_EVENT && onRefresh();
    } catch {
      // ignore
    }

    if (!document.hidden) start();
    document.addEventListener('visibilitychange', onVisibility);
    window.addEventListener(REFRESH_EVENT, onRefresh);
    window.addEventListener('online', onRefresh);
    return () => {
      cancelled = true;
      stop();
      document.removeEventListener('visibilitychange', onVisibility);
      window.removeEventListener(REFRESH_EVENT, onRefresh);
      window.removeEventListener('online', onRefresh);
      bc?.close();
    };
  }, [url, intervalMs]);

  const refetch = useCallback(() => loadRef.current?.(), []);
  const current = state.url === url ? state : initial<T>(url);
  return { data: current.data, loading: !current.done, offline: current.offline, notFound: current.notFound, refetch };
}
