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

app.use(async (request, response, next) => {
  try {
    const url = request.originalUrl;
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
      .end(template.replace("<!--app-html-->", () => render(url)));
  } catch (error) {
    vite?.ssrFixStacktrace(error);
    next(error);
  }
});

app.listen(port, () => {
  console.log(`Samryetha running at http://localhost:${port}`);
});
