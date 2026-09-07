// buildFreshness.test.mjs — «пора ли перезагрузить вкладку после деплоя».
//
// 🔥 ЖАЛОБА (Влад, 07.09.2026): «после каждого деплоя ломается веб на телефоне,
// показывается старая версия, а новую видно после перезагрузки страницы».
//
// Правило умеет отказать МОЛЧА В ОБЕ СТОРОНЫ, и обе дороги плохие:
//   • не перезагрузиться, когда сборка сменилась → тот самый дефект;
//   • перезагружаться при каждой проверке → вкладка мигает бесконечно, и это ХУЖЕ
//     исходной жалобы, потому что работать становится нельзя вовсе.
// Поэтому проверяем не «функция что-то вернула», а обе границы поимённо.
import test from 'node:test'
import assert from 'node:assert/strict'
import { shouldReload, isChunkLoadError } from '../src/utils/buildFreshness.js'

test('сборка сменилась — перезагружаемся', () => {
  assert.equal(shouldReload('100', '200', ''), true)
})

test('та же сборка — не трогаем вкладку', () => {
  assert.equal(shouldReload('100', '100', ''), false)
})

test('🔒 ради этой метки уже перезагружались — второй раз НЕ идём', () => {
  // Ровно защита от вечного круга: сервер может продолжать отдавать старое (кэш прокси,
  // недокатившийся деплой), и без этого условия человек получил бы мигающий экран.
  assert.equal(shouldReload('100', '200', '200'), false)
  // А вот СЛЕДУЮЩАЯ сборка должна пройти: замок держит одну метку, а не все.
  assert.equal(shouldReload('100', '300', '200'), true)
})

test('🔒 незнание — не повод перезагружаться', () => {
  // Пустая серверная метка = «не спросили» либо «нет связи». Перезагрузка в офлайне
  // выкинула бы человека из работающего кабинета в пустой экран.
  assert.equal(shouldReload('100', '', ''), false)
  assert.equal(shouldReload('', '200', ''), false)
})

test('ошибку загрузки чанка узнаём во ВСЕХ формулировках браузеров', () => {
  // Единого кода ошибки нет, текст у всех свой. Проверяем живые формулировки.
  for (const msg of [
    'Failed to fetch dynamically imported module: https://esstu-gradebook.ru/assets/x.js',
    'error loading dynamically imported module',
    'Importing a module script failed.',
    'Loading chunk 42 failed.',
  ]) {
    assert.equal(isChunkLoadError(new Error(msg)), true, `не узнали: ${msg}`)
  }
})

test('обычную ошибку роутера за устаревший чанк НЕ принимаем', () => {
  // Иначе любая ошибка навигации превращалась бы в перезагрузку — и человек терял бы
  // работу на ровном месте.
  for (const msg of ['Network Error', 'Navigation aborted', 'Redirected when going from /a to /b']) {
    assert.equal(isChunkLoadError(new Error(msg)), false, `ложное срабатывание: ${msg}`)
  }
  assert.equal(isChunkLoadError(null), false)
  assert.equal(isChunkLoadError(undefined), false)
})
