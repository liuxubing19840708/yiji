<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { api, rows } from '../api'
import { fullDateTime, money, monthRange } from '../format'
import AppIcon from './AppIcon.vue'
import PageState from './PageState.vue'
import CommercialModal from './CommercialModal.vue'
import CommercialPagination from './CommercialPagination.vue'
import MobileLoadMore from './MobileLoadMore.vue'

const props = defineProps({ section: { type: String, default: 'payables' } })
const emit = defineEmits(['toast'])
const PAGE_SIZE = 20
const section = computed(() => ['payables', 'payments', 'entries', 'cashflow'].includes(props.section) ? props.section : 'payables')
const sectionTitle = computed(() => ({ payables: '采购应付', payments: '供应商付款', entries: '费用支出与其他收入', cashflow: '经营现金流' }[section.value]))
const paymentMethods = [['bank', '银行转账'], ['cash', '现金'], ['acceptance', '承兑'], ['other', '其他']]
const methodText = Object.fromEntries(paymentMethods)
const payableStatuses = [['', '全部状态'], ['unpaid', '未付'], ['partial', '部分付款'], ['settled', '已付清']]
const activeStatuses = [['', '全部状态'], ['active', '有效'], ['voided', '已作废']]

function localDate() {
  const date = new Date()
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`
}
function localDateTime() { return `${localDate()}T${String(new Date().getHours()).padStart(2, '0')}:${String(new Date().getMinutes()).padStart(2, '0')}` }
function amount(value) { return value === null || value === undefined || value === '' ? '—' : money(value) }
function purchaseLabel(id) {
  if (!id) return '未关联采购'
  return purchases.value.find((item) => item.id === Number(id))?.order_no || `采购单 #${id}`
}
function statusClass(status) {
  return { unpaid: 'overdue', partial: 'producing', settled: 'settled', active: 'done', voided: 'cancelled' }[status] || ''
}
function positiveAmount(value) { return value !== '' && Number.isFinite(Number(value)) && Number(value) > 0 }

const states = reactive({
  payables: { data: { count: 0, results: [] }, loading: false, loadingMore: false, error: '', appendError: '', filters: { status: '', supplier: '', keyword: '', date_from: '', date_to: '', page: 1 } },
  payments: { data: { count: 0, results: [] }, loading: false, loadingMore: false, error: '', appendError: '', filters: { status: '', date_from: '', date_to: '', page: 1 } },
  entries: { data: { count: 0, results: [] }, loading: false, loadingMore: false, error: '', appendError: '', filters: { direction: '', category: '', status: '', date_from: '', date_to: '', page: 1 } },
})
const listPaths = { payables: '/finance/payables', payments: '/finance/supplier-payments', entries: '/finance/cash-entries' }
const activeState = computed(() => states[section.value])
const hasMore = computed(() => section.value !== 'cashflow' && rows(activeState.value.data).length < Number(activeState.value.data.count || 0))

async function loadList(kind = section.value, append = false) {
  const state = states[kind]
  if (!state || (append ? state.loadingMore : state.loading)) return
  if (!append && window.matchMedia('(max-width: 760px)').matches) state.filters.page = 1
  const requestedPage = state.filters.page
  if (append) { state.loadingMore = true; state.appendError = '' }
  else { state.loading = true; state.error = ''; state.appendError = '' }
  try {
    const result = await api(listPaths[kind], { params: { ...state.filters, page_size: PAGE_SIZE } })
    state.data = append ? { ...result, results: [...rows(state.data), ...rows(result)] } : result
  } catch (error) {
    if (append) { state.appendError = error.message; state.filters.page = Math.max(1, requestedPage - 1) }
    else state.error = error.message
  } finally { state.loading = false; state.loadingMore = false }
}
function queryList(kind) { states[kind].filters.page = 1; return loadList(kind) }
function changePage(page) { activeState.value.filters.page = page; loadList() }
function loadMore() {
  if (!hasMore.value || activeState.value.loading || activeState.value.loadingMore) return
  activeState.value.filters.page += 1
  loadList(section.value, true)
}
function refresh() {
  if (section.value === 'cashflow') return loadCashFlow()
  return loadList()
}

const suppliers = ref([])
const purchases = ref([])
const payableOptions = ref([])
const optionsError = ref('')
let optionsPromise
function loadOptions() {
  if (!optionsPromise) {
    optionsPromise = Promise.allSettled([
      api('/suppliers', { params: { page: 1, page_size: 100 } }),
      api('/purchase-orders', { params: { page: 1, page_size: 100 } }),
    ]).then(([supplierResult, purchaseResult]) => {
      if (supplierResult.status === 'fulfilled') suppliers.value = rows(supplierResult.value)
      if (purchaseResult.status === 'fulfilled') purchases.value = rows(purchaseResult.value)
      const failures = [supplierResult, purchaseResult].filter((result) => result.status === 'rejected')
      optionsError.value = failures.length ? `部分主档未能读取：${failures[0].reason.message}。仍可填写供应商名称，关联采购可留空。` : ''
    })
  }
  return optionsPromise
}

const payableDetail = ref(null)
const payableDetailLoading = ref(false)
const payableDetailError = ref('')
const payableBusy = ref(false)
async function openPayable(item) {
  payableDetail.value = item
  payableDetailLoading.value = true
  payableDetailError.value = ''
  try { payableDetail.value = await api(`/finance/payables/${item.id}`) }
  catch (error) { payableDetailError.value = error.message }
  finally { payableDetailLoading.value = false }
}
async function voidPayable() {
  const item = payableDetail.value
  if (!item || payableBusy.value) return
  if (!window.confirm(`确认作废应付 ${item.payable_no}？存在有效付款时后端会拒绝。`)) return
  payableBusy.value = true
  payableDetailError.value = ''
  try {
    await api(`/finance/payables/${item.id}/void`, { method: 'POST', body: {} })
    payableDetail.value = null
    emit('toast', '采购应付已作废')
    await loadList('payables')
  } catch (error) { payableDetailError.value = error.message }
  finally { payableBusy.value = false }
}

