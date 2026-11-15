<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { api, rows, upload } from '../api'
import { dateTime, number, pct, statusText } from '../format'
import { COMMON_COLORS, COMMON_SIZES, uniqueOptions } from '../spec-options'
import { MATRIX_VIEWS, applyArrayQuantity, fillMatrixLine, matrixItems, matrixModel, positiveInteger } from '../order-matrix'
import AppIcon from '../components/AppIcon.vue'
import PageState from '../components/PageState.vue'

const loading = ref(true), error = ref(''), data = ref({ count: 0 }), detail = ref(null), progress = ref(null), detailError = ref('')
const audit = ref(null), auditRows = ref([]), auditLoading = ref(false), auditError = ref(''), auditPage = ref(1), auditHasMore = ref(false)
const mobileRows = ref([]), loadingMore = ref(false), moreError = ref(''), hasMore = ref(false)
const filters = reactive({ status: '', keyword: '', page: 1 })
const props = defineProps({ section: { type: String, default: '' } })
const emit = defineEmits(['toast', 'navigate'])
const editor = ref(null), saving = ref(false), editError = ref(''), quoteSource = ref(null)
const styleInfo = ref(null), styleInfoError = ref('')
const imageUploading = ref(false), imagePreview = ref(''), newColor = ref(''), newSize = ref('')
const productions = ref([]), productionsLoading = ref(false), productionsError = ref('')
const options = reactive({ styles: [], customers: [], processes: [], templates: [], colors: [], sizes: [] })
const form = reactive({ id: null, style: '', customer: '', delivery_date: '', order_type: '自产', remark: '', images: [], process_items: [], colors: [], sizes: [], cells: {}, matrix_view: 'h', multi_base: 1, multi_counts: {}, array_qty: 1 })
const orderColorOptions = computed(() => uniqueOptions(form.colors, options.colors, COMMON_COLORS))
const orderSizeOptions = computed(() => uniqueOptions(form.sizes, options.sizes, COMMON_SIZES))
const matrix = computed(() => matrixModel(form.colors, form.sizes, form.cells, form.multi_counts, form.matrix_view, form.multi_base))
function blankForm() { return { id: null, style: '', customer: '', delivery_date: '', order_type: '自产', remark: '', images: [], process_items: [], colors: [], sizes: [], cells: {}, matrix_view: 'h', multi_base: 1, multi_counts: {}, array_qty: 1 } }
async function openEditor(order = null, quoteId = null) {
  editError.value = ''; quoteSource.value = null; Object.assign(form, blankForm()); editor.value = true
  try {
    const [styles, customers, processes, templates] = await Promise.all([api('/styles', { params: { page_size: 200 } }), api('/customers', { params: { page_size: 200 } }), api('/processes', { params: { page_size: 200 } }), api('/process-templates', { params: { page_size: 100 } })])
    options.styles = rows(styles); options.customers = rows(customers); options.processes = rows(processes); options.templates = rows(templates)
    Promise.all([api('/production-colors'), api('/production-hints')]).then(([colorData, hintData]) => { options.colors = rows(colorData).map((item) => item.name); options.sizes = (hintData.sizes || []).map((item) => item.name) }).catch(() => { /* 无生产权限时仍可使用内置常用规格 */ })
    if (order) {
      const full = await api(`/orders/${order.id}`)
      const colors = [...new Set((full.items || []).map((item) => item.color))]
      const sizes = [...new Set((full.items || []).map((item) => item.size))]
      const cells = {}
      ;(full.items || []).forEach((item) => { cells[`${item.color}|${item.size}`] = item.qty })
      Object.assign(form, { id: full.id, style: full.style || '', customer: full.customer || '', delivery_date: full.delivery_date || '', order_type: full.order_type || '自产', remark: full.remark || '', images: [...(full.images || [])], process_items: (full.process_items || []).map((p) => ({ process_id: p.process_id, price: p.price })), colors, sizes, cells })
    } else if (quoteId) {
      const quote = await api(`/quotation/quotations/${Number(quoteId)}`)
      const style = await api(`/styles/${quote.style}`)
      quoteSource.value = quote
      Object.assign(form, {
        style: quote.style || '', customer: quote.customer || '', remark: quote.remark || '',
        process_items: (style.processes || []).map((p) => ({ process_id: p.process_id ?? p.process, price: p.price ?? '0' })),
      })
    }
  } catch (e) { editError.value = e.message }
}
function addMatrixValue(kind) {
  const target = kind === 'color' ? newColor : newSize
  const value = target.value.trim()
  if (!value) return
  const list = kind === 'color' ? form.colors : form.sizes
  if (list.includes(value)) { editError.value = `${kind === 'color' ? '颜色' : '尺码'}已存在`; return }
  list.push(value); target.value = ''; editError.value = ''
}
function removeMatrixValue(kind, index) {
  const list = kind === 'color' ? form.colors : form.sizes
  const [removed] = list.splice(index, 1)
  Object.keys(form.cells).forEach((key) => { if (key.split('|')[kind === 'color' ? 0 : 1] === removed) { delete form.cells[key]; delete form.multi_counts[key] } })
}
function fillMatrix(axis, index) {
  const result = fillMatrixLine({ colors: form.colors, sizes: form.sizes, cells: form.cells, multiCounts: form.multi_counts, view: form.matrix_view, axis, index, multiBase: form.multi_base })
  if (!result.sample) { editError.value = `请先在目标${axis === 'row' ? '行' : '列'}填写一个正整数`; return }
  form.cells = result.cells; form.multi_counts = result.multiCounts; editError.value = ''
}
function switchMatrixView(view) {
  if (view === 'multi' && form.matrix_view !== 'multi') {
    const base = positiveInteger(form.multi_base)
    const counts = {}
    if (base) Object.entries(form.cells).forEach(([key, qty]) => { if (qty % base === 0) counts[key] = qty / base })
    form.multi_counts = counts
  }
  form.matrix_view = view
}
function updateCell(key, value) { if (positiveInteger(value)) form.cells[key] = Number(value); else delete form.cells[key] }
function updateMultiCount(key, value) {
  if (positiveInteger(value)) { form.multi_counts[key] = Number(value); form.cells[key] = positiveInteger(form.multi_base) * Number(value) }
  else { delete form.multi_counts[key]; delete form.cells[key] }
}
function changeMultiBase() {
  Object.entries(form.multi_counts).forEach(([key, count]) => { form.cells[key] = positiveInteger(form.multi_base) * positiveInteger(count) })
}
function fillArray() {
  if (!positiveInteger(form.array_qty)) { editError.value = '排列铺满数量须为正整数'; return }
  form.cells = applyArrayQuantity(form.colors, form.sizes, form.cells, form.array_qty)
  form.multi_counts = {}
  editError.value = ''
}
async function addRemarkImages(event) {
  const input = event.target
  const files = Array.from(input.files || [])
  input.value = ''
  const left = 9 - form.images.length
  if (files.length > left) { editError.value = '备注图片最多 9 张'; return }
  if (files.some((file) => !file.type.startsWith('image/') || file.size > 5 * 1024 * 1024)) { editError.value = '仅支持 5MB 以内的图片'; return }
  imageUploading.value = true; editError.value = ''
  try {
    for (const file of files) {
      const result = await upload('/uploads', file)
      if (!result.url?.startsWith('/media/')) throw new Error('服务器未返回有效媒体路径')
      form.images.push(result.url)
    }
  } catch (e) { editError.value = e.message } finally { imageUploading.value = false }
}
function orderStyleChanged() { const style=options.styles.find((item)=>item.id===Number(form.style));if(style?.customer)form.customer=style.customer }
function addProcess() { form.process_items.push({ process_id: '', price: '0' }) }
function applyTemplate(event) { const tpl = options.templates.find((t) => t.id === Number(event.target.value)); if (tpl) form.process_items = (tpl.items || []).map((p) => ({ process_id: p.process_id, price: p.price })); event.target.value = '' }
async function saveOrder() {
  if (imageUploading.value) { editError.value = '请等待备注图片上传完成'; return }
  const items = matrixItems(form.colors, form.sizes, form.cells)
  if (!form.style) { editError.value = '请选择款式'; return }
  if (!items.length) { editError.value = '请添加色码组合并填写正整数数量'; return }
  saving.value = true; editError.value = ''
  const payload = { style: Number(form.style), customer: form.customer ? Number(form.customer) : null, delivery_date: form.delivery_date || null, order_type: form.order_type, remark: form.remark, images: [...form.images], items, process_items: form.process_items.filter((p) => p.process_id).map((p) => ({ process_id: Number(p.process_id), price: String(p.price || 0) })) }
  try {
    const editingId = form.id, quote = quoteSource.value
    const saved = await api(editingId ? `/orders/${editingId}` : '/orders', { method: editingId ? 'PUT' : 'POST', body: payload })
    editor.value = null; detail.value = null; quoteSource.value = null
    if (!editingId && quote) {
      try {
        await api(`/quotation/quotations/${quote.id}/mark-status`, { method: 'POST', body: { status: 'won', order_id: saved.id } })
        emit('toast', `订单已创建，报价 ${quote.quote_no} 已成单并关联`)
      } catch (e) { emit('toast', `订单 ${saved.order_no} 已创建，但报价关联失败：${e.message}`) }
    } else emit('toast', editingId ? '订单已保存' : '订单已创建')
    load()
  } catch (e) { editError.value = e.message } finally { saving.value = false }
}
async function changeStatus(status) { if (!confirm(`确认将订单 ${detail.value.order_no} 设为${statusText(status)}？`)) return; try { await api(`/orders/${detail.value.id}/status`, { method: 'PUT', body: { status } }); detail.value = null; emit('toast', '订单状态已更新'); load() } catch (e) { detailError.value = e.message } }
async function removeOrder() { if (!confirm(`确认删除订单 ${detail.value.order_no}？已有挂单床次时后端会拒绝。`)) return; try { await api(`/orders/${detail.value.id}`, { method: 'DELETE' }); detail.value = null; emit('toast', '订单已删除'); load() } catch (e) { detailError.value = e.message } }
let searchTimer, listRequest = 0
async function load() {
  const serial = ++listRequest
  loading.value = true; error.value = ''; moreError.value = ''; loadingMore.value = false; hasMore.value = false; mobileRows.value = []
  try {
    const result = await api('/orders', { params: { ...filters, page_size: 20 } })
    if (serial !== listRequest) return
    data.value = result
    mobileRows.value = rows(result)
    hasMore.value = result.next !== undefined ? Boolean(result.next) : filters.page * 20 < (result.count || 0)
  } catch (e) {
    if (serial === listRequest) error.value = e.message
  } finally {
    if (serial === listRequest) loading.value = false
  }
}
async function loadMore() {
  if (loading.value || loadingMore.value || !hasMore.value) return
  const serial = listRequest, page = filters.page + 1
  loadingMore.value = true; moreError.value = ''
  try {
    const result = await api('/orders', { params: { ...filters, page, page_size: 20 } })
    if (serial !== listRequest) return
    data.value = result
    mobileRows.value = mobileRows.value.concat(rows(result))
    filters.page = page
    hasMore.value = result.next !== undefined ? Boolean(result.next) : page * 20 < (result.count || 0)
  } catch (e) {
    if (serial === listRequest) moreError.value = e.message
  } finally {
    if (serial === listRequest) loadingMore.value = false
  }
}
function search() { clearTimeout(searchTimer); searchTimer = setTimeout(() => { filters.page = 1; load() }, 300) }
async function open(order) {
  detail.value = order; progress.value = null; detailError.value = ''; styleInfo.value = null; styleInfoError.value = ''
  productions.value = []; productionsLoading.value = true; productionsError.value = ''
  try {
    const [full, progressData, productionData, styleResult] = await Promise.all([api(`/orders/${order.id}`), api(`/orders/${order.id}/progress`), api('/productions', { params: { order_id: order.id, page_size: 50 } }), api(`/styles/${order.style}`).then((value) => ({ value }), (reason) => ({ reason }))])
    if (detail.value?.id !== order.id) return
    detail.value = full; progress.value = progressData; productions.value = rows(productionData)
    if (styleResult.value) styleInfo.value = styleResult.value
    else styleInfoError.value = styleResult.reason.message
  } catch (e) { detailError.value = e.message; productionsError.value = e.message } finally { productionsLoading.value = false }
}
function close() { detail.value = null; progress.value = null; productions.value = []; styleInfo.value = null; closeAudit() }
function closeAudit() { audit.value = null; auditRows.value = []; auditError.value = ''; auditPage.value = 1; auditHasMore.value = false }
async function openAudit(order) {
  audit.value = order
  auditRows.value = []
  auditPage.value = 1
  await loadAudit(1)
}
async function loadAudit(page) {
  if (!audit.value || auditLoading.value) return
  auditLoading.value = true; auditError.value = ''
  try {
    const result = await api(`/orders/${audit.value.id}/audit-logs`, { params: { page, page_size: 20 } })
    auditRows.value = page === 1 ? rows(result) : auditRows.value.concat(rows(result))
    auditPage.value = page
    auditHasMore.value = result.next !== undefined ? Boolean(result.next) : page * 20 < (result.count || 0)
  } catch (e) { auditError.value = e.message } finally { auditLoading.value = false }
}
function auditDiff(log) {
  const parts = []
  for (const [label, value] of [['变更前', log.before], ['变更后', log.after]]) {
    if (value === null || value === undefined) continue
    if (typeof value === 'object') {
      const entries = Object.entries(value).slice(0, 3).map(([key, item]) => `${key}：${typeof item === 'object' ? JSON.stringify(item) : item}`)
      if (entries.length) parts.push(`${label}：${entries.join('；')}`)
    } else parts.push(`${label}：${value}`)
  }
  return parts.join('；')
}
function cells() { return progress.value?.cells || progress.value?.items || [] }
function productionProgress(item) { return item.ticket_count ? pct(Number(item.done_ticket_count || 0) / Number(item.ticket_count) * 100) : 0 }
function orderLamp(order) {
  if (order.status === 'done') return 'green'
  if (!order.delivery_date || order.days_left === null || order.days_left === undefined) return ''
  if (order.days_left < 0) return 'red'
  return order.days_left <= 7 ? 'yellow' : 'green'
}
onMounted(async () => {
  await load()
  if (props.section.startsWith('quote-')) {
    await openEditor(null, props.section.slice(6))
  } else if (props.section) {
    try { await open(await api(`/orders/${Number(props.section)}`)) } catch (e) { error.value = e.message }
  }
})
</script>

