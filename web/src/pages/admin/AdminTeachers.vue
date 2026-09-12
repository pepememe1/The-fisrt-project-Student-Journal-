<script setup>
// AdminTeachers — преподаватели + CRUD (Phase B). Создание/правка (ФИО, логин, нагрузка
// = список предметов, пароль) / удаление. id на сервере = teach:login (как в синке
// десктопа); удаление мягкое → изменения доезжают до десктопа обычным pull.
import { ref, computed, onMounted } from 'vue'
import StickyXScroll from '@/components/ui/StickyXScroll.vue'
import { RotateCw, Copy } from '@lucide/vue'
import { adminApi, scheduleApi } from '@/api/endpoints'
import { generatePassword } from '@/utils/passwordGen'
import { copyText } from '@/utils/clipboard'
import AppButton from '@/components/ui/AppButton.vue'
import Badge from '@/components/ui/Badge.vue'
import { useToast } from '@/composables/useToast'
import { useConfirm } from '@/composables/useConfirm'
import { useLocaleStore } from '@/stores/locale'

const BCP47 = { ru: 'ru-RU', en: 'en-US', zh: 'zh-CN' }

const locale = useLocaleStore()
const toast = useToast()
const { confirm } = useConfirm()
const all = ref([])
const loading = ref(true)
const allSubjects = ref([])
const allGroups = ref([])
const q = ref('')
const showPass = ref(false)

// ── Фильтр по категории расписания (schedule/parser.py::CATEGORIES) — как в
// AdminStudents.vue, но у преподавателя нет ОДНОЙ своей группы: категорию определяем
// по его курируемым группам (единственная связь препод→группа, видимая в этом списке).
// Препод без curated_groups — вообще не курирует, под фильтром по категории не найдётся
// ни в одной, кроме «Все категории»: это честно, а не баг, второй связи препод↔группа
// (SubjectHours.teacher_id) этот список не загружает.
const categories = ref([])
const categoryFilter = ref('')
const groupCategory = ref({})   // {имя группы: category}
async function loadCategories() {
  try { categories.value = (await scheduleApi.categories()).data.categories || [] }
  catch { categories.value = [] }
}
function fmtDT(iso) {
  if (!iso) return '—'
  const d = new Date(iso)
  return isNaN(d) ? '—' : d.toLocaleString(BCP47[locale.active] || 'ru-RU', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })
}

async function reload() {
  loading.value = true
  try { all.value = (await adminApi.teachers()).data.teachers || [] } catch { all.value = [] } finally { loading.value = false }
}
onMounted(async () => {
  await reload()
  await loadCategories()
  try { allSubjects.value = (await adminApi.subjects()).data.subjects?.map((s) => s.name) || [] } catch { /* */ }
  try {
    const dbGroups = (await adminApi.groups()).data.groups || []
    allGroups.value = dbGroups.map((g) => g.name)
    groupCategory.value = Object.fromEntries(dbGroups.map((g) => [g.name, g.category || 'college']))
  } catch { /* */ }
})

const rows = computed(() => {
  const s = q.value.trim().toLowerCase()
  return all.value.filter((r) => {
    if (s && !`${r.name} ${r.login}`.toLowerCase().includes(s)) return false
    if (categoryFilter.value) {
      const cats = (r.curated_groups || []).map((g) => groupCategory.value[g] || 'college')
      if (!cats.includes(categoryFilter.value)) return false
    }
    return true
  })
})

const showForm = ref(false)
const editing = ref(null)
const form = ref({ full_name: '', login: '', subjects: [], curated_groups: [], password: '' })
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
// диктовать преподавателю, а из точек не продиктуешь. Повторное нажатие — новый пароль.
function regeneratePassword() {
  form.value.password = generatePassword()
  showPass.value = true
}

// Копируется ТО, ЧТО В ПОЛЕ (новый пароль): текущего у нас нет, в базе только хеш.
// Провал копирования показываем — в вебвью и по локальному IP буфер бывает недоступен,
// и молчаливый отказ читается как «кнопка не работает».
async function copyPassword() {
  if (await copyText(form.value.password)) toast.success(locale.t('password.copied', 'Пароль скопирован'))
  else toast.error(locale.t('password.copyFailed', 'Не удалось скопировать — выделите пароль и скопируйте вручную'))
}

