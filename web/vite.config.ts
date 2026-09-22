/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The control-plane API t16 serves (POST /api/voice/start|stop,
// POST /api/mic/mute, GET /api/status, GET /api/events for the SSE
// projection of embodiment/bus.py). Same-origin in production once t16
// serves `dist/` itself; proxied in dev so the browser never needs CORS.
const API_TARGET = process.env.EMBODIMENT_API ?? "http://127.0.0.1:8787";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": {
        target: API_TARGET,
        changeOrigin: true,
      },
    },
    // Component tests import the committed bus-event fixtures directly
    // from ../tests/fixtures/events (outside web/) so the dashboard and
    // embodiment/bus.py validate against the identical files (t13's
    // contract) rather than a duplicated copy that could drift.
    fs: { allow: [".."] },
  },
  preview: {
    host: "127.0.0.1",
    port: 4173,
    strictPort: true,
  },
  build: {
    outDir: "dist",
    // No external origin may appear in the build output (acceptance #3):
    // sourcemaps embed absolute source paths, never a remote URL, but keep
    // them off in the shipped build to keep dist/ minimal and self-contained.
    sourcemap: false,
  },
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: ["./vitest.setup.ts"],
    css: false,
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
