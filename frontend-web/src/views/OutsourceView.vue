<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { api, download, rows } from '../api'
import { money, number } from '../format'
import AppIcon from '../components/AppIcon.vue'
import PageState from '../components/PageState.vue'
import AdminModal from '../components/AdminModal.vue'
import AdminPagination from '../components/AdminPagination.vue'
import MobileLoadMore from '../components/MobileLoadMore.vue'

const emit = defineEmits(['toast'])
const data = ref({ count: 0 })
const loading = ref(true)
const loadingMore = ref(false)
const error = ref('')
const appendError = ref('')
const hasMore = computed(() => rows(data.value).length < Number(data.value.count || 0))
const filters = reactive({ status: '', supplier_id: '', order_id: '', page: 1 })
const suppliers = ref([])
const orders = ref([])
const styles = ref([])
const optionError = ref('')
const busy = ref('')
const modal = ref('')
const saving = ref(false)
const formError = ref('')
const today = () => {
  const date = new Date(); const offset = date.getTimezoneOffset() * 60000
  return new Date(date.getTime() - offset).toISOString().slice(0, 10)
}
const createForm = reactive({ supplier: '', factory_name: '', order: '', style: '', process_desc: '', send_qty: 1, unit_price: '0', send_date: today(), due_date: '', remark: '' })
const receiveForm = reactive({ id: 0, no: '', qty: 1, max: 1, remark: '' })

