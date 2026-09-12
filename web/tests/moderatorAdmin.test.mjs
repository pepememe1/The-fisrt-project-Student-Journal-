/**
 * moderatorAdmin.test.mjs — раздел «Модераторы» в админке (12.09.2026).
 *
 * ━━ ЗАЧЕМ ━━
 * Требование Влада: «пароль модератора по аналогии с паролем админа» — то есть заводить
 * и перевыдавать его из продукта, а не консольным скриптом на боевой машине.
 *
 * Ломается такое ТИХО и в трёх местах сразу:
 * • пункт меню без маршрута — нажал и попал на 404;
 * • маршрут без пункта — раздел есть, войти нечем;
 * • минимум длины пароля разошёлся с сервером — админ вводит пароль, сервер его не
 *   принимает, и виноватой выглядит страница.
 *
 * ⚠️ ОБРАТНЫЙ ХОД ПРОВЕРЕН: сменить `MIN_PASSWORD` на странице — краснеет третий тест;
 * убрать пункт из NAV.admin — первый; убрать маршрут — он же; отдать пароль наружу в
 * ответе сервера — четвёртый.
 */
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { NAV } from '../src/config/nav.js'

const read = (rel) => readFileSync(
  new URL(rel, import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1'), 'utf8')

const page = read('../src/pages/admin/AdminModerators.vue')
const router = read('../src/router/index.js')
const endpoints = read('../src/api/endpoints.js')
const serverWrite = read('../../server/app/routers/web/write.py')
const serverSecurity = read('../../server/app/security.py')

test('пункт меню и маршрут заведены вместе', () => {
  const paths = NAV.admin.filter((i) => i.to).map((i) => i.to)
  assert.ok(paths.includes('/admin/moderators'), 'нет пункта «Модераторы» в меню админа')
  assert.ok(/page\('moderators',/.test(router), 'нет маршрута /admin/moderators')
})

test('раздел живёт у админа и НИ У КОГО больше', () => {
  // 🔒 Модератор не заводит модераторов. Пункт в его меню был бы обещанием, которое
  // сервер всё равно отклонит, — а человек решит, что продукт сломан.
  for (const role of ['moderator', 'teacher', 'student', 'parent']) {
    const paths = (NAV[role] || []).filter((i) => i.to).map((i) => i.to)
    assert.ok(!paths.includes('/admin/moderators'),
      `раздел модераторов попал в меню роли ${role}`)
  }
})

test('минимум длины пароля совпадает с серверным', () => {
  // ⚠️ Копии ДВЕ по необходимости: сервер обязан проверять границу сам (кнопку обходит
  // прямой запрос), клиент обязан её предлагать, а сослаться друг на друга им нечем.
  // Поэтому расхождение ловит этот тест, а не договорённость.
  const onPage = page.match(/const MIN_PASSWORD = (\d+)/)
  assert.ok(onPage, 'на странице нет константы MIN_PASSWORD')
  const onServer = serverSecurity.match(/^MIN_PASSWORD_LEN = (\d+)/m)
  assert.ok(onServer, 'на сервере нет MIN_PASSWORD_LEN')
  assert.equal(onPage[1], onServer[1],
    `страница требует ${onPage[1]} символов, сервер — ${onServer[1]}`)
})

test('сервер не отдаёт пароль наружу, а страница его не показывает', () => {
  // Наружу уходит признак «задан», а не хеш: иначе страница администратора становится
  // способом унести хеши на офлайн-перебор.
  const list = serverWrite.slice(serverWrite.indexOf('def admin_list_moderators'))
    .slice(0, serverWrite.slice(serverWrite.indexOf('def admin_list_moderators')).indexOf('@router'))
  assert.ok(/has_password/.test(list), 'список обязан отдавать признак, а не хеш')
  // ⚠️ Ищем хеш как КЛЮЧ ОТВЕТА, а не любое упоминание: функция обязана прочитать
  // `r.password_hash`, чтобы вывести булев признак, и запрет на само слово сделал бы
  // тест красным на правильном коде. Поведение всё равно закрыто серверным сторожем
  // (`test_the_list_never_leaks_the_hash`), который смотрит на живой ответ.
  assert.ok(!/["']password_hash["']\s*:/.test(list), 'в ответ списка попал хеш пароля')
  assert.ok(!/r\.password_hash/.test(page), 'страница читает хеш пароля')
})

test('пустое поле пароля НЕ отправляется — это «не менять»', () => {
  // Страница пароль не показывает и показать не может. Отправив пустую строку, мы
  // сказали бы «сделай пароль пустым» — человек молча потерял бы доступ.
  assert.ok(/password: pwValue\.value/.test(page), 'смена пароля не передаёт новое значение')
  // В правке имени пароль не участвует вовсе: ключа в теле нет.
  assert.ok(!/updateModerator\([^)]*full_name[^)]*password/.test(page),
    'правка имени тащит с собой пароль')
})

test('клиент зовёт ровно те адреса, что объявлены на сервере', () => {
  const called = [...endpoints.matchAll(/\/web\/admin\/moderators[^'`]*/g)].map((m) => m[0])
  assert.ok(called.length >= 4, `ожидали четыре вызова, нашли ${called.length}`)
  assert.ok(/@router\.get\("\/admin\/moderators"\)/.test(serverWrite))
  assert.ok(/@router\.post\("\/admin\/moderators"\)/.test(serverWrite))
  assert.ok(/@router\.put\("\/admin\/moderators\/\{login\}"\)/.test(serverWrite))
  assert.ok(/@router\.delete\("\/admin\/moderators\/\{login\}"\)/.test(serverWrite))
})

test('дверь на сервере админская, а не модераторская', () => {
  // Главная граница всей правки. Проверяем ТЕКСТ, потому что «просто поменять
  // зависимость» выглядит правкой одной строки.
  const raw = serverWrite.slice(serverWrite.indexOf('# --- МОДЕРАТОРЫ (CRUD)'),
                                serverWrite.indexOf('# --- Заявки на регистрацию'))
  assert.ok(raw.length > 100, 'не нашёл блок модераторов в write.py')
  // ⚠️ Комментарии вырезаем ДО разбора. Блок ОБЯЗАН объяснять, почему дверь не
  // модераторская, и первая же версия этого теста покраснела на собственном пояснении —
  // ровно тот случай, из-за которого сторож заставляет вычеркнуть урок. Та же починка,
  // что у сторожа контракта публичного расписания.
  const block = raw.split('\n').filter((l) => !l.trim().startsWith('#')).join('\n')
  assert.ok(!/require_moderation/.test(block),
    'дверь модераторская — модератор сможет завести себе подкрепление')
  assert.equal((block.match(/require_admin/g) || []).length, 4,
    'у каждой из четырёх ручек обязана быть админская дверь')
})