const payableOpen = ref(false)
const payableSaving = ref(false)
const payableError = ref('')
const payableForm = reactive({ supplier: '', supplier_name: '', purchase: '', amount: '', payable_at: '', due_at: '', remark: '' })
async function openPayableCreate() {
  await loadOptions()
  Object.assign(payableForm, { supplier: '', supplier_name: '', purchase: '', amount: '', payable_at: localDate(), due_at: '', remark: '' })
  payableError.value = ''
  payableOpen.value = true
}
function payablePurchaseChanged() {
  const purchase = purchases.value.find((item) => item.id === Number(payableForm.purchase))
  if (purchase && !payableForm.supplier_name) payableForm.supplier_name = purchase.supplier_name || ''
}
async function savePayable() {
  if (payableSaving.value) return
  if (!payableForm.supplier && !payableForm.supplier_name.trim()) { payableError.value = '请选择供应商或填写供应商名称'; return }
  if (!positiveAmount(payableForm.amount)) { payableError.value = '应付金额必须大于 0'; return }
  if (!payableForm.payable_at) { payableError.value = '请选择应付日期'; return }
  if (payableForm.due_at && payableForm.due_at < payableForm.payable_at) { payableError.value = '到期日期不能早于应付日期'; return }
  payableSaving.value = true
  payableError.value = ''
  try {
    const saved = await api('/finance/payables', { method: 'POST', body: {
      supplier: payableForm.supplier ? Number(payableForm.supplier) : null,
      supplier_name: payableForm.supplier_name.trim(),
      purchase: payableForm.purchase ? Number(payableForm.purchase) : null,
      amount: payableForm.amount,
      payable_at: payableForm.payable_at,
      due_at: payableForm.due_at || null,
      remark: payableForm.remark.trim(),
    } })
    payableOpen.value = false
    emit('toast', `应付 ${saved.payable_no} 已建立`)
    await loadList('payables')
    await openPayable(saved)
  } catch (error) { payableError.value = error.message }
  finally { payableSaving.value = false }
}

const paymentOpen = ref(false)
const paymentSaving = ref(false)
const paymentError = ref('')
const paymentForm = reactive({ payable: '', supplier: '', supplier_name: '', amount: '', paid_at: '', method: 'bank', transaction_ref: '', remark: '' })
async function openPayment(payable = null) {
  payableDetail.value = null
  paymentError.value = ''
  const [, payableResult] = await Promise.all([
    loadOptions(),
    api('/finance/payables', { params: { page: 1, page_size: 100 } }).then((result) => {
      payableOptions.value = rows(result).filter((item) => item.status !== 'settled')
    }).catch((error) => { paymentError.value = `应付单选项读取失败：${error.message}` }),
  ])
  Object.assign(paymentForm, {
    payable: payable?.id || '', supplier: payable?.supplier || '', supplier_name: payable?.supplier_name || '',
    amount: payable?.outstanding || '', paid_at: localDateTime(), method: 'bank', transaction_ref: '', remark: '',
  })
  paymentOpen.value = true
}
function paymentPayableChanged() {
  const payable = payableOptions.value.find((item) => item.id === Number(paymentForm.payable))
  paymentForm.supplier = payable?.supplier || ''
  paymentForm.supplier_name = payable?.supplier_name || ''
}
async function savePayment() {
  if (paymentSaving.value) return
  if (!paymentForm.payable && !paymentForm.supplier && !paymentForm.supplier_name.trim()) { paymentError.value = '请选择供应商或填写供应商名称'; return }
  if (!positiveAmount(paymentForm.amount)) { paymentError.value = '付款金额必须大于 0'; return }
  paymentSaving.value = true
  paymentError.value = ''
  try {
    await api('/finance/supplier-payments', { method: 'POST', body: {
      payable: paymentForm.payable ? Number(paymentForm.payable) : null,
      supplier: paymentForm.payable ? null : (paymentForm.supplier ? Number(paymentForm.supplier) : null),
      supplier_name: paymentForm.payable ? '' : paymentForm.supplier_name.trim(),
      amount: paymentForm.amount,
      paid_at: paymentForm.paid_at || null,
      method: paymentForm.method,
      transaction_ref: paymentForm.transaction_ref.trim(),
      remark: paymentForm.remark.trim(),
    } })
    paymentOpen.value = false
    payableDetail.value = null
    emit('toast', '供应商付款已登记')
    await loadList(section.value === 'payments' ? 'payments' : 'payables')
  } catch (error) { paymentError.value = error.message }
  finally { paymentSaving.value = false }
}

