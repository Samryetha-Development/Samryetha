import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { relative, resolve } from "node:path";

// 这两个包是 file: 链接、经 pnpm 落在 node_modules 下，Vite 默认不 watch node_modules，
// 于是 `pnpm --dir packages/ui-commons build` 之后 dev server 仍吐旧 transform（现象：改了没反应，
// 必须重启）。这里显式 watch 工作区里的真实 dist 目录，改动就清掉模块图缓存并整页刷新。
// These file:-linked packages live under node_modules (pnpm), which Vite does not watch, so a
// rebuild served stale transforms until restart. Watch the real workspace dist dirs instead.
function workspaceDistReload(): Plugin {
  const roots = [
    resolve(__dirname, "../packages/ui-commons/dist"),
    resolve(__dirname, "../lako/packages/ui/dist"),
  ].map((root) => root.replace(/\\/g, "/"));
  const underRoot = (file: string) => {
    const normalized = file.replace(/\\/g, "/");
    return roots.some((root) => normalized === root || normalized.startsWith(`${root}/`));
  };
  return {
    name: "workspace-dist-reload",
    apply: "serve",
    configureServer(server) {
      server.watcher.add(roots);
      const onFsEvent = (file: string) => {
        if (!underRoot(file)) return;
        // 暴力清空模块图：缓存 key 是 pnpm 的 .pnpm 路径，按包名匹配不可靠，全清最稳。
        for (const mod of server.moduleGraph.idToModuleMap.values()) {
          server.moduleGraph.invalidateModule(mod);
        }
        server.config.logger.info(`[workspace-dist] ${relative(__dirname, file)} changed → full reload`);
        server.ws.send({ type: "full-reload" });
      };
      server.watcher.on("change", onFsEvent);
      server.watcher.on("add", onFsEvent);
      server.watcher.on("unlink", onFsEvent);
    },
  };
}

export default defineConfig({
  plugins: [react(), tailwindcss(), workspaceDistReload()],
  resolve: {
    // file: 链接的包不 dedupe 的话会解析到自己那份 react，出现双实例
    // （hooks 直接报 "Invalid hook call"）。
    // radix 同样要 dedupe：Dialog.Root 与 Dialog.Trigger 必须来自同一份模块，
    // 否则 React context 对不上——那种失败**没有任何报错**，只是对话框不弹。
    dedupe: [
      "react",
      "react-dom",
      "@radix-ui/react-dialog",
      "@radix-ui/react-alert-dialog",
      "@radix-ui/react-dismissable-layer",
      "@radix-ui/react-slot",
    ],
  },
  optimizeDeps: {
    // 别预打包这两个本地包。它们是 file: 链接的，Vite 的预打包缓存不会随
    // dist 变化失效——重建了包、也 pnpm install 了，浏览器拿到的仍是旧缓存，
    // 现象是"改了没反应"，排查起来极费劲。排除掉就直接读 dist 文件。
    exclude: ["@lako/ui", "samryetha-ui-commons"],
  },
  ssr: {
    // Vite 默认把 node_modules 里的依赖 externalize，交给 Node 运行时解析。
    // 这两个包的 dist 是合法 ESM，本来能跑；仍然显式打包进来，
    // 免得踩 symlink 解析的边角情况。
    noExternal: ["@lako/ui", "samryetha-ui-commons"],
  },
  server: {
    // middlewareMode 下 Vite 会给 HMR 自选独立端口，默认 24678 落在 Windows
    // Hyper-V 端口排除范围（24556–25055），绑定必报 EACCES → 前端 HMR 连不上。
    // 显式指定一个干净端口。
    hmr: { port: 3010 },
    proxy: {
      "/api": {
        target: process.env.API_TARGET || "http://localhost:3001",
        changeOrigin: false,
      },
    },
  },
});
