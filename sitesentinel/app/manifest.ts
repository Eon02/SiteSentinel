import type { MetadataRoute } from 'next';

// Makes the app installable ("Add to Home Screen"). Served at /manifest.webmanifest.
export default function manifest(): MetadataRoute.Manifest {
  return {
    name: 'SiteSentinel',
    short_name: 'SiteSentinel',
    description: 'Job site hazard ranking, live safety alerts, and crew hazard reports.',
    start_url: '/',
    scope: '/',
    display: 'standalone',
    orientation: 'any',
    background_color: '#ffffff',
    theme_color: '#16213E',
    icons: [
      { src: '/icons/icon-192.png', sizes: '192x192', type: 'image/png' },
      { src: '/icons/icon-512.png', sizes: '512x512', type: 'image/png' },
      { src: '/icons/maskable-512.png', sizes: '512x512', type: 'image/png', purpose: 'maskable' },
    ],
    shortcuts: [{ name: 'Report a hazard', url: '/report', icons: [{ src: '/icons/icon-192.png', sizes: '192x192' }] }],
  };
}
