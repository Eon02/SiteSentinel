import type { Site } from './types';

// The site closest to a point (great-circle distance).
export function nearestSite(sites: Site[], from: { lat: number; lon: number }): Site {
  const toRad = (d: number) => (d * Math.PI) / 180;
  const dist = (s: Site) => {
    const dLat = toRad(s.lat - from.lat);
    const dLon = toRad(s.lon - from.lon);
    const a = Math.sin(dLat / 2) ** 2 + Math.cos(toRad(from.lat)) * Math.cos(toRad(s.lat)) * Math.sin(dLon / 2) ** 2;
    return 2 * Math.asin(Math.sqrt(a));
  };
  return sites.reduce((best, s) => (dist(s) < dist(best) ? s : best));
}
