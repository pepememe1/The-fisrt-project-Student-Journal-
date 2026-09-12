<script setup>
// DataTable — простая таблица под дизайн-систему (тихая шапка, разделители-строки).
// columns: [{ key, label, align?: 'right' }]; rows: массив объектов. Ячейку можно
// переопределить слотом cell-<key> (передаётся { row, value }).
import { useLocaleStore } from '@/stores/locale'
import StickyXScroll from '@/components/ui/StickyXScroll.vue'
const locale = useLocaleStore()
defineProps({
  columns: { type: Array, required: true },
  rows: { type: Array, default: () => [] },
  loading: { type: Boolean, default: false },
  empty: { type: String, default: '' },
})
</script>

<template>
  <StickyXScroll class="rounded-lg border border-border bg-card shadow-card">
    <table class="w-full text-sm">
      <thead>
        <tr class="border-b border-border2 bg-bg2 text-left text-tiny uppercase tracking-wide text-text2">
          <th v-for="c in columns" :key="c.key" class="px-4 py-2.5 font-semibold"
              :class="c.align === 'right' ? 'text-right' : ''">{{ c.label }}</th>
        </tr>
      </thead>
      <tbody>
        <tr v-if="loading"><td :colspan="columns.length" class="px-4 py-6 text-center text-text3">{{ locale.t('common.loading') }}</td></tr>
        <tr v-else-if="!rows.length"><td :colspan="columns.length" class="px-4 py-8 text-center text-text3">{{ empty || locale.t('common.empty') }}</td></tr>
        <tr v-for="(row, i) in rows" :key="i" class="border-b border-border last:border-0 hover:bg-bg2/60">
          <td v-for="c in columns" :key="c.key" class="px-4 py-3 text-text3"
              :class="c.align === 'right' ? 'text-right' : ''">
            <slot :name="`cell-${c.key}`" :row="row" :value="row[c.key]">{{ row[c.key] ?? '—' }}</slot>
          </td>
        </tr>
      </tbody>
    </table>
  </StickyXScroll>
</template>
