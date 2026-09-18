import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { resolve } from "node:path";

// dev 下 /tasks 是多页入口 tasks.html，不是 SPA 路由；重写到实际入口文件，
// 让 Vite 中间件链按多页处理（configureServer 里注册，先于内置中间件生效）。
function tasksDevRewrite(): Plugin {
  return {
    name: "tasks-dev-rewrite",
    configureServer(server) {
      server.middlewares.use((req, _res, next) => {
        if (req.url && /^\/tasks(?:[/?]|$)/.test(req.url)) {
          req.url = `/tasks.html${req.url.slice("/tasks".length)}`;
        }
        next();
      });
    },
  };
}

export default defineConfig({
  plugins: [react(), tailwindcss(), tasksDevRewrite()],
  resolve: {
    // @lako/ui 走 file: 链接，是个 symlink。不 dedupe 的话它会解析到自己那份
    // react，出现双实例（hooks 直接报 "Invalid hook call"）。
    dedupe: ["react", "react-dom"],
  },
  optimizeDeps: {
    // 别预打包 @lako/ui。它是 file: 链接的本地包，Vite 的预打包缓存不会随
    // dist 变化失效——重建了包、也 pnpm install 了，浏览器拿到的仍是旧缓存，
    // 现象是"改了没反应"，排查起来极费劲。排除掉就直接读 dist 文件。
    exclude: ["@lako/ui"],
  },
  ssr: {
    // Vite 默认把 node_modules 里的依赖 externalize，交给 Node 运行时解析。
    // @lako/ui 的 dist 是合法 ESM，本来能跑；仍然显式打包进来，
    // 免得踩 symlink 解析的边角情况。
    noExternal: ["@lako/ui"],
  },
  build: {
    rollupOptions: {
      input: {
        main: resolve(__dirname, "index.html"),
        tasks: resolve(__dirname, "tasks.html"),
      },
    },
  },
  server: {
    // middlewareMode 下 Vite 会给 HMR 自选独立端口，默认 24678 落在 Windows
    // Hyper-V 端口排除范围（24556–25055），绑定必报 EACCES → 前端 HMR 连不上。
    // 显式指定一个干净端口。
    // 注意：不能用 3002——那是 i18n 服务的端口。HMR 是纯 WebSocket 服务，
    // 普通 HTTP 请求会被它以 426 Upgrade Required 拒掉；而 localhost 在本机优先
    // 解析到 ::1，SSR 预取 i18n catalog 时会打到 HMR 上，词条静默变空。
    hmr: { port: 3010 },
    proxy: {
      "/api": {
        target: process.env.API_TARGET || "http://localhost:3001",
        changeOrigin: false,
      },
    },
  },
});
