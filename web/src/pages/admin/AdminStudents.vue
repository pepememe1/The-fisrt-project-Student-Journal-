<script setup>
// AdminStudents — список студентов + CRUD (Phase B). Добавление/правка/удаление с
// выбором группы из списка (синкнутые группы БД + спарсенные из расписания). id
// студента на сервере — stud:login (как в синке десктопа); удаление мягкое (надгробие),
// поэтому изменения доезжают до десктопа обычным pull.
import { ref, computed, onMounted } from 'vue'
import StickyXScroll from '@/components/ui/StickyXScroll.vue'
import { RotateCw, Copy } from '@lucide/vue'
import { adminApi, scheduleApi } from '@/api/endpoints'
import { generatePassword } from '@/utils/passwordGen'
import { copyText } from '@/utils/clipboard'
import AppButton from '@/components/ui/AppButton.vue'
import { useToast } from '@/composables/useToast'
import { useConfirm } from '@/composables/useConfirm'
import { useLocaleStore } from '@/stores/locale'

const BCP47 = { ru: 'ru-RU', en: 'en-US', zh: 'zh-CN' }

const locale = useLocaleStore()
const toast = useToast()
const { confirm } = useConfirm()
const all = ref([])
const loading = ref(true)
const q = ref('')
const groupChoices = ref([])
const showPass = ref(false)     // показать вводимый пароль в модалке (по глазку)

// ── Категория расписания + фильтр по группе (schedule/parser.py::CATEGORIES) —
// раньше фильтра по группе в веб-версии не было вообще (только текстовый поиск,
// десктоп такой фильтр уже имел) — заодно закрывает и этот разрыв.
const categories = ref([])
const categoryFilter = ref('')
const groupFilter = ref('')
const groupCategory = ref({})   // {имя группы: category}
async function loadCategories() {
  try { categories.value = (await scheduleApi.categories()).data.categories || [] }
  catch { categories.value = [] }
}
// Курс (3.5.5) — сужает список групп ВНУТРИ категории ЕЩЁ дальше (сортировка
// колледж/бакалавриат/заочное → курс → группа, как просили). Разведано с портала
// (столбец таблицы индекса), число курсов НЕ фиксировано на 4.
const courseFilter = ref('')
const byCourse = ref({})   // {курс: [имена с портала]} — для ТЕКУЩЕЙ categoryFilter (кнопки)
async function loadByCourse() {
  courseFilter.value = ''
  if (!categoryFilter.value) { byCourse.value = {}; return }
  try { byCourse.value = (await scheduleApi.groups(categoryFilter.value)).data.by_course || {} }
  catch { byCourse.value = {} }
}
const courseKeys = computed(() => Object.keys(byCourse.value).map(Number).sort((a, b) => a - b))

// ⚠️ (живой отзыв Влада) Курс раньше сужал только выпадающий список ГРУПП в фильтре —
// саму таблицу студентов не трогал вовсе (кнопка «нажимается, но не сортирует»), и
// столбца с курсом не было, чтобы вообще понять, где студент. Здесь — ОБЩАЯ карта
// {группа → курс} по ВСЕМ категориям сразу (не только по выбранной), нужна и для
// столбца (виден без выбора категории), и для реального фильтра строк ниже.
const groupCourse = ref({})
// {имя группы: category} ПО ПОРТАЛУ — заполняет пробелы там, где группы ещё нет в БД
// (спарсили из расписания, но не завели). Без этого такая группа считалась бы
// колледжем по умолчанию, и заочная группа попадала бы не в своё направление.
const portalCategory = ref({})
async function loadPortalMaps() {
  const courses = {}
  const cats = {}
  for (const c of categories.value) {
    try {
      const by = (await scheduleApi.groups(c.key)).data.by_course || {}
      for (const [course, names] of Object.entries(by)) {
        for (const g of names) { courses[g] = Number(course); cats[g] = c.key }
      }
    } catch { /* эта категория недоступна — остальные всё равно посчитаем */ }
  }
  groupCourse.value = courses
  portalCategory.value = cats
}

// Направление группы. Порядок источников важен: БД ГЛАВНЕЕ портала — категорию там
// мог поправить админ вручную, и портал не должен её переопределять.
function categoryOf(name) {
  return groupCategory.value[name] || portalCategory.value[name] || 'college'
}
function categoryLabel(key) {
  return categories.value.find((c) => c.key === key)?.label || key
}

