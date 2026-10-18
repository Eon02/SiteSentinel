'use client';

import { setWorkerUrl } from 'maplibre-gl';

// MapLibre v6 finds its worker relative to its own module URL, which breaks once Next bundles it.
// `npm run dev`/`build` copy the worker into public/ (see the copy-maplibre-worker script).
setWorkerUrl('/maplibre-gl-worker.mjs');

export const MAP_STYLE = 'https://tiles.openfreemap.org/styles/liberty';

// Map tiles, styles and fonts that can't be downloaded (no signal, and not viewed before) are
// expected offline: the map shows a plain background and markers still work. Keep those out of
// the console and still report anything else.
export function onMapError(e: { error?: Error }) {
  if (!e.error?.message?.startsWith('AJAXError')) console.error(e.error);
}
