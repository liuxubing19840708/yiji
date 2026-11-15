<script setup>
import { computed, onMounted, provide, reactive, ref, watch } from 'vue'
import { api, rows } from '../api'
import { hasPermission } from '../permissions'
import AppIcon from '../components/AppIcon.vue'
import FlowContextBar from '../components/manufacturing/FlowContextBar.vue'
import RouteWorkspace from '../components/manufacturing/RouteWorkspace.vue'
import QualityInspections from '../components/manufacturing/QualityInspections.vue'
import CuttingHandovers from '../components/manufacturing/CuttingHandovers.vue'
import FinishedGoods from '../components/manufacturing/FinishedGoods.vue'
import FinishedInventory from '../components/manufacturing/FinishedInventory.vue'
import PreproductionApproval from '../components/manufacturing/PreproductionApproval.vue'
import FlowDashboard from '../components/manufacturing/FlowDashboard.vue'
import OperationsDashboard from '../components/manufacturing/OperationsDashboard.vue'

const emit = defineEmits(['toast', 'navigate'])
const state = reactive({
  routes: [], stages: [], productions: [], orders: [], tickets: [],
  selectedRouteId: '', selectedTicketId: '', selectedNodeId: '',
  loading: true, ticketLoading: false, contextError: '', baseError: '',
})
const route = computed(() => state.routes.find((item) => item.id === Number(state.selectedRouteId)) || null)
const ticket = computed(() => state.tickets.find((item) => item.id === Number(state.selectedTicketId)) || null)
const nodes = computed(() => [...(route.value?.nodes || [])].sort((a, b) => a.sequence - b.sequence))
const productionById = computed(() => new Map(state.productions.map((item) => [item.id, item])))
const orderById = computed(() => new Map(state.orders.map((item) => [item.id, item])))
const reloadKey = ref(0)

const tabs = [
  { key: 'execution', label: '路线执行', permission: 'manufacturing.view', component: RouteWorkspace },
  { key: 'dashboard', label: '流程看板', permission: 'manufacturing.view', component: FlowDashboard },
  { key: 'operations', label: '运营看板', permission: 'manufacturing.view', component: OperationsDashboard },
  { key: 'quality', label: '节点质检', permission: 'quality.view', component: QualityInspections },
  { key: 'cutting', label: '裁片交接', permission: 'cutting.view', component: CuttingHandovers },
  { key: 'finished', label: '包装入库', permission: 'finished_goods.view', component: FinishedGoods },
  { key: 'inventory', label: '成品现货', permission: 'finished_goods.view', component: FinishedInventory },
  { key: 'preproduction', label: '产前审批', permission: 'preproduction.view', component: PreproductionApproval },
]
const visibleTabs = tabs.filter((tab) => hasPermission(tab.permission))
const activeTab = ref(visibleTabs[0]?.key || '')
const currentTab = computed(() => visibleTabs.find((tab) => tab.key === activeTab.value) || visibleTabs[0])

function routeAnchor(item) {
  if (item.is_template) return '模板'
  if (item.production) {
    const production = productionById.value.get(item.production)
    return production ? `${production.bed_no} · ${production.style_no}` : `床次 #${item.production}`
  }
  const order = orderById.value.get(item.order)
  return order ? `${order.order_no} · ${order.style_no}` : `订单 #${item.order || '—'}`
}
function ticketFor(id) { return state.tickets.find((item) => item.id === Number(id)) || null }
function nodeFor(id) { return nodes.value.find((item) => item.id === Number(id)) || null }

