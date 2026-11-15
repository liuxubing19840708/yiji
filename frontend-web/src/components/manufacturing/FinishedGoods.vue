<script setup>
import { computed, inject, reactive, ref, watch } from 'vue'
import { api, createRequestKey, rows } from '../../api'
import { dateTime } from '../../format'
import AdminModal from '../AdminModal.vue'
import PageState from '../PageState.vue'
import TicketIdentity from './TicketIdentity.vue'
import { useTicketLookup } from './useTicketLookup'

const emit = defineEmits(['toast'])
const flow = inject('manufacturingFlow')
const { hydrate, displayTicket } = useTicketLookup(flow)
const tasks = ref([])
const inbounds = ref([])
const loading = ref(true)
const error = ref('')
const modal = ref('')
const saving = ref(false)
const formError = ref('')
const busy = ref('')
const approvalKeys = reactive({})
const reversalDetails = reactive({})
const reversalLoading = ref('')
const packing = reactive({ task_no: '', remark: '', requestId: '', boxes: [] })
const inbound = reactive({ taskId: '', document_no: '', warehouse_name: '', location_name: '', remark: '', requestId: '', selections: {} })
const approvedTasks = computed(() => tasks.value.filter((task) => task.status === 'approved'))
const selectedTask = computed(() => approvedTasks.value.find((task) => task.id === Number(inbound.taskId)) || null)
const canWrite = computed(() => flow.can('finished_goods.manage') && flow.can('manufacturing.view') && flow.route.value && !flow.route.value.is_template)
const allLines = (documents) => documents.filter((document) => document.status !== 'reversed').flatMap((document) => document.lines || [])
async function load() {
  loading.value = true
  error.value = ''
  const params = flow.route.value && !flow.route.value.is_template ? { route_id: flow.route.value.id } : {}
  try {
    const [taskData, inboundData] = await Promise.all([
      api('/finished-goods/packing-tasks', { params }), api('/finished-goods/inbounds', { params }),
    ])
    tasks.value = rows(taskData)
    inbounds.value = rows(inboundData)
    const lines = [...tasks.value.flatMap((task) => task.boxes.flatMap((box) => box.lines)), ...allLines(inbounds.value)]
    await hydrate(lines)
  } catch (reason) { error.value = reason.message } finally { loading.value = false }
}
function addBox() { packing.boxes.push({ box_no: '', items: [{ ticket_id: flow.ticket.value ? String(flow.ticket.value.id) : '', quantity: 1 }] }) }
function addLine(box) { box.items.push({ ticket_id: flow.ticket.value ? String(flow.ticket.value.id) : '', quantity: 1 }) }
function openPacking() {
  Object.assign(packing, { task_no: '', remark: '', requestId: createRequestKey(), boxes: [] })
  addBox()
  formError.value = ''
  modal.value = 'packing'
}
async function createPacking() {
  formError.value = ''
  if (!flow.route.value || !packing.boxes.length) { formError.value = '请选择实例路线并至少添加一个包装箱'; return }
  if (packing.boxes.some((box) => !box.box_no.trim() || !box.items.length)) { formError.value = '每箱均需填写箱号并至少一行菲票'; return }
  const boxNumbers = packing.boxes.map((box) => box.box_no.trim())
  if (new Set(boxNumbers).size !== boxNumbers.length) { formError.value = '箱号不能重复'; return }
  for (const box of packing.boxes) {
    const ids = box.items.map((line) => Number(line.ticket_id))
    if (ids.some((id) => !id) || new Set(ids).size !== ids.length) { formError.value = '每箱必须选择菲票，且同箱菲票不能重复'; return }
    if (box.items.some((line) => !Number.isInteger(Number(line.quantity)) || Number(line.quantity) <= 0)) { formError.value = '包装数量须为正整数'; return }
  }
  saving.value = true
  try {
    await api('/finished-goods/packing-tasks', { method: 'POST', body: {
      route_id: flow.route.value.id, task_no: packing.task_no.trim(), request_id: packing.requestId, remark: packing.remark.trim(),
      boxes: packing.boxes.map((box) => ({ box_no: box.box_no.trim(), items: box.items.map((line) => ({ ticket_id: Number(line.ticket_id), quantity: Number(line.quantity) })) })),
    } })
    modal.value = ''
    emit('toast', '包装任务已创建，请审核后形成包装合格量')
    await load()
  } catch (reason) {
    const rejected = reason.status >= 400 && reason.status < 500
    if (rejected) packing.requestId = createRequestKey()
    formError.value = `${reason.message}；${rejected ? '请求已被拒绝，修正后重试将使用新幂等号' : '网络或服务异常，重试沿用同一幂等号'}`
  } finally { saving.value = false }
}
async function loadReversalDetail(kind, document, event) {
  if (!event.target.open) return
  const key = `${kind}-${document.id}`
  if (reversalDetails[key] || reversalLoading.value) return
  reversalLoading.value = key
  try {
    reversalDetails[key] = await api(`/finished-goods/${kind}/${document.id}`)
  } catch (reason) { error.value = reason.message } finally { reversalLoading.value = '' }
}
async function reverseDocument(kind, document) {
  const isTask = kind === 'packing-tasks'
  const label = isTask ? document.task_no : document.document_no
  const reason = window.prompt(`冲销${isTask ? '包装任务' : '入库单'} ${label} 的原因：`)
  if (reason === null) return
  if (!reason.trim()) { error.value = '冲销原因不能为空'; return }
  if (!window.confirm('冲销只新增反向数量流水并保留原单，不能重新审核。确认继续？')) return
  const key = `reverse-${kind}-${document.id}`
  approvalKeys[key] ||= createRequestKey()
  busy.value = key
  error.value = ''
  try {
    await api(`/finished-goods/${kind}/${document.id}/reverse`, { method: 'POST', body: { request_id: approvalKeys[key], reason: reason.trim() } })
    delete approvalKeys[key]
    emit('toast', `${isTask ? '包装任务' : '入库单'}已冲销，原单及流水保留`)
    await load()
  } catch (reason) {
    if (reason.status >= 400 && reason.status < 500) approvalKeys[key] = createRequestKey()
    error.value = `${reason.message}${reason.status >= 400 && reason.status < 500 ? '；修正后续单证后再次冲销将使用新幂等号' : '；网络或服务异常可沿用原幂等号重试'}`
  } finally { busy.value = '' }
}
async function approveTask(task) {
  if (!window.confirm(`审核包装任务 ${task.task_no}？审核后箱明细冻结，并写入包装合格量。`)) return
  busy.value = `task-${task.id}`
  approvalKeys[`task-${task.id}`] ||= createRequestKey()
  error.value = ''
  try { await api(`/finished-goods/packing-tasks/${task.id}/approve`, { method: 'POST', body: { request_id: approvalKeys[`task-${task.id}`] } }); delete approvalKeys[`task-${task.id}`]; emit('toast', '包装任务已审核'); await load() }
  catch (reason) {
    if (reason.status >= 400 && reason.status < 500) approvalKeys[`task-${task.id}`] = createRequestKey()
    error.value = `${reason.message}${reason.status >= 400 && reason.status < 500 ? '；再次审核将使用新幂等号' : '；网络或服务异常可沿用原幂等号重试'}`
  } finally { busy.value = '' }
}
function remaining(box, line) {
  const used = allLines(inbounds.value)
    .filter((item) => item.box === box.id && item.ticket === line.ticket).reduce((sum, item) => sum + Number(item.quantity), 0)
  return Math.max(0, Number(line.quantity) - used)
}
function selectTask() {
  inbound.selections = {}
  inbound.requestId = createRequestKey()
  selectedTask.value?.boxes.forEach((box) => box.lines.forEach((line) => {
    const available = remaining(box, line)
    if (available > 0) inbound.selections[`${box.id}-${line.ticket}`] = { ...line, checked: true, quantity: available, max: available, box_id: box.id, ticket_id: line.ticket }
  }))
}
function openInbound() {
  Object.assign(inbound, { taskId: approvedTasks.value[0]?.id || '', document_no: '', warehouse_name: '', location_name: '', remark: '', requestId: createRequestKey(), selections: {} })
  formError.value = ''
  selectTask()
  modal.value = 'inbound'
}
const selectedInboundLines = computed(() => Object.entries(inbound.selections).filter(([, line]) => line.checked))
async function createInbound() {
  formError.value = ''
  if (!flow.route.value || !selectedInboundLines.value.length) { formError.value = '请选择已审核包装任务和至少一行入库明细'; return }
  if (selectedInboundLines.value.some(([, line]) => !Number.isInteger(Number(line.quantity)) || Number(line.quantity) <= 0 || Number(line.quantity) > line.max)) { formError.value = '入库数量须为正整数，且不能超过已包装未入库数量'; return }
  saving.value = true
  try {
    await api('/finished-goods/inbounds', { method: 'POST', body: {
      route_id: flow.route.value.id, document_no: inbound.document_no.trim(), warehouse_name: inbound.warehouse_name.trim(), location_name: inbound.location_name.trim(), remark: inbound.remark.trim(), request_id: inbound.requestId,
      lines: selectedInboundLines.value.map(([, line]) => ({ box_id: line.box_id, ticket_id: line.ticket_id, quantity: Number(line.quantity) })),
    } })
    modal.value = ''
    emit('toast', '成品入库单已创建，审核后计入现货')
    await load()
  } catch (reason) {
    const rejected = reason.status >= 400 && reason.status < 500
    if (rejected) inbound.requestId = createRequestKey()
    formError.value = `${reason.message}；${rejected ? '请求已被拒绝，修正后重试将使用新幂等号' : '网络或服务异常，重试沿用同一幂等号'}`
  } finally { saving.value = false }
}
async function approveInbound(document) {
  if (!window.confirm(`审核入库单 ${document.document_no}？审核后从包装节点转入成品入库节点。`)) return
  busy.value = `inbound-${document.id}`
  approvalKeys[`inbound-${document.id}`] ||= createRequestKey()
  error.value = ''
  try { await api(`/finished-goods/inbounds/${document.id}/approve`, { method: 'POST', body: { request_id: approvalKeys[`inbound-${document.id}`] } }); delete approvalKeys[`inbound-${document.id}`]; emit('toast', '成品入库单已审核'); await load() }
  catch (reason) {
    if (reason.status >= 400 && reason.status < 500) approvalKeys[`inbound-${document.id}`] = createRequestKey()
    error.value = `${reason.message}${reason.status >= 400 && reason.status < 500 ? '；再次审核将使用新幂等号' : '；网络或服务异常可沿用原幂等号重试'}`
  } finally { busy.value = '' }
}
watch(() => flow.state.selectedRouteId, load, { immediate: true })
</script>

