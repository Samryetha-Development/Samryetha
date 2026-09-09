import fs from "node:fs/promises";
import http from "node:http";
import path from "node:path";
import { fileURLToPath } from "node:url";
import express from "express";

const root = path.dirname(fileURLToPath(import.meta.url));
const production = process.env.NODE_ENV === "production";
const port = Number(process.env.PORT || 3000);
const API_TARGET = process.env.API_TARGET || "http://localhost:3001";
const app = express();

// 生产模式：/api 请求转发到后端 3001（dev 由 Vite 的 server.proxy 处理）。
// 手写转发而非引入 http-proxy-middleware，保持零依赖。SSE 流式经 pipe 原样透传。
const HOP_BY_HOP_HEADERS = new Set([
  "connection",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "te",
  "trailer",
  "transfer-encoding",
  "upgrade",
  "content-length",
]);

function apiProxy(req, res, next) {
  if (!req.originalUrl.startsWith("/api")) return next();
  const target = new URL(API_TARGET);
  const upstream = http.request(
    {
      hostname: target.hostname,
      port: target.port || undefined,
      method: req.method,
      path: req.originalUrl,
      headers: { ...req.headers, host: target.host },
    },
    (upstreamRes) => {
      res.status(upstreamRes.statusCode ?? 502);
      for (const [key, value] of Object.entries(upstreamRes.headers)) {
        if (HOP_BY_HOP_HEADERS.has(key)) continue;
        res.setHeader(key, value);
      }
      upstreamRes.pipe(res);
    },
  );
  upstream.setTimeout(30_000, () => {
    upstream.destroy();
    if (res.headersSent) res.destroy();
    else res.status(504).json({ error: { code: "GATEWAY_TIMEOUT", message: "API timed out" } });
  });
  upstream.on("error", () => {
    if (res.headersSent) res.destroy();
    else res.status(502).json({ error: { code: "SERVICE_UNAVAILABLE", message: "API unavailable" } });
  });
  req.pipe(upstream);
}

let vite;
if (!production) {
  const { createServer } = await import("vite");
  vite = await createServer({ server: { middlewareMode: true }, appType: "custom" });
  app.use(vite.middlewares);
} else {
  app.use(apiProxy);
  app.use(express.static(path.resolve(root, "dist/client"), { index: false }));
}

const SUPPORTED_LANGS = new Set(["en", "zh-CN", "zh-TW", "ja", "ko", "es", "fr", "de"]);

// 与前端 resolveLocale 同规则：把 Accept-Language 标签映射到支持语言（zh-Hant/HK/MO → zh-TW，其余 zh → zh-CN，en 兜底）。
function resolveAcceptLanguage(header) {
  if (!header) return "en";
  for (const raw of String(header).split(",")) {
    const tag = raw.split(";")[0].trim().toLowerCase().replace("_", "-");
    if (!tag) continue;
    if (tag === "zh-tw" || tag === "zh-hk" || tag === "zh-mo" || tag.startsWith("zh-hant") || tag.startsWith("zh-hk") || tag.startsWith("zh-mo")) return "zh-TW";
    if (tag.startsWith("zh")) return "zh-CN";
    if (tag.startsWith("ja")) return "ja";
    if (tag.startsWith("ko")) return "ko";
    if (tag.startsWith("es")) return "es";
    if (tag.startsWith("fr")) return "fr";
    if (tag.startsWith("de")) return "de";
    if (tag.startsWith("en")) return "en";
  }
  return "en";
}

function requestLocale(request) {
  const cookies = (request.headers.cookie || "").split(";");
  for (const part of cookies) {
    const index = part.indexOf("=");
    if (index < 0) continue;
    if (part.slice(0, index).trim() === "samryetha_lang") {
      try {
        const value = decodeURIComponent(part.slice(index + 1).trim());
        if (SUPPORTED_LANGS.has(value)) return value;
      } catch {
        // 非法 cookie 值（如畸形 % 编码）直接忽略，落到系统语言
      }
    }
  }
  // 无偏好 cookie → 按浏览器 Accept-Language 直出系统语言，避免首帧闪 en
  return resolveAcceptLanguage(request.headers["accept-language"]);
}

app.use(async (request, response, next) => {
  try {
    const url = request.originalUrl;
    const locale = requestLocale(request);
    const isTasks = new URL(url, "http://localhost").pathname === "/tasks";
    let template;
    let render;
    let renderTasks;

    if (!production) {
      template = await fs.readFile(path.resolve(root, isTasks ? "tasks.html" : "index.html"), "utf-8");
      template = await vite.transformIndexHtml(url, template);
      ({ render, renderTasks } = await vite.ssrLoadModule("/src/entry-server.tsx"));
      render = isTasks ? renderTasks : render;
    } else {
      template = await fs.readFile(path.resolve(root, isTasks ? "dist/client/tasks.html" : "dist/client/index.html"), "utf-8");
      ({ render, renderTasks } = await import("./dist/server/entry-server.js"));
      render = isTasks ? renderTasks : render;
    }

    response
      .status(200)
      .set({ "Content-Type": "text/html" })
      .end(template.replace("<!--app-html-->", () => (isTasks ? renderTasks(locale) : render(url, locale))).replace('<html lang="en">', `<html lang="${locale}">`));
  } catch (error) {
    vite?.ssrFixStacktrace(error);
    next(error);
  }
});

app.listen(port, () => {
  console.log(`Samryetha running at http://localhost:${port}`);
});
