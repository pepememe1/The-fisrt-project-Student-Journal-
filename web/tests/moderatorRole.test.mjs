/**
 * moderatorRole.test.mjs — сторожа роли `moderator` на клиенте (11.09.2026).
 *
 * ━━ ЗАЧЕМ ━━
 * Новая роль на клиенте ломается ТИХО и в обе стороны:
 *
 * • пункт меню БЕЗ маршрута — человек нажимает и попадает на 404 (у нас так уже было:
 *   у админа не было `/admin/profile`, а на него безусловно ссылались карточка себя и
 *   шапка, см. комментарий в router/index.js);
 * • маршрут БЕЗ пункта меню — раздел существует, но войти в него нельзя ничем, кроме
 *   набранного руками адреса (наш `RAILLESS_VIEWS`);
 * • роль, которой нет в `ROLE_PREFIXES`, ломает разбор промаха: `/moderator/что-то`
 *   считался бы «адресом без роли», то есть чужой раздел молча вёл бы на главную
 *   вместо честного «вам сюда нельзя».
 *
 * Ни сборка, ни линтер ничего из этого не видят: файлы валидны, ошибок нет.
 *
 * ⚠️ ОБРАТНЫЙ ХОД ПРОВЕРЕН: убрать `moderation` из NAV.moderator — краснеет первый тест;
 * убрать ветку `/moderator` из роутера — краснеет он же; убрать 'moderator' из
 * ROLE_PREFIXES — краснеет второй; изменить набор сроков в MuteDialog — краснеет третий.
 */
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { NAV, HOME_BY_ROLE } from '../src/config/nav.js'
import { ROLE_PREFIXES, decideMiss } from '../src/utils/missedRoute.js'

const read = (rel) => readFileSync(
  new URL(rel, import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1'), 'utf8')

const router = read('../src/router/index.js')

test('у каждого пункта меню модератора есть маршрут, и наоборот', () => {
  const nav = NAV.moderator
  assert.ok(Array.isArray(nav) && nav.length, 'ветка меню модератора не заведена')

  //Адреса пунктов (секции пропускаем — у них нет `to`).
  const navPaths = nav.filter((i) => i.to).map((i) => i.to)
  assert.ok(navPaths.includes('/moderator'), 'модерация обязана быть первым пунктом')

  //Блок маршрутов ветки `/moderator` — от её объявления до следующей роли.
  const start = router.indexOf("path: '/moderator'")
  assert.ok(start > 0, 'ветка /moderator отсутствует в роутере')
  const block = router.slice(start, router.indexOf('// РОДИТЕЛЬ', start))

  for (const p of navPaths) {
    const tail = p.replace('/moderator', '').replace(/^\//, '')
    //Корень ветки объявлен как `path: ''`.
    const needle = tail ? `'${tail}'` : "path: ''"
    assert.ok(block.includes(needle), `пункт меню «${p}» ведёт в никуда: маршрута нет`)
  }

  //Дом роли обязан существовать — иначе после входа человека некуда отправить.
  assert.equal(HOME_BY_ROLE.moderator, '/moderator')
})

test('промах по адресу разбирается для модератора как для своей роли', () => {
  assert.ok(ROLE_PREFIXES.includes('moderator'),
            'без префикса чужой раздел молча вёл бы на главную вместо 404')
  //Свой раздел с опечаткой — обычный промах, домой.
  assert.equal(decideMiss('/moderator/опечатка', 'moderator'), 'home')
  //Чужой раздел — «вам сюда нельзя», а не «такой страницы нет».
  assert.equal(decideMiss('/admin/students', 'moderator'), 'notFound')
  //И наоборот: студенту в модерацию нельзя.
  assert.equal(decideMiss('/moderator', 'student'), 'notFound')
})

test('сроки ограничения в диалоге совпадают с теми, что знает сервер', () => {
  //🔑 Список сроков ЖИВЁТ В ДВУХ МЕСТАХ по необходимости: сервер обязан проверять
  //границы сам (кнопки обходятся), клиент обязан их предлагать. Сослаться друг на друга
  //им нечем — поэтому расхождение ловит этот тест, а не «договорённость».
  const dialog = read('../src/components/messenger/MuteDialog.vue')
  const py = read('../../server/app/routers/messenger/moderation.py')

  const client = /const PRESETS = \[([^\]]+)\]/.exec(dialog)
  assert.ok(client, 'в MuteDialog не найден список готовых сроков')
  const server = /MUTE_PRESET_HOURS = \(([^)]+)\)/.exec(py)
  assert.ok(server, 'на сервере не найден MUTE_PRESET_HOURS')

  const nums = (s) => s.split(',').map((x) => x.trim()).filter(Boolean).map(Number)
  assert.deepEqual(nums(client[1]), nums(server[1]),
                   'наборы сроков разошлись: человек выберет то, чего сервер не примет')
})

test('бессрочного ограничения в диалоге нет', () => {
  //Наказание «пока не снимут» снимать некому — о замьюченном перестают вспоминать.
  //Сервер такое и не примет, но кнопка, которая всегда отвечает отказом, хуже её
  //отсутствия: человек будет считать, что сломалось.
  const dialog = read('../src/components/messenger/MuteDialog.vue')
  const template = dialog.slice(dialog.indexOf('<template>'))
  assert.ok(!/бессроч/i.test(template), 'в диалоге предлагается бессрочный вариант')
  assert.ok(/ticketClosed|ticket-closed/.test(dialog),
            'кнопка обязана гаснуть при закрытом тикете — иначе подсказки нет вовсе')
})
