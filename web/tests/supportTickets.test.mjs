/**
 * supportTickets.test.mjs — обращения в модерацию: кнопки тем у человека и очередь
 * тикетов у модератора (12.09.2026).
 *
 * ━━ ЧТО ЗДЕСЬ ЗАЩИЩАЕТСЯ ━━
 * Обе половины ломаются ТИХО и выглядят при этом работающими:
 * • автоответчик перечисляет темы ТЕКСТОМ. Пропадут кнопки — человек всё равно увидит
 *   список и сможет написать ответ словами, то есть ровно тот лишний круг переписки,
 *   ради снятия которого автоответчик и заведён. Ни ошибки, ни пустого экрана;
 * • очередь у модератора обязана ставить срочные НАВЕРХ, и порядок задаёт сервер.
 *   Клиентская пересортировка не сломает ни один экран — она просто уведёт «Позовите
 *   человека» в середину списка, и заметить это можно только сравнив два экрана.
 *
 * ⚠️ Комментарии вырезаются ПЕРЕД разбором: этот файл и шапки компонентов объясняют, как
 * выглядел дефект, и разбор, считающий пояснение кодом, краснел бы на исправном файле —
 * то есть заставлял бы вычеркнуть урок (наш приём из publicScheduleContract).
 */
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const WEB = new URL('../', import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1')
const ROOT = new URL('../../', import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1')

const read = (p) => readFileSync(p, 'utf8')

/** Убрать комментарии обоих языков, не тронув содержимое строк. */
function stripComments(src) {
  return src
    .replace(/<!--[\s\S]*?-->/g, '')
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/(^|[^:'"`\\])\/\/[^\n]*/g, '$1')
}

const thread = stripComments(read(WEB + 'src/components/messenger/ChatThread.vue'))
const admin = stripComments(read(WEB + 'src/pages/admin/AdminMessenger.vue'))
const endpoints = stripComments(read(WEB + 'src/api/endpoints.js'))
const server = read(ROOT + 'server/app/routers/messenger/moderation.py')

test('у человека есть КНОПКИ тем, а не только текст автоответчика', () => {
  assert.match(thread, /supportCategories\(\)/,
    'список тем не запрашивается — кнопкам неоткуда взяться')
  assert.match(thread, /pickSupportCategory\(/, 'нажатие темы никуда не уходит')
  assert.match(thread, /v-for="c in support\.categories"/,
    'кнопки не рисуются по списку с сервера')
  assert.match(thread, /@click="pickSupportCategory\(c\.code\)"/,
    'у кнопки темы нет обработчика — она выглядит рабочей и ничего не делает')
})

test('список тем НЕ продублирован на клиенте', () => {
  // Две копии закрытого списка расходятся молча и всегда: человек выберет тему, которой
  // сервер не знает, и получит 400 на нажатие собственной кнопки.
  const codes = ['harassment', 'spam', 'content', 'account', 'mute']
  const hits = codes.filter((c) => new RegExp(`['"]${c}['"]`).test(thread))
  assert.deepEqual(hits, [],
    'коды тем зашиты в компонент — список обязан приходить с сервера одним источником')
})

test('дверь наружу видна: «Другое» и «Позвать человека» приходят тем же списком', () => {
  // 🔴 Робот, из которого нельзя выйти к человеку, превращает поддержку в тупик. Дверь
  // обязана быть в ТОМ ЖЕ наборе кнопок, а не отдельной строчкой, которую легко потерять.
  assert.match(server, /\{"code": "other",/, 'тема «Другое» пропала из списка сервера')
  assert.match(server, /\{"code": "human",/, 'тема «Позвать человека» пропала из списка')
  assert.match(server, /URGENT_CATEGORIES = \{[^}]*"other"[^}]*"human"[^}]*\}/,
    'дверь наружу перестала быть срочной — она встанет в общую очередь')
})

test('поле ввода в чате модерации остаётся рабочим при показе кнопок', () => {
  // Анкета, из которой нельзя выйти текстом, — тот же тупик. Кнопки стоят НАД композером
  // отдельным блоком, а не вместо него.
  const picker = thread.indexOf('v-if="showSupportPicker"')
  assert.ok(picker > 0, 'блок выбора темы не найден')
  const composer = thread.indexOf('v-if="canPost"')
  assert.ok(composer > picker,
    'композер не идёт следом за кнопками — проверь, не заменили ли его анкетой')
})

test('очередь модератора НЕ пересортировывается на клиенте', () => {
  // Порядок задаёт сервер: очередь читают разные экраны, и «срочное наверху» обязано
  // означать на всех одно и то же.
  const from = admin.indexOf('const inboxRows = computed(')
  assert.ok(from > 0, 'сборка строк очереди не найдена')
  const body = admin.slice(from, admin.indexOf('\n})', from))
  assert.ok(!/\.sort\(/.test(body), 'очередь пересортировывается на клиенте')
  assert.match(admin, /messengerModApi\.support\(/, 'очередь тикетов не запрашивается вовсе')
})

test('переписка без тикета не выпадает из очереди', () => {
  // Тикеты появились 12.09.2026 — у заведённых раньше обращений их нет. Очередь, которая
  // показывает только тикеты, молча спрятала бы такие обращения целиком.
  const from = admin.indexOf('const inboxRows = computed(')
  const body = admin.slice(from, admin.indexOf('\n})', from))
  assert.match(body, /covered\.has\(c\.conversation_id\)/,
    'беседы без тикета не добавляются в очередь')
  assert.match(body, /ticket: null/, 'строка без тикета не заводится')
})

test('срочное обращение помечено в самой строке, а не только порядком', () => {
  // Порядок виден, только когда список длинный. Пометка обязана читаться в одной строке:
  // модератор смотрит на очередь секунду, а не изучает её.
  assert.match(admin, /row\.ticket\?\.urgent \? 'border-red/,
    'срочная строка ничем не отличается от обычной')
  assert.match(admin, /v-if="row\.ticket\?\.urgent"/, 'нет значка срочности в строке')
})

test('«взять в работу» и «закрыть» есть и вызывают сервер', () => {
  assert.match(admin, /@click="claimTicket\(row\.ticket\)"/, 'нет кнопки «взять в работу»')
  assert.match(admin, /@click="resolveTicket\(row\.ticket\)"/, 'нет кнопки закрытия')
  assert.match(admin, /messengerModApi\.claimSupport\(/, 'кнопка «взять» никуда не ходит')
  assert.match(admin, /messengerModApi\.resolveSupport\(/, 'кнопка закрытия никуда не ходит')
})

test('адреса ручек совпадают с серверными', () => {
  // Поле, которого нет в ответе, читается молча (наш урок с расписанием без входа);
  // адрес, которого нет на сервере, отдаёт заглушку SPA и тоже не падает.
  const paths = [
    ['/web/messenger/moderation/categories', '@router.get("/moderation/categories")'],
    ['/web/messenger/moderation/category', '@router.post("/moderation/category")'],
    ['/web/admin/messenger/support', '@mod_router.get("/support")'],
  ]
  for (const [client, decl] of paths) {
    assert.ok(endpoints.includes(client), `клиент не зовёт ${client}`)
    assert.ok(server.includes(decl), `на сервере нет ручки для ${client}`)
  }
  assert.match(endpoints, /support\/\$\{id\}\/claim/, 'нет вызова claim')
  assert.match(server, /@mod_router\.post\("\/support\/\{tid\}\/claim"\)/, 'нет ручки claim')
  assert.match(endpoints, /support\/\$\{id\}\/resolve/, 'нет вызова resolve')
  assert.match(server, /@mod_router\.post\("\/support\/\{tid\}\/resolve"\)/, 'нет ручки resolve')
})

test('обратный ход: разбор видит дословную форму каждого дефекта', () => {
  // Сторож, зелёный и без починки, неотличим от исправного кода.
  const sorted = 'const inboxRows = computed(() => {\n  return rows.sort((a) => a)\n})'
  const body = sorted.slice(sorted.indexOf('computed('), sorted.indexOf('\n})'))
  assert.ok(/\.sort\(/.test(body), 'пересортировку на клиенте разбор не заметил бы')

  const hardcoded = stripComments("const CATS = [{ code: 'spam' }]")
  assert.ok(/['"]spam['"]/.test(hardcoded), 'зашитый список тем разбор не заметил бы')

  // И проверка самого stripComments: он обязан убирать пояснения, но не трогать строки.
  const mixed = stripComments("// про 'spam' в комментарии\nconst a = 'spam'")
  assert.ok(!mixed.includes('комментарии'), 'комментарии не вырезаются')
  assert.ok(mixed.includes("'spam'"), 'вырезано лишнее — задета строка кода')
})
