<script setup>
// CourseDetailPage — один курс: структура (разделы с материалами), материалы вне
// разделов и задания. Автору курса и админу доступно редактирование (can_edit с сервера).
import { ref, computed, onMounted } from 'vue'
import StickyXScroll from '@/components/ui/StickyXScroll.vue'
import { useRoute, useRouter } from 'vue-router'
import {
  ArrowLeft, BookOpen, Paperclip, Link2, ClipboardList, Plus, Trash2, Archive,
  Loader2, X, Users,
} from '@lucide/vue'
import { coursesApi } from '@/api/endpoints'
import { useAuthStore } from '@/stores/auth'
import { useLocaleStore } from '@/stores/locale'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const loc = useLocaleStore()
const t = (k, f) => loc.t(k, f)

const id = computed(() => route.params.id)
const course = ref(null)
const loading = ref(true)
const notFound = ref(false)

// Формы добавления (простые инлайн-поля, показываются по кнопке)
const newSection = ref('')
const matDraft = ref({ open: 0, title: '', url: '' })   // open = section_id (или -1 для «вне раздела»)
const asgDraft = ref({ open: false, title: '', due: '' })

async function load() {
  loading.value = true; notFound.value = false
  try {
    const { data } = await coursesApi.get(id.value)
    course.value = data
  } catch (e) {
    if (e?.response?.status === 404 || e?.response?.status === 403) notFound.value = true
    course.value = null
  } finally { loading.value = false }
}
onMounted(load)

const canEdit = computed(() => !!course.value?.can_edit)

async function addSection() {
  const title = newSection.value.trim()
  if (!title) return
  await coursesApi.addSection(id.value, title, (course.value.sections?.length || 0) + 1)
  newSection.value = ''
  await load()
}
async function delSection(sid) {
  if (!confirm(t('courses.confirmDelSection', 'Удалить раздел? Материалы останутся (вне разделов).'))) return
  await coursesApi.delSection(id.value, sid); await load()
}
async function addMaterial(sectionId) {
  const title = matDraft.value.title.trim()
  const url = matDraft.value.url.trim()
  if (!url) return
  await coursesApi.addMaterial(id.value, { title: title || url, url, kind: 'link', sectionId: sectionId > 0 ? sectionId : 0 })
  matDraft.value = { open: 0, title: '', url: '' }
  await load()
}
async function delMaterial(mid) {
  await coursesApi.delMaterial(id.value, mid); await load()
}
async function addAssignment() {
  const title = asgDraft.value.title.trim()
  if (!title) return
  await coursesApi.addAssignment(id.value, { title, dueDate: asgDraft.value.due.trim() })
  asgDraft.value = { open: false, title: '', due: '' }
  await load()
}
async function delAssignment(aid) {
  await coursesApi.delAssignment(id.value, aid); await load()
}
async function archive() {
  if (!confirm(t('courses.confirmArchive', 'Отправить курс в архив? Студенты перестанут его видеть.'))) return
  await coursesApi.archive(id.value)
  router.push(`/${auth.role}/courses`)
}
</script>

