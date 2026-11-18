import type { NextConfig } from "next";

const API_URL = process.env.MERGE_API_URL ?? "http://127.0.0.1:9100";

const config: NextConfig = {
  reactStrictMode: true,
  // In dev we proxy /api/* to the FastAPI backend on a different port.
  // In prod, Caddy handles the same routing at the reverse-proxy layer, so
  // these rewrites are inert (Next.js routes never match /api/*).
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${API_URL}/api/:path*` }];
  },
};

export default config;
