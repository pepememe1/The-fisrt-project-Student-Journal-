/*
 * sw.js — Service worker для PWA-режима (устанавливаемое приложение + офлайн-оболочка).
 *
 * Стратегии:
 *   • API (/auth, /web, /me, /sync, /admin, /connect, /health) — ТОЛЬКО сеть, никогда
 *     не кэшируем: это живые данные и авторизация (кэш тут = утечка/устаревание ПДн).
 *   • Навигация (открытие страниц SPA) — network-first: свежий index.html, а офлайн —
 *     отдаём закэшированную оболочку, чтобы приложение открылось без сети.
 *   • Статика (/assets/* с хэшем в имени) — cache-first: файлы неизменяемы, берём из
 *     кэша мгновенно, чего нет — догружаем и кладём.
 *   • Тяжёлый арт (/easter/*, /mascot/*) — тоже cache-first, см. MEDIA_PREFIXES ниже.
 */
// ⚠️ Подняли до v4 (07.09.2026): заодно с меткой сборки выметаем накопившиеся копии
// СТАРЫХ чанков. Они безвредны (имена с хешем не пересекаются), но их некому убирать —
// список `/assets/*` растёт от деплоя к деплою и занимает место на телефоне.
const VERSION = 'gb-v4'   // bump очищает старый (возможно «отравленный» HTML-заглушкой) кэш
const SHELL = `${VERSION}-shell`
const ASSETS = `${VERSION}-assets`
const APP_SHELL = ['/', '/index.html', '/favicon.svg', '/manifest.webmanifest']

// Пути API — их запросы проходят мимо кэша.
const API_PREFIXES = ['/auth', '/me', '/sync', '/admin', '/connect', '/web', '/health', '/docs', '/openapi.json']

// 🔥 ТЯЖЁЛЫЙ НЕИЗМЕНЯЕМЫЙ АРТ — CACHE-FIRST (28.08.2026, просьба Ярослава «пасхалки много
// весят, желательно их кешировать»).
//
// Раньше эти файлы попадали в общую ветку «прочая статика», а она NETWORK-FIRST: браузер
// шёл в сеть КАЖДЫЙ раз и лишь при неудаче брал копию. Для пасхалок это худший из
// возможных выборов — один только фоновый шум офиса весит 2.4 МБ, и он перекачивался при
// каждом срабатывании, хотя файл не менялся с момента появления.
//
// ⚠️ Cache-first здесь безопасен ровно потому, что арт неизменяем ПО СМЫСЛУ: звук и
// картинка пасхалки не правятся, а заменяются новым файлом. У маскота, который всё-таки
// пересобирают, в адресе стоит `?v=ART_VERSION` (см. config/mascot.js) — при пересборке
// меняется адрес, то есть ключ кэша, и старая копия не мешает.
//
// ⚠️ Сюда НЕ входит `/activity/` — снимки экранов активностей меняются вместе с
// интерфейсом, и версии в адресе у них нет; пусть остаются network-first.
const MEDIA_PREFIXES = ['/easter/', '/mascot/', '/fonts/']

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(SHELL).then((c) => c.addAll(APP_SHELL)).then(() => self.skipWaiting()))
})

self.addEventListener('activate', (event) => {
  // Чистим старые версии кэша при обновлении приложения.
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => !k.startsWith(VERSION)).map((k) => caches.delete(k)))
    ).then(() => self.clients.claim())
  )
})

self.addEventListener('fetch', (event) => {
  const { request } = event
  if (request.method !== 'GET') return // POST/PUT/DELETE (вход, запись) — всегда сеть

  const url = new URL(request.url)
  if (url.origin !== self.location.origin) return // сторонние (шрифты) — как есть

  // API — только сеть, без кэша.
  if (API_PREFIXES.some((p) => url.pathname.startsWith(p))) return

  // 🔄 Метка сборки — ВСЕГДА мимо кэша. Её единственный смысл в том, чтобы отличаться от
  // той, с которой вкладка запустилась (см. src/utils/appVersion.js); отданная из кэша,
  // она всегда равна себе самой — то есть проверка обновления перестала бы работать,
  // причём молча и именно после деплоя, ровно когда нужна.
  if (url.pathname === '/version.json') return

  // Навигация — network-first с офлайн-фолбэком на оболочку SPA.
  if (request.mode === 'navigate') {
    event.respondWith(
      fetch(request)
        .then((resp) => {
          const copy = resp.clone()
          caches.open(SHELL).then((c) => c.put('/index.html', copy))
          return resp
        })
        .catch(() => caches.match('/index.html').then((r) => r || caches.match('/')))
    )
    return
  }

  // Хешированные ассеты (/assets/*) и тяжёлый неизменяемый арт — cache-first.
  if (url.pathname.startsWith('/assets/')
      || MEDIA_PREFIXES.some((p) => url.pathname.startsWith(p))) {
    event.respondWith(
      caches.match(request).then((hit) =>
        hit ||
        fetch(request).then((resp) => {
          if (resp.ok && resp.type === 'basic') {
            const copy = resp.clone()
            caches.open(ASSETS).then((c) => c.put(request, copy))
          }
          return resp
        })
      )
    )
    return
  }

  // Прочая статика (картинки маскота, иконки) — NETWORK-FIRST. В кэш кладём ТОЛЬКО
  // успешный НЕ-HTML ответ (картинку), никогда HTML-заглушку SPA-фолбэка — иначе кэш
  // «отравляется» и вместо PNG отдаётся страница → битая картинка. Офлайн — из кэша.
  event.respondWith(
    fetch(request)
      .then((resp) => {
        const ct = resp.headers.get('content-type') || ''
        if (resp.ok && resp.type === 'basic' && !ct.includes('text/html')) {
          const copy = resp.clone()
          caches.open(ASSETS).then((c) => c.put(request, copy))
        }
        return resp
      })
      .catch(() => caches.match(request))
  )
})
