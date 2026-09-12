<script setup>
/**
 * AdminModerators — учётные записи МОДЕРАТОРОВ: завести, сменить пароль, убрать.
 *
 * 🔑 ЗАЧЕМ (12.09.2026, требование Влада «пароль модератора по аналогии с паролем
 * админа»). До этого модератор заводился ТОЛЬКО консольным скриптом на боевой машине, то
 * есть завести человека мог лишь тот, у кого есть ssh. Для роли, которую по замыслу
 * назначает администратор колледжа, это тупик: он админ продукта, а не сервера.
 *
 * 🔒 ДВЕРЬ АДМИНСКАЯ, И ПРОВЕРЯЕТ ЕЁ СЕРВЕР (`require_admin`). Модератор не заводит
 * модераторов и не перевыдаёт пароли: иначе роль, созданная разбирать жалобы, сама
 * выписывает себе подкрепление и меняет пароль тому, кто её проверяет. Спрятанный пункт
 * меню — подсказка, а не защита: её обходит прямой запрос.
 *
 * ⚠️ ПУСТОЕ ПОЛЕ ПАРОЛЯ = «НЕ МЕНЯТЬ», а не «стереть». Страница пароль не показывает и
 * показать не может (в базе только хеш), поэтому при правке имени поле придёт пустым —
 * молчаливое стирание выбило бы человека из продукта, и причину он бы не узнал. То же
 * правило, что у ключа GigaChat и у пароля сервера.
 *
 * ⚠️ Консольный скрипт НЕ УДАЛЁН и остаётся запасным путём: эта страница требует живого
 * администратора, а первый запуск на чистой машине бывает и без него.
 */
import { ref, computed, onMounted } from 'vue'
import { adminApi } from '@/api/endpoints'
import AppButton from '@/components/ui/AppButton.vue'
import { useToast } from '@/composables/useToast'
import { useConfirm } from '@/composables/useConfirm'
import { useLocaleStore } from '@/stores/locale'
import { ShieldCheck, KeyRound, Trash2, UserPlus } from '@lucide/vue'

const locale = useLocaleStore()
const toast = useToast()
const { confirm } = useConfirm()

/**
 * Минимальная длина пароля.
 *
 * ⚠️ ВТОРАЯ КОПИЯ ПО НЕОБХОДИМОСТИ, как у сроков мьюта: сервер обязан проверять границу
 * сам (кнопку обходит прямой запрос), клиент обязан её предлагать, а сослаться друг на
 * друга им нечем. Поэтому расхождение ловит тест (`web/tests/moderatorAdmin.test.mjs`),
 * а не договорённость: разойдись они — админ введёт пароль, который сервер не примет,
 * и решит, что сломалась страница.
 */
const MIN_PASSWORD = 8

const rows = ref([])
const loading = ref(true)
const loadError = ref('')

const showForm = ref(false)
const fLogin = ref('')
const fName = ref('')
const fPassword = ref('')
const saving = ref(false)
const formError = ref('')

const pwFor = ref('')          //логин, которому сейчас меняют пароль
const pwValue = ref('')
const pwError = ref('')

const canSave = computed(() =>
  fLogin.value.trim().length > 0 && fPassword.value.length >= MIN_PASSWORD)

async function reload() {
  loading.value = true
  loadError.value = ''
  try {
    rows.value = (await adminApi.moderators()).data.moderators || []
  } catch (e) {
    //⚠️ «Не удалось загрузить» и «никого нет» — РАЗНЫЕ сообщения. Показав пустоту при
    //сбое сети, мы говорим администратору «модераторов нет», и он заведёт второго.
    rows.value = []
    loadError.value = e?.response?.data?.detail
      || locale.t('adminModerators.loadFailed', 'Не удалось загрузить список.')
  } finally {
    loading.value = false
  }
}
onMounted(reload)

function openCreate() {
  fLogin.value = ''
  fName.value = ''
  fPassword.value = ''
  formError.value = ''
  showForm.value = true
}

async function save() {
  if (!canSave.value) return
  saving.value = true
  formError.value = ''
  try {
    await adminApi.createModerator({
      login: fLogin.value.trim(),
      full_name: fName.value.trim(),
      password: fPassword.value,
    })
    showForm.value = false
    //Пароль в памяти страницы не держим ни секунды дольше нужного.
    fPassword.value = ''
    toast.show(locale.t('adminModerators.created', 'Модератор заведён'))
    await reload()
  } catch (e) {
    formError.value = e?.response?.data?.detail
      || locale.t('adminModerators.saveFailed', 'Не удалось сохранить')
  } finally {
    saving.value = false
  }
}

function openPassword(login) {
  pwFor.value = login
  pwValue.value = ''
  pwError.value = ''
}

async function savePassword() {
  if (pwValue.value.length < MIN_PASSWORD) {
    pwError.value = locale.t('adminModerators.tooShort', { n: MIN_PASSWORD })
    return
  }
  try {
    await adminApi.updateModerator(pwFor.value, { password: pwValue.value })
    pwFor.value = ''
    pwValue.value = ''
    toast.show(locale.t('adminModerators.passwordChanged', 'Пароль изменён'))
    await reload()
  } catch (e) {
    pwError.value = e?.response?.data?.detail
      || locale.t('adminModerators.saveFailed', 'Не удалось сохранить')
  }
}

async function remove(row) {
  const ok = await confirm({
    title: locale.t('adminModerators.removeTitle', 'Убрать модератора?'),
    message: locale.t('adminModerators.removeBody',
      'Человек потеряет доступ к жалобам и модерации чатов. Переписка и жалобы останутся.'),
  })
  if (!ok) return
  try {
    await adminApi.deleteModerator(row.login)
    await reload()
  } catch (e) {
    toast.show(e?.response?.data?.detail
      || locale.t('adminModerators.saveFailed', 'Не удалось сохранить'))
  }
}
</script>

