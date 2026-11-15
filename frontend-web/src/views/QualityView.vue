<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { api, download, rows } from '../api'
import { dateTime, number } from '../format'
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
const filters = reactive({ status: '', producer_id: '', bed_no: '', page: 1 })
const processes = ref([])
const employees = ref([])
const optionError = ref('')
const busy = ref('')
const modal = ref('')
const saving = ref(false)
const formError = ref('')
const lookupLoading = ref(false)
const ticketOptions = ref([])
const createForm = reactive({ bed_no: '', bundle_no: '', ticket: '', defect_qty: 1, reason: '', process: '', producer: '', remark: '' })
const actionForm = reactive({ id: 0, received_qty: 1, producer: '', remark: '', max: 1 })
const selectedTicket = computed(() => ticketOptions.value.find((ticket) => ticket.id === Number(createForm.ticket)))

async function loadPage(append = false) {
  if (!append && window.matchMedia('(max-width: 760px)').matches) filters.page = 1
  const requestedPage = filters.page
  if (append) { loadingMore.value = true; appendError.value = '' }
  else { loading.value = true; error.value = ''; appendError.value = '' }
  try {
    const result = await api('/quality/records', { params: { ...filters, page_size: 20 } })
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
    api('/processes', { params: { page: 1, page_size: 200 } }),
    api('/employees', { params: { tab: 'all' } }),
  ])
  if (results[0].status === 'fulfilled') processes.value = rows(results[0].value)
  if (results[1].status === 'fulfilled') employees.value = rows(results[1].value)
  optionError.value = results.filter((result) => result.status === 'rejected').map((result) => result.reason.message).join('；')
}
function resetPage() { filters.page = 1; load() }
async function exportData() { try { await download('/quality/export', { status: filters.status, producer_id: filters.producer_id, bed_no: filters.bed_no }); emit('toast', '质检记录已导出') } catch (e) { error.value = e.message } }
function statusClass(status) { return status === 'done' ? 'done' : status === 'received' ? 'settled' : 'producing' }
function openCreate() {
  Object.assign(createForm, { bed_no: '', bundle_no: '', ticket: '', defect_qty: 1, reason: '', process: '', producer: '', remark: '' })
  ticketOptions.value = []; formError.value = ''; modal.value = 'create'
}
function closeModal() { if (!saving.value) modal.value = '' }
async function lookupTickets() {
  formError.value = ''
  if (!createForm.bed_no.trim() && !createForm.bundle_no.trim()) { formError.value = '床次号和扎号至少填写一项'; return }
  lookupLoading.value = true
  try {
    const result = await api('/tickets', { params: { bed_no: createForm.bed_no.trim(), bundle_no: createForm.bundle_no.trim(), page: 1, page_size: 50 } })
    ticketOptions.value = rows(result); createForm.ticket = ''
    if (!ticketOptions.value.length) formError.value = '未找到匹配菲票，请调整床次号或扎号'
  } catch (e) { formError.value = e.message; ticketOptions.value = [] } finally { lookupLoading.value = false }
}
async function createRecord() {
  formError.value = ''
  const ticket = selectedTicket.value
  const qty = Number(createForm.defect_qty)
  if (!ticket) { formError.value = '请先查询并选择本公司菲票'; return }
  if (!Number.isInteger(qty) || qty <= 0 || qty > Number(ticket.qty)) { formError.value = `返修数量须为 1~${ticket.qty} 的整数`; return }
  if (!createForm.reason.trim()) { formError.value = '请填写返修原因'; return }
  saving.value = true
  try {
    await api('/quality/records', { method: 'POST', body: { ticket: ticket.id, defect_qty: qty, reason: createForm.reason.trim(), process: createForm.process || null, producer: createForm.producer || null, remark: createForm.remark.trim() } })
    modal.value = ''; emit('toast', '返修记录已登记'); filters.page = 1; await load()
  } catch (e) { formError.value = e.message } finally { saving.value = false }
}
function openReceive(record) {
  Object.assign(actionForm, { id: record.id, received_qty: Number(record.defect_qty), producer: record.producer || '', remark: '', max: Number(record.defect_qty) })
  formError.value = ''; modal.value = 'receive'
}
function openFinish(record) {
  Object.assign(actionForm, { id: record.id, received_qty: record.received_qty, producer: record.producer || '', remark: record.remark || '', max: record.defect_qty })
  formError.value = ''; modal.value = 'finish'
}
async function submitAction(kind) {
  formError.value = ''
  const body = { action: kind }
  if (kind === 'receive') {
    const qty = Number(actionForm.received_qty)
    if (!Number.isInteger(qty) || qty < 1 || qty > actionForm.max) { formError.value = `收回数量须为 1~${actionForm.max} 的整数`; return }
    body.received_qty = qty
    if (actionForm.producer) body.producer = Number(actionForm.producer)
  } else body.remark = actionForm.remark.trim()
  saving.value = true
  try {
    await api(`/quality/records/${actionForm.id}`, { method: 'PUT', body })
    modal.value = ''; emit('toast', kind === 'receive' ? '返修收货已登记' : '返修已完成'); await load()
  } catch (e) { formError.value = e.message } finally { saving.value = false }
}
async function remove(record) {
  if (!window.confirm(`确定删除 ${record.ticket_bed_no} 第 ${record.ticket_bundle_no} 扎的质检记录吗？删除后不可恢复。`)) return
  busy.value = `delete-${record.id}`; error.value = ''
  try {
    await api(`/quality/records/${record.id}`, { method: 'DELETE' }); emit('toast', '质检记录已删除')
    if (rows(data.value).length === 1 && filters.page > 1) filters.page -= 1
    await load()
  } catch (e) { error.value = e.message } finally { busy.value = '' }
}
onMounted(() => { load(); loadOptions() })
</script>