async function fetchAll(path, params = {}) {
  const result = []
  let page = 1
  while (page <= 50) {
    const data = await api(path, { params: { ...params, page, page_size: 100 } })
    result.push(...rows(data))
    if (!data?.next) break
    page += 1
  }
  return result
}
async function loadTickets() {
  const selected = route.value
  state.selectedTicketId = ''
  state.tickets = []
  if (!selected || selected.is_template) return
  state.ticketLoading = true
  state.contextError = ''
  try {
    if (selected.production) {
      state.tickets = await fetchAll('/tickets', { production_id: selected.production })
    } else {
      const beds = state.productions.filter((item) => item.order === selected.order)
      const groups = await Promise.all(beds.map((bed) => fetchAll('/tickets', { production_id: bed.id })))
      state.tickets = groups.flat().sort((a, b) => String(a.bed_no).localeCompare(String(b.bed_no)) || Number(a.bundle_no) - Number(b.bundle_no))
    }
    state.selectedTicketId = state.tickets.length ? String(state.tickets[0].id) : ''
  } catch (error) {
    state.contextError = `菲票读取失败：${error.message}`
  } finally { state.ticketLoading = false }
}
function selectRoute(id) {
  state.selectedRouteId = String(id || '')
  state.selectedNodeId = nodes.value.length ? String(nodes.value[0].id) : ''
  loadTickets()
}
async function loadBase() {
  if (!hasPermission('manufacturing.view')) { state.loading = false; return }
  state.loading = true
  state.baseError = ''
  try {
    const [routeData, stageData, productionData, orderData] = await Promise.all([
      api('/manufacturing/routes'), api('/manufacturing/stages'),
      fetchAll('/productions'), fetchAll('/orders'),
    ])
    state.routes = rows(routeData)
    state.stages = rows(stageData)
    state.productions = productionData
    state.orders = orderData
    const preferred = state.routes.find((item) => item.id === Number(state.selectedRouteId))
      || state.routes.find((item) => !item.is_template) || state.routes[0]
    if (preferred) await selectRoute(preferred.id)
    else { state.selectedRouteId = ''; state.selectedNodeId = ''; state.tickets = [] }
  } catch (error) { state.baseError = error.message } finally { state.loading = false }
}
async function reloadRoutes(selectId = state.selectedRouteId) {
  state.routes = rows(await api('/manufacturing/routes'))
  if (selectId) await selectRoute(selectId)
}
function refresh() { reloadKey.value += 1; return loadBase() }
function notify(message) { emit('toast', message) }

provide('manufacturingFlow', {
  state, route, ticket, nodes, productionById, orderById,
  routeAnchor, ticketFor, nodeFor, selectRoute, loadTickets, reloadRoutes,
  can: hasPermission, notify,
})
watch(activeTab, (key) => { if (key !== 'preproduction' && hasPermission('manufacturing.view') && !state.routes.length && !state.loading) loadBase() })
onMounted(loadBase)
</script>

<template>
  <div class="manufacturing-flow">
    <div class="welcome"><div><h2>全流程生产</h2><p>以同一生产路线和菲票贯穿报工、质检、裁片交接、包装审核与成品入库。</p></div><div class="form-actions"><button v-if="hasPermission('handover.view')" class="button secondary" @click="emit('navigate', 'handovers')"><AppIcon name="building" :size="16" />责任交接中心</button><button class="button secondary" @click="refresh"><AppIcon name="refresh" :size="16" />刷新当前流程</button></div></div>
    <nav v-if="visibleTabs.length" class="flow-steps" aria-label="生产流程环节"><button v-for="(tab, index) in visibleTabs" :key="tab.key" :class="{ active: activeTab === tab.key }" @click="activeTab = tab.key"><span>{{ index + 1 }}</span>{{ tab.label }}</button></nav>
    <div v-if="!visibleTabs.length" class="card page-state">当前岗位没有生产流程查看权限，请联系管理员授权。</div>
    <template v-else>
      <div v-if="state.baseError" class="form-error flow-base-error" role="alert">生产基础数据读取失败：{{ state.baseError }} <button class="button ghost small" @click="loadBase">重试</button></div>
      <FlowContextBar v-if="hasPermission('manufacturing.view') && !['preproduction', 'operations'].includes(activeTab)" />
      <div v-if="state.loading && !['preproduction', 'operations'].includes(activeTab)" class="card page-state"><span class="spinner" />正在读取生产路线…</div>
      <component :is="currentTab.component" v-else :key="`${activeTab}-${reloadKey}`" @toast="notify" @open-route="selectRoute($event); activeTab = 'dashboard'" />
    </template>
  </div>
</template>

<style scoped>
.flow-steps{display:flex;gap:8px;margin-bottom:16px;padding:2px 0 8px;overflow-x:auto;scrollbar-width:thin}.flow-steps button{display:flex;align-items:center;justify-content:center;gap:7px;flex:1;min-width:max-content;border:0;border-radius:999px;background:#fff;box-shadow:0 1px 4px #1F232908;padding:10px 17px;color:var(--text-secondary);font-size:13px;white-space:nowrap;cursor:pointer}.flow-steps button>span{display:grid;place-items:center;width:20px;height:20px;border-radius:50%;background:#F2F3F5;font-size:10px}.flow-steps button.active{background:var(--orange);color:#fff;box-shadow:none;font-weight:600}.flow-steps button.active>span{background:#FFFFFF26;color:#fff}.flow-base-error{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:14px}
@media(max-width:760px){.flow-steps{margin-inline:-12px;padding:2px 12px 8px;scrollbar-width:none}.flow-steps button{padding:9px 14px;font-size:12px}.flow-steps button>span{display:none}}
</style>
