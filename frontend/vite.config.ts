import path from 'path';
import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, '.', '');
  return {
    plugins: [tailwindcss(), react()],
    server: {
      port: 3000,
      host: '0.0.0.0',
      proxy: {
        // Always proxy to local backend; ignore VITE_API_URL here so absolute
        // env values don't break the /api → :5001 hop.
        '/api': {
          target: 'http://127.0.0.1:5001',
          changeOrigin: true,
          secure: false,
          // Analyzer Manager / LY pivots can exceed the default proxy idle timeout.
          timeout: 180_000,
          proxyTimeout: 180_000,
        },
      },
    },
    define: {
      'process.env.API_KEY': JSON.stringify(env.GEMINI_API_KEY),
      'process.env.GEMINI_API_KEY': JSON.stringify(env.GEMINI_API_KEY)
    },
    resolve: {
      alias: {
        '@': path.resolve(__dirname, '.'),
      }
    }
  };
});