// Список групп в фильтре сужается под выбранную категорию, затем под курс — иначе
// можно было бы выбрать «колледж» и группу заочки одновременно и увидеть пусто без
// понятной причины.
const groupFilterChoices = computed(() => {
  let list = groupChoices.value
  if (categoryFilter.value) {
    list = list.filter((g) => categoryOf(g) === categoryFilter.value)
  }
  if (courseFilter.value) {
    const names = new Set(byCourse.value[courseFilter.value] || [])
    list = list.filter((g) => names.has(g))
  }
  return list
})
function setCategoryFilter(key) {
  categoryFilter.value = key
  loadByCourse()
  if (groupFilter.value && !groupFilterChoices.value.includes(groupFilter.value)) groupFilter.value = ''
}
function setCourseFilter(c) {
  courseFilter.value = c
  if (groupFilter.value && !groupFilterChoices.value.includes(groupFilter.value)) groupFilter.value = ''
}

// Пароль в БД хранится ХЕШЕМ и не показывается — глазок открывает то, что админ ВВОДИТ.
function fmtDT(iso) {
  if (!iso) return '—'
  const d = new Date(iso)
  return isNaN(d) ? '—' : d.toLocaleString(BCP47[locale.active] || 'ru-RU', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })
}

async function reload() {
  loading.value = true
  try { all.value = (await adminApi.students()).data.students || [] } catch { all.value = [] } finally { loading.value = false }
}

onMounted(async () => {
  await reload()
  await loadCategories()
  loadPortalMaps()   // фоном — таблица показывает «—», пока карта не готова, не блокируем список
  // Список групп для выбора: синкнутые (БД) + спарсенные из расписания, без дублей —
  // как _all_group_choices в десктопе.
  try {
    const dbGroups = (await adminApi.groups()).data.groups || []
    const dbG = dbGroups.map((g) => g.name)
    groupCategory.value = Object.fromEntries(dbGroups.map((g) => [g.name, g.category || 'college']))
    let parsed = []
    try { parsed = (await scheduleApi.groups()).data.groups || [] } catch { /* оффлайн — ок */ }
    groupChoices.value = [...new Set([...dbG, ...parsed].filter(Boolean))]
  } catch { /* */ }
})

const rows = computed(() => {
  const s = q.value.trim().toLowerCase()
  return all.value.filter((r) => {
    if (s && !`${r.surname} ${r.name} ${r.group} ${r.login}`.toLowerCase().includes(s)) return false
    if (groupFilter.value && r.group !== groupFilter.value) return false
    if (categoryFilter.value && categoryOf(r.group) !== categoryFilter.value) return false
    if (courseFilter.value && groupCourse.value[r.group] !== Number(courseFilter.value)) return false
    return true
  })
})

// Модалка создания/правки
const showForm = ref(false)
const editing = ref(null) // null = создание; иначе исходный login (ключ)
const form = ref({ surname: '', name: '', patronymic: '', login: '', group: '', password: '', birthday: '' })
const saving = ref(false)
const formError = ref('')

