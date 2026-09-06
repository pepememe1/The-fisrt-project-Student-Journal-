// highlightFragment.js — найти процитированный кусок в теле сообщения и подсветить его.
//
// ━━ ЗАЧЕМ ━━
// Просьба Влада (05.09.2026): «при нажатии [на цитату] перекинет к выделенному фрагменту
// и подсветит его». Перемотка у нас была и раньше (`jumpTo`), а вот подсветка ИМЕННО
// куска — нет: в плотной переписке центр экрана ни на что не указывает, и человек,
// перейдя по цитате, всё равно ищет глазами, ради какой строки его сюда привели.
//
// ━━ ПОЧЕМУ РАЗБОР ОТДЕЛЁН ОТ DOM ━━
// Тело сообщения — это HTML после Markdown-lite: искомая фраза почти всегда разорвана
// тегами (`<strong>`, `<a>`, `<br>`), и наивный `innerHTML.replace()` либо не найдёт её,
// либо разломает разметку. Поэтому ищем по ТЕКСТОВЫМ УЗЛАМ, а сама арифметика «в каком
// узле и с какого смещения лежит фраза» вынесена сюда чистой функцией и проверяется без
// браузера (`web/tests/highlightFragment.test.mjs`).
//
// ⚠️ Сравниваем по НОРМАЛИЗОВАННЫМ пробелам — тем же правилом, что сервер проверяет саму
// цитату (`messages._clean_quote`). Выделение мышью через перенос строки браузер отдаёт
// с пробелом вместо переноса, и точное совпадение не нашлось бы у совершенно честной
// цитаты. Правила по обе стороны обязаны совпадать: разойдутся — сервер цитату примет,
// а подсветка её «не найдёт», и выглядеть это будет как сломанный переход.

/** Пробелы к одному виду: перенос, таб и повтор — обычный пробел. */
export function flatten(s) {
  return String(s ?? '').replace(/\s+/g, ' ')
}

/**
 * Где лежит фраза в последовательности текстовых кусков.
 *
 * @param {string[]} chunks — тексты узлов по порядку
 * @param {string} needle — искомая фраза
 * @returns {{start:{index:number,offset:number}, end:{index:number,offset:number}}|null}
 *
 * Возвращаются координаты в ИСХОДНЫХ кусках (индекс узла + смещение внутри него) —
 * именно то, что нужно `Range` для выделения. Нормализация пробелов при этом не должна
 * сдвигать координаты, поэтому карта соответствия строится посимвольно, а не заменой
 * строки целиком: «два пробела подряд» в оригинале — это один символ в нормализованном
 * виде, и без карты подсветка уехала бы на этот символ вбок.
 */
export function findFragment(chunks, needle) {
  const want = flatten(needle).trim()
  if (!want) return null

  // Плоский текст + карта «позиция в плоском → (узел, смещение)».
  let flat = ''
  const map = []
  let prevSpace = true          // ведущие пробелы не переносим — как делает trim()
  for (let i = 0; i < chunks.length; i += 1) {
    const raw = String(chunks[i] ?? '')
    for (let j = 0; j < raw.length; j += 1) {
      const isSpace = /\s/.test(raw[j])
      if (isSpace) {
        if (prevSpace) continue
        flat += ' '
        map.push({ index: i, offset: j })
        prevSpace = true
        continue
      }
      flat += raw[j]
      map.push({ index: i, offset: j })
      prevSpace = false
    }
  }

  const at = flat.indexOf(want)
  if (at === -1) return null
  const from = map[at]
  const lastChar = map[at + want.length - 1]
  if (!from || !lastChar) return null
  return {
    start: { index: from.index, offset: from.offset },
    // Конец Range — ПОСЛЕ последнего символа, поэтому +1.
    end: { index: lastChar.index, offset: lastChar.offset + 1 },
  }
}

/** Все текстовые узлы элемента по порядку (для DOM-обёртки ниже). */
function textNodes(el) {
  const out = []
  const walk = document.createTreeWalker(el, NodeFilter.SHOW_TEXT)
  let node = walk.nextNode()
  while (node) { out.push(node); node = walk.nextNode() }
  return out
}

/**
 * Подсветить фразу внутри элемента на `ms` миллисекунд. Возвращает true, если нашли.
 *
 * ⚠️ Подсветка ВРЕМЕННАЯ и снимается сама. Постоянная осталась бы висеть после того, как
 * человек уже прочитал, и следующая перемотка к другому куску дала бы на экране два
 * «важных» места сразу.
 *
 * ⚠️ Не нашли — НЕ ошибка и не повод молчать в интерфейсе: оригинал могли отредактировать
 * после ответа. Возвращаем false, а вызывающий подсвечивает всё сообщение целиком —
 * перемотка всё равно привела туда, куда обещала.
 */
export function highlightIn(el, needle, ms = 2500) {
  if (!el || typeof document === 'undefined') return false
  const nodes = textNodes(el)
  const found = findFragment(nodes.map((n) => n.nodeValue), needle)
  if (!found) return false
  try {
    const range = document.createRange()
    range.setStart(nodes[found.start.index], found.start.offset)
    range.setEnd(nodes[found.end.index], found.end.offset)
    const mark = document.createElement('mark')
    mark.className = 'gb-quote-hit'
    //`surroundContents` бросает исключение, когда выделение пересекает границу тега
    //(«часть жирного и часть обычного») — законный случай. Тогда переносим содержимое
    //в mark вручную: результат тот же, ограничения нет.
    try { range.surroundContents(mark) } catch {
      mark.appendChild(range.extractContents())
      range.insertNode(mark)
    }
    setTimeout(() => {
      const parent = mark.parentNode
      if (!parent) return
      while (mark.firstChild) parent.insertBefore(mark.firstChild, mark)
      parent.removeChild(mark)
      parent.normalize()
    }, ms)
    return true
  } catch {
    return false
  }
}
