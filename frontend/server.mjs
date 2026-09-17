import fs from "node:fs/promises";
import http from "node:http";
import https from "node:https";
import path from "node:path";
import { fileURLToPath } from "node:url";
import express from "express";

const root = path.dirname(fileURLToPath(import.meta.url));
const production = process.env.NODE_ENV === "production";
const port = Number(process.env.PORT || 3000);
const API_TARGET = process.env.API_TARGET || "http://localhost:3001";
// i18n server origin（SSR 预取用）：
//   开发默认 localhost:3002；生产指向内网地址（仅服务端 fetch，浏览器访问不到）。
// 注入页面供浏览器 fetch 的地址见 I18N_CLIENT_ORIGIN（必须公网可达）。
const I18N_API_ORIGIN = process.env.I18N_API_ORIGIN || "http://localhost:3002";
// 注入 window.__I18N_ORIGIN__ 的地址：浏览器端按语言拉 catalog 时用。
// 生产必须指到公网 i18n 域（如 https://i18n.samryetha.com）；默认与 I18N_API_ORIGIN 相同（dev 用 localhost:3002）。
// 生产不默认 localhost：未显式配置 I18N_CLIENT_ORIGIN 时留空，不注入 __I18N_ORIGIN__，避免泄漏 dev 默认地址。
const I18N_CLIENT_ORIGIN = production
  ? process.env.I18N_CLIENT_ORIGIN || ""
  : process.env.I18N_CLIENT_ORIGIN || I18N_API_ORIGIN;

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

/**
 * 从 i18n server 获取指定 locale 的 catalog。
 * 若 i18n server 不可用，返回 null（graceful fallback）。
 */
async function fetchCatalogFromI18nServer(locale) {
  const base = I18N_API_ORIGIN.replace(/\/$/, "");
  const url = `${base}/api/catalog/${locale}/translations`;
  return new Promise((resolve) => {
    const lib = url.startsWith("https://") ? https : http;
    const req = lib.get(url, { timeout: 3000 }, (res) => {
      if (res.statusCode !== 200) {
        res.resume();
        resolve(null);
        return;
      }
      let body = "";
      res.setEncoding("utf-8");
      res.on("data", (chunk) => { body += chunk; });
      res.on("end", () => {
        try {
          const data = JSON.parse(body);
          resolve(data.translations ?? null);
        } catch {
          resolve(null);
        }
      });
    });
    req.on("error", () => resolve(null));
    req.on("timeout", () => { req.destroy(); resolve(null); });
  });
}

/**
 * SSR 阶段：从 i18n server 预取 locale + en 两个 catalog，
 * 返回 { locale, translations, en } 供注入 HTML。
 * 若 i18n server 不可用，translations 和 en 均为 null。
 */
async function prefetchCatalogs(locale) {
  const tasks = [fetchCatalogFromI18nServer(locale)];
  if (locale !== "en") tasks.push(fetchCatalogFromI18nServer("en"));
  const [localeCatalog, enCatalog] = await Promise.all(tasks);
  return { localeCatalog: localeCatalog ?? null, enCatalog: locale === "en" ? (localeCatalog ?? null) : (enCatalog ?? null) };
}

/**
 * 把 catalog 安全序列化为 JSON 并包装为 <script> 标签注入 HTML。
 * 使用 </script 转义防 XSS。
 */
function buildCatalogScript(locale, localeCatalog, enCatalog, i18nOrigin) {
  if (!localeCatalog && !enCatalog && !i18nOrigin) return "";
  const payload = {
    locale,
    translations: localeCatalog ?? {},
    en: enCatalog ?? {},
  };
  const json = JSON.stringify(payload).replace(/<\/script/gi, "<\\/script");
  const originInjection = i18nOrigin ? `window.__I18N_ORIGIN__=${JSON.stringify(i18nOrigin)};` : "";
  return `<script>window.__I18N_CATALOG__=${json};${originInjection}</script>`;
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
    } else {
      template = await fs.readFile(path.resolve(root, isTasks ? "dist/client/tasks.html" : "dist/client/index.html"), "utf-8");
      ({ render, renderTasks } = await import("./dist/server/entry-server.js"));
    }

    // 从 i18n server 预取 catalog（不可用时 graceful fallback，不阻塞页面）
    const { localeCatalog, enCatalog } = await prefetchCatalogs(locale);
    // 注入浏览器的 origin 用公网地址（I18N_CLIENT_ORIGIN），而非内网 SSR 地址
    const catalogScript = buildCatalogScript(locale, localeCatalog, enCatalog, I18N_CLIENT_ORIGIN);

    // catalog 作为参数传给 SSR render（供 LanguageProvider 使用，跳过客户端首次 fetch）
    const catalog = localeCatalog ?? undefined;
    const appHtml = isTasks
      ? renderTasks(locale, catalog)
      : render(url, locale, catalog);

    // 将 catalog script 注入 </head> 前（或作为 body 第一个 script）
    let html = template
      .replace("<!--app-html-->", () => appHtml)
      .replace('<html lang="en">', `<html lang="${locale}">`);

    if (catalogScript) {
      // 注入到 </head> 之前；若无 </head> 则追加到 </body> 前
      if (html.includes("</head>")) {
        html = html.replace("</head>", `${catalogScript}</head>`);
      } else if (html.includes("</body>")) {
        html = html.replace("</body>", `${catalogScript}</body>`);
      }
    }

    response.status(200).set({ "Content-Type": "text/html" }).end(html);
  } catch (error) {
    vite?.ssrFixStacktrace(error);
    next(error);
  }
});

app.listen(port, () => {
  console.log(`Samryetha running at http://localhost:${port}`);
  console.log(`i18n API origin (SSR):   ${I18N_API_ORIGIN}`);
  console.log(`i18n client origin (CSR): ${I18N_CLIENT_ORIGIN}`);
});
