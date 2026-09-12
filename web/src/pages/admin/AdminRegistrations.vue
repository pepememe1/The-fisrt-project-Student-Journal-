<script setup>
// AdminRegistrations — заявки студентов на самостоятельную регистрацию. Админ одобряет
// (система создаёт студента, генерирует пароль, логин=email, шлёт на почту) или отклоняет.
// Если письмо не ушло (SMTP не настроен) — сервер вернёт пароль, показываем его админу
// для ручной передачи.
import { ref, onMounted } from 'vue'
import StickyXScroll from '@/components/ui/StickyXScroll.vue'
import { adminApi } from '@/api/endpoints'
import AppButton from '@/components/ui/AppButton.vue'
import EmptyState from '@/components/ui/EmptyState.vue'
import { useToast } from '@/composables/useToast'
import { useConfirm } from '@/composables/useConfirm'
import { useLocaleStore } from '@/stores/locale'

const locale = useLocaleStore()
const toast = useToast()
const { confirm } = useConfirm()
const rows = ref([])
const loading = ref(true)
const busy = ref('')            // id заявки, по которой идёт действие
const issued = ref(null)       // {email, password} — если письмо не ушло

async function reload() {
  loading.value = true
  try { rows.value = (await adminApi.registrations()).data.requests || [] } catch { rows.value = [] } finally { loading.value = false }
}
onMounted(reload)

async function approve(r) {
  busy.value = r.id
  try {
    const res = (await adminApi.approveRegistration(r.id)).data
    if (res.sent) {
      toast.success(locale.t('adminRegistrations.approvedSent', { login: res.login }))
    } else {
      // Письмо не ушло (SMTP не настроен) — показываем креды админу.
      issued.value = { email: res.login, password: res.password }
    }
    await reload()
  } catch (e) { toast.error(e?.response?.data?.detail || locale.t('adminRegistrations.approveFailed', 'Не удалось одобрить')) }
  finally { busy.value = '' }
}
async function reject(r) {
  if (!(await confirm({ title: locale.t('adminRegistrations.confirmReject', { name: r.full_name }), message: r.email, okText: locale.t('adminRegistrations.reject', 'Отклонить'), danger: true }))) return
  busy.value = r.id
  try { await adminApi.rejectRegistration(r.id); await reload() }
  catch (e) { toast.error(e?.response?.data?.detail || locale.t('adminRegistrations.rejectFailed', 'Не удалось отклонить')) }
  finally { busy.value = '' }
}
</script>

<template>
  <div class="space-y-4">
    <div class="flex items-center gap-3">
      <p class="text-sm text-text3">{{ locale.t('adminRegistrations.hint', 'Новые студенты, ожидающие подтверждения регистрации.') }}</p>
      <AppButton variant="ghost" size="sm" class="ml-auto" @click="reload">{{ locale.t('common.refresh') }}</AppButton>
    </div>

    <p v-if="loading" class="text-sm text-text3">{{ locale.t('common.loading') }}</p>
    <EmptyState v-else-if="!rows.length" :title="locale.t('adminRegistrations.noRequestsTitle', 'Заявок нет')" :message="locale.t('adminRegistrations.noRequestsMessage', 'Новые регистрации появятся здесь.')" />

    <StickyXScroll v-else class="rounded-lg border border-border bg-card shadow-card">
      <table class="w-full text-sm">
        <thead>
          <tr class="border-b border-border2 bg-bg2 text-left text-tiny uppercase tracking-wide text-text2">
            <th class="px-4 py-2.5 font-semibold">{{ locale.t('adminRegistrations.fullName', 'ФИО') }}</th>
            <th class="px-4 py-2.5 font-semibold">{{ locale.t('adminRegistrations.group', 'Группа') }}</th>
            <th class="px-4 py-2.5 font-semibold">{{ locale.t('adminRegistrations.phone', 'Телефон') }}</th>
            <th class="px-4 py-2.5 font-semibold">{{ locale.t('adminRegistrations.emailLogin', 'Почта (логин)') }}</th>
            <th class="px-4 py-2.5 text-right font-semibold">{{ locale.t('adminRegistrations.decision', 'Решение') }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in rows" :key="r.id" class="border-b border-border last:border-0 hover:bg-bg2/60">
            <td class="px-4 py-2.5 font-medium text-text">{{ r.full_name }}</td>
            <td class="px-4 py-2.5 text-text2">{{ r.group }}</td>
            <td class="whitespace-nowrap px-4 py-2.5 text-text2">{{ r.phone }}</td>
            <td class="px-4 py-2.5 text-text2">{{ r.email }}</td>
            <td class="whitespace-nowrap px-4 py-2.5 text-right">
              <AppButton variant="green" size="sm" :disabled="busy === r.id" @click="approve(r)">
                {{ busy === r.id ? '…' : locale.t('adminRegistrations.approve', 'Одобрить') }}
              </AppButton>
              <button class="ml-2 text-text3 hover:text-red" :title="locale.t('adminRegistrations.reject', 'Отклонить')" :disabled="busy === r.id" @click="reject(r)">✕</button>
            </td>
          </tr>
        </tbody>
      </table>
    </StickyXScroll>

    <!-- Если письмо не ушло — показываем креды админу для ручной передачи -->
    <div v-if="issued" class="fixed inset-0 z-50 flex items-center justify-center bg-black/45 p-4" @click.self="issued = null">
      <div class="w-full max-w-sm rounded-2xl border border-border bg-card p-6 shadow-card">
        <h3 class="mb-2 font-title text-lg font-bold text-text">{{ locale.t('adminRegistrations.accountCreated', 'Аккаунт создан') }}</h3>
        <p class="text-xs text-text3">{{ locale.t('adminRegistrations.emailNotSent', 'Письмо не отправлено (почта на сервере не настроена). Передайте студенту эти данные лично:') }}</p>
        <div class="mt-3 space-y-1 rounded-md border border-border2 bg-card2 p-3 text-sm">
          <p>{{ locale.t('adminRegistrations.loginLabel', 'Логин:') }} <b class="text-text">{{ issued.email }}</b></p>
          <p>{{ locale.t('adminRegistrations.passwordLabel', 'Пароль:') }} <b class="text-accent">{{ issued.password }}</b></p>
        </div>
        <AppButton variant="green" size="sm" class="mt-4" @click="issued = null">{{ locale.t('adminRegistrations.done', 'Готово') }}</AppButton>
      </div>
    </div>
  </div>
</template>
