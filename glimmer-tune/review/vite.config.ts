import {defineConfig} from 'vite';

// `npm run dev` proxies the API to `python -m a2ui_eval.review` on its default port.
export default defineConfig({
  resolve: {dedupe: ['lit']},
  build: {target: 'esnext', rollupOptions: {input: {main: 'index.html', render: 'render.html'}}},
  server: {host: '127.0.0.1', proxy: {'/api': 'http://127.0.0.1:8765'}},
});
