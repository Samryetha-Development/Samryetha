import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
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
  ssr: {
    noExternal: ["@lako/ui"],
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
