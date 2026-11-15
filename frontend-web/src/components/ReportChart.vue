<script setup>
import { computed, ref } from 'vue'

const props = defineProps({
  report: { type: Object, required: true },
  type: { type: String, default: 'bar' },
})

const activeIndex = ref(-1)
const dimensionCount = computed(() => Math.max(0, props.report.columns.length - 1))
const metricColumn = computed(() => props.report.columns.at(-1) || { label: '指标' })
const rows = computed(() => props.report.rows.map((values, index) => {
  const label = values.slice(0, dimensionCount.value).map((value) => value ?? '（空）').join(' / ')
  const value = Number(values.at(-1))
  return { index, label, value: Number.isFinite(value) ? value : 0 }
}))
const activeRow = computed(() => rows.value[activeIndex.value])
const values = computed(() => rows.value.map((row) => row.value))
const minimum = computed(() => Math.min(0, ...values.value))
const maximum = computed(() => Math.max(0, ...values.value))
const span = computed(() => maximum.value - minimum.value || 1)
const zeroPercent = computed(() => (0 - minimum.value) / span.value * 100)

const numberFormatter = new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 3 })
function format(value) {
  return numberFormatter.format(Number(value) || 0)
}
function axisTicks() {
  return [minimum.value, (minimum.value + maximum.value) / 2, maximum.value]
}
function barStyle(row) {
  const start = Math.min(0, row.value)
  const end = Math.max(0, row.value)
  return {
    left: `${(start - minimum.value) / span.value * 100}%`,
    width: `${Math.max((end - start) / span.value * 100, row.value === 0 ? 0 : 0.35)}%`,
  }
}

const plot = { left: 64, right: 874, top: 24, bottom: 258 }
const points = computed(() => rows.value.map((row, index) => ({
  ...row,
  x: rows.value.length === 1
    ? (plot.left + plot.right) / 2
    : plot.left + index / (rows.value.length - 1) * (plot.right - plot.left),
  y: plot.bottom - (row.value - minimum.value) / span.value * (plot.bottom - plot.top),
})))
const polyline = computed(() => points.value.map((point) => `${point.x},${point.y}`).join(' '))
const lineTicks = computed(() => Array.from({ length: 5 }, (_, index) => {
  const ratio = index / 4
  return {
    y: plot.top + ratio * (plot.bottom - plot.top),
    value: maximum.value - ratio * span.value,
  }
}))
const xLabels = computed(() => {
  if (points.value.length <= 6) return points.value
  return points.value.filter((_, index) => (
    index === 0 || index === points.value.length - 1
    || index % Math.ceil((points.value.length - 1) / 5) === 0
  ))
})
function select(index) {
  activeIndex.value = index
}
</script>

<template>
  <section class="report-chart" :aria-label="`${metricColumn.label}图表`">
    <div v-if="rows.length === 1" class="report-stat">
      <span>{{ rows[0].label }}</span>
      <strong>{{ format(rows[0].value) }}</strong>
      <small>{{ metricColumn.label }}</small>
    </div>

    <template v-else-if="type === 'line'">
      <div class="chart-tooltip" v-if="activeRow" role="status">
        <span>{{ activeRow.label }}</span>
        <strong>{{ format(activeRow.value) }}</strong>
        <small>{{ metricColumn.label }}</small>
      </div>
      <svg class="line-chart" viewBox="0 0 900 304" role="img"
        :aria-label="`${metricColumn.label}按${report.columns[0]?.label || '时间'}变化趋势`">
        <g class="chart-grid">
          <g v-for="tick in lineTicks" :key="tick.y">
            <line :x1="plot.left" :x2="plot.right" :y1="tick.y" :y2="tick.y" />
            <text :x="plot.left - 10" :y="tick.y + 4" text-anchor="end">{{ format(tick.value) }}</text>
          </g>
        </g>
        <line class="chart-baseline" :x1="plot.left" :x2="plot.right"
          :y1="plot.bottom" :y2="plot.bottom" />
        <line v-if="activeIndex >= 0 && points[activeIndex]" class="chart-crosshair"
          :x1="points[activeIndex].x" :x2="points[activeIndex].x"
          :y1="plot.top" :y2="plot.bottom" />
        <polyline class="chart-line" :points="polyline" />
        <g v-for="point in points" :key="point.index">
          <circle class="chart-point" :cx="point.x" :cy="point.y" r="4" />
          <circle class="chart-hit" :cx="point.x" :cy="point.y" r="12" tabindex="0"
            role="button" :aria-label="`${point.label}，${metricColumn.label} ${format(point.value)}`"
            @mouseenter="select(point.index)" @focus="select(point.index)"
            @mouseleave="activeIndex = -1" @blur="activeIndex = -1" />
        </g>
        <g class="chart-x-labels">
          <text v-for="point in xLabels" :key="point.index" :x="point.x" y="282"
            :text-anchor="point.index === 0 ? 'start' : point.index === points.length - 1 ? 'end' : 'middle'">
            {{ point.label }}
          </text>
        </g>
      </svg>
    </template>

    <div v-else class="bar-chart">
      <div class="chart-tooltip" v-if="activeRow" role="status">
        <span>{{ activeRow.label }}</span>
        <strong>{{ format(activeRow.value) }}</strong>
        <small>{{ metricColumn.label }}</small>
      </div>
      <div v-for="row in rows" :key="row.index" class="bar-row">
        <span class="bar-label" :title="row.label">{{ row.label }}</span>
        <button type="button" class="bar-plot"
          :aria-label="`${row.label}，${metricColumn.label} ${format(row.value)}`"
          @mouseenter="select(row.index)" @focus="select(row.index)"
          @mouseleave="activeIndex = -1" @blur="activeIndex = -1">
          <span class="bar-zero" :style="{ left: `${zeroPercent}%` }" />
          <span class="bar-mark" :style="barStyle(row)" />
        </button>
      </div>
      <div class="bar-axis">
        <span v-for="tick in axisTicks()" :key="tick">{{ format(tick) }}</span>
      </div>
    </div>
    <p class="chart-help">悬停或键盘聚焦图形可读取数值；完整数据见下方明细表。</p>
  </section>
