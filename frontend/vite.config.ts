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
    hmr: { port: 3002 },
    proxy: {
      "/api": {
        target: process.env.API_TARGET || "http://localhost:3001",
        changeOrigin: false,
      },
    },
  },
});