async function loadPage(append = false) {
  if (!append && window.matchMedia('(max-width: 760px)').matches) filters.page = 1
  const requestedPage = filters.page
  if (append) { loadingMore.value = true; appendError.value = '' }
  else { loading.value = true; error.value = ''; appendError.value = '' }
  try {
    const result = await api('/outsource/orders', { params: { ...filters, page_size: 20 } })
    data.value = append ? { ...result, results: [...rows(data.value), ...rows(result)] } : result
  } catch (e) {
    if (append) { appendError.value = e.message; filters.page = Math.max(1, requestedPage - 1) }
    else { error.value = e.message; data.value = { count: 0 } }
  } finally { loading.value = false; loadingMore.value = false }
}
function load() { return loadPage(false) }
function loadMore() {
  if (!hasMore.value || loadingMore.value || loading.value) return
  filters.page += 1
  return loadPage(true)
}
async function loadOptions() {
  optionError.value = ''
  const results = await Promise.allSettled([
    api('/suppliers', { params: { page: 1, page_size: 200 } }),
    api('/orders', { params: { page: 1, page_size: 200 } }),
    api('/styles', { params: { page: 1, page_size: 200 } }),
  ])
  const targets = [suppliers, orders, styles]
  const labels = ['供应商', '订单', '款式']
  const failed = []
  results.forEach((result, index) => {
    if (result.status === 'fulfilled') targets[index].value = rows(result.value)
    else failed.push(`${labels[index]}：${result.reason.message}`)
  })
  optionError.value = failed.join('；')
}
function resetPage() { filters.page = 1; load() }
async function exportData() { try { await download('/outsource/export', { status: filters.status, supplier_id: filters.supplier_id, order_id: filters.order_id }); emit('toast', '外发记录已导出') } catch (e) { error.value = e.message } }
function statusClass(status) { return status === 'settled' ? 'settled' : status === 'returned' ? 'done' : status === 'partial' ? 'producing' : '' }
function openCreate() {
  Object.assign(createForm, { supplier: '', factory_name: '', order: '', style: '', process_desc: '', send_qty: 1, unit_price: '0', send_date: today(), due_date: '', remark: '' })
  formError.value = ''; modal.value = 'create'
}
function closeModal() { if (!saving.value) modal.value = '' }
function onOrderChange() {
  const order = orders.value.find((item) => item.id === Number(createForm.order))
  if (order?.style && !createForm.style) createForm.style = order.style
}
async function createOrder() {
  formError.value = ''
  const qty = Number(createForm.send_qty)
  const price = createForm.unit_price === '' ? 0 : Number(createForm.unit_price)
  if (!createForm.supplier && !createForm.factory_name.trim()) { formError.value = '请选择本公司供应商，或手填外协厂名称'; return }
  if (!Number.isInteger(qty) || qty <= 0) { formError.value = '外发数量须为正整数'; return }
  if (!Number.isFinite(price) || price < 0) { formError.value = '加工单价须为不小于 0 的数字'; return }
  if (!createForm.send_date) { formError.value = '请选择发出日期'; return }
  saving.value = true
  try {
    await api('/outsource/orders', { method: 'POST', body: {
      supplier: createForm.supplier || null, factory_name: createForm.factory_name.trim(), order: createForm.order || null, style: createForm.style || null,
      process_desc: createForm.process_desc.trim(), send_qty: qty, unit_price: String(price), send_date: createForm.send_date,
      due_date: createForm.due_date || null, remark: createForm.remark.trim(),
    } })
    modal.value = ''; emit('toast', '外发单已创建'); filters.page = 1; await load()
  } catch (e) { formError.value = e.message } finally { saving.value = false }
}
function openReceive(order) {
  const remaining = Number(order.send_qty) - Number(order.received_qty)
  Object.assign(receiveForm, { id: order.id, no: order.no, qty: remaining, max: remaining, remark: '' })
  formError.value = ''; modal.value = 'receive'
}
async function submitReceive() {
  formError.value = ''
  const qty = Number(receiveForm.qty)
  if (!Number.isInteger(qty) || qty < 1 || qty > receiveForm.max) { formError.value = `收回数量须为 1~${receiveForm.max} 的整数`; return }
  saving.value = true
  try {
    await api(`/outsource/orders/${receiveForm.id}`, { method: 'PUT', body: { action: 'receive', qty, remark: receiveForm.remark.trim() } })
    modal.value = ''; emit('toast', '外发收回已登记'); await load()
  } catch (e) { formError.value = e.message } finally { saving.value = false }
}
async function settle(order) {
  if (!window.confirm(`确定结算外发单 ${order.no} 吗？加工费 ${money(order.amount)}，结算后为终态且不可删除。`)) return
  busy.value = `settle-${order.id}`; error.value = ''
  try { await api(`/outsource/orders/${order.id}`, { method: 'PUT', body: { action: 'settle' } }); emit('toast', '外发单已结算'); await load() } catch (e) { error.value = e.message } finally { busy.value = '' }
}
async function remove(order) {
  if (!window.confirm(`确定删除外发单 ${order.no} 吗？删除后不可恢复；已有收回记录的外发单不可删除。`)) return
  busy.value = `delete-${order.id}`; error.value = ''
  try {
    await api(`/outsource/orders/${order.id}`, { method: 'DELETE' }); emit('toast', '外发单已删除')
    if (rows(data.value).length === 1 && filters.page > 1) filters.page -= 1
    await load()
  } catch (e) { error.value = e.message } finally { busy.value = '' }
}
onMounted(() => { load(); loadOptions() })
</script>

