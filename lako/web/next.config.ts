import type { NextConfig } from "next";
const api = process.env.LAKO_API_INTERNAL_URL ?? "http://localhost:8000";
const config: NextConfig = { async rewrites() { return [
  { source: "/api/:path*", destination: `${api}/api/:path*` },
  { source: "/oauth/:path*", destination: `${api}/oauth/:path*` },
  { source: "/.well-known/:path*", destination: `${api}/.well-known/:path*` },
]; } };
export default config;
