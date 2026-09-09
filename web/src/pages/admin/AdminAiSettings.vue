<script setup>
// AdminAiSettings — «Настройки ИИ» (порт ui/admin_dashboard._build_api). Админ выбирает
// провайдера «Вектора» (Оффлайн/GigaChat/Ollama) и вводит ключ GigaChat. Хранится в той
// же строке config, что и на десктопе → синхронизируется в обе стороны (ключ, заданный
// на ПК, приезжает на веб и наоборот).
import { ref, computed, onMounted } from 'vue'
import { Bot, Eye, EyeOff, Check, CircleAlert, CircleCheck } from '@lucide/vue'
import { adminApi } from '@/api/endpoints'
import Card from '@/components/ui/Card.vue'
import AppButton from '@/components/ui/AppButton.vue'
import { useLocaleStore } from '@/stores/locale'

const locale = useLocaleStore()

const cfg = ref({
  vector_llm: 'offline',
  gigachat_credentials: '',
  gigachat_scope: 'GIGACHAT_API_B2B',
  gigachat_model: 'GigaChat',
  local_model: 'qwen2.5:3b',
  tts_enabled: true,
  stt_mode: 'auto',
  stt_installed: false,     //только чтение: что реально стоит на хосте
})
const showKey = ref(false)
const loading = ref(false)
const saved = ref(false)
const testMsg = ref('')
const testOk = ref(null)

// computed, а не const-литерал: t() читается при ОПРЕДЕЛЕНИИ массива, и застывший
// на языке первого рендера массив не переключался бы вместе с интерфейсом.
const PROVIDERS = computed(() => [
  { id: 'offline', name: locale.t('adminAiSettings.provider.offline.name', 'Оффлайн-шаблоны'), hint: locale.t('adminAiSettings.provider.offline.hint', 'Без внешней LLM — ответы строго по фактам. Работает всегда.') },
  { id: 'gigachat', name: locale.t('adminAiSettings.provider.gigachat.name', 'GigaChat (Сбер, РФ)'), hint: locale.t('adminAiSettings.provider.gigachat.hint', 'Тёплая переформулировка ответов. Нужен ключ авторизации. Сервер в РФ — GigaChat работает.') },
  { id: 'local', name: locale.t('adminAiSettings.provider.local.name', 'Ollama (локально)'), hint: locale.t('adminAiSettings.provider.local.hint', 'Своя модель на сервере, если поднят Ollama. Данные никуда не уходят.') },
])
const SCOPES = ['GIGACHAT_API_PERS', 'GIGACHAT_API_B2B', 'GIGACHAT_API_CORP']

// Голосовой ввод: чем распознавать речь. Три режима — см. `_STT_MODES` на сервере.
const STT_MODES = computed(() => [
  { id: 'auto', name: locale.t('adminAiSettings.stt.auto.name', 'Автоматически (рекомендуется)'),
    hint: locale.t('adminAiSettings.stt.auto.hint', 'Whisper — там, где он установлен; на остальных машинах распознаёт браузер. Никого не заставляет ничего скачивать.') },
  { id: 'server', name: locale.t('adminAiSettings.stt.server.name', 'OpenAI Whisper на сервере — для всех'),
    hint: locale.t('adminAiSettings.stt.server.hint', 'Звук распознаёт только наш сервер, наружу не уходит. Требует машину с видеокартой: на нынешнем хостинге движок не поместится.') },
  { id: 'browser', name: locale.t('adminAiSettings.stt.browser.name', 'Обычный голосовой ввод (браузер)'),
    hint: locale.t('adminAiSettings.stt.browser.hint', 'Распознаёт браузер (Chrome/Edge) силами Google. Нагрузки на сервер нет, но звук уходит третьей стороне.') },
])
const inputCls =
  'h-11 w-full rounded-sm border border-border2 bg-card2 px-3.5 text-text outline-none transition-colors focus:border-accent focus:bg-card'

// 🔒 Ключ GigaChat WRITE-ONLY (06.09.2026, P1 плана Ярослава: «секрет должен быть
// write-only: настроен / не настроен, но не доступен для повторного чтения»). Сервер
// больше не отдаёт значение, поэтому поле всегда стартует пустым, а факт настройки живёт
// отдельным признаком.
const keyConfigured = ref(false)

onMounted(async () => {
  try {
    const { data } = await adminApi.aiConfig()
    cfg.value = { ...cfg.value, ...data }
    keyConfigured.value = !!data.gigachat_configured
    //Значения ключа в ответе нет — гасим поле явно, чтобы в нём не осталось прежнего
    //черновика после повторного открытия страницы.
    cfg.value.gigachat_credentials = ''
  } catch { /* нет связи — дефолты */ }
})

