<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { api } from '../api'
import { number } from '../format'
import AppIcon from '../components/AppIcon.vue'
import PageState from '../components/PageState.vue'

const range = ref('month'), dimension = ref('style')
const loading = ref(true), error = ref('')
const dashboard = ref({ summary: {}, plans: [], processes: [], rank: [], group_rank: [] })
const rendered = ref({ columns: [], groups: [] })
const dimensional = ref({ columns: [], rows: [] })
const custom = reactive({ start_date: '', end_date: '' })
const ranges = [['yesterday','昨日'],['today','今日'],['week','本周'],['month','本月'],['custom','自定义']]
const dimensions = [['style','款式'],['order','订单'],['bed','裁床'],['post','部门'],['group','组'],['user','人'],['process','工序']]
const summaryItems = computed(() => [
  ['已裁件数', dashboard.value.summary.cut_qty], ['完工件数', dashboard.value.summary.done_qty],
  ['在制件数', dashboard.value.summary.working_qty], ['登记件数', dashboard.value.summary.registered_qty],
  ['在产床次', dashboard.value.summary.active_beds], ['经手菲票', dashboard.value.summary.ticket_count],
])
const planRows = computed(() => rendered.value.groups.flatMap((group) => group.blocks.flatMap((block) => block.rows.map((row) => ({ ...row, date: group.date })))))
function params() { return { range: range.value, ...(range.value === 'custom' ? custom : {}) } }
async function load() {
  if (range.value === 'custom' && (!custom.start_date || !custom.end_date)) return
  loading.value = true; error.value = ''
  try {
    const requests = [api('/dashboard', { params: params() })]
    requests.push(dimension.value === 'style' ? api('/kanban/render', { params: params() }) : api('/dashboard/dim', { params: { ...params(), dim: dimension.value } }))
    const [summary, table] = await Promise.all(requests)
    dashboard.value = summary
    if (dimension.value === 'style') rendered.value = table
    else dimensional.value = table
  } catch (e) { error.value = e.message } finally { loading.value = false }
}
function chooseRange(value) { range.value = value; if (value !== 'custom') load() }
function chooseDimension(value) { dimension.value = value; load() }
function cell(row, index) { return row.cells[index]?.text ?? row.cells.find((item) => item.col === rendered.value.columns[index]?.id)?.text ?? '—' }
onMounted(load)
</script>
<template>
  <div class="welcome"><div><h2>生产看板</h2><p>按时间、款式与组织维度查看真实生产汇总。</p></div><button class="button secondary" @click="load"><AppIcon name="refresh" :size="16" />刷新</button></div>
  <div class="kanban-ranges" aria-label="看板时间范围"><button v-for="item in ranges" :key="item[0]" :class="{active:range===item[0]}" @click="chooseRange(item[0])">{{ item[1] }}</button></div>
  <form v-if="range==='custom'" class="filter-bar kanban-custom" @submit.prevent="load"><input v-model="custom.start_date" type="date" aria-label="开始日期" required /><input v-model="custom.end_date" type="date" aria-label="结束日期" required /><button class="button primary">查询</button></form>
  <PageState :loading="loading" :error="error" @retry="load">
    <section class="kanban-summary"><article v-for="item in summaryItems" :key="item[0]"><strong>{{ number(item[1]) }}</strong><span>{{ item[0] }}</span></article></section>
    <div class="kanban-dimensions" aria-label="看板统计维度"><button v-for="item in dimensions" :key="item[0]" :class="{active:dimension===item[0]}" @click="chooseDimension(item[0])">{{ item[1] }}</button></div>
    <section class="card table-card kanban-table-card">
      <div v-if="dimension==='style' && planRows.length" class="table-wrap"><table><thead><tr><th v-for="column in rendered.columns" :key="column.id">{{ column.title }}</th></tr></thead><tbody><tr v-for="row in planRows" :key="`${row.date}-${row.key}`"><td v-for="(column,index) in rendered.columns" :key="column.id">{{ cell(row,index) || column.placeholder || '—' }}</td></tr></tbody></table></div>
      <div v-else-if="dimension!=='style' && dimensional.rows.length" class="table-wrap"><table><thead><tr><th v-for="column in dimensional.columns" :key="column.key">{{ column.title }}</th></tr></thead><tbody><tr v-for="row in dimensional.rows" :key="row.key"><td v-for="(text,index) in row.cells" :key="index">{{ text || '—' }}</td></tr></tbody></table></div>
      <div v-else class="page-state">当前范围暂无生产数据</div>
    </section>
    <div class="kanban-bottom-grid"><section class="card"><div class="card-header"><h3>工序完成量</h3></div><div v-if="dashboard.processes.length" class="kanban-rank-list"><div v-for="(item,index) in dashboard.processes" :key="item.name"><span>{{ index+1 }}</span><strong>{{ item.name }}</strong><b>{{ number(item.qty) }} 件</b><small>{{ item.percent }}%</small></div></div><p v-else class="section-note">暂无工序数据</p></section><section class="card"><div class="card-header"><h3>员工登记排行</h3></div><div v-if="dashboard.rank.length" class="kanban-rank-list"><div v-for="(item,index) in dashboard.rank" :key="item.user_id"><span>{{ index+1 }}</span><strong>{{ item.name }}</strong><b>{{ number(item.qty) }} 件</b><small>{{ item.ticket_count }} 扎</small></div></div><p v-else class="section-note">暂无登记排行</p></section></div>
  </PageState>
</template>
