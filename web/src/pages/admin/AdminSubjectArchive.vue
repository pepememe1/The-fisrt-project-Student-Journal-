<script setup>
// AdminSubjectArchive — архив предметов группы по семестрам (живой запрос 3.6.1:
// «текущие [при парсинге с сайта] должны перезаписаться, а старые улететь в архив,
// где будут видны в админке»). Группа → список термина, у ТЕКУЩЕГО термина видно и
// действующий план, и то, что последний реимпорт/правка плана только что вытеснили
// (помечено «Архив» — строка SubjectHours погашена, см. write._archive_dropped_subjects).
import { ref, computed, onMounted, watch } from 'vue'
import StickyXScroll from '@/components/ui/StickyXScroll.vue'
import { ChevronDown } from '@lucide/vue'
import { adminApi, scheduleApi } from '@/api/endpoints'
import EmptyState from '@/components/ui/EmptyState.vue'
import Badge from '@/components/ui/Badge.vue'
import { useLocaleStore } from '@/stores/locale'

const locale = useLocaleStore()

const DEFAULT_CATEGORY = 'college'
const categories = ref([])
const category = ref(DEFAULT_CATEGORY)
const groups = ref([])
const group = ref('')
const loading = ref(false)
const data = ref(null)

// Сортировка по категории портала (§правка, 3.6.1) — тот же реестр и тот же паттерн
// фильтрации, что уже есть в AdminSchedule.vue/SchedulePage.vue: группы без сохранённой
// категории (заведены до её появления) считаются колледжем.
const groupsForCategory = computed(() =>
  groups.value.filter((g) => (g.category || DEFAULT_CATEGORY) === category.value))

async function onCategoryChange(key) {
  if (key === category.value) return
  category.value = key
  const list = groupsForCategory.value
  if (list.length && !list.some((g) => g.name === group.value)) group.value = list[0].name
  else if (!list.length) { group.value = ''; data.value = null }
}

function seasonLabel(semester) {
  return semester === 1 ? locale.t('adminGroups.seasonFall', 'осенний') : locale.t('adminGroups.seasonSpring', 'весенний')
}
function termLabel(t) {
  return locale.t('adminGroups.termLabel', { year: t.year, semester: seasonLabel(t.semester) })
}

async function load() {
  if (!group.value) { data.value = null; return }
  loading.value = true
  try { data.value = (await adminApi.groupSubjectArchive(group.value)).data }
  catch { data.value = null } finally { loading.value = false }
}
watch(group, load)

// Предметы семестра свёрнуты по умолчанию (§правка, 3.6.1, живой отзыв) — таблица на
// десяток строк на каждый семестр разворачивала бы страницу в длиннющую простыню при
// каждом заходе. Тот же приём, что уже применён к курируемым группам во вкладке
// «ИИ Помощник» (TeacherOverview.vue::expandedCurator) — реактивный Set ключей термина.
const expandedTerms = ref(new Set())
function termKey(t) { return `${t.year}|${t.semester}` }
function isExpanded(t) { return expandedTerms.value.has(termKey(t)) }
function toggleTerm(t) {
  const k = termKey(t)
  const next = new Set(expandedTerms.value)
  if (next.has(k)) next.delete(k); else next.add(k)
  expandedTerms.value = next
}

onMounted(async () => {
  try {
    const r = (await scheduleApi.categories()).data
    categories.value = r.categories || []
    category.value = r.default || DEFAULT_CATEGORY
  } catch { categories.value = []; category.value = DEFAULT_CATEGORY }
  try {
    groups.value = (await adminApi.groups()).data.groups || []
    group.value = groupsForCategory.value[0]?.name || ''
  } catch { groups.value = [] }
  await load()
})
</script>