function openCreate() { editing.value = null; form.value = { full_name: '', login: '', subjects: [], curated_groups: [], password: '' }; formError.value = ''; showForm.value = true }
function openEdit(t) { editing.value = t.login; form.value = { full_name: t.name, login: t.login, subjects: [...(t.subjects || [])], curated_groups: [...(t.curated_groups || [])], password: '' }; formError.value = ''; showForm.value = true ; passwordSetAt.value = t.password_set_at || '' }
function toggleSubject(s) {
  const i = form.value.subjects.indexOf(s)
  if (i >= 0) form.value.subjects.splice(i, 1)
  else form.value.subjects.push(s)
}
function toggleCurated(g) {
  const i = form.value.curated_groups.indexOf(g)
  if (i >= 0) form.value.curated_groups.splice(i, 1)
  else form.value.curated_groups.push(g)
}
async function save() {
  const f = form.value
  if (!f.full_name.trim()) { formError.value = locale.t('adminTeachers.enterFullName', 'Введите ФИО'); return }
  if (!f.login.trim()) { formError.value = locale.t('adminTeachers.enterLogin', 'Введите логин'); return }
  saving.value = true; formError.value = ''
  try {
    const payload = { full_name: f.full_name, subjects: f.subjects, curated_groups: f.curated_groups, password: f.password }
    if (editing.value) await adminApi.updateTeacher(editing.value, payload)
    else await adminApi.createTeacher({ ...payload, login: f.login })
    showForm.value = false; await reload()
  } catch (e) { formError.value = e?.response?.data?.detail || locale.t('adminTeachers.saveFailed', 'Не удалось сохранить') }
  finally { saving.value = false }
}
async function del(t) {
  if (!(await confirm({ title: locale.t('adminTeachers.confirmDelete', { name: t.name }), okText: locale.t('common.delete'), danger: true }))) return
  try { await adminApi.deleteTeacher(t.login); await reload() }
  catch (e) { toast.error(e?.response?.data?.detail || locale.t('adminTeachers.deleteFailed', 'Не удалось удалить')) }
}
</script>

