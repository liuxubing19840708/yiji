<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { api, rows } from '../api'
import { dateTime, monthRange, number, statusText } from '../format'
import AppIcon from '../components/AppIcon.vue'
import PageState from '../components/PageState.vue'
import CommercialModal from '../components/CommercialModal.vue'
import CommercialPagination from '../components/CommercialPagination.vue'
import MobileLoadMore from '../components/MobileLoadMore.vue'

const emit = defineEmits(['toast'])
const props = defineProps({ section: { type: String, default: '' } })
const active = ref(['costs', 'wage', 'pnl'].includes(props.section) ? props.section : 'costs')
const loading = ref(true), error = ref(''), costData = ref({ count: 0 })
const filters = reactive({ ...monthRange(), keyword: '', computed_at_from: '', page: 1 })
const costQuick = ref('month')
const pnlLoading = ref(false), pnlError = ref(''), pnlData = ref({ count: 0 }), pnlLoaded = ref(false)
const pnlFilters = reactive({ ...monthRange(), keyword: '', status: '', page: 1 })
const costLoadingMore = ref(false), costAppendError = ref('')
const pnlLoadingMore = ref(false), pnlAppendError = ref('')
const costHasMore = computed(() => rows(costData.value).length < Number(costData.value.count || 0))
const pnlHasMore = computed(() => rows(pnlData.value).length < Number(pnlData.value.count || 0))
const wageLoading = ref(false), wageError = ref(''), wageData = ref(null), wageLoaded = ref(false)
const wageFilters = reactive(monthRange())
const overheadRow = ref(null), overheadValue = ref(''), overheadSaving = ref(false), overheadError = ref('')
const actionBusy = ref('')
let costSearchTimer, pnlSearchTimer
const amt = (v) => v === null || v === undefined || v === '' ? '—' : String(v)
const percent = (v) => v === null || v === undefined || v === '' ? '—' : `${v}%`
function isoDay(date) { const d = new Date(date.getTime() - date.getTimezoneOffset() * 60000); return d.toISOString().slice(0, 10) }
function setCostQuick(key) {
  const now = new Date(); now.setHours(12, 0, 0, 0)
  if (key === 'month') Object.assign(filters, monthRange())
  else if (key === 'today' || key === 'yesterday') {
    if (key === 'yesterday') now.setDate(now.getDate() - 1)
    filters.date_from = isoDay(now); filters.date_to = isoDay(now)
  } else if (key === 'last_month') {
    const start = new Date(now.getFullYear(), now.getMonth() - 1, 1, 12)
    const end = new Date(now.getFullYear(), now.getMonth(), 0, 12)
    filters.date_from = isoDay(start); filters.date_to = isoDay(end)
  }
  costQuick.value = key
}
const noOrderCount = computed(() => Math.max(0, ...rows(costData.value).map((r) => Number(r.no_order_cnt) || 0)))
function notify(message) { emit('toast', message) }

