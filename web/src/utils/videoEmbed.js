/**
 * videoEmbed.js — извлечение видео-эмбеда из ссылки мессенджера (Фаза 1,
 * docs/done/MESSENGER-ATTACHMENTS-PLAN.md §2/§6/§9).
 *
 * БЕЛЫЙ СПИСОК хостов (YouTube/VK Video/Rutube) — id ролика достаётся regex'ом из самой
 * ссылки, сервер НИКУДА не ходит за превью. Это закрывает SSRF архитектурно: серверу
 * незачем разбирать произвольный URL, а нераспознанные ссылки остаются обычным
 * кликабельным текстом (см. markdownLite.js) без всякого встраивания.
 */

const PATTERNS = [
  {
    provider: 'youtube',
    re: /(?:youtube\.com\/watch\?(?:[^\s#]*&)?v=|youtube\.com\/shorts\/|youtu\.be\/)([a-zA-Z0-9_-]{6,})/,
    embed: (id) => `https://www.youtube.com/embed/${id}`,
  },
  {
    provider: 'vk',
    re: /vk\.com\/video(-?\d+_\d+)/,
    embed: (id) => {
      const [oid, vid] = id.split('_')
      return `https://vk.com/video_ext.php?oid=${oid}&id=${vid}&hd=2`
    },
  },
  {
    provider: 'rutube',
    re: /rutube\.ru\/video\/([a-zA-Z0-9]+)/,
    embed: (id) => `https://rutube.ru/play/embed/${id}`,
  },
]

/** @returns {{provider:string,id:string,embedUrl:string,sourceUrl:string}|null} */
export function detectVideo(url) {
  const clean = String(url ?? '').trim()
  if (!/^https?:\/\//i.test(clean)) return null
  for (const p of PATTERNS) {
    const m = p.re.exec(clean)
    if (m) return { provider: p.provider, id: m[1], embedUrl: p.embed(m[1]), sourceUrl: clean }
  }
  return null
}

/** Ссылки из СЫРОГО текста сообщения → распознанные видео (без дублей по url). */
export function extractVideos(text) {
  const urls = String(text ?? '').match(/https?:\/\/[^\s<>]+/g) || []
  const seen = new Set()
  const out = []
  for (const raw of urls) {
    const clean = raw.replace(/[.,!?;:)\]}]+$/, '')
    if (seen.has(clean)) continue
    const v = detectVideo(clean)
    if (v) { seen.add(clean); out.push(v) }
  }
  return out
}

/**
 * Как показывать распознанное видео ЗДЕСЬ: встроенным плеером или ссылкой наружу.
 *
 * В браузере (сайт) — плеер: там чужой фрейм ограничен и `sandbox`, и заголовками
 * Caddy (`frame-src` белым списком), и самим origin-барьером браузера.
 *
 * В мобильном приложении — ТОЛЬКО ссылка. Причина не в CSP (её там нет вовсе:
 * страница отдаётся из бандла по `https://localhost`, заголовки сервера туда не
 * доезжают), а в нативных мостах: `addJavascriptInterface` в MainActivity.java
 * вешает объект БЕЗ привязки к origin — у Google для origin-ограничения заведён
 * отдельный `addWebMessageListener` с `allowedOriginRules`. За мостом лежат токен
 * устройства и `setEndpoint()` виджета — адрес, по которому виджет рабочего стола
 * потом сам ходит за расписанием месяцами, без токена и без запущенного приложения.
 * Пока мосты не переехали на origin-ограниченный API, чужому фрейму внутри APK
 * места нет.
 *
 * ⚠️ Аргумент обязателен по смыслу: пропущенный/неизвестный признак трактуется как
 * «мы в приложении». Ошибка вызова должна вести к БОЛЕЕ осторожному поведению.
 *
 * @param {boolean} native — мы внутри нативного приложения (`isNativeApp()`).
 * @returns {'iframe'|'link'}
 */
export function embedMode(native) {
  return native === false ? 'iframe' : 'link'
}
