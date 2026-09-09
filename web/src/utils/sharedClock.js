/**
 * sharedClock.js — ОДНИ ЧАСЫ НА ВСЕ КАРТОЧКИ, А НЕ СВОИ У КАЖДОЙ.
 *
 * ━━ ЗАЧЕМ (B7 разбора 07.09.2026) ━━
 * `ActivityCard.vue` и `PollMessage.vue` заводили `setInterval(..., 1000)` БЕЗУСЛОВНО —
 * в том числе завершённой активности и закрытому опросу, где значение уже никогда не
 * изменится. Таймеры снимались корректно, но их число росло вместе с числом карточек в
 * ленте: пролистал историю на полсотни сообщений — полсотни таймеров, каждый будит
 * реактивность Vue раз в секунду. На телефоне это батарея, на слабой машине — кадры.
 *
 * ━━ ДВА ПРАВИЛА, И ВТОРОЕ ВАЖНЕЕ ПЕРВОГО ━━
 * 1. таймер В ПРОЦЕССЕ ОДИН, сколько бы карточек ни подписалось;
 * 2. карточка, которой часы не нужны, НЕ ПОДПИСАНА ВОВСЕ — и потому не перерисовывается.
 *
 * ⚠️ Второе правило — причина, по которой подписчик получает СВОЙ `ref`, а не общий.
 * Общий выглядит проще и был первым вариантом, но тогда полсотни завершённых карточек
 * продолжали бы читать тикающее значение и пересчитывать свои `computed` каждую секунду.
 * Таймер бы стал один, а работы осталось бы ровно столько же — то есть починка была бы
 * видимой в коде и отсутствующей на экране.
 *
 * ⚠️ В скрытой вкладке часы СТОЯТ (то же правило, что у опроса мессенджера: §B4). При
 * возвращении делается немедленный тик — иначе подпись «идёт 03:11» осталась бы той,
 * что была в момент сворачивания, и человек увидел бы неверное время до следующей
 * секунды. Возврат чинит значение сразу, а не «почти сразу».
 */
import { ref, watch, onScopeDispose } from 'vue'

/** Период тика. Секунда — минимальная единица, которую показывают подписи. */
export const TICK_MS = 1000

const subscribers = new Set()
let timer = null
let visibilityBound = false

function isHidden() {
  return typeof document !== 'undefined' && !!document.hidden
}

function fire() {
  const t = Date.now()
  //Копия набора: подписчик вправе отписаться прямо в обработчике (карточка опроса
  //ровно так и делает, когда срок вышел), а правка Set во время обхода теряет соседа.
  for (const fn of [...subscribers]) {
    try { fn(t) } catch { /* один сломанный подписчик не останавливает часы */ }
  }
}

function startTimer() {
  if (timer || !subscribers.size || isHidden()) return
  timer = setInterval(fire, TICK_MS)
  //Не даём таймеру держать процесс живым (важно для тестов и для node-окружения).
  if (typeof timer === 'object' && timer && typeof timer.unref === 'function') timer.unref()
}

function stopTimer() {
  if (!timer) return
  clearInterval(timer)
  timer = null
}

function onVisibility() {
  if (isHidden()) { stopTimer(); return }
  fire()          //догоняем время, пропущенное в фоне
  startTimer()
}

function bindVisibility() {
  if (visibilityBound || typeof document === 'undefined') return
  document.addEventListener('visibilitychange', onVisibility)
  visibilityBound = true
}

function unbindVisibility() {
  if (!visibilityBound || typeof document === 'undefined') return
  document.removeEventListener('visibilitychange', onVisibility)
  visibilityBound = false
}

/**
 * Подписаться на общий тик. Возвращает функцию отписки (идемпотентную).
 * Первый подписчик заводит таймер, последний — снимает.
 */
export function subscribe(fn) {
  subscribers.add(fn)
  bindVisibility()
  startTimer()
  let released = false
  return () => {
    if (released) return
    released = true
    subscribers.delete(fn)
    if (!subscribers.size) { stopTimer(); unbindVisibility() }
  }
}

/**
 * Часы, которые идут ТОЛЬКО пока `active` истинно.
 *
 * `active` — ref, computed или функция. Возвращается СВОЙ ref времени: пока карточка
 * неактивна, он не меняется вовсе, значит и перерисовки нет.
 */
export function useSharedNow(active) {
  const now = ref(Date.now())
  let release = null

  const stop = () => { if (release) { release(); release = null } }
  const read = () => {
    if (typeof active === 'function') return !!active()
    if (active && typeof active === 'object' && 'value' in active) return !!active.value
    return !!active
  }

  watch(read, (on) => {
    if (on) {
      if (!release) { now.value = Date.now(); release = subscribe((t) => { now.value = t }) }
    } else stop()
  }, { immediate: true })

  onScopeDispose(stop)
  return now
}

/** Только для тестов: сколько сейчас подписчиков и жив ли таймер. */
export function _state() {
  return { subscribers: subscribers.size, running: timer !== null, visibilityBound }
}
