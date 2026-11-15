<script setup>
const props = defineProps({
  start: { type: String, default: '' },
  end: { type: String, default: '' },
  quick: { type: String, default: '' },
})
const emit = defineEmits(['change'])

const tabs = [
  { key: 'today', label: '今日' },
  { key: 'yesterday', label: '昨日' },
  { key: 'month', label: '本月' },
  { key: 'last_month', label: '上月' },
]

function isoDate(date) {
  const pad = (value) => String(value).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
}

function quickRange(key) {
  const now = new Date()
  const today = isoDate(now)
  if (key === 'today') return { start: today, end: today }
  if (key === 'yesterday') {
    const yesterday = new Date(now)
    yesterday.setDate(yesterday.getDate() - 1)
    const value = isoDate(yesterday)
    return { start: value, end: value }
  }
  if (key === 'month') return { start: `${today.slice(0, 8)}01`, end: today }
  const first = new Date(now.getFullYear(), now.getMonth() - 1, 1)
  const last = new Date(now.getFullYear(), now.getMonth(), 0)
  return { start: isoDate(first), end: isoDate(last) }
}

function chooseQuick(key) {
  if (key === props.quick) return
  emit('change', { ...quickRange(key), quick: key })
}

function changeStart(event) {
  const start = event.target.value
  if (!start) return
  const end = props.end && props.end >= start ? props.end : start
  emit('change', { start, end, quick: '' })
}

function changeEnd(event) {
  const end = event.target.value
  if (!end) return
  const start = props.start && props.start <= end ? props.start : end
  emit('change', { start, end, quick: '' })
}
</script>

<template>
  <div class="date-range-bar" aria-label="日期区间与快捷筛选">
    <div class="date-range-fields">
      <input class="date-range-input" :value="start" type="date" aria-label="日期区间开始" @change="changeStart" />
      <span class="date-range-separator">至</span>
      <input class="date-range-input" :value="end" type="date" aria-label="日期区间结束" @change="changeEnd" />
    </div>
    <div class="date-range-tabs" aria-label="日期快捷筛选">
      <button
        v-for="tab in tabs"
        :key="tab.key"
        type="button"
        :class="{ active: quick === tab.key }"
        :aria-pressed="quick === tab.key"
        @click="chooseQuick(tab.key)"
      >{{ tab.label }}</button>
    </div>
  </div>
</template>

<style scoped>
.date-range-bar {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 7px;
  min-width: 0;
  padding: 6px 8px;
  border-radius: 999px;
  background: #fff;
}
.date-range-fields {
  display: flex;
  align-items: center;
  flex: 1 1 auto;
  min-width: 0;
}
.date-range-input {
  width: 104px;
  min-width: 0;
  min-height: 30px;
  padding: 4px 2px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--ink);
  font-size: 11px;
  white-space: nowrap;
}
.date-range-separator {
  flex: none;
  margin: 0 3px;
  color: var(--muted);
  font-size: 11px;
}
.date-range-tabs {
  display: flex;
  flex: none;
  gap: 1px;
  margin-left: auto;
  padding: 2px;
  border-radius: 999px;
  background: #f2f3f5;
}
.date-range-tabs button {
  min-height: 28px;
  padding: 4px 8px;
  border: 0;
  border-radius: 999px;
  background: transparent;
  color: var(--text-secondary);
  font-size: 11px;
  white-space: nowrap;
}
.date-range-tabs button.active {
  background: var(--orange);
  color: #fff;
  font-weight: 600;
}
@media (max-width: 450px) {
  .date-range-bar { border-radius: 12px; }
  .date-range-fields { flex: 1 1 100%; justify-content: center; }
  .date-range-input { width: 116px; }
  .date-range-tabs { width: 100%; margin-left: 0; }
  .date-range-tabs button { flex: 1; padding-inline: 4px; }
}
</style>
