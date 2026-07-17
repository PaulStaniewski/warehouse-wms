import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const usePolling = process.env.VITE_USE_POLLING === "true";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "0.0.0.0",
    port: 3000,
    allowedHosts: [".trycloudflare.com"],
    hmr: {
      host: "localhost",
      port: 3000,
    },
    watch: usePolling
      ? {
          interval: 250,
          usePolling: true,
        }
      : undefined,
    proxy: {
      "/api": {
        target: process.env.VITE_API_PROXY_TARGET ?? "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
});
