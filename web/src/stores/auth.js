/**
 * auth.js — стор авторизации (порт идей desktop sync_client login + saved_session).
 *
 * Как в десктопе: пароль НЕ храним. Персистентный вход — по сохранённым JWT
 * (access + refresh) и «визитке» пользователя (login/role/name), пришедшей в ответе
 * логина. Роль определяет доступные разделы (guard в router).
 */
import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { authApi } from '@/api/endpoints'
import { getAccess, setTokens, clearTokens } from '@/api/tokens'
//Поколение сессии: им отсекается запоздавший refresh ПРЕЖНЕГО аккаунта —
//см. подробный разбор в api/client.js. Двигать обязаны все три двери:
//вход (_afterLogin), выход (logout) и локальный сброс (clearSession).
import { bumpSessionGeneration } from '@/api/client'
import { clearCache } from '@/api/offlineCache'
import { clearDrafts } from '@/utils/drafts'
import { resetOfflineSession } from '@/api/offlineSession'
import { flushOutbox, reloadOutbox } from '@/api/outbox'
import { registerToken, unregisterToken } from '@/services/push'
import { clear as clearScheduleWidget, refreshFromServer as refreshWidgetSchedule,
  saveEndpoint as saveWidgetEndpoint } from '@/services/scheduleWidget'
import { loginWithPasskey } from '@/api/webauthn'
import { useMessengerStore } from '@/stores/messenger'
import { useVectorStore } from '@/stores/vector'
import { useProfileStore } from '@/stores/profile'
import { useEasterStore } from '@/stores/easterEggs'
import { useActivityStore } from '@/stores/activity'

const LS_USER = 'gb.user'

function loadUser() {
  try {
    const raw = localStorage.getItem(LS_USER)
    return raw ? JSON.parse(raw) : null
  } catch {
    return null
  }
}

