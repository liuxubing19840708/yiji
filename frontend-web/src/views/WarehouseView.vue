<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { api, rows } from '../api'
import { dateTime, fullDateTime, money, number } from '../format'
import { COMMON_COLORS } from '../spec-options'
import AppIcon from '../components/AppIcon.vue'
import PageState from '../components/PageState.vue'
import ImportDialog from '../components/ImportDialog.vue'
import { hasPermission } from '../permissions'

const emit = defineEmits(['toast'])
const canManageWarehouse = hasPermission('warehouse.manage')
const props = defineProps({ section: { type: String, default: '' } })
const PAGE_SIZE = 20
const tabs = [
  ['lots', '库存批次'], ['suppliers', '供应商'], ['purchases', '采购单'],
  ['stockIns', '入库单'], ['issues', '领料单'], ['receipts', '暂收 / IQC'],
  ['demand', '需求汇总'], ['divergence', '差异报表'], ['trace', '批次追溯'],
]
const listPaths = {
  lots: '/lots', suppliers: '/suppliers', purchases: '/purchase-orders',
  stockIns: '/stock-ins', issues: '/material-issues', receipts: '/receipts',
}
const statusText = { draft: '草稿', done: '已完成', confirmed: '已确认' }
const sourceText = { purchase: '采购入库', return: '退货入库', other: '其他', opening: '期初入库' }
const issueTypeText = { issue: '领料', return: '退料' }
const overStateText = { over: '超额', pending: '待补换算', no_req: '无定额', normal: '正常' }
const activeTab = ref(tabs.some(([key]) => key === props.section) ? props.section : 'lots')
const lists = reactive({
  lots: { count: 0, results: [] }, suppliers: { count: 0, results: [] },
  purchases: { count: 0, results: [] }, stockIns: { count: 0, results: [] },
  issues: { count: 0, results: [] }, receipts: { count: 0, results: [] },
})
const states = reactive(Object.fromEntries(Object.keys(listPaths).map((key) => [key, { loading: false, error: '' }])))
const filters = reactive({
  lots: { keyword: '', page: 1 }, suppliers: { keyword: '', page: 1 },
  purchases: { status: '', page: 1 }, stockIns: { status: '', page: 1 },
  issues: { status: '', page: 1 }, receipts: { status: '', keyword: '', page: 1 },
})
const lookups = reactive({ materials: [], styles: [], customers: [], productions: [], lots: [], suppliers: [], purchases: [], orders: [], tickets: [] })
const lookupLoaded = reactive(Object.fromEntries(Object.keys(lookups).map((key) => [key, false])))
const lookupLoading = ref(false)
const lookupError = ref('')
const lookupPromises = {}
const actionId = ref('')
const actionError = ref('')
const modal = ref('')
const saving = ref(false)
const formError = ref('')
const editingId = ref(null)
const lotForm = reactive({})
const supplierForm = reactive({})
const purchaseForm = reactive({})
const stockForm = reactive({})
const issueForm = reactive({})
const receiptForm = reactive({})
const issueBomItems = reactive({})
const issueBomLoading = reactive({})
const mobileIssueMode = ref('issue')
const mobileIssueFlow = ref(false)
const issueBedLots = ref([])
let searchTimer

