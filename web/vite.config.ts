import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Dev proxy mirrors the nginx production config: UI never knows the API origin.
export default defineConfig({
  // "/" for local dev and the single-origin nginx build; the Pages build sets
  // VITE_BASE=/app/ so assets resolve under the subpath.
  base: process.env.VITE_BASE || "/",
  plugins: [react()],
  server: {
    port: 3000,
    proxy: {
      "/api": "http://localhost:8000",
      "/agui": "http://localhost:8000",
    },
  },
});
