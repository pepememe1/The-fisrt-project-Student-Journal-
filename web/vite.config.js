import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import tailwindcss from '@tailwindcss/vite'
import { fileURLToPath, URL } from 'node:url'
//Плагин лежит отдельным файлом, чтобы его можно было проверить тестом, не
//поднимая весь Vite (web/tests/stripHtmlComments.test.mjs).
import stripHtmlComments from './build/stripHtmlComments.js'
//Метка сборки: по ней открытая вкладка узнаёт, что её код устарел после
//деплоя (см. src/utils/appVersion.js). Тоже отдельным файлом — под тест.
import buildStamp from './build/buildStamp.js'

// Во время разработки фронтенд крутится на своём порту (5173), а API — на 8000.
// Чтобы в dev всё выглядело как ОДИН адрес (как на бою за Caddy), проксируем
// известные API-префиксы на локальный FastAPI. Тогда в коде везде относительные
// пути (/auth/login, /web/...), и на проде тот же код работает без правок.
const API_TARGET = process.env.VITE_API_TARGET || 'http://localhost:8000'
const API_PREFIXES = ['/auth', '/me', '/sync', '/admin', '/connect', '/web', '/health', '/docs', '/openapi.json']

export default defineConfig({
  plugins: [vue(), tailwindcss(), stripHtmlComments(), buildStamp()],
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  server: {
    proxy: Object.fromEntries(
      API_PREFIXES.map((p) => [p, { target: API_TARGET, changeOrigin: true }])
    ),
  },
})
