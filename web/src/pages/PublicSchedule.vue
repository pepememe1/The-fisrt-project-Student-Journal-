<script setup>
/**
 * PublicSchedule.vue — расписание БЕЗ ВХОДА В АККАУНТ.
 *
 * 🔥 ЗАЧЕМ (01.09.2026, прямая просьба Ярослава): «из акка вылетело, надо быстро чекнуть
 * расписание, а его нет — бесит». Так и было: истёк токен → клиент чистит сессию и
 * выбрасывает на экран входа, а вместе с кабинетом исчезает и расписание. При этом
 * расписание — ОБЩЕДОСТУПНАЯ информация: оно берётся с публичного портала ВСГУТУ, где
 * его видит кто угодно без всякого входа. Требовать токен там, где он ничего не
 * защищает, — значит забирать у человека то, что и так открыто.
 *
 * 🔥 ПОЧЕМУ СТРАНИЦА НЕ ПОКАЗЫВАЛА НИЧЕГО (12.09.2026, жалоба Влада «просмотр не
 * работает»). Причина оказалась не в вёрстке и не в сервере, а в РАЗОШЕДШЕМСЯ КОНТРАКТЕ,
 * и была она тихой вдвойне:
 *   • страница читала `resp.days`, а сервер такого ключа не отдаёт НИКОГДА — расписание
 *     лежит в `resp.schedule.weeks[неделя][день]`. Список дней получался пустым всегда, и
 *     экран честно писал «На этой неделе пар нет» — то есть отказ выглядел как ответ;
 *   • номер недели читался как `week.parity`, а `/public/week` отдаёт `{date, week}` —
 *     подпись про неделю не показывалась ни разу.
 * Ни сборка, ни линтер, ни тесты этого не видят: обращение к несуществующему полю в JS
 * законно и молча даёт `undefined`. Поэтому форма ответа теперь под сторожем
 * (`web/tests/publicScheduleContract.test.mjs`) — он сверяет ключи, которые читает
 * страница, с теми, что кладёт сервер.
 *
 * ⚠️ ЗДЕСЬ НЕТ И НЕ ДОЛЖНО БЫТЬ НИ ОДНОЙ СТРОКИ ИЗ ЖУРНАЛА: ни оценок, ни посещаемости,
 * ни списков студентов. Ровно та же граница, что на сервере (`routers/publicschedule.py`,
 * см. предупреждение в его шапке). Появится соблазн «показать тут же средний балл» —
 * это будет утечка, а не удобство.
 *
 * ⚠️ ТОЛЬКО ГРУППЫ. Расписание преподавателя без входа сервер отдаёт (им живёт виджет на
 * телефоне), но ЗДЕСЬ его выбора нет намеренно: чтобы предложить список преподавателей,
 * пришлось бы отдать наружу справочник сотрудников, а это уже не «оглавление публичного
 * портала». Требование Влада дословно: «просмотр только групп».
 *
 * ⚠️ Выбранная группа запоминается локально: человек, вылетевший из аккаунта, открывает
 * эту страницу ради одного конкретного расписания — своего, — и заставлять его каждый раз
 * искать группу в списке значит сделать страницу бесполезной ровно в тот момент, когда
 * она нужна быстро.
 */
