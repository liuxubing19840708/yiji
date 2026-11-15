<script setup>
import { onMounted, reactive, ref } from 'vue'
import { api, rows } from '../api'
import { dateTime, number } from '../format'
import { uniqueOptions } from '../spec-options'
import AppIcon from '../components/AppIcon.vue'
import PageState from '../components/PageState.vue'
import CommercialModal from '../components/CommercialModal.vue'
import CommercialPagination from '../components/CommercialPagination.vue'
import ShipmentPrintDialog from '../components/ShipmentPrintDialog.vue'

const emit = defineEmits(['toast'])
const loading = ref(true), error = ref(''), listData = ref({ count: 0 })
const mobileRows = ref([]), loadingMore = ref(false), moreError = ref(''), hasMore = ref(false)
const filters = reactive({ status: '', customer_id: '', keyword: '', page: 1 })
const customers = ref([]), orders = ref([]), optionsLoaded = ref(false)
const detail = ref(null), detailLoading = ref(false), detailError = ref(''), extrasError = ref('')
const undelivered = ref(null), boxes = ref([])
const form = ref(null), saving = ref(false), formError = ref(''), shipmentSpecItems = ref([])
const actionBusy = ref(false), boxOpen = ref(false), boxSaving = ref(false), boxError = ref(''), printing = ref(false)
const boxForm = reactive({ box_no: '', color: '', size: '', qty: '0', gross_wt: '', net_wt: '', box_spec: '' })
let searchTimer, optionsPromise, listRequest = 0

const statusText = (s) => ({ draft: '草稿', confirmed: '全部出货', partial: '部分出货', shipped: '已发运', signed: '已签收' }[s] || s || '—')
const statusClass = (s) => ({ confirmed: 'done', signed: 'done', partial: 'producing', draft: '' }[s] || '')
const amt = (v) => v === null || v === undefined || v === '' ? '—' : String(v)
function notify(message) { emit('toast', message) }

