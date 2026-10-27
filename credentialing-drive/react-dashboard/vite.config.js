import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { resolve } from "node:path";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    fs: { allow: [resolve(import.meta.dirname, "..")] },
    proxy: {
      "/auth": "http://127.0.0.1:8080",
      "/entities": "http://127.0.0.1:8080",
      "/static": "http://127.0.0.1:8080",
    },
  },
});