import { ref, computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { publicScheduleApi } from '@/api/endpoints'
import { useLocaleStore } from '@/stores/locale'
import HexBackground from '@/components/HexBackground.vue'
import Badge from '@/components/ui/Badge.vue'
import { dayLabels, kindMap } from '@/config/scheduleView'
import { filterGroups } from '@/utils/groupSearch'
import { ArrowLeft, CalendarDays, Search, RotateCw } from '@lucide/vue'

const locale = useLocaleStore()
const router = useRouter()
const route = useRoute()

const LS_GROUP = 'gb.public.group'
const LS_CATEGORY = 'gb.public.category'
const DEFAULT_CATEGORY = 'college'

//Подписи дней и типов занятий — ОБЩИЕ с кабинетом (`config/scheduleView.js`). Своя копия
//разошлась бы с той молча, и одна и та же пара называлась бы по-разному на двух экранах.
const DAYS = computed(() => dayLabels(locale.t))
const KIND = computed(() => kindMap(locale.t))

const categories = ref([])
const category = ref(DEFAULT_CATEGORY)
const categoryDated = computed(() =>
  categories.value.find((c) => c.key === category.value)?.dated || false)

const groups = ref([])
const byCourse = ref({})
const courseFilter = ref('')
const query = ref('')
const groupsLoading = ref(false)
const groupsFailed = ref(false)

const group = ref('')
const data = ref(null)
const week = ref(1)
const weekNow = ref(null)
const loading = ref(false)
const error = ref('')

//Группа из АДРЕСА важнее запомненной: по такой ссылке приходят, когда её прислали
//(«вот расписание К74/1»), и показать вместо неё свою из памяти значит ответить не на
//тот вопрос. Сюда же приземляется переадресация со старого `/public/schedule?group=…`.
try {
  group.value = String(route.query.group || '').trim() || localStorage.getItem(LS_GROUP) || ''
  category.value = localStorage.getItem(LS_CATEGORY) || DEFAULT_CATEGORY
} catch {
  //Приватный режим — просто покажем выбор с начала.
  group.value = String(route.query.group || '').trim()
}

function remember(key, value) {
  try { localStorage.setItem(key, value) } catch { /* приватный режим — не беда */ }
}

// ━━━ СПИСОК ГРУПП ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
//
// ⚠️ Актуальность ответа сверяем ТОКЕНОМ запроса, а не значением `category`. Быстрое
// переключение категорий (клик-клик-клик) запускает несколько запросов разом, и более
// медленный ответ мог бы прилететь последним и подменить список чужими группами — при
// уже подсвеченной другой кнопке. Тот же приём и по той же причине, что в `SchedulePage`.
let reqSeq = 0
const nextReq = () => ++reqSeq

const courseKeys = computed(() =>
  Object.keys(byCourse.value).map(Number).filter((n) => !Number.isNaN(n)).sort((a, b) => a - b))

/** Группы после фильтра по курсу и поиска по названию. */
const shownGroups = computed(() => {
  let list = groups.value
  if (courseFilter.value) {
    const names = new Set(byCourse.value[courseFilter.value] || [])
    list = list.filter((g) => names.has(g))
  }
  //Поиск умеет регистр, раскладку и цифры — правило целиком в `utils/groupSearch.js`.
  return filterGroups(list, query.value)
})

async function loadCategories() {
  try {
    const r = (await publicScheduleApi.categories()).data
    categories.value = r.categories || []
    //Запомненная категория важнее умолчания сервера — иначе человек, смотрящий заочное,
    //каждый раз возвращался бы в колледж. Но только если она ещё существует в реестре.
    const known = categories.value.some((c) => c.key === category.value)
    if (!known) category.value = r.default || DEFAULT_CATEGORY
  } catch {
    //Категорий нет — остаётся колледж и ручной ввод названия. Это деградация, а не сбой.
    categories.value = []
  }
}

async function loadGroupsList() {
  const my = nextReq()
  const forCategory = category.value
  groupsLoading.value = true
  groupsFailed.value = false
  try {
    const r = (await publicScheduleApi.groups(forCategory)).data
    if (my !== reqSeq) return
    groups.value = r.groups || []
    byCourse.value = r.by_course || {}
    //Пустой список — это НЕ «групп нет», это «портал не ответил». Сказать «групп нет»
    //значило бы уверенно соврать: на портале они есть, и человек это знает.
    groupsFailed.value = !groups.value.length
  } catch {
    if (my !== reqSeq) return
    groups.value = []
    byCourse.value = {}
    groupsFailed.value = true
  } finally {
    if (my === reqSeq) groupsLoading.value = false
  }
}

function onCategoryChange(key) {
  if (key === category.value) return
  category.value = key
  remember(LS_CATEGORY, key)
  courseFilter.value = ''
  query.value = ''
  groups.value = []
  byCourse.value = {}
  loadGroupsList()
}

// ━━━ РАСПИСАНИЕ ВЫБРАННОЙ ГРУППЫ ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
/**
 * Расписание группы.
 *
 * ⚠️ КАТЕГОРИЯ ПЕРЕДАЁТСЯ ОТДЕЛЬНЫМ АРГУМЕНТОМ, и пустая строка здесь ОСМЫСЛЕННА: сервер
 * резолвит категорию из базы (`Group.category`) ТОЛЬКО когда параметр пуст. Группа,
 * пришедшая из АДРЕСА или из памяти, могла быть выбрана в другой категории — подставив ей
 * текущую, мы получили бы «группа не найдена» на присланной ссылке, хотя расписание есть.
 * Для группы, выбранной в списке, наоборот: категория известна точно, и передать её надо.
 */
async function load(name, cat) {
  const g = String(name || group.value || '').trim()
  if (!g) return
  const forCategory = cat === undefined ? category.value : cat
  const my = nextReq()
  loading.value = true
  error.value = ''
  try {
    const { data: resp } = await publicScheduleApi.group(g, forCategory)
    if (my !== reqSeq) return
    if (!resp?.available) {
      //«Группы не нашли» и «сервер недоступен» — разные события, и человеку важно
      //различать: в первом случае он поправит название, во втором подождёт.
      error.value = locale.t('publicSchedule.notFound', 'Группа не найдена. Проверьте название.')
      data.value = null
      return
    }
    data.value = resp
    group.value = g
    remember(LS_GROUP, g)
    //Категорию группы знает только сервер (Group.category) — подсвечиваем ту, что пришла.
    if (resp.category && resp.category !== category.value) {
      category.value = resp.category
      remember(LS_CATEGORY, resp.category)
    }
    //У сессионных категорий (заочное) «текущей» недели не существует: это не календарная
    //чётность, а порядковый номер сессии — берём первый реально найденный блок.
    const wk = Object.keys(resp.schedule?.weeks || {}).map(Number).sort((a, b) => a - b)
    week.value = categoryDated.value ? (wk[0] || 1) : (resp.week || 1)
  } catch {
    if (my !== reqSeq) return
    error.value = locale.t('publicSchedule.offline',
      'Не удалось получить расписание. Проверьте связь.')
  } finally {
    if (my === reqSeq) loading.value = false
  }
}

function openGroup(name, cat) {
  group.value = name
  load(name, cat)
}

/** Вернуться к выбору. Саму группу НЕ забываем: человек может просто смотреть соседнюю. */
function backToPicker() {
  data.value = null
  error.value = ''
  if (!groups.value.length && !groupsLoading.value) loadGroupsList()
}

const weeks = computed(() => data.value?.schedule?.weeks || {})
const weekKeys = computed(() => Object.keys(weeks.value).map(Number).sort((a, b) => a - b))
//Обычные категории — всегда две кнопки (даже если на эту чётность пар нет: «занятий нет»
//это ответ). Сессионные — сколько блоков реально пришло, число у групп разное.
const weekButtons = computed(() => {
  if (!categoryDated.value) return [1, 2]
  return weekKeys.value.length ? weekKeys.value : [1]
})
function weekButtonLabel(wk) {
  if (!categoryDated.value) {
    const label = wk === 2
      ? locale.t('schedulePage.week2', 'II неделя')
      : locale.t('schedulePage.week1', 'I неделя')
    return label + (data.value?.week === wk ? locale.t('schedulePage.nowSuffix', '  · сейчас') : '')
  }
  const days = Object.keys(weeks.value[String(wk)] || {})
  if (!days.length) return locale.t('schedulePage.sessionN', { n: wk })
  const first = days[0].split(', ').pop()
  const last = days[days.length - 1].split(', ').pop()
  return first === last
    ? locale.t('schedulePage.sessionNSingle', { n: wk, date: first })
    : locale.t('schedulePage.sessionNRange', { n: wk, from: first, to: last })
}
//Дни-колонки: обычные категории — фиксированные шесть; сессионные — реальные даты
//выбранного блока в том порядке, в котором их разобрал парсер (уже хронологическом).
const dayColumns = computed(() => {
  if (!categoryDated.value) return DAYS.value
  return Object.keys(weeks.value[String(week.value)] || {}).map((d) => [d, d])
})
function dayLessons(dayKey) { return (weeks.value[String(week.value)] || {})[dayKey] || [] }
const hasAny = computed(() => !!data.value?.available && Object.keys(weeks.value).length > 0)

onMounted(async () => {
  //Номер недели — отдельным публичным маршрутом. Без него расписание читается неверно:
  //у чётной и нечётной недели пары разные. Ключ ответа — `week`, а НЕ `parity`.
  try {
    weekNow.value = (await publicScheduleApi.week()).data?.week ?? null
  } catch {
    //Не показываем номер недели — это подпись, а не содержимое.
  }
  await loadCategories()
  //Пустая категория — намеренно: пусть сервер определит её по самой группе (см. load()).
  if (group.value) load(group.value, '')
  //Список готовим в любом случае: он нужен и для «к выбору группы».
  loadGroupsList()
})
</script>

<template>
  <div class="relative min-h-dvh bg-bg text-text">
    <HexBackground />

    <div class="relative mx-auto w-full max-w-6xl px-4 py-6">
      <!-- Выход отсюда обязателен: страницу открывают с экрана входа, и без кнопки
           «назад» человек в мобильном приложении оказывается в тупике — тот же дефект,
           что был у политики ПДн и соглашения. -->
      <button type="button" @click="router.push('/login')"
              class="mb-4 inline-flex min-h-11 items-center gap-2 rounded-lg px-2 text-sm font-semibold text-accent hover:underline">
        <ArrowLeft class="size-4" />
        {{ locale.t('publicSchedule.back', 'К входу в журнал') }}
      </button>

      <h1 class="flex items-center gap-2 font-title text-2xl font-bold">
        <CalendarDays class="size-6 text-accent" />
        {{ locale.t('publicSchedule.title', 'Расписание') }}
      </h1>
      <p class="mt-1 text-sm text-text3">
        {{ locale.t('publicSchedule.subtitle', 'Открыто без входа в журнал — как и на портале ВСГУТУ.') }}
        <span v-if="weekNow"> · {{ weekNow === 2
          ? locale.t('publicSchedule.weekEven', 'неделя II')
          : locale.t('publicSchedule.weekOdd', 'неделя I') }}</span>
      </p>

      <!-- ━━━ ВЫБОР ГРУППЫ ━━━ Показываем, пока расписание не открыто.
           ⚠️ Условие — РОВНО `!data`, без `|| error`. С ним обрыв связи при обновлении
           выбрасывал человека из расписания, которое он читает, обратно к списку групп:
           `error` ставится, а `data` остаётся. «Нет связи» — не повод убирать с экрана
           уже показанное (то же правило записано у расписания в кабинете). Ошибка
           «группа не найдена» сюда всё равно попадает: там `data` гасится явно. -->
      <div v-if="!data" class="mt-5 space-y-4">
        <!-- Категории портала — те же, что в кабинете (колледж, бакалавриат, заочное). -->
        <div v-if="categories.length > 1" class="flex flex-wrap gap-2">
          <button v-for="c in categories" :key="c.key" type="button"
                  class="rounded-full border px-3 py-1.5 text-xs font-medium transition-colors"
                  :class="category === c.key
                    ? 'border-accent bg-accent text-white'
                    : 'border-border2 bg-card2 text-text2 hover:border-accent/50'"
                  @click="onCategoryChange(c.key)">
            {{ c.label }}
          </button>
        </div>

        <div class="flex flex-wrap items-center gap-2">
          <!-- Поиск и по буквам, и по цифрам: правило в utils/groupSearch.js. Название
               группы человек помнит приблизительно («что-то на К, семьдесят четвёртая»),
               и требовать точного написания значит требовать того, чего он не знает. -->
          <label class="relative min-w-0 flex-1">
            <Search class="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-text3" />
            <input v-model="query" type="text" inputmode="text" autocomplete="off"
                   :placeholder="locale.t('publicSchedule.searchPlaceholder', 'Поиск: «к», «74», «К74/1»')"
                   class="h-11 w-full rounded-lg border border-border2 bg-card pl-9 pr-3 text-base text-text outline-none focus:border-accent" />
          </label>
          <select v-if="courseKeys.length > 1" v-model="courseFilter"
                  class="h-11 rounded-lg border border-border2 bg-card2 px-3 text-sm text-text outline-none focus:border-accent">
            <option value="">{{ locale.t('publicSchedule.allCourses', 'Все курсы') }}</option>
            <option v-for="c in courseKeys" :key="c" :value="c">
              {{ locale.t('adminGroups.courseN', { n: c }) }}
            </option>
          </select>
        </div>

        <p v-if="error" class="rounded-lg border border-red/40 bg-red/10 px-3 py-2 text-sm text-red">
          {{ error }}
        </p>

        <p v-if="groupsLoading" class="text-sm text-text3">
          {{ locale.t('publicSchedule.loadingGroups', 'Загружаем список групп…') }}
        </p>

        <!-- ⚠️ «Список не пришёл» и «групп нет» — РАЗНЫЕ сообщения. Сказав «групп нет»,
             мы уверенно соврали бы: на портале они есть. Поэтому здесь названа причина и
             оставлен ручной ввод — он работал и раньше, и терять его нельзя. -->
        <div v-else-if="groupsFailed"
             class="rounded-lg border border-border2 bg-card p-4 text-sm text-text3">
          {{ locale.t('publicSchedule.groupsUnavailable',
             'Список групп сейчас недоступен — портал не ответил. Введите название группы и нажмите «Показать».') }}
          <form class="mt-3 flex gap-2" @submit.prevent="openGroup(query)">
            <input v-model="query" type="text" autocomplete="off"
                   :placeholder="locale.t('publicSchedule.groupPlaceholder', 'Группа, например К74/1')"
                   class="h-11 min-w-0 flex-1 rounded-lg border border-border2 bg-card px-3 text-base text-text outline-none focus:border-accent" />
            <button type="submit" :disabled="loading || !query.trim()"
                    class="inline-flex h-11 items-center gap-2 rounded-lg bg-accent px-4 font-semibold text-white disabled:opacity-50">
              {{ loading ? locale.t('publicSchedule.loading', 'Ищем…') : locale.t('publicSchedule.show', 'Показать') }}
            </button>
          </form>
        </div>

        <template v-else>
          <p v-if="!shownGroups.length" class="rounded-lg border border-border2 bg-card p-4 text-center text-sm text-text3">
            {{ locale.t('publicSchedule.nothingFound', 'Ничего не найдено. Попробуйте номер цифрами — например «74».') }}
          </p>
          <div v-else class="grid grid-cols-2 gap-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6">
            <button v-for="g in shownGroups" :key="g" type="button"
                    class="min-h-11 rounded-lg border border-border2 bg-card px-2 py-2.5 text-sm font-semibold text-text transition-colors hover:border-accent hover:text-accent"
                    @click="openGroup(g)">
              {{ g }}
            </button>
          </div>
        </template>
      </div>

      <!-- ━━━ РАСПИСАНИЕ ━━━ -->
      <div v-else class="mt-5 space-y-4">
        <div class="flex flex-wrap items-center gap-2">
          <button type="button" @click="backToPicker"
                  class="inline-flex min-h-11 items-center gap-2 rounded-lg px-2 text-sm font-semibold text-accent hover:underline">
            <ArrowLeft class="size-4" />
            {{ locale.t('publicSchedule.changeGroup', 'к выбору группы') }}
          </button>
          <p class="text-sm font-semibold text-text2">{{ data.group }}</p>
          <div class="flex flex-wrap overflow-hidden rounded-sm border border-border2">
            <button v-for="wk in weekButtons" :key="wk" type="button" class="px-3 py-2 text-sm"
                    :class="week === wk ? 'bg-accent text-white' : 'bg-card2 text-text3'"
                    @click="week = wk">{{ weekButtonLabel(wk) }}</button>
          </div>
          <button type="button" :disabled="loading" @click="load(group, data.category || '')"
                  class="inline-flex min-h-11 items-center gap-1.5 rounded-lg border border-border2 px-3 text-sm text-text2 hover:border-accent hover:text-accent disabled:opacity-50">
            <RotateCw class="size-3.5" />
            {{ locale.t('schedulePage.refresh', 'Обновить') }}
          </button>
        </div>

        <!-- Отказ обновления виден ЗДЕСЬ, а не уводит к выбору группы. -->
        <p v-if="error" class="rounded-lg border border-red/40 bg-red/10 px-3 py-2 text-sm text-red">
          {{ error }}
        </p>

        <p v-if="loading" class="text-sm text-text3">
          {{ locale.t('schedulePage.loadingSchedule', 'Загрузка расписания…') }}
        </p>

        <p v-else-if="!hasAny" class="rounded-xl border border-border bg-card px-3 py-6 text-center text-sm text-text3">
          {{ locale.t('publicSchedule.empty', 'На этой неделе пар нет.') }}
        </p>

        <!-- Раскладка та же, что в кабинете: дни колонками, на узком экране — в столбик.
             h-full — чтобы карточки дней в одном ряду были равной высоты, иначе ряд идёт
             «лесенкой» и выглядит как сбой вёрстки при верных данных. -->
        <div v-else class="grid grid-cols-1 items-stretch gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
          <div v-for="[key, full] in dayColumns" :key="key"
               class="flex h-full flex-col rounded-lg border border-border bg-card p-3 shadow-card">
            <p class="mb-2 font-title text-base font-bold text-text">{{ full }}</p>
            <p v-if="!dayLessons(key).length" class="py-4 text-center text-xs text-text2">
              {{ locale.t('schedulePage.noLessons', 'Занятий нет') }}
            </p>
            <ul v-else class="space-y-2">
              <li v-for="(l, i) in dayLessons(key)" :key="i" class="rounded-md border border-border bg-card2 p-2.5">
                <div class="mb-1 flex flex-wrap items-center justify-between gap-x-2 gap-y-1">
                  <!-- Время не переносим: «10:45–12:20» на двух строках читается как две пары. -->
                  <span class="whitespace-nowrap text-xs font-semibold text-text3">{{ l.pair_no }}. {{ l.time }}</span>
                  <span class="flex shrink-0 items-center gap-1.5">
                    <!-- Подгруппа обязательна: портал кладёт в одну клетку два занятия,
                         и без метки это просто две пары в одно время. -->
                    <Badge v-if="l.subgroup" variant="muted">{{ locale.t('schedulePage.subgroup', { n: l.subgroup }) }}</Badge>
                    <Badge v-if="l.kind" :variant="(KIND[l.kind] || ['', 'muted'])[1]">{{ (KIND[l.kind] || [l.kind])[0] }}</Badge>
                  </span>
                </div>
                <p class="text-sm font-medium text-text">{{ l.subject || l.raw }}</p>
                <p v-if="l.teacher || l.room" class="mt-0.5 text-xs text-text3">
                  {{ l.teacher }}<span v-if="l.room"> · {{ locale.t('schedulePage.roomLabel', { room: l.room }) }}</span>
                </p>
              </li>
            </ul>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