<template>
  <div class="welcome orders-welcome"><div><h2>订单管理</h2><p>创建订单、维护色码工序，并跟踪交期与生产进度。</p></div><div class="orders-head-actions"><button class="button secondary orders-refresh" @click="load"><AppIcon name="refresh" :size="16" />刷新</button><button class="button primary" @click="openEditor()">新建订单</button></div></div>
  <div class="filter-bar orders-filter"><div class="segmented"><button v-for="item in [['producing','生产中'],['done','已完成'],['cancelled','已取消'],['','全部']]" :key="item[0]" :class="{ active: filters.status === item[0] }" @click="filters.status = item[0]; filters.page = 1; load()">{{ item[1] }}</button></div><label class="search-box"><AppIcon name="search" :size="17" /><input v-model="filters.keyword" placeholder="搜索订单号或客户" @input="search" /></label></div>
  <section class="card table-card orders-list-card"><PageState :loading="loading" :error="error" :empty="!rows(data).length" empty-text="没有符合条件的订单" @retry="load">
    <div class="table-wrap desktop-table"><table><thead><tr><th>订单号 / 款式</th><th>客户</th><th class="numeric">订单数量</th><th>生产进度</th><th>交期</th><th>状态</th><th></th></tr></thead><tbody><tr v-for="order in rows(data)" :key="order.id" class="clickable" @click="open(order)"><td><span class="cell-title">{{ order.order_no }}</span><small class="cell-subtitle">{{ order.style_no }} · {{ order.style_name }}</small></td><td>{{ order.customer_name || '—' }}</td><td class="numeric">{{ number(order.total_qty) }}</td><td><div class="row-progress"><div class="meter-track"><div class="meter-fill" :style="{ width: `${pct(order.progress)}%` }" /></div><span>{{ pct(order.progress).toFixed(1) }}%</span></div></td><td>{{ order.delivery_date || '未设置' }}<small v-if="order.days_left !== null && order.days_left !== undefined" class="cell-subtitle">{{ order.days_left < 0 ? `已逾期 ${-order.days_left} 天` : `剩余 ${order.days_left} 天` }}</small></td><td><span class="badge" :class="[order.status, { overdue: order.days_left < 0 }]">{{ statusText(order.status) }}</span></td><td><AppIcon name="chevron" :size="16" /></td></tr></tbody></table></div>
    <div class="mobile-cards"><button v-for="order in mobileRows" :key="order.id" class="mobile-row order-mobile-card" @click="open(order)"><div class="mobile-row-head"><div class="mobile-row-title">{{ order.order_no }}</div><span class="badge" :class="order.status">{{ order.status_display || statusText(order.status) }}</span></div><div class="order-mobile-line"><span>{{ order.customer_name || '未指定客户' }}</span><span v-if="order.delivery_date" class="order-delivery">交期 {{ order.delivery_date }}<i v-if="orderLamp(order)" class="order-lamp" :class="`lamp-${orderLamp(order)}`" /></span></div><div class="row-progress"><div class="meter-track"><div class="meter-fill" :class="{ 'progress-low': pct(order.progress) < 80 }" :style="{ width: `${pct(order.progress)}%` }" /></div><span>{{ Math.round(pct(order.progress)) }}%</span></div><div class="order-mobile-total">订单 {{ number(order.total_qty) }} 件 · 已登记 {{ number(order.registered_qty) }} 件</div></button></div>
  </PageState><div class="pagination desktop-pagination"><button class="button ghost small" :disabled="filters.page <= 1" @click="filters.page--; load()">上一页</button><span>第 {{ filters.page }} 页 · 共 {{ data.count || 0 }} 条</span><button class="button ghost small" :disabled="filters.page * 20 >= (data.count || 0)" @click="filters.page++; load()">下一页</button></div><div v-if="mobileRows.length" class="mobile-list-more"><p v-if="moreError" class="mobile-more-error" role="alert">{{ moreError }} <button type="button" @click="loadMore">重试</button></p><button v-else-if="hasMore" class="mobile-load-more" type="button" :disabled="loadingMore" @click="loadMore"><span v-if="loadingMore" class="spinner" />{{ loadingMore ? '加载中…' : '加载更多' }}</button><p v-else class="mobile-no-more">没有更多了</p></div></section>

  <Teleport to="body"><div v-if="detail" class="modal-backdrop" @click.self="close"><section class="modal order-detail-modal" role="dialog" aria-modal="true" :aria-label="`订单 ${detail.order_no}`"><div class="modal-header"><div><h2>{{ detail.order_no }}</h2><p>{{ detail.style_no }} · {{ detail.style_name }}</p></div><button class="icon-button" aria-label="关闭" @click="close"><AppIcon name="close" /></button></div><ol class="mobile-task-steps"><li class="active">订单信息</li><li>完成度矩阵</li><li>状态处理</li></ol><div class="mobile-detail-copy"><span>类型：{{ detail.order_type || '自产' }}</span><span v-if="detail.remark">备注：{{ detail.remark }}</span></div><div class="detail-grid"><div class="detail-item"><small>客户</small><strong>{{ detail.customer_name || '—' }}</strong></div><div class="detail-item"><small>交期</small><strong>{{ detail.delivery_date || '未设置' }}</strong></div><div class="detail-item"><small>创建时间</small><strong>{{ dateTime(detail.created_at) }}</strong></div></div>
    <section class="order-style-card" aria-label="款式信息"><img v-if="styleInfo?.image || detail.style_image" class="order-style-cover" :src="styleInfo?.image || detail.style_image" :alt="`${detail.style_name}款式图`"><div class="order-style-main"><div class="card-header"><h3>款式信息</h3><span class="badge">{{ detail.style_no }}</span></div><p v-if="styleInfo?.craft_note" class="order-craft-note">{{ styleInfo.craft_note }}</p><p v-else class="editor-hint">{{ styleInfoError || '暂无工艺说明' }}</p><div v-if="styleInfo?.craft_images?.length" class="order-craft-images" aria-label="工艺图片"><button v-for="(url,index) in styleInfo.craft_images" :key="`${url}-${index}`" type="button" @click="imagePreview = url"><img :src="url" :alt="`工艺图片 ${index + 1}`"></button></div><video v-if="styleInfo?.video" class="order-craft-video" :src="styleInfo.video" controls preload="metadata" aria-label="工艺视频" /></div></section>
    <div v-if="detailError" class="form-error">{{ detailError }}</div><div v-else-if="!progress" class="page-state"><span class="spinner" />正在读取订单进度…</div><template v-else><div class="detail-grid order-progress-stats"><div class="detail-item"><small>订单总数</small><strong>{{ number(progress.total_qty ?? progress.total?.order_qty) }}</strong></div><div class="detail-item"><small>已登记工序量</small><strong>{{ number(progress.registered_qty ?? progress.total?.registered_qty) }}</strong></div><div class="detail-item"><small>整体进度</small><strong>{{ pct(progress.progress ?? progress.total?.progress).toFixed(1) }}%</strong></div></div><div class="table-wrap desktop-detail-matrix"><table><thead><tr><th>颜色 / 尺码</th><th class="numeric">订单数</th><th class="numeric">已裁</th><th class="numeric">登记量</th><th class="numeric">进度</th></tr></thead><tbody><tr v-for="cell in cells()" :key="`${cell.color}-${cell.size}`"><td>{{ cell.color }} / {{ cell.size }}</td><td class="numeric">{{ number(cell.order_qty ?? cell.qty) }}</td><td class="numeric">{{ number(cell.cut_qty) }}</td><td class="numeric">{{ number(cell.registered_qty) }}</td><td class="numeric">{{ pct(cell.progress).toFixed(1) }}%</td></tr></tbody></table></div><div class="mobile-order-cells"><div class="mobile-section-heading"><h3>完成度矩阵</h3><span class="badge">整单 {{ Math.round(pct(progress.progress ?? progress.total?.progress)) }}%</span></div><p class="mobile-matrix-legend">红底表示完成率低于 80%</p><article v-for="cell in cells()" :key="`mobile-${cell.color}-${cell.size}`" class="mobile-matrix-card" :class="{ low: pct(cell.progress) < 80 }"><div><strong>{{ cell.color }} / {{ cell.size }}</strong><b>{{ pct(cell.progress).toFixed(0) }}%</b></div><dl><div><dt>订单数</dt><dd>{{ number(cell.order_qty ?? cell.qty) }}</dd></div><div><dt>已裁</dt><dd>{{ number(cell.cut_qty) }}</dd></div><div><dt>已登记</dt><dd>{{ number(cell.registered_qty) }}</dd></div></dl></article><p v-if="!cells().length" class="mobile-empty-copy">暂无数量明细</p></div></template><div v-if="detail.images?.length" class="order-remark-images" aria-label="订单备注图片"><button v-for="(url, index) in detail.images" :key="`${url}-${index}`" type="button" @click="imagePreview = url"><img :src="url" :alt="`备注图片 ${index + 1}`"></button></div><section class="order-production-links"><div class="card-header"><h3>关联床次</h3><span v-if="!productionsLoading" class="badge">{{ productions.length }} 个</span></div><p v-if="productionsLoading" class="editor-hint">正在读取关联床次…</p><p v-else-if="productionsError" class="form-error">{{ productionsError }}</p><div v-else-if="productions.length" class="order-production-list"><button v-for="item in productions" :key="item.id" type="button" class="order-production-link" @click="emit('navigate', 'production', String(item.id))"><span><strong>床次 {{ item.bed_no }}</strong><small>{{ item.style_no }} · {{ item.style_name }}</small></span><span class="order-production-status"><b :class="{ locked: item.is_locked }">{{ item.is_locked ? '已锁定' : item.is_cut_done ? '已裁完' : '生产中' }}</b><small>裁货 {{ number(item.cut_total || item.total_qty) }} · 登记 {{ number(item.registered_qty) }}</small><small>完成 {{ productionProgress(item).toFixed(1) }}% ›</small></span></button></div><p v-else class="editor-hint">暂无关联床次，可通过下方“关联开床”创建。</p></section><p class="section-note">登记量是多工序工作量，不等于已成衣件数。已裁用于开床余量，两者不可混用。</p><div class="form-actions"><button class="button secondary" @click="emit('navigate', 'preproduction', String(detail.id))">产前准备</button><button class="button secondary" @click="emit('navigate', 'production', `create-${detail.id}`)">关联开床</button><button class="button secondary" @click="openAudit(detail)">变更留痕</button><button class="button danger" @click="removeOrder">删除</button><button class="button secondary" @click="openEditor(detail)">编辑订单</button><template v-if="detail.status === 'producing'"><button class="button secondary" @click="changeStatus('cancelled')">取消订单</button><button class="button primary" @click="changeStatus('done')">完成订单</button></template></div></section></div></Teleport>

  <Teleport to="body">
    <div v-if="audit" class="modal-backdrop order-audit-backdrop" @click.self="closeAudit">
      <section class="modal order-audit-modal" role="dialog" aria-modal="true" :aria-label="`${audit.order_no} 变更留痕`">
        <div class="modal-header"><div><h2>订单变更留痕</h2><p>{{ audit.order_no }} · 只读审计记录</p></div><button class="icon-button" aria-label="关闭" @click="closeAudit"><AppIcon name="close" /></button></div>
        <PageState :loading="auditLoading && !auditRows.length" :error="auditError && !auditRows.length ? auditError : ''" :empty="!auditLoading && !auditError && !auditRows.length" empty-text="暂无变更记录" @retry="loadAudit(1)">
          <div class="order-audit-list">
            <article v-for="log in auditRows" :key="log.id" class="order-audit-item">
              <div class="order-audit-head"><span class="badge" :class="`audit-${log.type || 'other'}`">{{ log.type_display || log.type || '其他' }}</span><time>{{ dateTime(log.created_at) }}</time></div>
              <p class="order-audit-message">{{ log.message || '—' }}</p>
              <p v-if="auditDiff(log)" class="order-audit-diff">{{ auditDiff(log) }}</p>
              <footer>操作人：{{ log.operator_name || '—' }}<span v-if="log.target_user_name"> · 对象：{{ log.target_user_name }}</span></footer>
            </article>
          </div>
        </PageState>
        <p v-if="auditError && auditRows.length" class="form-error">{{ auditError }}</p>
        <button v-if="auditHasMore" class="button secondary audit-load-more" :disabled="auditLoading" @click="loadAudit(auditPage + 1)">{{ auditLoading ? '加载中…' : '加载更多' }}</button>
        <p v-else-if="auditRows.length" class="audit-end">没有更多了</p>
      </section>
    </div>
  </Teleport>

  <Teleport to="body"><div v-if="editor" class="modal-backdrop" @click.self="editor = null"><section class="modal order-editor-modal" role="dialog" aria-modal="true" aria-label="订单编辑"><div class="modal-header"><div><h2>{{ form.id ? '编辑订单' : '新建订单' }}</h2><p>{{ quoteSource ? `报价 ${quoteSource.quote_no} 已预填；保存后自动成单并关联新订单` : '新订单号由后端自动生成' }}</p></div><button class="icon-button" aria-label="关闭" @click="editor = null"><AppIcon name="close" /></button></div><form @submit.prevent="saveOrder">
    <div class="mobile-form-section"><div class="form-grid"><label class="field"><span>款式 *</span><select v-model="form.style" required @change="orderStyleChanged"><option value="">请选择款式</option><option v-for="item in options.styles" :key="item.id" :value="item.id">{{ item.style_no }} · {{ item.name }}</option></select></label><label class="field"><span>客户</span><select v-model="form.customer"><option value="">不指定客户</option><option v-for="item in options.customers" :key="item.id" :value="item.id">{{ item.customer_no }} · {{ item.name }}</option></select></label><label class="field"><span>交期</span><input v-model="form.delivery_date" type="date" /></label><label class="field"><span>订单类型</span><select v-model="form.order_type"><option>自产</option><option>外发</option></select></label><label class="field full"><span>备注</span><textarea v-model="form.remark" rows="2" /></label></div>
      <div class="order-image-editor"><div class="card-header"><h3>备注图片</h3><span>{{ form.images.length }}/9</span></div><div class="order-image-grid"><div v-for="(url,index) in form.images" :key="`${url}-${index}`" class="order-image-thumb"><button type="button" aria-label="预览备注图片" @click="imagePreview = url"><img :src="url" :alt="`备注图片 ${index + 1}`"></button><button type="button" class="order-image-delete" :aria-label="`删除备注图片 ${index + 1}`" @click="form.images.splice(index,1)">×</button></div><label v-if="form.images.length < 9" class="order-image-add" :class="{ uploading: imageUploading }"><input type="file" accept="image/jpeg,image/png,image/gif,image/webp" multiple :disabled="imageUploading" @change="addRemarkImages"><span>{{ imageUploading ? '上传中…' : '+ 上传' }}</span></label></div><p class="editor-hint">支持多选上传，单张不超过 5MB；点击图片预览，最多 9 张。</p></div>
    </div>
    <div class="mobile-form-section"><div class="card-header editor-section-head"><h3>数量矩阵</h3><span class="badge">合计 {{ matrix.total }} 件</span></div>
      <div class="matrix-axis-add"><label>颜色<input v-model="newColor" list="order-color-options" placeholder="输入或选择颜色" @keyup.enter.prevent="addMatrixValue('color')"></label><button type="button" class="button secondary small" @click="addMatrixValue('color')">添加颜色</button></div><div class="matrix-axis-chips"><span v-for="(color,index) in form.colors" :key="color">{{ color }}<button type="button" :aria-label="`删除颜色 ${color}`" @click="removeMatrixValue('color',index)">×</button></span></div>
      <div class="matrix-axis-add"><label>尺码<input v-model="newSize" list="order-size-options" placeholder="输入或选择尺码" @keyup.enter.prevent="addMatrixValue('size')"></label><button type="button" class="button secondary small" @click="addMatrixValue('size')">添加尺码</button></div><div class="matrix-axis-chips"><span v-for="(size,index) in form.sizes" :key="size">{{ size }}<button type="button" :aria-label="`删除尺码 ${size}`" @click="removeMatrixValue('size',index)">×</button></span></div>
      <datalist id="order-color-options"><option v-for="color in orderColorOptions" :key="color" :value="color" /></datalist><datalist id="order-size-options"><option v-for="size in orderSizeOptions" :key="size" :value="size" /></datalist>
      <template v-if="form.colors.length && form.sizes.length"><div class="segmented matrix-view-tabs" aria-label="数量矩阵视图"><button v-for="view in MATRIX_VIEWS" :key="view.key" type="button" :class="{ active: form.matrix_view === view.key }" @click="switchMatrixView(view.key)">{{ view.label }}</button></div>
        <div v-if="form.matrix_view === 'multi'" class="matrix-quick-settings"><label>倍数基数（件）<input v-model.number="form.multi_base" type="number" min="1" step="1" aria-label="倍数基数" @change="changeMultiBase"></label><span>每格填写份数，数量 = 基数 × 份数</span></div><div v-else-if="form.matrix_view === 'array'" class="matrix-quick-settings"><label>每格件数<input v-model.number="form.array_qty" type="number" min="1" step="1" aria-label="每格件数"></label><button type="button" class="button secondary small" @click="fillArray">一键铺满</button></div>
        <div class="order-matrix-scroll"><table class="order-quantity-matrix"><thead><tr><th>{{ form.matrix_view === 'v' ? '尺码＼颜色' : '颜色＼尺码' }}</th><th v-for="(column,index) in matrix.columns" :key="column"><button type="button" :aria-label="`铺满列 ${column}`" title="以本列首个已填值铺满" @click="fillMatrix('column',index)">{{ column }}</button></th><th>合计</th></tr></thead><tbody><tr v-for="(row,rowIndex) in matrix.rows" :key="row.label"><th><button type="button" :aria-label="`铺满行 ${row.label}`" title="以本行首个已填值铺满" @click="fillMatrix('row',rowIndex)">{{ row.label }}</button></th><td v-for="cell in row.values" :key="cell.key" :data-cell="cell.key"><input v-if="form.matrix_view === 'multi'" :key="`${cell.key}-multi`" :value="cell.count || ''" type="number" inputmode="numeric" min="1" step="1" :aria-label="`${cell.key} 份数`" @input="updateMultiCount(cell.key,$event.target.value)"><input v-else :key="`${cell.key}-qty`" :value="cell.qty || ''" :readonly="form.matrix_view === 'array'" type="number" inputmode="numeric" min="1" step="1" :aria-label="`${cell.key} 数量`" @input="updateCell(cell.key,$event.target.value)"><small v-if="form.matrix_view === 'multi' && cell.count">{{ cell.qty }} 件</small></td><td class="numeric">{{ row.total }}</td></tr></tbody></table></div><p class="editor-hint">点行头/列头，以对应行/列首个已填值铺满；横向与纵向仅改变排列，色码数据保持一致。</p>
      </template><p v-else class="editor-hint">请先添加颜色和尺码，再录入数量。</p>
    </div>
    <div class="mobile-form-section"><div class="card-header editor-section-head"><h3>关联工序与工价</h3><div class="editor-section-actions"><select aria-label="按模板填充工序" :disabled="!options.templates.length" @change="applyTemplate"><option value="">{{ options.templates.length ? '按模板填充' : '暂无工序模板' }}</option><option v-for="tpl in options.templates" :key="tpl.id" :value="tpl.id">{{ tpl.name }}</option></select><button type="button" class="button secondary small" @click="addProcess">添加工序</button></div></div><div v-for="(item,index) in form.process_items" :key="index" class="order-process-editor"><select v-model="item.process_id"><option value="">选择工序</option><option v-for="p in options.processes" :key="p.id" :value="p.id">{{ p.name }}</option></select><input v-model="item.price" type="number" min="0" step="0.001" aria-label="工价" /><button type="button" class="icon-button" aria-label="删除工序" @click="form.process_items.splice(index,1)"><AppIcon name="close" :size="16" /></button></div><p class="editor-hint">选填：可用模板带出工序，也可手工添加后逐行填写工价。<template v-if="!options.templates.length">尚未创建工序模板，请先在「基础与系统 → 基础档案 → 工序模板」中新建。</template></p></div><p v-if="editError" class="form-error">{{ editError }}</p><div class="form-actions"><button type="button" class="button secondary" @click="editor = null">取消</button><button class="button primary" :disabled="saving || imageUploading">{{ saving ? '保存中…' : '保存订单' }}</button></div></form></section></div></Teleport>
  <Teleport to="body"><div v-if="imagePreview" class="modal-backdrop order-image-preview-backdrop" @click.self="imagePreview = ''"><section class="modal order-image-preview" role="dialog" aria-modal="true" aria-label="备注图片预览"><button class="icon-button" aria-label="关闭图片预览" @click="imagePreview = ''"><AppIcon name="close" /></button><img :src="imagePreview" alt="订单备注图片大图"></section></div></Teleport>
