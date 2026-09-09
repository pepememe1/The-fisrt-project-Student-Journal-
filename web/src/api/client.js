/**
 * client.js — HTTP-клиент к тому же REST API, что и десктоп (порт идей sync_client).
 *
 * База пустая ('') → запросы идут на ТОТ ЖЕ origin, что и сайт. На бою Caddy отдаёт
 * SPA в корне домена и проксирует /auth, /me, /web, ... в FastAPI — поэтому «адрес
 * сервера = адрес сайта» выполняется само собой, и CORS не нужен (same-origin).
 * В dev проксирование делает vite.config.js. Переопределить базу можно через
 * VITE_API_BASE (например, чтобы фронт смотрел на удалённый сервер).
 *
 * Интерсепторы:
 *   • запрос — подставляют Authorization: Bearer <access>, X-Device-Id, X-Client: web;
 *   • ответ  — на 401 один раз пытаются тихо обновить access через /auth/refresh
 *     (как десктопный клиент), затем повторяют исходный запрос. Не вышло — сигналим
 *     «сессия истекла» (обработчик регистрирует App → редирект на /login).
 */
import axios from 'axios'
import { getAccess, getRefresh, setTokens, clearTokens, getDeviceId } from './tokens'
import { isCacheable, writeCache, readCache, servingStale } from './offlineCache'
import { noteOffline, noteOnline } from './offlineSession'
import { getApiBase, isNativeApp } from './server'

/**
 * Чем мы представляемся серверу: 'android' в приложении, 'web' в браузере.
 *
 * Отличие нужно ровно для ОДНОГО: у мобильного приложения свой, недельный потолок
 * сессии вместо пятичасового (см. server/app/config.py::session_ttl_min). Телефон
 * личный и заперт блокировкой экрана, а общий компьютер в аудитории — нет, и там
 * пятичасовой потолок и есть основная защита.
 *
 * Прав заголовок не даёт: сервер записывает его ОДИН РАЗ при входе в auth_sessions
 * и дальше уже не перечитывает — подделав его в браузере, можно получить только
 * собственную новую сессию, но не растянуть чужую или уже выданную.
 */
function clientKind() {
  return isNativeApp() ? 'android' : 'web'
}

export const api = axios.create({
  baseURL: getApiBase(),
  timeout: 20000,
})

// Обработчик «сессия истекла» регистрирует App (чтобы не тянуть сюда router и не плодить циклы).
let authExpiredHandler = null
//Обработчик «админу нужен второй фактор». Ставит оболочка приложения.
let mfaSetupHandler = null
/**
 * Клиент БЕЗ интерсепторов — для публичных ручек, которым токен не нужен.
 *
 * ⚠️ Отдельный экземпляр, а не `api`, и это принципиально: у основного клиента на 401
 * стоит обновление токена и выброс на экран входа. Публичный запрос, прошедший через
 * него, при любой неудаче выкидывал бы из аккаунта человека, который в нём и не был, —
 * а публичные страницы существуют ровно для тех, кто НЕ вошёл.
 *
 * Базовый адрес берём тем же способом (`getApiBase()`): внутри программы и в мобильном
 * приложении он задаётся в рантайме, и зашивать его нельзя.
 */
export const rawApi = axios.create({ timeout: 20000 })
rawApi.interceptors.request.use((config) => {
  config.baseURL = getApiBase()
  return config
})

export function setAuthExpiredHandler(fn) {
  authExpiredHandler = fn
}

/** Куда сообщать, что администратору нужно завести второй фактор. */
export function setMfaSetupHandler(fn) {
  mfaSetupHandler = fn
}

api.interceptors.request.use((config) => {
  // Адрес сервера берём динамически на КАЖДЫЙ запрос: в приложении он задаётся в
  // рантайме (экран подключения), поэтому не фиксируем его при создании клиента.
  config.baseURL = getApiBase()
  const token = getAccess()
  if (token) config.headers.Authorization = `Bearer ${token}`
  config.headers['X-Device-Id'] = getDeviceId()
  config.headers['X-Client'] = clientKind()
  return config
})

// Чтобы параллельные запросы не стартовали несколько refresh разом — общий промис.
let refreshing = null

/**
 * 🔥 ПОКОЛЕНИЕ СЕССИИ (07.09.2026).
 *
 * Дефект, ради которого заведено. `doRefresh` брал refresh-токен, УХОДИЛ В СЕТЬ и по
 * возвращении записывал новые токены БЕЗУСЛОВНО. Ветка ошибки так же безусловно их
 * стирала. А между отправкой и ответом человек мог выйти и войти под другим аккаунтом —
 * на общем компьютере колледжа это не экзотика, а обычный день.
 * Сценарий: запрос аккаунта A задержался, вошёл B. Поздний УСПЕХ A перезаписывает токены
 * B (человек продолжает работу под чужой сессией — и права, и данные чужие); поздний
 * ОТКАЗ A выбрасывает B из аккаунта посреди выставления оценок.
 *
 * Лечится не «проверить логин» — логин к моменту ответа уже другой, и сравнивать не с
 * чем, — а НОМЕРОМ ПОКОЛЕНИЯ. Записывать токены, стирать их и повторять запрос вправе
 * только то поколение, которое этот refresh начало.
 *
 * ⚠️ Счётчик двигает КАЖДАЯ смена владельца сессии: и вход, и выход. Двигать только на
 * входе недостаточно — «вышел и не вошёл» это тоже другая сессия, и дописывать в неё
 * чужие токены нельзя.
 */