<template>
  <div class="space-y-4">
    <div class="flex flex-wrap items-center justify-between gap-2">
      <div>
        <h1 class="flex items-center gap-2 font-title text-xl font-bold text-text">
          <ShieldCheck class="size-5 text-accent" />
          {{ locale.t('adminModerators.title', 'Модераторы') }}
        </h1>
        <p class="mt-1 text-sm text-text3">
          {{ locale.t('adminModerators.subtitle',
             'Разбирают жалобы и модерацию чатов. Журнал и оценки им закрыты.') }}
        </p>
      </div>
      <AppButton @click="openCreate">
        <UserPlus class="size-4" />
        {{ locale.t('adminModerators.add', 'Завести модератора') }}
      </AppButton>
    </div>

    <p v-if="loading" class="text-sm text-text3">
      {{ locale.t('common.loading', 'Загрузка…') }}
    </p>

    <!-- Отказ загрузки называется отказом, а не «никого нет». -->
    <p v-else-if="loadError"
       class="rounded-xl border border-red/40 bg-red/10 px-3 py-2 text-sm text-red">
      {{ loadError }}
    </p>

    <p v-else-if="!rows.length"
       class="rounded-xl border border-border2 bg-card p-6 text-center text-sm text-text3">
      {{ locale.t('adminModerators.empty', 'Модераторов пока нет.') }}
    </p>

    <div v-else class="overflow-hidden rounded-xl border border-border bg-card">
      <div v-for="r in rows" :key="r.login"
           class="flex flex-wrap items-center gap-3 border-b border-border/50 px-4 py-3 last:border-0">
        <span class="min-w-0 flex-1">
          <span class="block text-sm font-semibold text-text">{{ r.full_name || r.login }}</span>
          <span class="block text-xs text-text3">{{ r.login }}</span>
        </span>
        <span class="shrink-0 text-xs"
              :class="r.has_password ? 'text-text3' : 'text-red'">
          {{ r.has_password
            ? locale.t('adminModerators.passwordSet', 'пароль задан')
            : locale.t('adminModerators.passwordMissing', 'пароля нет — войти не сможет') }}
        </span>
        <AppButton variant="ghost" size="sm" @click="openPassword(r.login)">
          <KeyRound class="size-3.5" />
          {{ locale.t('adminModerators.changePassword', 'Сменить пароль') }}
        </AppButton>
        <AppButton variant="ghost" size="sm" @click="remove(r)">
          <Trash2 class="size-3.5" />
        </AppButton>
      </div>
    </div>

    <!-- Смена пароля отдельной строкой под списком: это одно поле, и ради него не нужна
         вторая модалка (тот же приём, что у правки названия предмета). -->
    <div v-if="pwFor" class="rounded-xl border border-accent/40 bg-card p-4">
      <p class="text-sm font-semibold text-text">
        {{ locale.t('adminModerators.newPasswordFor', { login: pwFor }) }}
      </p>
      <div class="mt-2 flex flex-wrap gap-2">
        <input v-model="pwValue" type="password" autocomplete="new-password"
               class="h-11 min-w-0 flex-1 rounded-lg border border-border2 bg-card2 px-3 text-base text-text outline-none focus:border-accent" />
        <AppButton :disabled="pwValue.length < MIN_PASSWORD" @click="savePassword">
          {{ locale.t('common.save', 'Сохранить') }}
        </AppButton>
        <AppButton variant="ghost" @click="pwFor = ''">
          {{ locale.t('common.cancel', 'Отмена') }}
        </AppButton>
      </div>
      <p v-if="pwError" class="mt-2 text-sm text-red">{{ pwError }}</p>
      <p class="mt-2 text-tiny text-text3">
        {{ locale.t('adminModerators.passwordHint', { n: MIN_PASSWORD }) }}
      </p>
    </div>

    <!-- Заведение -->
    <div v-if="showForm" class="rounded-xl border border-border bg-card p-4">
      <p class="text-sm font-semibold text-text">
        {{ locale.t('adminModerators.newTitle', 'Новый модератор') }}
      </p>
      <div class="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-3">
        <input v-model="fLogin" type="text" autocomplete="off"
               :placeholder="locale.t('adminModerators.loginPlaceholder', 'Логин')"
               class="h-11 rounded-lg border border-border2 bg-card2 px-3 text-base text-text outline-none focus:border-accent" />
        <input v-model="fName" type="text" autocomplete="off"
               :placeholder="locale.t('adminModerators.namePlaceholder', 'Имя (видно в чатах)')"
               class="h-11 rounded-lg border border-border2 bg-card2 px-3 text-base text-text outline-none focus:border-accent" />
        <input v-model="fPassword" type="password" autocomplete="new-password"
               :placeholder="locale.t('adminModerators.passwordPlaceholder', 'Пароль')"
               class="h-11 rounded-lg border border-border2 bg-card2 px-3 text-base text-text outline-none focus:border-accent" />
      </div>
      <p v-if="formError" class="mt-2 text-sm text-red">{{ formError }}</p>
      <p class="mt-2 text-tiny text-text3">
        {{ locale.t('adminModerators.passwordHint', { n: MIN_PASSWORD }) }}
      </p>
      <div class="mt-3 flex gap-2">
        <AppButton :disabled="!canSave || saving" @click="save">
          {{ locale.t('common.save', 'Сохранить') }}
        </AppButton>
        <AppButton variant="ghost" @click="showForm = false">
          {{ locale.t('common.cancel', 'Отмена') }}
        </AppButton>
      </div>
    </div>
  </div>
</template>