async function loadCosts(append = false) {
  if (!append && window.matchMedia('(max-width: 760px)').matches) filters.page = 1
  const requestedPage = filters.page
  if (append) { costLoadingMore.value = true; costAppendError.value = '' }
  else { loading.value = true; error.value = ''; costAppendError.value = '' }
  try {
    const result = await api('/cost/orders', { params: { ...filters, page_size: 20 } })
    costData.value = append ? { ...result, results: [...rows(costData.value), ...rows(result)] } : result
  } catch (e) {
    if (append) { costAppendError.value = e.message; filters.page = Math.max(1, requestedPage - 1) }
    else error.value = e.message
  } finally { loading.value = false; costLoadingMore.value = false }
}
function loadMoreCosts() {
  if (!costHasMore.value || costLoadingMore.value || loading.value) return
  filters.page += 1
  return loadCosts(true)
}
function searchCosts() { clearTimeout(costSearchTimer); costSearchTimer = setTimeout(() => { filters.page = 1; loadCosts() }, 300) }
function costPage(page) { filters.page = page; loadCosts() }
async function loadPnl(append = false) {
  if (!append && window.matchMedia('(max-width: 760px)').matches) pnlFilters.page = 1
  const requestedPage = pnlFilters.page
  if (append) { pnlLoadingMore.value = true; pnlAppendError.value = '' }
  else { pnlLoading.value = true; pnlError.value = ''; pnlAppendError.value = '' }
  try {
    const result = await api('/cost/orders/pnl', { params: { ...pnlFilters, page_size: 20 } })
    pnlData.value = append ? { ...result, results: [...rows(pnlData.value), ...rows(result)] } : result
    pnlLoaded.value = true
  } catch (e) {
    if (append) { pnlAppendError.value = e.message; pnlFilters.page = Math.max(1, requestedPage - 1) }
    else pnlError.value = e.message
  } finally { pnlLoading.value = false; pnlLoadingMore.value = false }
}
function loadMorePnl() {
  if (!pnlHasMore.value || pnlLoadingMore.value || pnlLoading.value) return
  pnlFilters.page += 1
  return loadPnl(true)
}
function searchPnl() { clearTimeout(pnlSearchTimer); pnlSearchTimer = setTimeout(() => { pnlFilters.page = 1; loadPnl() }, 300) }
function pnlPage(page) { pnlFilters.page = page; loadPnl() }
async function loadWage() {
  wageLoading.value = true; wageError.value = ''
  try { wageData.value = await api('/cost/wage-reconciliation', { params: wageFilters }); wageLoaded.value = true }
  catch (e) { wageError.value = e.message }
  finally { wageLoading.value = false }
}
function switchTab(tab) {
  active.value = tab
  if (tab === 'pnl' && !pnlLoaded.value) loadPnl()
  if (tab === 'wage' && !wageLoaded.value) loadWage()
}
async function recompute(row) {
  if (actionBusy.value) return
  if (!window.confirm(`重算订单 ${row.order_no} 的成本？将按当前领料、登记和外发数据覆盖上次计算结果。`)) return
  actionBusy.value = `order-${row.order}`
  try { await api(`/cost/orders/${row.order}/recompute`, { method: 'POST', body: {} }); notify(`${row.order_no} 已重算`); await loadCosts() }
  catch (e) { notify(e.message) }
  finally { actionBusy.value = '' }
}
async function recomputeBatch() {
  if (actionBusy.value) return
  if (!filters.date_from && !filters.date_to) { notify('请先选择订单创建日期范围'); return }
  if (!window.confirm(`批量重算创建日期 ${filters.date_from || '最早'} 至 ${filters.date_to || '今天'} 的全部订单成本？`)) return
  actionBusy.value = 'batch'
  try {
    const result = await api('/cost/orders/recompute-batch', { method: 'POST', body: { date_from: filters.date_from || undefined, date_to: filters.date_to || undefined } })
    const failed = result.failed || []
    const failureText = failed.map((f) => `订单 #${f.order_id}：${f.error}`).join('\n')
    window.alert(`批量重算完成：成功 ${result.count || 0} 单，失败 ${failed.length} 单。${failureText ? `\n${failureText}` : ''}`)
    await loadCosts()
  } catch (e) { notify(e.message) }
  finally { actionBusy.value = '' }
}
function openOverhead(row) { overheadRow.value = row; overheadValue.value = row.overhead_cost ?? '0'; overheadError.value = '' }
async function saveOverhead() {
  const value = Number(overheadValue.value)
  if (overheadValue.value === '' || !Number.isFinite(value) || value < 0) { overheadError.value = '制费金额须为不小于 0 的数字'; return }
  overheadSaving.value = true; overheadError.value = ''
  try {
    await api(`/cost/orders/${overheadRow.value.order}/overhead`, { method: 'PUT', body: { overhead_cost: overheadValue.value } })
    notify('制费已保存，成本已标记待重算'); overheadRow.value = null; await loadCosts()
  } catch (e) { overheadError.value = e.message }
  finally { overheadSaving.value = false }
}
function explainMissing(row) { window.alert(`${row.order_no} 有 ${row.missing_price_cnt} 行缺价。批次无价、0 价或单位换算缺参时不计材料成本；请补齐批次价格与换算参数后重算。`) }

onMounted(() => { if (active.value === 'costs') loadCosts(); else switchTab(active.value) })
</script>