async function save() {
  loading.value = true; saved.value = false
  // ⚠️ ПУСТОЕ ПОЛЕ ОЗНАЧАЕТ «НЕ МЕНЯТЬ», а не «стереть». Сервер ключ не отдаёт, поле
  // стартует пустым — и без этого правила простое нажатие «Сохранить» уносило бы рабочий
  // ключ, ничего не трогая. Тот же приём и та же причина, что у пароля сервера в
  // разделе «Сервер» (§16): страница пароль не показывает, значит пустое поле — не выбор.
  const payload = { ...cfg.value }
  if (!String(payload.gigachat_credentials || '').trim()) delete payload.gigachat_credentials
  try {
    await adminApi.aiConfigSave(payload)
    if (payload.gigachat_credentials) keyConfigured.value = true
    cfg.value.gigachat_credentials = ''
    saved.value = true; setTimeout(() => (saved.value = false), 2500)
  }
  finally { loading.value = false }
}

async function test() {
  loading.value = true; testMsg.value = ''; testOk.value = null
  try {
    const { data } = await adminApi.aiConfigTest(cfg.value)
    testOk.value = data.ok
    testMsg.value = data.message
  } catch (e) {
    testOk.value = false
    testMsg.value = e.response?.data?.detail || locale.t('adminAiSettings.testFailed', 'Не удалось проверить.')
  } finally { loading.value = false }
}
</script>