let sessionGen = 0

/** Позвать при входе И при выходе: всё, что было в полёте, теперь чужое. */
export function bumpSessionGeneration() {
  sessionGen += 1
  //Промис прежнего поколения больше никого не обслуживает: пусть следующий 401 начнёт
  //свой refresh, а не ждёт чужой ответ, который всё равно будет отброшен.
  refreshing = null
  return sessionGen
}

/** Только для тестов: текущее поколение. */
export function _sessionGeneration() { return sessionGen }

async function doRefresh() {
  const refresh = getRefresh()
  if (!refresh) throw new Error('no refresh token')
  const gen = sessionGen
  // Голый axios (без интерсепторов), чтобы не зациклиться на 401.
  const { data } = await axios.post(
    getApiBase() + '/auth/refresh',
    { refresh_token: refresh },
    { headers: { 'X-Device-Id': getDeviceId(), 'X-Client': clientKind() } },
  )
  if (gen !== sessionGen) {
    // Пока мы ходили в сеть, сессию сменили. Наши токены принадлежат ПРЕЖНЕМУ человеку;
    // записать их сейчас значит подменить сессию тому, кто вошёл после.
    throw new StaleSessionError()
  }
  setTokens({ access: data.access_token, refresh: data.refresh_token || refresh })
  return data.access_token
}

/** Отличает «сессию сменили» от «сервер отказал»: реакции на них ПРОТИВОПОЛОЖНЫЕ. */
class StaleSessionError extends Error {
  constructor() {
    super('session changed while refreshing')
    this.name = 'StaleSessionError'
    this.stale = true
  }
}

// Успешный ответ: кэшируем role-scoped READ (GET /web/*, /me/prefs) для оффлайна.
function isGet(config) { return (config?.method || 'get').toLowerCase() === 'get' }

api.interceptors.response.use(
  (resp) => {
    const config = resp.config || {}
    // Сервер ответил — значит связь есть, и отсчёт суточного окна офлайна
    // начинается заново (см. offlineSession.js).
    noteOnline()
    if (isGet(config) && isCacheable(config.url)) {
      writeCache(config, resp.data)
      servingStale.value = false     // пришли свежие данные — мы онлайн
    }
    return resp
  },
  async (error) => {
    const { response, config } = error
    // Ответа нет вовсе (сеть недоступна/таймаут) — это офлайн. Ответ с любым кодом,
    // даже 500, наоборот означает, что сервер на связи: различать обязательно, иначе
    // одна серверная ошибка запускала бы отсчёт окна офлайна на исправной сети.
    if (!response) noteOffline()
    // Для кэшируемых GET отдаём СОХРАНЁННОЕ — экран показывает данные, а не пустоту.
    // Обновятся, как только вернётся сеть.
    if (!response && config && isGet(config) && isCacheable(config.url)) {
      const hit = readCache(config)
      if (hit) {
        servingStale.value = true
        return Promise.resolve({
          data: hit.data, status: 200, statusText: 'OK (offline cache)',
          headers: { 'x-gb-stale': '1' }, config, request: null, cached: true,
        })
      }
    }
    // Администратор без второго фактора: сервер отвечает 403 на ВСЁ, кроме его
    // настройки (см. deps._require_second_factor_setup). Без этой ветки человек
    // увидел бы россыпь «нет прав» на исправных страницах и пошёл бы искать, кто
    // отобрал у него доступ, — вместо настройки за минуту. Заголовок для того и
    // машиночитаемый.
    if (response?.status === 403 && response.headers?.['x-gb-reason'] === 'mfa_setup_required') {
      if (mfaSetupHandler) mfaSetupHandler(response.data?.detail || '')
      return Promise.reject(error)
    }
    if (!response || response.status !== 401 || config._retried) {
      return Promise.reject(error)
    }
    // Логин/refresh сами по себе не рефрешим — иначе бесконечный цикл.
    if (config.url?.includes('/auth/login') || config.url?.includes('/auth/refresh')) {
      return Promise.reject(error)
    }
    const gen = sessionGen
    try {
      if (!refreshing) refreshing = doRefresh().finally(() => { refreshing = null })
      const newAccess = await refreshing
      if (gen !== sessionGen) return Promise.reject(error)   // повтор — уже не наш
      config._retried = true
      config.headers.Authorization = `Bearer ${newAccess}`
      return api(config)
    } catch (e) {
      // ⚠️ «Сессию сменили» и «сервер отказал» здесь РАЗНЫЕ события, и раньше оба
      // заканчивались `clearTokens()`. То есть запоздавший отказ по СТАРОМУ аккаунту
      // выбрасывал из программы того, кто вошёл после него.
      if (e?.stale || gen !== sessionGen) return Promise.reject(error)
      clearTokens()
      if (authExpiredHandler) authExpiredHandler()
      return Promise.reject(error)
    }
  },
)
