'use client';

import { useSyncExternalStore } from 'react';

// Whether the server is reachable. Starts from the browser's online flag, then follows real
// requests: a failed or cached-only API response marks us offline, a fresh one marks us online.
// (navigator.onLine alone says "online" on a weak or captive connection.)
let online = typeof navigator === 'undefined' ? true : navigator.onLine;
const listeners = new Set<() => void>();

export function setOnline(value: boolean) {
  if (value === online) return;
  online = value;
  listeners.forEach((l) => l());
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  const goOnline = () => setOnline(true);
  const goOffline = () => setOnline(false);
  window.addEventListener('online', goOnline);
  window.addEventListener('offline', goOffline);
  return () => {
    listeners.delete(listener);
    window.removeEventListener('online', goOnline);
    window.removeEventListener('offline', goOffline);
  };
}

export function useOnline(): boolean {
  return useSyncExternalStore(subscribe, () => online, () => true);
}