<template>
  <div>
    <button type="button" @click="router.push(`/${auth.role}/courses`)"
            class="mb-3 flex items-center gap-1.5 text-sm text-text2 transition-colors hover:text-accent">
      <ArrowLeft class="size-4" /> {{ t('courses.back', 'К списку курсов') }}
    </button>

    <div v-if="loading" class="grid place-items-center py-16 text-text3"><Loader2 class="size-6 animate-spin" /></div>

    <div v-else-if="notFound || !course" class="grid place-items-center gap-2 py-16 text-center text-text3">
      <X class="size-10 opacity-40" />
      <p>{{ t('courses.noAccess', 'Курс недоступен или не найден') }}</p>
    </div>

    <template v-else>
      <!-- Шапка курса -->
      <div class="mb-4 rounded-xl border border-border2 bg-card p-4 shadow-card">
        <div class="flex items-start gap-3">
          <span class="grid size-11 shrink-0 place-items-center rounded-lg bg-accent-glow text-accent"><BookOpen class="size-6" /></span>
          <div class="min-w-0 flex-1">
            <p class="text-xs text-text3">{{ course.subject || '—' }}<span v-if="course.group_name"> · {{ course.group_name }}</span></p>
            <h1 class="font-title text-lg font-extrabold text-text">{{ course.title }}</h1>
            <p v-if="course.authors?.length" class="mt-1 flex items-center gap-1.5 text-xs text-text2">
              <Users class="size-3.5 shrink-0" />{{ course.authors.map(a => a.name).filter(Boolean).join(', ') }}
            </p>
          </div>
          <button v-if="canEdit && !course.archived" type="button" @click="archive"
                  class="flex shrink-0 items-center gap-1.5 rounded-md border border-border2 px-2.5 py-1.5 text-xs text-text2 transition-colors hover:border-red hover:text-red">
            <Archive class="size-3.5" /> {{ t('courses.archive', 'В архив') }}
          </button>
        </div>
        <p v-if="course.description" class="mt-2 whitespace-pre-wrap border-t border-border pt-2 text-sm text-text2">{{ course.description }}</p>
      </div>

      <!-- Структура курса -->
      <section class="mb-4">
        <h2 class="mb-2 flex items-center gap-2 font-title text-sm font-bold text-text">
          <ClipboardList class="size-4 text-accent" /> {{ t('courses.structure', 'Структура курса') }}
        </h2>
        <div class="rounded-xl border border-border2 bg-card p-2 shadow-card">
          <ol v-if="course.sections.length" class="flex flex-col">
            <li v-for="(s, i) in course.sections" :key="s.id" class="rounded-lg px-2 py-2 hover:bg-bg2">
              <div class="flex items-center gap-2">
                <span class="grid size-6 shrink-0 place-items-center rounded-full bg-accent-glow text-xs font-bold text-accent">{{ i + 1 }}</span>
                <span class="min-w-0 flex-1 truncate text-sm font-medium text-text">{{ s.title }}</span>
                <button v-if="canEdit" type="button" @click="delSection(s.id)"
                        class="grid size-6 shrink-0 place-items-center rounded text-text3 hover:bg-red/10 hover:text-red"><Trash2 class="size-3.5" /></button>
              </div>
              <!-- Материалы раздела -->
              <div v-if="s.materials.length || canEdit" class="ml-8 mt-1.5 flex flex-col gap-1">
                <div v-for="m in s.materials" :key="m.id" class="flex items-center gap-2">
                  <a :href="m.url" target="_blank" rel="noopener noreferrer"
                     class="flex min-w-0 flex-1 items-center gap-1.5 text-sm text-blue hover:underline">
                    <Link2 class="size-3.5 shrink-0" /><span class="truncate">{{ m.title }}</span>
                  </a>
                  <button v-if="canEdit" type="button" @click="delMaterial(m.id)"
                          class="grid size-5 shrink-0 place-items-center rounded text-text3 hover:text-red"><Trash2 class="size-3" /></button>
                </div>
                <!-- добавить материал в раздел -->
                <div v-if="canEdit" class="mt-1">
                  <div v-if="matDraft.open === s.id" class="flex flex-wrap items-center gap-1.5">
                    <input v-model="matDraft.title" :placeholder="t('courses.mTitle', 'Название')"
                           class="h-8 min-w-0 flex-1 rounded border border-border2 bg-card2 px-2 text-xs text-text outline-none focus:border-accent" />
                    <input v-model="matDraft.url" placeholder="https://…" @keydown.enter="addMaterial(s.id)"
                           class="h-8 min-w-0 flex-[2] rounded border border-border2 bg-card2 px-2 text-xs text-text outline-none focus:border-accent" />
                    <button type="button" @click="addMaterial(s.id)" class="h-8 rounded bg-accent px-2 text-xs font-semibold text-white hover:bg-accent2">{{ t('common.add', 'Добавить') }}</button>
                    <button type="button" @click="matDraft.open = 0" class="grid size-8 place-items-center rounded text-text3 hover:text-text"><X class="size-4" /></button>
                  </div>
                  <button v-else type="button" @click="matDraft = { open: s.id, title: '', url: '' }"
                          class="flex items-center gap-1 text-xs text-text3 hover:text-accent"><Plus class="size-3.5" />{{ t('courses.addMaterial', 'Материал') }}</button>
                </div>
              </div>
            </li>
          </ol>
          <p v-else-if="!canEdit" class="px-2 py-3 text-sm text-text3">{{ t('courses.noStructure', 'Структура ещё не заполнена') }}</p>

          <!-- добавить раздел -->
          <div v-if="canEdit" class="mt-1 flex items-center gap-1.5 border-t border-border px-2 pt-2">
            <input v-model="newSection" :placeholder="t('courses.newSection', 'Новый раздел…')" @keydown.enter="addSection"
                   class="h-8 min-w-0 flex-1 rounded border border-border2 bg-card2 px-2 text-xs text-text outline-none focus:border-accent" />
            <button type="button" @click="addSection" class="flex h-8 items-center gap-1 rounded bg-accent px-2.5 text-xs font-semibold text-white hover:bg-accent2"><Plus class="size-3.5" />{{ t('courses.addSection', 'Раздел') }}</button>
          </div>
        </div>
      </section>

      <!-- Материалы вне разделов -->
      <section v-if="course.materials.length || canEdit" class="mb-4">
        <h2 class="mb-2 flex items-center gap-2 font-title text-sm font-bold text-text">
          <Paperclip class="size-4 text-accent" /> {{ t('courses.materials', 'Материалы') }}
        </h2>
        <div class="rounded-xl border border-border2 bg-card p-3 shadow-card">
          <div class="flex flex-wrap gap-2">
            <div v-for="m in course.materials" :key="m.id"
                 class="flex items-center gap-1.5 rounded-lg border border-border2 bg-card2 px-2.5 py-1.5">
              <a :href="m.url" target="_blank" rel="noopener noreferrer" class="flex items-center gap-1.5 text-sm text-blue hover:underline">
                <Link2 class="size-3.5 shrink-0" /><span class="min-w-0 max-w-[220px] truncate">{{ m.title }}</span>
              </a>
              <button v-if="canEdit" type="button" @click="delMaterial(m.id)" class="text-text3 hover:text-red"><Trash2 class="size-3.5" /></button>
            </div>
            <p v-if="!course.materials.length && canEdit" class="text-sm text-text3">{{ t('courses.noMaterials', 'Материалов вне разделов нет') }}</p>
          </div>
          <div v-if="canEdit" class="mt-2 flex flex-wrap items-center gap-1.5 border-t border-border pt-2">
            <input v-model="matDraft.title" :placeholder="t('courses.mTitle', 'Название')"
                   class="h-8 min-w-0 flex-1 rounded border border-border2 bg-card2 px-2 text-xs text-text outline-none focus:border-accent" />
            <input v-model="matDraft.url" placeholder="https://…" @keydown.enter="addMaterial(-1)"
                   class="h-8 min-w-0 flex-[2] rounded border border-border2 bg-card2 px-2 text-xs text-text outline-none focus:border-accent" />
            <button type="button" @click="addMaterial(-1)" class="h-8 rounded bg-accent px-2.5 text-xs font-semibold text-white hover:bg-accent2">{{ t('common.add', 'Добавить') }}</button>
          </div>
        </div>
      </section>

      <!-- Задания -->
      <section>
        <h2 class="mb-2 flex items-center gap-2 font-title text-sm font-bold text-text">
          <ClipboardList class="size-4 text-accent" /> {{ t('courses.assignments', 'Задания') }}
        </h2>
        <StickyXScroll class="rounded-xl border border-border2 bg-card shadow-card">
          <table class="w-full min-w-[520px] text-sm">
            <thead>
              <tr class="border-b border-border text-left text-xs text-text3">
                <th class="px-3 py-2 font-medium">№</th>
                <th class="px-3 py-2 font-medium">{{ t('courses.aName', 'Наименование работы') }}</th>
                <th class="px-3 py-2 font-medium">{{ t('courses.aDue', 'Выполнить до') }}</th>
                <th class="px-3 py-2 font-medium">{{ t('courses.aTeacher', 'Кто выдал') }}</th>
                <th v-if="canEdit" class="px-3 py-2"></th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(a, i) in course.assignments" :key="a.id" class="border-b border-border last:border-0">
                <td class="px-3 py-2 text-text3">{{ i + 1 }}</td>
                <td class="px-3 py-2 font-medium text-text">
                  <a v-if="a.url" :href="a.url" target="_blank" rel="noopener noreferrer" class="text-blue hover:underline">{{ a.title }}</a>
                  <span v-else>{{ a.title }}</span>
                </td>
                <td class="px-3 py-2 text-text2">{{ a.due_date || '—' }}</td>
                <td class="px-3 py-2 text-text2">{{ a.teacher_name || '—' }}</td>
                <td v-if="canEdit" class="px-3 py-2 text-right">
                  <button type="button" @click="delAssignment(a.id)" class="text-text3 hover:text-red"><Trash2 class="size-3.5" /></button>
                </td>
              </tr>
              <tr v-if="!course.assignments.length">
                <td :colspan="canEdit ? 5 : 4" class="px-3 py-6 text-center text-text3">{{ t('courses.noAssignments', 'Заданий пока нет') }}</td>
              </tr>
            </tbody>
          </table>
          <!-- добавить задание -->
          <div v-if="canEdit" class="flex flex-wrap items-center gap-1.5 border-t border-border p-2">
            <input v-model="asgDraft.title" :placeholder="t('courses.aName', 'Наименование работы')"
                   class="h-8 min-w-0 flex-[2] rounded border border-border2 bg-card2 px-2 text-xs text-text outline-none focus:border-accent" />
            <input v-model="asgDraft.due" placeholder="ДД.ММ.ГГГГ" @keydown.enter="addAssignment"
                   class="h-8 w-32 rounded border border-border2 bg-card2 px-2 text-xs text-text outline-none focus:border-accent" />
            <button type="button" @click="addAssignment" class="flex h-8 items-center gap-1 rounded bg-accent px-2.5 text-xs font-semibold text-white hover:bg-accent2"><Plus class="size-3.5" />{{ t('courses.addAssignment', 'Задание') }}</button>
          </div>
        </StickyXScroll>
      </section>
    </template>
  </div>
</template>