<template>
  <div class="welcome"><div><h2>委外加工</h2><p>管理外发单、分次收回与加工费结算。</p></div><div class="admin-outsource-head"><button class="button secondary outsource-export" @click="exportData">导出 Excel</button><button class="button secondary outsource-refresh" @click="load"><AppIcon name="refresh" :size="16" />刷新</button><button class="button primary" @click="openCreate">新建外发单</button></div></div>
  <div class="filter-bar outsource-filter"><div class="segmented admin-outsource-tabs"><button v-for="tab in [['','全部'],['sent','已发出'],['partial','部分收回'],['returned','已收回'],['settled','已结算']]" :key="tab[0]" :class="{ active: filters.status === tab[0] }" @click="filters.status = tab[0]; resetPage()">{{ tab[1] }}</button></div><div class="outsource-filter-fields"><select v-model="filters.supplier_id" aria-label="按供应商筛选" @change="resetPage"><option value="">全部外协厂</option><option v-for="supplier in suppliers" :key="supplier.id" :value="supplier.id">{{ supplier.name }}</option></select><select v-model="filters.order_id" aria-label="按订单筛选" @change="resetPage"><option value="">全部订单</option><option v-for="order in orders" :key="order.id" :value="order.id">{{ order.order_no }}</option></select></div></div>
  <p v-if="optionError" class="section-note">部分外键选项加载失败：{{ optionError }}。可重试；建单时也可手填外协厂。 <button class="button ghost small" @click="loadOptions">重试选项</button></p>
  <section class="card table-card outsource-list-card"><PageState :loading="loading" :error="error" :empty="!rows(data).length" empty-text="暂无外发单，点「新建外发单」开始外发" @retry="load"><div class="table-wrap desktop-table"><table><thead><tr><th>外发单 / 外协厂</th><th>订单 / 款式</th><th>工序说明</th><th class="numeric">外发 / 收回</th><th class="numeric">单价 / 加工费</th><th>日期 / 状态</th><th>操作</th></tr></thead><tbody><tr v-for="order in rows(data)" :key="order.id"><td><span class="cell-title">{{ order.no }}</span><small class="cell-subtitle">{{ order.supplier_name || order.factory_name || '未填写外协厂' }}</small></td><td>{{ order.order_no || '未关联订单' }}<small class="cell-subtitle">{{ order.style_no || '未关联款式' }}</small></td><td>{{ order.process_desc || '—' }}</td><td class="numeric">{{ number(order.send_qty) }} / {{ number(order.received_qty) }}</td><td class="numeric">{{ money(order.unit_price) }}<small class="cell-subtitle">{{ money(order.amount) }}</small></td><td>{{ order.send_date }}<small class="cell-subtitle">交回：{{ order.due_date || '未设置' }}</small><span class="badge admin-outsource-status" :class="statusClass(order.status)">{{ order.status_display }}</span></td><td><div class="admin-outsource-actions"><button v-if="['sent','partial'].includes(order.status)" class="button secondary small" :disabled="!!busy" @click="openReceive(order)">收回</button><button v-if="order.status === 'returned'" class="button primary small" :disabled="!!busy" @click="settle(order)">结算</button><button v-if="order.status !== 'settled' && !Number(order.received_qty)" class="button danger small" :disabled="!!busy" @click="remove(order)">删除</button></div></td></tr></tbody></table></div><div class="mobile-cards outsource-mobile-list"><article v-for="order in rows(data)" :key="order.id" class="mobile-row outsource-order-card"><div class="mobile-row-head"><div class="outsource-card-title"><div class="mobile-row-title">{{ order.no }}</div><small class="cell-subtitle"><strong>{{ order.supplier_name || order.factory_name || '未填写外协厂' }}</strong><template v-if="order.style_no"> · {{ order.style_no }}</template><template v-if="order.order_no"> · 订单 {{ order.order_no }}</template></small></div><span class="badge" :class="statusClass(order.status)">{{ order.status_display }}</span></div><p v-if="order.process_desc" class="outsource-process">工序：{{ order.process_desc }}</p><p class="mobile-meta outsource-quantity">外发 <strong>{{ number(order.send_qty) }}</strong> 件<template v-if="Number(order.received_qty)"> · 已收回 <b>{{ number(order.received_qty) }}</b> 件</template><template v-if="order.due_date"> · 交回 {{ order.due_date }}</template></p><div class="outsource-amount"><span>加工费 <strong>{{ money(order.amount) }}</strong> 元</span><small>（{{ money(order.unit_price) }} 元/件）</small></div><p class="outsource-card-meta">发出 {{ order.send_date }}<template v-if="order.return_date"> · 收回 {{ order.return_date }}</template><template v-if="order.creator_name"> · {{ order.creator_name }}</template></p><p v-if="order.remark" class="admin-outsource-note">备注：{{ order.remark }}</p><div v-if="order.status !== 'settled'" class="admin-outsource-actions outsource-card-actions"><button v-if="['sent','partial'].includes(order.status)" class="button primary small" :disabled="!!busy" @click="openReceive(order)">收回</button><button v-if="order.status === 'returned'" class="button primary small" :disabled="!!busy" @click="settle(order)">结算</button><button v-if="!Number(order.received_qty)" class="button danger small" :disabled="!!busy" @click="remove(order)">删除</button></div></article></div></PageState><MobileLoadMore :loading="loadingMore" :error="appendError" :has-more="hasMore" :count="rows(data).length" @more="loadMore" /><AdminPagination class="desktop-pagination" :page="filters.page" :count="data.count || 0" :disabled="loading" @change="filters.page = $event; load()" /></section>
  <p class="section-note">后端契约不提供普通字段编辑，本页提供建单、收回、结算、删除和按当前筛选导出。</p>

  <AdminModal v-if="modal === 'create'" title="新建外发单" description="供应商、订单和款式均从本公司接口选择；无主档外协厂可手填名称。" :saving="saving" wide @close="closeModal"><form class="outsource-task-form" @submit.prevent="createOrder"><div class="form-grid"><label class="field"><span>本公司供应商</span><select v-model="createForm.supplier"><option value="">手填外协厂 / 不关联主档</option><option v-for="supplier in suppliers" :key="supplier.id" :value="supplier.id">{{ supplier.code }} · {{ supplier.name }}</option></select></label><label class="field"><span>外协厂名称</span><input v-model="createForm.factory_name" maxlength="128" :placeholder="createForm.supplier ? '留空则使用供应商名称' : '未选供应商时必填'" /></label><label class="field"><span>关联订单</span><select v-model="createForm.order" @change="onOrderChange"><option value="">不关联订单</option><option v-for="order in orders" :key="order.id" :value="order.id">{{ order.order_no }} · {{ order.style_no }} · {{ order.customer_name }}</option></select></label><label class="field"><span>关联款式</span><select v-model="createForm.style"><option value="">不关联款式</option><option v-for="style in styles" :key="style.id" :value="style.id">{{ style.style_no }} · {{ style.name }}</option></select></label><label class="field full"><span>外发工序说明</span><input v-model="createForm.process_desc" maxlength="200" placeholder="如：平车、锁边、整烫" /></label><label class="field"><span>外发数量 *</span><input v-model.number="createForm.send_qty" type="number" min="1" step="1" required /></label><label class="field"><span>加工单价 / 件 *</span><input v-model="createForm.unit_price" type="number" min="0" step="0.001" required /></label><label class="field"><span>发出日期 *</span><input v-model="createForm.send_date" type="date" required /></label><label class="field"><span>约定交回日期</span><input v-model="createForm.due_date" type="date" /></label><label class="field full"><span>备注</span><textarea v-model="createForm.remark" maxlength="200" rows="3" /></label></div><p v-if="formError" class="form-error">{{ formError }}</p><div class="form-actions"><button type="button" class="button secondary" :disabled="saving" @click="closeModal">取消</button><button class="button primary" :disabled="saving"><span v-if="saving" class="spinner light" />创建外发单</button></div></form></AdminModal>

  <AdminModal v-if="modal === 'receive'" :title="`外发收回 · ${receiveForm.no}`" description="可分批收回，累计达到外发数量后自动变为已收回。" :saving="saving" @close="closeModal"><form @submit.prevent="submitReceive"><label class="field"><span>本次收回数量（不超过 {{ receiveForm.max }}）*</span><input v-model.number="receiveForm.qty" type="number" min="1" :max="receiveForm.max" step="1" required /></label><label class="field admin-receive-remark"><span>收回备注</span><textarea v-model="receiveForm.remark" maxlength="200" rows="3" /></label><p v-if="formError" class="form-error">{{ formError }}</p><div class="form-actions"><button type="button" class="button secondary" :disabled="saving" @click="closeModal">取消</button><button class="button primary" :disabled="saving">确认收回</button></div></form></AdminModal>
