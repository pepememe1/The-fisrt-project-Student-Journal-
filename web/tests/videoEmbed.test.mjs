// videoEmbed.test.mjs — извлечение видео-эмбеда по белому списку (Фаза 1,
// docs/done/MESSENGER-ATTACHMENTS-PLAN.md §2/§6/§9). Чистая функция, без сети — id ролика
// достаётся regex'ом из самой ссылки (сервер туда никогда не ходит).
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { detectVideo, extractVideos } from '../src/utils/videoEmbed.js'

test('YouTube watch-ссылка распознаётся', () => {
  const v = detectVideo('https://www.youtube.com/watch?v=dQw4w9WgXcQ')
  assert.equal(v.provider, 'youtube')
  assert.equal(v.id, 'dQw4w9WgXcQ')
  assert.equal(v.embedUrl, 'https://www.youtube.com/embed/dQw4w9WgXcQ')
})

test('youtu.be короткая ссылка распознаётся', () => {
  const v = detectVideo('https://youtu.be/dQw4w9WgXcQ')
  assert.equal(v.provider, 'youtube')
  assert.equal(v.id, 'dQw4w9WgXcQ')
})

test('YouTube Shorts распознаётся', () => {
  const v = detectVideo('https://youtube.com/shorts/abcDEF12345')
  assert.equal(v.provider, 'youtube')
  assert.equal(v.id, 'abcDEF12345')
})

test('VK Video распознаётся (частный случай отрицательного oid — группа)', () => {
  const v = detectVideo('https://vk.com/video-123456_789')
  assert.equal(v.provider, 'vk')
  assert.equal(v.embedUrl, 'https://vk.com/video_ext.php?oid=-123456&id=789&hd=2')
})

test('Rutube распознаётся', () => {
  const v = detectVideo('https://rutube.ru/video/abcd1234efgh5678/')
  assert.equal(v.provider, 'rutube')
  assert.equal(v.embedUrl, 'https://rutube.ru/play/embed/abcd1234efgh5678')
})

test('нераспознанный хост — null (обычная кликабельная ссылка, без эмбеда)', () => {
  assert.equal(detectVideo('https://vimeo.com/12345'), null)
  assert.equal(detectVideo('https://example.com/'), null)
})

test('нет протокола http(s) — null (не открываем произвольные схемы)', () => {
  assert.equal(detectVideo('javascript:alert(1)'), null)
  assert.equal(detectVideo('vk.com/video-1_2'), null)
})

test('extractVideos достаёт видео из сырого текста сообщения, режет висящую пунктуацию', () => {
  const vids = extractVideos('глянь https://youtu.be/dQw4w9WgXcQ. и вот ещё https://example.com/x')
  assert.equal(vids.length, 1)
  assert.equal(vids[0].id, 'dQw4w9WgXcQ')
})

test('extractVideos не дублирует одну и ту же ссылку дважды', () => {
  const vids = extractVideos('https://youtu.be/dQw4w9WgXcQ и снова https://youtu.be/dQw4w9WgXcQ')
  assert.equal(vids.length, 1)
})
