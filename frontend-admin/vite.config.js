import { defineConfig } from 'vite';
import vue from '@vitejs/plugin-vue';

// base 绝对路径：页面 URL 不带尾斜杠，相对路径会被解析到 /api/assets 导致白屏
export default defineConfig({
  base: '/api/kanban-admin/',
  plugins: [vue()],
  build: {
    outDir: '../backend/static/kanban_admin',
    emptyOutDir: true
  },
  server: {
    port: 5180,
    // 开发期代理到本地 Django（构建后无需）
    proxy: {
      '/api': 'http://127.0.0.1:8000'
    }
  }
});
