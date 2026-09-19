import type { NextConfig } from "next";

const api = process.env.LAKO_API_INTERNAL_URL ?? "http://localhost:8000";

const config: NextConfig = {
  devIndicators: false,
  async rewrites() {
    return [
      { source: "/api/:path*", destination: `${api}/api/:path*` },
      { source: "/oauth/:path*", destination: `${api}/oauth/:path*` },
      { source: "/.well-known/:path*", destination: `${api}/.well-known/:path*` },
    ];
  },
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "Content-Security-Policy", value: "frame-ancestors 'self' http://localhost:3000 https://samryetha.com" },
        ],
      },
    ];
  },
};

export default config;
