import type { NextConfig } from "next";

const api = process.env.LAKO_API_INTERNAL_URL ?? "http://localhost:8000";

// 允许以 iframe 嵌入本登录页的站点（Samryetha 主站弹层登录用）。
// 与 CORS 同源名单 ALLOWED_ORIGINS 保持一致；其余域名一律禁止嵌入（防 clickjacking）。
// 注意：这里只放 frame-ancestors，不设 X-Frame-Options —— XFO 无法表达白名单，
// 会把主站自己也挡在 iframe 之外。
const allowedOrigins = (process.env.ALLOWED_ORIGINS ?? "http://localhost:3000")
  .split(",")
  .map((origin) => origin.trim())
  .filter((origin) => /^https?:\/\//.test(origin));
const frameAncestors = ["'self'", ...allowedOrigins].join(" ");

const config: NextConfig = {
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
          { key: "Content-Security-Policy", value: `frame-ancestors ${frameAncestors}` },
        ],
      },
    ];
  },
};

export default config;