const entryOpen = ref(false)
const entrySaving = ref(false)
const entryError = ref('')
const entryForm = reactive({ direction: 'expense', category: '', amount: '', occurred_at: '', counterparty: '', method: '', reference_no: '', remark: '' })
function openEntry(direction) {
  Object.assign(entryForm, { direction, category: '', amount: '', occurred_at: localDateTime(), counterparty: '', method: '', reference_no: '', remark: '' })
  entryError.value = ''
  entryOpen.value = true
}
async function saveEntry() {
  if (entrySaving.value) return
  if (!entryForm.category.trim()) { entryError.value = '请填写费用或收入类别'; return }
  if (!positiveAmount(entryForm.amount)) { entryError.value = '收支金额必须大于 0'; return }
  entrySaving.value = true
  entryError.value = ''
  try {
    const saved = await api('/finance/cash-entries', { method: 'POST', body: {
      direction: entryForm.direction,
      category: entryForm.category.trim(),
      amount: entryForm.amount,
      occurred_at: entryForm.occurred_at || null,
      counterparty: entryForm.counterparty.trim(),
      method: entryForm.method.trim(),
      reference_no: entryForm.reference_no.trim(),
      remark: entryForm.remark.trim(),
    } })
    entryOpen.value = false
    emit('toast', `${saved.direction === 'expense' ? '费用支出' : '其他收入'}已登记`)
    await loadList('entries')
  } catch (error) { entryError.value = error.message }
  finally { entrySaving.value = false }
}

const voidTarget = ref(null)
const voidReason = ref('')
const voidSaving = ref(false)
const voidError = ref('')
function openVoid(kind, item) {
  voidTarget.value = { kind, item }
  voidReason.value = ''
  voidError.value = ''
}
async function saveVoid() {
  if (!voidTarget.value || voidSaving.value) return
  if (!voidReason.value.trim()) { voidError.value = '作废必须填写原因'; return }
  voidSaving.value = true
  voidError.value = ''
  const { kind, item } = voidTarget.value
  const path = kind === 'payments' ? `/finance/supplier-payments/${item.id}/void` : `/finance/cash-entries/${item.id}/void`
  try {
    await api(path, { method: 'POST', body: { reason: voidReason.value.trim() } })
    voidTarget.value = null
    emit('toast', kind === 'payments' ? '付款已作废，关联应付余额已由后端回退' : '收支记录已作废')
    await loadList(kind)
  } catch (error) { voidError.value = error.message }
  finally { voidSaving.value = false }
}

const cashFilters = reactive({ ...monthRange(), group_by: 'month' })
const cashLoading = ref(false)
const cashError = ref('')
const cashFlow = ref({ rows: [], totals: null, note: '' })
async function loadCashFlow() {
  if (cashLoading.value) return
  if (!cashFilters.date_from || !cashFilters.date_to) { cashError.value = '请选择现金流开始和结束日期'; return }
  if (cashFilters.date_from > cashFilters.date_to) { cashError.value = '开始日期不得晚于结束日期'; return }
  cashLoading.value = true
  cashError.value = ''
  try { cashFlow.value = await api('/finance/cash-flow', { params: cashFilters }) }
  catch (error) { cashError.value = error.message; cashFlow.value = { rows: [], totals: null, note: '' } }
  finally { cashLoading.value = false }
}

onMounted(() => {
  if (section.value === 'cashflow') loadCashFlow()
  else { loadList(); loadOptions() }
})
</script>

