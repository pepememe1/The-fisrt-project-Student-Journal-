// loginSelfServiceOff.test.mjs — на экране входа нет самообслуживания.
//
// 🔑 ТРЕБОВАНИЕ (12.09.2026, Влад): «на сайте ВСГУТУ нет кнопки регистрации и подобного,
// только вход по выданному логину и паролю… не удаляй, а сделай невидимыми и выключи
// кликабельность, чтобы не вызвать обходным путём окошки».
//
// ⚠️ ПОЧЕМУ ЭТО НЕ «ПРОВЕРКА ВЁРСТКИ». Спрятать кнопку — не защита: ref остаётся живым, и
// любая будущая строка `showRegister = true` снова покажет окно, причём молча. Поэтому
// сторож требует ТРИ замка сразу и краснеет, если убрали хотя бы один.
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, existsSync } from 'node:fs'

const root = new URL('../../', import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1')
const PAGE = readFileSync(root + 'web/src/pages/LoginPage.vue', 'utf8')

test('выключатель один и он выключен', () => {
  // Два флага (или флаг, размазанный по разметке) разошлись бы молча: половина экрана
  // считала бы регистрацию включённой, половина — выключенной.
  assert.ok(/const SELF_SERVICE_ENABLED = false/.test(PAGE),
    'нет единственного выключателя SELF_SERVICE_ENABLED = false')
  assert.equal((PAGE.match(/const SELF_SERVICE_ENABLED/g) || []).length, 1,
    'выключатель обязан быть ровно один')
})

test('замок 1: кнопок нет в разметке — их не видно, не нажать и не поймать Tab-ом', () => {
  const row = PAGE.slice(PAGE.indexOf("loc.t('login.audience')"))
    .slice(0, PAGE.indexOf("loc.t('login.accountHelp") + 400)
  assert.ok(/<div v-if="SELF_SERVICE_ENABLED"[^>]*>\s*\n\s*<button/.test(PAGE),
    'ряд кнопок регистрации/восстановления обязан стоять под v-if="SELF_SERVICE_ENABLED"')
  assert.ok(row.includes("loc.t('login.register')"), 'кнопка регистрации не должна быть УДАЛЕНА')
  assert.ok(row.includes("loc.t('login.recover')"), 'кнопка восстановления не должна быть УДАЛЕНА')
})

test('замок 2: сами окна тоже под флагом — обходной путь ничего не покажет', () => {
  for (const dlg of ['RegisterDialog', 'RecoverDialog']) {
    const re = new RegExp('<' + dlg + ' v-if="SELF_SERVICE_ENABLED && show')
    assert.ok(re.test(PAGE),
      `${dlg} обязан рисоваться только при SELF_SERVICE_ENABLED: подняли ref в обход — ` +
      'показывать нечего')
  }
})

test('замок 3: открывают окна только функции, и они отказывают', () => {
  for (const fn of ['openRegister', 'openRecover']) {
    const at = PAGE.indexOf('function ' + fn + '(')
    assert.ok(at > 0, `нет функции ${fn}`)
    const body = PAGE.slice(at, at + 160)
    assert.ok(/if \(!SELF_SERVICE_ENABLED\) return/.test(body),
      `${fn} обязана отказывать первой же строкой`)
  }
  // И ни одного прямого подъёма ref из разметки в обход этих функций.
  assert.ok(!/@click="show(Register|Recover) = true"/.test(PAGE),
    'ref поднимается прямо из разметки — это и есть обходной путь')
})

test('код НЕ удалён: обе модалки на месте и работоспособны', () => {
  // Прямое условие Влада. Политика приёма студентов — решение заказчика, и оно может
  // смениться обратно; удалив код, мы превратили бы одну строку в новый заход.
  for (const f of ['web/src/components/RegisterDialog.vue', 'web/src/components/RecoverDialog.vue']) {
    assert.ok(existsSync(root + f), `${f} удалён — а его просили оставить`)
  }
  assert.ok(PAGE.includes("import RegisterDialog from '@/components/RegisterDialog.vue'"))
  assert.ok(PAGE.includes("import RecoverDialog from '@/components/RecoverDialog.vue'"))
})

test('подсказка не отправляет студента к несуществующей кнопке', () => {
  // «Нет учётных данных? Преподавателю или родителю — обратитесь к администратору»
  // подразумевало, что студент заводит аккаунт сам. Теперь не заводит никто.
  assert.ok(PAGE.includes('login.accountHelpIssued'),
    'при выключенном самообслуживании подсказка обязана называть настоящий порядок')
  assert.ok(/v-if="SELF_SERVICE_ENABLED"[^>]*>\{\{ loc\.t\('login\.forStudents'\)/.test(PAGE),
    'подпись «Для обучающихся:» обязана гаснуть вместе с кнопками — иначе она указывает в пустоту')
})

test('обратный ход: прежняя редакция обязана считаться незащищённой', () => {
  // Дословно то, как было написано до правки. Сторож, зелёный и на дефекте, бесполезен.
  const before = [
    '<button type="button" @click="showRegister = true">Регистрация</button>',
    '<RegisterDialog v-if="showRegister" @close="showRegister = false" />',
  ].join('\n')
  assert.ok(/@click="show(Register|Recover) = true"/.test(before),
    'разбор обязан видеть прямой подъём ref')
  assert.ok(!/<RegisterDialog v-if="SELF_SERVICE_ENABLED && show/.test(before),
    'разбор обязан видеть незащищённую модалку')
})
