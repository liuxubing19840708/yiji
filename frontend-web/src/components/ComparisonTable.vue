<script setup>
import { computed } from 'vue'

const props = defineProps({
  comparison: { type: Object, required: true },
  report: { type: Object, required: true },
  plan: { type: Object, default: null },
})

const modeLabels = { previous: '上一期', year_ago: '去年同期' }
const numberFormatter = new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 3 })
const percentFormatter = new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 2 })
const metricKeys = computed(() => {
  const planned = props.plan?.metrics?.map((item) => item.key)
  return planned?.length ? planned : Object.keys(props.comparison.rows?.[0]?.metrics || {})
})
const dimensionKeys = computed(() => props.plan?.dimensions || [])
const dimensions = computed(() => dimensionKeys.value.map((key) => (
  props.report.columns?.find((column) => column.key === key) || { key, label: key, type: 'text' }
)))
const metrics = computed(() => metricKeys.value.map((key) => (
  props.report.columns?.find((column) => column.key === key) || { key, label: key, type: 'decimal' }
)))
const columnCount = computed(() => Math.max(1, dimensions.value.length) + metrics.value.length * 4)

function finite(value) {
  if (value === null || value === undefined || value === '' || typeof value === 'boolean') return null
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : null
}
function formatNumber(value, column) {
  const parsed = finite(value)
  if (parsed === null) return '—'
  if (column.type === 'money') return `¥${parsed.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
  if (column.type === 'integer') return Math.round(parsed).toLocaleString('zh-CN')
  return numberFormatter.format(parsed)
}
function formatRate(value) {
  const parsed = finite(value)
  return parsed === null ? '—' : `${percentFormatter.format(parsed * 100)}%`
}
function formatDimension(value, column) {
  if (value === null || value === undefined || value === '') return '—'
  const choice = column.choices?.find((item) => item.value === value)?.label
  return choice === undefined ? String(value) : choice
}
</script>

<template>
  <section class="comparison-block" :aria-label="`期间对比：${modeLabels[comparison.mode] || comparison.mode || '对比'}`">
    <h4>期间对比 · {{ modeLabels[comparison.mode] || comparison.mode || '对比' }}</h4>
    <dl class="comparison-ranges">
      <div><dt>本期范围</dt><dd>{{ comparison.current_range?.join(' 至 ') || '—' }}</dd></div>
      <div><dt>对比期范围</dt><dd>{{ comparison.previous_range?.join(' 至 ') || '—' }}</dd></div>
    </dl>
    <p v-if="comparison.message" class="comparison-message">{{ comparison.message }}</p>
    <div v-if="comparison.available" class="comparison-table-wrap" tabindex="0" role="region" aria-label="期间对比表，可横向滚动">
      <table class="comparison-table">
        <caption>各分组指标的本期、上期、差额与变化率；空值显示为—</caption>
        <thead>
          <tr>
            <th v-for="column in dimensions" :key="column.key" rowspan="2" scope="col">{{ column.label }}</th>
            <th v-if="!dimensions.length" rowspan="2" scope="col">分组</th>
            <th v-for="column in metrics" :key="column.key" colspan="4" scope="colgroup">{{ column.label }}</th>
          </tr>
          <tr>
            <template v-for="column in metrics" :key="column.key">
              <th scope="col">本期</th><th scope="col">上期</th><th scope="col">差额</th><th scope="col">变化率</th>
            </template>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(row, rowIndex) in comparison.rows" :key="rowIndex">
            <th v-if="!dimensions.length" scope="row">全公司汇总</th>
            <th v-for="(column, index) in dimensions" :key="column.key" scope="row">{{ formatDimension(row.dimensions?.[index], column) }}</th>
            <template v-for="column in metrics" :key="column.key">
              <td class="numeric">{{ formatNumber(row.metrics?.[column.key]?.current, column) }}</td>
              <td class="numeric">{{ formatNumber(row.metrics?.[column.key]?.previous, column) }}</td>
              <td class="numeric">{{ formatNumber(row.metrics?.[column.key]?.delta, column) }}</td>
              <td class="numeric">{{ formatRate(row.metrics?.[column.key]?.change_rate) }}</td>
            </template>
          </tr>
          <tr v-if="!comparison.rows?.length"><td :colspan="columnCount" class="comparison-empty">两期均无可对比数据</td></tr>
        </tbody>
      </table>
    </div>
  </section>
</template>

<style scoped>
.comparison-block{min-width:0;margin:14px 0}.comparison-block h4{margin:0 0 10px;font-size:14px}.comparison-ranges{display:flex;flex-wrap:wrap;gap:8px 24px;margin:0 0 8px}.comparison-ranges>div{display:flex;gap:7px}.comparison-ranges dt{color:var(--muted);font-size:12px}.comparison-ranges dd{margin:0;font-size:12px;font-variant-numeric:tabular-nums}.comparison-message{color:var(--muted);font-size:12px;line-height:1.6}.comparison-table-wrap{max-width:100%;overflow:auto;border:1px solid var(--line);border-radius:8px}.comparison-table-wrap:focus-visible{outline:2px solid var(--orange);outline-offset:2px}.comparison-table{width:100%;min-width:680px;border-collapse:collapse;font-size:12px}.comparison-table caption{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}.comparison-table th,.comparison-table td{padding:9px 10px;border-bottom:1px solid var(--line);text-align:left;white-space:nowrap}.comparison-table thead th{background:var(--surface-muted,#fafafa)}.comparison-table tbody th{font-weight:500}.comparison-table .numeric{text-align:right;font-variant-numeric:tabular-nums}.comparison-empty{text-align:center!important;color:var(--muted)}
@media(max-width:640px){.comparison-ranges{display:grid;gap:5px}.comparison-ranges>div{display:grid;grid-template-columns:64px minmax(0,1fr)}.comparison-table{font-size:11px}}
</style>
