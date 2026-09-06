// markupSpans.js — разбор набранного текста на «слова» и «символы разметки».
//
// ━━ ЗАЧЕМ ━━
// Просьба Влада (05.09.2026): «если с обоих сторон текста есть совпадающие символы
// которые его форматируют, сделай их полупрозрачными, как в дискорд». На присланном
// снимке Discord строка `*фаыфа* *вфввв`: у ЗАКРЫТОЙ пары звёздочки приглушены, у
// незакрытой (вторая) — обычные, потому что форматированием она пока не является.
//
// ━━ ПОЧЕМУ ЧИСТАЯ ФУНКЦИЯ, А НЕ КОД ВНУТРИ КОМПОНЕНТА ━━
// Поле ввода — обычная `<textarea>`, покрасить часть её содержимого нельзя в принципе.
// Приглушение рисует ЗЕРКАЛО под прозрачным текстом поля (см. ChatThread.vue), и вся
// сложность здесь — в разборе, а не в отрисовке. Разбор проверяется числами
// (`web/tests/markupSpans.test.mjs`), зеркало — глазами; смешав их в .vue, мы получили
// бы то, что нельзя проверить вовсе.
//
// ⚠️ РАЗМЕТКА ТА ЖЕ, ЧТО РИСУЕТ `markdownLite.js`. Расходиться им нельзя: приглушённая
// звёздочка обещает человеку, что текст станет курсивом, и обещание должно исполниться.
// Здесь перечислены только ПАРНЫЕ обёртки — блочная цитата `> ` парной не бывает, у неё
// нет «символов с обеих сторон», и приглушать в ней нечего.
//
// ⚠️ Порядок в списке ЗНАЧИМ: длинные маркеры проверяются раньше коротких, иначе `**`
// разберётся как две одиночные `*` и жирный будет выглядеть незакрытым курсивом.

/** Парные обёртки Markdown-lite, длинные — первыми. */
export const PAIRS = ['```', '***', '**', '__', '~~', '`', '*', '_']

/**
 * Разложить текст на куски: {text, dim}. `dim: true` — символы ЗАКРЫТОЙ пары.
 *
 * Правила:
 *  • пара считается закрытой, только когда найдена ВТОРАЯ такая же обёртка и между ними
 *    есть хоть один символ. `**` подряд — это не пустой жирный, а два обычных символа;
 *  • незакрытая обёртка остаётся обычным текстом: пока человек не дописал вторую,
 *    форматирования нет, и гасить нечего (ровно это видно на снимке Discord);
 *  • вложенность не разбираем: `**жирный и *курсив* внутри**` подсветит внешнюю пару, а
 *    внутренние звёздочки останутся текстом. Полный разбор здесь означал бы второй
 *    Markdown-парсер рядом с `markdownLite`, а расходятся такие пары всегда.
 */
export function markupSpans(text) {
  const src = String(text ?? '')
  const out = []
  let plain = ''
  let i = 0

  const flush = () => { if (plain) { out.push({ text: plain, dim: false }); plain = '' } }

  while (i < src.length) {
    const marker = PAIRS.find((p) => src.startsWith(p, i))
    if (marker) {
      // Ищем закрывающую — не вплотную (между обёртками обязан быть текст).
      const close = src.indexOf(marker, i + marker.length + 1)
      if (close !== -1) {
        flush()
        out.push({ text: marker, dim: true })
        out.push({ text: src.slice(i + marker.length, close), dim: false })
        out.push({ text: marker, dim: true })
        i = close + marker.length
        continue
      }
    }
    plain += src[i]
    i += 1
  }
  flush()
  return out
}

/**
 * Уже обёрнут ли ВЫДЕЛЕННЫЙ кусок этой парой — чтобы кнопка облачка показывала
 * состояние, а не только действие.
 *
 * Смотрим ДВА случая, и оба настоящие: человек мог выделить текст ВНУТРИ обёрток
 * (`**|жирный|**`) или ВМЕСТЕ с ними (`|**жирный**|`). Различать их обязательно —
 * иначе кнопка на одном и том же жирном тексте выглядела бы то нажатой, то нет,
 * в зависимости от того, как мышь зацепила края.
 */
export function selectionWrapped(text, start, end, marker) {
  const src = String(text ?? '')
  const sel = src.slice(start, end)
  if (!sel) return false
  const inside = src.slice(Math.max(0, start - marker.length), start) === marker
    && src.slice(end, end + marker.length) === marker
  const outside = sel.startsWith(marker) && sel.endsWith(marker)
    && sel.length > marker.length * 2
  return inside || outside
}

/**
 * Обернуть/снять обёртку у выделения. Возвращает новый текст и границы выделения в нём —
 * без них курсор прыгал бы в конец после каждой кнопки.
 *
 * ⚠️ Кнопка ПЕРЕКЛЮЧАЕТ, а не только добавляет. Иначе повторное нажатие давало бы
 * `****жирный****`, и починить это можно было бы только руками — то есть кнопка, которой
 * нельзя отменить саму себя.
 */
export function toggleWrap(text, start, end, marker) {
  const src = String(text ?? '')
  const sel = src.slice(start, end)
  const m = marker.length

  if (sel && src.slice(Math.max(0, start - m), start) === marker
      && src.slice(end, end + m) === marker) {
    return {
      text: src.slice(0, start - m) + sel + src.slice(end + m),
      start: start - m,
      end: end - m,
    }
  }
  if (sel.length > m * 2 && sel.startsWith(marker) && sel.endsWith(marker)) {
    const inner = sel.slice(m, -m)
    return { text: src.slice(0, start) + inner + src.slice(end), start, end: start + inner.length }
  }
  return {
    text: src.slice(0, start) + marker + sel + marker + src.slice(end),
    start: start + m,
    end: end + m,
  }
}
