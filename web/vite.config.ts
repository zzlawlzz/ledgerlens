import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Dev proxy mirrors the nginx production config: UI never knows the API origin.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 3000,
    proxy: {
      "/api": "http://localhost:8000",
      "/agui": "http://localhost:8000",
    },
  },
});
