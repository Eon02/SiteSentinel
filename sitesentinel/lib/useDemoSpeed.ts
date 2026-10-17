'use client';

import { useSyncExternalStore } from 'react';

const KEY = 'sitesentinel:demoSpeed';
const LOCAL_EVENT = 'sitesentinel:demoSpeed';

function read(): boolean {
  try {
    return window.localStorage.getItem(KEY) === 'true';
  } catch {
    return false;
  }
}

function subscribe(onChange: () => void) {
  const onStorage = (e: StorageEvent) => e.key === KEY && onChange();
  window.addEventListener('storage', onStorage);   // other tabs
  window.addEventListener(LOCAL_EVENT, onChange);  // this tab
  return () => {
    window.removeEventListener('storage', onStorage);
    window.removeEventListener(LOCAL_EVENT, onChange);
  };
}

export function setDemoSpeed(on: boolean) {
  try {
    window.localStorage.setItem(KEY, String(on));
  } catch {
    // Storage blocked: the toggle won't persist, which is acceptable.
  }
  window.dispatchEvent(new Event(LOCAL_EVENT));
}

// Shared across all tabs in the same browser via localStorage.
export function useDemoSpeed(): boolean {
  return useSyncExternalStore(subscribe, read, () => false);
}