<template>
  <section class="cash-heading">
    <div><h3>{{ sectionTitle }}</h3><p v-if="section === 'payables'">财务确认真实应付金额；采购单只作可选关联，不自动推算金额。</p><p v-else-if="section === 'payments'">可核销应付或登记独立付款；作废保留原因，并由后端回退应付。</p><p v-else-if="section === 'entries'">仅登记其他实际收支；工资、客户回款和供应商付款由各自业务流水统计。</p><p v-else>按实际业务时间汇总有效资金流水，不包含作废或撤销记录。</p></div>
    <div class="cash-actions">
      <button class="button secondary" type="button" :disabled="section === 'cashflow' ? cashLoading : activeState.loading" @click="refresh"><AppIcon name="refresh" :size="16" />刷新</button>
      <button v-if="section === 'payables'" class="button primary" type="button" @click="openPayableCreate">新建应付</button>
      <button v-if="section === 'payments'" class="button primary" type="button" @click="openPayment()">登记付款</button>
      <template v-if="section === 'entries'"><button class="button secondary" type="button" @click="openEntry('expense')">登记费用支出</button><button class="button primary" type="button" @click="openEntry('income')">登记其他收入</button></template>
    </div>
  </section>

  <template v-if="section === 'payables'">
    <form class="filter-bar cash-filter" @submit.prevent="queryList('payables')">
      <select v-model="states.payables.filters.status" aria-label="应付状态" @change="queryList('payables')"><option v-for="item in payableStatuses" :key="item[0]" :value="item[0]">{{ item[1] }}</option></select>
      <select v-model="states.payables.filters.supplier" aria-label="筛选应付供应商" @change="queryList('payables')"><option value="">全部供应商</option><option v-for="supplier in suppliers" :key="supplier.id" :value="supplier.id">{{ supplier.name }}</option></select>
      <label class="search-box"><AppIcon name="search" :size="17" /><span class="sr-only">搜索应付单号</span><input v-model="states.payables.filters.keyword" placeholder="搜索应付单号" /></label>
      <label class="field"><span>应付开始</span><input v-model="states.payables.filters.date_from" type="date" /></label>
      <label class="field"><span>应付截止</span><input v-model="states.payables.filters.date_to" type="date" /></label>
      <button class="button primary">查询</button>
    </form>
    <p v-if="optionsError" class="form-error cash-option-error" role="alert">{{ optionsError }}</p>
    <section class="card table-card cash-list-card">
      <PageState :loading="states.payables.loading" :error="states.payables.error" :empty="!rows(states.payables.data).length" empty-text="暂无应付记录，可点击「新建应付」登记" @retry="loadList('payables')">
        <div class="table-wrap desktop-table"><table><caption class="sr-only">采购应付列表</caption><thead><tr><th>应付单 / 采购单</th><th>供应商</th><th class="numeric">应付（元）</th><th class="numeric">已付（元）</th><th class="numeric">余额（元）</th><th>状态</th><th>应付 / 到期日</th><th>操作</th></tr></thead><tbody><tr v-for="item in rows(states.payables.data)" :key="item.id"><td><button class="link-button cell-title" type="button" :aria-label="`查看应付 ${item.payable_no}`" @click="openPayable(item)">{{ item.payable_no }}</button><small class="cell-subtitle">{{ purchaseLabel(item.purchase) }}</small></td><td>{{ item.supplier_name || '—' }}</td><td class="numeric">{{ amount(item.amount) }}</td><td class="numeric">{{ amount(item.paid_amount) }}</td><td class="numeric cell-title">{{ amount(item.outstanding) }}</td><td><span class="badge" :class="statusClass(item.status)">{{ item.status_display }}</span></td><td>{{ item.payable_at }}<small class="cell-subtitle">到期 {{ item.due_at || '—' }}</small></td><td><div class="row-actions"><button class="button ghost small" type="button" @click="openPayable(item)">详情</button><button v-if="item.status !== 'settled'" class="button secondary small" type="button" @click="openPayment(item)">付款</button></div></td></tr></tbody></table></div>
        <div class="mobile-cards cash-mobile-list"><article v-for="item in rows(states.payables.data)" :key="item.id" class="mobile-row cash-mobile-card"><div class="mobile-row-head"><div><button class="link-button mobile-row-title" type="button" @click="openPayable(item)">{{ item.payable_no }}</button><small class="cell-subtitle">{{ item.supplier_name }} · {{ purchaseLabel(item.purchase) }}</small></div><span class="badge" :class="statusClass(item.status)">{{ item.status_display }}</span></div><div class="cash-amount-grid"><span><strong>{{ amount(item.amount) }}</strong><small>应付</small></span><span><strong>{{ amount(item.paid_amount) }}</strong><small>已付</small></span><span><strong>{{ amount(item.outstanding) }}</strong><small>余额</small></span></div><p class="cash-mobile-date">应付 {{ item.payable_at }} · 到期 {{ item.due_at || '—' }}</p><div class="row-actions"><button class="button ghost small" type="button" @click="openPayable(item)">查看详情</button><button v-if="item.status !== 'settled'" class="button secondary small" type="button" @click="openPayment(item)">供应商付款</button></div></article></div>
      </PageState>
      <MobileLoadMore :loading="states.payables.loadingMore" :error="states.payables.appendError" :has-more="hasMore" :count="rows(states.payables.data).length" @more="loadMore" />
      <CommercialPagination class="desktop-pagination" :page="states.payables.filters.page" :count="states.payables.data.count || 0" :disabled="states.payables.loading" @change="changePage" />
    </section>
  </template>

  <template v-else-if="section === 'payments'">
    <form class="filter-bar cash-filter" @submit.prevent="queryList('payments')"><select v-model="states.payments.filters.status" aria-label="付款状态" @change="queryList('payments')"><option v-for="item in activeStatuses" :key="item[0]" :value="item[0]">{{ item[1] }}</option></select><label class="field"><span>付款开始</span><input v-model="states.payments.filters.date_from" type="date" /></label><label class="field"><span>付款截止</span><input v-model="states.payments.filters.date_to" type="date" /></label><button class="button primary">查询</button></form>
    <section class="card table-card cash-list-card"><PageState :loading="states.payments.loading" :error="states.payments.error" :empty="!rows(states.payments.data).length" empty-text="暂无供应商付款，可登记核销或不关联应付的付款" @retry="loadList('payments')"><div class="table-wrap desktop-table"><table><caption class="sr-only">供应商付款列表</caption><thead><tr><th>付款时间</th><th>供应商 / 应付单</th><th class="numeric">金额（元）</th><th>方式 / 流水号</th><th>状态</th><th>作废原因</th><th>操作</th></tr></thead><tbody><tr v-for="item in rows(states.payments.data)" :key="item.id"><td>{{ fullDateTime(item.paid_at) }}</td><td><span class="cell-title">{{ item.supplier_name }}</span><small class="cell-subtitle">{{ item.payable_no || '不关联应付' }}</small></td><td class="numeric">{{ amount(item.amount) }}</td><td>{{ methodText[item.method] || item.method }}<small class="cell-subtitle">{{ item.transaction_ref || '无外部流水号' }}</small></td><td><span class="badge" :class="statusClass(item.status)">{{ item.status_display }}</span></td><td class="reason-cell">{{ item.void_reason || '—' }}<small v-if="item.voided_at" class="cell-subtitle">{{ fullDateTime(item.voided_at) }}</small></td><td><button v-if="item.status === 'active'" class="button danger small" type="button" :aria-label="`作废 ${item.supplier_name} 付款 ${item.amount} 元`" @click="openVoid('payments', item)">作废</button><span v-else>—</span></td></tr></tbody></table></div><div class="mobile-cards cash-mobile-list"><article v-for="item in rows(states.payments.data)" :key="item.id" class="mobile-row cash-mobile-card"><div class="mobile-row-head"><div><strong>{{ item.supplier_name }}</strong><small class="cell-subtitle">{{ item.payable_no || '不关联应付' }} · {{ fullDateTime(item.paid_at) }}</small></div><span class="badge" :class="statusClass(item.status)">{{ item.status_display }}</span></div><div class="cash-payment-amount"><strong>{{ amount(item.amount) }}</strong><span>{{ methodText[item.method] || item.method }}</span></div><p v-if="item.transaction_ref" class="section-note">流水号：{{ item.transaction_ref }}</p><p v-if="item.void_reason" class="void-reason">作废原因：{{ item.void_reason }}</p><button v-if="item.status === 'active'" class="button danger small" type="button" @click="openVoid('payments', item)">作废付款</button></article></div></PageState><MobileLoadMore :loading="states.payments.loadingMore" :error="states.payments.appendError" :has-more="hasMore" :count="rows(states.payments.data).length" @more="loadMore" /><CommercialPagination class="desktop-pagination" :page="states.payments.filters.page" :count="states.payments.data.count || 0" :disabled="states.payments.loading" @change="changePage" /></section>
  </template>

  <template v-else-if="section === 'entries'">
    <form class="filter-bar cash-filter" @submit.prevent="queryList('entries')"><select v-model="states.entries.filters.direction" aria-label="收支方向" @change="queryList('entries')"><option value="">费用与收入</option><option value="expense">费用支出</option><option value="income">其他收入</option></select><label class="field"><span>类别</span><input v-model="states.entries.filters.category" maxlength="64" placeholder="精确类别" /></label><select v-model="states.entries.filters.status" aria-label="收支状态" @change="queryList('entries')"><option v-for="item in activeStatuses" :key="item[0]" :value="item[0]">{{ item[1] }}</option></select><label class="field"><span>业务开始</span><input v-model="states.entries.filters.date_from" type="date" /></label><label class="field"><span>业务截止</span><input v-model="states.entries.filters.date_to" type="date" /></label><button class="button primary">查询</button></form>
    <section class="card table-card cash-list-card"><PageState :loading="states.entries.loading" :error="states.entries.error" :empty="!rows(states.entries.data).length" empty-text="当前条件下暂无费用支出或其他收入" @retry="loadList('entries')"><div class="table-wrap desktop-table"><table><caption class="sr-only">费用支出与其他收入列表</caption><thead><tr><th>单号 / 业务时间</th><th>方向 / 类别</th><th class="numeric">金额（元）</th><th>往来方</th><th>方式 / 凭证号</th><th>状态</th><th>作废原因</th><th>操作</th></tr></thead><tbody><tr v-for="item in rows(states.entries.data)" :key="item.id"><td><span class="cell-title">{{ item.entry_no }}</span><small class="cell-subtitle">{{ fullDateTime(item.occurred_at) }}</small></td><td><span class="badge" :class="item.direction === 'expense' ? 'overdue' : 'settled'">{{ item.direction_display }}</span><small class="cell-subtitle">{{ item.category }}</small></td><td class="numeric">{{ amount(item.amount) }}</td><td>{{ item.counterparty || '—' }}</td><td>{{ item.method || '—' }}<small class="cell-subtitle">{{ item.reference_no || '无外部凭证号' }}</small></td><td><span class="badge" :class="statusClass(item.status)">{{ item.status_display }}</span></td><td class="reason-cell">{{ item.void_reason || '—' }}</td><td><button v-if="item.status === 'active'" class="button danger small" type="button" :aria-label="`作废收支 ${item.entry_no}`" @click="openVoid('entries', item)">作废</button><span v-else>—</span></td></tr></tbody></table></div><div class="mobile-cards cash-mobile-list"><article v-for="item in rows(states.entries.data)" :key="item.id" class="mobile-row cash-mobile-card"><div class="mobile-row-head"><div><strong>{{ item.category }}</strong><small class="cell-subtitle">{{ item.entry_no }} · {{ fullDateTime(item.occurred_at) }}</small></div><span class="badge" :class="item.direction === 'expense' ? 'overdue' : 'settled'">{{ item.direction_display }}</span></div><div class="cash-payment-amount"><strong>{{ amount(item.amount) }}</strong><span>{{ item.counterparty || '无往来方' }}</span></div><p v-if="item.method || item.reference_no" class="section-note">{{ item.method || '未填方式' }} · {{ item.reference_no || '无凭证号' }}</p><p v-if="item.void_reason" class="void-reason">作废原因：{{ item.void_reason }}</p><div class="row-actions"><span class="badge" :class="statusClass(item.status)">{{ item.status_display }}</span><button v-if="item.status === 'active'" class="button danger small" type="button" @click="openVoid('entries', item)">作废记录</button></div></article></div></PageState><MobileLoadMore :loading="states.entries.loadingMore" :error="states.entries.appendError" :has-more="hasMore" :count="rows(states.entries.data).length" @more="loadMore" /><CommercialPagination class="desktop-pagination" :page="states.entries.filters.page" :count="states.entries.data.count || 0" :disabled="states.entries.loading" @change="changePage" /></section>
  </template>

  <template v-else>
    <form class="filter-bar cash-flow-filter" @submit.prevent="loadCashFlow"><label class="field"><span>开始日期</span><input v-model="cashFilters.date_from" type="date" required /></label><label class="field"><span>结束日期</span><input v-model="cashFilters.date_to" type="date" required /></label><label class="field"><span>汇总周期</span><select v-model="cashFilters.group_by" aria-label="现金流汇总周期"><option value="day">按日</option><option value="month">按月</option></select></label><button class="button primary" :disabled="cashLoading">{{ cashLoading ? '汇总中…' : '查询现金流' }}</button></form>
    <div class="cash-flow-note" role="note"><strong>经营现金流不是利润表</strong><p>{{ cashFlow.note || '汇总有效客户回款、其他收入、实付工资、供应商付款和费用；净流出为负。本报表不是利润表。' }}</p></div>
    <PageState :loading="cashLoading" :error="cashError" :empty="!cashFlow.rows?.length" empty-text="该日期范围暂无有效经营资金流水" @retry="loadCashFlow">
      <div v-if="cashFlow.totals" class="cash-summary" aria-label="现金流合计"><article class="card"><small>流入合计</small><strong>{{ amount(cashFlow.totals.income) }}</strong></article><article class="card"><small>流出合计</small><strong>{{ amount(cashFlow.totals.outflow) }}</strong></article><article class="card"><small>净现金流</small><strong :class="Number(cashFlow.totals.net) < 0 ? 'negative' : 'positive'">{{ amount(cashFlow.totals.net) }}</strong></article></div>
      <section class="card table-card cash-flow-card"><div class="table-wrap"><table><caption class="sr-only">经营现金流{{ cashFilters.group_by === 'day' ? '按日' : '按月' }}明细</caption><thead><tr><th>{{ cashFilters.group_by === 'day' ? '日期' : '月份' }}</th><th class="numeric">客户回款</th><th class="numeric">其他收入</th><th class="numeric">实付工资</th><th class="numeric">供应商付款</th><th class="numeric">费用支出</th><th class="numeric">流入合计</th><th class="numeric">流出合计</th><th class="numeric">净现金流</th></tr></thead><tbody><tr v-for="row in cashFlow.rows" :key="row.period"><td class="cell-title">{{ row.period }}</td><td class="numeric">{{ amount(row.customer_receipts) }}</td><td class="numeric">{{ amount(row.other_income) }}</td><td class="numeric">{{ amount(row.salary_payments) }}</td><td class="numeric">{{ amount(row.supplier_payments) }}</td><td class="numeric">{{ amount(row.expenses) }}</td><td class="numeric">{{ amount(row.income) }}</td><td class="numeric">{{ amount(row.outflow) }}</td><td class="numeric cell-title" :class="Number(row.net) < 0 ? 'negative' : 'positive'">{{ amount(row.net) }}</td></tr><tr v-if="cashFlow.totals" class="cash-total-row"><td>合计</td><td class="numeric">{{ amount(cashFlow.totals.customer_receipts) }}</td><td class="numeric">{{ amount(cashFlow.totals.other_income) }}</td><td class="numeric">{{ amount(cashFlow.totals.salary_payments) }}</td><td class="numeric">{{ amount(cashFlow.totals.supplier_payments) }}</td><td class="numeric">{{ amount(cashFlow.totals.expenses) }}</td><td class="numeric">{{ amount(cashFlow.totals.income) }}</td><td class="numeric">{{ amount(cashFlow.totals.outflow) }}</td><td class="numeric">{{ amount(cashFlow.totals.net) }}</td></tr></tbody></table></div></section>
    </PageState>
  </template>

  <CommercialModal v-if="payableDetail" wide :title="`应付详情 ${payableDetail.payable_no}`" :subtitle="payableDetail.supplier_name" @close="!payableBusy && (payableDetail = null)"><PageState :loading="payableDetailLoading" :error="payableDetailError && !payableDetail.amount ? payableDetailError : ''" @retry="openPayable(payableDetail)"><div v-if="payableDetailError" class="form-error commercial-error" role="alert">{{ payableDetailError }}</div><div class="detail-grid"><div class="detail-item"><small>供应商</small><strong>{{ payableDetail.supplier_name || '—' }}</strong></div><div class="detail-item"><small>关联采购</small><strong>{{ purchaseLabel(payableDetail.purchase) }}</strong></div><div class="detail-item"><small>应付金额</small><strong>{{ amount(payableDetail.amount) }}</strong></div><div class="detail-item"><small>已付金额</small><strong>{{ amount(payableDetail.paid_amount) }}</strong></div><div class="detail-item"><small>未付余额</small><strong>{{ amount(payableDetail.outstanding) }}</strong></div><div class="detail-item"><small>状态</small><strong>{{ payableDetail.status_display }}</strong></div><div class="detail-item"><small>应付日期</small><strong>{{ payableDetail.payable_at }}</strong></div><div class="detail-item"><small>到期日期</small><strong>{{ payableDetail.due_at || '—' }}</strong></div><div class="detail-item"><small>创建时间</small><strong>{{ fullDateTime(payableDetail.created_at) }}</strong></div></div><p v-if="payableDetail.remark" class="section-note">备注：{{ payableDetail.remark }}</p><div class="cash-actions modal-actions"><button v-if="payableDetail.status !== 'settled'" class="button primary" type="button" @click="openPayment(payableDetail)">登记付款</button><button class="button danger" type="button" :disabled="payableBusy" @click="voidPayable">{{ payableBusy ? '作废中…' : '作废应付' }}</button></div></PageState></CommercialModal>

  <CommercialModal v-if="payableOpen" wide title="新建采购应付" subtitle="供应商主档与手填名称二选一；金额由财务确认" @close="!payableSaving && (payableOpen = false)"><form @submit.prevent="savePayable"><div class="form-grid"><label class="field"><span>供应商主档（可选）</span><select v-model="payableForm.supplier"><option value="">不选择主档，使用手填名称</option><option v-for="supplier in suppliers" :key="supplier.id" :value="supplier.id">{{ supplier.code }} · {{ supplier.name }}</option></select></label><label class="field"><span>供应商名称</span><input v-model="payableForm.supplier_name" maxlength="128" placeholder="未选主档时必填" /></label><label class="field full"><span>关联采购单（可选）</span><select v-model="payableForm.purchase" @change="payablePurchaseChanged"><option value="">不关联采购单</option><option v-for="purchase in purchases" :key="purchase.id" :value="purchase.id">{{ purchase.order_no }} · {{ purchase.supplier_name || '无供应商名称' }}</option></select></label><label class="field"><span>应付金额（元）</span><input v-model="payableForm.amount" type="number" min="0.01" step="0.01" required /></label><label class="field"><span>应付日期</span><input v-model="payableForm.payable_at" type="date" required /></label><label class="field"><span>到期日期（可选）</span><input v-model="payableForm.due_at" type="date" /></label><label class="field full"><span>备注</span><textarea v-model="payableForm.remark" rows="2" maxlength="255" /></label></div><p class="field-help">选择供应商主档时，后端以主档名称为准；到期日不得早于应付日。</p><div v-if="optionsError" class="form-error commercial-error" role="alert">{{ optionsError }}</div><div v-if="payableError" class="form-error commercial-error" role="alert">{{ payableError }}</div><div class="form-actions"><button class="button secondary" type="button" :disabled="payableSaving" @click="payableOpen = false">取消</button><button class="button primary" :disabled="payableSaving">{{ payableSaving ? '保存中…' : '建立应付' }}</button></div></form></CommercialModal>

  <CommercialModal v-if="paymentOpen" wide title="登记供应商付款" subtitle="可核销应付，也可登记不关联应付的独立付款" @close="!paymentSaving && (paymentOpen = false)"><form @submit.prevent="savePayment"><div class="form-grid"><label class="field full"><span>关联应付单（可选）</span><select v-model="paymentForm.payable" @change="paymentPayableChanged"><option value="">不关联应付，独立付款</option><option v-for="item in payableOptions" :key="item.id" :value="item.id">{{ item.payable_no }} · {{ item.supplier_name }} · 余额 {{ amount(item.outstanding) }}</option></select></label><template v-if="!paymentForm.payable"><label class="field"><span>供应商主档（可选）</span><select v-model="paymentForm.supplier"><option value="">不选择主档，使用手填名称</option><option v-for="supplier in suppliers" :key="supplier.id" :value="supplier.id">{{ supplier.code }} · {{ supplier.name }}</option></select></label><label class="field"><span>供应商名称</span><input v-model="paymentForm.supplier_name" maxlength="128" placeholder="未选主档时必填" /></label></template><div v-else class="field full selected-payable"><span>核销供应商</span><strong>{{ paymentForm.supplier_name }}</strong></div><label class="field"><span>付款金额（元）</span><input v-model="paymentForm.amount" type="number" min="0.01" step="0.01" required /></label><label class="field"><span>付款时间</span><input v-model="paymentForm.paid_at" type="datetime-local" /></label><label class="field"><span>付款方式</span><select v-model="paymentForm.method"><option v-for="item in paymentMethods" :key="item[0]" :value="item[0]">{{ item[1] }}</option></select></label><label class="field"><span>外部流水号（可选）</span><input v-model="paymentForm.transaction_ref" maxlength="64" /></label><label class="field full"><span>备注</span><textarea v-model="paymentForm.remark" rows="2" maxlength="255" /></label></div><p class="field-help">关联应付时，本笔金额不得超过未付余额；前端提示不替代后端事务校验。</p><div v-if="optionsError" class="form-error commercial-error" role="alert">{{ optionsError }}</div><div v-if="paymentError" class="form-error commercial-error" role="alert">{{ paymentError }}</div><div class="form-actions"><button class="button secondary" type="button" :disabled="paymentSaving" @click="paymentOpen = false">取消</button><button class="button primary" :disabled="paymentSaving">{{ paymentSaving ? '付款登记中…' : '确认付款' }}</button></div></form></CommercialModal>

  <CommercialModal v-if="entryOpen" wide :title="entryForm.direction === 'expense' ? '登记费用支出' : '登记其他收入'" subtitle="只登记真实已发生的其他资金收支" @close="!entrySaving && (entryOpen = false)"><form @submit.prevent="saveEntry"><div class="form-grid"><label class="field"><span>收支方向</span><select v-model="entryForm.direction"><option value="expense">费用支出</option><option value="income">其他收入</option></select></label><label class="field"><span>类别</span><input v-model="entryForm.category" maxlength="64" required placeholder="如房租、水电、利息收入" /></label><label class="field"><span>金额（元）</span><input v-model="entryForm.amount" type="number" min="0.01" step="0.01" required /></label><label class="field"><span>业务时间</span><input v-model="entryForm.occurred_at" type="datetime-local" /></label><label class="field"><span>往来方（可选）</span><input v-model="entryForm.counterparty" maxlength="128" /></label><label class="field"><span>收支方式（可选）</span><input v-model="entryForm.method" maxlength="32" placeholder="如银行转账、现金" /></label><label class="field"><span>外部凭证号（可选）</span><input v-model="entryForm.reference_no" maxlength="64" /></label><label class="field full"><span>备注</span><textarea v-model="entryForm.remark" rows="2" maxlength="255" /></label></div><div v-if="entryError" class="form-error commercial-error" role="alert">{{ entryError }}</div><div class="form-actions"><button class="button secondary" type="button" :disabled="entrySaving" @click="entryOpen = false">取消</button><button class="button primary" :disabled="entrySaving">{{ entrySaving ? '保存中…' : '确认登记' }}</button></div></form></CommercialModal>

  <CommercialModal v-if="voidTarget" :title="voidTarget.kind === 'payments' ? '作废供应商付款' : '作废收支记录'" :subtitle="voidTarget.kind === 'payments' ? `${voidTarget.item.supplier_name} · ${amount(voidTarget.item.amount)}` : `${voidTarget.item.entry_no} · ${amount(voidTarget.item.amount)}`" @close="!voidSaving && (voidTarget = null)"><form @submit.prevent="saveVoid"><p class="section-note">作废后保留流水与原因，不再计入经营现金流；关联付款会由后端回退应付。</p><label class="field"><span>作废原因（必填）</span><textarea v-model="voidReason" rows="3" maxlength="200" required autofocus placeholder="请说明实际作废原因" /></label><div v-if="voidError" class="form-error commercial-error" role="alert">{{ voidError }}</div><div class="form-actions"><button class="button secondary" type="button" :disabled="voidSaving" @click="voidTarget = null">取消</button><button class="button danger" :disabled="voidSaving || !voidReason.trim()">{{ voidSaving ? '作废中…' : '确认作废' }}</button></div></form></CommercialModal>