<template>
  <div class="welcome"><div><h2>成本核算</h2><p>订单全成本、工资口径对账及理论/实收双口径损益。</p></div><div class="commercial-actions"><button class="button secondary" @click="active === 'costs' ? loadCosts() : active === 'pnl' ? loadPnl() : loadWage()"><AppIcon name="refresh" :size="16" />刷新</button><button v-if="active === 'costs'" class="button primary" :disabled="!!actionBusy" @click="recomputeBatch">{{ actionBusy === 'batch' ? '批量重算中…' : '按日期批量重算' }}</button></div></div>
  <div class="segmented commercial-tabs"><button :class="{ active: active === 'costs' }" @click="switchTab('costs')">订单成本</button><button :class="{ active: active === 'wage' }" @click="switchTab('wage')">工资对账</button><button :class="{ active: active === 'pnl' }" @click="switchTab('pnl')">订单损益</button></div>

  <template v-if="active === 'costs'">
    <form class="filter-bar cost-filter" @submit.prevent="filters.page = 1; loadCosts()"><div class="cost-date-chips"><button v-for="quick in [['today','今天'],['yesterday','昨天'],['month','本月'],['last_month','上月']]" :key="quick[0]" type="button" :class="{ active: costQuick === quick[0] }" @click="setCostQuick(quick[0])">{{ quick[1] }}</button></div><div class="cost-filter-main"><label class="search-box"><AppIcon name="search" :size="17" /><input v-model="filters.keyword" placeholder="订单号/客户/款号/款名" @input="searchCosts" /></label><button class="button primary commercial-query">查询</button></div><details class="cost-custom-range" open><summary>自定义日期与计算时间</summary><label class="field"><span>订单创建开始</span><input v-model="filters.date_from" type="date" @change="costQuick = ''" /></label><label class="field"><span>订单创建截止</span><input v-model="filters.date_to" type="date" @change="costQuick = ''" /></label><label class="field cost-computed-field"><span>计算时间不早于</span><input v-model="filters.computed_at_from" type="date" /></label></details></form>
    <div v-if="noOrderCount" class="form-error commercial-notice">有成本活动但未挂订单的床次共 {{ noOrderCount }} 个，其成本不会归入任何订单，请尽快补挂订单。</div>
    <section class="card table-card cost-list-card"><PageState :loading="loading" :error="error" :empty="!rows(costData).length" empty-text="该范围内暂无成本行，登记或领料后重算生成" @retry="loadCosts"><div class="table-wrap desktop-table"><table class="cost-table"><thead><tr><th>订单 / 款式</th><th>状态</th><th class="numeric">理论收入</th><th class="numeric">材料</th><th class="numeric">人工</th><th class="numeric">外发</th><th class="numeric">制费</th><th class="numeric">总成本</th><th class="numeric">产出 / 单件</th><th class="numeric">毛利 / 毛利率</th><th>计算信息</th><th>操作</th></tr></thead><tbody><tr v-for="row in rows(costData)" :key="row.id"><td><span class="cell-title">{{ row.order_no }}</span><small class="cell-subtitle">{{ row.customer_name || '无客户' }} · {{ row.style_no }} {{ row.style_name }}</small><small v-if="row.missing_price_cnt" class="cell-subtitle missing-link" @click="explainMissing(row)">缺价 {{ row.missing_price_cnt }} 行（查看说明）</small></td><td><span class="badge" :class="row.order_status">{{ statusText(row.order_status) }}</span><small v-if="row.is_stale" class="badge producing stale-badge">待重算</small></td><td class="numeric">{{ amt(row.revenue) }}</td><td class="numeric">{{ amt(row.material_cost) }}</td><td class="numeric">{{ amt(row.labor_cost) }}</td><td class="numeric">{{ amt(row.outsourcing_cost) }}</td><td class="numeric">{{ amt(row.overhead_cost) }}</td><td class="numeric cell-title">{{ amt(row.total_cost) }}</td><td class="numeric">{{ number(row.output_qty) }}<small class="cell-subtitle">{{ amt(row.unit_cost) }}</small></td><td class="numeric">{{ amt(row.gross_profit) }}<small class="cell-subtitle">{{ percent(row.gross_margin) }}</small></td><td>{{ dateTime(row.computed_at) }}<small class="cell-subtitle">{{ row.computed_by_name || '未计算' }}</small></td><td><div class="row-actions"><button class="button secondary small" :disabled="!!actionBusy" @click="recompute(row)">重算</button><button class="button ghost small" @click="openOverhead(row)">制费</button></div></td></tr></tbody></table></div><div class="mobile-cards cost-mobile-list"><article v-for="row in rows(costData)" :key="row.id" class="mobile-row cost-order-card"><div class="mobile-row-head"><div class="cost-card-title"><span class="mobile-row-title">{{ row.order_no }}</span><small class="cell-subtitle">{{ row.customer_name || '无客户' }}<template v-if="row.style_no"> · {{ row.style_no }} {{ row.style_name }}</template></small></div><div class="cost-card-badges"><span v-if="row.is_stale" class="badge producing">数据待更新</span><button v-if="row.missing_price_cnt" class="cost-missing-badge" type="button" @click="explainMissing(row)">缺价{{ row.missing_price_cnt }}行</button><span v-if="!row.is_stale && !row.missing_price_cnt" class="badge" :class="row.order_status">{{ statusText(row.order_status) }}</span></div></div><div class="mobile-meta cost-metric-grid"><span><strong>{{ amt(row.revenue) }}</strong><small>收入(理论)</small></span><span><strong>{{ amt(row.material_cost) }}</strong><small>材料</small></span><span><strong>{{ amt(row.labor_cost) }}</strong><small>人工</small></span><span><strong>{{ amt(row.outsourcing_cost) }}</strong><small>外发</small></span><span><strong>{{ amt(row.overhead_cost) }}</strong><small>制费</small></span><span><strong>{{ amt(row.total_cost) }}</strong><small>合计成本</small></span><span><strong>{{ amt(row.unit_cost) }}</strong><small>单件成本</small></span><span><strong :class="{ 'negative-profit': Number(row.gross_profit) < 0 }">{{ amt(row.gross_profit) }}<small v-if="row.gross_margin !== null && row.gross_margin !== undefined && row.gross_margin !== ''"> / {{ percent(row.gross_margin) }}</small></strong><small>毛利额/率</small></span></div><p class="cost-computed-meta">{{ dateTime(row.computed_at) }}<template v-if="row.computed_by_name"> · {{ row.computed_by_name }}</template> · 产出 {{ number(row.output_qty) }} 件</p><div class="row-actions mobile-actions"><button class="button secondary small" :disabled="!!actionBusy" @click="recompute(row)">重算</button><button class="button ghost small" @click="openOverhead(row)">录制费</button></div></article></div></PageState><MobileLoadMore :loading="costLoadingMore" :error="costAppendError" :has-more="costHasMore" :count="rows(costData).length" @more="loadMoreCosts" /><CommercialPagination class="desktop-pagination" :page="filters.page" :count="costData.count || 0" :disabled="loading" @change="costPage" /></section>
    <p class="section-note">金额单位为元，直接展示后端字符串。理论收入按订单色码数量×定价，与实际收款存在差异属预期。</p>
  </template>

  <template v-else-if="active === 'wage'">
    <form class="filter-bar" @submit.prevent="loadWage"><label class="field"><span>开始日期</span><input v-model="wageFilters.date_from" type="date" /></label><label class="field"><span>结束日期</span><input v-model="wageFilters.date_to" type="date" /></label><button class="button primary commercial-query">查询对账</button></form>
    <PageState :loading="wageLoading" :error="wageError" :empty="!wageData?.rows?.length" empty-text="当前期间暂无工资对账数据" @retry="loadWage"><section class="stats-grid wage-stats"><article class="stat-card salary-highlight"><div class="stat-label">期间已结算工资<AppIcon class="stat-icon" name="wallet" /></div><div class="stat-value amount-value">{{ amt(wageData.paid_total) }}</div><p class="stat-note">结算单快照，按期间重叠取数</p></article><article class="stat-card"><div class="stat-label">未结算应发合计</div><div class="stat-value amount-value">{{ amt(wageData.wage_total) }}</div><p class="stat-note">{{ wageData.date_from }} 至 {{ wageData.date_to }}</p></article></section><section class="card table-card"><div class="card-header wage-heading"><div><h3>逐项口径对账</h3><p>差异 = 本行金额 − 期间已结算工资；口径差异属预期</p></div></div><div class="table-wrap wage-desktop-table"><table><thead><tr><th>项目</th><th>取数来源</th><th class="numeric">金额（元）</th><th class="numeric">与已结算差异（元）</th></tr></thead><tbody><tr v-for="row in wageData.rows" :key="row.key"><td class="cell-title">{{ row.label }}</td><td class="source-cell">{{ row.source }}</td><td class="numeric">{{ amt(row.amount) }}</td><td class="numeric">{{ amt(row.diff) }}</td></tr></tbody></table></div><div class="wage-mobile-list"><article v-for="row in wageData.rows" :key="row.key"><h4>{{ row.label }}</h4><p>{{ row.source }}</p><div><span>金额 <strong>{{ amt(row.amount) }}</strong></span><span>与已结算差异 <strong>{{ amt(row.diff) }}</strong></span></div></article></div></section><ul class="wage-notes"><li v-for="note in wageData.notes" :key="note">{{ note }}</li></ul></PageState>
  </template>

  <template v-else>
    <form class="filter-bar pnl-filter" @submit.prevent="pnlFilters.page = 1; loadPnl()"><div class="segmented mini-chip-bar"><button v-for="s in [['','全部'],['producing','生产中'],['done','已完成'],['cancelled','已取消']]" :key="s[0]" type="button" :class="{ active: pnlFilters.status === s[0] }" @click="pnlFilters.status = s[0]; pnlFilters.page = 1; loadPnl()">{{ s[1] }}</button></div><label class="field"><span>订单创建开始</span><input v-model="pnlFilters.date_from" type="date" /></label><label class="field"><span>订单创建截止</span><input v-model="pnlFilters.date_to" type="date" /></label><label class="search-box"><AppIcon name="search" :size="17" /><input v-model="pnlFilters.keyword" placeholder="订单、客户或款式" @input="searchPnl" /></label><button class="button secondary commercial-query">查询</button></form>
    <section class="card table-card pnl-list-card"><PageState :loading="pnlLoading" :error="pnlError" :empty="!rows(pnlData).length" empty-text="暂无损益数据，请先在订单成本中重算" @retry="loadPnl"><div class="table-wrap desktop-table"><table class="pnl-table"><thead><tr><th>订单 / 款式</th><th>订单状态</th><th class="numeric">理论收入</th><th class="numeric">总成本</th><th class="numeric">理论毛利 / 率</th><th class="numeric">应收</th><th class="numeric">已收</th><th class="numeric">未回款</th><th class="numeric">实收口径利润</th><th>计算时间</th></tr></thead><tbody><tr v-for="row in rows(pnlData)" :key="row.id"><td><span class="cell-title">{{ row.order_no }}</span><small class="cell-subtitle">{{ row.customer_name || '无客户' }} · {{ row.style_no }} {{ row.style_name }}</small></td><td><span class="badge" :class="row.order_status">{{ statusText(row.order_status) }}</span><small v-if="row.is_stale" class="badge producing stale-badge">待重算</small></td><td class="numeric">{{ amt(row.revenue) }}</td><td class="numeric">{{ amt(row.total_cost) }}</td><td class="numeric">{{ amt(row.gross_profit) }}<small class="cell-subtitle">{{ percent(row.gross_margin) }}</small></td><td class="numeric">{{ amt(row.ar_amount) }}</td><td class="numeric">{{ amt(row.ar_received) }}</td><td class="numeric">{{ amt(row.ar_gap) }}</td><td class="numeric cell-title" :class="{ 'negative-profit': Number(row.real_profit) < 0 }">{{ amt(row.real_profit) }}</td><td>{{ dateTime(row.computed_at) }}</td></tr></tbody></table></div><div class="mobile-cards cost-mobile-list"><article v-for="row in rows(pnlData)" :key="row.id" class="mobile-row cost-order-card"><div class="mobile-row-head"><div class="cost-card-title"><span class="mobile-row-title">{{ row.order_no }}</span><small class="cell-subtitle">{{ row.customer_name || '无客户' }} · {{ row.style_no }} {{ row.style_name }}</small></div><span class="badge" :class="row.is_stale ? 'producing' : row.order_status">{{ row.is_stale ? '待重算' : statusText(row.order_status) }}</span></div><div class="mobile-meta cost-metric-grid"><span><strong>{{ amt(row.revenue) }}</strong><small>理论收入</small></span><span><strong>{{ amt(row.total_cost) }}</strong><small>总成本</small></span><span><strong :class="{ 'negative-profit': Number(row.gross_profit) < 0 }">{{ amt(row.gross_profit) }}</strong><small>理论毛利 / {{ percent(row.gross_margin) }}</small></span><span><strong :class="{ 'negative-profit': Number(row.real_profit) < 0 }">{{ amt(row.real_profit) }}</strong><small>实收利润</small></span><span><strong>{{ amt(row.ar_amount) }}</strong><small>应收</small></span><span><strong>{{ amt(row.ar_received) }}</strong><small>已收</small></span><span><strong>{{ amt(row.ar_gap) }}</strong><small>未回款</small></span></div><p class="cost-computed-meta">计算时间 {{ dateTime(row.computed_at) }}</p></article></div></PageState><MobileLoadMore :loading="pnlLoadingMore" :error="pnlAppendError" :has-more="pnlHasMore" :count="rows(pnlData).length" @more="loadMorePnl" /><CommercialPagination class="desktop-pagination" :page="pnlFilters.page" :count="pnlData.count || 0" :disabled="pnlLoading" @change="pnlPage" /></section><p class="section-note">理论毛利 = 理论收入 − 总成本；实收口径利润 = 已核销收款 − 总成本。未挂订单的应收不计入本表。</p>
  </template>

  <CommercialModal v-if="overheadRow" title="录入制费 / 管理费" :subtitle="`${overheadRow.order_no} · 保存后须重算更新合计`" @close="overheadRow = null"><form @submit.prevent="saveOverhead"><label class="field"><span>制费 / 管理费总额（元）</span><input v-model="overheadValue" type="number" min="0" step="0.01" required /></label><div v-if="overheadError" class="form-error commercial-error">{{ overheadError }}</div><div class="form-actions"><button class="button secondary" type="button" @click="overheadRow = null">取消</button><button class="button primary" :disabled="overheadSaving">{{ overheadSaving ? '保存中…' : '保存制费' }}</button></div></form></CommercialModal>