</template>

<style scoped>
.order-audit-backdrop{z-index:30}.order-audit-modal{max-width:720px}.order-audit-list{display:flex;flex-direction:column;gap:12px}.order-audit-item{padding:15px;border:1px solid var(--line);border-radius:10px;background:#fafbfc}.order-audit-head{display:flex;align-items:center;justify-content:space-between;gap:12px}.order-audit-head time{color:var(--muted);font-size:12px}.order-audit-message{margin:12px 0;color:var(--text-primary);line-height:1.65;overflow-wrap:anywhere}.order-audit-diff{padding:9px 11px;border-radius:7px;background:#f0f2f5;color:var(--text-secondary);font-size:12px;line-height:1.6;overflow-wrap:anywhere}.order-audit-item footer{color:var(--muted);font-size:12px}.audit-price,.audit-revoke{background:var(--danger-soft);color:var(--danger)}.audit-qty,.audit-process{background:#eaf2ff;color:#2864b4}.audit-assist{background:var(--good-soft);color:var(--good)}.audit-load-more{display:flex;margin:16px auto 0}.audit-end{text-align:center;color:var(--muted);font-size:12px}
.orders-head-actions{display:flex;gap:8px}.mobile-list-more,.mobile-task-steps,.mobile-detail-copy,.mobile-order-cells{display:none}.order-process-editor{display:grid;grid-template-columns:1fr 130px 36px;gap:8px;margin-bottom:8px}.order-process-editor input,.order-process-editor select{min-width:0}.editor-section-head{margin-top:20px}.editor-section-actions{display:flex;align-items:center;gap:8px}.editor-section-actions select{width:auto;max-width:220px}.mobile-form-section{min-width:0}.editor-hint{margin:12px 0 0;color:var(--muted);font-size:12px}.mobile-more-error button{border:0;background:none;color:var(--orange);padding:4px}
.order-image-editor{margin-top:16px}.order-image-grid{display:grid;grid-template-columns:repeat(5,82px);gap:10px}.order-image-thumb,.order-image-add{position:relative;width:82px;height:82px;border:1px solid var(--line);border-radius:9px;overflow:hidden}.order-image-thumb>button:first-child,.order-image-thumb img{display:block;width:100%;height:100%;border:0;padding:0;object-fit:cover}.order-image-delete{position:absolute;right:3px;top:3px;width:22px;height:22px;min-height:0;padding:0;border:0;border-radius:50%;background:rgba(0,0,0,.65);color:#fff}.order-image-add input{position:absolute;inset:0;opacity:0;cursor:pointer}.order-image-add span{display:flex;align-items:center;justify-content:center;height:100%;color:var(--orange);font-size:13px}.order-image-add.uploading{opacity:.6}.order-image-preview-backdrop{z-index:50}.order-image-preview{position:relative;max-width:900px;background:#111}.order-image-preview>img{display:block;max-width:100%;max-height:80vh;object-fit:contain}.order-image-preview>.icon-button{position:absolute;right:8px;top:8px;background:rgba(255,255,255,.85)}.order-remark-images{display:flex;gap:8px;flex-wrap:wrap;margin:14px 0}.order-remark-images button{width:76px;height:76px;padding:0;border:1px solid var(--line);border-radius:8px;overflow:hidden}.order-remark-images img{width:100%;height:100%;object-fit:cover}.order-production-links{margin-top:18px}.order-production-list{display:grid;gap:8px}.order-production-link{display:flex;align-items:center;justify-content:space-between;gap:12px;width:100%;padding:12px;border:1px solid var(--line);border-radius:9px;background:#fafbfc;text-align:left}.order-production-link span{display:flex;flex-direction:column;gap:4px}.order-production-link small{color:var(--muted)}.order-production-link>span:last-child{flex:none;color:var(--orange);font-size:12px}.matrix-axis-add{display:flex;align-items:end;gap:8px;margin-top:12px}.matrix-axis-add label{display:flex;flex-direction:column;gap:5px;color:var(--muted);font-size:12px}.matrix-axis-add input{min-width:200px;min-height:36px}.matrix-axis-chips{display:flex;gap:7px;flex-wrap:wrap;margin-top:8px}.matrix-axis-chips>span{display:inline-flex;align-items:center;gap:5px;padding:5px 8px;border-radius:999px;background:var(--orange-soft);color:var(--orange-dark);font-size:12px}.matrix-axis-chips button{border:0;background:none;color:inherit;padding:0}.matrix-view-tabs{margin-top:18px}.matrix-view-tabs button{flex:1}.matrix-quick-settings{display:flex;align-items:center;gap:12px;margin-top:14px;padding:10px 12px;background:#f7f8fa;border-radius:8px}.matrix-quick-settings label{display:flex;align-items:center;gap:8px;font-size:13px}.matrix-quick-settings input{width:110px;min-height:34px}.matrix-quick-settings span{color:var(--muted);font-size:12px}.order-matrix-scroll{margin-top:12px;overflow-x:auto}.order-quantity-matrix{min-width:480px}.order-quantity-matrix th button{width:100%;border:0;background:transparent;color:inherit;font-weight:600;cursor:pointer}.order-quantity-matrix td{min-width:90px;text-align:center}.order-quantity-matrix td input{width:76px;min-height:36px;text-align:center}.order-quantity-matrix td small{display:block;color:var(--muted);font-size:10px}
.order-style-card{display:flex;gap:16px;margin:18px 0;padding:14px;border:1px solid var(--line);border-radius:10px;background:#fafbfc}.order-style-cover{width:132px;height:132px;flex:none;border-radius:9px;object-fit:cover}.order-style-main{flex:1;min-width:0}.order-craft-note{white-space:pre-wrap;line-height:1.7;color:var(--text-secondary);font-size:13px}.order-craft-images{display:flex;flex-wrap:wrap;gap:8px;margin-top:10px}.order-craft-images button{width:72px;height:72px;padding:0;border:1px solid var(--line);border-radius:8px;overflow:hidden}.order-craft-images img{width:100%;height:100%;object-fit:cover}.order-craft-video{display:block;width:min(100%,420px);max-height:240px;margin-top:12px;background:#111;border-radius:8px}.order-production-status{align-items:flex-end}.order-production-status b{font-size:12px;color:var(--orange)}.order-production-status b.locked{color:var(--danger)}
@media(max-width:760px){
  .orders-welcome{flex-direction:row;align-items:center;margin-bottom:12px}.orders-welcome>div:first-child{display:none}.orders-head-actions{width:100%;justify-content:flex-end}.orders-refresh{display:none}.orders-head-actions .button.primary{margin-left:auto}
  .orders-filter{display:flex;flex-direction:column;align-items:stretch;gap:10px;margin-bottom:12px}.orders-filter .segmented{display:flex;width:100%;padding:0;background:#fff;border-radius:12px;box-shadow:0 2px 8px rgba(31,35,41,.06);overflow:visible}.orders-filter .segmented button{position:relative;flex:1;min-width:0;padding:13px 2px 14px;border-radius:0;background:transparent;box-shadow:none;color:var(--text-secondary);font-size:13px}.orders-filter .segmented button.active{color:var(--orange);background:transparent;box-shadow:none}.orders-filter .segmented button.active:after{content:'';position:absolute;bottom:4px;left:50%;width:24px;height:3px;transform:translateX(-50%);border-radius:99px;background:var(--orange)}.orders-filter .search-box{width:100%;min-width:0;max-width:none}.orders-filter .search-box input{min-height:42px;border:0;border-radius:999px;box-shadow:0 2px 8px rgba(31,35,41,.06)}
  .orders-list-card{padding:0;background:transparent;border-radius:0;box-shadow:none;overflow:visible}.orders-list-card .mobile-cards{gap:10px}.orders-list-card .mobile-row{border:0;border-radius:12px;box-shadow:0 2px 8px rgba(31,35,41,.06);padding:14px}.orders-list-card .mobile-row-head{margin-bottom:8px}.orders-list-card .mobile-row-title{font-size:16px;overflow-wrap:anywhere}.order-mobile-line{display:flex;align-items:center;justify-content:space-between;gap:8px;margin:8px 0 12px;color:var(--muted);font-size:12px}.order-delivery{display:inline-flex;align-items:center;gap:5px;text-align:right}.order-lamp{display:inline-block;width:8px;height:8px;flex:none;border-radius:50%}.lamp-green{background:var(--good)}.lamp-yellow{background:var(--warning)}.lamp-red{background:var(--danger)}.order-mobile-card .row-progress{margin-top:0;min-width:0}.order-mobile-card .row-progress .meter-track{height:7px;background:#F2F3F5}.order-mobile-card .meter-fill{background:var(--orange)}.order-mobile-card .meter-fill.progress-low{background:var(--danger)}.order-mobile-total{margin-top:10px;color:var(--muted);font-size:12px;font-variant-numeric:tabular-nums}
  .desktop-pagination,.desktop-detail-matrix{display:none}.mobile-list-more{display:block;padding:4px 0 14px;text-align:center}.mobile-load-more{display:inline-flex;align-items:center;justify-content:center;gap:7px;min-width:132px;min-height:40px;padding:8px 20px;border:1px solid var(--line);border-radius:999px;background:#fff;color:var(--text-secondary);font-size:13px}.mobile-no-more,.mobile-more-error{margin:0;padding:10px 0;color:var(--muted);font-size:12px}.mobile-more-error{color:var(--danger)}
  .mobile-task-steps{display:flex;list-style:none;margin:0 0 14px;padding:0;background:#F5F6F8;border-radius:10px;overflow:hidden}.mobile-task-steps li{position:relative;flex:1;min-width:0;padding:9px 2px;text-align:center;color:var(--muted);font-size:11px}.mobile-task-steps li.active{color:var(--orange);font-weight:600}.mobile-task-steps li+li:before{content:'›';position:absolute;left:-3px;color:#C9CDD4}.mobile-detail-copy{display:flex;flex-direction:column;gap:5px;margin:0 0 12px;color:var(--text-secondary);font-size:12px;overflow-wrap:anywhere}.order-detail-modal .detail-grid{gap:8px;margin-bottom:12px}.order-detail-modal .detail-item{padding:10px 8px;min-width:0}.order-detail-modal .detail-item strong{font-size:12px;overflow-wrap:anywhere}.order-detail-modal .detail-item:last-child:nth-child(3){grid-column:1/-1}.order-detail-modal .order-progress-stats .detail-item:last-child:nth-child(3){grid-column:auto}.order-detail-modal .section-note{line-height:1.6}.mobile-order-cells{display:block;margin-top:14px}.mobile-section-heading{display:flex;align-items:center;justify-content:space-between;gap:8px}.mobile-section-heading h3{margin:0;font-size:15px}.mobile-matrix-legend{margin:6px 0 10px;color:var(--muted);font-size:11px}.mobile-matrix-card{padding:12px;margin-top:8px;border:1px solid var(--line);border-radius:10px;background:#FAFBFC}.mobile-matrix-card.low{border-color:#FECACA;background:#FFF1F0}.mobile-matrix-card>div{display:flex;justify-content:space-between;gap:10px;font-size:13px}.mobile-matrix-card>div b{color:var(--orange)}.mobile-matrix-card.low>div b{color:var(--danger)}.mobile-matrix-card dl{display:grid;grid-template-columns:repeat(3,1fr);margin:12px 0 0;text-align:center}.mobile-matrix-card dl div+div{border-left:1px solid var(--line)}.mobile-matrix-card dt{color:var(--muted);font-size:10px}.mobile-matrix-card dd{margin:4px 0 0;font-size:13px;font-weight:600}.mobile-empty-copy{text-align:center;color:var(--muted);font-size:12px}
  .order-editor-modal .mobile-form-section{padding:12px 0;border-bottom:1px solid var(--line)}.order-editor-modal .mobile-form-section:first-child{padding-top:0}.order-editor-modal .form-grid{gap:12px}.order-editor-modal .editor-section-head{margin-top:0;margin-bottom:12px}.order-editor-modal .editor-section-actions{flex-wrap:wrap;justify-content:flex-end;max-width:65%}.order-editor-modal .editor-section-actions select{max-width:150px;min-height:32px;padding:5px 8px;font-size:12px}.order-item-editor{grid-template-columns:minmax(0,1fr) minmax(0,1fr) 76px 32px;gap:6px;padding:9px 0;margin:0;border-bottom:1px dashed var(--line)}.order-item-editor input{min-height:38px;padding:8px}.order-process-editor{grid-template-columns:minmax(0,1fr) 88px 32px;gap:6px;padding:9px 0;margin:0;border-bottom:1px dashed var(--line)}.order-process-editor input,.order-process-editor select{min-height:38px;padding:8px}.order-editor-modal .form-actions .button{flex:1}.order-image-grid{grid-template-columns:repeat(3,82px)}.matrix-axis-add label{flex:1}.matrix-axis-add input{width:100%;min-width:0}.matrix-quick-settings{align-items:flex-start;flex-wrap:wrap}.order-production-link{align-items:flex-start}.order-production-link>span:last-child{max-width:45%;text-align:right}
}
@media(max-width:350px){.order-item-editor{grid-template-columns:minmax(0,1fr) minmax(0,1fr) 66px 30px;gap:4px}.order-item-editor input,.order-process-editor input,.order-process-editor select{padding-inline:6px;font-size:12px}.order-process-editor{grid-template-columns:minmax(0,1fr) 76px 30px;gap:4px}.order-mobile-line{align-items:flex-start;flex-direction:column;gap:5px}.order-delivery{text-align:left}}
</style>
