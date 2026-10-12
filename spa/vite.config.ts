import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev server proxies /api to the opal-event-svc REST server so the SPA can be developed
// standalone without CORS configuration on the backend.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: process.env.OPAL_EVENT_API_URL || "http://127.0.0.1:8080",
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "dist",
  },
});