<template>
  <div class="finished-goods">
    <PageState :loading="loading" :error="error" :empty="!tasks.length && !inbounds.length" empty-text="当前路线暂无包装或入库单证" @retry="load">
      <section class="card document-card"><div class="card-header"><div><h3>包装任务</h3><p>按箱维护菲票色码明细，审核后写入 packing 合格量。</p></div><button v-if="canWrite" class="button primary" :disabled="!flow.ticket.value" @click="openPacking">新建包装任务</button></div><div class="document-list"><article v-for="task in tasks" :key="task.id" class="document-row"><div class="document-summary"><div><strong>{{ task.task_no }}</strong><small>{{ task.route_code }} · {{ task.boxes.length }} 箱 · {{ task.total_quantity }} 件 · {{ dateTime(task.created_at) }}</small></div><span class="badge" :class="task.status === 'approved' ? 'done' : task.status === 'reversed' ? 'cancelled' : 'producing'">{{ task.status_display }}</span><button v-if="flow.can('finished_goods.manage') && task.status === 'draft'" class="button primary small" :disabled="busy === `task-${task.id}`" @click="approveTask(task)">审核包装</button><button v-if="flow.can('finished_goods.manage') && task.status !== 'reversed'" class="button danger small" :disabled="!!busy" @click="reverseDocument('packing-tasks', task)">冲销包装</button></div><p v-if="task.status === 'reversed'" class="reversal-note">冲销：{{ task.reversal_reason }} · {{ task.reverser_name || '未知人员' }} · {{ dateTime(task.reversed_at) }}</p><details @toggle="task.status === 'reversed' && loadReversalDetail('packing-tasks', task, $event)"><summary>查看箱内菲票、床款色码及冲销关联流水</summary><div class="document-lines"><div v-for="box in task.boxes" :key="box.id"><strong>箱 {{ box.box_no }}</strong><p v-for="line in box.lines" :key="line.id">{{ displayTicket(line).bed_no }} · {{ displayTicket(line).style_no || '—' }} · #{{ line.ticket }} / 第{{ line.bundle_no }}扎 · {{ line.color }} / {{ line.size }} · {{ line.quantity }}件</p></div></div><div v-if="task.status === 'reversed'" class="ledger-detail"><h4>原流水与反向关联</h4><p v-if="reversalLoading === `packing-tasks-${task.id}`">正在读取流水…</p><p v-for="entry in reversalDetails[`packing-tasks-${task.id}`]?.ledger_entries || []" :key="entry.id">流水 #{{ entry.id }} · {{ entry.node_name }} · {{ entry.event_type_display }} {{ entry.quantity }}件 · {{ entry.operator_name || '系统' }} · {{ entry.reversal_of ? `冲销原流水 #${entry.reversal_of}` : '原流水' }}</p><p v-if="reversalDetails[`packing-tasks-${task.id}`] && !reversalDetails[`packing-tasks-${task.id}`].ledger_entries.length">该单未写入数量流水</p></div></details></article><p v-if="!tasks.length" class="section-note">暂无包装任务。</p></div></section>
      <section class="card document-card"><div class="card-header"><div><h3>成品入库单</h3><p>只能引用本路线已审核箱内明细，审核后才计入成品现货。</p></div><button v-if="canWrite" class="button primary" :disabled="!approvedTasks.length" @click="openInbound">新建入库单</button></div><div class="document-list"><article v-for="document in inbounds" :key="document.id" class="document-row"><div class="document-summary"><div><strong>{{ document.document_no }}</strong><small>{{ document.warehouse_name || '未填仓库' }} / {{ document.location_name || '未填库位' }} · {{ document.total_quantity }} 件 · {{ dateTime(document.created_at) }}</small></div><span class="badge" :class="document.status === 'approved' ? 'done' : document.status === 'reversed' ? 'cancelled' : 'producing'">{{ document.status_display }}</span><button v-if="flow.can('finished_goods.manage') && document.status === 'draft'" class="button primary small" :disabled="busy === `inbound-${document.id}`" @click="approveInbound(document)">审核入库</button><button v-if="flow.can('finished_goods.manage') && document.status !== 'reversed'" class="button danger small" :disabled="!!busy" @click="reverseDocument('inbounds', document)">冲销入库</button></div><p v-if="document.status === 'reversed'" class="reversal-note">冲销：{{ document.reversal_reason }} · {{ document.reverser_name || '未知人员' }} · {{ dateTime(document.reversed_at) }}</p><details @toggle="document.status === 'reversed' && loadReversalDetail('inbounds', document, $event)"><summary>查看入库菲票、床款色码及冲销关联流水</summary><div class="document-lines"><p v-for="line in document.lines" :key="line.id">箱 {{ line.box_no }} · {{ displayTicket(line).bed_no }} · {{ displayTicket(line).style_no || '—' }} · #{{ line.ticket }} / 第{{ line.bundle_no }}扎 · {{ line.color }} / {{ line.size }} · {{ line.quantity }}件</p></div><div v-if="document.status === 'reversed'" class="ledger-detail"><h4>原流水与反向关联</h4><p v-if="reversalLoading === `inbounds-${document.id}`">正在读取流水…</p><p v-for="entry in reversalDetails[`inbounds-${document.id}`]?.ledger_entries || []" :key="entry.id">流水 #{{ entry.id }} · {{ entry.node_name }} · {{ entry.event_type_display }} {{ entry.quantity }}件 · {{ entry.operator_name || '系统' }} · {{ entry.reversal_of ? `冲销原流水 #${entry.reversal_of}` : '原流水' }}</p><p v-if="reversalDetails[`inbounds-${document.id}`] && !reversalDetails[`inbounds-${document.id}`].ledger_entries.length">该单未写入数量流水</p></div></details></article><p v-if="!inbounds.length" class="section-note">暂无成品入库单。</p></div></section>
    </PageState>

    <AdminModal v-if="modal === 'packing'" title="新建包装任务" description="每箱同一菲票只能出现一次；任务审核前仍可重新核对明细。" :saving="saving" wide @close="modal = ''"><form @submit.prevent="createPacking"><TicketIdentity v-if="flow.ticket.value" :ticket="flow.ticket.value" /><div class="form-grid packing-head"><label class="field"><span>任务号（留空自动生成）</span><input v-model="packing.task_no" maxlength="64" /></label><label class="field"><span>备注</span><input v-model="packing.remark" maxlength="255" /></label></div><div v-for="(box, boxIndex) in packing.boxes" :key="boxIndex" class="packing-box"><div class="packing-box-head"><label class="field"><span>箱号 *</span><input v-model="box.box_no" maxlength="64" required /></label><button type="button" class="button danger small" :disabled="packing.boxes.length === 1" @click="packing.boxes.splice(boxIndex, 1)">删除箱</button></div><div v-for="(line, lineIndex) in box.items" :key="lineIndex" class="packing-line"><select v-model="line.ticket_id" required aria-label="包装菲票"><option value="">选择菲票</option><option v-for="ticket in flow.state.tickets" :key="ticket.id" :value="String(ticket.id)">{{ ticket.bed_no }} · {{ ticket.style_no }} · {{ ticket.color }}/{{ ticket.size }} · 第{{ ticket.bundle_no }}扎</option></select><input v-model.number="line.quantity" type="number" min="1" step="1" required aria-label="包装数量" /><button type="button" class="button ghost small" :disabled="box.items.length === 1" @click="box.items.splice(lineIndex, 1)">移除</button></div><button type="button" class="button secondary small" @click="addLine(box)">添加箱内菲票</button></div><button type="button" class="button secondary" @click="addBox">添加包装箱</button><p v-if="formError" class="form-error">{{ formError }}</p><div class="form-actions"><button type="button" class="button secondary" @click="modal = ''">取消</button><button class="button primary" :disabled="saving">保存包装任务</button></div></form></AdminModal>

    <AdminModal v-if="modal === 'inbound'" title="新建成品入库单" description="数量上限按已审核包装减去全部待审核及已审核入库计算。" :saving="saving" wide @close="modal = ''"><form @submit.prevent="createInbound"><label class="field"><span>引用已审核包装任务 *</span><select v-model="inbound.taskId" required @change="selectTask"><option v-for="task in approvedTasks" :key="task.id" :value="task.id">{{ task.task_no }} · {{ task.total_quantity }} 件</option></select></label><div class="form-grid inbound-head"><label class="field"><span>入库单号（留空自动生成）</span><input v-model="inbound.document_no" maxlength="64" /></label><label class="field"><span>仓库</span><input v-model="inbound.warehouse_name" maxlength="128" /></label><label class="field"><span>库位</span><input v-model="inbound.location_name" maxlength="128" /></label><label class="field"><span>备注</span><input v-model="inbound.remark" maxlength="255" /></label></div><div class="inbound-selection"><label v-for="(line, key) in inbound.selections" :key="key" class="inbound-select-row"><input v-model="line.checked" type="checkbox" /><span>{{ displayTicket(line).bed_no }} · 菲票 #{{ line.ticket_id }} · 箱内可入 {{ line.max }} 件</span><input v-model.number="line.quantity" type="number" min="1" :max="line.max" step="1" :disabled="!line.checked" aria-label="入库数量" /></label><p v-if="!Object.keys(inbound.selections).length" class="section-note">该任务的箱内明细均已入库，没有可入数量。</p></div><p v-if="formError" class="form-error">{{ formError }}</p><div class="form-actions"><button type="button" class="button secondary" @click="modal = ''">取消</button><button class="button primary" :disabled="saving || !selectedInboundLines.length">保存入库单</button></div></form></AdminModal>
  </div>
