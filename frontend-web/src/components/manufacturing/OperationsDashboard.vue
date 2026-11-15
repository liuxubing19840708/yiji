<script setup>
import { computed, inject, onMounted, reactive, ref } from 'vue'
import { api } from '../../api'
import { dateTime } from '../../format'
import PageState from '../PageState.vue'

const emit = defineEmits(['open-route'])
const flow = inject('manufacturingFlow')
const data = ref(null)
const filters = reactive({ orderId: '', productionId: '', customerId: '', styleNo: '', q: '' })
const customers = computed(() => {
  const byId = new Map()
  flow.state.orders.forEach((order) => { if (order.customer) byId.set(order.customer, order.customer_name || `客户 #${order.customer}`) })
  return [...byId.entries()].map(([id, name]) => ({ id, name }))
})
const loading = ref(true)
const error = ref('')
const stalledHours = ref(24)
const page = ref(1)
const pageSize = 20
const onlyStalled = ref(false)
async function load(pageNumber = page.value) {
  loading.value = true
  error.value = ''
  try {
    data.value = await api('/manufacturing/operations', { params: {
      stalled_hours: stalledHours.value, page: pageNumber, page_size: pageSize,
      order_id: filters.orderId || undefined, production_id: filters.productionId || undefined,
      customer_id: filters.customerId || undefined, style_no: filters.styleNo.trim() || undefined,
      q: filters.q.trim() || undefined,
    } })
    page.value = data.value.page
  }
  catch (reason) { error.value = reason.message }
  finally { loading.value = false }
}
function resetFilters() {
  Object.assign(filters, { orderId: '', productionId: '', customerId: '', styleNo: '', q: '' })
  onlyStalled.value = false
  load(1)
}
function changePage(pageNumber) {
  if (pageNumber < 1 || pageNumber > (data.value?.pages || 1) || loading.value) return
  load(pageNumber)
}
function visibleRows() {
  return (data.value?.results || []).filter((row) => !onlyStalled.value || row.stalled_nodes.length)
}
onMounted(() => load(1))
</script>

<template>
  <div class="operations-dashboard">
    <section class="card operations-filter" aria-label="运营看板筛选">
      <label>订单<select v-model="filters.orderId" aria-label="筛选订单"><option value="">全部订单</option><option v-for="order in flow.state.orders" :key="order.id" :value="order.id">{{ order.order_no }}</option></select></label>
      <label>床次<select v-model="filters.productionId" aria-label="筛选床次"><option value="">全部床次</option><option v-for="production in flow.state.productions" :key="production.id" :value="production.id">{{ production.bed_no }}</option></select></label>
      <label>客户<select v-model="filters.customerId" aria-label="筛选客户"><option value="">全部客户</option><option v-for="customer in customers" :key="customer.id" :value="customer.id">{{ customer.name }}</option></select></label>
      <label>款号<input v-model="filters.styleNo" aria-label="筛选款号" placeholder="款号关键字" @keyup.enter="load(1)" /></label>
      <label>路线 / 单号<input v-model="filters.q" aria-label="筛选路线或单号" placeholder="路线、订单或床号" @keyup.enter="load(1)" /></label>
      <label>滞留阈值<select v-model.number="stalledHours"><option :value="1">1 小时</option><option :value="24">24 小时</option><option :value="72">72 小时</option><option :value="168">7 天</option></select></label>
      <label class="stalled-filter"><input v-model="onlyStalled" type="checkbox" />只看滞留路线</label>
      <button class="button primary small" :disabled="loading" @click="load(1)">查询</button>
      <button class="button ghost small" :disabled="loading" @click="resetFilters">重置</button>
    </section>
    <PageState :loading="loading" :error="error" :empty="!visibleRows().length" empty-text="当前筛选下暂无生产路线" @retry="load">
      <template v-if="data">
        <section class="operations-summary" aria-label="全部路线数量汇总">
          <div><span>生产路线</span><strong>{{ data.summary.routes }}</strong></div>
          <div><span>生产在手</span><strong>{{ data.summary.wip }}</strong></div>
          <div><span>累计报废</span><strong>{{ data.summary.scrapped }}</strong></div>
          <div><span>成品现货</span><strong>{{ data.summary.finished_available }}</strong></div>
          <div><span>滞留路线</span><strong>{{ data.summary.stalled_routes }}</strong></div>
        </section>
        <p class="section-note">顶部指标汇总全部 {{ data.total }} 条筛选路线；表格第 {{ data.page }} / {{ data.pages || 1 }} 页，每页 {{ data.page_size }} 条。“只看滞留路线”作用于当前页。</p>
        <section class="card operations-card">
          <div class="card-header"><div><h3>跨订单、跨床次生产状态</h3><p>数量来自统一流水；滞留指节点有在手数量且最后流水活动早于阈值。</p></div></div>
          <div class="table-wrap"><table><thead><tr><th>路线 / 业务锚点</th><th>生产在手</th><th>累计报废</th><th>成品现货</th><th>交付</th><th>待办</th><th>滞留节点</th><th>操作</th></tr></thead><tbody><tr v-for="row in visibleRows()" :key="row.route"><td><strong>{{ row.route_code }}</strong><small>{{ row.route_name }} · {{ row.production ? `床次 #${row.production}` : `订单 #${row.order || '—'}` }}</small></td><td>{{ row.summary.wip }}</td><td>{{ row.summary.scrapped }}</td><td>{{ row.summary.finished_available }}</td><td>{{ row.delivery ? `未交 ${row.delivery.remaining} / 订单 ${row.delivery.ordered}` : '—' }}</td><td>检 {{ row.pending.quality }} · 裁 {{ row.pending.cutting }} · 包 {{ row.pending.packing }} · 入 {{ row.pending.inbound }}</td><td><span v-if="!row.stalled_nodes.length">无</span><div v-for="node in row.stalled_nodes" v-else :key="node.node_id" class="stalled-node"><strong>滞留 · {{ node.stage_name }}</strong><small>在手 {{ node.available }} · 最后活动 {{ dateTime(node.last_activity_at) }}</small></div></td><td><button class="button ghost small" @click="emit('open-route', row.route)">查看路线</button></td></tr></tbody></table></div>
          <div class="operations-pagination"><button class="button secondary small" :disabled="!data.has_previous || loading" @click="changePage(data.page - 1)">上一页</button><span>第 {{ data.page }} / {{ data.pages || 1 }} 页，共 {{ data.total }} 条</span><button class="button secondary small" :disabled="!data.has_next || loading" @click="changePage(data.page + 1)">下一页</button></div>
        </section>
      </template>
    </PageState>
  </div>