<template>
  <div class="welcome"><div><h2>质检管理</h2><p>登记返修，并按“返修中 → 已收回 → 已完成”跟踪处理。</p></div><div class="admin-quality-head"><button class="button secondary quality-export" @click="exportData">导出 Excel</button><button class="button secondary quality-refresh" @click="load"><AppIcon name="refresh" :size="16" />刷新</button><button class="button primary" @click="openCreate">登记返修</button></div></div>
  <form class="filter-bar quality-filter" @submit.prevent="resetPage"><div class="segmented quality-status-tabs"><button v-for="tab in [['','全部'],['rework','返修中'],['received','已收回'],['done','已完成']]" :key="tab[0]" type="button" :class="{ active: filters.status === tab[0] }" @click="filters.status = tab[0]; resetPage()">{{ tab[1] }}</button></div><div class="quality-filter-fields"><label class="search-box"><AppIcon name="search" :size="17" /><input v-model="filters.bed_no" placeholder="按床次号精确筛选" /></label><select v-model="filters.producer_id" aria-label="按返修工人筛选" @change="resetPage"><option value="">全部返修工人</option><option v-for="employee in employees" :key="employee.user_id" :value="employee.user_id">{{ employee.name }}</option></select><button class="button primary quality-query">查询</button></div></form>
  <p v-if="optionError" class="section-note">工序或员工选项加载失败：{{ optionError }} <button class="button ghost small" @click="loadOptions">重试</button></p>
  <section class="card table-card quality-list-card"><PageState :loading="loading" :error="error" :empty="!rows(data).length" empty-text="暂无质检记录，点「登记返修」开始质检" @retry="load"><div class="table-wrap desktop-table"><table><thead><tr><th>菲票 / 款号</th><th>返修工序</th><th class="numeric">返修 / 收回</th><th>返修工人 / 质检人</th><th>状态 / 时间</th><th>原因与操作</th></tr></thead><tbody><tr v-for="record in rows(data)" :key="record.id"><td><span class="cell-title">{{ record.ticket_bed_no }} · 第 {{ record.ticket_bundle_no }} 扎</span><small class="cell-subtitle">{{ record.style_no || '—' }} · {{ record.ticket_color }} / {{ record.ticket_size }} · 扎数 {{ record.ticket_qty }}</small></td><td>{{ record.process_name || '整扎通用返修' }}</td><td class="numeric">{{ number(record.defect_qty) }} / {{ number(record.received_qty) }}</td><td>{{ record.producer_name || '未指定' }}<small class="cell-subtitle">质检：{{ record.inspector_name || '—' }}</small></td><td><span class="badge" :class="statusClass(record.status)">{{ record.status_display }}</span><small class="cell-subtitle">{{ dateTime(record.inspected_at) }}</small></td><td><span class="admin-quality-reason">{{ record.reason }}</span><div class="admin-quality-actions"><button v-if="record.status === 'rework'" class="button secondary small" :disabled="!!busy" @click="openReceive(record)">收货</button><button v-if="record.status === 'received'" class="button primary small" :disabled="!!busy" @click="openFinish(record)">完成</button><button v-if="record.status !== 'done'" class="button danger small" :disabled="!!busy" @click="remove(record)">删除</button></div></td></tr></tbody></table></div><div class="mobile-cards quality-mobile-list"><article v-for="record in rows(data)" :key="record.id" class="mobile-row quality-record-card"><div class="mobile-row-head"><div class="quality-card-title"><div class="mobile-row-title">{{ record.ticket_bed_no }} 第{{ record.ticket_bundle_no }}扎</div><small class="cell-subtitle">{{ record.style_no || '—' }} · {{ record.ticket_color }}/{{ record.ticket_size }}<template v-if="record.process_name"> · {{ record.process_name }}</template></small></div><span class="badge" :class="statusClass(record.status)">{{ record.status_display }}</span></div><p class="mobile-meta quality-quantity">返修 <strong>{{ number(record.defect_qty) }}</strong> 件<template v-if="Number(record.received_qty)"> · 已收回 <b>{{ number(record.received_qty) }}</b> 件</template><span>（扎数 {{ number(record.ticket_qty) }}）</span></p><p class="admin-quality-note">原因：{{ record.reason }}<span v-if="record.remark">；备注：{{ record.remark }}</span></p><p class="quality-card-meta">质检人 {{ record.inspector_name || '—' }} · {{ dateTime(record.inspected_at) }}<template v-if="record.producer_name"> · 返修 {{ record.producer_name }}</template></p><div v-if="record.status !== 'done'" class="admin-quality-actions quality-card-actions"><button v-if="record.status === 'rework'" class="button primary small" :disabled="!!busy" @click="openReceive(record)">收货</button><button v-if="record.status === 'received'" class="button primary small" :disabled="!!busy" @click="openFinish(record)">完成</button><button class="button danger small" :disabled="!!busy" @click="remove(record)">删除</button></div></article></div></PageState><MobileLoadMore :loading="loadingMore" :error="appendError" :has-more="hasMore" :count="rows(data).length" @more="loadMore" /><AdminPagination class="desktop-pagination" :page="filters.page" :count="data.count || 0" :disabled="loading" @change="filters.page = $event; load()" /></section>
  <p class="section-note">后端契约不提供普通字段编辑，本页提供新增、收货、完成、删除和按当前筛选导出。</p>

  <AdminModal v-if="modal === 'create'" title="登记返修" description="先从本公司菲票接口查询并选择，再填写返修信息。" :saving="saving" wide @close="closeModal"><form class="quality-task-form" @submit.prevent="createRecord"><div class="admin-ticket-lookup"><label class="field"><span>床次号</span><input v-model="createForm.bed_no" placeholder="如：C20261101" /></label><label class="field"><span>扎号</span><input v-model="createForm.bundle_no" placeholder="如：1" /></label><button type="button" class="button secondary" :disabled="lookupLoading" @click="lookupTickets"><span v-if="lookupLoading" class="spinner" />查询菲票</button></div><label class="field admin-ticket-select"><span>本公司菲票 *</span><select v-model="createForm.ticket" required><option value="">{{ ticketOptions.length ? '请选择菲票' : '请先查询菲票' }}</option><option v-for="ticket in ticketOptions" :key="ticket.id" :value="ticket.id">{{ ticket.bed_no }} · 第 {{ ticket.bundle_no }} 扎 · {{ ticket.style_no }} · {{ ticket.color }}/{{ ticket.size }} · {{ ticket.qty }} 件</option></select></label><div class="form-grid"><label class="field"><span>返修数量 *</span><input v-model.number="createForm.defect_qty" type="number" min="1" :max="selectedTicket?.qty" step="1" required /></label><label class="field"><span>返修工序</span><select v-model="createForm.process"><option value="">整扎通用返修</option><option v-for="process in processes" :key="process.id" :value="process.id">{{ process.name }}</option></select></label><label class="field"><span>返修工人</span><select v-model="createForm.producer"><option value="">暂不指定</option><option v-for="employee in employees" :key="employee.user_id" :value="employee.user_id">{{ employee.name }} · {{ employee.post_name || '未设岗位' }}</option></select></label><label class="field full"><span>返修原因 *</span><textarea v-model="createForm.reason" required maxlength="200" rows="3" placeholder="描述疵点、尺寸或工艺问题" /></label><label class="field full"><span>备注</span><textarea v-model="createForm.remark" maxlength="200" rows="2" /></label></div><p v-if="formError" class="form-error">{{ formError }}</p><div class="form-actions"><button type="button" class="button secondary" :disabled="saving" @click="closeModal">取消</button><button class="button primary" :disabled="saving || !selectedTicket"><span v-if="saving" class="spinner light" />保存返修记录</button></div></form></AdminModal>

  <AdminModal v-if="modal === 'receive'" title="返修收货" description="确认实际收回数量，可补充或调整返修工人。" :saving="saving" @close="closeModal"><form @submit.prevent="submitAction('receive')"><div class="form-grid"><label class="field"><span>收回数量（不超过 {{ actionForm.max }}）*</span><input v-model.number="actionForm.received_qty" type="number" min="1" :max="actionForm.max" step="1" required /></label><label class="field"><span>返修工人</span><select v-model="actionForm.producer"><option value="">保持未指定</option><option v-for="employee in employees" :key="employee.user_id" :value="employee.user_id">{{ employee.name }}</option></select></label></div><p v-if="formError" class="form-error">{{ formError }}</p><div class="form-actions"><button type="button" class="button secondary" :disabled="saving" @click="closeModal">取消</button><button class="button primary" :disabled="saving">确认收货</button></div></form></AdminModal>

  <AdminModal v-if="modal === 'finish'" title="确认返修完成" description="完成后为终态，不可再删除或重复收货。" :saving="saving" @close="closeModal"><form @submit.prevent="submitAction('finish')"><label class="field"><span>处理结果备注</span><textarea v-model="actionForm.remark" maxlength="200" rows="4" placeholder="选填" /></label><p v-if="formError" class="form-error">{{ formError }}</p><div class="form-actions"><button type="button" class="button secondary" :disabled="saving" @click="closeModal">取消</button><button class="button primary" :disabled="saving">确认完成</button></div></form></AdminModal>