export const useAuthStore = defineStore('auth', () => {
  const user = ref(loadUser())
  const loading = ref(false)
  const error = ref('')
  // Сколько секунд осталось до конца блокировки за перебор. Ноль — не заперты.
  const lockedFor = ref(0)

  //Незавершённый вход: пароль принят, ждём код. Живёт только в памяти вкладки —
  //класть challenge в localStorage нельзя, это половина пропуска, и переживать
  //перезагрузку она не должна.
  const mfaChallenge = ref('')
  const mfaLogin = ref('')
  //Когда окно подтверждения закроется — метка времени БРАУЗЕРА, посчитанная от
  //присланного сервером срока в секундах. Нужна ради обратного отсчёта на экране:
  //без него истечение выглядит как внезапный выброс на форму входа (жалоба Ярослава
  //03.09.2026, воспроизведена на бою).
  const mfaExpiresAt = ref(0)

  const isAuthenticated = computed(() => !!user.value && !!getAccess())
  const role = computed(() => user.value?.role || null)

  /**
   * Общий хвост УСПЕШНОГО входа — каким бы способом он ни произошёл.
   *
   * ⚠️ Раньше этот код был скопирован в парольный вход и во вход по биометрии, и
   * копии уже разошлись: у входящего по отпечатку виджет расписания не наполнялся
   * ВООБЩЕ и молча оставался пустым (это прямо описано в комментарии, который тут
   * стоял). Со вторым фактором появилась бы ТРЕТЬЯ копия — то есть третье место,
   * где однажды забудут строку. Поэтому одна функция.
   */
  function _afterLogin(data, loginStr) {
    //ПЕРВОЙ строкой, до записи токенов: всё, что улетело в сеть от прежнего человека,
    //с этого момента чужое и не имеет права ни дописать токены, ни стереть их.
    bumpSessionGeneration()
    setTokens({ access: data.access_token, refresh: data.refresh_token })
    user.value = {
      login: loginStr || data.login || '',
      role: data.role,
      name: data.name || loginStr || data.login || '',
    }
    localStorage.setItem(LS_USER, JSON.stringify(user.value))
    // Привязываем телефон к ЭТОМУ аккаунту: на одном устройстве могли входить
    // разные люди, и уведомления должны идти последнему вошедшему.
    registerToken()
    // Язык интерфейса аккаунта. Человек мог выбрать его на ДРУГОМ устройстве, и
    // заставлять выставлять заново — ровно та мелочь, из-за которой настройкой
    // перестают пользоваться.
    import('@/stores/locale')
      .then(({ useLocaleStore }) => useLocaleStore().loadFromAccount())
      .catch(() => { /* язык — не условие входа */ })
    // Виджет расписания на рабочем столе Android наполняем ИМЕННО ЗДЕСЬ: на страницу
    // «Расписание» человек может не заходить неделями, а вход случается гарантированно.
    // Адрес сервера — обязательно рядом со снимком, иначе нативная часть не знает, куда
    // ходить. Ошибку глотаем: виджет — дополнение, из-за него вход падать не должен.
    saveWidgetEndpoint()
    refreshWidgetSchedule(data.role).catch(() => {})
    // Очередь оценок принадлежит логину и пережила выход из аккаунта. Перечитываем её
    // под нового вошедшего и сразу пробуем отправить: сеть только что была — вход по
    // ней и произошёл, лучшего момента не будет.
    reloadOutbox()
    flushOutbox().catch(() => {})
    return user.value
  }

  async function login(login, password) {
    loading.value = true
    error.value = ''
    lockedFor.value = 0
    try {
      const { data } = await authApi.login(login.trim(), password)
      // ── Второй фактор ────────────────────────────────────────────────────────
      // Сервер ответил 200, но токенов НЕ ПРИСЛАЛ: пароль верен, нужен код.
      // Возвращаем это ВЫЗЫВАЮЩЕМУ, а не бросаем ошибку: для человека второй шаг
      // входа — не сбой, и экран должен показать поле для кода, а не красную плашку.
      if (data?.mfa_required) {
        mfaChallenge.value = data.challenge || ''
        mfaLogin.value = login.trim()
        //Срок берём у сервера, а не зашиваем: разъехались бы при первой же правке, и
        //отсчёт на экране показывал бы не то время, что действует на самом деле.
        mfaExpiresAt.value = Date.now() + (Number(data.expires_in) || 300) * 1000
        return { mfaRequired: true }
      }
      return _afterLogin(data, login.trim())
    } catch (e) {
      const status = e.response?.status
      //Секунды до разблокировки держим отдельно: по ним рисуется обратный отсчёт, и
      //только он превращает «подождите» в понятное «осталось столько-то».
      lockedFor.value = status === 429 ? Number(e.response?.headers?.['retry-after'] || 0) : 0
      if (status === 401) error.value = 'Неверный логин или пароль'
      else if (status === 403) error.value = e.response?.data?.detail || 'Устройство не подтверждено администратором'
      else if (status === 429) error.value = e.response?.data?.detail || 'Слишком много попыток входа. Подождите.'
      else error.value = 'Не удалось войти. Проверьте соединение с сервером.'
      throw e
    } finally {
      loading.value = false
    }
  }

  // Вход по passkey (Face ID / отпечаток) — без пароля. Токены и «визитку» ставим так
  // же, как при обычном входе; логин приходит в ответе (клиент его не вводил).
  async function loginPasskey() {
    loading.value = true
    error.value = ''
    try {
      const data = await loginWithPasskey('')
      return _afterLogin(data, data.login || '')
    } catch (e) {
      // Отмена пользователем (NotAllowedError/AbortError) — не ошибка, пробрасываем молча.
      if (e?.name === 'NotAllowedError' || e?.name === 'AbortError') throw e
      error.value = e.response?.data?.detail || 'Не удалось войти по биометрии. Войдите паролем.'
      throw e
    } finally {
      loading.value = false
    }
  }

  /**
   * Второй шаг входа: код из приложения или код восстановления.
   *
   * ⚠️ Логин берём из `mfaLogin`, а не из поля формы: к этому моменту человек уже
   * на другом экране, и поле может быть очищено.
   */
  async function verifyMfa(code) {
    if (!mfaChallenge.value) throw new Error('нет незавершённого входа')
    loading.value = true
    error.value = ''
    try {
      const { data } = await authApi.mfaVerify(mfaChallenge.value, String(code || '').trim())
      //🔥 ЗДЕСЬ НЕЛЬЗЯ ГАСИТЬ `mfaChallenge`, И ЭТО СТОИЛО НАСТОЯЩЕГО ДЕФЕКТА
      //(03.09.2026, жалоба Ярослава «ввожу код — в аккаунт не входит, а после
      //перезагрузки всё нормально»; воспроизведено на стенде).
      //
      //Окно ввода кода показывается по `v-if="auth.mfaChallenge"`. Обнуляя его ЗДЕСЬ,
      //мы приказывали Vue размонтировать компонент — а размонтирование успевает
      //случиться РАНЬШЕ, чем продолжится `await` у вызывающего: и то и другое живёт в
      //очереди микрозадач, и очередь перерисовки встаёт в неё первой. Дальше
      //`emit('done')` уходит от УЖЕ УДАЛЁННОГО компонента и не доходит ни до кого.
      //
      //Наружу это выглядело так: токены выданы (они кладутся строкой ниже), окно кода
      //исчезло, на экране снова форма входа — то есть «журнал меня выкинул». А после
      //F5 страж роутера видел живую сессию и открывал кабинет, из-за чего дефект
      //казался мистическим. Ни ошибки, ни следа в консоли при этом нет.
      //
      //Правило шире этого места: КОМПОНЕНТ НЕ ИМЕЕТ ПРАВА СНИМАТЬ САМ СЕБЯ С ЭКРАНА
      //до того, как сообщил о результате. Гасит незавершённый вход теперь вызывающий
      //(`LoginPage.onMfaDone` → `cancelMfa`), и делает это ПОСЛЕ того, как получил
      //управление.
      return _afterLogin(data, mfaLogin.value)
    } catch (e) {
      const status = e.response?.status
      if (status === 401) {
        //Срок challenge вышел — возвращаем человека к паролю ЯВНО. Иначе он будет
        //вводить коды в поле, которое уже ничего не значит.
        mfaChallenge.value = ''
        mfaExpiresAt.value = 0
        error.value = 'Время подтверждения истекло — войдите заново'
      } else if (status === 429) {
        error.value = e.response?.data?.detail || 'Слишком много попыток. Подождите.'
      } else {
        error.value = e.response?.data?.detail || 'Код не подошёл'
      }
      throw e
    } finally {
      loading.value = false
    }
  }

  function cancelMfa() {
    mfaChallenge.value = ''
    mfaLogin.value = ''
    mfaExpiresAt.value = 0
    error.value = ''
  }

  async function logout() {
    // Отписываем устройство ДО гашения токена: после clearTokens запрос уже не пройдёт,
    // и бывший владелец телефона продолжал бы получать чужие уведомления.
    await unregisterToken()
    // Гасим токен и на сервере (чёрный список), а не только локально — безопасный выход.
    try { await authApi.logout() } catch { /* офлайн — всё равно чистим локально */ }
    //«Вышел и не вошёл» — это ТОЖЕ другая сессия: запоздавший успешный refresh иначе
    //вернул бы в хранилище живые токены человека, который только что вышел.
    bumpSessionGeneration()
    clearTokens()
    localStorage.removeItem(LS_USER)
    clearCache()   // стираем оффлайн-кэш — чтобы данные не показались другому юзеру
    // ⚠️ И НЕДОПИСАННЫЕ сообщения. Раньше эта строка отсутствовала, а карта черновиков
    // ключевалась только id беседы — то есть следующий вошедший на этом телефоне видел
    // чужой текст в поле ввода общего канала. Ключ теперь с логином (utils/drafts.js),
    // но уборка всё равно обязательна: выход — это момент, когда устройство меняет
    // владельца. Держит web/tests/drafts.test.mjs.
    clearDrafts()
    useMessengerStore().reset()   // и переписку в памяти — тем же соображением
    useVectorStore().reset()      // и диалог с Вектором: он тоже жил в памяти store
    useProfileStore().reset()     // и профиль (аватар/«о себе»/цвет/шрифт) — та же причина
    // Активность живёт ВНЕ страницы (плавающее окно поверх RouterView) и переживает смену
    // пользователя в той же вкладке: без сброса следующий вошедший увидел бы чужую
    // викторину поверх своего кабинета.
    useActivityStore().reset()
    //И пасхалки: иначе тост «достижение открыто» от прошлого человека
    //всплывает на экране входа у следующего (см. easterEggs.js::reset).
    useEasterStore().reset()
    // Виджет на рабочем столе Android живёт ВНЕ страницы и переживает выход: без этой
    // строки он продолжал бы показывать группу предыдущего владельца сессии тому, кто
    // войдёт следом (на телефоне в колледже это норма, а не редкость).
    clearScheduleWidget()
    // Отсчёт суточного окна офлайна начинаем с нуля: оно принадлежит сессии, а не
    // устройству. Иначе следующий вошедший унаследовал бы чужой почти истёкший счётчик
    // и получил бы «войдите заново» через десять минут после входа.
    // ⚠️ Очередь неотправленных оценок (outbox) при этом НЕ трогаем — она привязана к
    // логину автора и обязана пережить выход: иначе преподаватель, вышедший из
    // аккаунта до появления сети, потеряет всё, что выставил.
    resetOfflineSession()
    user.value = null
  }

  // Локальная очистка сессии без обращения к серверу (напр. при протухшем refresh).
  function clearSession() {
    bumpSessionGeneration()
    clearTokens()
    localStorage.removeItem(LS_USER)
    clearCache()
    clearDrafts()      //та же причина, что в logout — см. комментарий выше
    useMessengerStore().reset()
    useVectorStore().reset()
    useProfileStore().reset()
    useActivityStore().reset()
    //И пасхалки: иначе тост «достижение открыто» от прошлого человека
    //всплывает на экране входа у следующего (см. easterEggs.js::reset).
    useEasterStore().reset()
    clearScheduleWidget()
    // Отсчёт суточного окна офлайна начинаем с нуля: оно принадлежит сессии, а не
    // устройству. Иначе следующий вошедший унаследовал бы чужой почти истёкший счётчик
    // и получил бы «войдите заново» через десять минут после входа.
    // ⚠️ Очередь неотправленных оценок (outbox) при этом НЕ трогаем — она привязана к
    // логину автора и обязана пережить выход: иначе преподаватель, вышедший из
    // аккаунта до появления сети, потеряет всё, что выставил.
    resetOfflineSession()
    user.value = null
  }

  return { user, loading, error, lockedFor, isAuthenticated, role, login, loginPasskey,
           mfaChallenge, mfaExpiresAt, verifyMfa, cancelMfa, logout, clearSession }
})