function today() {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}
function localDateTime() {
  const d = new Date()
  return `${today()}T${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}
function toDateTimeInput(value) { return value ? String(value).slice(0, 16) : localDateTime() }
function showToast(message) { emit('toast', message) }
function statusClass(status) { return status === 'draft' ? 'producing' : 'done' }
function idValue(value) { return value === '' || value === null || value === undefined ? null : Number(value) }
function optionalNumber(value) { return value === '' || value === null || value === undefined ? null : Number(value) }
function itemCount(item) { return item.items?.length || 0 }

async function loadAll(path, params = {}) {
  const result = []
  let page = 1
  while (true) {
    const data = await api(path, { params: { ...params, page, page_size: 100 } })
    const batch = rows(data)
    result.push(...batch)
    if (!Array.isArray(data) && result.length >= (data.count || 0)) break
    if (!batch.length || batch.length < 100) break
    page += 1
  }
  return result
}
async function ensureLookups(names, refresh = false) {
  lookupLoading.value = true
  lookupError.value = ''
  try {
    await Promise.all(names.map(async (name) => {
      if (lookupLoaded[name] && !refresh) return
      if (!refresh && lookupPromises[name]) return lookupPromises[name]
      const path = {
        materials: '/tech/materials', styles: '/styles', customers: '/customers', productions: '/productions',
        lots: '/lots', suppliers: '/suppliers', purchases: '/purchase-orders', orders: '/orders', tickets: '/tickets',
      }[name]
      const request = loadAll(path).then((data) => { lookups[name] = data; lookupLoaded[name] = true }).finally(() => { delete lookupPromises[name] })
      lookupPromises[name] = request
      return request
    }))
  } catch (e) { lookupError.value = e.message; throw e } finally { lookupLoading.value = false }
}
async function loadList(name = activeTab.value) {
  if (!listPaths[name]) return
  states[name].loading = true
  states[name].error = ''
  try { lists[name] = await api(listPaths[name], { params: { ...filters[name], page_size: PAGE_SIZE } }) }
  catch (e) { states[name].error = e.message }
  finally { states[name].loading = false }
}
function changePage(name, delta) { filters[name].page += delta; loadList(name) }
function hasMore(name) { return filters[name].page * PAGE_SIZE < (lists[name].count || 0) }
function setStatusFilter(name, status) {
  if (filters[name].status === status) return
  filters[name].status = status
  filters[name].page = 1
  loadList(name)
}
async function loadMore(name) {
  if (!hasMore(name) || states[name].loading) return
  const nextPage = filters[name].page + 1
  states[name].loading = true
  states[name].error = ''
  try {
    const data = await api(listPaths[name], { params: { ...filters[name], page: nextPage, page_size: PAGE_SIZE } })
    lists[name] = { ...data, results: [...lists[name].results, ...rows(data)] }
    filters[name].page = nextPage
  } catch (e) { states[name].error = e.message }
  finally { states[name].loading = false }
}
function searchNow(name) {
  clearTimeout(searchTimer)
  filters[name].page = 1
  loadList(name)
}
function searchList(name) {
  clearTimeout(searchTimer)
  searchTimer = setTimeout(() => { filters[name].page = 1; loadList(name) }, 300)
}
function switchTab(name) {
  activeTab.value = name
  actionError.value = ''
  if (listPaths[name]) loadList(name)
  if (name === 'demand') { ensureLookups(['styles', 'customers']).catch(() => {}); loadDemand() }
  if (name === 'divergence') { ensureLookups(['orders', 'productions', 'materials']).catch(() => {}); loadDivergence() }
  if (name === 'trace') switchTraceMode(traceMode.value)
}
function invalidateLookup(name) { lookupLoaded[name] = false }

function openLot(item = null) {
  editingId.value = item?.id || null
  Object.assign(lotForm, { lot_no: '', fabric_name: '', color: '', qty: '0', unit: '米', unit_cost: '', batch_date: '', remark: '' }, item || {})
  formError.value = ''
  modal.value = 'lot'
}
async function saveLot() {
  formError.value = ''
  if (!lotForm.lot_no?.trim()) { formError.value = '请填写批次号'; return }
  if (Number(lotForm.qty) < 0 || Number(lotForm.unit_cost) < 0) { formError.value = '数量和单价不能为负数'; return }
  saving.value = true
  try {
    const body = { ...lotForm, lot_no: lotForm.lot_no.trim(), qty: Number(lotForm.qty || 0), unit_cost: optionalNumber(lotForm.unit_cost), batch_date: lotForm.batch_date || null }
    await api(editingId.value ? `/lots/${editingId.value}` : '/lots', { method: editingId.value ? 'PUT' : 'POST', body })
    modal.value = ''; invalidateLookup('lots'); showToast(editingId.value ? '批次已更新' : '批次已创建'); await loadList('lots')
  } catch (e) { formError.value = e.message } finally { saving.value = false }
}
async function deleteLot(item) {
  if (!window.confirm(`确定删除批次“${item.lot_no}”吗？\n已有床次血缘或领料记录时后端会拒绝删除。`)) return
  actionId.value = `lot-${item.id}`; actionError.value = ''
  try {
    await api(`/lots/${item.id}`, { method: 'DELETE' }); invalidateLookup('lots'); showToast('批次已删除')
    if (lists.lots.results.length === 1 && filters.lots.page > 1) filters.lots.page -= 1
    await loadList('lots')
  } catch (e) { actionError.value = e.message } finally { actionId.value = '' }
}
function openSupplier(item = null) {
  editingId.value = item?.id || null
  Object.assign(supplierForm, { code: '', name: '', contact_person: '', phone: '', settle_method: '', rating: '', remark: '' }, item || {})
  formError.value = ''; modal.value = 'supplier'
}
async function saveSupplier() {
  formError.value = ''
  if (!supplierForm.code?.trim() || !supplierForm.name?.trim()) { formError.value = '请填写供应商编码和名称'; return }
  saving.value = true
  try {
    const body = { ...supplierForm, code: supplierForm.code.trim(), name: supplierForm.name.trim() }
    await api(editingId.value ? `/suppliers/${editingId.value}` : '/suppliers', { method: editingId.value ? 'PUT' : 'POST', body })
    modal.value = ''; invalidateLookup('suppliers'); showToast(editingId.value ? '供应商已更新' : '供应商已创建'); await loadList('suppliers')
  } catch (e) { formError.value = e.message } finally { saving.value = false }
}
async function deleteSupplier(item) {
  if (!window.confirm(`确定删除供应商“${item.code} · ${item.name}”吗？\n被采购单或暂收单引用时后端会拒绝删除。`)) return
  actionId.value = `supplier-${item.id}`; actionError.value = ''
  try {
    await api(`/suppliers/${item.id}`, { method: 'DELETE' }); invalidateLookup('suppliers'); showToast('供应商已删除')
    if (lists.suppliers.results.length === 1 && filters.suppliers.page > 1) filters.suppliers.page -= 1
    await loadList('suppliers')
  } catch (e) { actionError.value = e.message } finally { actionId.value = '' }
}

function newPurchaseItem() { return { material_id: '', fabric_name: '', color: '', qty: '', unit: '米', price: '0', price_unit: '', demand_source: 'manual' } }
async function openPurchase(item = null, refresh = false) {
  formError.value = ''
  try { await ensureLookups(['materials'], refresh) } catch (e) { formError.value = e.message; return }
  editingId.value = item?.id || null
  Object.assign(purchaseForm, {
    order_no: '', supplier_name: '', ordered_at: today(), status: 'draft', remark: '',
    ...(item || {}), items: item?.items?.length ? item.items.map((line) => ({ ...line, material_id: line.material || '' })) : [newPurchaseItem()],
  })
  modal.value = 'purchase'
}
function purchaseMaterialChanged(line) {
  const material = lookups.materials.find((m) => Number(m.id) === Number(line.material_id))
  if (!material) return
  line.fabric_name = material.name; line.unit = material.unit
  if (line.price_unit === '') line.price_unit = material.price_unit || ''
}
function validateLines(items, kind) {
  if (!items.length) return '请至少添加一行明细'
  for (let i = 0; i < items.length; i += 1) {
    const line = items[i]
    if (['purchase', 'stock', 'receipt'].includes(kind) && !line.fabric_name?.trim()) return `第 ${i + 1} 行请填写物料/面料名称`
    if (['stock', 'receipt'].includes(kind) && !line.lot_no?.trim()) return `第 ${i + 1} 行请填写批次号`
    if (kind === 'issue' && (!line.production_id || !line.lot_id)) return `第 ${i + 1} 行请选择床次和批次`
    const qty = kind === 'receipt' ? line.arrived_qty : line.qty
    if (!(Number(qty) > 0)) return `第 ${i + 1} 行数量必须大于 0`
    if (Number(line.price) < 0 || Number(line.unit_price) < 0 || Number(line.qualified_qty) < 0 || Number(line.rejected_qty) < 0) return `第 ${i + 1} 行数值不能为负数`
  }
  return ''
}
const purchaseReadOnly = computed(() => !!editingId.value && purchaseForm.status !== 'draft')
async function savePurchase() {
  formError.value = ''
  if (!editingId.value) { const error = validateLines(purchaseForm.items, 'purchase'); if (error) { formError.value = error; return } }
  saving.value = true
  try {
    const head = { supplier_name: purchaseForm.supplier_name || '', ordered_at: purchaseForm.ordered_at || today(), remark: purchaseForm.remark || '' }
    const body = editingId.value ? head : { ...head, items: purchaseForm.items.map((line) => ({
      material_id: idValue(line.material_id), fabric_name: line.fabric_name.trim(), color: line.color || '', qty: Number(line.qty),
      unit: line.unit || '米', price: Number(line.price || 0), price_unit: line.price_unit || '', demand_source: line.demand_source || 'manual',
    })) }
    await api(editingId.value ? `/purchase-orders/${editingId.value}` : '/purchase-orders', { method: editingId.value ? 'PUT' : 'POST', body })
    modal.value = ''; invalidateLookup('purchases'); showToast(editingId.value ? '采购单头已保存（明细保持不变）' : '采购单已创建'); await loadList('purchases')
  } catch (e) { formError.value = e.message } finally { saving.value = false }
}

function newStockItem() { return { purchase_item: '', material_id: '', fabric_name: '', color: '', lot_no: '', qty: '', unit: '米', unit_price: '', price_unit: '' } }
async function openStock(item = null) {
  formError.value = ''
  try { await ensureLookups(['materials', 'purchases'], true) } catch (e) { formError.value = e.message; return }
  editingId.value = item?.id || null
  Object.assign(stockForm, {
    in_no: '', source_type: 'purchase', purchase: '', in_at: localDateTime(), status: 'draft', remark: '',
    ...(item || {}), in_at: toDateTimeInput(item?.in_at), purchase: item?.purchase || '',
    items: item?.items?.length ? item.items.map((line) => ({ ...line, purchase_item: line.purchase_item || '', material_id: line.material || '', unit_price: line.unit_price ?? '', price_unit: line.price_unit || '' })) : [newStockItem()],
  })
  modal.value = 'stock'
}
const stockPurchaseItems = computed(() => lookups.purchases.find((p) => Number(p.id) === Number(stockForm.purchase))?.items || [])
function stockPurchaseChanged() { stockForm.items.forEach((line) => { line.purchase_item = '' }) }
function stockPurchaseItemChanged(line) {
  const pi = stockPurchaseItems.value.find((p) => Number(p.id) === Number(line.purchase_item))
  if (!pi) return
  line.material_id = pi.material || ''; line.fabric_name = pi.fabric_name; line.color = pi.color || ''; line.unit = pi.unit
  line.unit_price = ''; line.price_unit = ''
}
function stockMaterialChanged(line) {
  const material = lookups.materials.find((m) => Number(m.id) === Number(line.material_id))
  if (material) { line.fabric_name = material.name; line.unit = material.unit }
}
function fillStockFromPurchase() {
  if (!stockForm.purchase) { formError.value = '请先选择采购单'; return }
  if (!stockPurchaseItems.value.length) { formError.value = '该采购单没有可带出的明细'; return }
  stockForm.items = stockPurchaseItems.value.map((pi) => ({
    purchase_item: pi.id, material_id: pi.material || '', fabric_name: pi.fabric_name, color: pi.color || '', lot_no: '',
    qty: String(Math.max(0, Number(pi.qty || 0) - Number(pi.arrived_qty || 0)) || Number(pi.qty || 0)), unit: pi.unit,
    unit_price: '', price_unit: '',
  }))
  formError.value = ''
}
const stockReadOnly = computed(() => !!editingId.value && stockForm.status !== 'draft')
async function saveStock() {
  formError.value = ''
  if (!editingId.value) { const error = validateLines(stockForm.items, 'stock'); if (error) { formError.value = error; return } }
  saving.value = true
  try {
    const body = editingId.value
      ? { in_at: stockForm.in_at, remark: stockForm.remark || '' }
      : { source_type: stockForm.source_type, purchase: idValue(stockForm.purchase), in_at: stockForm.in_at, remark: stockForm.remark || '', items: stockForm.items.map((line) => ({
          purchase_item: idValue(line.purchase_item), material_id: idValue(line.material_id), fabric_name: line.fabric_name.trim(), color: line.color || '',
          lot_no: line.lot_no.trim(), qty: Number(line.qty), unit: line.unit || '米', unit_price: optionalNumber(line.unit_price), price_unit: line.price_unit || '',
        })) }
    await api(editingId.value ? `/stock-ins/${editingId.value}` : '/stock-ins', { method: editingId.value ? 'PUT' : 'POST', body })
    modal.value = ''; invalidateLookup('lots'); showToast(editingId.value ? '入库单头已保存（明细保持不变）' : '入库单已创建，确认后完成价链落定'); await loadList('stockIns')
  } catch (e) { formError.value = e.message } finally { saving.value = false }
}

function newIssueItem() { return { production_id: '', lot_id: '', qty: '', bom_item_id: '', material_id: '', qty_req: '', over_reason: '' } }
async function openIssue(item = null) {
  mobileIssueFlow.value = false
  issueBedLots.value = []
  formError.value = ''
  try { await ensureLookups(['productions', 'lots', 'materials'], true) } catch (e) { formError.value = e.message; return }
  editingId.value = item?.id || null
  Object.assign(issueForm, {
    issue_no: '', type: 'issue', issue_at: localDateTime(), status: 'draft', remark: '',
    ...(item || {}), issue_at: toDateTimeInput(item?.issue_at),
    items: item?.items?.length ? item.items.map((line) => ({ ...line, production_id: line.production, lot_id: line.lot, bom_item_id: line.bom_item || '', material_id: line.material || '', qty_req: line.qty_req ?? '' })) : [newIssueItem()],
  })
  modal.value = 'issue'
  if (!item) return
  const productionIds = [...new Set(issueForm.items.map((line) => Number(line.production_id)).filter(Boolean))]
  productionIds.forEach((id) => loadIssueBom({ production_id: id }, false))
}
async function loadIssueBom(line, reset = true) {
  const productionId = Number(line.production_id)
  if (reset) { line.bom_item_id = ''; line.material_id = '' }
  if (!productionId) return
  if (issueBomItems[productionId] || issueBomLoading[productionId]) return
  const production = lookups.productions.find((p) => Number(p.id) === productionId)
  if (!production?.style) { issueBomItems[productionId] = []; return }
  issueBomLoading[productionId] = true
  try {
    const boms = await loadAll('/tech/boms', { style_id: production.style, status: 'active' })
    issueBomItems[productionId] = boms[0]?.items || []
  } catch (e) { issueBomItems[productionId] = []; formError.value = `读取床次定额失败：${e.message}` }
  finally { issueBomLoading[productionId] = false }
}
function issueBomChanged(line) {
  const bomItem = (issueBomItems[Number(line.production_id)] || []).find((bi) => Number(bi.id) === Number(line.bom_item_id))
  if (bomItem) {
    line.material_id = bomItem.material
    line.qty_req = issueQuota(bomItem, line.production_id)
  } else line.qty_req = ''
}
function issueQuota(bomItem, productionId) {
  const production = lookups.productions.find((p) => Number(p.id) === Number(productionId))
  const baseQty = Number(production?.plan_qty ?? production?.total_qty ?? 0)
  const usage = Number(bomItem?.unit_usage || 0)
  const loss = Number(bomItem?.loss_rate || 0)
  return usage > 0 && baseQty > 0 ? String(Math.round(usage * (1 + loss / 100) * baseQty * 100) / 100) : ''
}
async function openMobileIssue() {
  await openIssue()
  if (modal.value !== 'issue') return
  mobileIssueFlow.value = true
  issueForm.type = mobileIssueMode.value
}
async function mobileIssueProductionChanged(line) {
  const productionId = Number(line.production_id)
  issueBedLots.value = []
  await loadIssueBom(line)
  if (!productionId) return
  try { issueBedLots.value = await api(`/productions/${productionId}/lot-links`) } catch { issueBedLots.value = [] }
  if (issueForm.type !== 'issue') return
  const bomItems = issueBomItems[productionId] || []
  if (!bomItems.length) return
  issueForm.items = bomItems.map((bomItem) => ({
    ...newIssueItem(), production_id: productionId, bom_item_id: bomItem.id,
    material_id: bomItem.material, qty_req: issueQuota(bomItem, productionId),
  }))
}
function issueProductionChanged(line) {
  if (mobileIssueFlow.value) return mobileIssueProductionChanged(line)
  return loadIssueBom(line)
}
function issueLineOver(line) {
  return line.qty !== '' && line.qty_req !== '' && Number(line.qty) > Number(line.qty_req)
}
function issueLotMismatch(line) {
  const lot = lookups.lots.find((item) => Number(item.id) === Number(line.lot_id))
  return !!lot && issueBedLots.value.length > 0 && !issueBedLots.value.some((item) => item.lot_no === lot.lot_no)
}
const issueReadOnly = computed(() => !!editingId.value && issueForm.status !== 'draft')
async function saveIssue() {
  formError.value = ''
  if (!editingId.value) {
    const error = validateLines(issueForm.items, 'issue'); if (error) { formError.value = error; return }
    const pairs = new Set()
    for (const line of issueForm.items) {
      const key = mobileIssueFlow.value
          ? `${line.production_id}|${line.lot_id}|${line.bom_item_id || ''}|${line.material_id || ''}`
          : `${line.production_id}|${line.lot_id}`
      if (pairs.has(key)) { formError.value = '同一床次和批次不可重复，请合并数量'; return }
      pairs.add(key)
    }
  }
  saving.value = true
  try {
    const body = editingId.value
      ? { issue_at: issueForm.issue_at, remark: issueForm.remark || '' }
      : { type: issueForm.type, issue_at: issueForm.issue_at, remark: issueForm.remark || '', items: issueForm.items.map((line) => ({
          production_id: Number(line.production_id), lot_id: Number(line.lot_id), qty: Number(line.qty),
          bom_item_id: idValue(line.bom_item_id), material_id: idValue(line.material_id),
          qty_req: line.bom_item_id ? null : optionalNumber(line.qty_req), over_reason: line.over_reason || '',
        })) }
    await api(editingId.value ? `/material-issues/${editingId.value}` : '/material-issues', { method: editingId.value ? 'PUT' : 'POST', body })
    modal.value = ''; showToast(editingId.value ? '领料单头已保存（明细保持不变）' : `${issueTypeText[issueForm.type]}单已创建`); await loadList('issues')
  } catch (e) { formError.value = e.message } finally { saving.value = false }
}

function newReceiptItem() { return { purchase_item: '', material_id: '', fabric_name: '', color: '', lot_no: '', unit: '米', arrived_qty: '', qualified_qty: '', rejected_qty: '0', remark: '' } }
async function openReceipt(item = null) {
  formError.value = ''
  try { await ensureLookups(['suppliers', 'purchases', 'materials'], true) } catch (e) { formError.value = e.message; return }
  editingId.value = item?.id || null
  Object.assign(receiptForm, {
    receipt_no: '', supplier: '', supplier_name: '', purchase: '', status: 'draft', remark: '',
    ...(item || {}), supplier: item?.supplier || '', purchase: item?.purchase || '',
    items: item?.items?.length ? item.items.map((line) => ({ ...line, purchase_item: line.purchase_item || '', material_id: line.material || '' })) : [newReceiptItem()],
  })
  modal.value = 'receipt'
}
const receiptPurchaseItems = computed(() => lookups.purchases.find((p) => Number(p.id) === Number(receiptForm.purchase))?.items || [])
function receiptSupplierChanged() {
  const supplier = lookups.suppliers.find((s) => Number(s.id) === Number(receiptForm.supplier))
  if (supplier) receiptForm.supplier_name = supplier.name
}
function receiptPurchaseChanged() {
  const purchase = lookups.purchases.find((p) => Number(p.id) === Number(receiptForm.purchase))
  receiptForm.supplier_name = purchase?.supplier_name || receiptForm.supplier_name
  receiptForm.items.forEach((line) => { line.purchase_item = '' })
}
function receiptPurchaseItemChanged(line) {
  const pi = receiptPurchaseItems.value.find((p) => Number(p.id) === Number(line.purchase_item))
  if (!pi) return
  line.material_id = pi.material || ''; line.fabric_name = pi.fabric_name; line.color = pi.color || ''; line.unit = pi.unit
}
function receiptMaterialChanged(line) {
  const material = lookups.materials.find((m) => Number(m.id) === Number(line.material_id))
  if (material) { line.fabric_name = material.name; line.unit = material.unit }
}
function fillReceiptFromPurchase() {
  if (!receiptForm.purchase) { formError.value = '请先选择采购单'; return }
  if (!receiptPurchaseItems.value.length) { formError.value = '该采购单没有可带出的明细'; return }
  receiptForm.items = receiptPurchaseItems.value.map((pi) => {
    const remaining = Math.max(0, Number(pi.qty || 0) - Number(pi.arrived_qty || 0)) || Number(pi.qty || 0)
    return { purchase_item: pi.id, material_id: pi.material || '', fabric_name: pi.fabric_name, color: pi.color || '', lot_no: '', unit: pi.unit,
      arrived_qty: String(remaining), qualified_qty: String(remaining), rejected_qty: '0', remark: '' }
  })
  formError.value = ''
}
const receiptReadOnly = computed(() => !!editingId.value && receiptForm.status !== 'draft')
async function saveReceipt() {
  formError.value = ''
  const error = validateLines(receiptForm.items, 'receipt'); if (error) { formError.value = error; return }
  saving.value = true
  try {
    const body = {
      supplier: idValue(receiptForm.supplier), supplier_name: receiptForm.supplier_name || '', purchase: idValue(receiptForm.purchase), remark: receiptForm.remark || '',
      items: receiptForm.items.map((line) => ({ purchase_item: idValue(line.purchase_item), material_id: idValue(line.material_id), fabric_name: line.fabric_name.trim(),
        color: line.color || '', lot_no: line.lot_no.trim(), unit: line.unit || '米', arrived_qty: Number(line.arrived_qty),
        qualified_qty: Number(line.qualified_qty || 0), rejected_qty: Number(line.rejected_qty || 0), remark: line.remark || '' })),
    }
    await api(editingId.value ? `/receipts/${editingId.value}` : '/receipts', { method: editingId.value ? 'PUT' : 'POST', body })
    modal.value = ''; invalidateLookup('purchases'); showToast(editingId.value ? '暂收单及 IQC 明细已保存' : '暂收单已创建'); await loadList('receipts')
  } catch (e) { formError.value = e.message } finally { saving.value = false }
}

async function documentAction(kind, action, item) {
  const config = {
    purchase: { path: '/purchase-orders', name: '采购单', no: item.order_no, tab: 'purchases', lookup: 'purchases' },
    stock: { path: '/stock-ins', name: '入库单', no: item.in_no, tab: 'stockIns', lookup: 'lots' },
    issue: { path: '/material-issues', name: item.type === 'return' ? '退料单' : '领料单', no: item.issue_no, tab: 'issues' },
    receipt: { path: '/receipts', name: '暂收单', no: item.receipt_no, tab: 'receipts', lookup: 'purchases' },
  }[kind]
  const confirming = action === 'confirm'
  const effect = {
    purchase: '确认后采购单完成，不能再修改或删除。', stock: '确认后入库单完成，并落定批次成本，不能再修改或删除。',
    issue: item.type === 'return' ? '确认后冲减对应床次批次血缘，且不可撤销。' : '确认后累加床次批次血缘，且不可撤销。',
    receipt: '确认后按合格数量自动生成已完成入库单，拒收量不入库存，且不可再改。',
  }[kind]
  const message = confirming ? `确定确认${config.name}“${config.no}”吗？\n${effect}` : `确定删除草稿${config.name}“${config.no}”吗？\n删除后不再出现在单据列表中。`
  if (!window.confirm(message)) return
  actionId.value = `${kind}-${action}-${item.id}`; actionError.value = ''; formError.value = ''
  try {
    const result = await api(`${config.path}/${item.id}${confirming ? '/confirm' : ''}`, { method: confirming ? 'POST' : 'DELETE', ...(confirming ? { body: {} } : {}) })
    if (!confirming) {
      modal.value = ''
      if (lists[config.tab].results.length === 1 && filters[config.tab].page > 1) filters[config.tab].page -= 1
    }
    if (config.lookup) invalidateLookup(config.lookup)
    if (kind === 'receipt') invalidateLookup('lots')
    showToast(confirming ? (kind === 'receipt' && result.in_no ? `暂收已确认，自动生成入库单 ${result.in_no}` : `${config.name}已确认`) : `${config.name}已删除`)
    await loadList(config.tab)
    if (confirming && modal.value) modal.value = ''
  } catch (e) { formError.value = e.message; actionError.value = e.message } finally { actionId.value = '' }
}

const demandFilters = reactive({ style_id: '', customer_id: '' })
const demandData = ref({ summary: {}, rows: [] })
const demandLoading = ref(false)
const demandError = ref('')
async function loadDemand() {
  demandLoading.value = true; demandError.value = ''
  try { demandData.value = await api('/demand-summary', { params: demandFilters }) } catch (e) { demandError.value = e.message } finally { demandLoading.value = false }
}
async function createFromDemand(row) {
  await openPurchase(null, true)
  if (modal.value !== 'purchase') return
  const material = lookups.materials.find((m) => Number(m.id) === Number(row.material_id))
  purchaseForm.items = [{ material_id: row.material_id, fabric_name: row.material_name, color: '', qty: row.qty_reorder,
    unit: row.unit, price: '0', price_unit: material?.price_unit || '', demand_source: 'summary_import' }]
  purchaseForm.remark = `由需求汇总生成：${row.material_code} · ${row.material_name}`
}

const divergenceFilters = reactive({ date_from: '', date_to: '', order_id: '', production_id: '', material_id: '' })
const divergenceData = ref({ summary: {}, rows: [], pending_units: [] })
const divergenceLoading = ref(false)
const divergenceError = ref('')
const divergenceDetail = ref(null)
async function loadDivergence() {
  divergenceLoading.value = true; divergenceError.value = ''
  try { divergenceData.value = await api('/issues/divergence', { params: divergenceFilters }) } catch (e) { divergenceError.value = e.message } finally { divergenceLoading.value = false }
}
function openDivergence(row) { divergenceDetail.value = row; modal.value = 'divergence' }

const traceMode = ref(window.matchMedia('(max-width: 760px)').matches ? 'reverse' : 'lot')
const traceKeyword = ref('')
const traceLots = ref([])
const traceLoading = ref(false)
const traceError = ref('')
const lotTrace = ref(null)
const productionTrace = ref(null)
const ticketTrace = ref(null)
const reverseFilters = reactive({ style_no: '', color: '', customer_id: '', date_from: '', date_to: '' })
const reverseMatches = ref([])
const ticketId = ref('')
async function loadTraceLots() {
  traceLoading.value = true; traceError.value = ''; lotTrace.value = null; productionTrace.value = null
  try { const data = await api('/trace/lot', { params: { keyword: traceKeyword.value } }); traceLots.value = data.results || [] } catch (e) { traceError.value = e.message } finally { traceLoading.value = false }
}
async function openTraceLot(item) {
  traceLoading.value = true; traceError.value = ''; productionTrace.value = null
  try { lotTrace.value = await api(`/trace/lot/${item.id}`) } catch (e) { traceError.value = e.message } finally { traceLoading.value = false }
}
function goTraceLot(item) { activeTab.value = 'trace'; traceMode.value = 'lot'; openTraceLot(item) }
async function switchTraceMode(mode) {
  traceMode.value = mode; traceError.value = ''
  if (mode === 'lot') loadTraceLots()
  if (mode === 'reverse') { ensureLookups(['customers']).catch((e) => { traceError.value = e.message }); loadReverseTrace() }
  if (mode === 'ticket') { ticketTrace.value = null; productionTrace.value = null; try { await ensureLookups(['tickets'], true) } catch (e) { traceError.value = e.message } }
}
async function loadReverseTrace() {
  traceLoading.value = true; traceError.value = ''; productionTrace.value = null
  try { const data = await api('/trace/reverse', { params: reverseFilters }); reverseMatches.value = data.matches || [] } catch (e) { traceError.value = e.message } finally { traceLoading.value = false }
}
async function openProductionTrace(id) {
  traceLoading.value = true; traceError.value = ''
  try { productionTrace.value = await api(`/trace/production/${id}`) } catch (e) { traceError.value = e.message } finally { traceLoading.value = false }
}
async function loadTicketTrace() {
  if (!ticketId.value) { traceError.value = '请选择本公司菲票'; return }
  traceLoading.value = true; traceError.value = ''
  try { ticketTrace.value = await api(`/trace/ticket/${ticketId.value}`) } catch (e) { traceError.value = e.message; ticketTrace.value = null } finally { traceLoading.value = false }
}

onMounted(() => switchTab(activeTab.value))
</script>

<template>
  <div class="warehouse-mobile">
    <nav class="warehouse-tabs warehouse-mobile-section-nav" aria-label="当前仓库功能">
      <button class="active">{{ tabs.find(([key]) => key === activeTab)?.[1] }}</button>
    </nav>
    <div v-if="actionError" class="form-error warehouse-action-error" role="alert">{{ actionError }}</div>
    <div v-if="lookupError && !modal" class="form-error warehouse-action-error" role="alert">基础档案读取失败：{{ lookupError }}</div>
    <div v-if="listPaths[activeTab] && states[activeTab].error && lists[activeTab].results.length" class="form-error warehouse-action-error" role="alert">{{ states[activeTab].error }}<button class="warehouse-text-button" @click="loadList(activeTab)">重试</button></div>

    <template v-if="activeTab === 'lots'">
      <div class="warehouse-mobile-toolbar"><span /><button class="button primary" v-if="canManageWarehouse" @click="openLot()">新增批次</button></div>
      <section class="card warehouse-mobile-filter">
        <label class="search-box"><AppIcon name="search" :size="17" /><input v-model="filters.lots.keyword" placeholder="批次号 / 面料 / 颜色" @keyup.enter="searchNow('lots')" /></label>
        <button class="warehouse-search-button" @click="searchNow('lots')">查询</button>
      </section>
      <PageState :loading="states.lots.loading && !lists.lots.results.length" :error="lists.lots.results.length ? '' : states.lots.error" :empty="!lists.lots.results.length" empty-text="暂无库存批次" @retry="loadList('lots')">
        <div class="warehouse-mobile-list">
          <article v-for="item in lists.lots.results" :key="item.id" class="card warehouse-mobile-card">
            <div class="warehouse-mobile-head"><div><h3>{{ item.lot_no }}</h3><p>{{ item.fabric_name || '未填写面料' }} · {{ item.color || '无颜色' }}</p></div><strong>{{ number(item.qty) }} {{ item.unit }}</strong></div>
            <div class="warehouse-mobile-meta"><span>批次单价 <strong>{{ item.unit_cost === null ? '待补价' : money(item.unit_cost) }}</strong></span><span>批次日期 <strong>{{ item.batch_date || '—' }}</strong></span><span>来源 <strong>{{ item.source_in_no || '手工 / 其他' }}</strong></span></div>
            <div class="warehouse-mobile-actions"><button class="button secondary small" @click="openLot(item)">编辑</button><button class="button secondary small" @click="goTraceLot(item)">追溯</button><button class="button danger small" :disabled="actionId === `lot-${item.id}`" v-if="canManageWarehouse" @click="deleteLot(item)">删除</button></div>
          </article>
        </div>
      </PageState>
      <div v-if="lists.lots.results.length" class="warehouse-mobile-more"><button v-if="hasMore('lots')" :disabled="states.lots.loading" @click="loadMore('lots')">{{ states.lots.loading ? '加载中…' : '加载更多' }}</button><span v-else>没有更多了</span></div>
    </template>

    <template v-else-if="activeTab === 'suppliers'">
      <div class="warehouse-mobile-toolbar"><span /><button class="button primary" v-if="canManageWarehouse" @click="openSupplier()">新增供应商</button></div>
      <section class="card warehouse-mobile-filter"><label class="search-box"><AppIcon name="search" :size="17" /><input v-model="filters.suppliers.keyword" placeholder="编码 / 名称 / 联系人 / 电话" @keyup.enter="searchNow('suppliers')" /></label><button class="warehouse-search-button" @click="searchNow('suppliers')">查询</button></section>
      <PageState :loading="states.suppliers.loading && !lists.suppliers.results.length" :error="lists.suppliers.results.length ? '' : states.suppliers.error" :empty="!lists.suppliers.results.length" empty-text="暂无供应商，点新增供应商建立主档" @retry="loadList('suppliers')">
        <div class="warehouse-mobile-list"><article v-for="item in lists.suppliers.results" :key="item.id" class="card warehouse-mobile-card">
          <div class="warehouse-mobile-head"><div><h3>{{ item.code }}</h3><p>{{ item.name || '—' }}</p></div><span v-if="item.settle_method" class="badge">{{ item.settle_method }}</span></div>
          <p class="warehouse-mobile-line">联系人：{{ item.contact_person || '—' }}<span v-if="item.phone"> · {{ item.phone }}</span></p><p v-if="item.rating" class="warehouse-mobile-line">评级：{{ item.rating }}</p><p v-if="item.remark" class="warehouse-mobile-line">备注：{{ item.remark }}</p>
          <div class="warehouse-mobile-actions"><button class="button secondary small" @click="openSupplier(item)">编辑</button><button class="button danger small" :disabled="actionId === `supplier-${item.id}`" v-if="canManageWarehouse" @click="deleteSupplier(item)">删除</button></div>
        </article></div>
      </PageState>
      <div v-if="lists.suppliers.results.length" class="warehouse-mobile-more"><button v-if="hasMore('suppliers')" :disabled="states.suppliers.loading" @click="loadMore('suppliers')">{{ states.suppliers.loading ? '加载中…' : '加载更多' }}</button><span v-else>没有更多了</span></div>
    </template>

    <template v-else-if="activeTab === 'issues'">
      <div class="warehouse-mobile-toolbar"><div class="warehouse-mobile-segment"><button :class="{ active: mobileIssueMode === 'issue' }" @click="mobileIssueMode = 'issue'">领料</button><button :class="{ active: mobileIssueMode === 'return' }" @click="mobileIssueMode = 'return'">退料</button></div><button class="warehouse-text-button" @click="switchTab('divergence')">差异报表 ›</button></div>
      <div class="warehouse-mobile-chips" aria-label="领退料状态"><button v-for="option in [['', '全部'], ['draft', '草稿'], ['confirmed', '已确认']]" :key="option[0]" :class="{ active: filters.issues.status === option[0] }" @click="setStatusFilter('issues', option[0])">{{ option[1] }}</button><button v-if="canManageWarehouse" class="warehouse-mobile-create" @click="openMobileIssue">＋ 新建{{ mobileIssueMode === 'return' ? '退料' : '领料' }}</button></div>
      <PageState :loading="states.issues.loading && !lists.issues.results.length" :error="lists.issues.results.length ? '' : states.issues.error" :empty="!lists.issues.results.length" empty-text="暂无领料或退料单" @retry="loadList('issues')">
        <div class="warehouse-mobile-list"><article v-for="item in lists.issues.results" :key="item.id" class="card warehouse-mobile-card">
          <div class="warehouse-mobile-head"><button class="warehouse-doc-link" @click="openIssue(item)">{{ item.issue_no }}</button><div><span class="badge">{{ issueTypeText[item.type] }}</span> <span class="badge" :class="statusClass(item.status)">{{ statusText[item.status] }}</span></div></div>
          <p class="warehouse-mobile-line">{{ dateTime(item.issue_at) }}</p><p v-for="line in item.items || []" :key="line.id" class="warehouse-mobile-line">床次 {{ line.bed_no }} · 批次 {{ line.lot_no }} × {{ line.qty }}<span v-if="line.over" class="warehouse-warning"> · 超领</span></p>
          <div class="warehouse-mobile-actions"><button class="button secondary small" @click="openIssue(item)">{{ item.status === 'draft' ? '编辑' : '详情' }}</button><template v-if="item.status === 'draft'"><button class="button primary small" v-if="canManageWarehouse" @click="documentAction('issue', 'confirm', item)">确认（写血缘）</button><button class="button danger small" v-if="canManageWarehouse" @click="documentAction('issue', 'delete', item)">删除</button></template></div>
        </article></div>
      </PageState>
      <div v-if="lists.issues.results.length" class="warehouse-mobile-more"><button v-if="hasMore('issues')" :disabled="states.issues.loading" @click="loadMore('issues')">{{ states.issues.loading ? '加载中…' : '加载更多' }}</button><span v-else>没有更多了</span></div>
    </template>

    <template v-else-if="activeTab === 'receipts'">
      <div class="warehouse-mobile-toolbar"><button class="warehouse-text-button" @click="switchTab('demand')">采购需求汇总 ›</button><button class="button primary" v-if="canManageWarehouse" @click="openReceipt()">新建暂收</button></div>
      <div class="warehouse-mobile-chips" aria-label="暂收状态"><button v-for="option in [['', '全部'], ['draft', '草稿'], ['confirmed', '已确认']]" :key="option[0]" :class="{ active: filters.receipts.status === option[0] }" @click="setStatusFilter('receipts', option[0])">{{ option[1] }}</button></div>
      <section class="card warehouse-mobile-filter"><label class="search-box"><AppIcon name="search" :size="17" /><input v-model="filters.receipts.keyword" placeholder="单号 / 供应商 / 面料" @keyup.enter="searchNow('receipts')" /></label><button class="warehouse-search-button" @click="searchNow('receipts')">查询</button></section>
      <PageState :loading="states.receipts.loading && !lists.receipts.results.length" :error="lists.receipts.results.length ? '' : states.receipts.error" :empty="!lists.receipts.results.length" empty-text="暂无暂收单，到料后点新建暂收登记" @retry="loadList('receipts')">
        <div class="warehouse-mobile-list"><article v-for="item in lists.receipts.results" :key="item.id" class="card warehouse-mobile-card warehouse-mobile-clickable" @click="openReceipt(item)">
          <div class="warehouse-mobile-head"><div><h3>{{ item.receipt_no }}</h3><p>{{ item.supplier_name || '—' }}<span v-if="item.purchase_no"> · {{ item.purchase_no }}</span></p></div><span class="badge" :class="statusClass(item.status)">{{ statusText[item.status] }}</span></div>
          <div class="warehouse-mobile-meta"><span>明细 <strong>{{ itemCount(item) }} 行</strong></span><span>创建 <strong>{{ dateTime(item.created_at) }}</strong></span><span>合格 <strong>{{ number(item.items?.reduce((sum, line) => sum + Number(line.qualified_qty || 0), 0)) }}</strong></span><span>拒收 <strong>{{ number(item.items?.reduce((sum, line) => sum + Number(line.rejected_qty || 0), 0)) }}</strong></span></div>
          <p v-if="item.remark" class="warehouse-mobile-line">备注：{{ item.remark }}</p><div class="warehouse-mobile-actions" @click.stop><button class="button secondary small" @click="openReceipt(item)">{{ item.status === 'draft' ? 'IQC / 编辑' : '暂收详情' }}</button><button v-if="item.status === 'draft'" class="button primary small" @click="documentAction('receipt', 'confirm', item)">确认</button></div>
        </article></div>
      </PageState>
      <div v-if="lists.receipts.results.length" class="warehouse-mobile-more"><button v-if="hasMore('receipts')" :disabled="states.receipts.loading" @click="loadMore('receipts')">{{ states.receipts.loading ? '加载中…' : '加载更多' }}</button><span v-else>没有更多了</span></div>
    </template>

    <template v-else-if="activeTab === 'purchases'">
      <div class="warehouse-mobile-toolbar"><span /><button class="button primary" v-if="canManageWarehouse" @click="openPurchase()">新增采购单</button></div><div class="warehouse-mobile-chips"><button v-for="option in [['', '全部'], ['draft', '草稿'], ['done', '已完成']]" :key="option[0]" :class="{ active: filters.purchases.status === option[0] }" @click="setStatusFilter('purchases', option[0])">{{ option[1] }}</button></div>
      <PageState :loading="states.purchases.loading && !lists.purchases.results.length" :error="lists.purchases.results.length ? '' : states.purchases.error" :empty="!lists.purchases.results.length" empty-text="暂无采购单" @retry="loadList('purchases')"><div class="warehouse-mobile-list"><article v-for="item in lists.purchases.results" :key="item.id" class="card warehouse-mobile-card">
        <div class="warehouse-mobile-head"><button class="warehouse-doc-link" @click="openPurchase(item)">{{ item.order_no }}</button><span class="badge" :class="statusClass(item.status)">{{ statusText[item.status] }}</span></div><p class="warehouse-mobile-line">{{ item.supplier_name || '—' }} · {{ item.ordered_at }}</p><p v-for="line in item.items || []" :key="line.id" class="warehouse-mobile-line">{{ line.material_name || line.fabric_name }} {{ line.color }} × {{ line.qty }}{{ line.unit }}</p>
        <div class="warehouse-mobile-actions"><button class="button secondary small" @click="openPurchase(item)">{{ item.status === 'draft' ? '编辑' : '详情' }}</button><template v-if="item.status === 'draft'"><button class="button primary small" v-if="canManageWarehouse" @click="documentAction('purchase', 'confirm', item)">确认</button><button class="button danger small" v-if="canManageWarehouse" @click="documentAction('purchase', 'delete', item)">删除</button></template></div>
      </article></div></PageState><div v-if="lists.purchases.results.length" class="warehouse-mobile-more"><button v-if="hasMore('purchases')" :disabled="states.purchases.loading" @click="loadMore('purchases')">{{ states.purchases.loading ? '加载中…' : '加载更多' }}</button><span v-else>没有更多了</span></div>
    </template>

    <template v-else-if="activeTab === 'stockIns'">
      <div class="warehouse-mobile-toolbar"><span /><button class="button primary" v-if="canManageWarehouse" @click="openStock()">新增入库单</button></div><div class="warehouse-mobile-chips"><button v-for="option in [['', '全部'], ['draft', '草稿'], ['done', '已完成']]" :key="option[0]" :class="{ active: filters.stockIns.status === option[0] }" @click="setStatusFilter('stockIns', option[0])">{{ option[1] }}</button></div>
      <PageState :loading="states.stockIns.loading && !lists.stockIns.results.length" :error="lists.stockIns.results.length ? '' : states.stockIns.error" :empty="!lists.stockIns.results.length" empty-text="暂无入库单" @retry="loadList('stockIns')"><div class="warehouse-mobile-list"><article v-for="item in lists.stockIns.results" :key="item.id" class="card warehouse-mobile-card">
        <div class="warehouse-mobile-head"><button class="warehouse-doc-link" @click="openStock(item)">{{ item.in_no }}</button><span class="badge" :class="statusClass(item.status)">{{ statusText[item.status] }}</span></div><p class="warehouse-mobile-line">{{ sourceText[item.source_type] }} · {{ dateTime(item.in_at) }}</p><p v-for="line in item.items || []" :key="line.id" class="warehouse-mobile-line">{{ line.material_name || line.fabric_name }} · 批次 {{ line.lot_no }} × {{ line.qty }}{{ line.unit }}</p>
        <div class="warehouse-mobile-actions"><button class="button secondary small" @click="openStock(item)">{{ item.status === 'draft' ? '编辑' : '详情' }}</button><template v-if="item.status === 'draft'"><button class="button primary small" v-if="canManageWarehouse" @click="documentAction('stock', 'confirm', item)">确认</button><button class="button danger small" v-if="canManageWarehouse" @click="documentAction('stock', 'delete', item)">删除</button></template></div>
      </article></div></PageState><div v-if="lists.stockIns.results.length" class="warehouse-mobile-more"><button v-if="hasMore('stockIns')" :disabled="states.stockIns.loading" @click="loadMore('stockIns')">{{ states.stockIns.loading ? '加载中…' : '加载更多' }}</button><span v-else>没有更多了</span></div>
    </template>

    <template v-else-if="activeTab === 'demand'">
      <div class="warehouse-mobile-toolbar"><button class="warehouse-text-button" @click="switchTab('receipts')">‹ 到料暂收</button><button class="button secondary" @click="loadDemand">重新计算</button></div><section class="card warehouse-mobile-report-filter"><select v-model="demandFilters.style_id" aria-label="需求款式" @change="loadDemand"><option value="">全部款式</option><option v-for="style in lookups.styles" :key="style.id" :value="style.id">{{ style.style_no }} · {{ style.name }}</option></select><select v-model="demandFilters.customer_id" aria-label="需求客户" @change="loadDemand"><option value="">全部客户</option><option v-for="customer in lookups.customers" :key="customer.id" :value="customer.id">{{ customer.customer_no }} · {{ customer.name }}</option></select></section>
      <div class="warehouse-mobile-summary"><span>物料 <strong>{{ demandData.summary.material_cnt || 0 }}</strong></span><span>补货 <strong>{{ demandData.summary.reorder_cnt || 0 }}</strong></span><span>库存预警 <strong>{{ demandData.summary.safety_alert_cnt || 0 }}</strong></span></div>
      <PageState :loading="demandLoading" :error="demandError" :empty="!demandData.rows.length" empty-text="当前筛选下没有需求或安全库存物料" @retry="loadDemand"><div class="warehouse-mobile-list"><article v-for="row in demandData.rows" :key="row.material_id" class="card warehouse-mobile-card"><div class="warehouse-mobile-head"><div><h3>{{ row.material_code }} · {{ row.material_name }}</h3><p>{{ row.unit }}</p></div><strong class="warehouse-warning">补 {{ number(row.qty_reorder) }}</strong></div><div class="warehouse-mobile-meta"><span>需求 <strong>{{ number(row.qty_demand) }}</strong></span><span>库存 <strong>{{ number(row.qty_stock) }}</strong></span><span>在途 <strong>{{ number(row.qty_in_transit) }}</strong></span><span>安全库存 <strong>{{ number(row.safety_stock) }}</strong></span></div><div class="warehouse-mobile-actions"><button class="button primary small" :disabled="Number(row.qty_reorder) <= 0" @click="createFromDemand(row)">生成采购草稿</button></div></article></div></PageState>
    </template>

    <template v-else-if="activeTab === 'divergence'">
      <div class="warehouse-mobile-toolbar"><button class="warehouse-text-button" @click="switchTab('issues')">‹ 按床领料</button><button class="button primary" @click="loadDivergence">查询</button></div><section class="card warehouse-mobile-report-filter"><input v-model="divergenceFilters.date_from" type="date" aria-label="差异开始日期" /><input v-model="divergenceFilters.date_to" type="date" aria-label="差异结束日期" /><select v-model="divergenceFilters.production_id" aria-label="差异床次"><option value="">全部床次</option><option v-for="production in lookups.productions" :key="production.id" :value="production.id">{{ production.bed_no }} · {{ production.style_no }}</option></select><select v-model="divergenceFilters.material_id" aria-label="差异物料"><option value="">全部物料</option><option v-for="material in lookups.materials" :key="material.id" :value="material.id">{{ material.code }} · {{ material.name }}</option></select></section>
      <PageState :loading="divergenceLoading" :error="divergenceError" :empty="!divergenceData.rows.length" empty-text="当前筛选下没有已确认领料差异" @retry="loadDivergence"><div class="warehouse-mobile-list"><button v-for="(row, index) in divergenceData.rows" :key="index" class="card warehouse-mobile-card warehouse-mobile-clickable" @click="openDivergence(row)"><div class="warehouse-mobile-head"><div><h3>{{ row.bed_no }} · {{ row.material_code || row.lot_no }}</h3><p>{{ row.material_name || row.order_no }}</p></div><span class="badge" :class="row.over ? 'overdue' : row.unit_pending ? 'producing' : 'done'">{{ row.unit_pending ? '待补换算' : row.over ? '有超额' : '正常' }}</span></div><div class="warehouse-mobile-meta"><span>净领用 <strong>{{ number(row.qty_net) }}</strong></span><span>超领 <strong>{{ number(row.over_qty) }}</strong></span><span>快照定额 <strong>{{ number(row.qty_req_snapshot) }}</strong></span><span>当前定额 <strong>{{ row.qty_req_current ?? '—' }}</strong></span></div></button></div></PageState>
    </template>

    <template v-else-if="activeTab === 'trace'">
      <div class="warehouse-mobile-segment warehouse-trace-modes"><button :class="{ active: traceMode === 'reverse' }" @click="switchTraceMode('reverse')">款色反查</button><button :class="{ active: traceMode === 'lot' }" @click="switchTraceMode('lot')">批次正查</button><button :class="{ active: traceMode === 'ticket' }" @click="switchTraceMode('ticket')">菲票链路</button></div>
      <div v-if="traceError" class="form-error warehouse-action-error" role="alert">{{ traceError }}</div>
      <template v-if="traceMode === 'reverse'">
        <section class="card warehouse-mobile-filter warehouse-trace-filter"><input v-model="reverseFilters.style_no" placeholder="款号 / 款名（可空）" /><input v-model="reverseFilters.color" placeholder="颜色（可空，如 宝蓝）" list="warehouse-color-options" /><button class="button primary" :disabled="traceLoading" @click="loadReverseTrace">查询</button></section>
        <PageState :loading="traceLoading && !productionTrace" :error="''" :empty="!reverseMatches.length" empty-text="无匹配床次" @retry="loadReverseTrace"><div class="warehouse-mobile-list"><button v-for="match in reverseMatches" :key="match.production_id" class="card warehouse-mobile-card warehouse-mobile-clickable" @click="openProductionTrace(match.production_id)"><h3>{{ match.style_no }} · 床次 {{ match.bed_no }}</h3><p class="warehouse-mobile-line">客户：{{ match.customer_name || '—' }} · 来源单：{{ match.source_order_no || '—' }}</p><div class="warehouse-chip-list"><span v-for="lot in match.lots" :key="lot.id" class="warehouse-lot-chip">{{ lot.lot_no }}</span><span v-if="!match.lots.length">未挂批次</span></div></button></div></PageState>
      </template>
      <template v-else-if="traceMode === 'lot'">
        <section class="card warehouse-mobile-filter"><label class="search-box"><AppIcon name="search" :size="17" /><input v-model="traceKeyword" placeholder="批次号 / 面料 / 颜色" @keyup.enter="loadTraceLots" /></label><button class="warehouse-search-button" @click="loadTraceLots">查询</button></section>
        <PageState :loading="traceLoading && !lotTrace" :error="''" :empty="!traceLots.length" empty-text="无匹配批次" @retry="loadTraceLots"><div class="warehouse-mobile-list"><button v-for="lot in traceLots" :key="lot.id" class="card warehouse-mobile-card warehouse-mobile-clickable" @click="openTraceLot(lot)"><div class="warehouse-mobile-head"><h3>{{ lot.lot_no }}</h3><span>{{ lot.color || '—' }}</span></div><p class="warehouse-mobile-line">{{ lot.fabric_name || '—' }} · {{ lot.qty || 0 }}{{ lot.unit || '' }}</p><p class="warehouse-mobile-line">波及 {{ lot.production_count }} 个床次 / {{ lot.ticket_count }} 扎 / {{ number(lot.qty_total) }} 件</p></button></div></PageState>
        <section v-if="lotTrace" class="card warehouse-mobile-detail"><div class="warehouse-mobile-head"><div><h3>{{ lotTrace.lot.lot_no }} · 影响范围</h3><p>{{ lotTrace.lot.fabric_name }} · {{ lotTrace.lot.color || '无颜色' }}</p></div><button class="icon-button" aria-label="关闭批次追溯" @click="lotTrace = null"><AppIcon name="close" /></button></div><div class="warehouse-mobile-meta"><span>床次 <strong>{{ lotTrace.impact.production_count }}</strong></span><span>菲票 <strong>{{ lotTrace.impact.ticket_count }} 扎</strong></span><span>总件数 <strong>{{ number(lotTrace.impact.qty_total) }}</strong></span><span>来源 <strong>{{ lotTrace.lot.source_in_no || '—' }}</strong></span></div><button v-for="production in lotTrace.productions" :key="production.id" class="warehouse-mobile-subrow" @click="openProductionTrace(production.id)"><strong>{{ production.bed_no }} · {{ production.style_no }}</strong><span>{{ production.ticket_count }} 扎 · 用量 {{ number(production.issue_qty) }}</span></button></section>
      </template>
      <template v-else>
        <section class="card warehouse-mobile-filter warehouse-trace-filter"><select v-model="ticketId" aria-label="追溯菲票"><option value="">请选择本公司菲票</option><option v-for="ticket in lookups.tickets" :key="ticket.id" :value="ticket.id">{{ ticket.bed_no }} / 扎 {{ ticket.bundle_no }} · {{ ticket.style_no }} · {{ ticket.color }}/{{ ticket.size }}</option></select><button class="button primary" :disabled="lookupLoading || traceLoading" @click="loadTicketTrace">读取全链路</button></section>
        <section v-if="ticketTrace" class="card warehouse-mobile-detail"><div class="warehouse-mobile-head"><div><h3>{{ ticketTrace.ticket.bed_no }} / 扎 {{ ticketTrace.ticket.bundle_no }}</h3><p>{{ ticketTrace.ticket.qr_code }}</p></div><span class="badge done">{{ ticketTrace.ticket.qty }} 件</span></div><p class="warehouse-mobile-line">{{ ticketTrace.production.style_no }} · {{ ticketTrace.ticket.color }}/{{ ticketTrace.ticket.size }}</p><div class="warehouse-chip-list"><span v-for="lot in ticketTrace.lots" :key="lot.id" class="warehouse-lot-chip">{{ lot.lot_no }} · {{ lot.color }}</span></div><article v-for="record in ticketTrace.records" :key="record.id" class="warehouse-mobile-subrow"><strong>{{ record.process }} · {{ record.qty }} 件</strong><span>{{ record.user }} · {{ fullDateTime(record.registered_at) }}</span></article></section>
        <div v-else-if="!traceLoading" class="page-state"><span class="empty-symbol">⌁</span><p>选择菲票后查看批次、床次与登记时间线</p></div><div v-else class="page-state"><span class="spinner" />正在读取追溯链路…</div>
      </template>
      <section v-if="productionTrace" class="card warehouse-mobile-detail"><div class="warehouse-mobile-head"><div><h3>{{ productionTrace.production.bed_no }} · 床次追溯</h3><p>{{ productionTrace.production.style_no }} · {{ productionTrace.production.customer_name || '无客户' }}</p></div><button class="icon-button" aria-label="关闭床次追溯" @click="productionTrace = null"><AppIcon name="close" /></button></div><div class="warehouse-mobile-meta"><span>件数 <strong>{{ number(productionTrace.production.total_qty) }}</strong></span><span>菲票 <strong>{{ productionTrace.ticket_count }} 扎</strong></span></div><div class="warehouse-chip-list"><span v-for="lot in productionTrace.lots" :key="lot.id" class="warehouse-lot-chip">{{ lot.lot_no }} · 用量 {{ lot.qty }}</span></div><article v-for="(issue, index) in productionTrace.issues" :key="index" class="warehouse-mobile-subrow"><strong>{{ issue.issue_no }} · {{ issue.lot_no }}</strong><span>{{ issue.qty }} · {{ dateTime(issue.issue_at) }}</span></article></section>
    </template>
  </div>

  <div class="warehouse-desktop">
  <div class="welcome"><div><h2>仓库与追溯</h2><p>覆盖采购到料、IQC 入库、领退料、需求差异和批次全链路追溯。</p></div><div style="display:flex;gap:8px"><button class="button secondary" @click="modal = 'import'">导入期初库存</button><button class="button secondary" @click="listPaths[activeTab] ? loadList() : activeTab === 'demand' ? loadDemand() : activeTab === 'divergence' ? loadDivergence() : switchTraceMode(traceMode)"><AppIcon name="refresh" :size="16" />刷新</button></div></div>
  <nav class="warehouse-tabs" aria-label="仓库功能"><button v-for="tab in tabs" :key="tab[0]" :class="{ active: activeTab === tab[0] }" @click="switchTab(tab[0])">{{ tab[1] }}</button></nav>
  <div v-if="actionError" class="form-error warehouse-action-error" role="alert">{{ actionError }}</div>
  <div v-if="lookupError && !modal" class="form-error warehouse-action-error" role="alert">基础档案读取失败：{{ lookupError }}</div>

  <template v-if="activeTab === 'lots'">
    <div class="filter-bar"><label class="search-box"><AppIcon name="search" :size="17" /><input v-model="filters.lots.keyword" placeholder="搜索批次号、面料或颜色" @input="searchList('lots')" /></label><button class="button primary" v-if="canManageWarehouse" @click="openLot()">新增批次</button></div>
    <section class="card table-card"><PageState :loading="states.lots.loading" :error="states.lots.error" :empty="!lists.lots.results.length" empty-text="暂无库存批次" @retry="loadList('lots')"><div class="table-wrap desktop-table"><table><thead><tr><th>批次 / 面料</th><th>颜色</th><th class="numeric">批次登记量</th><th class="numeric">批次单价</th><th>批次日期</th><th>来源入库单</th><th>操作</th></tr></thead><tbody><tr v-for="item in lists.lots.results" :key="item.id"><td><span class="cell-title">{{ item.lot_no }}</span><small class="cell-subtitle">{{ item.fabric_name || '未填写面料' }}</small></td><td>{{ item.color || '—' }}</td><td class="numeric">{{ number(item.qty) }} {{ item.unit }}</td><td class="numeric">{{ item.unit_cost === null ? '待补价' : money(item.unit_cost) }}</td><td>{{ item.batch_date || '—' }}</td><td>{{ item.source_in_no || '手工 / 其他' }}</td><td><div class="warehouse-row-actions"><button class="button ghost small" @click="openLot(item)">编辑</button><button class="button ghost small" @click="goTraceLot(item)">追溯</button><button class="button ghost small warehouse-danger-text" :disabled="actionId === `lot-${item.id}`" v-if="canManageWarehouse" @click="deleteLot(item)">删除</button></div></td></tr></tbody></table></div><div class="mobile-cards"><article v-for="item in lists.lots.results" :key="item.id" class="mobile-row"><div class="mobile-row-head"><div><div class="mobile-row-title">{{ item.lot_no }}</div><small class="cell-subtitle">{{ item.fabric_name }} · {{ item.color || '无颜色' }}</small></div><strong>{{ number(item.qty) }} {{ item.unit }}</strong></div><div class="mobile-meta"><span>单价 <strong>{{ item.unit_cost === null ? '待补价' : money(item.unit_cost) }}</strong></span><span>日期 <strong>{{ item.batch_date || '—' }}</strong></span></div><div class="warehouse-mobile-actions"><button class="button secondary small" @click="openLot(item)">编辑</button><button class="button secondary small" @click="goTraceLot(item)">追溯</button><button class="button danger small" v-if="canManageWarehouse" @click="deleteLot(item)">删除</button></div></article></div></PageState><div class="pagination"><button class="button ghost small" :disabled="filters.lots.page <= 1" @click="changePage('lots', -1)">上一页</button><span>第 {{ filters.lots.page }} 页 · 共 {{ lists.lots.count || 0 }} 条</span><button class="button ghost small" :disabled="filters.lots.page * PAGE_SIZE >= (lists.lots.count || 0)" @click="changePage('lots', 1)">下一页</button></div></section>
  </template>

  <template v-if="activeTab === 'suppliers'">
    <div class="filter-bar"><label class="search-box"><AppIcon name="search" :size="17" /><input v-model="filters.suppliers.keyword" placeholder="搜索供应商、联系人或电话" @input="searchList('suppliers')" /></label><button class="button primary" v-if="canManageWarehouse" @click="openSupplier()">新增供应商</button></div>
    <section class="card table-card"><PageState :loading="states.suppliers.loading" :error="states.suppliers.error" :empty="!lists.suppliers.results.length" empty-text="暂无供应商档案" @retry="loadList('suppliers')"><div class="table-wrap desktop-table"><table><thead><tr><th>编码 / 名称</th><th>联系人</th><th>电话</th><th>结算方式</th><th>评级</th><th>备注</th><th>操作</th></tr></thead><tbody><tr v-for="item in lists.suppliers.results" :key="item.id"><td><span class="cell-title">{{ item.code }} · {{ item.name }}</span></td><td>{{ item.contact_person || '—' }}</td><td>{{ item.phone || '—' }}</td><td>{{ item.settle_method || '—' }}</td><td>{{ item.rating || '—' }}</td><td>{{ item.remark || '—' }}</td><td><div class="warehouse-row-actions"><button class="button ghost small" @click="openSupplier(item)">编辑</button><button class="button ghost small warehouse-danger-text" v-if="canManageWarehouse" @click="deleteSupplier(item)">删除</button></div></td></tr></tbody></table></div><div class="mobile-cards"><article v-for="item in lists.suppliers.results" :key="item.id" class="mobile-row"><div class="mobile-row-head"><div><div class="mobile-row-title">{{ item.code }} · {{ item.name }}</div><small class="cell-subtitle">{{ item.contact_person || '无联系人' }} · {{ item.phone || '无电话' }}</small></div><span class="badge">{{ item.rating || '未评级' }}</span></div><div class="mobile-meta"><span>结算 <strong>{{ item.settle_method || '—' }}</strong></span><span>备注 <strong>{{ item.remark || '—' }}</strong></span></div><div class="warehouse-mobile-actions"><button class="button secondary small" @click="openSupplier(item)">编辑</button><button class="button danger small" v-if="canManageWarehouse" @click="deleteSupplier(item)">删除</button></div></article></div></PageState><div class="pagination"><button class="button ghost small" :disabled="filters.suppliers.page <= 1" @click="changePage('suppliers', -1)">上一页</button><span>第 {{ filters.suppliers.page }} 页 · 共 {{ lists.suppliers.count || 0 }} 条</span><button class="button ghost small" :disabled="filters.suppliers.page * PAGE_SIZE >= (lists.suppliers.count || 0)" @click="changePage('suppliers', 1)">下一页</button></div></section>
  </template>

  <template v-if="activeTab === 'purchases'">
    <div class="filter-bar"><select v-model="filters.purchases.status" @change="filters.purchases.page = 1; loadList('purchases')"><option value="">全部状态</option><option value="draft">草稿</option><option value="done">已完成</option></select><button class="button primary" v-if="canManageWarehouse" @click="openPurchase()">新增采购单</button></div>
    <section class="card table-card"><PageState :loading="states.purchases.loading" :error="states.purchases.error" :empty="!lists.purchases.results.length" empty-text="暂无采购单" @retry="loadList('purchases')"><div class="table-wrap desktop-table"><table><thead><tr><th>采购单号</th><th>供应商</th><th class="numeric">采购总量</th><th class="numeric">明细</th><th>采购日期</th><th>状态</th><th>操作</th></tr></thead><tbody><tr v-for="item in lists.purchases.results" :key="item.id"><td><button class="warehouse-doc-link" @click="openPurchase(item)">{{ item.order_no }}</button><small class="cell-subtitle">{{ item.remark || '' }}</small></td><td>{{ item.supplier_name || '—' }}</td><td class="numeric">{{ number(item.total_qty) }}</td><td class="numeric">{{ itemCount(item) }} 行</td><td>{{ item.ordered_at }}</td><td><span class="badge" :class="statusClass(item.status)">{{ statusText[item.status] }}</span></td><td><div class="warehouse-row-actions"><button class="button ghost small" @click="openPurchase(item)">{{ item.status === 'draft' ? '编辑' : '详情' }}</button><button v-if="item.status === 'draft'" class="button ghost small" @click="documentAction('purchase', 'confirm', item)">确认</button><button v-if="item.status === 'draft'" class="button ghost small warehouse-danger-text" @click="documentAction('purchase', 'delete', item)">删除</button></div></td></tr></tbody></table></div><div class="mobile-cards"><article v-for="item in lists.purchases.results" :key="item.id" class="mobile-row"><div class="mobile-row-head"><button class="warehouse-doc-link" @click="openPurchase(item)">{{ item.order_no }}</button><span class="badge" :class="statusClass(item.status)">{{ statusText[item.status] }}</span></div><div class="mobile-meta"><span>供应商 <strong>{{ item.supplier_name || '—' }}</strong></span><span>日期 <strong>{{ item.ordered_at }}</strong></span><span>数量 <strong>{{ number(item.total_qty) }}</strong></span><span>明细 <strong>{{ itemCount(item) }} 行</strong></span></div><div class="warehouse-mobile-actions"><button class="button secondary small" @click="openPurchase(item)">{{ item.status === 'draft' ? '编辑' : '详情' }}</button><template v-if="item.status === 'draft'"><button class="button primary small" v-if="canManageWarehouse" @click="documentAction('purchase', 'confirm', item)">确认</button><button class="button danger small" v-if="canManageWarehouse" @click="documentAction('purchase', 'delete', item)">删除</button></template></div></article></div></PageState><div class="pagination"><button class="button ghost small" :disabled="filters.purchases.page <= 1" @click="changePage('purchases', -1)">上一页</button><span>第 {{ filters.purchases.page }} 页 · 共 {{ lists.purchases.count || 0 }} 条</span><button class="button ghost small" :disabled="filters.purchases.page * PAGE_SIZE >= (lists.purchases.count || 0)" @click="changePage('purchases', 1)">下一页</button></div></section>
  </template>

  <template v-if="activeTab === 'stockIns'">
    <div class="filter-bar"><select v-model="filters.stockIns.status" @change="filters.stockIns.page = 1; loadList('stockIns')"><option value="">全部状态</option><option value="draft">草稿</option><option value="done">已完成</option></select><button class="button primary" v-if="canManageWarehouse" @click="openStock()">新增入库单</button></div>
    <section class="card table-card"><PageState :loading="states.stockIns.loading" :error="states.stockIns.error" :empty="!lists.stockIns.results.length" empty-text="暂无入库单" @retry="loadList('stockIns')"><div class="table-wrap desktop-table"><table><thead><tr><th>入库单号</th><th>来源 / 采购单</th><th class="numeric">明细</th><th>入库时间</th><th>状态</th><th>操作</th></tr></thead><tbody><tr v-for="item in lists.stockIns.results" :key="item.id"><td><button class="warehouse-doc-link" @click="openStock(item)">{{ item.in_no }}</button><small class="cell-subtitle">{{ item.remark || '' }}</small></td><td>{{ sourceText[item.source_type] || item.source_type }}<small class="cell-subtitle">{{ item.purchase_no || '无关联采购单' }}</small></td><td class="numeric">{{ itemCount(item) }} 行</td><td>{{ dateTime(item.in_at) }}</td><td><span class="badge" :class="statusClass(item.status)">{{ statusText[item.status] }}</span></td><td><div class="warehouse-row-actions"><button class="button ghost small" @click="openStock(item)">{{ item.status === 'draft' ? '编辑' : '详情' }}</button><button v-if="item.status === 'draft'" class="button ghost small" @click="documentAction('stock', 'confirm', item)">确认</button><button v-if="item.status === 'draft'" class="button ghost small warehouse-danger-text" @click="documentAction('stock', 'delete', item)">删除</button></div></td></tr></tbody></table></div><div class="mobile-cards"><article v-for="item in lists.stockIns.results" :key="item.id" class="mobile-row"><div class="mobile-row-head"><button class="warehouse-doc-link" @click="openStock(item)">{{ item.in_no }}</button><span class="badge" :class="statusClass(item.status)">{{ statusText[item.status] }}</span></div><div class="mobile-meta"><span>来源 <strong>{{ sourceText[item.source_type] }}</strong></span><span>采购单 <strong>{{ item.purchase_no || '—' }}</strong></span><span>时间 <strong>{{ dateTime(item.in_at) }}</strong></span><span>明细 <strong>{{ itemCount(item) }} 行</strong></span></div><div class="warehouse-mobile-actions"><button class="button secondary small" @click="openStock(item)">{{ item.status === 'draft' ? '编辑' : '详情' }}</button><template v-if="item.status === 'draft'"><button class="button primary small" v-if="canManageWarehouse" @click="documentAction('stock', 'confirm', item)">确认</button><button class="button danger small" v-if="canManageWarehouse" @click="documentAction('stock', 'delete', item)">删除</button></template></div></article></div></PageState><div class="pagination"><button class="button ghost small" :disabled="filters.stockIns.page <= 1" @click="changePage('stockIns', -1)">上一页</button><span>第 {{ filters.stockIns.page }} 页 · 共 {{ lists.stockIns.count || 0 }} 条</span><button class="button ghost small" :disabled="filters.stockIns.page * PAGE_SIZE >= (lists.stockIns.count || 0)" @click="changePage('stockIns', 1)">下一页</button></div></section>
  </template>

  <template v-if="activeTab === 'issues'">
    <div class="filter-bar"><select v-model="filters.issues.status" @change="filters.issues.page = 1; loadList('issues')"><option value="">全部状态</option><option value="draft">草稿</option><option value="confirmed">已确认</option></select><button class="button primary" v-if="canManageWarehouse" @click="openIssue()">新增领 / 退料单</button></div>
    <section class="card table-card"><PageState :loading="states.issues.loading" :error="states.issues.error" :empty="!lists.issues.results.length" empty-text="暂无领料或退料单" @retry="loadList('issues')"><div class="table-wrap desktop-table"><table><thead><tr><th>单据号</th><th>类型</th><th class="numeric">明细</th><th>超额行</th><th>领料时间</th><th>状态</th><th>操作</th></tr></thead><tbody><tr v-for="item in lists.issues.results" :key="item.id"><td><button class="warehouse-doc-link" @click="openIssue(item)">{{ item.issue_no }}</button><small class="cell-subtitle">{{ item.remark || '' }}</small></td><td>{{ issueTypeText[item.type] }}</td><td class="numeric">{{ itemCount(item) }} 行</td><td class="numeric">{{ item.items?.filter((line) => line.over || line.over_state === 'over').length || 0 }}</td><td>{{ dateTime(item.issue_at) }}</td><td><span class="badge" :class="statusClass(item.status)">{{ statusText[item.status] }}</span></td><td><div class="warehouse-row-actions"><button class="button ghost small" @click="openIssue(item)">{{ item.status === 'draft' ? '编辑' : '详情' }}</button><button v-if="item.status === 'draft'" class="button ghost small" @click="documentAction('issue', 'confirm', item)">确认</button><button v-if="item.status === 'draft'" class="button ghost small warehouse-danger-text" @click="documentAction('issue', 'delete', item)">删除</button></div></td></tr></tbody></table></div><div class="mobile-cards"><article v-for="item in lists.issues.results" :key="item.id" class="mobile-row"><div class="mobile-row-head"><button class="warehouse-doc-link" @click="openIssue(item)">{{ item.issue_no }}</button><span class="badge" :class="statusClass(item.status)">{{ statusText[item.status] }}</span></div><div class="mobile-meta"><span>类型 <strong>{{ issueTypeText[item.type] }}</strong></span><span>明细 <strong>{{ itemCount(item) }} 行</strong></span><span>时间 <strong>{{ dateTime(item.issue_at) }}</strong></span><span>超额 <strong>{{ item.items?.filter((line) => line.over).length || 0 }} 行</strong></span></div><div class="warehouse-mobile-actions"><button class="button secondary small" @click="openIssue(item)">{{ item.status === 'draft' ? '编辑' : '详情' }}</button><template v-if="item.status === 'draft'"><button class="button primary small" v-if="canManageWarehouse" @click="documentAction('issue', 'confirm', item)">确认</button><button class="button danger small" v-if="canManageWarehouse" @click="documentAction('issue', 'delete', item)">删除</button></template></div></article></div></PageState><div class="pagination"><button class="button ghost small" :disabled="filters.issues.page <= 1" @click="changePage('issues', -1)">上一页</button><span>第 {{ filters.issues.page }} 页 · 共 {{ lists.issues.count || 0 }} 条</span><button class="button ghost small" :disabled="filters.issues.page * PAGE_SIZE >= (lists.issues.count || 0)" @click="changePage('issues', 1)">下一页</button></div></section>
  </template>

  <template v-if="activeTab === 'receipts'">
    <div class="filter-bar"><label class="search-box"><AppIcon name="search" :size="17" /><input v-model="filters.receipts.keyword" placeholder="搜索暂收单、供应商或面料" @input="searchList('receipts')" /></label><select v-model="filters.receipts.status" @change="filters.receipts.page = 1; loadList('receipts')"><option value="">全部状态</option><option value="draft">暂收</option><option value="confirmed">已确认</option></select><button class="button primary" v-if="canManageWarehouse" @click="openReceipt()">新增暂收单</button></div>
    <section class="card table-card"><PageState :loading="states.receipts.loading" :error="states.receipts.error" :empty="!lists.receipts.results.length" empty-text="暂无到料暂收单" @retry="loadList('receipts')"><div class="table-wrap desktop-table"><table><thead><tr><th>暂收单号</th><th>供应商 / 采购单</th><th class="numeric">明细</th><th>IQC 合格 / 拒收</th><th>确认信息</th><th>状态</th><th>操作</th></tr></thead><tbody><tr v-for="item in lists.receipts.results" :key="item.id"><td><button class="warehouse-doc-link" @click="openReceipt(item)">{{ item.receipt_no }}</button></td><td>{{ item.supplier_name || '—' }}<small class="cell-subtitle">{{ item.purchase_no || '无单直收' }}</small></td><td class="numeric">{{ itemCount(item) }} 行</td><td class="numeric">{{ number(item.items?.reduce((sum, line) => sum + Number(line.qualified_qty || 0), 0)) }} / {{ number(item.items?.reduce((sum, line) => sum + Number(line.rejected_qty || 0), 0)) }}</td><td>{{ item.confirmed_at ? dateTime(item.confirmed_at) : '—' }}<small class="cell-subtitle">{{ item.confirmed_by_name || '' }}</small></td><td><span class="badge" :class="statusClass(item.status)">{{ statusText[item.status] }}</span></td><td><div class="warehouse-row-actions"><button class="button ghost small" @click="openReceipt(item)">{{ item.status === 'draft' ? 'IQC / 编辑' : '详情' }}</button><button v-if="item.status === 'draft'" class="button ghost small" @click="documentAction('receipt', 'confirm', item)">确认</button><button v-if="item.status === 'draft'" class="button ghost small warehouse-danger-text" @click="documentAction('receipt', 'delete', item)">删除</button></div></td></tr></tbody></table></div><div class="mobile-cards"><article v-for="item in lists.receipts.results" :key="item.id" class="mobile-row"><div class="mobile-row-head"><button class="warehouse-doc-link" @click="openReceipt(item)">{{ item.receipt_no }}</button><span class="badge" :class="statusClass(item.status)">{{ statusText[item.status] }}</span></div><div class="mobile-meta"><span>供应商 <strong>{{ item.supplier_name || '—' }}</strong></span><span>采购单 <strong>{{ item.purchase_no || '无单直收' }}</strong></span><span>明细 <strong>{{ itemCount(item) }} 行</strong></span><span>确认 <strong>{{ item.confirmed_at ? dateTime(item.confirmed_at) : '—' }}</strong></span></div><div class="warehouse-mobile-actions"><button class="button secondary small" @click="openReceipt(item)">{{ item.status === 'draft' ? 'IQC / 编辑' : '详情' }}</button><template v-if="item.status === 'draft'"><button class="button primary small" v-if="canManageWarehouse" @click="documentAction('receipt', 'confirm', item)">确认</button><button class="button danger small" @click="documentAction('receipt', 'delete', item)">删除</button></template></div></article></div></PageState><div class="pagination"><button class="button ghost small" :disabled="filters.receipts.page <= 1" @click="changePage('receipts', -1)">上一页</button><span>第 {{ filters.receipts.page }} 页 · 共 {{ lists.receipts.count || 0 }} 条</span><button class="button ghost small" :disabled="filters.receipts.page * PAGE_SIZE >= (lists.receipts.count || 0)" @click="changePage('receipts', 1)">下一页</button></div></section>
  </template>

  <template v-if="activeTab === 'demand'">
    <div class="filter-bar"><select v-model="demandFilters.style_id" @change="loadDemand"><option value="">全部款式</option><option v-for="style in lookups.styles" :key="style.id" :value="style.id">{{ style.style_no }} · {{ style.name }}</option></select><select v-model="demandFilters.customer_id" @change="loadDemand"><option value="">全部客户</option><option v-for="customer in lookups.customers" :key="customer.id" :value="customer.id">{{ customer.customer_no }} · {{ customer.name }}</option></select><button class="button secondary" @click="loadDemand">重新计算</button></div>
    <div class="stats-grid warehouse-demand-stats"><div class="stat-card"><div class="stat-label">订单范围</div><div class="stat-value">{{ demandData.summary.order_total || 0 }}</div><div class="stat-note">已排完 {{ demandData.summary.order_planned_full || 0 }} · 无生效 BOM {{ demandData.summary.order_no_bom || 0 }}</div></div><div class="stat-card"><div class="stat-label">物料行</div><div class="stat-value">{{ demandData.summary.material_cnt || 0 }}</div><div class="stat-note">订单空差需采购 {{ demandData.summary.need_cnt || 0 }} 行</div></div><div class="stat-card"><div class="stat-label">补货建议</div><div class="stat-value">{{ demandData.summary.reorder_cnt || 0 }}</div><div class="stat-note">含安全库存水位触发</div></div><div class="stat-card"><div class="stat-label">安全库存预警</div><div class="stat-value">{{ demandData.summary.safety_alert_cnt || 0 }}</div><div class="stat-note">待补换算 {{ demandData.summary.conversion_pending_cnt || 0 }} 组</div></div></div>
    <section class="card table-card"><PageState :loading="demandLoading" :error="demandError" :empty="!demandData.rows.length" empty-text="当前筛选下没有需求或安全库存物料" @retry="loadDemand"><div class="table-wrap desktop-table"><table><thead><tr><th>物料</th><th class="numeric">订单需求</th><th class="numeric">当前库存</th><th class="numeric">在途</th><th class="numeric">安全库存</th><th class="numeric">订单空差</th><th class="numeric">补货建议</th><th>操作</th></tr></thead><tbody><tr v-for="row in demandData.rows" :key="row.material_id"><td><span class="cell-title">{{ row.material_code }} · {{ row.material_name }}</span><small class="cell-subtitle">单位：{{ row.unit }}</small></td><td class="numeric">{{ number(row.qty_demand) }}</td><td class="numeric">{{ number(row.qty_stock) }}</td><td class="numeric">{{ number(row.qty_in_transit) }}</td><td class="numeric">{{ number(row.safety_stock) }}</td><td class="numeric" :class="{ 'warehouse-negative': Number(row.qty_suggested) < 0 }">{{ number(row.qty_suggested) }}</td><td class="numeric"><strong :class="{ 'warehouse-warning': Number(row.qty_reorder) > 0 }">{{ number(row.qty_reorder) }} {{ row.unit }}</strong></td><td><button class="button ghost small" :disabled="Number(row.qty_reorder) <= 0" @click="createFromDemand(row)">生成采购草稿</button></td></tr></tbody></table></div><div class="mobile-cards"><article v-for="row in demandData.rows" :key="row.material_id" class="mobile-row"><div class="mobile-row-head"><div><div class="mobile-row-title">{{ row.material_code }} · {{ row.material_name }}</div><small class="cell-subtitle">{{ row.unit }}</small></div><strong :class="{ 'warehouse-warning': Number(row.qty_reorder) > 0 }">补 {{ number(row.qty_reorder) }}</strong></div><div class="mobile-meta"><span>需求 <strong>{{ number(row.qty_demand) }}</strong></span><span>库存 <strong>{{ number(row.qty_stock) }}</strong></span><span>在途 <strong>{{ number(row.qty_in_transit) }}</strong></span><span>安全库存 <strong>{{ number(row.safety_stock) }}</strong></span></div><div class="warehouse-mobile-actions"><button class="button secondary small" :disabled="Number(row.qty_reorder) <= 0" @click="createFromDemand(row)">生成采购草稿</button></div></article></div></PageState><p class="section-note warehouse-note">订单空差 = 需求 − 库存 − 在途，可为负；补货建议另计安全库存且不为负。库存含草稿入库流水，单位换算缺参行不入合计。</p></section>
  </template>

  <template v-if="activeTab === 'divergence'">
    <div class="filter-bar warehouse-report-filters"><input v-model="divergenceFilters.date_from" type="date" aria-label="开始日期" /><input v-model="divergenceFilters.date_to" type="date" aria-label="结束日期" /><select v-model="divergenceFilters.order_id"><option value="">全部订单</option><option v-for="order in lookups.orders" :key="order.id" :value="order.id">{{ order.order_no }} · {{ order.style_no }}</option></select><select v-model="divergenceFilters.production_id"><option value="">全部床次</option><option v-for="production in lookups.productions" :key="production.id" :value="production.id">{{ production.bed_no }} · {{ production.style_no }}</option></select><select v-model="divergenceFilters.material_id"><option value="">全部物料</option><option v-for="material in lookups.materials" :key="material.id" :value="material.id">{{ material.code }} · {{ material.name }}</option></select><button class="button primary" @click="loadDivergence">查询</button></div>
    <div class="stats-grid"><div class="stat-card"><div class="stat-label">已发料（主单位合计）</div><div class="stat-value warehouse-stat-small">{{ number(divergenceData.summary.issued_total) }}</div></div><div class="stat-card"><div class="stat-label">超领量</div><div class="stat-value warehouse-stat-small">{{ number(divergenceData.summary.over_total) }}</div><div class="stat-note">超领率 {{ divergenceData.summary.over_ratio ?? '—' }}%</div></div><div class="stat-card"><div class="stat-label">无定额领料</div><div class="stat-value warehouse-stat-small">{{ number(divergenceData.summary.no_req_total) }}</div><div class="stat-note">占比 {{ divergenceData.summary.no_req_ratio ?? '—' }}%</div></div><div class="stat-card"><div class="stat-label">待补单位参数</div><div class="stat-value">{{ divergenceData.summary.pending_count || 0 }}</div></div></div>
    <section class="card table-card"><PageState :loading="divergenceLoading" :error="divergenceError" :empty="!divergenceData.rows.length" empty-text="当前筛选下没有已确认领料差异" @retry="loadDivergence"><div class="table-wrap desktop-table"><table><thead><tr><th>床次 / 订单</th><th>物料</th><th class="numeric">净领用</th><th class="numeric">发料定额</th><th class="numeric">当前 BOM 定额</th><th class="numeric">超领量</th><th class="numeric">无定额量</th><th>状态</th></tr></thead><tbody><tr v-for="(row, index) in divergenceData.rows" :key="`${row.production_id}-${row.material_id}-${row.lot_no}-${index}`" class="clickable" @click="openDivergence(row)"><td><span class="cell-title">{{ row.bed_no }} · {{ row.style_no }}</span><small class="cell-subtitle">{{ row.order_no || '未挂单' }}</small></td><td>{{ row.material_code || row.lot_no }}<small class="cell-subtitle">{{ row.material_name || '未关联物料' }}</small></td><td class="numeric">{{ number(row.qty_net) }} {{ row.material_unit }}</td><td class="numeric">{{ number(row.qty_req_snapshot) }}</td><td class="numeric">{{ row.qty_req_current === null ? '—' : number(row.qty_req_current) }}</td><td class="numeric warehouse-warning">{{ number(row.over_qty) }}</td><td class="numeric">{{ number(row.no_req_qty) }}</td><td><span v-if="row.unit_pending" class="badge producing">待补换算</span><span v-else-if="row.over" class="badge overdue">有超额</span><span v-else class="badge done">正常</span></td></tr></tbody></table></div><div class="mobile-cards"><button v-for="(row, index) in divergenceData.rows" :key="index" class="mobile-row" @click="openDivergence(row)"><div class="mobile-row-head"><div><div class="mobile-row-title">{{ row.bed_no }} · {{ row.material_code || row.lot_no }}</div><small class="cell-subtitle">{{ row.material_name || row.order_no }}</small></div><span class="badge" :class="row.over ? 'overdue' : row.unit_pending ? 'producing' : 'done'">{{ row.unit_pending ? '待补换算' : row.over ? '有超额' : '正常' }}</span></div><div class="mobile-meta"><span>净领用 <strong>{{ number(row.qty_net) }}</strong></span><span>超领 <strong>{{ number(row.over_qty) }}</strong></span><span>快照定额 <strong>{{ number(row.qty_req_snapshot) }}</strong></span><span>当前定额 <strong>{{ row.qty_req_current ?? '—' }}</strong></span></div></button></div></PageState><p class="section-note warehouse-note">只统计已确认领退料。快照定额与当前生效 BOM 定额并列，改版差异不会被覆盖；点击行查看超额流水。</p></section>
    <section v-if="divergenceData.pending_units.length" class="card warehouse-pending"><div class="card-header"><h3>待补换算参数流水</h3><span class="badge producing">{{ divergenceData.pending_units.length }} 行</span></div><div class="table-wrap"><table><thead><tr><th>领料单</th><th>批次</th><th>批次单位 → 物料单位</th><th class="numeric">数量</th></tr></thead><tbody><tr v-for="(line, index) in divergenceData.pending_units" :key="index"><td>{{ line.issue_no }}</td><td>{{ line.lot_no }}</td><td>{{ line.lot_unit }} → {{ line.material_unit }}</td><td class="numeric">{{ line.qty }}</td></tr></tbody></table></div></section>
  </template>

  <template v-if="activeTab === 'trace'">
    <div class="filter-bar"><div class="segmented"><button :class="{ active: traceMode === 'lot' }" @click="switchTraceMode('lot')">批次正向追溯</button><button :class="{ active: traceMode === 'reverse' }" @click="switchTraceMode('reverse')">款色反向追溯</button><button :class="{ active: traceMode === 'ticket' }" @click="switchTraceMode('ticket')">菲票全链路</button></div></div>
    <div v-if="traceError" class="form-error warehouse-action-error" role="alert">{{ traceError }}</div>
    <template v-if="traceMode === 'lot'">
      <div class="filter-bar"><label class="search-box"><AppIcon name="search" :size="17" /><input v-model="traceKeyword" placeholder="输入批次号、面料或颜色" @keyup.enter="loadTraceLots" /></label><button class="button primary" @click="loadTraceLots">查询批次</button></div>
      <section class="card table-card"><PageState :loading="traceLoading && !lotTrace" :error="''" :empty="!traceLots.length" empty-text="没有符合条件的批次" @retry="loadTraceLots"><div class="table-wrap desktop-table"><table><thead><tr><th>批次 / 面料</th><th>颜色</th><th class="numeric">批次量</th><th class="numeric">影响床次</th><th class="numeric">影响菲票</th><th class="numeric">菲票件数</th><th></th></tr></thead><tbody><tr v-for="lot in traceLots" :key="lot.id" class="clickable" @click="openTraceLot(lot)"><td><span class="cell-title">{{ lot.lot_no }}</span><small class="cell-subtitle">{{ lot.fabric_name }}</small></td><td>{{ lot.color || '—' }}</td><td class="numeric">{{ number(lot.qty) }} {{ lot.unit }}</td><td class="numeric">{{ lot.production_count }}</td><td class="numeric">{{ lot.ticket_count }}</td><td class="numeric">{{ number(lot.qty_total) }}</td><td><AppIcon name="chevron" :size="16" /></td></tr></tbody></table></div><div class="mobile-cards"><button v-for="lot in traceLots" :key="lot.id" class="mobile-row" @click="openTraceLot(lot)"><div class="mobile-row-head"><div><div class="mobile-row-title">{{ lot.lot_no }}</div><small class="cell-subtitle">{{ lot.fabric_name }} · {{ lot.color }}</small></div><strong>{{ number(lot.qty) }} {{ lot.unit }}</strong></div><div class="mobile-meta"><span>床次 <strong>{{ lot.production_count }}</strong></span><span>菲票 <strong>{{ lot.ticket_count }}</strong></span></div></button></div></PageState><p class="section-note warehouse-note">追溯搜索最多返回 100 条批次；请输入更精确的条件缩小范围。</p></section>
      <section v-if="lotTrace" class="card warehouse-trace-detail"><div class="card-header"><div><h3>{{ lotTrace.lot.lot_no }} · 影响范围</h3><p>{{ lotTrace.lot.fabric_name }} · {{ lotTrace.lot.color || '无颜色' }}</p></div><span class="badge producing">{{ lotTrace.impact.production_count }} 床次</span></div><div class="detail-grid"><div class="detail-item"><small>影响菲票</small><strong>{{ lotTrace.impact.ticket_count }} 扎</strong></div><div class="detail-item"><small>菲票总件数</small><strong>{{ number(lotTrace.impact.qty_total) }}</strong></div><div class="detail-item"><small>来源入库单</small><strong>{{ lotTrace.lot.source_in_no || '—' }}</strong></div></div><div class="table-wrap"><table><thead><tr><th>床次 / 款号</th><th>客户 / 来源单</th><th class="numeric">床次件数</th><th class="numeric">批次用量</th><th class="numeric">菲票</th><th></th></tr></thead><tbody><tr v-for="production in lotTrace.productions" :key="production.id" class="clickable" @click="openProductionTrace(production.id)"><td><span class="cell-title">{{ production.bed_no }}</span><small class="cell-subtitle">{{ production.style_no }} · {{ production.style_name }}</small></td><td>{{ production.customer_name || '—' }}<small class="cell-subtitle">{{ production.source_order_no || '—' }}</small></td><td class="numeric">{{ number(production.total_qty) }}</td><td class="numeric">{{ number(production.issue_qty) }}</td><td class="numeric">{{ production.ticket_count }}</td><td><button class="button ghost small">床次详情</button></td></tr></tbody></table></div></section>
    </template>
    <template v-if="traceMode === 'reverse'">
      <div class="filter-bar warehouse-report-filters"><input v-model="reverseFilters.style_no" placeholder="款号或款式名称" /><input v-model="reverseFilters.color" placeholder="菲票颜色" list="warehouse-color-options" /><select v-model="reverseFilters.customer_id"><option value="">全部客户</option><option v-for="customer in lookups.customers" :key="customer.id" :value="customer.id">{{ customer.customer_no }} · {{ customer.name }}</option></select><input v-model="reverseFilters.date_from" type="date" aria-label="创建开始日期" /><input v-model="reverseFilters.date_to" type="date" aria-label="创建结束日期" /><button class="button primary" @click="loadReverseTrace">查询</button></div>
      <section class="card table-card"><PageState :loading="traceLoading && !productionTrace" :error="''" :empty="!reverseMatches.length" empty-text="没有符合条件的床次批次关系" @retry="loadReverseTrace"><div class="table-wrap desktop-table"><table><thead><tr><th>床次 / 款式</th><th>客户 / 来源单</th><th class="numeric">床次件数</th><th>关联批次</th><th>创建时间</th><th></th></tr></thead><tbody><tr v-for="match in reverseMatches" :key="match.production_id"><td><span class="cell-title">{{ match.bed_no }}</span><small class="cell-subtitle">{{ match.style_no }} · {{ match.style_name }}</small></td><td>{{ match.customer_name || '—' }}<small class="cell-subtitle">{{ match.source_order_no || '—' }}</small></td><td class="numeric">{{ number(match.total_qty) }}</td><td><span v-for="lot in match.lots" :key="lot.id" class="warehouse-lot-chip">{{ lot.lot_no }} · {{ lot.color || '无颜色' }}（{{ number(lot.qty) }}）</span><span v-if="!match.lots.length">未关联批次</span></td><td>{{ dateTime(match.created_at) }}</td><td><button class="button ghost small" @click="openProductionTrace(match.production_id)">追溯</button></td></tr></tbody></table></div><div class="mobile-cards"><article v-for="match in reverseMatches" :key="match.production_id" class="mobile-row"><div class="mobile-row-head"><div><div class="mobile-row-title">{{ match.bed_no }} · {{ match.style_no }}</div><small class="cell-subtitle">{{ match.customer_name || '无客户' }}</small></div><strong>{{ number(match.total_qty) }} 件</strong></div><p class="warehouse-mobile-lots">{{ match.lots.map((lot) => `${lot.lot_no}(${lot.qty})`).join('、') || '未关联批次' }}</p><div class="warehouse-mobile-actions"><button class="button secondary small" @click="openProductionTrace(match.production_id)">查看追溯</button></div></article></div></PageState><p class="section-note warehouse-note">反向追溯最多返回 100 个床次，颜色按菲票自由文本匹配。</p></section>
    </template>
    <template v-if="traceMode === 'ticket'">
      <div class="filter-bar"><select v-model="ticketId" class="warehouse-ticket-select"><option value="">请选择本公司菲票</option><option v-for="ticket in lookups.tickets" :key="ticket.id" :value="ticket.id">{{ ticket.bed_no }} / 扎 {{ ticket.bundle_no }} · {{ ticket.style_no }} · {{ ticket.color }}/{{ ticket.size }}</option></select><button class="button primary" :disabled="lookupLoading" @click="loadTicketTrace">读取全链路</button></div>
      <section v-if="traceLoading" class="card"><div class="page-state"><span class="spinner" />正在读取追溯链路…</div></section>
      <section v-else-if="!ticketTrace" class="card"><div class="page-state"><span class="empty-symbol">⌁</span><p>选择菲票后查看批次、床次与登记时间线</p></div></section>
      <section v-else class="card warehouse-trace-detail"><div class="card-header"><div><h3>{{ ticketTrace.ticket.bed_no }} / 扎 {{ ticketTrace.ticket.bundle_no }}</h3><p>{{ ticketTrace.ticket.qr_code }}</p></div><span class="badge done">{{ ticketTrace.ticket.qty }} 件</span></div><div class="detail-grid"><div class="detail-item"><small>款式 / 客户</small><strong>{{ ticketTrace.production.style_no }} · {{ ticketTrace.production.customer_name || '—' }}</strong></div><div class="detail-item"><small>颜色 / 尺码</small><strong>{{ ticketTrace.ticket.color }} / {{ ticketTrace.ticket.size }}</strong></div><div class="detail-item"><small>打印</small><strong>{{ ticketTrace.print.print_count }} 次 · {{ dateTime(ticketTrace.print.print_at) }}</strong></div></div><h4>关联批次</h4><div class="warehouse-chip-list"><span v-for="lot in ticketTrace.lots" :key="lot.id" class="warehouse-lot-chip">{{ lot.lot_no }} · {{ lot.fabric_name }} · {{ lot.color }}（{{ lot.qty }}）</span><span v-if="!ticketTrace.lots.length">未关联批次</span></div><h4>登记时间线</h4><div class="table-wrap"><table><thead><tr><th>时间</th><th>工序</th><th>员工 / 代登记人</th><th class="numeric">数量</th><th class="numeric">工价</th></tr></thead><tbody><tr v-for="record in ticketTrace.records" :key="record.id"><td>{{ fullDateTime(record.registered_at) }}</td><td>{{ record.process }}</td><td>{{ record.user }}<small class="cell-subtitle">{{ record.operator || '' }}</small></td><td class="numeric">{{ record.qty }}</td><td class="numeric">{{ money(record.price) }}</td></tr></tbody></table></div></section>
    </template>
    <section v-if="productionTrace" class="card warehouse-trace-detail"><div class="card-header"><div><h3>{{ productionTrace.production.bed_no }} · 床次追溯</h3><p>{{ productionTrace.production.style_no }} · {{ productionTrace.production.customer_name || '无客户' }} · {{ productionTrace.production.source_order_no || '无来源单' }}</p></div><button class="icon-button" aria-label="关闭床次追溯" @click="productionTrace = null"><AppIcon name="close" /></button></div><div class="detail-grid"><div class="detail-item"><small>床次件数</small><strong>{{ number(productionTrace.production.total_qty) }}</strong></div><div class="detail-item"><small>菲票</small><strong>{{ productionTrace.ticket_count }} 扎 / {{ number(productionTrace.qty_total) }} 件</strong></div><div class="detail-item"><small>批次</small><strong>{{ productionTrace.lots.length }} 个</strong></div></div><div class="warehouse-chip-list"><span v-for="lot in productionTrace.lots" :key="lot.id" class="warehouse-lot-chip">{{ lot.lot_no }} · {{ lot.color }} · 用量 {{ lot.qty }}</span></div><div class="table-wrap"><table><thead><tr><th>领料单</th><th>批次</th><th class="numeric">数量</th><th>时间</th><th>状态</th></tr></thead><tbody><tr v-for="(issue, index) in productionTrace.issues" :key="index"><td>{{ issue.issue_no }}</td><td>{{ issue.lot_no }}</td><td class="numeric">{{ issue.qty }}</td><td>{{ dateTime(issue.issue_at) }}</td><td>{{ statusText[issue.status] || issue.status }}</td></tr></tbody></table></div></section>
  </template>
  </div>

  <Teleport to="body"><div v-if="modal === 'lot'" class="modal-backdrop" @click.self="modal = ''"><section class="modal" role="dialog" aria-modal="true" aria-label="批次编辑"><div class="modal-header"><div><h2>{{ editingId ? '编辑批次' : '新增批次' }}</h2><p>批次登记量不是实时可用库存；实时口径请查看需求汇总。</p></div><button class="icon-button" aria-label="关闭" @click="modal = ''"><AppIcon name="close" /></button></div><form @submit.prevent="saveLot"><div class="form-grid"><label class="field"><span>批次号 / 缸号 *</span><input v-model="lotForm.lot_no" required maxlength="64" /></label><label class="field"><span>面料名称</span><input v-model="lotForm.fabric_name" maxlength="64" /></label><label class="field"><span>颜色</span><input v-model="lotForm.color" maxlength="64" list="warehouse-color-options" /></label><label class="field"><span>批次日期</span><input v-model="lotForm.batch_date" type="date" /></label><label class="field"><span>登记数量</span><input v-model="lotForm.qty" type="number" min="0" step="0.01" required /></label><label class="field"><span>单位</span><input v-model="lotForm.unit" maxlength="16" required /></label><label class="field"><span>批次单价</span><input v-model="lotForm.unit_cost" type="number" min="0" step="0.001" placeholder="留空待补价" /></label><label class="field full"><span>备注</span><textarea v-model="lotForm.remark" maxlength="255" rows="3" /></label></div><div v-if="formError" class="form-error">{{ formError }}</div><div class="form-actions"><button type="button" class="button secondary" @click="modal = ''">取消</button><button class="button primary" :disabled="saving">{{ saving ? '保存中…' : '保存批次' }}</button></div></form></section></div></Teleport>

  <Teleport to="body"><div v-if="modal === 'supplier'" class="modal-backdrop" @click.self="modal = ''"><section class="modal" role="dialog" aria-modal="true" aria-label="供应商编辑"><div class="modal-header"><div><h2>{{ editingId ? '编辑供应商' : '新增供应商' }}</h2><p>编码在本公司内唯一。</p></div><button class="icon-button" aria-label="关闭" @click="modal = ''"><AppIcon name="close" /></button></div><form @submit.prevent="saveSupplier"><div class="form-grid"><label class="field"><span>供应商编码 *</span><input v-model="supplierForm.code" required maxlength="64" /></label><label class="field"><span>供应商名称 *</span><input v-model="supplierForm.name" required maxlength="128" /></label><label class="field"><span>联系人</span><input v-model="supplierForm.contact_person" maxlength="64" /></label><label class="field"><span>电话</span><input v-model="supplierForm.phone" maxlength="32" /></label><label class="field"><span>结算方式</span><input v-model="supplierForm.settle_method" maxlength="32" placeholder="现结 / 月结 / 预付" /></label><label class="field"><span>评级</span><input v-model="supplierForm.rating" maxlength="16" /></label><label class="field full"><span>备注</span><textarea v-model="supplierForm.remark" maxlength="255" rows="3" /></label></div><div v-if="formError" class="form-error">{{ formError }}</div><div class="form-actions"><button type="button" class="button secondary" @click="modal = ''">取消</button><button class="button primary" :disabled="saving">{{ saving ? '保存中…' : '保存供应商' }}</button></div></form></section></div></Teleport>

  <Teleport to="body"><div v-if="modal === 'purchase'" class="modal-backdrop" @click.self="modal = ''"><section class="modal warehouse-doc-modal" role="dialog" aria-modal="true" aria-label="采购单"><div class="modal-header"><div><h2>{{ editingId ? purchaseForm.order_no : '新增采购单' }}</h2><p>{{ editingId ? '后端仅支持草稿单头编辑，已有明细不会随保存替换。' : '单号由后端自动生成，物料 ID 均从本公司物料主档选择。' }}</p></div><button class="icon-button" aria-label="关闭" @click="modal = ''"><AppIcon name="close" /></button></div><form @submit.prevent="savePurchase"><div class="form-grid"><label class="field"><span>供应商名称（合同为文本字段）</span><input v-model="purchaseForm.supplier_name" :disabled="purchaseReadOnly" maxlength="128" /></label><label class="field"><span>采购日期</span><input v-model="purchaseForm.ordered_at" :disabled="purchaseReadOnly" type="date" /></label><label class="field full"><span>备注</span><input v-model="purchaseForm.remark" :disabled="purchaseReadOnly" maxlength="255" /></label></div><div class="warehouse-editor-heading"><h3>采购明细</h3><button v-if="!editingId" type="button" class="button secondary small" @click="purchaseForm.items.push(newPurchaseItem())">添加明细</button></div><div v-if="!editingId" class="warehouse-edit-lines"><div v-for="(line, index) in purchaseForm.items" :key="index" class="warehouse-edit-line"><div class="warehouse-line-title"><strong>第 {{ index + 1 }} 行</strong><button type="button" class="button ghost small warehouse-danger-text" @click="purchaseForm.items.length > 1 ? purchaseForm.items.splice(index, 1) : null">移除</button></div><div class="warehouse-line-grid"><label class="field warehouse-wide-field"><span>物料主档</span><select v-model.number="line.material_id" @change="purchaseMaterialChanged(line)"><option value="">不关联物料（不进入需求统计）</option><option v-for="material in lookups.materials" :key="material.id" :value="material.id">{{ material.code }} · {{ material.name }}（{{ material.unit }}）</option></select></label><label class="field"><span>面料 / 物料名称 *</span><input v-model="line.fabric_name" required maxlength="64" /></label><label class="field"><span>颜色</span><input v-model="line.color" maxlength="64" list="warehouse-color-options" /></label><label class="field"><span>数量 *</span><input v-model="line.qty" type="number" min="0.01" step="0.01" required /></label><label class="field"><span>单位</span><input v-model="line.unit" maxlength="16" required /></label><label class="field"><span>单价</span><input v-model="line.price" type="number" min="0" step="0.001" /></label><label class="field"><span>单价单位</span><select v-model="line.price_unit"><option value="">按主单位</option><option value="kg">元/公斤</option><option value="m">元/米</option></select></label><label class="field"><span>需求来源</span><select v-model="line.demand_source"><option value="manual">手工</option><option value="summary_import">需求汇总</option></select></label></div></div></div><div v-else class="table-wrap"><table><thead><tr><th>物料 / 颜色</th><th class="numeric">数量</th><th class="numeric">已到 / 合格</th><th class="numeric">主单位单价</th><th>来源</th></tr></thead><tbody><tr v-for="line in purchaseForm.items" :key="line.id"><td>{{ line.material_name || line.fabric_name }}<small class="cell-subtitle">{{ line.color || '—' }}</small></td><td class="numeric">{{ line.qty }} {{ line.unit }}</td><td class="numeric">{{ line.arrived_qty }} / {{ line.qualified_qty }}</td><td class="numeric">{{ money(line.price) }}</td><td>{{ line.demand_source === 'summary_import' ? '需求汇总' : '手工' }}</td></tr></tbody></table></div><div v-if="formError" class="form-error">{{ formError }}</div><div class="form-actions"><template v-if="editingId && purchaseForm.status === 'draft'"><button type="button" class="button danger" :disabled="actionId" @click="documentAction('purchase', 'delete', purchaseForm)">删除草稿</button><button type="button" class="button secondary" :disabled="actionId" @click="documentAction('purchase', 'confirm', purchaseForm)">确认完成</button></template><button type="button" class="button secondary" @click="modal = ''">关闭</button><button v-if="!purchaseReadOnly" class="button primary" :disabled="saving || lookupLoading">{{ saving ? '保存中…' : editingId ? '保存单头' : '创建采购单' }}</button></div></form></section></div></Teleport>

  <Teleport to="body"><div v-if="modal === 'stock'" class="modal-backdrop" @click.self="modal = ''"><section class="modal warehouse-doc-modal" role="dialog" aria-modal="true" aria-label="入库单"><div class="modal-header"><div><h2>{{ editingId ? stockForm.in_no : '新增入库单' }}</h2><p>{{ editingId ? '仅可修改草稿单头；已有入库明细只读。' : '保存即创建/复用行级批次，确认后完成单据与批次价链。' }}</p></div><button class="icon-button" aria-label="关闭" @click="modal = ''"><AppIcon name="close" /></button></div><form @submit.prevent="saveStock"><div class="form-grid"><label class="field"><span>来源类型</span><select v-model="stockForm.source_type" :disabled="!!editingId"><option value="purchase">采购入库</option><option value="return">退货入库</option><option value="other">其他</option><option value="opening">期初入库</option></select></label><label class="field"><span>关联采购单</span><select v-model.number="stockForm.purchase" :disabled="!!editingId" @change="stockPurchaseChanged"><option value="">不关联采购单</option><option v-for="purchase in lookups.purchases" :key="purchase.id" :value="purchase.id">{{ purchase.order_no }} · {{ purchase.supplier_name || '无供应商' }}</option></select></label><label class="field"><span>入库时间</span><input v-model="stockForm.in_at" :disabled="stockReadOnly" type="datetime-local" /></label><label class="field"><span>备注</span><input v-model="stockForm.remark" :disabled="stockReadOnly" maxlength="255" /></label></div><div class="warehouse-editor-heading"><h3>入库明细</h3><div v-if="!editingId" class="warehouse-row-actions"><button type="button" class="button secondary small" @click="fillStockFromPurchase">带出采购明细</button><button type="button" class="button secondary small" @click="stockForm.items.push(newStockItem())">添加明细</button></div></div><div v-if="!editingId" class="warehouse-edit-lines"><div v-for="(line, index) in stockForm.items" :key="index" class="warehouse-edit-line"><div class="warehouse-line-title"><strong>第 {{ index + 1 }} 行</strong><button type="button" class="button ghost small warehouse-danger-text" @click="stockForm.items.length > 1 ? stockForm.items.splice(index, 1) : null">移除</button></div><div class="warehouse-line-grid"><label class="field warehouse-wide-field"><span>采购单行</span><select v-model.number="line.purchase_item" :disabled="!stockForm.purchase" @change="stockPurchaseItemChanged(line)"><option value="">不关联采购行</option><option v-for="pi in stockPurchaseItems" :key="pi.id" :value="pi.id">{{ pi.material_name || pi.fabric_name }} · {{ pi.color || '无颜色' }} · 采购 {{ pi.qty }} {{ pi.unit }}</option></select></label><label class="field warehouse-wide-field"><span>物料主档</span><select v-model.number="line.material_id" @change="stockMaterialChanged(line)"><option value="">不关联物料</option><option v-for="material in lookups.materials" :key="material.id" :value="material.id">{{ material.code }} · {{ material.name }}（{{ material.unit }}）</option></select></label><label class="field"><span>面料 / 物料名称 *</span><input v-model="line.fabric_name" required maxlength="64" /></label><label class="field"><span>颜色</span><input v-model="line.color" maxlength="64" list="warehouse-color-options" /></label><label class="field"><span>批次号 *</span><input v-model="line.lot_no" required maxlength="64" /></label><label class="field"><span>数量 *</span><input v-model="line.qty" type="number" min="0.01" step="0.01" required /></label><label class="field"><span>单位</span><input v-model="line.unit" required maxlength="16" /></label><label class="field"><span>手工单价（空则随采购行）</span><input v-model="line.unit_price" type="number" min="0" step="0.001" /></label><label class="field"><span>手工单价单位</span><select v-model="line.price_unit"><option value="">按主单位 / 随采购行</option><option value="kg">元/公斤</option><option value="m">元/米</option></select></label></div></div></div><div v-else class="table-wrap"><table><thead><tr><th>批次 / 物料</th><th>颜色</th><th class="numeric">数量</th><th class="numeric">主单位单价</th><th>来源采购单</th></tr></thead><tbody><tr v-for="line in stockForm.items" :key="line.id"><td><span class="cell-title">{{ line.lot_no }}</span><small class="cell-subtitle">{{ line.material_name || line.fabric_name }}</small></td><td>{{ line.color || '—' }}</td><td class="numeric">{{ line.qty }} {{ line.unit }}</td><td class="numeric">{{ line.unit_price === null ? '—' : money(line.unit_price) }}</td><td>{{ line.purchase_item_no || '—' }}</td></tr></tbody></table></div><div v-if="formError" class="form-error">{{ formError }}</div><div class="form-actions"><template v-if="editingId && stockForm.status === 'draft'"><button type="button" class="button danger" :disabled="actionId" @click="documentAction('stock', 'delete', stockForm)">删除草稿</button><button type="button" class="button secondary" :disabled="actionId" @click="documentAction('stock', 'confirm', stockForm)">确认入库</button></template><button type="button" class="button secondary" @click="modal = ''">关闭</button><button v-if="!stockReadOnly" class="button primary" :disabled="saving || lookupLoading">{{ saving ? '保存中…' : editingId ? '保存单头' : '创建入库单' }}</button></div></form></section></div></Teleport>

  <Teleport to="body"><div v-if="modal === 'issue'" class="modal-backdrop" @click.self="modal = ''"><section class="modal warehouse-doc-modal" role="dialog" aria-modal="true" aria-label="领退料单"><div class="modal-header"><div><h2>{{ editingId ? issueForm.issue_no : '新增领 / 退料单' }}</h2><p>{{ editingId ? '仅可修改草稿单头；领料明细只读。' : '选定床次后可选择该款式生效 BOM 定额行；超额登记不拦截，但会留痕。' }}</p></div><button class="icon-button" aria-label="关闭" @click="modal = ''"><AppIcon name="close" /></button></div><form @submit.prevent="saveIssue"><section v-if="mobileIssueFlow && !editingId" class="warehouse-issue-context"><strong>{{ lookups.productions.find((p) => Number(p.id) === Number(issueForm.items[0]?.production_id))?.bed_no || '请先选择床次' }}</strong><p v-if="issueForm.items[0]?.production_id">款式 {{ lookups.productions.find((p) => Number(p.id) === Number(issueForm.items[0]?.production_id))?.style_no || '无' }} · 定额基数 {{ lookups.productions.find((p) => Number(p.id) === Number(issueForm.items[0]?.production_id))?.plan_qty ?? lookups.productions.find((p) => Number(p.id) === Number(issueForm.items[0]?.production_id))?.total_qty ?? 0 }} 件</p><p v-if="issueBedLots.length">本床已用缸号：{{ issueBedLots.map((item) => item.lot_no).join('、') }}</p><p v-else-if="issueForm.items[0]?.production_id">本床尚未用缸</p><p v-if="issueForm.type === 'return'" class="warehouse-warning">退料数量填正数，确认后冲减该床次已领量。</p><p v-else-if="issueForm.items[0]?.production_id && !(issueBomItems[issueForm.items[0].production_id] || []).length">该床次款式无生效 BOM，请手选物料领料。</p></section><div class="form-grid"><label class="field"><span>单据类型</span><select v-model="issueForm.type" :disabled="!!editingId"><option value="issue">领料</option><option value="return">退料（数量填正数）</option></select></label><label class="field"><span>领退料时间</span><input v-model="issueForm.issue_at" :disabled="issueReadOnly" type="datetime-local" /></label><label class="field full"><span>备注</span><input v-model="issueForm.remark" :disabled="issueReadOnly" maxlength="255" /></label></div><div class="warehouse-editor-heading"><h3>领退料明细</h3><button v-if="!editingId" type="button" class="button secondary small" @click="issueForm.items.push(newIssueItem())">添加明细</button></div><div v-if="!editingId" class="warehouse-edit-lines"><div v-for="(line, index) in issueForm.items" :key="index" class="warehouse-edit-line" :class="{ 'warehouse-issue-over': mobileIssueFlow && issueLineOver(line) }"><div class="warehouse-line-title"><strong>第 {{ index + 1 }} 行</strong><span v-if="mobileIssueFlow && issueLineOver(line)" class="badge producing">超领</span><span v-if="mobileIssueFlow && issueLotMismatch(line)" class="badge producing">新缸号·注意缸差</span><button type="button" class="button ghost small warehouse-danger-text" @click="issueForm.items.length > 1 ? issueForm.items.splice(index, 1) : null">移除</button></div><div class="warehouse-line-grid"><label class="field warehouse-wide-field"><span>床次 *</span><select v-model.number="line.production_id" required @change="issueProductionChanged(line)"><option value="">请选择本公司床次</option><option v-for="production in lookups.productions" :key="production.id" :value="production.id">{{ production.bed_no }} · {{ production.style_no }} · {{ production.style_name }}</option></select></label><label class="field warehouse-wide-field"><span>库存批次 *</span><select v-model.number="line.lot_id" required><option value="">请选择本公司批次</option><option v-for="lot in lookups.lots" :key="lot.id" :value="lot.id">{{ lot.lot_no }} · {{ lot.fabric_name }} · {{ lot.color || '无颜色' }}（{{ lot.qty }} {{ lot.unit }}）</option></select></label><label v-if="mobileIssueFlow" class="field"><span>定额（空 = 无定额）</span><input v-model="line.qty_req" type="number" min="0" step="0.01" :disabled="!!line.bom_item_id" /></label><label class="field"><span>数量（正数）*</span><input v-model="line.qty" type="number" min="0.01" step="0.01" required /></label><label class="field warehouse-wide-field"><span>BOM 定额行（可选）</span><select v-model.number="line.bom_item_id" :disabled="!line.production_id || issueBomLoading[line.production_id]" @change="issueBomChanged(line)"><option value="">{{ issueBomLoading[line.production_id] ? '正在读取定额…' : '不使用 BOM 定额' }}</option><option v-for="bi in issueBomItems[line.production_id] || []" :key="bi.id" :value="bi.id">{{ bi.material_code }} · {{ bi.material_name }} · {{ bi.color || '通色' }}/{{ bi.size || '通码' }} · 单耗 {{ bi.unit_usage }}</option></select></label><label class="field warehouse-wide-field"><span>物料（无定额时可手选）</span><select v-model.number="line.material_id" :disabled="!!line.bom_item_id"><option value="">不关联物料</option><option v-for="material in lookups.materials" :key="material.id" :value="material.id">{{ material.code }} · {{ material.name }}（{{ material.unit }}）</option></select></label><label class="field warehouse-full-field"><span>超额原因（可选）</span><input v-model="line.over_reason" maxlength="255" /></label></div></div></div><div v-else class="table-wrap"><table><thead><tr><th>床次 / 批次</th><th>物料</th><th class="numeric">数量</th><th class="numeric">定额</th><th>差异状态</th><th>原因</th></tr></thead><tbody><tr v-for="line in issueForm.items" :key="line.id"><td>{{ line.bed_no }}<small class="cell-subtitle">{{ line.lot_no }} · {{ line.lot_unit }}</small></td><td>{{ line.material_name || '未关联' }}</td><td class="numeric">{{ line.qty }}</td><td class="numeric">{{ line.qty_req ?? '无定额' }}</td><td><span class="badge" :class="line.over_state === 'over' ? 'overdue' : line.over_state === 'pending' ? 'producing' : ''">{{ overStateText[line.over_state] || line.over_state }}</span></td><td>{{ line.over_reason || '—' }}</td></tr></tbody></table></div><p v-if="mobileIssueFlow && !editingId" class="section-note">选择床次后自动展开生效 BOM 定额行；超领只提示、不拦截。保存草稿后在列表确认写入血缘。</p><div v-if="formError" class="form-error">{{ formError }}</div><div class="form-actions"><template v-if="editingId && issueForm.status === 'draft'"><button type="button" class="button danger" :disabled="actionId" @click="documentAction('issue', 'delete', issueForm)">删除草稿</button><button type="button" class="button secondary" :disabled="actionId" @click="documentAction('issue', 'confirm', issueForm)">确认{{ issueTypeText[issueForm.type] }}</button></template><button type="button" class="button secondary" @click="modal = ''">关闭</button><button v-if="!issueReadOnly" class="button primary" :disabled="saving || lookupLoading">{{ saving ? '保存中…' : editingId ? '保存单头' : '创建单据' }}</button></div></form></section></div></Teleport>

  <Teleport to="body"><div v-if="modal === 'receipt'" class="modal-backdrop" @click.self="modal = ''"><section class="modal warehouse-doc-modal" role="dialog" aria-modal="true" aria-label="暂收与IQC"><div class="modal-header"><div><h2>{{ editingId ? receiptForm.receipt_no : '新增到料暂收单' }}</h2><p>草稿可整表修改 IQC 拆量；确认只把合格数量生成入库，拒收数量仅留痕。</p></div><button class="icon-button" aria-label="关闭" @click="modal = ''"><AppIcon name="close" /></button></div><form @submit.prevent="saveReceipt"><div class="form-grid"><label class="field"><span>供应商主档</span><select v-model.number="receiptForm.supplier" :disabled="receiptReadOnly" @change="receiptSupplierChanged"><option value="">不关联供应商主档</option><option v-for="supplier in lookups.suppliers" :key="supplier.id" :value="supplier.id">{{ supplier.code }} · {{ supplier.name }}</option></select></label><label class="field"><span>供应商名称（直收可手填）</span><input v-model="receiptForm.supplier_name" :disabled="receiptReadOnly" maxlength="128" /></label><label class="field"><span>关联采购单</span><select v-model.number="receiptForm.purchase" :disabled="receiptReadOnly" @change="receiptPurchaseChanged"><option value="">无单直收</option><option v-for="purchase in lookups.purchases" :key="purchase.id" :value="purchase.id">{{ purchase.order_no }} · {{ purchase.supplier_name || '无供应商' }}</option></select></label><label class="field"><span>备注</span><input v-model="receiptForm.remark" :disabled="receiptReadOnly" maxlength="255" /></label></div><div class="warehouse-editor-heading"><h3>暂收与 IQC 明细</h3><div v-if="!receiptReadOnly" class="warehouse-row-actions"><button type="button" class="button secondary small" @click="fillReceiptFromPurchase">带出采购明细</button><button type="button" class="button secondary small" @click="receiptForm.items.push(newReceiptItem())">添加明细</button></div></div><div v-if="!receiptReadOnly" class="warehouse-edit-lines"><div v-for="(line, index) in receiptForm.items" :key="index" class="warehouse-edit-line"><div class="warehouse-line-title"><strong>第 {{ index + 1 }} 行</strong><button type="button" class="button ghost small warehouse-danger-text" @click="receiptForm.items.length > 1 ? receiptForm.items.splice(index, 1) : null">移除</button></div><div class="warehouse-line-grid"><label class="field warehouse-wide-field"><span>采购单行</span><select v-model.number="line.purchase_item" :disabled="!receiptForm.purchase" @change="receiptPurchaseItemChanged(line)"><option value="">无采购行直收</option><option v-for="pi in receiptPurchaseItems" :key="pi.id" :value="pi.id">{{ pi.material_name || pi.fabric_name }} · {{ pi.color || '无颜色' }} · 已到 {{ pi.arrived_qty }}/{{ pi.qty }}</option></select></label><label class="field warehouse-wide-field"><span>物料主档</span><select v-model.number="line.material_id" @change="receiptMaterialChanged(line)"><option value="">不关联物料</option><option v-for="material in lookups.materials" :key="material.id" :value="material.id">{{ material.code }} · {{ material.name }}（{{ material.unit }}）</option></select></label><label class="field"><span>面料 / 物料名称 *</span><input v-model="line.fabric_name" required maxlength="64" /></label><label class="field"><span>颜色</span><input v-model="line.color" maxlength="64" list="warehouse-color-options" /></label><label class="field"><span>批次号 / 缸号 *</span><input v-model="line.lot_no" required maxlength="64" /></label><label class="field"><span>单位</span><input v-model="line.unit" required maxlength="16" /></label><label class="field"><span>到料数量 *</span><input v-model="line.arrived_qty" type="number" min="0.01" step="0.01" required /></label><label class="field"><span>IQC 合格数量</span><input v-model="line.qualified_qty" type="number" min="0" step="0.01" required /></label><label class="field"><span>IQC 拒收数量</span><input v-model="line.rejected_qty" type="number" min="0" step="0.01" required /></label><label class="field warehouse-full-field"><span>行备注</span><input v-model="line.remark" maxlength="255" /></label></div></div></div><div v-else class="table-wrap"><table><thead><tr><th>批次 / 物料</th><th class="numeric">到料</th><th class="numeric">合格</th><th class="numeric">拒收</th><th>采购单 / 备注</th></tr></thead><tbody><tr v-for="line in receiptForm.items" :key="line.id"><td><span class="cell-title">{{ line.lot_no }}</span><small class="cell-subtitle">{{ line.material_name || line.fabric_name }} · {{ line.color }}</small></td><td class="numeric">{{ line.arrived_qty }} {{ line.unit }}</td><td class="numeric">{{ line.qualified_qty }}</td><td class="numeric">{{ line.rejected_qty }}</td><td>{{ line.purchase_item_no || '无单直收' }}<small class="cell-subtitle">{{ line.remark || '' }}</small></td></tr></tbody></table></div><p v-if="!receiptReadOnly" class="section-note">后端允许合格 + 拒收与到料量不一致，用于让步接收等场景；请确认拆量后再提交。</p><div v-if="formError" class="form-error">{{ formError }}</div><div class="form-actions"><template v-if="editingId && receiptForm.status === 'draft'"><button type="button" class="button danger" :disabled="actionId" @click="documentAction('receipt', 'delete', receiptForm)">删除草稿</button><button type="button" class="button secondary" :disabled="actionId" @click="documentAction('receipt', 'confirm', receiptForm)">确认 IQC 并入库</button></template><button type="button" class="button secondary" @click="modal = ''">关闭</button><button v-if="!receiptReadOnly" class="button primary" :disabled="saving || lookupLoading">{{ saving ? '保存中…' : editingId ? '保存暂收 / IQC' : '创建暂收单' }}</button></div></form></section></div></Teleport>

  <Teleport to="body"><div v-if="modal === 'divergence' && divergenceDetail" class="modal-backdrop" @click.self="modal = ''"><section class="modal warehouse-doc-modal" role="dialog" aria-modal="true" aria-label="差异流水"><div class="modal-header"><div><h2>{{ divergenceDetail.bed_no }} · {{ divergenceDetail.material_code || divergenceDetail.lot_no }}</h2><p>{{ divergenceDetail.material_name }} · {{ divergenceDetail.order_no || '未挂单' }}</p></div><button class="icon-button" aria-label="关闭" @click="modal = ''"><AppIcon name="close" /></button></div><div class="detail-grid"><div class="detail-item"><small>净领用</small><strong>{{ divergenceDetail.qty_net }} {{ divergenceDetail.material_unit }}</strong></div><div class="detail-item"><small>发料 / 当前定额</small><strong>{{ divergenceDetail.qty_req_snapshot }} / {{ divergenceDetail.qty_req_current ?? '—' }}</strong></div><div class="detail-item"><small>超领 / 无定额</small><strong>{{ divergenceDetail.over_qty }} / {{ divergenceDetail.no_req_qty }}</strong></div></div><div class="table-wrap"><table><thead><tr><th>领料单</th><th>类型 / 批次</th><th class="numeric">数量</th><th class="numeric">行定额</th><th>状态</th><th>原因</th></tr></thead><tbody><tr v-for="line in divergenceDetail.over_rows" :key="`${line.issue_id}-${line.lot_no}`"><td>{{ line.issue_no }}</td><td>{{ issueTypeText[line.issue_type] }}<small class="cell-subtitle">{{ line.lot_no }}</small></td><td class="numeric">{{ line.qty }}</td><td class="numeric">{{ line.qty_req ?? '—' }}</td><td>{{ overStateText[line.state] || line.state }}</td><td>{{ line.over_reason || '—' }}</td></tr></tbody></table></div><p v-if="!divergenceDetail.over_rows.length" class="page-state">该行没有超额或待补换算流水</p></section></div></Teleport>
  <datalist id="warehouse-color-options"><option v-for="color in COMMON_COLORS" :key="color" :value="color" /></datalist>
  <ImportDialog v-if="modal === 'import'" title="导入期初库存" endpoint="/opening-stock/import" description="按后端期初库存模板选择 .xlsx；校验失败会显示行号，整批不会部分写入。" @close="modal = ''" @imported="invalidateLookup('lots'); loadList('lots'); showToast('期初库存导入完成')" />
</template>

<style scoped>
.warehouse-mobile{display:none}.warehouse-mobile-toolbar{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:12px}.warehouse-mobile-toolbar>span{flex:1}.warehouse-mobile-filter{display:flex;align-items:center;gap:10px;margin-bottom:12px}.warehouse-mobile-filter .search-box{min-width:0;max-width:none;flex:1}.warehouse-mobile-filter input,.warehouse-mobile-filter select{min-width:0}.warehouse-search-button,.warehouse-text-button{border:0;background:transparent;color:var(--orange);padding:8px 0;font-size:13px;white-space:nowrap}.warehouse-mobile-chips{display:flex;align-items:center;gap:8px;overflow-x:auto;margin-bottom:12px;padding:2px}.warehouse-mobile-chips button{flex:none;border:1px solid var(--line);border-radius:999px;background:#fff;color:var(--text-secondary);padding:7px 16px;font-size:12px}.warehouse-mobile-chips button.active{border-color:var(--orange);background:var(--orange);color:#fff}.warehouse-mobile-chips .warehouse-mobile-create{margin-left:auto;border-color:var(--orange);color:var(--orange)}.warehouse-mobile-segment{display:inline-flex;padding:3px;border-radius:999px;background:#fff;box-shadow:0 2px 8px #1f23290f}.warehouse-mobile-segment button{border:0;border-radius:999px;background:transparent;color:var(--text-secondary);padding:7px 20px;font-size:13px}.warehouse-mobile-segment button.active{background:var(--orange);color:#fff}.warehouse-mobile-list{display:flex;flex-direction:column;gap:12px}.warehouse-mobile-card{display:block;width:100%;text-align:left;color:inherit}.warehouse-mobile-card h3{margin:0;font-size:15px;overflow-wrap:anywhere}.warehouse-mobile-card p{margin:0}.warehouse-mobile-head{display:flex;align-items:flex-start;justify-content:space-between;gap:10px;margin-bottom:10px}.warehouse-mobile-head>div{min-width:0}.warehouse-mobile-head p{margin-top:5px;color:var(--muted);font-size:12px}.warehouse-mobile-head>strong{white-space:nowrap}.warehouse-mobile-meta{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:9px;color:var(--muted);font-size:12px}.warehouse-mobile-meta strong{display:block;margin-top:3px;color:var(--ink);font-weight:500;overflow-wrap:anywhere}.warehouse-mobile-line{margin-top:8px!important;color:var(--text-secondary);font-size:12px;line-height:1.55;overflow-wrap:anywhere}.warehouse-mobile-clickable{cursor:pointer}.warehouse-mobile-more{padding:18px 0 8px;text-align:center;color:var(--muted);font-size:12px}.warehouse-mobile-more button{border:0;background:transparent;color:var(--orange);padding:8px 20px}.warehouse-mobile-summary{display:grid;grid-template-columns:repeat(3,1fr);margin-bottom:12px;padding:14px 4px;border-radius:12px;background:#fff;text-align:center;color:var(--muted);font-size:11px;box-shadow:0 2px 8px #1f23290f}.warehouse-mobile-summary strong{display:block;margin-top:5px;color:var(--ink);font-size:20px}.warehouse-mobile-report-filter{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:12px}.warehouse-mobile-report-filter input,.warehouse-mobile-report-filter select{min-width:0}.warehouse-trace-modes{display:flex;margin-bottom:12px}.warehouse-trace-modes button{flex:1;padding-inline:8px}.warehouse-trace-filter{flex-wrap:wrap}.warehouse-trace-filter>input,.warehouse-trace-filter>select{flex:1 1 45%;width:auto}.warehouse-trace-filter>.button{flex:1 1 100%}.warehouse-mobile-detail{margin-top:12px}.warehouse-mobile-subrow{display:flex;align-items:flex-start;justify-content:space-between;gap:10px;width:100%;margin-top:10px;padding:12px 0 0;border:0;border-top:1px solid var(--line);background:transparent;color:var(--text-secondary);text-align:left;font-size:12px}.warehouse-mobile-subrow strong{color:var(--ink)}.warehouse-issue-context{margin-bottom:16px;padding:14px;border-radius:12px;background:#f5f6f8}.warehouse-issue-context strong{font-size:15px}.warehouse-issue-context p{margin:7px 0 0;color:var(--text-secondary);font-size:12px;line-height:1.5}.warehouse-edit-line.warehouse-issue-over{border-color:var(--orange)}.warehouse-line-title>span{margin-left:auto}.warehouse-line-title>span+span{margin-left:4px}.warehouse-line-title>span~button{margin-left:6px}
.warehouse-tabs{display:flex;gap:6px;margin-bottom:20px;padding:5px;background:#eae7e2;border-radius:11px;overflow-x:auto}.warehouse-tabs button{border:0;background:transparent;border-radius:8px;padding:9px 14px;color:#6e6660;font-size:13px;white-space:nowrap}.warehouse-tabs button.active{background:#fff;color:var(--orange-dark);font-weight:650;box-shadow:0 1px 4px #0000000d}.warehouse-action-error{margin-bottom:16px}.warehouse-row-actions,.warehouse-mobile-actions{display:flex;align-items:center;gap:6px;flex-wrap:wrap}.warehouse-mobile-actions{justify-content:flex-end;margin-top:14px}.warehouse-danger-text{color:var(--danger)}.warehouse-doc-link{border:0;background:none;padding:0;color:var(--orange-dark);font-weight:650;text-align:left}.warehouse-note{padding:0 18px 16px}.warehouse-negative{color:var(--good)}.warehouse-warning{color:var(--danger)}.warehouse-stat-small{font-size:26px}.warehouse-report-filters select{max-width:220px}.warehouse-pending{margin-top:18px}.warehouse-doc-modal{width:min(1180px,100%)}.warehouse-editor-heading{display:flex;align-items:center;justify-content:space-between;gap:12px;margin:22px 0 12px}.warehouse-editor-heading h3{margin:0;font-size:15px}.warehouse-edit-lines{display:flex;flex-direction:column;gap:12px}.warehouse-edit-line{border:1px solid var(--line);border-radius:11px;background:#fcfbfa;padding:14px}.warehouse-line-title{display:flex;align-items:center;justify-content:space-between;margin-bottom:12px;font-size:13px}.warehouse-line-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}.warehouse-wide-field{grid-column:span 2}.warehouse-full-field{grid-column:1/-1}.form-error{margin-top:14px}.warehouse-trace-detail{margin-top:18px}.warehouse-lot-chip{display:inline-flex;padding:5px 8px;margin:2px 4px 2px 0;border-radius:7px;background:#f3f0ec;font-size:11px;color:#5f5751}.warehouse-chip-list{margin:0 0 18px}.warehouse-mobile-lots{font-size:12px;color:var(--muted);margin:0}.warehouse-ticket-select{max-width:620px}.warehouse-demand-stats .stat-value{font-size:28px}h4{font-size:13px;margin:18px 0 10px}@media(max-width:1000px){.warehouse-line-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.warehouse-wide-field{grid-column:span 2}.warehouse-full-field{grid-column:1/-1}.warehouse-report-filters input,.warehouse-report-filters select{max-width:none;flex:1;min-width:150px}}@media(max-width:760px){.warehouse-desktop{display:none}.warehouse-mobile{display:block}.warehouse-mobile-head p,.warehouse-mobile-head .warehouse-doc-link,.warehouse-mobile-subrow>*,.warehouse-mobile .warehouse-lot-chip{min-width:0;max-width:100%;overflow-wrap:anywhere;white-space:normal}.warehouse-mobile-section-nav{margin-bottom:12px}.warehouse-mobile-section-nav button{flex:1}.warehouse-tabs{margin-inline:-12px;border-radius:0;padding-inline:12px}.warehouse-line-grid{grid-template-columns:1fr}.warehouse-wide-field,.warehouse-full-field{grid-column:auto}.warehouse-edit-line{padding:12px}.warehouse-editor-heading{align-items:flex-start}.warehouse-editor-heading .warehouse-row-actions{justify-content:flex-end}.warehouse-report-filters input,.warehouse-report-filters select{width:100%;min-width:100%}.warehouse-demand-stats{grid-template-columns:repeat(2,minmax(0,1fr))}.warehouse-stat-small{font-size:21px}.warehouse-ticket-select{max-width:none}.warehouse-doc-modal .form-actions .button{flex:1}.warehouse-trace-detail{margin-top:14px}}
</style>