</template>

<style scoped>
.operations-dashboard{display:grid;gap:16px}.operations-filter{display:grid;grid-template-columns:repeat(4,minmax(130px,1fr));align-items:end;gap:12px;padding:14px 18px}.operations-filter>label:not(.stalled-filter){display:flex;flex-direction:column;gap:6px;color:var(--text-secondary);font-size:12px}.operations-filter select,.operations-filter input:not([type=checkbox]){box-sizing:border-box;width:100%;min-height:34px;padding:6px 9px;border:1px solid var(--line);border-radius:7px;background:var(--surface,#fff)}.stalled-filter{display:flex;align-items:center;gap:7px;min-height:34px;color:var(--text-secondary);font-size:12px}.stalled-filter input{width:16px;min-height:16px}.operations-summary{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:1px;border:1px solid var(--line);border-radius:12px;background:var(--line);overflow:hidden}.operations-summary>div{padding:18px;background:var(--surface,#fff)}.operations-summary span,.operations-summary strong{display:block}.operations-summary span{color:var(--muted);font-size:12px}.operations-summary strong{margin-top:7px;font-size:25px}.operations-card{padding:0;overflow:hidden}.operations-card>.card-header{padding:18px 20px 10px}.table-wrap{overflow:auto}table{width:100%;border-collapse:collapse;font-size:12px}th,td{padding:11px 12px;border-top:1px solid var(--line);text-align:left;vertical-align:top}th{color:var(--muted);font-weight:500;white-space:nowrap}td>strong,td>small,.stalled-node strong,.stalled-node small{display:block}td>small,.stalled-node small{margin-top:4px;color:var(--muted)}.stalled-node{margin-bottom:9px;white-space:nowrap}.stalled-node strong{font-weight:500}.operations-pagination{display:flex;align-items:center;justify-content:center;gap:14px;padding:14px 18px;color:var(--text-secondary);font-size:12px}
@media(max-width:760px){.operations-summary{grid-template-columns:repeat(2,minmax(0,1fr))}.operations-summary>div:last-child{grid-column:1/-1}.operations-filter{grid-template-columns:repeat(2,minmax(0,1fr))}.operations-card>.card-header{padding:14px 12px 8px}}
</style>
