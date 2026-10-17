import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  /* config options here */
  cacheComponents: true,
  partialPrefetching: true,
  // Let phones on the local network load the dev server by IP (e.g. http://10.x.x.x:3000).
  // Dev-only; production builds ignore it. `*` matches one label of the hostname.
  allowedDevOrigins: ["10.*.*.*", "192.168.*.*"],
  // The service worker must never be served from a cache, or app updates would stall.
  async headers() {
    return [
      {
        source: "/sw.js",
        headers: [
          { key: "Content-Type", value: "application/javascript; charset=utf-8" },
          { key: "Cache-Control", value: "no-cache, no-store, must-revalidate" },
        ],
      },
    ];
  },
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;
