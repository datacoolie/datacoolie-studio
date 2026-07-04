import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "../src/datacoolie_studio/static",
    emptyOutDir: true
  },
  server: {
    proxy: {
      "/api/v1": "http://127.0.0.1:8765"
    }
  }
});
