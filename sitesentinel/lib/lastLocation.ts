'use client';

// The phone's last GPS fix, kept on the device so the nearest site can still be picked when a
// fresh fix isn't available (no signal, location timed out). Only used to choose a site, never
// as a hazard's location.
const KEY = 'sitesentinel:last-location';

export type LatLon = { lat: number; lon: number };

export function saveLastLocation(pos: LatLon) {
  try {
    localStorage.setItem(KEY, JSON.stringify({ lat: pos.lat, lon: pos.lon }));
  } catch {
    // storage blocked: nothing remembered
  }
}

export function readLastLocation(): LatLon | null {
  if (typeof window === 'undefined') return null;
  try {
    const v = JSON.parse(localStorage.getItem(KEY) ?? 'null');
    return v && Number.isFinite(v.lat) && Number.isFinite(v.lon) ? { lat: v.lat, lon: v.lon } : null;
  } catch {
    return null;
  }
}
