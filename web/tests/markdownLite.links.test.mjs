// markdownLite.links.test.mjs — ссылки (Фаза 1, docs/done/MESSENGER-ATTACHMENTS-PLAN.md).
//
// Не парный Python-контракт (сервер markdown не рендерит) — обычный юнит-тест на
// автолинковку и XSS-безопасность результата.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { renderMarkdownLite } from '../src/utils/markdownLite.js'

test('голый http(s) URL становится ссылкой с data-external-link', () => {
  const html = renderMarkdownLite('смотри https://youtu.be/dQw4w9WgXcQ тут')
  assert.match(html, /<a href="https:\/\/youtu\.be\/dQw4w9WgXcQ" data-external-link="https:\/\/youtu\.be\/dQw4w9WgXcQ" rel="noopener noreferrer">/)
})

test('[текст](url) даёт ссылку с текстом, а не голым адресом', () => {
  const html = renderMarkdownLite('см. [расписание](https://example.com/sched)')
  assert.match(html, /<a href="https:\/\/example\.com\/sched"[^>]*>расписание<\/a>/)
})

test('висящая пунктуация с хвоста ссылки НЕ входит в href', () => {
  const html = renderMarkdownLite('ссылка (https://a.b/x).')
  assert.match(html, /<a href="https:\/\/a\.b\/x"[^>]*>https:\/\/a\.b\/x<\/a>\)\./)
})

test('URL внутри code-спана не превращается в ссылку', () => {
  const html = renderMarkdownLite('`https://a.b/секрет`')
  assert.doesNotMatch(html, /<a /)
  assert.match(html, /<code>https:\/\/a\.b\/[^<]*<\/code>/)
})

test('& в URL остаётся валидной HTML-сущностью (&amp;), а не разрывает атрибут', () => {
  const html = renderMarkdownLite('https://a.b/?x=1&y=2')
  assert.match(html, /href="https:\/\/a\.b\/\?x=1&amp;y=2"/)
})

test('текст сообщения "C0"/"L0" не путается с внутренними плейсхолдерами', () => {
  // Раньше плейсхолдеры были обычными буквами (C0/L0) — реальное сообщение с такой
  // подстрокой могло бы подставить чужой code/ссылку. Управляющие символы это исключают.
  const html = renderMarkdownLite('код `x` и текст C0 и ссылка https://a.b/ и L0 тоже')
  assert.match(html, /текст C0 и ссылка/)
  assert.match(html, /и L0 тоже/)
  assert.equal((html.match(/<code>/g) || []).length, 1)
  assert.equal((html.match(/<a /g) || []).length, 1)
})

test('ссылка НЕ содержит target="_blank" — переход перехватывает ChatThread.vue', () => {
  const html = renderMarkdownLite('https://a.b/')
  assert.doesNotMatch(html, /target=/)
})

test('XSS: javascript: URL в [текст](url) не проходит как http(s)', () => {
  const html = renderMarkdownLite('[кликни](javascript:alert(1))')
  assert.doesNotMatch(html, /<a /)
})

test('обычный текст без ссылок не меняется', () => {
  assert.equal(renderMarkdownLite('просто текст без ссылок'), 'просто текст без ссылок')
})