async function load() {
  const serial = ++listRequest
  loading.value = true; error.value = ''; moreError.value = ''; loadingMore.value = false; hasMore.value = false; mobileRows.value = []
  try {
    const result = await api('/shipments', { params: { ...filters, page_size: 20 } })
    if (serial !== listRequest) return
    listData.value = result
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
    const result = await api('/shipments', { params: { ...filters, page, page_size: 20 } })
    if (serial !== listRequest) return
    listData.value = result
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
function changePage(page) { filters.page = page; load() }
async function ensureOptions() {
  if (optionsLoaded.value) return
  if (!optionsPromise) {
    optionsPromise = Promise.all([
      api('/customers', { params: { page: 1, page_size: 100 } }),
      api('/orders', { params: { page: 1, page_size: 100 } }),
    ]).then(([c, o]) => { customers.value = rows(c); orders.value = rows(o); optionsLoaded.value = true })
      .catch((e) => { optionsPromise = null; throw e })
  }
  return optionsPromise
}
async function loadFilterOptions() {
  try { await ensureOptions() } catch { /* 列表主体仍可查询；建单时显示具体错误 */ }
}

async function openDetail(item) {
  detail.value = item; detailLoading.value = true; detailError.value = ''; extrasError.value = ''
  undelivered.value = null; boxes.value = []; boxOpen.value = false
  try {
    detail.value = await api(`/shipments/${item.id}`)
    await loadExtras(item.id)
  } catch (e) { detailError.value = e.message }
  finally { detailLoading.value = false }
}
async function loadExtras(id) {
  extrasError.value = ''
  const [matrix, packing] = await Promise.allSettled([
    api(`/shipments/${id}/undelivered`), api(`/shipments/${id}/boxes`),
  ])
  if (matrix.status === 'fulfilled') undelivered.value = matrix.value
  if (packing.status === 'fulfilled') boxes.value = packing.value || []
  const failed = [matrix, packing].find((r) => r.status === 'rejected')
  if (failed) extrasError.value = failed.reason?.message || '部分附属信息读取失败'
}
function closeDetail() { detail.value = null; detailError.value = ''; boxOpen.value = false; printing.value = false }

function blankItem() { return { color: '', size: '', qty: '' } }
function specColors(items) { return uniqueOptions((items || []).map((item) => item.color)) }
function specSizes(items, color) { return uniqueOptions((items || []).filter((item) => !color || item.color === color).map((item) => item.size)) }
function specColorChanged(item, specs) { const sizes=specSizes(specs,item.color);if(!sizes.includes(item.size))item.size=sizes.length===1?sizes[0]:'' }
function nextBoxNo() { const used=new Set(boxes.value.map((box)=>box.box_no));let index=boxes.value.length+1;while(used.has(`BOX-${String(index).padStart(3,'0')}`))index+=1;return `BOX-${String(index).padStart(3,'0')}` }
async function openCreate() {
  try { await ensureOptions() } catch (e) { notify(e.message); return }
  formError.value = ''
  shipmentSpecItems.value = []
  form.value = { id: null, status: 'draft', shipment_no: '', order: '', customer: '', delivery_date: '', ship_date: '', logistics_no: '', remark: '', items: [blankItem()] }
}
async function openEdit(shipment) {
  try { await ensureOptions() } catch (e) { notify(e.message); return }
  formError.value = ''
  const selectedOrder=orders.value.find((order)=>order.id===Number(shipment.order))
  shipmentSpecItems.value=selectedOrder?.items?.length?selectedOrder.items:(shipment.items||[])
  form.value = {
    id: shipment.id, status: shipment.status, shipment_no: shipment.shipment_no,
    order: shipment.order || '', customer: shipment.customer || '',
    delivery_date: shipment.delivery_date || '', ship_date: shipment.ship_date || '',
    logistics_no: shipment.logistics_no || '', remark: shipment.remark || '',
    items: shipment.items?.length ? shipment.items.map((it) => ({ color: it.color, size: it.size, qty: String(it.qty) })) : [blankItem()],
  }
}
async function orderChanged() {
  const f = form.value
  const selected = orders.value.find((o) => o.id === Number(f.order))
  shipmentSpecItems.value = selected?.items || []
  if (!selected) return
  f.customer = selected.customer || ''
  if (!f.id) f.delivery_date = selected.delivery_date || ''
  formError.value = ''
  try {
    const order = await api(`/orders/${selected.id}`)
    if (!form.value || Number(form.value.order) !== selected.id) return
    form.value.customer = order.customer || form.value.customer
    shipmentSpecItems.value = order.items || []
    form.value.items = order.items?.length ? order.items.map((it) => ({ color: it.color, size: it.size, qty: String(it.qty) })) : [blankItem()]
  } catch (e) { formError.value = `订单明细读取失败，请重新选择订单：${e.message}` }
}
function addItem() { const used=new Set(form.value.items.map((item)=>`${item.color}|${item.size}`));const next=shipmentSpecItems.value.find((item)=>!used.has(`${item.color}|${item.size}`));form.value.items.push(next?{color:next.color,size:next.size,qty:String(next.qty||'')}:blankItem()) }
function removeItem(index) { form.value.items.splice(index, 1); if (!form.value.items.length) form.value.items.push(blankItem()) }
function validItems(items) {
  const seen = new Set()
  for (let i = 0; i < items.length; i++) {
    const it = items[i], qty = Number(it.qty)
    if (!String(it.color || '').trim()) return `第 ${i + 1} 行缺少颜色`
    if (!String(it.size || '').trim()) return `第 ${i + 1} 行缺少尺码`
    if (!Number.isInteger(qty) || qty <= 0) return `第 ${i + 1} 行数量须为正整数`
    const key = `${it.color.trim()}/${it.size.trim()}`
    if (shipmentSpecItems.value.length && !shipmentSpecItems.value.some((spec) => spec.color === it.color && spec.size === it.size)) return `第 ${i + 1} 行颜色与尺码须按订单组合选择`
    if (seen.has(key)) return `同单同色码重复：${key}`
    seen.add(key)
  }
  return ''
}
async function saveShipment() {
  const f = form.value
  if (!f || saving.value) return
  formError.value = ''
  if (f.status === 'draft') {
    if (!f.order) { formError.value = '请选择订单'; return }
    if (!f.customer) { formError.value = '请选择客户'; return }
    const invalid = validItems(f.items)
    if (invalid) { formError.value = invalid; return }
  }
  saving.value = true
  const common = { ship_date: f.ship_date || null, logistics_no: f.logistics_no.trim(), remark: f.remark.trim() }
  const body = f.status === 'draft' ? {
    ...common, order: Number(f.order), customer: Number(f.customer), delivery_date: f.delivery_date || null,
    items: f.items.map((it) => ({ color: it.color.trim(), size: it.size.trim(), qty: Number(it.qty) })),
  } : common
  try {
    const saved = await api(f.id ? `/shipments/${f.id}` : '/shipments', { method: f.id ? 'PUT' : 'POST', body })
    notify(f.id ? '出货单已保存' : `出货单 ${saved.shipment_no} 已建立`)
    form.value = null; await load(); await openDetail(saved)
  } catch (e) { formError.value = e.message }
  finally { saving.value = false }
}
async function auditShipment() {
  const shp = detail.value
  if (!shp || actionBusy.value) return
  if (!window.confirm(`审核 ${shp.shipment_no}？系统将校验未交量、成品库存和色码定价，通过后自动生成应收。`)) return
  actionBusy.value = true; detailError.value = ''
  try {
    const result = await api(`/shipments/${shp.id}/audit`, { method: 'POST', body: {} })
    const rec = result.receivable
    notify(`审核通过，应收 ${rec.receivable_no} 已生成（${amt(rec.amount)} 元）`)
    await Promise.all([load(), openDetail(result.shipment)])
  } catch (e) { detailError.value = e.message; notify(e.message) }
  finally { actionBusy.value = false }
}
async function voidShipment() {
  const shp = detail.value
  if (!shp || actionBusy.value) return
  if (!window.confirm(`作废 ${shp.shipment_no}？作废后单据下架，已出量回到未交池，已生成的应收不会自动撤销。`)) return
  actionBusy.value = true
  try {
    const result = await api(`/shipments/${shp.id}/void`, { method: 'POST', body: {} })
    const linked = (result.receivables || []).map((r) => `${r.receivable_no}：应收 ${amt(r.amount)}，已收 ${amt(r.received_amount)}`).join('\n')
    window.alert([result.warning, linked].filter(Boolean).join('\n'))
    closeDetail(); await load(); notify('出货单已作废')
  } catch (e) { detailError.value = e.message; notify(e.message) }
  finally { actionBusy.value = false }
}
async function deleteShipment() {
  const shp = detail.value
  if (!shp || shp.status !== 'draft' || actionBusy.value) return
  if (!window.confirm(`删除草稿 ${shp.shipment_no}？删除后不再显示，已审核单只能作废。`)) return
  actionBusy.value = true
  try { await api(`/shipments/${shp.id}`, { method: 'DELETE' }); closeDetail(); await load(); notify('草稿已删除') }
  catch (e) { detailError.value = e.message; notify(e.message) }
  finally { actionBusy.value = false }
}
function openBox() {
  Object.assign(boxForm, { box_no: nextBoxNo(), color: '', size: '', qty: '0', gross_wt: '', net_wt: '', box_spec: '' })
  boxError.value = ''; boxOpen.value = true
}
async function saveBox() {
  if (!boxForm.box_no.trim()) { boxError.value = '请填写箱号'; return }
  const qty = Number(boxForm.qty)
  if (!Number.isInteger(qty) || qty < 0) { boxError.value = '装箱数量须为不小于 0 的整数'; return }
  boxSaving.value = true; boxError.value = ''
  const body = { box_no: boxForm.box_no.trim(), color: boxForm.color.trim(), size: boxForm.size.trim(), qty, box_spec: boxForm.box_spec.trim() }
  if (boxForm.gross_wt !== '') body.gross_wt = boxForm.gross_wt
  if (boxForm.net_wt !== '') body.net_wt = boxForm.net_wt
  try {
    await api(`/shipments/${detail.value.id}/boxes`, { method: 'POST', body })
    boxOpen.value = false; notify('装箱行已添加')
    const fresh = await api(`/shipments/${detail.value.id}`); detail.value = fresh
    await loadExtras(fresh.id); await load()
  } catch (e) { boxError.value = e.message }
  finally { boxSaving.value = false }
}
async function deleteBox(box) {
  if (!window.confirm(`删除箱 ${box.box_no}？箱数汇总会自动更新。`)) return
  actionBusy.value = true
  try {
    await api(`/shipments/${detail.value.id}/boxes/${box.id}`, { method: 'DELETE' })
    const fresh = await api(`/shipments/${detail.value.id}`); detail.value = fresh
    await loadExtras(fresh.id); await load(); notify('装箱行已删除')
  } catch (e) { extrasError.value = e.message; notify(e.message) }
  finally { actionBusy.value = false }
}

onMounted(() => { load(); loadFilterOptions() })
</script>

<template>
  <div class="welcome shipments-welcome"><div><h2>出货管理</h2><p>出货明细、订单未交量、审核应收联动与装箱统一管理。</p></div><div class="commercial-actions"><button class="button secondary shipments-refresh" @click="load"><AppIcon name="refresh" :size="16" />刷新</button><button class="button primary" @click="openCreate">新建出货单</button></div></div>
  <div class="filter-bar shipments-filter">
    <div class="segmented"><button v-for="s in [['','全部'],['draft','草稿'],['confirmed','全部出货'],['partial','部分出货'],['shipped','已发运'],['signed','已签收']]" :key="s[0]" :class="{ active: filters.status === s[0] }" @click="filters.status = s[0]; filters.page = 1; load()">{{ s[1] }}</button></div>
    <div class="shipments-query"><select v-model="filters.customer_id" aria-label="筛选客户" @change="filters.page = 1; load()"><option value="">全部客户</option><option v-for="c in customers" :key="c.id" :value="c.id">{{ c.name }}</option></select><label class="search-box"><AppIcon name="search" :size="17" /><input v-model="filters.keyword" placeholder="出货单号、订单、客户或物流单号" @input="search" @keyup.enter="filters.page = 1; load()" /></label><button class="shipments-search-button" type="button" @click="filters.page = 1; load()">查询</button></div>
  </div>
  <section class="card table-card shipments-list-card">
    <PageState :loading="loading" :error="error" :empty="!rows(listData).length" empty-text="没有符合条件的出货单" @retry="load">
      <div class="table-wrap desktop-table"><table><thead><tr><th>出货单 / 订单</th><th>客户</th><th class="numeric">出货数量</th><th class="numeric">箱数</th><th>发货日期</th><th>物流单号</th><th>状态</th><th></th></tr></thead><tbody><tr v-for="shp in rows(listData)" :key="shp.id" class="clickable" @click="openDetail(shp)"><td><span class="cell-title">{{ shp.shipment_no }}</span><small class="cell-subtitle">{{ shp.order_no }}</small></td><td>{{ shp.customer_name || '—' }}</td><td class="numeric">{{ number(shp.total_qty) }}</td><td class="numeric">{{ shp.boxes || 0 }}</td><td>{{ shp.ship_date || '未设置' }}</td><td>{{ shp.logistics_no || '—' }}</td><td><span class="badge" :class="statusClass(shp.status)">{{ shp.status_display || statusText(shp.status) }}</span></td><td><AppIcon name="chevron" :size="16" /></td></tr></tbody></table></div>
      <div class="mobile-cards"><button v-for="shp in mobileRows" :key="shp.id" class="mobile-row shipment-mobile-card" @click="openDetail(shp)"><div class="mobile-row-head"><div class="shipment-card-main"><span class="mobile-row-title">{{ shp.shipment_no }}</span><small class="cell-subtitle">{{ shp.customer_name || '—' }}<template v-if="shp.order_no"> · {{ shp.order_no }}</template></small></div><span class="badge" :class="statusClass(shp.status)">{{ shp.status_display || statusText(shp.status) }}</span></div><div class="shipment-mobile-meta"><span>{{ number(shp.total_qty) }} 件 · {{ shp.boxes || 0 }} 箱</span><span v-if="shp.ship_date">{{ shp.ship_date }}</span></div><span v-if="shp.logistics_no" class="shipment-mobile-sub">物流：{{ shp.logistics_no }}</span><span v-if="shp.remark" class="shipment-mobile-sub">备注：{{ shp.remark }}</span></button></div>
    </PageState>
    <CommercialPagination class="desktop-pagination" :page="filters.page" :count="listData.count || 0" :disabled="loading" @change="changePage" />
    <div v-if="mobileRows.length" class="mobile-list-more"><p v-if="moreError" class="mobile-more-error" role="alert">{{ moreError }} <button type="button" @click="loadMore">重试</button></p><button v-else-if="hasMore" class="mobile-load-more" type="button" :disabled="loadingMore" @click="loadMore"><span v-if="loadingMore" class="spinner" />{{ loadingMore ? '加载中…' : '加载更多' }}</button><p v-else class="mobile-no-more">没有更多了</p></div>
  </section>

  <CommercialModal v-if="detail" wide :title="detail.shipment_no" :subtitle="`${detail.order_no || ''} · ${detail.customer_name || ''}`" @close="closeDetail">
    <div class="shipment-detail-content"><PageState :loading="detailLoading" :error="detailError && !detail.items ? detailError : ''" @retry="openDetail(detail)">
      <ol class="mobile-task-steps"><li class="active">核对明细</li><li>未交数量</li><li>装箱</li><li>审核打印</li></ol>
      <div v-if="detailError" class="form-error commercial-error">{{ detailError }}</div>
      <div class="detail-grid"><div class="detail-item"><small>状态</small><strong>{{ detail.status_display || statusText(detail.status) }}</strong></div><div class="detail-item"><small>出货总数量</small><strong>{{ number(detail.total_qty) }}</strong></div><div class="detail-item"><small>箱数</small><strong>{{ detail.boxes || 0 }}</strong></div><div class="detail-item"><small>交货 / 发货日期</small><strong>{{ detail.delivery_date || '—' }} / {{ detail.ship_date || '—' }}</strong></div><div class="detail-item"><small>物流单号</small><strong>{{ detail.logistics_no || '—' }}</strong></div><div class="detail-item"><small>审核信息</small><strong>{{ detail.audited_by_name || '未审核' }}<span v-if="detail.audited_at"> · {{ dateTime(detail.audited_at) }}</span></strong></div></div>
      <p v-if="detail.remark" class="section-note commercial-top-note">备注：{{ detail.remark }}</p>
      <div class="commercial-actions commercial-wrap commercial-section shipment-detail-actions"><button class="button secondary small" @click="printing = true">打印 / 打印历史</button><button class="button secondary small" @click="openEdit(detail)">编辑{{ detail.status === 'draft' ? '单据' : '物流信息' }}</button><button v-if="detail.status === 'draft'" class="button primary small" :disabled="actionBusy" @click="auditShipment"><AppIcon name="check" :size="15" />审核并生成应收</button><button v-if="detail.status === 'draft'" class="button danger small" :disabled="actionBusy" @click="deleteShipment">删除草稿</button><button class="button danger small" :disabled="actionBusy" @click="voidShipment">作废出货单</button></div>
      <div v-if="extrasError" class="form-error commercial-error">{{ extrasError }}</div>
      <section class="commercial-section"><div class="card-header"><div><h3>出货明细</h3><p>审核时按订单色码校验数量与定价</p></div></div><div class="table-wrap desktop-shipment-table"><table><thead><tr><th>颜色</th><th>尺码</th><th class="numeric">数量</th></tr></thead><tbody><tr v-for="it in detail.items || []" :key="it.id"><td>{{ it.color }}</td><td>{{ it.size }}</td><td class="numeric">{{ number(it.qty) }}</td></tr><tr v-if="!detail.items?.length"><td colspan="3">暂无明细</td></tr></tbody></table></div><div class="mobile-shipment-items"><article v-for="(it, index) in detail.items || []" :key="`mobile-item-${it.id}`"><span>第 {{ index + 1 }} 行</span><strong>{{ it.color }} / {{ it.size }}</strong><b>{{ number(it.qty) }} 件</b></article><p v-if="!detail.items?.length">暂无明细</p></div></section>
      <section class="commercial-section"><div class="card-header"><div><h3>订单未交量</h3><p>订单量 − 已审核出货量；本单草稿不预占</p></div></div><div v-if="undelivered" class="mobile-undelivered-total">合计 订单 {{ number(undelivered.total_qty) }} · 已出 {{ number(undelivered.total_shipped) }} · 未交 <strong>{{ number(undelivered.total_undelivered) }}</strong></div><div class="table-wrap desktop-shipment-table"><table><thead><tr><th>颜色 / 尺码</th><th class="numeric">订单量</th><th class="numeric">已出量</th><th class="numeric">未交量</th></tr></thead><tbody><tr v-for="it in undelivered?.items || []" :key="`${it.color}-${it.size}`"><td>{{ it.color }} / {{ it.size }}</td><td class="numeric">{{ number(it.qty) }}</td><td class="numeric">{{ number(it.shipped_qty) }}</td><td class="numeric cell-title">{{ number(it.undelivered_qty) }}</td></tr><tr v-if="!undelivered"><td colspan="4" class="page-state"><span class="spinner" />正在读取未交量…</td></tr><tr v-else-if="!undelivered.items?.length"><td colspan="4">订单暂无色码明细</td></tr></tbody></table></div><div class="mobile-undelivered-items"><div v-if="!undelivered" class="page-state"><span class="spinner" />正在读取未交量…</div><article v-for="it in undelivered?.items || []" :key="`mobile-undelivered-${it.color}-${it.size}`"><strong>{{ it.color }} / {{ it.size }}</strong><dl><div><dt>订单量</dt><dd>{{ number(it.qty) }}</dd></div><div><dt>已出量</dt><dd>{{ number(it.shipped_qty) }}</dd></div><div><dt>未交量</dt><dd>{{ number(it.undelivered_qty) }}</dd></div></dl></article><p v-if="undelivered && !undelivered.items?.length">订单暂无色码明细</p></div></section>
      <section class="commercial-section"><div class="card-header"><div><h3>装箱明细</h3><p>箱数由后端按装箱行自动汇总</p></div><button class="button secondary small" @click="openBox">新增装箱行</button></div><div class="table-wrap desktop-shipment-table"><table><thead><tr><th>箱号</th><th>颜色 / 尺码</th><th class="numeric">数量</th><th class="numeric">毛重 / 净重 kg</th><th>箱规</th><th></th></tr></thead><tbody><tr v-for="box in boxes" :key="box.id"><td class="cell-title">{{ box.box_no }}</td><td>{{ box.color || '—' }} / {{ box.size || '—' }}</td><td class="numeric">{{ number(box.qty) }}</td><td class="numeric">{{ amt(box.gross_wt) }} / {{ amt(box.net_wt) }}</td><td>{{ box.box_spec || '—' }}</td><td><button class="button danger small" :disabled="actionBusy" @click="deleteBox(box)">删除</button></td></tr><tr v-if="!boxes.length"><td colspan="6" class="page-state">暂无装箱行</td></tr></tbody></table></div><div class="mobile-box-items"><article v-for="box in boxes" :key="`mobile-box-${box.id}`"><div><strong>{{ box.box_no }}</strong><button class="button danger small" :disabled="actionBusy" @click="deleteBox(box)">删除</button></div><p>{{ box.color || '—' }} / {{ box.size || '—' }} · {{ number(box.qty) }} 件</p><p>毛重 {{ amt(box.gross_wt) }} · 净重 {{ amt(box.net_wt) }}<template v-if="box.box_spec"> · 箱规 {{ box.box_spec }}</template></p></article><p v-if="!boxes.length" class="mobile-empty-copy">暂无装箱行</p></div></section>
    </PageState></div>
  </CommercialModal>

  <CommercialModal v-if="form" wide :title="form.id ? `编辑 ${form.shipment_no}` : '新建出货单'" :subtitle="form.status === 'draft' ? '草稿可修改订单、客户与全部明细' : '已审核单仅可修改物流单号、发货日期与备注'" @close="form = null">
    <form class="shipment-editor-form" @submit.prevent="saveShipment"><div class="form-grid"><label class="field"><span>订单</span><select v-model="form.order" :disabled="form.status !== 'draft'" required @change="orderChanged"><option value="">请选择当前公司订单</option><option v-for="o in orders" :key="o.id" :value="o.id">{{ o.order_no }} · {{ o.customer_name || '无客户' }} · {{ o.style_no }}</option></select></label><label class="field"><span>客户</span><select v-model="form.customer" :disabled="form.status !== 'draft'" required><option value="">请选择当前公司客户</option><option v-for="c in customers" :key="c.id" :value="c.id">{{ c.name }}</option></select></label><label class="field"><span>交货日期</span><input v-model="form.delivery_date" type="date" :disabled="form.status !== 'draft'" /></label><label class="field"><span>发货日期</span><input v-model="form.ship_date" type="date" /></label><label class="field full"><span>物流单号</span><input v-model="form.logistics_no" maxlength="64" placeholder="可稍后补录" /></label><label class="field full"><span>备注</span><textarea v-model="form.remark" maxlength="255" rows="2" /></label></div>
      <section v-if="form.status === 'draft'" class="commercial-section"><div class="card-header"><div><h3>出货色码明细</h3><p>选择订单后自动带出，可调整本次出货数量</p></div><button class="button secondary small" type="button" @click="addItem">添加一行</button></div><div v-for="(it, index) in form.items" :key="index" class="commercial-item-grid"><label class="field"><span>颜色</span><select v-model="it.color" required @change="specColorChanged(it, shipmentSpecItems)"><option value="">选择颜色</option><option v-for="color in specColors(shipmentSpecItems)" :key="color" :value="color">{{ color }}</option></select></label><label class="field"><span>尺码</span><select v-model="it.size" required><option value="">选择尺码</option><option v-for="size in specSizes(shipmentSpecItems, it.color)" :key="size" :value="size">{{ size }}</option></select></label><label class="field"><span>数量</span><input v-model="it.qty" type="number" min="1" step="1" required /></label><button class="button danger small" type="button" @click="removeItem(index)">删除</button></div><p v-if="!shipmentSpecItems.length" class="form-error commercial-error">该订单尚未维护颜色尺码，请先在订单明细中补齐。</p></section>
      <div v-if="formError" class="form-error commercial-error">{{ formError }}</div><div class="form-actions"><button class="button secondary" type="button" @click="form = null">取消</button><button class="button primary" :disabled="saving"><span v-if="saving" class="spinner light" />{{ saving ? '保存中…' : '保存出货单' }}</button></div>
    </form>
  </CommercialModal>

  <CommercialModal v-if="boxOpen" title="新增装箱行" :subtitle="detail?.shipment_no" @close="boxOpen = false"><form class="shipment-box-form" @submit.prevent="saveBox"><div class="form-grid"><label class="field"><span>箱号</span><input v-model="boxForm.box_no" maxlength="32" required /></label><label class="field"><span>数量</span><input v-model="boxForm.qty" type="number" min="0" step="1" /></label><label class="field"><span>颜色</span><select v-model="boxForm.color" @change="specColorChanged(boxForm, detail?.items || [])"><option value="">混装 / 不指定</option><option v-for="color in specColors(detail?.items || [])" :key="color" :value="color">{{ color }}</option></select></label><label class="field"><span>尺码</span><select v-model="boxForm.size"><option value="">混装 / 不指定</option><option v-for="size in specSizes(detail?.items || [], boxForm.color)" :key="size" :value="size">{{ size }}</option></select></label><label class="field"><span>毛重（kg）</span><input v-model="boxForm.gross_wt" type="number" min="0" step="0.01" /></label><label class="field"><span>净重（kg）</span><input v-model="boxForm.net_wt" type="number" min="0" step="0.01" /></label><label class="field full"><span>箱规</span><input v-model="boxForm.box_spec" maxlength="64" placeholder="如 60×40×40cm" /></label></div><div v-if="boxError" class="form-error commercial-error">{{ boxError }}</div><div class="form-actions"><button class="button secondary" type="button" @click="boxOpen = false">取消</button><button class="button primary" :disabled="boxSaving">{{ boxSaving ? '保存中…' : '保存装箱行' }}</button></div></form></CommercialModal>
  <ShipmentPrintDialog v-if="printing && detail" :shipment="detail" :boxes="boxes" @close="printing = false" @toast="notify" />
</template>

<style scoped>
.commercial-actions{display:flex;gap:8px;align-items:center}.commercial-wrap{flex-wrap:wrap}.commercial-section{margin-top:22px}.commercial-error{margin-top:14px}.commercial-top-note{margin-top:0}.commercial-item-grid{display:grid;grid-template-columns:1fr 1fr 1fr auto;gap:12px;align-items:end;padding:12px 0;border-bottom:1px solid var(--line)}.shipments-query{display:contents}.shipments-search-button{display:none}.mobile-list-more,.mobile-task-steps,.mobile-shipment-items,.mobile-undelivered-total,.mobile-undelivered-items,.mobile-box-items{display:none}.mobile-more-error button{border:0;background:none;color:var(--orange);padding:4px}
@media(max-width:760px){
  .shipments-welcome{flex-direction:row;align-items:center;margin-bottom:12px}.shipments-welcome>div:first-child{display:none}.shipments-welcome>.commercial-actions{width:100%;flex-wrap:nowrap;justify-content:flex-end}.shipments-refresh{display:none}.shipments-welcome .button.primary{margin-left:auto}
  .shipments-filter{flex-direction:column;align-items:stretch;gap:10px;margin-bottom:12px}.shipments-filter>.segmented{display:flex;flex-wrap:wrap;gap:7px;width:100%;max-width:none;padding:0;background:transparent;border-radius:0;overflow:visible}.shipments-filter>.segmented button{flex:0 1 auto;padding:7px 13px;border:1px solid var(--line);border-radius:999px;background:#fff;color:var(--text-secondary);box-shadow:none;font-size:12px}.shipments-filter>.segmented button.active{border-color:var(--orange);background:var(--orange);color:#fff;box-shadow:none}.shipments-filter>.segmented button:nth-child(n+5){display:none}.shipments-query{display:flex;align-items:center;gap:8px;width:100%;padding:10px;border-radius:12px;background:#fff;box-shadow:0 2px 8px rgba(31,35,41,.06)}.shipments-query>select{width:92px;min-width:0;flex:none;min-height:34px;padding:6px 8px;border:0;border-radius:999px;background:#F5F6F8;color:var(--text-secondary);font-size:11px}.shipments-query .search-box{flex:1;min-width:0;max-width:none}.shipments-query .search-box input{min-height:34px;padding:6px 4px 6px 28px;border:0;background:transparent;font-size:12px}.shipments-query .search-box svg{left:5px;width:14px}.shipments-search-button{display:block;flex:none;padding:4px 0;border:0;background:transparent;color:var(--orange);font-size:12px}
  .shipments-list-card{padding:0;background:transparent;border-radius:0;box-shadow:none;overflow:visible}.shipments-list-card .mobile-cards{gap:10px}.shipments-list-card .mobile-row{border:0;border-radius:12px;box-shadow:0 2px 8px rgba(31,35,41,.06);padding:14px}.shipments-list-card .mobile-row-head{margin-bottom:10px;align-items:flex-start}.shipment-card-main{min-width:0}.shipments-list-card .mobile-row-title{display:block;font-size:15px;overflow-wrap:anywhere}.shipment-card-main .cell-subtitle{overflow-wrap:anywhere}.shipment-mobile-meta{display:flex;align-items:center;justify-content:space-between;gap:10px;color:var(--muted);font-size:12px}.shipment-mobile-sub{display:block;margin:7px 0 0;color:var(--muted);font-size:11px;overflow-wrap:anywhere}.shipments-list-card :deep(.desktop-pagination){display:none}.mobile-list-more{display:block;padding:4px 0 14px;text-align:center}.mobile-load-more{display:inline-flex;align-items:center;justify-content:center;gap:7px;min-width:132px;min-height:40px;padding:8px 20px;border:1px solid var(--line);border-radius:999px;background:#fff;color:var(--text-secondary);font-size:13px}.mobile-no-more,.mobile-more-error{margin:0;padding:10px 0;color:var(--muted);font-size:12px}.mobile-more-error{color:var(--danger)}
  .mobile-task-steps{display:flex;list-style:none;margin:0 0 14px;padding:0;background:#F5F6F8;border-radius:10px;overflow:hidden}.mobile-task-steps li{position:relative;flex:1;min-width:0;padding:9px 1px;text-align:center;color:var(--muted);font-size:10px}.mobile-task-steps li.active{color:var(--orange);font-weight:600}.mobile-task-steps li+li:before{content:'›';position:absolute;left:-3px;color:#C9CDD4}.shipment-detail-content .detail-grid{grid-template-columns:repeat(3,1fr);gap:7px}.shipment-detail-content .detail-item{min-width:0;padding:10px 7px}.shipment-detail-content .detail-item strong{font-size:11px;overflow-wrap:anywhere}.shipment-detail-content .detail-item:nth-child(4),.shipment-detail-content .detail-item:nth-child(5),.shipment-detail-content .detail-item:nth-child(6){grid-column:span 1}.shipment-detail-content .commercial-top-note{line-height:1.6}.shipment-detail-actions{gap:7px;margin-top:14px}.shipment-detail-actions .button{flex:1 1 30%;padding-inline:8px}.shipment-detail-content .commercial-section{margin-top:12px;padding:12px;border:1px solid var(--line);border-radius:12px;background:#fff}.shipment-detail-content .commercial-section .card-header{align-items:flex-start;margin-bottom:10px}.shipment-detail-content .commercial-section .card-header h3{font-size:15px}.shipment-detail-content .commercial-section .card-header p{line-height:1.5}.desktop-shipment-table{display:none}.mobile-shipment-items,.mobile-undelivered-items,.mobile-box-items{display:block}.mobile-shipment-items article{display:grid;grid-template-columns:1fr auto;gap:5px 10px;padding:10px 0;border-top:1px solid var(--line);font-size:12px}.mobile-shipment-items article>span{grid-column:1/-1;color:var(--muted);font-size:10px}.mobile-shipment-items article>b{color:var(--orange);font-weight:600}.mobile-shipment-items>p,.mobile-undelivered-items>p,.mobile-empty-copy{margin:8px 0 0;text-align:center;color:var(--muted);font-size:12px}.mobile-undelivered-total{display:block;margin:0 0 8px;color:var(--muted);font-size:11px;line-height:1.6}.mobile-undelivered-total strong{color:var(--danger)}.mobile-undelivered-items article{padding:11px 0;border-top:1px solid var(--line)}.mobile-undelivered-items article>strong{font-size:12px}.mobile-undelivered-items dl{display:grid;grid-template-columns:repeat(3,1fr);margin:9px 0 0;text-align:center}.mobile-undelivered-items dl>div+div{border-left:1px solid var(--line)}.mobile-undelivered-items dt{color:var(--muted);font-size:10px}.mobile-undelivered-items dd{margin:4px 0 0;font-size:13px;font-weight:600}.mobile-undelivered-items dl>div:last-child dd{color:var(--danger)}.mobile-undelivered-items .page-state{min-height:90px;padding:16px;font-size:12px}.mobile-box-items article{margin-top:9px;padding:11px;border-radius:9px;background:#F5F6F8}.mobile-box-items article>div{display:flex;align-items:center;justify-content:space-between;gap:10px}.mobile-box-items article>div>strong{font-size:13px}.mobile-box-items article p{margin:7px 0 0;color:var(--text-secondary);font-size:11px;overflow-wrap:anywhere}
  .shipment-editor-form .form-grid,.shipment-box-form .form-grid{gap:12px}.shipment-editor-form .commercial-section{counter-reset:shipment-row;margin-top:16px;padding:0;border:0;border-radius:0}.shipment-editor-form .commercial-item-grid{counter-increment:shipment-row;grid-template-columns:1fr 1fr;gap:10px;margin-top:10px;padding:12px;border:1px solid var(--line);border-radius:10px;background:#FAFBFC}.shipment-editor-form .commercial-item-grid:before{content:'第 ' counter(shipment-row) ' 行';grid-column:1/-1;color:var(--text-secondary);font-size:12px;font-weight:600}.shipment-editor-form .commercial-item-grid .field:nth-of-type(3){grid-column:1/-1}.shipment-editor-form .commercial-item-grid .button{align-self:end;width:100%}.shipment-editor-form .form-actions .button,.shipment-box-form .form-actions .button{flex:1}
}
@media(max-width:350px){.shipments-query{gap:6px;padding:8px}.shipments-query>select{width:82px}.shipments-filter>.segmented button{padding-inline:10px}.shipment-detail-content .detail-grid{grid-template-columns:1fr 1fr}.shipment-detail-content .detail-item:nth-child(4),.shipment-detail-content .detail-item:nth-child(5),.shipment-detail-content .detail-item:nth-child(6){grid-column:1/-1}.shipment-detail-actions .button{flex-basis:45%}}
</style>