// Дата последней выдачи пароля. Показываем ТОЛЬКО дату: сам пароль показать нельзя,
// в базе лежит необратимый хеш. Пусто — пароль не менялся с тех пор, как появилось
// это поле; выдумывать дату задним числом нельзя, «—» честнее.
const passwordSetAt = ref('')
function fmtIssued(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  return isNaN(d) ? '' : d.toLocaleString(BCP47[locale.active] || 'ru-RU',
    { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' })
}

// Кнопка «⟳» у поля пароля. Сгенерированный пароль СРАЗУ раскрываем глазком: админу его
// диктовать человеку, а из точек не продиктуешь — иначе кнопка выглядела бы сломанной.
// Повторное нажатие просто перезаписывает поле новым паролем.
function regeneratePassword() {
  form.value.password = generatePassword()
  showPass.value = true
}

// Копируется ТО, ЧТО В ПОЛЕ (новый пароль), — текущего у нас нет и быть не может.
// Результат обязательно показываем: `copyText` умеет вернуть false (в вебвью без прав к
// буферу, при заходе по локальному IP без защищённого контекста), и молчаливый провал
// читается как «кнопка не работает» — этой болезнью уже болел мессенджер.
async function copyPassword() {
  if (await copyText(form.value.password)) toast.success(locale.t('password.copied', 'Пароль скопирован'))
  else toast.error(locale.t('password.copyFailed', 'Не удалось скопировать — выделите пароль и скопируйте вручную'))
}

// Группы в карточке студента подчиняются ТОЙ ЖЕ вкладке направления, что и таблица
// (живой запрос). Выбран «Колледж» — в списке только колледж; выбраны «Все категории»
// — все, но РАЗЛОЖЕННЫЕ по направлению, а не свалкой: иначе в списке из полутора сотен
// имён невозможно понять, к какому направлению относится «К74/1», и админ заводит
// студента в одноимённую группу чужого направления.
//
// ⚠️ Объявлено ПОСЛЕ `form`: computed ленив и порядок ему безразличен, но правило
// «не читать ref, объявленный ниже» в этом проекте уже стоило пустой страницы, и
// нарушать его даже там, где это безопасно, — плохая привычка.
const formGroupChoices = computed(() => {
  const order = categories.value.map((c) => c.key)
  const rank = (g) => {
    const i = order.indexOf(categoryOf(g))
    return i < 0 ? order.length : i          // незнакомое направление — в конец, не теряем
  }
  const cur = (form.value.group || '').trim()
  let list = groupChoices.value
  if (categoryFilter.value) {
    list = list.filter((g) => categoryOf(g) === categoryFilter.value)
    // Уже выбранную группу показываем ВСЕГДА, даже если она из другого направления:
    // иначе при правке студента список её не предлагает, и стёртое поле нечем вернуть.
    if (cur && !list.includes(cur)) list = [cur, ...list]
    return [...list].sort((a, b) => a.localeCompare(b, 'ru'))
  }
  return [...list].sort((a, b) => rank(a) - rank(b) || a.localeCompare(b, 'ru'))
})
// Подпись направления рядом с группой нужна ТОЛЬКО в режиме «все категории»: внутри
// выбранного направления она одинакова у всех строк и превращается в шум.
function formGroupLabel(g) {
  return categoryFilter.value ? '' : categoryLabel(categoryOf(g))
}

function openCreate() {
  editing.value = null
  form.value = { surname: '', name: '', patronymic: '', login: '', group: '', password: '', birthday: '' }
  formError.value = ''
  showForm.value = true
}
function openEdit(r) {
  editing.value = r.login
  //Имя и отчество — раздельно: сервер отдаёт first_name/patronymic (name — полная форма-ключ).
  form.value = {
    surname: r.surname, name: r.first_name ?? r.name, patronymic: r.patronymic ?? '',
    login: r.login, group: r.group, password: '', birthday: r.birthday ?? '',
  }
  passwordSetAt.value = r.password_set_at || ''
  formError.value = ''
  showForm.value = true
}

async function save() {
  const f = form.value
  if (!f.surname.trim() || !f.name.trim()) { formError.value = locale.t('adminStudents.enterSurnameName', 'Введите фамилию и имя'); return }
  if (!f.login.trim()) { formError.value = locale.t('adminStudents.enterLogin', 'Введите логин'); return }
  saving.value = true
  formError.value = ''
  try {
    if (editing.value) {
      await adminApi.updateStudent(editing.value, { surname: f.surname, name: f.name, patronymic: f.patronymic, group: f.group, password: f.password, birthday: f.birthday })
    } else {
      await adminApi.createStudent({ surname: f.surname, name: f.name, patronymic: f.patronymic, login: f.login, group: f.group, password: f.password, birthday: f.birthday })
    }
    showForm.value = false
    await reload()
  } catch (e) {
    formError.value = e?.response?.data?.detail || locale.t('adminStudents.saveFailed', 'Не удалось сохранить')
  } finally {
    saving.value = false
  }
}

async function del(r) {
  if (!(await confirm({ title: locale.t('adminStudents.confirmDelete', { name: `${r.surname} ${r.name}` }), okText: locale.t('common.delete'), danger: true }))) return
  try { await adminApi.deleteStudent(r.login); await reload() }
  catch (e) { toast.error(e?.response?.data?.detail || locale.t('adminStudents.deleteFailed', 'Не удалось удалить')) }
}
</script>

<template>
  <div class="space-y-4">
    <!-- Кнопки-категории (та же идея, что в «Расписании»/«Группах») — сужают список
         групп в фильтре ниже. -->
    <div v-if="categories.length > 1" class="flex flex-wrap gap-2">
      <button class="rounded-full border px-3 py-1.5 text-xs font-medium transition-colors"
              :class="!categoryFilter ? 'border-accent bg-accent text-white' : 'border-border2 bg-card2 text-text2 hover:border-accent/50'"
              @click="setCategoryFilter('')">{{ locale.t('adminStudents.allCategories', 'Все категории') }}</button>
      <button v-for="c in categories" :key="c.key"
              class="rounded-full border px-3 py-1.5 text-xs font-medium transition-colors"
              :class="categoryFilter === c.key ? 'border-accent bg-accent text-white' : 'border-border2 bg-card2 text-text2 hover:border-accent/50'"
              @click="setCategoryFilter(c.key)">{{ c.label }}</button>
    </div>

    <!-- Кнопки-курсы — ВНУТРИ выбранной категории (3.5.5), число курсов не
         фиксировано на 4 (разведано с портала). -->
    <div v-if="categoryFilter && courseKeys.length > 1" class="flex flex-wrap gap-2">
      <button class="rounded-full border px-3 py-1.5 text-xs font-medium transition-colors"
              :class="!courseFilter ? 'border-accent bg-accent text-white' : 'border-border2 bg-card2 text-text2 hover:border-accent/50'"
              @click="setCourseFilter('')">{{ locale.t('adminGroups.allCourses', 'Все курсы') }}</button>
      <button v-for="c in courseKeys" :key="c"
              class="rounded-full border px-3 py-1.5 text-xs font-medium transition-colors"
              :class="courseFilter === c ? 'border-accent bg-accent text-white' : 'border-border2 bg-card2 text-text2 hover:border-accent/50'"
              @click="setCourseFilter(c)">{{ locale.t('adminGroups.courseN', { n: c }) }}</button>
    </div>

    <div class="flex flex-wrap items-center gap-3">
      <input v-model="q" :placeholder="locale.t('adminStudents.searchPlaceholder', 'Поиск по ФИО, группе или логину…')"
             class="h-10 w-full max-w-sm rounded-sm border border-border2 bg-card2 px-3.5 text-sm text-text outline-none focus:border-accent focus:bg-card" />
      <select v-model="groupFilter"
              class="h-10 rounded-sm border border-border2 bg-card2 px-3 text-sm text-text outline-none focus:border-accent">
        <option value="">{{ locale.t('adminStudents.allGroups', 'Все группы') }}</option>
        <option v-for="g in groupFilterChoices" :key="g" :value="g">{{ g }}</option>
      </select>
      <AppButton variant="green" size="sm" @click="openCreate">{{ locale.t('adminStudents.addAction', '+ Добавить') }}</AppButton>
    </div>

    <StickyXScroll class="rounded-lg border border-border bg-card shadow-card">
      <table class="w-full text-sm">
        <thead>
          <tr class="border-b border-border2 bg-bg2 text-left text-tiny uppercase tracking-wide text-text2">
            <th class="px-4 py-2.5 font-semibold">{{ locale.t('adminStudents.colFullName', 'ФИО') }}</th>
            <th class="px-4 py-2.5 font-semibold">{{ locale.t('adminStudents.colGroup', 'Группа') }}</th>
            <th class="px-4 py-2.5 font-semibold">{{ locale.t('adminStudents.colCourse', 'Курс') }}</th>
            <th class="px-4 py-2.5 font-semibold">{{ locale.t('adminStudents.colLogin', 'Логин') }}</th>
            <th class="px-4 py-2.5 font-semibold">{{ locale.t('adminStudents.colPhone', 'Телефон') }}</th>
            <th class="px-4 py-2.5 font-semibold">{{ locale.t('adminStudents.colLastLogin', 'Посл. вход') }}</th>
            <th class="px-4 py-2.5 font-semibold">{{ locale.t('adminStudents.colIp', 'IP') }}</th>
            <th class="px-4 py-2.5 text-right font-semibold">{{ locale.t('adminStudents.colActions', 'Действия') }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="loading"><td colspan="8" class="px-4 py-6 text-center text-text3">{{ locale.t('common.loading') }}</td></tr>
          <tr v-else-if="!rows.length"><td colspan="8" class="px-4 py-6 text-center text-text3">{{ locale.t('adminStudents.noStudents', 'Студентов нет') }}</td></tr>
          <tr v-for="(r, i) in rows" :key="i" class="border-b border-border last:border-0 hover:bg-bg2/60">
            <td class="whitespace-nowrap px-4 py-2.5 font-medium text-text">{{ r.surname }} {{ r.name }}</td>
            <td class="px-4 py-2.5 text-text2">{{ r.group || '—' }}</td>
            <td class="whitespace-nowrap px-4 py-2.5 text-text2">{{ groupCourse[r.group] ?? '—' }}</td>
            <td class="px-4 py-2.5 text-text2">{{ r.login || '—' }}</td>
            <td class="whitespace-nowrap px-4 py-2.5 text-text2">{{ r.phone || '—' }}</td>
            <td class="whitespace-nowrap px-4 py-2.5 text-text3" :title="r.device ? locale.t('adminStudents.deviceTitle', { device: r.device }) : ''">{{ fmtDT(r.last_login) }}</td>
            <td class="whitespace-nowrap px-4 py-2.5 text-text3">{{ r.ip || '—' }}</td>
            <td class="whitespace-nowrap px-4 py-2.5 text-right">
              <button class="mr-3 text-text3 hover:text-accent" :title="locale.t('adminStudents.edit', 'Изменить')" @click="openEdit(r)">✎</button>
              <button class="text-text3 hover:text-red" :title="locale.t('common.delete')" @click="del(r)">✕</button>
            </td>
          </tr>
        </tbody>
      </table>
    </StickyXScroll>

    <!-- Модалка создания/правки -->
    <div v-if="showForm" class="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" @click.self="showForm = false">
      <div class="w-full max-w-md rounded-lg border border-border bg-card p-5 shadow-card">
        <h3 class="mb-4 font-title text-lg font-bold text-text">{{ editing ? locale.t('adminStudents.editTitle', 'Изменить студента') : locale.t('adminStudents.addTitle', 'Добавить студента') }}</h3>
        <div class="space-y-3">
          <div class="grid grid-cols-2 gap-3">
            <label class="block"><span class="mb-1 block text-tiny uppercase text-text3">{{ locale.t('adminStudents.surname', 'Фамилия') }}</span>
              <input v-model="form.surname" class="h-10 w-full rounded-sm border border-border2 bg-card2 px-3 text-sm text-text outline-none focus:border-accent" /></label>
            <label class="block"><span class="mb-1 block text-tiny uppercase text-text3">{{ locale.t('adminStudents.firstName', 'Имя') }}</span>
              <input v-model="form.name" class="h-10 w-full rounded-sm border border-border2 bg-card2 px-3 text-sm text-text outline-none focus:border-accent" /></label>
          </div>
          <label class="block"><span class="mb-1 block text-tiny uppercase text-text3">{{ locale.t('adminStudents.patronymic', 'Отчество') }} <span class="text-text3 normal-case">{{ locale.t('adminStudents.optionalHint', '(необязательно)') }}</span></span>
            <input v-model="form.patronymic" class="h-10 w-full rounded-sm border border-border2 bg-card2 px-3 text-sm text-text outline-none focus:border-accent" /></label>
          <!-- День рождения БЕЗ ГОДА: для поздравления он не нужен, а полная дата
               рождения — совсем другой уровень чувствительности. Сервер год отбрасывает
               молча, даже если его вписали. -->
          <label class="block"><span class="mb-1 block text-tiny uppercase text-text3">{{ locale.t('adminStudents.birthday', 'День рождения') }}
            <span class="text-text3 normal-case">{{ locale.t('adminStudents.birthdayHint', '(день и месяц, например 07.03)') }}</span></span>
            <input v-model="form.birthday" inputmode="numeric" placeholder="07.03" maxlength="10"
                   class="h-10 w-full rounded-sm border border-border2 bg-card2 px-3 text-sm text-text outline-none focus:border-accent" /></label>
          <label class="block"><span class="mb-1 block text-tiny uppercase text-text3">{{ locale.t('adminStudents.colLogin', 'Логин') }}</span>
            <input v-model="form.login" :disabled="!!editing"
                   class="h-10 w-full rounded-sm border border-border2 bg-card2 px-3 text-sm text-text outline-none focus:border-accent disabled:opacity-60" /></label>
          <label class="block"><span class="mb-1 block text-tiny uppercase text-text3">{{ locale.t('adminStudents.colGroup', 'Группа') }}</span>
            <input v-model="form.group" list="admin-group-list" :placeholder="locale.t('adminStudents.groupPlaceholder', 'Выберите или введите группу')"
                   class="h-10 w-full rounded-sm border border-border2 bg-card2 px-3 text-sm text-text outline-none focus:border-accent" />
            <!-- label у option в datalist показывается второй строкой рядом со значением
                 (Chromium: и браузер, и WebView2 на десктопе, и WebView в APK) — так
                 направление видно, а само значение остаётся чистым именем группы. -->
            <datalist id="admin-group-list">
              <option v-for="g in formGroupChoices" :key="g" :value="g" :label="formGroupLabel(g) || undefined" />
            </datalist>
            <span class="mt-1 block text-tiny text-text3">{{ categoryFilter
              ? locale.t('adminStudents.groupsOfCategoryHint', { category: categoryLabel(categoryFilter) })
              : locale.t('adminStudents.groupsAllCategoriesHint', 'Показаны группы всех направлений — направление подписано рядом.') }}</span>
          </label>
          <label class="block"><span class="mb-1 block text-tiny uppercase text-text3">{{ editing ? locale.t('adminStudents.newPasswordHint', 'Новый пароль (пусто — не менять)') : locale.t('adminStudents.passwordLabel', 'Пароль') }}</span>
            <!-- Сам пароль показать нельзя (в базе необратимый хеш), но ДАТА выдачи
                 отвечает на реальный вопрос админа: свежий пароль или полугодовой. -->
            <p v-if="editing" class="mb-1 text-tiny text-text3">
              <span class="uppercase">{{ locale.t('password.issuedLabel', 'Пароль выдан:') }}</span>
              <span class="ml-1 text-text2">{{ fmtIssued(passwordSetAt) || locale.t('password.issuedUnknown', 'неизвестно') }}</span>
              <span v-if="form.password" class="ml-1 text-accent">{{ locale.t('password.issuedPending', '→ обновится при сохранении') }}</span>
            </p>
            <div class="flex gap-2">
              <div class="relative flex-1">
                <input v-model="form.password" :type="showPass ? 'text' : 'password'" placeholder="••••••••"
                       class="h-10 w-full rounded-sm border border-border2 bg-card2 px-3 pr-10 text-sm text-text outline-none focus:border-accent" />
                <button type="button" class="absolute right-2 top-1/2 -translate-y-1/2 text-sm text-text3 hover:text-accent" @click="showPass = !showPass">
                  {{ showPass ? '🙈' : '👁' }}
                </button>
              </div>
              <button type="button" :title="locale.t('password.generate', 'Сгенерировать пароль')"
                      :aria-label="locale.t('password.generate', 'Сгенерировать пароль')"
                      class="grid size-10 shrink-0 place-items-center rounded-sm border border-border2 bg-card2 text-text3 outline-none hover:border-accent hover:text-accent focus:border-accent"
                      @click="regeneratePassword">
                <RotateCw class="size-4" />
              </button>
              <!-- Пустое поле копировать нечего: кнопка гаснет, а не отвечает ошибкой. -->
              <button type="button" :disabled="!form.password"
                      :title="locale.t('password.copy', 'Скопировать пароль')"
                      :aria-label="locale.t('password.copy', 'Скопировать пароль')"
                      class="grid size-10 shrink-0 place-items-center rounded-sm border border-border2 bg-card2 text-text3 outline-none hover:border-accent hover:text-accent focus:border-accent disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:border-border2 disabled:hover:text-text3"
                      @click="copyPassword">
                <Copy class="size-4" />
              </button>
            </div>
            <span class="mt-1 block text-tiny text-text3">{{ editing
              ? locale.t('password.replaceHint', 'Пусто — пароль останется прежним. Введите свой или нажмите ⟳ — заменит на новый.')
              : locale.t('password.generateHint', 'Нажмите ⟳ — сгенерируется пароль из 10 символов: строчные, заглавные, цифра и спецсимвол.') }}</span></label>
          <p v-if="formError" class="text-sm text-red">{{ formError }}</p>
        </div>
        <div class="mt-5 flex justify-end gap-2">
          <AppButton variant="ghost" size="sm" @click="showForm = false">{{ locale.t('common.cancel') }}</AppButton>
          <AppButton variant="green" size="sm" :disabled="saving" @click="save">
            {{ saving ? locale.t('adminStudents.saving', 'Сохранение…') : (editing ? locale.t('common.save') : locale.t('adminStudents.addAction2', 'Добавить')) }}
          </AppButton>
        </div>
      </div>
    </div>
  </div>
</template>