</template>

<style scoped>
.commercial-actions{display:flex;gap:8px;align-items:center}.commercial-tabs{width:max-content;max-width:100%;margin-bottom:18px}.commercial-query{align-self:flex-end}.commercial-notice{margin-bottom:16px;color:var(--warning);background:#fff7e8}.commercial-error{margin-top:14px}.cost-table{min-width:1450px}.pnl-table{min-width:1250px}.cost-date-chips{display:none}.cost-filter-main,.cost-custom-range{display:contents}.cost-custom-range summary{display:none}.wage-mobile-list{display:none}.stale-badge{display:flex;width:max-content;margin-top:6px}.missing-link{color:var(--warning);cursor:pointer}.missing-text{color:var(--warning);cursor:pointer}.row-actions{display:flex;gap:6px;align-items:center;white-space:nowrap}.mobile-actions{margin-top:14px;justify-content:flex-end}.wage-stats{grid-template-columns:repeat(2,minmax(0,1fr))}.amount-value{font-size:28px}.wage-heading{padding:20px 20px 0}.source-cell{min-width:340px;max-width:580px;white-space:normal;line-height:1.6;color:var(--muted)}.wage-notes{color:var(--muted);font-size:12px;line-height:1.8;padding-left:20px}.negative-profit{color:var(--danger)}
@media(max-width:760px){
  .desktop-pagination{display:none}
  .commercial-tabs{width:100%;padding:4px;border-radius:12px}.commercial-tabs button{flex:1;padding-inline:8px;border-radius:999px}.commercial-tabs button.active{background:var(--orange-soft);color:var(--orange);box-shadow:none}.welcome>.commercial-actions{flex-wrap:wrap;justify-content:flex-end}.welcome>.commercial-actions .secondary:first-child{display:none}
  .cost-filter,.pnl-filter{display:block}.cost-date-chips{display:flex;gap:7px;overflow-x:auto;margin-bottom:10px;padding:1px;scrollbar-width:none}.cost-date-chips button{flex:1 0 auto;min-height:34px;padding:6px 13px;border:0;border-radius:999px;background:#fff;color:var(--text-secondary);font-size:12px;white-space:nowrap}.cost-date-chips button.active{background:var(--orange);color:#fff}.cost-filter-main{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:8px;padding:10px;background:#fff;border-radius:12px;box-shadow:0 2px 8px rgba(31,35,41,.06)}.cost-filter-main .search-box{width:100%;min-width:0;max-width:none}.cost-filter-main input{min-height:36px;border:0;border-radius:999px;background:#f5f6f8;padding:7px 8px 7px 34px;font-size:12px}.cost-filter-main .button{min-height:36px;padding:7px 16px;font-size:12px}
  .cost-custom-range{display:grid;grid-template-columns:1fr 1fr;gap:9px;margin-top:8px;padding:0 10px;background:#fff;border-radius:12px}.cost-custom-range summary{grid-column:1/-1;display:block;padding:10px 0;color:var(--text-secondary);font-size:12px;cursor:pointer}.cost-custom-range:not([open]){display:block}.cost-custom-range[open]{padding:10px}.cost-custom-range[open] summary{padding:0}.cost-custom-range .field{min-width:0}.cost-custom-range input{min-width:0;padding-inline:7px;font-size:11px}.cost-computed-field{grid-column:1/-1}.commercial-notice{margin:10px 0 12px;line-height:1.6}
  .cost-list-card.card,.pnl-list-card.card{padding:0;background:transparent;box-shadow:none;overflow:visible}.cost-list-card :deep(.page-state),.pnl-list-card :deep(.page-state){border-radius:12px;background:#fff;box-shadow:0 2px 8px rgba(31,35,41,.06)}.cost-mobile-list{gap:12px}.cost-order-card.mobile-row{padding:15px 12px;border:0;border-radius:12px;box-shadow:0 2px 8px rgba(31,35,41,.06)}.cost-card-title{min-width:0}.cost-card-badges{display:flex;flex:0 0 auto;flex-direction:column;align-items:flex-end;gap:5px}.cost-missing-badge{padding:3px 8px;border:0;border-radius:999px;background:#fff0ef;color:var(--danger);font-size:10px;white-space:nowrap}.cost-metric-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px 3px;margin-top:15px}.cost-metric-grid>span{min-width:0;text-align:center}.cost-metric-grid strong,.cost-metric-grid>span>small{display:block}.cost-metric-grid strong{font-size:12px;font-variant-numeric:tabular-nums;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.cost-metric-grid strong small{display:inline;font-size:10px;font-weight:400;color:var(--muted)}.cost-metric-grid>span>small{margin-top:4px;color:var(--muted);font-size:10px;line-height:1.35}.cost-computed-meta{margin:14px 0 0;color:var(--muted);font-size:10px;line-height:1.6}.mobile-actions{padding-top:11px;border-top:1px solid var(--line)}
  .pnl-filter>.mini-chip-bar{display:flex;width:100%;gap:8px;overflow-x:auto;margin-bottom:10px;padding:2px 1px;background:transparent;scrollbar-width:none}.pnl-filter>.mini-chip-bar button{flex:1 0 auto;border:1px solid var(--line);border-radius:999px;background:#fff;padding:7px 13px;font-size:12px}.pnl-filter>.mini-chip-bar button.active{border-color:var(--orange);background:var(--orange);color:#fff}.pnl-filter>.field{display:inline-flex;width:calc(50% - 5px);margin:0 5px 10px 0;min-width:0}.pnl-filter>.field:nth-of-type(2){margin-right:0}.pnl-filter>.field input{min-width:0;font-size:11px}.pnl-filter>.search-box{display:inline-flex;width:calc(100% - 70px);min-width:0;vertical-align:middle}.pnl-filter>.commercial-query{width:58px;min-height:40px;padding:8px}
  .wage-desktop-table{display:none}.wage-mobile-list{display:grid;gap:10px;padding:12px}.wage-mobile-list article{padding:13px;border-radius:10px;background:#f7f8fa}.wage-mobile-list h4{margin:0 0 6px;font-size:14px}.wage-mobile-list p{margin:0;color:var(--muted);font-size:11px;line-height:1.6}.wage-mobile-list article>div{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:10px;font-size:11px;color:var(--muted)}.wage-mobile-list strong{display:block;margin-top:3px;color:var(--ink);font-size:13px}.wage-stats{grid-template-columns:1fr 1fr}.source-cell{min-width:280px}.amount-value{font-size:22px}
}
@media(max-width:350px){.cost-order-card.mobile-row{padding-inline:9px}.cost-metric-grid{gap:12px 1px}.cost-metric-grid strong{font-size:11px}.pnl-filter>.field{width:100%;margin-right:0}.pnl-filter>.search-box{width:calc(100% - 64px)}}
</style>
