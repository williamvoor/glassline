/// <reference types="vitest/config" />
import { defineConfig } from 'vite';

// The Python server serves the built UI and the API on one origin. In development, Vite proxies
// /api to it, so there is still one origin and no CORS.
export default defineConfig({
  build: { target: 'es2022' },
  server: {
    host: '127.0.0.1',
    port: 5174,
    strictPort: true,
    proxy: { '/api': { target: 'http://127.0.0.1:8765', changeOrigin: true } },
  },
  test: { environment: 'happy-dom', testTimeout: 30_000 },
});