<template>
  <div class="space-y-4">
    <!-- Кнопки-категории (та же идея, что в «Студентах»/«Расписании») — по курируемым
         группам преподавателя (единственная видимая здесь связь препод↔группа). -->
    <div v-if="categories.length > 1" class="flex flex-wrap gap-2">
      <button class="rounded-full border px-3 py-1.5 text-xs font-medium transition-colors"
              :class="!categoryFilter ? 'border-accent bg-accent text-white' : 'border-border2 bg-card2 text-text2 hover:border-accent/50'"
              @click="categoryFilter = ''">{{ locale.t('adminTeachers.allCategories', 'Все категории') }}</button>
      <button v-for="c in categories" :key="c.key"
              class="rounded-full border px-3 py-1.5 text-xs font-medium transition-colors"
              :class="categoryFilter === c.key ? 'border-accent bg-accent text-white' : 'border-border2 bg-card2 text-text2 hover:border-accent/50'"
              @click="categoryFilter = c.key">{{ c.label }}</button>
    </div>

    <div class="flex flex-wrap items-center gap-3">
      <input v-model="q" :placeholder="locale.t('adminTeachers.searchPlaceholder', 'Поиск по ФИО или логину…')"
             class="h-10 w-full max-w-sm rounded-sm border border-border2 bg-card2 px-3.5 text-sm text-text outline-none focus:border-accent focus:bg-card" />
      <AppButton variant="green" size="sm" @click="openCreate">{{ locale.t('adminTeachers.addAction', '+ Добавить') }}</AppButton>
    </div>

    <StickyXScroll class="rounded-lg border border-border bg-card shadow-card">
      <table class="w-full text-sm">
        <thead>
          <tr class="border-b border-border2 bg-bg2 text-left text-tiny uppercase tracking-wide text-text2">
            <th class="px-4 py-2.5 font-semibold">{{ locale.t('adminTeachers.colFullName', 'ФИО') }}</th>
            <th class="px-4 py-2.5 font-semibold">{{ locale.t('adminTeachers.colLogin', 'Логин') }}</th>
            <th class="px-4 py-2.5 font-semibold">{{ locale.t('adminTeachers.colSubjects', 'Предметы') }}</th>
            <th class="px-4 py-2.5 font-semibold">{{ locale.t('adminTeachers.colLastLogin', 'Посл. вход') }}</th>
            <th class="px-4 py-2.5 font-semibold">{{ locale.t('adminTeachers.colIp', 'IP') }}</th>
            <th class="px-4 py-2.5 text-right font-semibold">{{ locale.t('adminTeachers.colActions', 'Действия') }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="loading"><td colspan="6" class="px-4 py-6 text-center text-text3">{{ locale.t('common.loading') }}</td></tr>
          <tr v-else-if="!rows.length"><td colspan="6" class="px-4 py-6 text-center text-text3">{{ locale.t('adminTeachers.noTeachers', 'Преподавателей нет') }}</td></tr>
          <tr v-for="(t, i) in rows" :key="i" class="border-b border-border last:border-0 hover:bg-bg2/60">
            <td class="whitespace-nowrap px-4 py-2.5 font-medium text-text">
              {{ t.name || '—' }}
              <Badge v-if="t.curated_groups?.length" variant="blue" class="ml-1.5" :title="locale.t('adminTeachers.curatorTitle', { groups: t.curated_groups.join(', ') })">{{ locale.t('adminTeachers.curatorBadge', 'куратор') }}</Badge>
            </td>
            <td class="px-4 py-2.5 text-text2">{{ t.login || '—' }}</td>
            <td class="px-4 py-2.5">
              <div class="flex flex-wrap gap-1.5">
                <Badge v-for="s in (t.subjects || []).slice(0, 4)" :key="s" variant="muted">{{ s }}</Badge>
                <span v-if="(t.subjects?.length || 0) > 4" class="text-xs text-text3">+{{ t.subjects.length - 4 }}</span>
                <span v-if="!t.subjects?.length" class="text-text3">—</span>
              </div>
            </td>
            <td class="whitespace-nowrap px-4 py-2.5 text-text3" :title="t.device ? locale.t('adminTeachers.deviceTitle', { device: t.device }) : ''">{{ fmtDT(t.last_login) }}</td>
            <td class="whitespace-nowrap px-4 py-2.5 text-text3">{{ t.ip || '—' }}</td>
            <td class="whitespace-nowrap px-4 py-2.5 text-right">
              <button class="mr-3 text-text3 hover:text-accent" :title="locale.t('adminTeachers.edit', 'Изменить')" @click="openEdit(t)">✎</button>
              <button class="text-text3 hover:text-red" :title="locale.t('common.delete')" @click="del(t)">✕</button>
            </td>
          </tr>
        </tbody>
      </table>
    </StickyXScroll>

    <div v-if="showForm" class="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" @click.self="showForm = false">
      <div class="w-full max-w-md rounded-lg border border-border bg-card p-5 shadow-card">
        <h3 class="mb-4 font-title text-lg font-bold text-text">{{ editing ? locale.t('adminTeachers.editTitle', 'Изменить преподавателя') : locale.t('adminTeachers.addTitle', 'Добавить преподавателя') }}</h3>
        <div class="space-y-3">
          <label class="block"><span class="mb-1 block text-tiny uppercase text-text3">{{ locale.t('adminTeachers.colFullName', 'ФИО') }}</span>
            <input v-model="form.full_name" placeholder="Иванов Иван Иванович"
                   class="h-10 w-full rounded-sm border border-border2 bg-card2 px-3 text-sm text-text outline-none focus:border-accent" /></label>
          <label class="block"><span class="mb-1 block text-tiny uppercase text-text3">{{ locale.t('adminTeachers.colLogin', 'Логин') }}</span>
            <input v-model="form.login" :disabled="!!editing"
                   class="h-10 w-full rounded-sm border border-border2 bg-card2 px-3 text-sm text-text outline-none focus:border-accent disabled:opacity-60" /></label>
          <div>
            <span class="mb-1 block text-tiny uppercase text-text3">{{ locale.t('adminTeachers.workload', 'Нагрузка (предметы)') }}</span>
            <div class="max-h-48 overflow-y-auto rounded-sm border border-border2 bg-card2 p-2">
              <label v-for="s in allSubjects" :key="s" class="flex cursor-pointer items-center gap-2 rounded px-1 py-1 text-sm text-text hover:bg-bg2">
                <input type="checkbox" :checked="form.subjects.includes(s)" @change="toggleSubject(s)" />
                {{ s }}
              </label>
              <p v-if="!allSubjects.length" class="px-1 py-2 text-xs text-text3">{{ locale.t('adminTeachers.noSubjectsHint', 'Сначала заведите предметы во вкладке «Предметы».') }}</p>
            </div>
          </div>
          <div>
            <span class="mb-1 block text-tiny uppercase text-text3">{{ locale.t('adminTeachers.curationLabel', 'Курирование групп') }} <span class="normal-case text-text3">{{ locale.t('adminTeachers.curationHint', '(куратор видит все предметы группы, только чтение)') }}</span></span>
            <div class="max-h-40 overflow-y-auto rounded-sm border border-border2 bg-card2 p-2">
              <label v-for="g in allGroups" :key="g" class="flex cursor-pointer items-center gap-2 rounded px-1 py-1 text-sm text-text hover:bg-bg2">
                <input type="checkbox" :checked="form.curated_groups.includes(g)" @change="toggleCurated(g)" />
                {{ g }}
              </label>
              <p v-if="!allGroups.length" class="px-1 py-2 text-xs text-text3">{{ locale.t('adminTeachers.noGroupsHint', 'Групп пока нет.') }}</p>
            </div>
          </div>
          <label class="block"><span class="mb-1 block text-tiny uppercase text-text3">{{ editing ? locale.t('adminTeachers.newPasswordHint', 'Новый пароль (пусто — не менять)') : locale.t('adminTeachers.passwordLabel', 'Пароль') }}</span>
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
          <AppButton variant="green" size="sm" :disabled="saving" @click="save">{{ saving ? locale.t('adminTeachers.saving', 'Сохранение…') : (editing ? locale.t('common.save') : locale.t('adminTeachers.addAction2', 'Добавить')) }}</AppButton>
        </div>
      </div>
    </div>
  </div>
</template>
