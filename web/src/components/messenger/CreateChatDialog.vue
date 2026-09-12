<script setup>
// CreateChatDialog — создание группы или канала (kind). Название + (канал) публичность +
// выбор участников/писателей через поиск по каталогу. Наверх уходит create-событие.
import { ref, watch, computed, onMounted } from 'vue'
import { X, Search, Check } from '@lucide/vue'
import { messengerApi, curatorApi } from '@/api/endpoints'
import { useAuthStore } from '@/stores/auth'
import { useLocaleStore } from '@/stores/locale'

const props = defineProps({
  kind: { type: String, default: 'group' },   // group | channel
  //Кого отметить сразу. Приходит из трёх точек карточки профиля («Добавить в группу»):
  //человек уже выбран, и заставлять его искать себя же в каталоге незачем.
  preset: { type: Object, default: null },
})
const emit = defineEmits(['create', 'close'])

const auth = useAuthStore()
const locale = useLocaleStore()
const isChannel = computed(() => props.kind === 'channel')
const title = ref('')
const about = ref('')
const isPublic = ref(true)
const role = ref('student')
const q = ref('')
const found = ref([])
const chosen = ref(props.preset
  ? [{ id: props.preset.id, full_name: props.preset.full_name, role: props.preset.role }]
  : [])          // [{id, full_name}]
let debounce = null

async function search() {
  try { found.value = (await messengerApi.users(role.value, q.value)).data.users || [] }
  catch { found.value = [] }
}
watch([role, q], () => { clearTimeout(debounce); debounce = setTimeout(search, 250) })
search()

function toggle(u) {
  const i = chosen.value.findIndex(x => x.id === u.id)
  if (i >= 0) chosen.value.splice(i, 1)
  //Роль запоминаем вместе с человеком: по ней решается, показывать ли подсказку про
  //подтверждение. Вкладка (role.value) для этого не годится — выбирают из разных.
  else chosen.value.push({ id: u.id, full_name: u.full_name, role: u.user_role || u.role || role.value })
}
const isChosen = (id) => chosen.value.some(x => x.id === id)

// §12: режим куратора — при создании ГРУППЫ преподаватель-куратор может разом добавить
// ВСЮ учебную группу (все её студенты станут участниками), вперемешку с отдельными
// людьми из обычного поиска выше. Список — ТОЛЬКО его собственные curated_groups
// (сервер тоже это проверит — см. create_group в messenger.py).
const curatedGroups = ref([])
const chosenGroups = ref([])
//Куратора проверяем ВСЕГДА (группа и канал) — от неё зависит не только блок «целая
//группа» (он группо-only, см. v-if ниже), но и вкладка «Родители» в поиске участников/
//авторов (roleTabs), которая нужна и при создании канала тоже.
onMounted(async () => {
  if (auth.role !== 'teacher') return
  try { curatedGroups.value = (await curatorApi.groups()).data.groups || [] } catch { /* не куратор */ }
})
function toggleGroup(g) {
  const i = chosenGroups.value.indexOf(g)
  if (i >= 0) chosenGroups.value.splice(i, 1)
  else chosenGroups.value.push(g)
}
// Студент, выбравший преподавателя, должен ЗАРАНЕЕ понимать, что тот появится не сразу:
// иначе он создаст группу, не увидит преподавателя в списке и решит, что не сработало.
const staffChosen = computed(() =>
  auth.role === 'student' && chosen.value.some(x => x.role === 'teacher' || x.role === 'admin'))

const roleTabs = computed(() => {
  const t = [['student', locale.t('messenger.tab.student', 'Студенты')], ['teacher', locale.t('createChat.teachers', 'Преподаватели')]]
  if (curatedGroups.value.length) t.push(['parent', locale.t('messenger.tab.parent', 'Родители')])
  return t
})

function submit() {
  const t = title.value.trim()
  if (!t) return
  emit('create', { title: t, ids: chosen.value.map(x => x.id), isPublic: isPublic.value,
    about: about.value.trim(), classGroups: chosenGroups.value })
}
</script>