<template>
  <div class="max-w-2xl space-y-6">
    <Card :title="locale.t('adminAiSettings.vectorTitle', 'ИИ-помощник «Вектор»')" :subtitle="locale.t('adminAiSettings.vectorSubtitle', 'Как Вектор озвучивает ответы. Цифры он всегда берёт из реальных данных — LLM лишь переформулирует.')">
      <div class="space-y-2.5">
        <label v-for="p in PROVIDERS" :key="p.id"
               class="flex cursor-pointer items-start gap-3 rounded-lg border p-3 transition-colors"
               :class="cfg.vector_llm === p.id ? 'border-accent bg-accent-glow' : 'border-border hover:border-accent'">
          <input type="radio" :value="p.id" v-model="cfg.vector_llm" class="mt-1 accent-[var(--gb-accent)]" />
          <span>
            <span class="flex items-center gap-2 font-semibold text-text"><Bot class="size-4 text-accent" />{{ p.name }}</span>
            <span class="mt-0.5 block text-xs text-text3">{{ p.hint }}</span>
          </span>
        </label>
      </div>
    </Card>

    <!-- Озвучка (TTS): глобальный рубильник. Выключение прячет озвучку у всех
         пользователей. Персональный выбор (вкл/голос) каждый делает в своём профиле. -->
    <Card :title="locale.t('adminAiSettings.ttsTitle', 'Озвучка ответов (голос Вектора)')"
          :subtitle="locale.t('adminAiSettings.ttsSubtitle', 'Синтез речи на сервере. Выключите, если сервер перегружен — пользователи не увидят озвучку.')">
      <label class="flex cursor-pointer items-center justify-between gap-3">
        <span class="text-sm text-text">{{ locale.t('adminAiSettings.ttsAllow', 'Разрешить озвучку на сервере') }}</span>
        <input type="checkbox" v-model="cfg.tts_enabled" class="size-5 accent-[var(--gb-accent)]" />
      </label>
    </Card>

    <!-- Голосовой ввод (STT): чем распознавать речь у всех пользователей. -->
    <Card :title="locale.t('adminAiSettings.sttTitle', 'Голосовой ввод (распознавание речи)')"
          :subtitle="locale.t('adminAiSettings.sttSubtitle', 'Чем превращать речь в текст. Настройка общая: действует и на сайте, и в программе, и в телефоне.')">
      <div class="space-y-2.5">
        <label v-for="m in STT_MODES" :key="m.id"
               class="flex cursor-pointer items-start gap-3 rounded-md border p-3 transition-colors"
               :class="cfg.stt_mode === m.id ? 'border-accent bg-accent-glow' : 'border-border hover:border-accent'">
          <input type="radio" :value="m.id" v-model="cfg.stt_mode" class="mt-1 accent-[var(--gb-accent)]" />
          <span>
            <span class="block text-sm font-semibold text-text">{{ m.name }}</span>
            <span class="mt-0.5 block text-xs text-text3">{{ m.hint }}</span>
          </span>
        </label>
      </div>

      <!-- Честное состояние хоста. Без этой строки админ включил бы «Whisper для всех»
           на машине, где движка нет, и голосовой ввод пропал бы у всего колледжа. -->
      <div class="mt-3 rounded-md border px-3 py-2.5 text-xs"
           :class="cfg.stt_installed ? 'border-accent/40 text-text2' : 'border-orange/40 text-text2'">
        <template v-if="cfg.stt_installed">
          {{ locale.t('adminAiSettings.sttInstalledYes', '✅ Whisper установлен на этом сервере — режим «для всех» будет работать.') }}
        </template>
        <template v-else>
          {{ locale.t('adminAiSettings.sttInstalledNo', '⚠️ Whisper на этом сервере не установлен. Режим «для всех» станет доступен, когда журнал переедет на машину колледжа с видеокартой — до тех пор он честно покажет пользователям, что распознавание недоступно, а не подменит его тихо браузерным.') }}
        </template>
      </div>
    </Card>

    <!-- GigaChat: ключ + скоуп -->
    <Card v-if="cfg.vector_llm === 'gigachat'" :title="locale.t('adminAiSettings.gigachatTitle', 'GigaChat')" :subtitle="locale.t('adminAiSettings.gigachatSubtitle', 'Ключ авторизации и scope из личного кабинета GigaChat (Сбер).')">
      <div class="space-y-4">
        <div>
          <label class="mb-1.5 block text-xs font-medium text-text3">{{ locale.t('adminAiSettings.credentialsLabel', 'КЛЮЧ АВТОРИЗАЦИИ (Credentials)') }}</label>
          <div class="relative">
            <!-- Поле стартует ПУСТЫМ даже когда ключ настроен: сервер его не отдаёт.
                 Подпись-плейсхолдер честно говорит, есть ключ или нет, — иначе пустое
                 поле читалось бы как «ключ стёрли», и админ вписал бы новый поверх
                 работающего. -->
            <input v-model="cfg.gigachat_credentials" :type="showKey ? 'text' : 'password'"
                   :class="inputCls + ' pr-11'" autocomplete="off"
                   :placeholder="keyConfigured
                     ? locale.t('adminAiSettings.credentialsSet', 'ключ настроен — впишите новый, чтобы заменить')
                     : locale.t('adminAiSettings.credentialsPlaceholder', 'вставьте ключ авторизации GigaChat')" />
            <button type="button" class="absolute right-2 top-1/2 grid size-8 -translate-y-1/2 place-items-center rounded-sm text-text2 hover:text-accent"
                    @click="showKey = !showKey"><EyeOff v-if="showKey" class="size-4" /><Eye v-else class="size-4" /></button>
          </div>
        </div>
        <div>
          <label class="mb-1.5 block text-xs font-medium text-text3">{{ locale.t('adminAiSettings.scopeLabel', 'SCOPE') }}</label>
          <select v-model="cfg.gigachat_scope" :class="inputCls">
            <option v-for="s in SCOPES" :key="s" :value="s">{{ s }}</option>
          </select>
        </div>
      </div>
    </Card>

    <!-- Ollama: модель -->
    <Card v-if="cfg.vector_llm === 'local'" :title="locale.t('adminAiSettings.ollamaTitle', 'Ollama')" :subtitle="locale.t('adminAiSettings.ollamaSubtitle', 'Имя локальной модели (должен быть запущен Ollama на сервере).')">
      <label class="mb-1.5 block text-xs font-medium text-text3">{{ locale.t('adminAiSettings.modelLabel', 'МОДЕЛЬ') }}</label>
      <input v-model="cfg.local_model" :class="inputCls" :placeholder="locale.t('adminAiSettings.modelPlaceholder', 'напр. qwen2.5:3b')" />
    </Card>

    <div class="flex flex-wrap items-center gap-3">
      <AppButton variant="green" :disabled="loading" @click="save">
        <Check class="mr-2 inline size-4" />{{ loading ? locale.t('adminAiSettings.saving', 'Сохраняю…') : locale.t('common.save') }}
      </AppButton>
      <AppButton v-if="cfg.vector_llm !== 'offline'" variant="ghost" :disabled="loading" @click="test">
        {{ locale.t('adminAiSettings.testConnection', 'Проверить связь') }}
      </AppButton>
      <span v-if="saved" class="text-sm font-medium text-green">{{ locale.t('adminAiSettings.savedSynced', 'Сохранено — синхронизируется на все ПК.') }}</span>
    </div>

    <p v-if="testMsg" class="flex items-start gap-2 rounded-lg border px-3 py-2.5 text-sm"
       :class="testOk ? 'border-green/40 bg-green/10 text-text' : 'border-red/40 bg-red/10 text-red'">
      <component :is="testOk ? CircleCheck : CircleAlert" class="mt-0.5 size-4 shrink-0" :class="testOk ? 'text-green' : 'text-red'" />
      <span>{{ testOk ? locale.t('adminAiSettings.connectionOk', 'Связь есть. Пример ответа: ') : '' }}{{ testMsg }}</span>
    </p>
  </div>
</template>