</template>

<style scoped>
.report-chart {
  --chart-surface: #fcfcfb;
  --chart-ink: #0b0b0b;
  --chart-muted: #52514e;
  --chart-grid: #e1e0d9;
  --chart-axis: #c3c2b7;
  --chart-series: #2a78d6;
  position: relative;
  padding: 16px;
  margin: 12px 0;
  color: var(--chart-ink);
  background: var(--chart-surface);
  border: 1px solid var(--chart-grid);
  border-radius: 12px;
  color-scheme: light;
}
.report-stat { display: grid; gap: 8px; padding: 18px 4px; }
.report-stat span, .report-stat small { color: var(--chart-muted); }
.report-stat strong { font-size: 48px; line-height: 1.1; font-weight: 650; }
.chart-tooltip {
  display: flex; align-items: baseline; gap: 10px; min-height: 28px;
  color: var(--chart-muted); font-size: 13px;
}
.chart-tooltip strong { color: var(--chart-ink); font-size: 20px; }
.line-chart { display: block; width: 100%; min-height: 280px; overflow: visible; }
.chart-grid line { stroke: var(--chart-grid); stroke-width: 1; }
.chart-grid text, .chart-x-labels text { fill: var(--chart-muted); font-size: 11px; font-variant-numeric: tabular-nums; }
.chart-baseline { stroke: var(--chart-axis); stroke-width: 1; }
.chart-crosshair { stroke: var(--chart-axis); stroke-width: 1; }
.chart-line { fill: none; stroke: var(--chart-series); stroke-width: 2; stroke-linecap: round; stroke-linejoin: round; }
.chart-point { fill: var(--chart-series); stroke: var(--chart-surface); stroke-width: 2; pointer-events: none; }
.chart-hit { fill: transparent; cursor: crosshair; outline: none; }
.chart-hit:focus { stroke: var(--chart-ink); stroke-width: 2; }
.bar-chart { display: grid; gap: 8px; }
.bar-row { display: grid; grid-template-columns: minmax(100px, 180px) 1fr; align-items: center; gap: 12px; }
.bar-label { overflow: hidden; color: var(--chart-muted); font-size: 13px; text-overflow: ellipsis; white-space: nowrap; }
.bar-plot { position: relative; height: 26px; padding: 0; background: transparent; border: 0; cursor: crosshair; }
.bar-plot:focus { outline: 2px solid var(--chart-ink); outline-offset: 2px; }
.bar-zero { position: absolute; top: 0; bottom: 0; width: 1px; background: var(--chart-axis); }
.bar-mark {
  position: absolute; top: 1px; height: 24px; min-width: 0;
  background: var(--chart-series); border-radius: 0 4px 4px 0;
}
.bar-plot:hover .bar-mark, .bar-plot:focus .bar-mark { filter: brightness(1.12); }
.bar-axis { display: flex; justify-content: space-between; margin-left: min(192px, 30%); color: var(--chart-muted); font-size: 11px; font-variant-numeric: tabular-nums; }
.chart-help { margin: 10px 0 0; color: var(--chart-muted); font-size: 12px; }
@media (prefers-color-scheme: dark) {
  .report-chart {
    --chart-surface: #1a1a19;
    --chart-ink: #f0efec;
    --chart-muted: #c3c2b7;
    --chart-grid: #2c2c2a;
    --chart-axis: #383835;
    --chart-series: #3987e5;
    color-scheme: dark;
  }
}
@media (forced-colors: active) {
  .bar-mark { background: CanvasText; }
  .chart-line, .chart-point { stroke: CanvasText; }
  .chart-point { fill: CanvasText; }
}
@media (max-width: 640px) {
  .report-chart { padding: 10px; }
  .bar-row { grid-template-columns: 96px 1fr; gap: 8px; }
  .bar-axis { margin-left: 104px; }
  .line-chart { min-width: 620px; }
  .report-chart:has(.line-chart) { overflow-x: auto; }
}
</style>