</template>

<style scoped>
.admin-outsource-head{display:flex;gap:8px}.admin-outsource-tabs{max-width:100%;overflow:auto}.outsource-filter-fields{display:contents}.admin-outsource-status{margin-top:7px}.admin-outsource-actions{display:flex;gap:6px;flex-wrap:wrap}.admin-outsource-note{font-size:12px;color:var(--muted);margin:12px 0}.admin-receive-remark{margin-top:16px}.form-error{margin-top:15px}
@media(max-width:760px){
  .desktop-pagination{display:none}
  .admin-outsource-head{justify-content:flex-end}.admin-outsource-head .outsource-refresh{display:none}.admin-outsource-head .button{font-size:12px;padding:8px 13px}.outsource-filter{display:block}.admin-outsource-tabs{display:flex;width:100%;gap:7px;overflow-x:auto;margin-bottom:10px;padding:2px 1px;background:transparent;scrollbar-width:none}.admin-outsource-tabs button{flex:1 0 auto;padding:7px 13px;border:0;border-radius:999px;background:#eceff1;color:var(--text-secondary);font-size:12px}.admin-outsource-tabs button.active{background:var(--orange);color:#fff;box-shadow:none}.outsource-filter-fields{display:grid;grid-template-columns:1fr 1fr;gap:8px;padding:10px;background:#fff;border-radius:12px;box-shadow:0 2px 8px rgba(31,35,41,.06)}.outsource-filter-fields select{width:100%;min-width:0;min-height:38px;border:0;border-radius:999px;background:#f5f6f8;padding:8px 10px;font-size:12px}
  .outsource-list-card.card{padding:0;background:transparent;box-shadow:none;overflow:visible}.outsource-list-card :deep(.page-state){border-radius:12px;background:#fff;box-shadow:0 2px 8px rgba(31,35,41,.06)}.outsource-mobile-list{gap:12px}.outsource-order-card.mobile-row{padding:16px;border:0;border-radius:12px;box-shadow:0 2px 8px rgba(31,35,41,.06)}.outsource-card-title{min-width:0}.outsource-card-title .cell-subtitle{line-height:1.65;overflow-wrap:anywhere}.outsource-card-title .cell-subtitle strong{color:var(--text-secondary);font-weight:600}.outsource-process{margin:0 0 7px;color:var(--text-secondary);font-size:12px}.outsource-quantity{display:block;margin:0 0 7px;color:var(--text-secondary);font-size:13px;line-height:1.7}.outsource-quantity strong{color:var(--ink);font-size:16px}.outsource-quantity b{color:var(--good)}.outsource-amount{display:flex;align-items:baseline;gap:5px;flex-wrap:wrap;color:var(--text-secondary);font-size:13px}.outsource-amount strong{color:var(--ink);font-size:16px}.outsource-amount small{color:var(--muted);font-size:11px}.outsource-card-meta{margin:7px 0 0;color:var(--muted);font-size:11px;line-height:1.7}.outsource-card-actions{justify-content:flex-end;margin-top:13px;padding-top:12px;border-top:1px solid var(--line)}.outsource-card-actions .button{min-width:68px}
  .outsource-task-form .form-grid{gap:12px}.outsource-task-form .field>span{font-size:12px}.outsource-task-form input,.outsource-task-form select,.outsource-task-form textarea{font-size:13px}.outsource-task-form .form-actions .button{flex:1}
}
@media(max-width:350px){.outsource-order-card.mobile-row{padding:14px}.outsource-filter-fields{grid-template-columns:1fr}.admin-outsource-head .button{padding-inline:11px}}
</style>