</template>

<style scoped>
.cash-heading{display:flex;align-items:center;justify-content:space-between;gap:18px;margin:0 0 16px}.cash-heading h3{margin:0 0 5px;font-size:17px}.cash-heading p{margin:0;color:var(--muted);font-size:12px;line-height:1.6}.cash-actions,.row-actions{display:flex;align-items:center;gap:8px;flex-wrap:wrap}.cash-filter{align-items:flex-end}.cash-filter>.field{min-width:132px}.cash-filter>.search-box{min-width:190px}.cash-option-error{margin-bottom:14px}.link-button{padding:0;border:0;background:transparent;color:var(--orange);text-align:left;cursor:pointer}.link-button:hover{text-decoration:underline}.reason-cell{max-width:220px;white-space:normal;overflow-wrap:anywhere}.cash-mobile-list{display:none}.commercial-error{margin-top:14px}.field-help{margin:12px 0 0;color:var(--muted);font-size:12px;line-height:1.6}.selected-payable{padding:12px;border-radius:10px;background:var(--orange-soft)}.selected-payable>span{display:block;margin-bottom:4px;color:var(--muted);font-size:12px}.modal-actions{margin-top:22px}.void-reason{padding:9px 11px;border-radius:8px;background:#fff2f0;color:var(--danger);font-size:12px;overflow-wrap:anywhere}.cash-flow-filter{align-items:flex-end}.cash-flow-note{margin:0 0 16px;padding:14px 16px;border:1px solid #f3d5ae;border-radius:12px;background:#fffaf2}.cash-flow-note strong{color:#9a5a11;font-size:14px}.cash-flow-note p{margin:5px 0 0;color:var(--text-secondary);font-size:12px;line-height:1.7}.cash-summary{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px;margin-bottom:16px}.cash-summary article{padding:18px 20px}.cash-summary small{display:block;color:var(--muted);font-size:12px}.cash-summary strong{display:block;margin-top:7px;font-size:23px;font-variant-numeric:tabular-nums}.positive{color:#18855b}.negative{color:var(--danger)}.cash-flow-card .table-wrap{overflow-x:auto}.cash-flow-card table{min-width:1050px}.cash-total-row{background:#f7f8fa;font-weight:650}
@media(max-width:1050px){.cash-filter>.search-box{min-width:150px}.cash-filter>.field{min-width:125px}}
@media(max-width:760px){
  .cash-heading{align-items:flex-start;flex-direction:column;gap:12px}.cash-heading .cash-actions{width:100%;justify-content:flex-end}.cash-heading .cash-actions .secondary:first-child{display:none}.cash-heading .cash-actions .button{min-height:36px;font-size:12px}.cash-filter{display:grid;grid-template-columns:1fr 1fr;gap:9px;padding:12px}.cash-filter>.field,.cash-filter>.search-box{min-width:0;width:100%}.cash-filter>.search-box{grid-column:1/-1}.cash-filter>.button{width:100%}.cash-list-card.card{padding:0;background:transparent;box-shadow:none;overflow:visible}.cash-list-card :deep(.page-state){background:#fff;border-radius:12px}.cash-mobile-list{display:grid;gap:12px}.cash-mobile-card.mobile-row{padding:15px;border:0;border-radius:12px;background:#fff;box-shadow:0 2px 8px rgba(31,35,41,.06)}.cash-mobile-card .cell-subtitle{display:block;margin-top:5px}.cash-amount-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));margin:14px 0}.cash-amount-grid span{text-align:center;min-width:0}.cash-amount-grid strong,.cash-amount-grid small{display:block}.cash-amount-grid strong{font-size:13px;font-variant-numeric:tabular-nums;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.cash-amount-grid small{margin-top:4px;color:var(--muted);font-size:11px}.cash-mobile-date{margin:0 0 12px;color:var(--muted);font-size:11px}.cash-payment-amount{display:flex;align-items:center;justify-content:space-between;gap:10px;margin:14px 0}.cash-payment-amount strong{font-size:19px;font-variant-numeric:tabular-nums}.cash-payment-amount span{color:var(--text-secondary);font-size:12px}.cash-mobile-card .section-note{font-size:11px}.desktop-pagination{display:none}.cash-flow-filter{display:grid;grid-template-columns:1fr 1fr;padding:12px}.cash-flow-filter .field{min-width:0}.cash-flow-filter .field:nth-child(3),.cash-flow-filter .button{grid-column:1/-1;width:100%}.cash-summary{gap:8px}.cash-summary article{padding:14px 10px;text-align:center}.cash-summary strong{font-size:16px;overflow-wrap:anywhere}.cash-flow-card.card{padding:0;box-shadow:0 2px 8px rgba(31,35,41,.06)}.modal-actions{justify-content:flex-end}
}
@media(max-width:350px){.cash-heading .cash-actions{justify-content:stretch}.cash-heading .cash-actions .button{flex:1}.cash-summary small{font-size:10px}.cash-summary strong{font-size:14px}}
</style>