</template>

<style scoped>
.finished-goods{display:grid;gap:16px}.document-card{padding:0;overflow:hidden}.document-card>.card-header{padding:20px 20px 0}.document-row{padding:14px 20px;border-top:1px solid var(--line)}.document-summary{display:grid;grid-template-columns:minmax(0,1fr) auto auto;align-items:center;gap:12px}.document-summary strong,.document-summary small{display:block}.document-summary small{margin-top:5px;color:var(--muted);font-size:11px}.document-row details{margin-top:10px}.reversal-note{margin:10px 0 0;color:var(--danger);font-size:12px}.document-row summary{cursor:pointer;color:var(--orange-dark);font-size:12px}.document-lines{padding:10px 12px;background:#F8F9FA;border-radius:8px}.document-lines p{margin:7px 0;color:var(--text-secondary);font-size:12px;overflow-wrap:anywhere}.ledger-detail{padding:10px 12px;margin-top:8px;border-top:1px dashed var(--line)}.ledger-detail h4{margin:0 0 6px;font-size:12px}.ledger-detail p{margin:6px 0;color:var(--text-secondary);font-size:12px;overflow-wrap:anywhere}.document-list>.section-note{margin:0;padding:0 20px 18px}.packing-head,.inbound-head{margin:16px 0}.packing-box{padding:12px;margin:12px 0;border:1px solid var(--line);border-radius:10px}.packing-box-head{display:flex;align-items:end;gap:10px}.packing-box-head .field{flex:1}.packing-line{display:grid;grid-template-columns:minmax(0,1fr) 100px 55px;gap:8px;margin:10px 0}.inbound-selection{margin-top:16px}.inbound-select-row{display:grid;grid-template-columns:20px minmax(0,1fr) 100px;align-items:center;gap:9px;padding:9px 0;border-bottom:1px solid var(--line);font-size:12px}.inbound-select-row>input[type=checkbox]{width:17px;min-height:17px}
@media(max-width:760px){.document-card>.card-header{padding:14px 12px 0}.document-row{padding:12px}.document-summary{grid-template-columns:minmax(0,1fr) auto}.document-summary .button{grid-column:1/-1;justify-self:end}.packing-line{grid-template-columns:minmax(0,1fr) 72px}.packing-line .button{grid-column:1/-1;justify-self:end}.inbound-select-row{grid-template-columns:20px minmax(0,1fr)}.inbound-select-row>input[type=number]{grid-column:2}}
</style>