</template>

<style scoped>
.admin-quality-head{display:flex;gap:8px}.quality-filter-fields{display:contents}.admin-quality-reason{display:block;max-width:240px;white-space:normal;line-height:1.5;margin-bottom:8px}.admin-quality-actions{display:flex;gap:6px;flex-wrap:wrap}.admin-quality-note{font-size:12px;color:var(--muted);line-height:1.6;margin:12px 0}.admin-ticket-lookup{display:grid;grid-template-columns:1fr 1fr auto;align-items:end;gap:12px;margin-bottom:16px}.admin-ticket-select{margin-bottom:16px}.form-error{margin-top:15px}
@media(max-width:760px){
  .desktop-pagination{display:none}
  .admin-quality-head{justify-content:flex-end}.admin-quality-head .quality-refresh{display:none}.admin-quality-head .button{font-size:12px;padding:8px 13px}.quality-filter{display:block}.quality-status-tabs{display:flex;width:100%;gap:7px;overflow-x:auto;margin-bottom:10px;padding:2px 1px;background:transparent;scrollbar-width:none}.quality-status-tabs button{flex:1 0 auto;padding:7px 13px;border:0;border-radius:999px;background:#eceff1;color:var(--text-secondary);font-size:12px}.quality-status-tabs button.active{background:var(--orange);color:#fff;box-shadow:none}.quality-filter-fields{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:8px;padding:10px;background:#fff;border-radius:12px;box-shadow:0 2px 8px rgba(31,35,41,.06)}.quality-filter-fields .search-box{grid-column:1/-1;width:100%;min-width:0;max-width:none}.quality-filter-fields .search-box input{min-height:38px;border:0;border-radius:999px;background:#f5f6f8;font-size:12px}.quality-filter-fields select{width:100%;min-width:0;min-height:36px;border:0;border-radius:999px;background:#f5f6f8;font-size:12px}.quality-query{min-height:36px;padding:7px 18px;font-size:12px}
  .quality-list-card.card{padding:0;background:transparent;box-shadow:none;overflow:visible}.quality-list-card :deep(.page-state){border-radius:12px;background:#fff;box-shadow:0 2px 8px rgba(31,35,41,.06)}.quality-mobile-list{gap:12px}.quality-record-card.mobile-row{padding:16px;border:0;border-radius:12px;box-shadow:0 2px 8px rgba(31,35,41,.06)}.quality-card-title{min-width:0}.quality-card-title .cell-subtitle{line-height:1.6;overflow-wrap:anywhere}.quality-quantity{display:block;margin:2px 0 0;color:var(--text-secondary);font-size:13px;line-height:1.7}.quality-quantity strong{color:var(--danger);font-size:17px}.quality-quantity b{color:var(--good)}.quality-quantity>span{color:var(--muted);font-size:11px}.quality-card-meta{margin:0;color:var(--muted);font-size:11px;line-height:1.7;overflow-wrap:anywhere}.quality-card-actions{justify-content:flex-end;margin-top:13px;padding-top:12px;border-top:1px solid var(--line)}.quality-card-actions .button{min-width:68px}
  .admin-ticket-lookup{grid-template-columns:1fr 1fr;gap:9px;padding:12px;border-radius:10px;background:#f7f8fa}.admin-ticket-lookup .button{grid-column:1/-1}.quality-task-form .form-grid{gap:12px}.quality-task-form .field>span{font-size:12px}.quality-task-form input,.quality-task-form select,.quality-task-form textarea{font-size:13px}.quality-task-form .admin-ticket-select{margin:14px 0}.quality-task-form .form-actions .button{flex:1}
}
@media(max-width:350px){.quality-record-card.mobile-row{padding:14px}.quality-filter-fields{padding:8px}.admin-quality-head .button{padding-inline:11px}}
</style>
