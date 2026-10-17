'use client';

import { setWorkerUrl } from 'maplibre-gl';

// MapLibre v6 finds its worker relative to its own module URL, which breaks once Next bundles it.
// `npm run dev`/`build` copy the worker into public/ (see the copy-maplibre-worker script).
setWorkerUrl('/maplibre-gl-worker.mjs');

export const MAP_STYLE = 'https://tiles.openfreemap.org/styles/liberty';
