<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { api, rows } from '../api'
import { dateTime, number } from '../format'
import AppIcon from '../components/AppIcon.vue'
import PageState from '../components/PageState.vue'
import CommercialModal from '../components/CommercialModal.vue'
import CommercialPagination from '../components/CommercialPagination.vue'
import MobileLoadMore from '../components/MobileLoadMore.vue'

const emit = defineEmits(['toast', 'navigate'])
const loading = ref(true), loadingMore = ref(false), error = ref(''), appendError = ref(''), listData = ref({ count: 0 })
const hasMore = computed(() => rows(listData.value).length < Number(listData.value.count || 0))
const filters = reactive({ status: '', customer_id: '', style_id: '', keyword: '', page: 1 })
const customers = ref([]), styles = ref([]), materials = ref([]), orders = ref([])
const detail = ref(null), detailLoading = ref(false), detailError = ref('')
const form = ref(null), saving = ref(false), formError = ref(''), bomLoading = ref(false)
const actionBusy = ref(false), statusOpen = ref(false), statusSaving = ref(false), statusError = ref(''), wonOrderId = ref('')
const compareOpen = ref(false), compareLoading = ref(false), compareError = ref(''), compareData = ref(null)
let searchTimer, optionsPromise
const amt = (v) => v === null || v === undefined || v === '' ? '—' : String(v)
const percent = (v) => v === null || v === undefined || v === '' ? '—' : `${v}%`
const statusClass = (s) => ({ draft: '', sent: 'producing', won: 'done', lost: 'cancelled' }[s] || '')
const rateWarning = computed(() => {
  const value = form.value?.labor?.rate
  if (value === '' || value === null || value === undefined || !Number.isFinite(Number(value))) return ''
  if (Number(value) < 1.8) return `倍率 ${value} 低于常规区间下限 1.8，请确认`
  if (Number(value) > 3) return `倍率 ${value} 高于常规区间上限 3.0，请确认`
  return ''
})
const comparisonRows = computed(() => {
  const diff = compareData.value?.diff
  if (!diff) return []
  return [
    ['材料', diff.material], ['人工（工价口径）', diff.labor],
    ['成本合计', diff.total], ['收入（报价 vs 理论）', diff.revenue],
  ].filter((row) => row[1]).map(([label, value]) => ({ label, ...value }))
})
function notify(message) { emit('toast', message) }
function today() { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}` }

async function loadPage(append = false) {
  if (!append && window.matchMedia('(max-width: 760px)').matches) filters.page = 1
  const requestedPage = filters.page
  if (append) { loadingMore.value = true; appendError.value = '' }
  else { loading.value = true; error.value = ''; appendError.value = '' }
  try {
    const result = await api('/quotation/quotations', { params: { ...filters, page_size: 20 } })
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
      api('/styles', { params: { page: 1, page_size: 100 } }),
      api('/tech/materials', { params: { page: 1, page_size: 100 } }),
      api('/orders', { params: { page: 1, page_size: 100 } }),
    ]).then(([c, s, m, o]) => {
      if (c.status === 'fulfilled') customers.value = rows(c.value)
      if (s.status === 'fulfilled') styles.value = rows(s.value)
      if (m.status === 'fulfilled') materials.value = rows(m.value)
      if (o.status === 'fulfilled') orders.value = rows(o.value)
    })
  }
  return optionsPromise
}
async function openDetail(item) {
  detail.value = item; detailLoading.value = true; detailError.value = ''
  try { detail.value = await api(`/quotation/quotations/${item.id}`) }
  catch (e) { detailError.value = e.message }
  finally { detailLoading.value = false }
}
function closeDetail() { detail.value = null; statusOpen.value = false; compareOpen.value = false }
function blankLabor() { return { labor_base: '', rate: '2.3', small_lot_addon: '', addon_amount: '' } }
async function openCreate() {
  await loadOptions()
  if (!styles.value.length) { notify('当前公司暂无可用款式，或款式档案读取失败'); return }
  formError.value = ''
  form.value = { id: null, quote_no: '', style: '', customer: '', qty: '', quote_date: today(), remark: '', materials: [], labor: blankLabor() }
}
async function openEdit(quote) {
  await loadOptions()
  formError.value = ''
  form.value = {
    id: quote.id, quote_no: quote.quote_no, style: quote.style || '', customer: quote.customer || '',
    qty: String(quote.qty), quote_date: quote.quote_date || today(), remark: quote.remark || '',
    materials: (quote.materials || []).map((m) => ({ material: m.material, bom_item: m.bom_item_id || null, quote_unit_usage: m.quote_unit_usage, price: m.price })),
    labor: quote.labor ? { labor_base: quote.labor.labor_base, rate: quote.labor.rate, small_lot_addon: quote.labor.small_lot_addon, addon_amount: quote.labor.addon_amount } : blankLabor(),
  }
}
function defaultPrice(materialId) { const m = materials.value.find((item) => item.id === Number(materialId)); return m?.price_reference === null || m?.price_reference === undefined || m?.price_reference === '' ? '0' : String(m.price_reference) }
function addMaterial() { form.value.materials.push({ material: '', bom_item: null, quote_unit_usage: '1', price: '0' }) }
function materialChanged(row) {
  row.bom_item = null
  if (row.material) row.price = defaultPrice(row.material)
}
function removeMaterial(index) { form.value.materials.splice(index, 1) }
async function pullBom() {
  if (!form.value?.style) { formError.value = '请先选择款式'; return }
  bomLoading.value = true; formError.value = ''
  try {
    const data = await api('/tech/boms', { params: { style_id: form.value.style, status: 'active', page: 1, page_size: 5 } })
    const bom = rows(data)[0]
    if (!bom?.items?.length) { formError.value = '该款式没有生效 BOM 明细'; return }
    const additions = bom.items.map((item) => ({ material: item.material, bom_item: item.id, quote_unit_usage: item.unit_usage, price: defaultPrice(item.material) }))
    form.value.materials.push(...additions); notify(`已从 BOM v${bom.version} 带出 ${additions.length} 行`)
  } catch (e) { formError.value = e.message }
  finally { bomLoading.value = false }
}
function validateForm(f) {
  if (!f.style) return '请选择款式'
  const qty = Number(f.qty)
  if (!f.qty || !Number.isInteger(qty) || qty <= 0) return '报价数量须为正整数'
  for (let i = 0; i < f.materials.length; i++) {
    const row = f.materials[i], usage = Number(row.quote_unit_usage), price = Number(row.price)
    if (!row.material) return `第 ${i + 1} 行请选择物料`
    if (row.quote_unit_usage === '' || !Number.isFinite(usage) || usage <= 0) return `第 ${i + 1} 行报价单耗须大于 0`
    if (row.price === '' || !Number.isFinite(price) || price < 0) return `第 ${i + 1} 行单价不能为负数`
  }
  const labor = f.labor
  if (labor.labor_base !== '' && (!Number.isFinite(Number(labor.labor_base)) || Number(labor.labor_base) < 0)) return '工序工价总和不能为负数'
  if (labor.rate !== '' && (!Number.isFinite(Number(labor.rate)) || Number(labor.rate) <= 0)) return '倍率必须大于 0'
  if (labor.small_lot_addon !== '' && (!Number.isFinite(Number(labor.small_lot_addon)) || Number(labor.small_lot_addon) < 0 || Number(labor.small_lot_addon) > 100)) return '小单溢价须在 0 至 100 之间'
  if (labor.addon_amount !== '' && (!Number.isFinite(Number(labor.addon_amount)) || Number(labor.addon_amount) < 0)) return '附加费不能为负数'
  return ''
}
async function saveQuote() {
  const f = form.value
  if (!f || saving.value) return
  const invalid = validateForm(f)
  if (invalid) { formError.value = invalid; return }
  const labor = {}
  for (const key of ['labor_base', 'rate', 'small_lot_addon', 'addon_amount']) if (f.labor[key] !== '') labor[key] = f.labor[key]
  const body = {
    style: Number(f.style), customer: f.customer ? Number(f.customer) : null, qty: Number(f.qty),
    quote_date: f.quote_date || undefined, remark: f.remark.trim(),
    materials: f.materials.map((m) => ({ material: Number(m.material), ...(m.bom_item ? { bom_item: Number(m.bom_item) } : {}), quote_unit_usage: m.quote_unit_usage, price: m.price })),
    labor,
  }
  saving.value = true; formError.value = ''
  try {
    const saved = await api(f.id ? `/quotation/quotations/${f.id}` : '/quotation/quotations', { method: f.id ? 'PUT' : 'POST', body })
    notify(f.id ? '报价单已保存' : `报价单 ${saved.quote_no} 已建立`)
    form.value = null; await load(); await openDetail(saved)
  } catch (e) { formError.value = e.message }
  finally { saving.value = false }
}
async function markStatus(target, orderId) {
  if (actionBusy.value) return
  actionBusy.value = true; statusSaving.value = true; statusError.value = ''; detailError.value = ''
  try {
    const body = { status: target }
    if (orderId) body.order_id = Number(orderId)
    const saved = await api(`/quotation/quotations/${detail.value.id}/mark-status`, { method: 'POST', body })
    detail.value = saved; statusOpen.value = false; compareOpen.value = false
    await load(); notify(target === 'won' ? '已标记成单' : target === 'lost' ? '已标记流失' : '已标记发送')
  } catch (e) { statusError.value = e.message; detailError.value = e.message; notify(e.message) }
  finally { actionBusy.value = false; statusSaving.value = false }
}
function requestStatus(target) {
  if (target === 'won') { openWon(); return }
  const terminal = target === 'lost'
  if (!window.confirm(terminal ? '标记流失后不可再流转，确定？' : '确认将该报价标记为已发送？')) return
  markStatus(target)
}
async function openWon() {
  await loadOptions(); wonOrderId.value = detail.value?.order || ''; statusError.value = ''; statusOpen.value = true
}
function confirmWon() {
  if (detail.value?.status === 'won' && !wonOrderId.value) { statusError.value = '已成交报价补挂订单时请选择订单'; return }
  markStatus('won', wonOrderId.value)
}
async function deleteQuote() {
  const quote = detail.value
  if (!quote || actionBusy.value) return
  if (!window.confirm(`删除报价单 ${quote.quote_no}？删除后列表不再显示，单号流水不会复用。`)) return
  actionBusy.value = true
  try { await api(`/quotation/quotations/${quote.id}`, { method: 'DELETE' }); closeDetail(); await load(); notify('报价单已删除') }
  catch (e) { detailError.value = e.message; notify(e.message) }
  finally { actionBusy.value = false }
}
async function openCompare() {
  compareOpen.value = true; compareLoading.value = true; compareError.value = ''; compareData.value = null
  try { compareData.value = await api(`/quotation/quotations/${detail.value.id}/backfill-compare`) }
  catch (e) { compareError.value = e.message }
  finally { compareLoading.value = false }
}

onMounted(() => { load(); loadOptions() })
</script>

<template>
  <div class="welcome"><div><h2>报价管理</h2><p>材料估耗、人工倍率、小单规则与成交后的实际成本回填对比。</p></div><div class="commercial-actions"><button class="button secondary" @click="load"><AppIcon name="refresh" :size="16" />刷新</button><button class="button primary" @click="openCreate">新建报价</button></div></div>
  <form class="filter-bar quotation-filter" @submit.prevent="filters.page = 1; load()"><div class="segmented mini-chip-bar"><button v-for="s in [['','全部'],['draft','草稿'],['sent','已发送'],['won','已成交'],['lost','已流失']]" :key="s[0]" type="button" :class="{ active: filters.status === s[0] }" @click="filters.status = s[0]; filters.page = 1; load()">{{ s[1] }}</button></div><div class="quotation-filter-main"><label class="search-box"><AppIcon name="search" :size="17" /><input v-model="filters.keyword" placeholder="报价单号/客户/款号/款名" @input="search" /></label><button class="button primary quotation-query">查询</button></div><details class="quotation-advanced-filter" open><summary>按客户与款式筛选</summary><select v-model="filters.customer_id" aria-label="筛选客户" @change="filters.page = 1; load()"><option value="">全部客户</option><option v-for="c in customers" :key="c.id" :value="c.id">{{ c.name }}</option></select><select v-model="filters.style_id" aria-label="筛选款式" @change="filters.page = 1; load()"><option value="">全部款式</option><option v-for="s in styles" :key="s.id" :value="s.id">{{ s.style_no }} · {{ s.name }}</option></select></details></form>
  <section class="card table-card quotation-list-card"><PageState :loading="loading" :error="error" :empty="!rows(listData).length" empty-text="暂无报价单，点「新建报价」录入款式与数量" @retry="load"><div class="table-wrap desktop-table"><table><thead><tr><th>报价单 / 款式</th><th>客户</th><th class="numeric">数量</th><th>报价日期</th><th class="numeric">材料合计（元）</th><th class="numeric">报价总额（元）</th><th class="numeric">利润率</th><th>状态 / 订单</th><th></th></tr></thead><tbody><tr v-for="q in rows(listData)" :key="q.id" class="clickable" @click="openDetail(q)"><td><span class="cell-title">{{ q.quote_no }}</span><small class="cell-subtitle">{{ q.style_no }} · {{ q.style_name }}</small></td><td>{{ q.customer_name || '—' }}</td><td class="numeric">{{ number(q.qty) }}</td><td>{{ q.quote_date }}</td><td class="numeric">{{ amt(q.material_total) }}</td><td class="numeric cell-title">{{ amt(q.total_price) }}</td><td class="numeric">{{ percent(q.profit_rate) }}</td><td><span class="badge" :class="statusClass(q.status)">{{ q.status_display }}</span><small class="cell-subtitle">{{ q.order_no || '未关联订单' }}</small></td><td><AppIcon name="chevron" :size="16" /></td></tr></tbody></table></div><div class="mobile-cards quotation-mobile-list"><button v-for="q in rows(listData)" :key="q.id" class="mobile-row quotation-business-card" @click="openDetail(q)"><div class="mobile-row-head"><div class="quotation-card-title"><span class="mobile-row-title">{{ q.quote_no }}</span><small class="cell-subtitle">{{ q.customer_name || '无客户' }} · {{ q.style_no }} {{ q.style_name }} · ×{{ number(q.qty) }}</small></div><span class="badge" :class="statusClass(q.status)">{{ q.status_display }}</span></div><div class="mobile-meta quotation-metric-grid"><span><strong>{{ amt(q.total_price) }}</strong><small>报价总价</small></span><span><strong>{{ percent(q.profit_rate) }}</strong><small>毛利率</small></span><span><strong>{{ q.quote_date }}</strong><small>报价日期</small></span></div><div v-if="q.order_no || q.status === 'won'" class="quotation-order-line"><span v-if="q.order_no">关联订单 {{ q.order_no }}</span><span v-else-if="q.status === 'won'" class="quotation-order-warning">已成单待关联订单，可在详情回填对比</span></div></button></div></PageState><MobileLoadMore :loading="loadingMore" :error="appendError" :has-more="hasMore" :count="rows(listData).length" @more="loadMore" /><CommercialPagination class="desktop-pagination" :page="filters.page" :count="listData.count || 0" :disabled="loading" @change="changePage" /></section>

  <CommercialModal v-if="detail" wide :title="detail.quote_no" :subtitle="`${detail.style_no || ''} · ${detail.style_name || ''} · ${detail.customer_name || '无客户'}`" @close="closeDetail"><PageState :loading="detailLoading" :error="detailError && !detail.quote_no ? detailError : ''" @retry="openDetail(detail)"><div v-if="detailError" class="form-error commercial-error">{{ detailError }}</div><div v-if="detail.status === 'won'" class="won-notice">已成交报价可回填实际成本；差异仅作提醒，不拦截业务操作。</div><div class="detail-grid"><div class="detail-item"><small>报价数量</small><strong>{{ number(detail.qty) }}</strong></div><div class="detail-item"><small>报价日期</small><strong>{{ detail.quote_date }}</strong></div><div class="detail-item"><small>状态</small><strong>{{ detail.status_display }}</strong></div><div class="detail-item"><small>材料合计（元）</small><strong>{{ amt(detail.material_total) }}</strong></div><div class="detail-item"><small>报价总额（元）</small><strong>{{ amt(detail.total_price) }}</strong></div><div class="detail-item"><small>利润率</small><strong>{{ percent(detail.profit_rate) }}</strong></div><div class="detail-item"><small>关联订单</small><strong>{{ detail.order_no || '—' }}</strong></div><div class="detail-item"><small>创建时间</small><strong>{{ dateTime(detail.created_at) }}</strong></div></div><p v-if="detail.remark" class="section-note">备注：{{ detail.remark }}</p><div class="commercial-actions commercial-section quotation-detail-actions"><button class="button secondary small" @click="openEdit(detail)">编辑报价</button><button v-if="['draft','sent'].includes(detail.status)" class="button secondary small" @click="requestStatus('sent')" v-show="detail.status === 'draft'">标记已发送</button><button v-if="!detail.order && ['draft','sent','won'].includes(detail.status)" class="button primary small" @click="emit('navigate','orders',`quote-${detail.id}`)">报价预填并创建订单</button><button v-if="['draft','sent'].includes(detail.status)" class="button secondary small" @click="requestStatus('won')">关联已有订单成单</button><button v-if="['draft','sent'].includes(detail.status)" class="button danger small" @click="requestStatus('lost')">标记流失</button><button v-if="detail.status === 'won'" class="button secondary small" @click="openWon">关联 / 改挂订单</button><button class="button secondary small" @click="openCompare">回填对比</button><button class="button danger small" :disabled="actionBusy" @click="deleteQuote">删除</button></div><section class="commercial-section"><div class="card-header"><div><h3>报价用料</h3><p>单件用料额由后端按报价单耗×单价下发</p></div></div><div class="table-wrap quotation-material-table"><table><thead><tr><th>物料</th><th>来源</th><th class="numeric">报价单耗</th><th class="numeric">单价</th><th class="numeric">单件用料额</th></tr></thead><tbody><tr v-for="m in detail.materials || []" :key="m.id"><td><span class="cell-title">{{ m.material_code }} · {{ m.material_name }}</span><small class="cell-subtitle">单位：{{ m.material_unit || '—' }}</small></td><td>{{ m.bom_item_id ? `BOM 行 #${m.bom_item_id}` : '手选物料' }}</td><td class="numeric">{{ amt(m.quote_unit_usage) }}</td><td class="numeric">{{ amt(m.price) }}</td><td class="numeric">{{ amt(m.amount) }}</td></tr><tr v-if="!detail.materials?.length"><td colspan="5" class="page-state">未填写用料行</td></tr></tbody></table></div><div class="quotation-material-mobile"><article v-for="m in detail.materials || []" :key="m.id"><div><strong>{{ m.material_code }} · {{ m.material_name }}</strong><span>{{ m.bom_item_id ? `BOM #${m.bom_item_id}` : '手选物料' }}</span></div><small>单位 {{ m.material_unit || '—' }}</small><p><span>单耗 {{ amt(m.quote_unit_usage) }}</span><span>单价 {{ amt(m.price) }}</span><span>单件用料额 {{ amt(m.amount) }}</span></p></article><p v-if="!detail.materials?.length" class="page-state">未填写用料行</p></div></section><section v-if="detail.labor" class="commercial-section"><div class="card-header"><div><h3>人工报价</h3><p>合计由服务端按工价、倍率、小单溢价和附加费计算</p></div></div><div class="detail-grid"><div class="detail-item"><small>工序工价总和</small><strong>{{ amt(detail.labor.labor_base) }}</strong></div><div class="detail-item"><small>倍率</small><strong>{{ amt(detail.labor.rate) }}</strong></div><div class="detail-item"><small>小单溢价</small><strong>{{ percent(detail.labor.small_lot_addon) }}</strong></div><div class="detail-item"><small>附加费（元）</small><strong>{{ amt(detail.labor.addon_amount) }}</strong></div><div class="detail-item"><small>人工合计（元）</small><strong>{{ amt(detail.labor.labor_total) }}</strong></div><div class="detail-item"><small>小单规则</small><strong>{{ detail.labor.addon_rule?.auto_applied ? '自动应用' : '手工指定' }}</strong></div></div><div v-if="detail.labor.rate_warning" class="form-error rate-warning">{{ detail.labor.rate_warning_msg }}</div></section></PageState></CommercialModal>

  <CommercialModal v-if="form" wide :title="form.id ? `编辑 ${form.quote_no}` : '新建报价'" subtitle="金额与合计均由后端保存时重算，留空的人工字段使用后端默认值" @close="form = null"><form class="quotation-task-form" @submit.prevent="saveQuote"><div class="form-grid"><label class="field"><span>款式</span><select v-model="form.style" required><option value="">请选择当前公司款式</option><option v-for="s in styles" :key="s.id" :value="s.id">{{ s.style_no }} · {{ s.name }}</option></select></label><label class="field"><span>客户（可选）</span><select v-model="form.customer"><option value="">不关联客户</option><option v-for="c in customers" :key="c.id" :value="c.id">{{ c.name }}</option></select></label><label class="field"><span>报价数量</span><input v-model="form.qty" type="number" min="1" step="1" required /></label><label class="field"><span>报价日期</span><input v-model="form.quote_date" type="date" /></label><label class="field full"><span>备注</span><textarea v-model="form.remark" rows="2" maxlength="255" /></label></div><section class="commercial-section"><div class="card-header"><div><h3>用料行</h3><p>可从生效 BOM 带出参照单耗，也可手选物料；报价估耗独立保存</p></div><div class="commercial-actions"><button class="button secondary small" type="button" :disabled="bomLoading" @click="pullBom">{{ bomLoading ? '读取中…' : '从 BOM 带出' }}</button><button class="button secondary small" type="button" @click="addMaterial">手选物料</button></div></div><div v-for="(m, index) in form.materials" :key="index" class="material-edit-row"><label class="field material-select"><span>物料</span><select v-model="m.material" required @change="materialChanged(m)"><option value="">请选择物料</option><option v-for="item in materials" :key="item.id" :value="item.id">{{ item.code }} · {{ item.name }}（{{ item.unit }}）</option></select><small v-if="m.bom_item" class="cell-subtitle">BOM 定额行 #{{ m.bom_item }}</small></label><label class="field"><span>报价单耗</span><input v-model="m.quote_unit_usage" type="number" min="0.0001" step="0.0001" required /></label><label class="field"><span>单价</span><input v-model="m.price" type="number" min="0" step="0.001" required /></label><button class="button danger small" type="button" @click="removeMaterial(index)">删除</button></div><p v-if="!form.materials.length" class="section-note">暂无用材；可保存纯加工报价。</p></section><section class="commercial-section"><div class="card-header"><div><h3>人工报价</h3><p>工价总和留空取款式工序快照；数量小于 300 时小单溢价默认 30%</p></div></div><div class="form-grid"><label class="field"><span>工序工价总和</span><input v-model="form.labor.labor_base" type="number" min="0" step="0.001" placeholder="留空自动带出" /></label><label class="field"><span>倍率</span><input v-model="form.labor.rate" type="number" min="0.01" step="0.01" placeholder="默认 2.3" /></label><label class="field"><span>小单溢价（%）</span><input v-model="form.labor.small_lot_addon" type="number" min="0" max="100" step="0.01" placeholder="留空按数量规则" /></label><label class="field"><span>附加费（元）</span><input v-model="form.labor.addon_amount" type="number" min="0" step="0.01" placeholder="默认 0" /></label></div><div v-if="rateWarning" class="form-error rate-warning">{{ rateWarning }}（仅提醒，不拦截）</div></section><div v-if="formError" class="form-error commercial-error">{{ formError }}</div><div class="form-actions"><button class="button secondary" type="button" @click="form = null">取消</button><button class="button primary" :disabled="saving">{{ saving ? '保存中…' : '保存报价' }}</button></div></form></CommercialModal>

  <CommercialModal v-if="statusOpen" :title="detail?.status === 'won' ? '关联 / 改挂订单' : '标记成单'" :subtitle="detail?.quote_no" @close="statusOpen = false"><form @submit.prevent="confirmWon"><label class="field"><span>关联当前公司订单</span><select v-model="wonOrderId"><option value="">暂不关联订单</option><option v-for="o in orders" :key="o.id" :value="o.id">{{ o.order_no }} · {{ o.customer_name || '无客户' }} · {{ o.style_no }}</option></select></label><p class="section-note">关联订单后，回填对比将读取该订单成本。成单为终态，不可回退。</p><div v-if="statusError" class="form-error commercial-error">{{ statusError }}</div><div class="form-actions"><button class="button secondary" type="button" @click="statusOpen = false">取消</button><button class="button primary" :disabled="statusSaving">{{ statusSaving ? '提交中…' : '确认成单' }}</button></div></form></CommercialModal>

  <CommercialModal v-if="compareOpen" wide title="报价回填对比" :subtitle="compareData?.quotation ? `${compareData.quotation.quote_no} · 实际减假设` : detail?.quote_no" @close="compareOpen = false"><PageState :loading="compareLoading" :error="compareError" @retry="openCompare"><div v-if="compareData?.message" class="compare-message">{{ compareData.message }}</div><div v-if="compareData" class="detail-grid"><div class="detail-item"><small>报价总额（元）</small><strong>{{ amt(compareData.quotation.total_price) }}</strong></div><div class="detail-item"><small>假设成本（元）</small><strong>{{ amt(compareData.assumed.cost_assumed) }}</strong></div><div class="detail-item"><small>报价人工合计（元）</small><strong>{{ amt(compareData.assumed.labor_total) }}</strong></div><div class="detail-item"><small>关联订单</small><strong>{{ compareData.order?.order_no || '未关联' }}</strong></div><div v-if="compareData.actual" class="detail-item"><small>实际总成本（元）</small><strong>{{ amt(compareData.actual.total_cost) }}</strong></div><div v-if="compareData.actual" class="detail-item"><small>成本计算时间</small><strong>{{ compareData.actual.computed_at || '—' }}</strong></div></div><div v-if="comparisonRows.length" class="table-wrap quotation-compare-table"><table><thead><tr><th>比较项目</th><th class="numeric">报价假设</th><th class="numeric">订单实际</th><th class="numeric">差额（实际−假设）</th><th class="numeric">差异率</th></tr></thead><tbody><tr v-for="row in comparisonRows" :key="row.label"><td class="cell-title">{{ row.label }}</td><td class="numeric">{{ amt(row.assumed) }}</td><td class="numeric">{{ amt(row.actual) }}</td><td class="numeric" :class="Number(row.diff) > 0 ? 'diff-up' : Number(row.diff) < 0 ? 'diff-down' : ''">{{ amt(row.diff) }}</td><td class="numeric">{{ percent(row.diff_rate) }}</td></tr></tbody></table></div><div v-if="comparisonRows.length" class="quotation-compare-mobile"><article v-for="row in comparisonRows" :key="row.label"><h4>{{ row.label }}</h4><div><span>报价假设 <strong>{{ amt(row.assumed) }}</strong></span><span>订单实际 <strong>{{ amt(row.actual) }}</strong></span></div><p>差额 <strong :class="Number(row.diff) > 0 ? 'diff-up' : Number(row.diff) < 0 ? 'diff-down' : ''">{{ amt(row.diff) }}</strong><span>差异率 {{ percent(row.diff_rate) }}</span></p></article></div><div v-else-if="compareData && !compareData.actual" class="page-state"><span class="empty-symbol">⌁</span><p>尚无可比较的实际成本，请先关联订单并重算成本</p></div><p class="section-note">材料、人工和成本差额为实际−假设；收入比较报价总额与订单理论收入。比较只读，不修改报价或成本。</p></PageState></CommercialModal>
