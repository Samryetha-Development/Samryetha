import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
  server: {
    port: 5200,
    proxy: {
      // Proxy API calls to backend during dev.
      // Set VITE_API_BASE in .env.local to override; default assumes backend on :3001.
      "/api": {
        // i18n 独立服务运行在 :3002；主站后端在 :3001（翻译站不需要主站后端）
        target: process.env.VITE_API_TARGET ?? "http://localhost:3002",
        changeOrigin: true,
      },
    },
  },
});
