import { defineConfig } from "vite";

// 开发模式下 /api 代理到本地 FastAPI（默认 127.0.0.1:8300）
export default defineConfig({
  server: {
    proxy: {
      "/api": "http://127.0.0.1:8300",
    },
  },
  build: {
    outDir: "dist",
  },
});