</template>

<style scoped>
.commercial-actions{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.commercial-section{margin-top:22px}.commercial-error{margin-top:14px}.quotation-filter-main,.quotation-advanced-filter{display:contents}.quotation-advanced-filter summary{display:none}.quotation-material-mobile,.quotation-compare-mobile{display:none}.won-notice,.compare-message{padding:12px 14px;border-radius:9px;background:var(--orange-soft);color:var(--orange-dark);font-size:13px;line-height:1.7;margin-bottom:18px}.rate-warning{margin-top:14px;color:var(--warning);background:#fff7e8}.material-edit-row{display:grid;grid-template-columns:minmax(240px,2fr) minmax(120px,1fr) minmax(120px,1fr) auto;gap:12px;align-items:end;padding:12px 0;border-bottom:1px solid var(--line)}.material-select small{margin-top:2px}.diff-up{color:var(--danger)}.diff-down{color:var(--good)}
@media(max-width:760px){
  .desktop-pagination{display:none}
  .welcome>.commercial-actions{justify-content:flex-end}.welcome>.commercial-actions .secondary:first-child{display:none}.quotation-filter{display:block}.quotation-filter>.mini-chip-bar{display:flex;width:100%;gap:8px;overflow-x:auto;margin-bottom:10px;padding:2px 1px;background:transparent;scrollbar-width:none}.quotation-filter>.mini-chip-bar button{flex:1 0 auto;padding:7px 14px;border:1px solid var(--line);border-radius:999px;background:#fff;color:var(--text-secondary);font-size:12px}.quotation-filter>.mini-chip-bar button.active{border-color:var(--orange);background:var(--orange);color:#fff;box-shadow:none}
  .quotation-filter-main{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:8px;padding:10px;background:#fff;border-radius:12px;box-shadow:0 2px 8px rgba(31,35,41,.06)}.quotation-filter-main .search-box{width:100%;min-width:0;max-width:none}.quotation-filter-main input{min-height:36px;border:0;border-radius:999px;background:#f5f6f8;padding:7px 8px 7px 34px;font-size:12px}.quotation-query{min-height:36px;padding:7px 16px;font-size:12px}.quotation-advanced-filter{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;width:100%;min-width:0;margin-top:8px;padding:0 10px;background:#fff;border-radius:12px}.quotation-advanced-filter summary{grid-column:1/-1;display:block;min-width:0;max-width:100%;padding:10px 0;color:var(--text-secondary);font-size:12px;cursor:pointer}.quotation-advanced-filter:not([open]){display:block}.quotation-advanced-filter[open]{padding:10px}.quotation-advanced-filter[open] summary{padding:0}.quotation-advanced-filter select{width:100%;min-width:0;max-width:100%;font-size:11px}
  .quotation-list-card.card{padding:0;background:transparent;box-shadow:none;overflow:visible}.quotation-list-card :deep(.page-state){border-radius:12px;background:#fff;box-shadow:0 2px 8px rgba(31,35,41,.06)}.quotation-mobile-list{gap:12px}.quotation-business-card.mobile-row{padding:16px;border:0;border-radius:12px;box-shadow:0 2px 8px rgba(31,35,41,.06)}.quotation-card-title{min-width:0}.quotation-card-title .cell-subtitle{line-height:1.6;overflow-wrap:anywhere}.quotation-metric-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));margin-top:14px}.quotation-metric-grid>span{min-width:0;text-align:center}.quotation-metric-grid strong,.quotation-metric-grid small{display:block}.quotation-metric-grid strong{font-size:13px;font-variant-numeric:tabular-nums;overflow-wrap:anywhere}.quotation-metric-grid small{margin-top:4px;color:var(--muted);font-size:11px}.quotation-order-line{display:flex;flex-wrap:wrap;gap:5px;margin-top:13px;padding-top:10px;border-top:1px solid var(--line);color:var(--muted);font-size:11px}.quotation-order-warning{color:var(--orange-dark)}
  .quotation-material-table,.quotation-compare-table{display:none}.quotation-material-mobile,.quotation-compare-mobile{display:grid;gap:10px}.quotation-material-mobile article,.quotation-compare-mobile article{padding:12px;border-radius:10px;background:#f7f8fa}.quotation-material-mobile article>div{display:flex;align-items:flex-start;justify-content:space-between;gap:8px}.quotation-material-mobile article>div strong{font-size:13px;overflow-wrap:anywhere}.quotation-material-mobile article>div span{flex:none;padding:3px 7px;border-radius:999px;background:var(--orange-soft);color:var(--orange);font-size:10px}.quotation-material-mobile article>small{display:block;margin-top:5px;color:var(--muted);font-size:10px}.quotation-material-mobile article>p{display:flex;flex-wrap:wrap;gap:10px;margin:10px 0 0;color:var(--text-secondary);font-size:11px}.quotation-compare-mobile h4{margin:0 0 9px;font-size:13px}.quotation-compare-mobile article>div{display:grid;grid-template-columns:1fr 1fr;gap:8px;color:var(--muted);font-size:11px}.quotation-compare-mobile article>div strong{display:block;margin-top:3px;color:var(--ink);font-size:13px}.quotation-compare-mobile p{display:flex;align-items:center;gap:8px;margin:10px 0 0;font-size:12px}.quotation-compare-mobile p>span{margin-left:auto;color:var(--muted);font-size:11px}
  .quotation-detail-actions{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}.quotation-detail-actions .button{width:100%;white-space:normal}.material-edit-row{grid-template-columns:1fr 1fr;gap:10px;margin-bottom:10px;padding:12px;border:0;border-radius:10px;background:#f7f8fa}.material-edit-row>.button{grid-column:1/-1;justify-self:end}.material-select{grid-column:1/-1}.commercial-section .card-header{align-items:flex-start;flex-wrap:wrap}.quotation-task-form .commercial-section{padding-top:14px;border-top:1px solid var(--line)}
}
@media(max-width:350px){.quotation-advanced-filter{grid-template-columns:1fr}.quotation-business-card.mobile-row{padding:14px}.quotation-metric-grid strong{font-size:12px}.material-edit-row{grid-template-columns:1fr}.material-select,.material-edit-row>.button{grid-column:1}}
</style>