<template>
  <div class="space-y-4">
    <EmptyState v-if="!groups.length" :title="locale.t('adminSubjectArchive.noGroupsTitle', 'Групп нет')"
                :message="locale.t('adminSubjectArchive.noGroupsMessage', 'Сначала заведите хотя бы одну группу во вкладке «Группы».')" />
    <template v-else>
      <!-- Категории портала (§правка, 3.6.1) — тот же реестр/паттерн, что в «Расписании»/
           «Группах»: сортируем список групп по колледжу/бакалавриату/заочному, а не
           одним плоским списком вперемешку. -->
      <div v-if="categories.length > 1" class="flex flex-wrap gap-2">
        <button v-for="c in categories" :key="c.key"
                class="rounded-full border px-3 py-1.5 text-xs font-medium transition-colors"
                :class="category === c.key
                  ? 'border-accent bg-accent text-white'
                  : 'border-border2 bg-card2 text-text2 hover:border-accent/50'"
                @click="onCategoryChange(c.key)">
          {{ c.label }}
        </button>
      </div>

      <div class="flex flex-wrap items-center gap-3">
        <select v-model="group" class="h-10 w-full rounded-sm border border-border2 bg-card2 px-3 text-sm text-text outline-none focus:border-accent sm:w-auto sm:min-w-52">
          <option v-for="g in groupsForCategory" :key="g.name" :value="g.name">{{ g.name }}</option>
        </select>
        <p class="text-xs text-text3">
          {{ locale.t('adminSubjectArchive.hint', 'Текущий семестр показывает и действующий план, и предметы, которые только что убрал реимпорт/правка плана.') }}
        </p>
      </div>

      <p v-if="loading" class="text-sm text-text3">{{ locale.t('common.loading') }}</p>
      <EmptyState v-else-if="!groupsForCategory.length" :title="locale.t('adminSubjectArchive.noGroupsTitle', 'Групп нет')"
                  :message="locale.t('adminSubjectArchive.noGroupsCategoryMessage', 'В этой категории пока нет ни одной группы.')" />
      <EmptyState v-else-if="!data?.terms?.length" :title="locale.t('adminSubjectArchive.emptyTitle', 'Нет данных')"
                  :message="locale.t('adminSubjectArchive.emptyMessage', 'У этой группы пока нет ни плана, ни занятий ни за один семестр.')" />

      <div v-else class="space-y-4">
        <div v-for="t in data.terms" :key="`${t.year}-${t.semester}`"
             class="overflow-hidden rounded-lg border border-border bg-card shadow-card">
          <!-- Предметы свёрнуты по умолчанию — раскрывает стрелка справа. -->
          <button type="button" class="flex w-full items-center gap-2 border-b border-border bg-bg2 px-4 py-2.5 text-left"
                  @click="toggleTerm(t)">
            <span class="font-title text-sm font-bold text-text">{{ termLabel(t) }}</span>
            <Badge v-if="t.is_current" variant="green">{{ locale.t('adminSubjectArchive.currentBadge', 'текущий') }}</Badge>
            <span class="ml-auto text-tiny text-text3">{{ locale.t('adminSubjectArchive.subjectsCount', { n: t.subjects.length }) }}</span>
            <ChevronDown class="size-4 shrink-0 text-text3 transition-transform" :class="isExpanded(t) ? 'rotate-180' : ''" />
          </button>
          <StickyXScroll v-if="isExpanded(t)">
            <table class="w-full text-sm">
              <thead>
                <tr class="border-b border-border2 text-left text-tiny uppercase tracking-wide text-text2">
                  <th class="py-2 pl-4 pr-3 font-semibold">{{ locale.t('adminSubjectArchive.colSubject', 'Предмет') }}</th>
                  <th class="py-2 pr-3 font-semibold">{{ locale.t('adminSubjectArchive.colHours', 'Часы') }}</th>
                  <th class="py-2 pr-3 font-semibold">{{ locale.t('adminSubjectArchive.colZet', 'ЗЕТ') }}</th>
                  <th class="py-2 pr-3 font-semibold">{{ locale.t('adminSubjectArchive.colTeacher', 'Преподаватель') }}</th>
                  <th class="py-2 pr-4 text-right font-semibold">{{ locale.t('adminSubjectArchive.colStatus', 'Статус') }}</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="s in t.subjects" :key="s.subject" class="border-b border-border last:border-0"
                    :class="s.active ? '' : 'opacity-60'">
                  <td class="py-2 pl-4 pr-3 text-text">{{ s.subject }}</td>
                  <td class="py-2 pr-3 text-text2">{{ s.hours_total || '—' }}</td>
                  <td class="py-2 pr-3 text-text2">{{ s.zet ?? '—' }}</td>
                  <td class="py-2 pr-3 text-text2">{{ s.teacher_name || '—' }}</td>
                  <td class="py-2 pr-4 text-right">
                    <Badge v-if="s.active" variant="green">{{ locale.t('adminSubjectArchive.active', 'активен') }}</Badge>
                    <Badge v-else variant="muted">{{ locale.t('adminSubjectArchive.archived', 'архив') }}</Badge>
                  </td>
                </tr>
              </tbody>
            </table>
          </StickyXScroll>
        </div>
      </div>
    </template>
  </div>
</template>
