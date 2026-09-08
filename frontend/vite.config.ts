import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // Bind to all interfaces (equivalent to `vite --host`), not just
    // localhost, so other machines on the warehouse LAN can reach this dev
    // server. See the "LAN deployment" section in README.md.
    host: true,
    // Forwards /api/* to the backend so the frontend can call relative
    // paths (see API_BASE in src/api.ts) in every environment -- dev, LAN,
    // and the single-service production deployment alike. This also means
    // only this port (5173) needs to be reachable on the LAN; the backend
    // itself only ever needs to answer this same machine's proxy, not the
    // network directly.
    proxy: {
      "/api": {
        target: "http://localhost:8010",
        changeOrigin: true,
      },
    },
  },
});
