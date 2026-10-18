'use client';

import { useSyncExternalStore } from 'react';

// Whether the home page is showing the Crewmember View. The dashboard sets it; the header's
// alert banner reads it and hides its bars, since the crew view lists its own site's alerts.
let active = false;
const listeners = new Set<() => void>();

export function setCrewViewActive(value: boolean) {
  if (value === active) return;
  active = value;
  listeners.forEach((l) => l());
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function useCrewViewActive(): boolean {
  return useSyncExternalStore(subscribe, () => active, () => false);
}
