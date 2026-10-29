import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// `npm run build` bundles the app into the Python package, which serves it and ships it in the
// wheel. `npm run dev` serves it with hot reload and sends /api to `uv run loci-app`.
export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": "http://127.0.0.1:8765" } },
  build: { outDir: "../src/loci/web/static", emptyOutDir: true, chunkSizeWarningLimit: 6000 },
});