<template>
  <div class="fixed inset-0 z-50 grid place-items-center p-4" style="background: var(--gb-overlay)"
       @click.self="emit('close')">
    <div class="flex max-h-[85vh] w-full max-w-md flex-col rounded-xl border border-border2 bg-card shadow-card">
      <div class="flex items-center justify-between border-b border-border p-4">
        <h3 class="font-title text-lg font-bold text-text">{{ isChannel ? locale.t('messenger.newChannel', 'Новый канал') : locale.t('messenger.newGroup', 'Новая группа') }}</h3>
        <button type="button" @click="emit('close')" class="grid size-8 place-items-center rounded-md text-text3 hover:bg-bg2 hover:text-text"><X class="size-5" /></button>
      </div>

      <div class="min-h-0 flex-1 overflow-y-auto p-4">
        <input v-model="title" :placeholder="isChannel ? locale.t('createChat.channelName', 'Название канала') : locale.t('createChat.groupName', 'Название группы')"
               class="mb-2 w-full rounded-lg border border-border2 bg-card2 px-3 py-2 text-sm text-text outline-none focus:border-accent" />
        <input v-model="about" :placeholder="locale.t('createChat.descOptional', 'Описание (необязательно)')"
               class="mb-2 w-full rounded-lg border border-border2 bg-card2 px-3 py-2 text-sm text-text outline-none focus:border-accent" />
        <label v-if="isChannel" class="mb-3 flex items-center gap-2 text-sm text-text2">
          <input type="checkbox" v-model="isPublic" class="accent-[var(--gb-accent)]" /> {{ locale.t('createChat.public', 'Публичный (виден в каталоге)') }}
        </label>

        <!-- §12: режим куратора — целые учебные группы одним нажатием. -->
        <div v-if="!isChannel && curatedGroups.length" class="mb-3">
          <p class="mb-1 text-xs font-semibold text-text3">{{ locale.t('createChat.addWholeGroup', 'Добавить целую группу (куратор)') }}</p>
          <div class="flex flex-wrap gap-1.5">
            <button v-for="g in curatedGroups" :key="g" type="button" @click="toggleGroup(g)"
                    class="rounded-full border px-2.5 py-1 text-xs font-semibold transition-colors"
                    :class="chosenGroups.includes(g) ? 'border-accent bg-accent-glow text-accent' : 'border-border2 text-text3 hover:bg-bg2'">
              {{ g }}
            </button>
          </div>
        </div>

        <p class="mb-1 mt-2 text-xs font-semibold text-text3">
          {{ isChannel ? locale.t('createChat.authors', 'Авторы (могут публиковать)') : locale.t('createChat.membersIndividually', 'Участники по отдельности') }}
        </p>
        <!-- выбранные чипсами -->
        <div v-if="chosen.length" class="mb-2 flex flex-wrap gap-1">
          <span v-for="p in chosen" :key="p.id" class="inline-flex items-center gap-1 rounded-full bg-accent-glow px-2 py-0.5 text-xs text-accent">
            {{ p.full_name }}
            <button type="button" @click="toggle({ id: p.id })" class="hover:text-red">✕</button>
          </span>
        </div>
        <div class="mb-2 flex gap-1">
          <button v-for="r in roleTabs" :key="r[0]" type="button"
                  @click="role = r[0]" class="rounded-md px-2 py-1 text-xs font-semibold"
                  :class="role === r[0] ? 'bg-accent-glow text-accent' : 'text-text3 hover:bg-bg2'">{{ r[1] }}</button>
        </div>
        <div class="mb-2 flex items-center gap-2 rounded-lg border border-border2 bg-card2 px-3">
          <Search class="size-4 text-text3" />
          <input v-model="q" :placeholder="locale.t('messenger.searchByName', 'Поиск по ФИО…')" class="h-9 min-w-0 flex-1 bg-transparent text-sm text-text outline-none" />
        </div>
        <div class="max-h-52 overflow-y-auto rounded-lg border border-border">
          <button v-for="u in found" :key="u.id" type="button" @click="toggle(u)"
                  class="flex w-full items-center justify-between gap-2 border-b border-border/50 px-3 py-2 text-left text-sm hover:bg-bg2">
            <span class="min-w-0 truncate text-text">
              {{ u.full_name }}
              <span v-if="u.role === 'parent' && u.groups?.length" class="text-text3"> · {{ locale.t('messenger.parentOf', 'род. ') }}{{ u.groups.join(', ') }}</span>
            </span>
            <span class="grid size-5 shrink-0 place-items-center rounded-full border"
                  :class="isChosen(u.id) ? 'border-accent bg-accent text-white' : 'border-border2'">
              <Check v-if="isChosen(u.id)" class="size-3.5" />
            </span>
          </button>
          <p v-if="!found.length" class="p-3 text-center text-xs text-text3">{{ locale.t('messenger.nobodyFound', 'Никого не найдено.') }}</p>
        </div>
      </div>

      <!-- Студент выбрал преподавателя: он появится в беседе не сразу, а когда согласится.
           Сказать это НАДО ЗАРАНЕЕ — иначе человек создаст группу, не увидит там
           преподавателя и решит, что выбор не сохранился. -->
      <p v-if="staffChosen" class="border-t border-border px-3 py-2 text-xs text-text3">
        {{ locale.t('createChat.staffNeedsConfirm', 'Преподаватель получит приглашение и войдёт в беседу, когда его примет.') }}
      </p>

      <div class="flex justify-end gap-2 border-t border-border p-3">
        <button type="button" @click="emit('close')" class="rounded-lg border border-border2 px-4 py-2 text-sm text-text2 hover:bg-bg2">{{ locale.t('common.cancel') }}</button>
        <button type="button" :disabled="!title.trim()" @click="submit"
                class="rounded-lg bg-accent px-4 py-2 text-sm font-semibold text-white hover:bg-accent2 disabled:opacity-50">
          {{ locale.t('createChat.create', 'Создать') }}
        </button>
      </div>
    </div>
  </div>
</template>
