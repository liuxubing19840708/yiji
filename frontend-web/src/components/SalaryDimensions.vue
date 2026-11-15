<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api'
import PageState from './PageState.vue'

const props = defineProps({
  dateFrom: { type: String, default: '' },
  dateTo: { type: String, default: '' },
})
const emit = defineEmits(['navigate', 'toast'])
const dimensions = [
  { key: 'order', label: '订单' },
  { key: 'bed', label: '裁床' },
  { key: 'post', label: '部门' },
  { key: 'group', label: '组' },
  { key: 'user', label: '人' },
]
const dimension = ref('order')
const columns = ref([])
const tableRows = ref([])
const loading = ref(false)
const error = ref('')
const exporting = ref(false)
let requestSerial = 0

function cellWidth(column) {
  const width = Number(column.width)
  return `${Number.isFinite(width) && width > 0 ? Math.max(70, width / 2) : 100}px`
}

async function load() {
  if (!props.dateFrom || !props.dateTo) {
    emit('toast', '请选择完整的开始和结束日期')
    return
  }
  const serial = ++requestSerial
  loading.value = true
  error.value = ''
  columns.value = []
  tableRows.value = []
  try {
    const result = await api('/dashboard/dim', {
      params: {
        dim: dimension.value,
        range: 'custom',
        start_date: props.dateFrom,
        end_date: props.dateTo,
      },
    })
    if (serial !== requestSerial) return
    columns.value = Array.isArray(result.columns) ? result.columns : []
    tableRows.value = Array.isArray(result.rows) ? result.rows : []
  } catch (e) {
    if (serial === requestSerial) error.value = e.message || '加载失败，请重试'
  } finally {
    if (serial === requestSerial) loading.value = false
  }
}

function chooseDimension(value) {
  if (value === dimension.value || loading.value) return
  dimension.value = value
  load()
}

function openRow(row) {
  if (row.nav !== 'order' && row.nav !== 'bed') return
  emit('navigate', row.nav === 'order' ? 'orders' : 'production', String(row.key))
}

function csvCell(value) {
  const text = String(value ?? '')
  return /[",\r\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text
}

async function exportCsv() {
  if (!tableRows.value.length) {
    emit('toast', '没有可导出的数据')
    return
  }
  if (exporting.value) return
  exporting.value = true
  try {
    const lines = [columns.value.map((column) => csvCell(column.title)).join(',')]
    for (const row of tableRows.value) {
      lines.push(columns.value.map((_, index) => csvCell(row.cells?.[index])).join(','))
    }
    const blob = new Blob([`﻿${lines.join('\r\n')}`], { type: 'text/csv;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    const label = dimensions.find((item) => item.key === dimension.value)?.label || dimension.value
    link.href = url
    link.download = `完成汇总_${label}_${props.dateFrom}_${props.dateTo}.csv`
    document.body.appendChild(link)
    link.click()
    link.remove()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
    emit('toast', '当前维度 CSV 已导出')
  } catch (e) {
    emit('toast', e.message || '导出失败')
  } finally {
    exporting.value = false
  }
}

defineExpose({ load })
onMounted(load)
</script>

<template>
  <section class="card salary-dimensions" aria-label="完成汇总维度表">
    <div class="salary-dimension-toolbar">
      <div class="segmented salary-dimension-tabs" aria-label="完成汇总统计维度">
        <button
          v-for="item in dimensions"
          :key="item.key"
          type="button"
          :class="{ active: dimension === item.key }"
          :aria-pressed="dimension === item.key"
          :disabled="loading"
          @click="chooseDimension(item.key)"
        >{{ item.label }}</button>
      </div>
      <button class="button secondary small" type="button" :disabled="loading || exporting || !tableRows.length" @click="exportCsv">
        {{ exporting ? '导出中…' : '导出当前维度 CSV' }}
      </button>
    </div>

    <PageState
      :loading="loading"
      :error="error"
      :empty="!tableRows.length"
      empty-text="该期间暂无登记数据"
      @retry="load"
    >
      <div class="table-wrap salary-dimension-scroll">
        <table>
          <thead><tr><th v-for="column in columns" :key="column.key" :style="{ minWidth: cellWidth(column) }">{{ column.title }}</th></tr></thead>
          <tbody>
            <tr
              v-for="row in tableRows"
              :key="row.key"
              :class="{ clickable: row.nav === 'order' || row.nav === 'bed' }"
              :tabindex="row.nav === 'order' || row.nav === 'bed' ? 0 : undefined"
              :aria-label="row.nav === 'order' ? `查看订单 ${row.cells?.[0] || row.key}` : row.nav === 'bed' ? `查看床次 ${row.cells?.[0] || row.key}` : undefined"
              @click="openRow(row)"
              @keydown.enter.prevent="openRow(row)"
              @keydown.space.prevent="openRow(row)"
            >
              <td v-for="(column, index) in columns" :key="column.key">{{ row.cells?.[index] ?? '—' }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <p v-if="dimension === 'order' || dimension === 'bed'" class="section-note">点行可下钻查看详情</p>
    </PageState>
  </section>
</template>

<style scoped>
.salary-dimensions { padding: 18px; }
.salary-dimension-toolbar { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 16px; }
.salary-dimension-tabs { min-width: 0; }
.salary-dimension-tabs button:disabled { cursor: wait; opacity: .65; }
.salary-dimension-scroll { max-width: 100%; overflow-x: auto; }
.salary-dimension-scroll table { min-width: 100%; }
.salary-dimension-scroll tbody tr[tabindex]:focus-visible { outline: 2px solid var(--orange); outline-offset: -2px; }
.salary-dimensions .section-note { margin: 12px 0 0; }
@media (max-width: 760px) {
  .salary-dimensions { padding: 13px; }
  .salary-dimension-toolbar { align-items: stretch; flex-direction: column; }
  .salary-dimension-tabs { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); width: 100%; padding: 4px; }
  .salary-dimension-tabs button { min-width: 0; min-height: 34px; padding: 6px 2px; font-size: 12px; }
  .salary-dimension-toolbar > .button { align-self: flex-end; }
  .salary-dimension-scroll { margin-inline: -13px; padding-inline: 13px; }
}
</style>
