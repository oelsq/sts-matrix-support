import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // Proxy API calls to backend (adjust target as needed)
    proxy: {
      '/api': {
        target: process.env.VITE_BACKEND_URL || 'http://localhost:8010',
        changeOrigin: true,
        secure: false,
        rewrite: (path) => path.replace(/^\/api(?=\/|$)/, ''),
      },
    },
  },
});