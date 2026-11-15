<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { api, rows } from '../api'
import { dateTime } from '../format'
import AppIcon from '../components/AppIcon.vue'
import PageState from '../components/PageState.vue'
import CommercialModal from '../components/CommercialModal.vue'
import CommercialPagination from '../components/CommercialPagination.vue'
import MobileLoadMore from '../components/MobileLoadMore.vue'
import FinanceCashView from '../components/FinanceCashView.vue'

const emit = defineEmits(['toast'])
const props = defineProps({ section: { type: String, default: '' } })
const financeSections = ['receivables', 'payables', 'payments', 'entries', 'cashflow', 'valuation']
const active = ref(financeSections.includes(props.section) ? props.section : 'receivables')
const loading = ref(true), loadingMore = ref(false), error = ref(''), appendError = ref(''), listData = ref({ count: 0 })
const hasMore = computed(() => rows(listData.value).length < Number(listData.value.count || 0))
const filters = reactive({ status: '', customer_id: '', keyword: '', date_from: '', date_to: '', page: 1 })
const customers = ref([]), orders = ref([]), materials = ref([])
const detail = ref(null), detailLoading = ref(false), detailError = ref(''), lines = ref([]), linesLoading = ref(false), linesError = ref('')
const createOpen = ref(false), createSaving = ref(false), createError = ref('')
const createForm = reactive({ customer: '', order: '', amount: '', remark: '' })
const receiptTarget = ref(null), receiptSaving = ref(false), receiptError = ref('')
const receiptForm = reactive({ amount: '', method: 'cash', received_at: '', handler: '', remark: '' })
const batchOpen = ref(false), batchSaving = ref(false), batchError = ref(''), batchResult = ref(null)
const batchForm = reactive({ customer: '', total: '', method: 'cash', received_at: '', handler: '', remark: '' })
const valuationLoading = ref(false), valuationError = ref(''), valuationLoaded = ref(false), valuation = ref({ rows: [], note: '' })
const valuationFilters = reactive({ material_id: '', date_to: '' })
const expanded = reactive({})
const actionBusy = ref(false)
const outstandingSort = ref(0)
let searchTimer, optionsPromise
const methods = [['cash', '现款'], ['monthly', '月结'], ['acceptance', '承兑'], ['other', '其他']]
const amt = (v) => v === null || v === undefined || v === '' ? '—' : String(v)
const statusClass = (s) => ({ unpaid: 'overdue', partial: 'producing', settled: 'settled' }[s] || '')
const displayReceivables = computed(() => {
  const list = [...rows(listData.value)]
  if (outstandingSort.value === 1) return list.sort((a, b) => Number(b.outstanding || 0) - Number(a.outstanding || 0))
  if (outstandingSort.value === 2) return list.sort((a, b) => Number(a.outstanding || 0) - Number(b.outstanding || 0))
  return list
})
function notify(message) { emit('toast', message) }
function toggleOutstandingSort() { outstandingSort.value = (outstandingSort.value + 1) % 3 }
function exportVisibleReceivables() {
  const data = displayReceivables.value
  if (!data.length) { notify('当前没有可导出的应收行'); return }
  const header = ['应收单号', '客户', '订单号', '应收金额', '已收金额', '未收余额', '状态', '建档时间']
  const body = data.map((row) => [row.receivable_no, row.customer_name || '', row.order_no || '', row.amount, row.received_amount, row.outstanding, row.status_display || row.status, row.created_at])
  const csv = [header, ...body].map((line) => line.map((cell) => `"${String(cell ?? '').replace(/"/g, '""')}"`).join(',')).join('\r\n')
  const blob = new Blob([`﻿${csv}`], { type: 'text/csv;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = `应收账款-${new Date().toISOString().slice(0, 10)}.csv`
  link.click()
  URL.revokeObjectURL(url)
}

async function loadPage(append = false) {
  if (!append && window.matchMedia('(max-width: 760px)').matches) filters.page = 1
  const requestedPage = filters.page
  if (append) { loadingMore.value = true; appendError.value = '' }
  else { loading.value = true; error.value = ''; appendError.value = '' }
  try {
    const result = await api('/finance/receivables', { params: { ...filters, page_size: 20 } })
    listData.value = append ? { ...result, results: [...rows(listData.value), ...rows(result)] } : result
  } catch (e) {
    if (append) { appendError.value = e.message; filters.page = Math.max(1, requestedPage - 1) }
    else error.value = e.message
  } finally { loading.value = false; loadingMore.value = false }
}
function load() { return loadPage(false) }
function loadMore() {
  if (!hasMore.value || loadingMore.value || loading.value) return
  filters.page += 1
  return loadPage(true)
}
function search() { clearTimeout(searchTimer); searchTimer = setTimeout(() => { filters.page = 1; load() }, 300) }
function changePage(page) { filters.page = page; load() }
function loadOptions() {
  if (!optionsPromise) {
    optionsPromise = Promise.allSettled([
      api('/customers', { params: { page: 1, page_size: 100 } }),
      api('/orders', { params: { page: 1, page_size: 100 } }),
      api('/tech/materials', { params: { page: 1, page_size: 100 } }),
    ]).then(([c, o, m]) => {
      if (c.status === 'fulfilled') customers.value = rows(c.value)
      if (o.status === 'fulfilled') orders.value = rows(o.value)
      if (m.status === 'fulfilled') materials.value = rows(m.value)
    })
  }
  return optionsPromise
}
function switchTab(tab) { active.value = tab; if (tab === 'valuation' && !valuationLoaded.value) loadValuation() }

async function openDetail(item) {
  detail.value = item; detailLoading.value = true; detailError.value = ''; lines.value = []; linesError.value = ''
  try {
    detail.value = await api(`/finance/receivables/${item.id}`)
    await loadLines(item.id)
  } catch (e) { detailError.value = e.message }
  finally { detailLoading.value = false }
}
async function loadLines(id) {
  linesLoading.value = true; linesError.value = ''
  try { lines.value = await api(`/finance/receivables/${id}/receipt-lines`) }
  catch (e) { linesError.value = e.message }
  finally { linesLoading.value = false }
}
function closeDetail() { detail.value = null; receiptTarget.value = null }

async function openCreate() {
  await loadOptions()
  Object.assign(createForm, { customer: '', order: '', amount: '', remark: '' })
  createError.value = ''; createOpen.value = true
}
function createOrderChanged() {
  const order = orders.value.find((o) => o.id === Number(createForm.order))
  if (order?.customer) createForm.customer = order.customer
}
async function saveCreate() {
  if (!createForm.customer) { createError.value = '请选择客户'; return }
  const amount = Number(createForm.amount)
  if (createForm.amount === '' || !Number.isFinite(amount) || amount <= 0) { createError.value = '应收金额必须大于 0'; return }
  createSaving.value = true; createError.value = ''
  const body = { customer: Number(createForm.customer), amount: createForm.amount, remark: createForm.remark.trim() }
  if (createForm.order) body.order = Number(createForm.order)
  try {
    const saved = await api('/finance/receivables', { method: 'POST', body })
    createOpen.value = false; await load(); notify(`应收 ${saved.receivable_no} 已建立`); await openDetail(saved)
  } catch (e) { createError.value = e.message }
  finally { createSaving.value = false }
}
function openReceipt(rec) {
  if (rec.status === 'settled') { notify('该应收已结清，如需调整请先撤销核销'); return }
  receiptTarget.value = rec
  Object.assign(receiptForm, { amount: rec.outstanding || '', method: 'cash', received_at: '', handler: '', remark: '' })
  receiptError.value = ''
}
async function saveReceipt() {
  const rec = receiptTarget.value
  const amount = Number(receiptForm.amount)
  if (!rec) return
  if (receiptForm.amount === '' || !Number.isFinite(amount) || amount <= 0) { receiptError.value = '核销金额必须大于 0'; return }
  if (amount > Number(rec.outstanding)) { receiptError.value = `本笔超过未收余额 ${amt(rec.outstanding)}，多收须拦截`; return }
  receiptSaving.value = true; receiptError.value = ''
  try {
    const result = await api(`/finance/receivables/${rec.id}/receipts`, { method: 'POST', body: {
      amount: receiptForm.amount, method: receiptForm.method, received_at: receiptForm.received_at || null,
      handler: receiptForm.handler.trim(), remark: receiptForm.remark.trim(),
    } })
    receiptTarget.value = null; notify('收款核销成功')
    await Promise.all([load(), openDetail(result.receivable)])
  } catch (e) { receiptError.value = e.message }
  finally { receiptSaving.value = false }
}
async function openBatch() {
  await loadOptions()
  Object.assign(batchForm, { customer: filters.customer_id || '', total: '', method: 'cash', received_at: '', handler: '', remark: '' })
  batchError.value = ''; batchResult.value = null; batchOpen.value = true
}
async function saveBatch() {
  if (!batchForm.customer) { batchError.value = '请选择客户'; return }
  const total = Number(batchForm.total)
  if (batchForm.total === '' || !Number.isFinite(total) || total <= 0) { batchError.value = '收款总额必须大于 0'; return }
  const customer = customers.value.find((c) => c.id === Number(batchForm.customer))
  if (!window.confirm(`确认收取 ${customer?.name || '该客户'} ${batchForm.total} 元？系统将按应收创建时间从最早开始核销，溢收款会整笔拒绝。`)) return
  batchSaving.value = true; batchError.value = ''; batchResult.value = null
  try {
    batchResult.value = await api(`/finance/customers/${batchForm.customer}/receipts-batch`, { method: 'POST', body: {
      total: batchForm.total, method: batchForm.method, received_at: batchForm.received_at || null,
      handler: batchForm.handler.trim(), remark: batchForm.remark.trim(),
    } })
    await load(); notify('批量核销完成')
  } catch (e) { batchError.value = e.message }
  finally { batchSaving.value = false }
}
async function revokeReceipt() {
  const rec = detail.value
  if (!rec || actionBusy.value) return
  if (Number(rec.received_amount) <= 0) { notify('无可撤销的核销记录'); return }
  if (!window.confirm(`撤销 ${rec.receivable_no} 最近一条核销？已收金额将反向回退并重新计算状态。`)) return
  actionBusy.value = true; detailError.value = ''
  try {
    const result = await api(`/finance/receivables/${rec.id}/receipts/revoke`, { method: 'POST', body: {} })
    notify('最近一条核销已撤销'); await Promise.all([load(), openDetail(result.receivable)])
  } catch (e) { detailError.value = e.message; notify(e.message) }
  finally { actionBusy.value = false }
}
async function voidReceivable() {
  const rec = detail.value
  if (!rec || actionBusy.value) return
  if (!window.confirm(`作废应收 ${rec.receivable_no}？作废后从列表下架；有活动核销须先全部撤销。`)) return
  actionBusy.value = true; detailError.value = ''
  try { await api(`/finance/receivables/${rec.id}/void`, { method: 'POST', body: {} }); closeDetail(); await load(); notify('应收已作废') }
  catch (e) { detailError.value = e.message; notify(e.message) }
  finally { actionBusy.value = false }
}
async function loadValuation() {
  valuationLoading.value = true; valuationError.value = ''
  try { valuation.value = await api('/finance/stock-valuation', { params: valuationFilters }); valuationLoaded.value = true }
  catch (e) { valuationError.value = e.message }
  finally { valuationLoading.value = false }
}
function toggleGroup(row) { const key = row.material_id ?? 'unassigned'; expanded[key] = !expanded[key] }
function isExpanded(row) { return !!expanded[row.material_id ?? 'unassigned'] }

onMounted(() => { if (active.value === 'receivables') { load(); loadOptions() } else switchTab(active.value) })
</script>

<template>
  <div class="welcome"><div><h2>财务管理</h2><p>应收核销、采购应付、实际资金收支与经营现金流。</p></div><div v-if="['receivables', 'valuation'].includes(active)" class="commercial-actions"><button class="button secondary" @click="active === 'receivables' ? load() : loadValuation()"><AppIcon name="refresh" :size="16" />刷新</button><button v-if="active === 'receivables'" class="button secondary" @click="openBatch">客户批量收款</button><button v-if="active === 'receivables'" class="button secondary" @click="toggleOutstandingSort">未收排序{{ ['（默认）','（高到低）','（低到高）'][outstandingSort] }}</button><button v-if="active === 'receivables'" class="button secondary" @click="exportVisibleReceivables">导出当前</button><button v-if="active === 'receivables'" class="button primary" @click="openCreate">新建应收</button></div></div>
  <div class="segmented commercial-tabs" role="tablist" aria-label="财务业务"><button v-for="tab in [['receivables', '应收账款'], ['payables', '采购应付'], ['payments', '供应商付款'], ['entries', '费用 / 收入'], ['cashflow', '经营现金流'], ['valuation', '库存估值']]" :key="tab[0]" type="button" role="tab" :aria-selected="active === tab[0]" :class="{ active: active === tab[0] }" @click="switchTab(tab[0])">{{ tab[1] }}</button></div>

  <template v-if="active === 'receivables'">
    <form class="filter-bar finance-filter" @submit.prevent="filters.page = 1; load()"><div class="segmented mini-chip-bar"><button v-for="s in [['','全部'],['unpaid','未收'],['partial','部分收款'],['settled','已结清']]" :key="s[0]" type="button" :class="{ active: filters.status === s[0] }" @click="filters.status = s[0]; filters.page = 1; load()">{{ s[1] }}</button></div><div class="finance-filter-main"><select v-model="filters.customer_id" aria-label="筛选客户" @change="filters.page = 1; load()"><option value="">全部客户</option><option v-for="c in customers" :key="c.id" :value="c.id">{{ c.name }}</option></select><label class="search-box"><AppIcon name="search" :size="17" /><input v-model="filters.keyword" placeholder="应收单号/客户/订单号" @input="search" /></label><button class="button primary commercial-query">查询</button></div><details class="finance-advanced-filter" open><summary>按建档日期筛选</summary><label class="field commercial-date"><span>建档开始</span><input v-model="filters.date_from" type="date" /></label><label class="field commercial-date"><span>建档截止</span><input v-model="filters.date_to" type="date" /></label></details></form>
    <section class="card table-card finance-list-card"><PageState :loading="loading" :error="error" :empty="!rows(listData).length" empty-text="暂无应收账，出货确认后自动建单或点「新建应收」" @retry="load"><div class="table-wrap desktop-table"><table><thead><tr><th>应收单 / 订单</th><th>客户</th><th class="numeric">应收金额（元）</th><th class="numeric">已收（元）</th><th class="numeric">未收（元）</th><th>状态</th><th>建档时间</th><th></th></tr></thead><tbody><tr v-for="rec in displayReceivables" :key="rec.id" class="clickable" @click="openDetail(rec)"><td><span class="cell-title">{{ rec.receivable_no }}</span><small class="cell-subtitle">{{ rec.order_no || '未关联订单' }}<template v-if="rec.shipment_id"> · 出货 #{{ rec.shipment_id }}</template></small></td><td>{{ rec.customer_name || '—' }}</td><td class="numeric">{{ amt(rec.amount) }}</td><td class="numeric">{{ amt(rec.received_amount) }}</td><td class="numeric cell-title">{{ amt(rec.outstanding) }}</td><td><span class="badge" :class="statusClass(rec.status)">{{ rec.status_display }}</span></td><td>{{ dateTime(rec.created_at) }}</td><td><AppIcon name="chevron" :size="16" /></td></tr></tbody></table></div><div class="mobile-cards finance-mobile-list"><button v-for="rec in displayReceivables" :key="rec.id" class="mobile-row finance-receivable-card" @click="openDetail(rec)"><div class="mobile-row-head"><div class="finance-card-title"><span class="mobile-row-title">{{ rec.receivable_no }}</span><small class="cell-subtitle">{{ rec.customer_name || '无客户' }}<template v-if="rec.order_no"> · {{ rec.order_no }}</template></small></div><span class="badge" :class="statusClass(rec.status)">{{ rec.status_display }}</span></div><div class="mobile-meta finance-amount-grid"><span><strong>{{ amt(rec.amount) }}</strong><small>应收</small></span><span><strong>{{ amt(rec.received_amount) }}</strong><small>已收</small></span><span><strong :class="{ 'finance-outstanding': rec.status !== 'settled' }">{{ amt(rec.outstanding) }}</strong><small>剩余</small></span></div><p v-if="rec.remark" class="finance-card-remark">备注：{{ rec.remark }}</p><small class="finance-card-date">建档 {{ dateTime(rec.created_at) }}</small></button></div></PageState><MobileLoadMore :loading="loadingMore" :error="appendError" :has-more="hasMore" :count="rows(listData).length" @more="loadMore" /><CommercialPagination class="desktop-pagination" :page="filters.page" :count="listData.count || 0" :disabled="loading" @change="changePage" /></section>
  </template>

  <template v-else-if="active === 'valuation'">
    <form class="filter-bar" @submit.prevent="loadValuation"><select v-model="valuationFilters.material_id" aria-label="估值物料"><option value="">全部物料</option><option v-for="m in materials" :key="m.id" :value="m.id">{{ m.code }} · {{ m.name }}</option></select><label class="field"><span>截止日期</span><input v-model="valuationFilters.date_to" type="date" /></label><button class="button primary commercial-query">查询估值</button></form>
    <p class="section-note valuation-note">{{ valuation.note || '财务对账采用移动加权口径；车间批次价口径并存。缺价入库不进额不进量。' }}</p>
    <PageState :loading="valuationLoading" :error="valuationError" :empty="!valuation.rows?.length" empty-text="当前条件下暂无有效库存流水" @retry="loadValuation"><section class="valuation-list"><article v-for="row in valuation.rows" :key="row.material_id ?? 'unassigned'" class="card valuation-card"><button class="valuation-head" type="button" @click="toggleGroup(row)"><div><h3>{{ row.material_id ? `${row.material_code} · ${row.material_name}` : '未归集物料' }}</h3><p>{{ row.unit || '组内存在混合单位，以批次明细为准' }}<span v-if="row.missing_price_rows" class="badge overdue valuation-badge">缺价 {{ row.missing_price_rows }} 行</span></p></div><AppIcon name="chevron" :size="16" /></button><div class="detail-grid"><div class="detail-item"><small>移动加权均价</small><strong>{{ amt(row.wavg_price) }}</strong></div><div class="detail-item"><small>结存数量</small><strong>{{ amt(row.balance_qty) }}</strong></div><div class="detail-item"><small>结存金额（元）</small><strong>{{ amt(row.balance_amt) }}</strong></div><div class="detail-item"><small>累计入库量 / 金额</small><strong>{{ amt(row.in_qty) }} / {{ amt(row.in_amt) }}</strong></div><div class="detail-item"><small>净领用量</small><strong>{{ amt(row.issued_qty) }}</strong></div></div><div v-if="isExpanded(row)" class="table-wrap valuation-lot-table"><table><thead><tr><th>批次</th><th>面料 / 颜色</th><th class="numeric">入库量</th><th class="numeric">净领用</th><th class="numeric">结存量</th><th class="numeric">加权价</th><th class="numeric">结存额</th><th class="numeric">缺价行</th></tr></thead><tbody><tr v-for="lot in row.lots" :key="lot.lot_id"><td class="cell-title">{{ lot.lot_no || `#${lot.lot_id}` }}</td><td>{{ lot.fabric_name || '—' }}<small class="cell-subtitle">{{ lot.color || '无颜色' }} · {{ lot.unit || '无单位' }}</small></td><td class="numeric">{{ amt(lot.in_qty) }}</td><td class="numeric">{{ amt(lot.issued_qty) }}</td><td class="numeric">{{ amt(lot.balance_qty) }}</td><td class="numeric">{{ amt(lot.wavg_price) }}</td><td class="numeric">{{ amt(lot.balance_amt) }}</td><td class="numeric">{{ lot.missing_price_rows || 0 }}</td></tr></tbody></table></div><div v-if="isExpanded(row)" class="valuation-lot-mobile"><article v-for="lot in row.lots" :key="lot.lot_id"><div><strong>{{ lot.lot_no || `#${lot.lot_id}` }}</strong><span v-if="lot.missing_price_rows">缺价 {{ lot.missing_price_rows }} 行</span></div><p>{{ lot.fabric_name || '—' }} · {{ lot.color || '无颜色' }} · {{ lot.unit || '无单位' }}</p><section><span>入库量<strong>{{ amt(lot.in_qty) }}</strong></span><span>净领用<strong>{{ amt(lot.issued_qty) }}</strong></span><span>结存量<strong>{{ amt(lot.balance_qty) }}</strong></span><span>加权价<strong>{{ amt(lot.wavg_price) }}</strong></span><span>结存额<strong>{{ amt(lot.balance_amt) }}</strong></span></section></article></div></article></section></PageState>
  </template>

  <FinanceCashView v-else :key="active" :section="active" @toast="notify" />

  <CommercialModal v-if="detail" wide :title="detail.receivable_no" :subtitle="`${detail.customer_name || '无客户'} · ${detail.order_no || '未关联订单'}`" @close="closeDetail"><PageState :loading="detailLoading" :error="detailError && !detail.amount ? detailError : ''" @retry="openDetail(detail)"><div v-if="detailError" class="form-error commercial-error">{{ detailError }}</div><div class="detail-grid"><div class="detail-item"><small>应收金额（元）</small><strong>{{ amt(detail.amount) }}</strong></div><div class="detail-item"><small>已收金额（元）</small><strong>{{ amt(detail.received_amount) }}</strong></div><div class="detail-item"><small>未收余额（元）</small><strong>{{ amt(detail.outstanding) }}</strong></div><div class="detail-item"><small>状态</small><strong>{{ detail.status_display }}</strong></div><div class="detail-item"><small>关联出货</small><strong>{{ detail.shipment_id || '—' }}</strong></div><div class="detail-item"><small>建档时间</small><strong>{{ dateTime(detail.created_at) }}</strong></div></div><p v-if="detail.remark" class="section-note">备注：{{ detail.remark }}</p><div class="commercial-actions commercial-section"><button class="button primary small" :disabled="detail.status === 'settled'" @click="openReceipt(detail)">收款核销</button><button class="button secondary small" :disabled="actionBusy || Number(detail.received_amount) <= 0" @click="revokeReceipt">撤销最近核销</button><button class="button danger small" :disabled="actionBusy" @click="voidReceivable">作废应收</button></div><section class="commercial-section"><div class="card-header"><div><h3>收款时间线</h3><p>最近一笔在前；已撤销流水不在此显示</p></div></div><div v-if="linesLoading" class="page-state"><span class="spinner" />正在读取收款流水…</div><div v-else-if="linesError" class="form-error">{{ linesError }} <button class="button ghost small" @click="loadLines(detail.id)">重试</button></div><div v-else class="table-wrap finance-receipt-table"><table><thead><tr><th>收款时间</th><th class="numeric">金额（元）</th><th>方式</th><th>经手人</th><th>备注</th></tr></thead><tbody><tr v-for="line in lines" :key="line.id"><td>{{ dateTime(line.received_at) }}</td><td class="numeric">{{ amt(line.amount) }}</td><td>{{ line.method_display || line.method }}</td><td>{{ line.handler || '—' }}</td><td>{{ line.remark || '—' }}</td></tr><tr v-if="!lines.length"><td colspan="5" class="page-state">暂无收款核销流水</td></tr></tbody></table></div><div class="finance-receipt-mobile"><article v-for="line in lines" :key="line.id"><div><strong>{{ amt(line.amount) }} 元</strong><span>{{ line.method_display || line.method }}</span></div><p>{{ dateTime(line.received_at) }} · 经手人 {{ line.handler || '—' }}</p><small v-if="line.remark">备注：{{ line.remark }}</small></article><p v-if="!lines.length" class="page-state">暂无收款核销流水</p></div></section></PageState></CommercialModal>

  <CommercialModal v-if="createOpen" title="新建应收" subtitle="单号由后端自动生成；订单可不关联" @close="createOpen = false"><form @submit.prevent="saveCreate"><div class="form-grid"><label class="field"><span>客户</span><select v-model="createForm.customer" required><option value="">请选择当前公司客户</option><option v-for="c in customers" :key="c.id" :value="c.id">{{ c.name }}</option></select></label><label class="field"><span>关联订单（可选）</span><select v-model="createForm.order" @change="createOrderChanged"><option value="">不关联订单</option><option v-for="o in orders" :key="o.id" :value="o.id">{{ o.order_no }} · {{ o.customer_name || '无客户' }}</option></select></label><label class="field full"><span>应收金额（元）</span><input v-model="createForm.amount" type="number" min="0.01" step="0.01" required /></label><label class="field full"><span>备注</span><textarea v-model="createForm.remark" rows="2" maxlength="255" /></label></div><div v-if="createError" class="form-error commercial-error">{{ createError }}</div><div class="form-actions"><button class="button secondary" type="button" @click="createOpen = false">取消</button><button class="button primary" :disabled="createSaving">{{ createSaving ? '保存中…' : '建立应收' }}</button></div></form></CommercialModal>

  <CommercialModal v-if="receiptTarget" title="收款核销" :subtitle="`${receiptTarget.receivable_no} · 未收 ${amt(receiptTarget.outstanding)} 元`" @close="receiptTarget = null"><form @submit.prevent="saveReceipt"><div class="form-grid"><label class="field"><span>本次核销金额（元）</span><input v-model="receiptForm.amount" type="number" min="0.01" step="0.01" required /></label><label class="field"><span>收款方式</span><select v-model="receiptForm.method"><option v-for="m in methods" :key="m[0]" :value="m[0]">{{ m[1] }}</option></select></label><label class="field"><span>收款时间（可选）</span><input v-model="receiptForm.received_at" type="datetime-local" /></label><label class="field"><span>经手人</span><input v-model="receiptForm.handler" maxlength="32" /></label><label class="field full"><span>备注</span><textarea v-model="receiptForm.remark" rows="2" maxlength="255" /></label></div><div v-if="receiptError" class="form-error commercial-error">{{ receiptError }}</div><div class="form-actions"><button class="button secondary" type="button" @click="receiptTarget = null">取消</button><button class="button primary" :disabled="receiptSaving">{{ receiptSaving ? '核销中…' : '确认核销' }}</button></div></form></CommercialModal>

  <CommercialModal v-if="batchOpen" wide title="客户批量收款" subtitle="一笔到账按应收创建时间 FIFO 分配；超额会整笔拒绝" @close="batchOpen = false"><form @submit.prevent="saveBatch"><div class="form-grid"><label class="field"><span>客户</span><select v-model="batchForm.customer" required><option value="">请选择当前公司客户</option><option v-for="c in customers" :key="c.id" :value="c.id">{{ c.name }}</option></select></label><label class="field"><span>收款总额（元）</span><input v-model="batchForm.total" type="number" min="0.01" step="0.01" required /></label><label class="field"><span>收款方式</span><select v-model="batchForm.method"><option v-for="m in methods" :key="m[0]" :value="m[0]">{{ m[1] }}</option></select></label><label class="field"><span>收款时间（可选）</span><input v-model="batchForm.received_at" type="datetime-local" /></label><label class="field"><span>经手人</span><input v-model="batchForm.handler" maxlength="32" /></label><label class="field"><span>备注</span><input v-model="batchForm.remark" maxlength="255" /></label></div><div v-if="batchError" class="form-error commercial-error">{{ batchError }}</div><div v-if="batchResult" class="batch-result"><h3>核销完成 · 合计 {{ amt(batchResult.total_allocated) }} 元</h3><div class="table-wrap"><table><thead><tr><th>应收单</th><th class="numeric">本次分配（元）</th></tr></thead><tbody><tr v-for="a in batchResult.allocated" :key="a.receivable_id"><td>{{ a.receivable_no }}</td><td class="numeric">{{ amt(a.amount) }}</td></tr></tbody></table></div><p class="section-note">未分配余额：{{ amt(batchResult.leftover) }} 元</p></div><div class="form-actions"><button class="button secondary" type="button" @click="batchOpen = false">关闭</button><button v-if="!batchResult" class="button primary" :disabled="batchSaving">{{ batchSaving ? '核销中…' : '确认批量核销' }}</button></div></form></CommercialModal>
</template>

<style scoped>
.commercial-actions{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.commercial-tabs{width:max-content;max-width:100%;margin-bottom:18px;overflow-x:auto;scrollbar-width:thin}.commercial-section{margin-top:22px}.commercial-error{margin-top:14px}.commercial-date{min-width:145px}.commercial-query{align-self:flex-end}.finance-filter-main{display:contents}.finance-advanced-filter{display:contents}.finance-advanced-filter summary{display:none}.finance-receipt-mobile,.valuation-lot-mobile{display:none}.valuation-note{margin:0 0 16px;line-height:1.7}.valuation-list{display:flex;flex-direction:column;gap:16px}.valuation-card{padding:0;overflow:hidden}.valuation-head{width:100%;border:0;background:#fff;text-align:left;padding:20px 22px 0;display:flex;align-items:flex-start;justify-content:space-between;color:inherit}.valuation-head h3{font-size:16px;margin:0 0 6px}.valuation-head p{margin:0;color:var(--muted);font-size:12px}.valuation-card>.detail-grid{padding:18px 22px 0;grid-template-columns:repeat(5,minmax(0,1fr));margin-bottom:18px}.valuation-card>.table-wrap{border-top:1px solid var(--line)}.valuation-badge{margin-left:10px}.batch-result{margin-top:20px}.batch-result h3{font-size:15px}
@media(max-width:1100px){.valuation-card>.detail-grid{grid-template-columns:repeat(3,minmax(0,1fr))}}
@media(max-width:760px){
  .desktop-pagination{display:none}
  .welcome>.commercial-actions{justify-content:flex-end}.welcome>.commercial-actions .secondary:first-child{display:none}.commercial-tabs{width:100%;padding:4px;border-radius:12px}.commercial-tabs button{flex:0 0 auto;border-radius:999px;white-space:nowrap}.commercial-tabs button.active{background:var(--orange-soft);color:var(--orange);box-shadow:none}
  .finance-filter{display:block;margin-bottom:12px}.mini-chip-bar{width:100%;max-width:100%;display:flex;gap:8px;overflow-x:auto;padding:2px 1px 10px;background:transparent;border-radius:0;scrollbar-width:none}.mini-chip-bar::-webkit-scrollbar{display:none}.mini-chip-bar button{flex:0 0 auto;padding:7px 16px;border:1px solid var(--line);border-radius:999px;background:#fff;color:var(--text-secondary);font-size:12px}.mini-chip-bar button.active{border-color:var(--orange);background:var(--orange);color:#fff;box-shadow:none}
  .finance-filter-main{display:grid;grid-template-columns:auto minmax(0,1fr) auto;gap:8px;padding:10px;background:#fff;border-radius:12px;box-shadow:0 2px 8px rgba(31,35,41,.06)}.finance-filter-main select{width:92px;min-width:0;min-height:36px;border:0;border-radius:999px;background:#f5f6f8;padding:7px 9px;font-size:11px}.finance-filter-main .search-box{min-width:0;width:100%;max-width:none}.finance-filter-main .search-box input{min-height:36px;border:0;border-radius:999px;background:#f5f6f8;padding:7px 8px 7px 32px;font-size:12px}.finance-filter-main .search-box svg{left:10px;width:14px}.finance-filter-main .commercial-query{min-height:36px;padding:7px 13px;font-size:12px}
  .finance-advanced-filter{display:block;margin-top:8px;padding:0 10px;background:#fff;border-radius:12px}.finance-advanced-filter summary{display:block;padding:10px 0;color:var(--text-secondary);font-size:12px;cursor:pointer}.finance-advanced-filter[open]{display:grid;grid-template-columns:1fr 1fr;gap:8px;padding:10px}.finance-advanced-filter[open] summary{grid-column:1/-1;padding:0}.finance-advanced-filter .commercial-date{min-width:0}.finance-advanced-filter input{min-width:0;font-size:11px}
  .finance-list-card.card{padding:0;background:transparent;box-shadow:none;overflow:visible}.finance-list-card>.page-state,.finance-list-card :deep(.page-state){background:#fff;border-radius:12px;box-shadow:0 2px 8px rgba(31,35,41,.06)}.finance-mobile-list{gap:12px}.finance-receivable-card.mobile-row{padding:16px;border:0;border-radius:12px;box-shadow:0 2px 8px rgba(31,35,41,.06)}.finance-card-title{min-width:0}.finance-card-title .cell-subtitle{overflow-wrap:anywhere}.finance-amount-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));margin-top:14px}.finance-amount-grid span{min-width:0;text-align:center}.finance-amount-grid strong,.finance-amount-grid small{display:block}.finance-amount-grid strong{font-size:14px;font-variant-numeric:tabular-nums;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.finance-amount-grid small{margin-top:4px;color:var(--muted);font-size:11px}.finance-outstanding{color:var(--danger)}.finance-card-remark{margin:13px 0 0;color:var(--text-secondary);font-size:12px;line-height:1.6}.finance-card-date{display:block;margin-top:10px;color:var(--muted);font-size:10px}
  .finance-receipt-table{display:none}.finance-receipt-mobile{display:grid;gap:10px}.finance-receipt-mobile article{padding:12px;border-radius:10px;background:#f7f8fa}.finance-receipt-mobile article>div{display:flex;align-items:center;justify-content:space-between;gap:10px}.finance-receipt-mobile article>div>span{padding:3px 9px;border-radius:999px;background:var(--orange-soft);color:var(--orange);font-size:11px}.finance-receipt-mobile p{margin:7px 0 0;color:var(--text-secondary);font-size:12px}.finance-receipt-mobile small{display:block;margin-top:5px;color:var(--muted);font-size:11px}.finance-receipt-mobile>.page-state{min-height:100px}
  .commercial-query{align-self:end}.valuation-card>.detail-grid{padding:16px 16px 0;grid-template-columns:1fr 1fr}.valuation-head{padding:17px 16px 0}.valuation-card .valuation-lot-table{display:none}.batch-result .table-wrap table{min-width:0!important}.valuation-lot-mobile{display:grid;gap:9px;padding:0 14px 14px}.valuation-lot-mobile article{padding:12px;border-radius:10px;background:#f7f8fa}.valuation-lot-mobile article>div{display:flex;justify-content:space-between;gap:8px}.valuation-lot-mobile article>div span{color:var(--danger);font-size:10px}.valuation-lot-mobile article>p{margin:5px 0 10px;color:var(--muted);font-size:11px}.valuation-lot-mobile article>section{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px 5px}.valuation-lot-mobile article>section span{color:var(--muted);font-size:10px}.valuation-lot-mobile article>section strong{display:block;margin-top:3px;color:var(--ink);font-size:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
}
@media(max-width:350px){.finance-filter-main{grid-template-columns:82px minmax(0,1fr) auto;gap:6px;padding:8px}.finance-filter-main select{width:82px}.finance-filter-main .commercial-query{padding-inline:10px}.mini-chip-bar button{padding-inline:13px}.finance-receivable-card.mobile-row{padding:14px}}
</style>
