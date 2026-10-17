import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  /* config options here */
  cacheComponents: true,
  partialPrefetching: true,
  // Let phones on the local network load the dev server by IP (e.g. http://10.x.x.x:3000).
  // Dev-only; production builds ignore it. `*` matches one label of the hostname.
  allowedDevOrigins: ["10.*.*.*", "192.168.*.*"],
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
