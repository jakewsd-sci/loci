import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// `npm run dev` serves the app with hot reload and sends /api to `uv run loci-app`.
export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": "http://127.0.0.1:8765" } },
  build: { outDir: "dist", chunkSizeWarningLimit: 6000 },
});
