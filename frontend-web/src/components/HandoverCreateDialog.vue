<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { api, createRequestKey, rows } from '../api'
import { createHandovers } from '../handover'
import AdminModal from './AdminModal.vue'
import PageState from './PageState.vue'

const emit = defineEmits(['close', 'created', 'toast'])
const loading = ref(true)
const ticketLoading = ref(false)
const saving = ref(false)
const error = ref('')
const routes = ref([])
const employees = ref([])
const productions = ref([])
const tickets = ref([])
const balances = ref([])
const form = reactive({ routeId: '', sourceNode: '', targetNode: '', remark: '', targets: [emptyTarget()] })
let requestId = createRequestKey()

function emptyTarget() { return { receiver: '', group: '' } }
function invalidate() { requestId = createRequestKey() }
const route = computed(() => routes.value.find((item) => item.id === Number(form.routeId)) || null)
const nodes = computed(() => [...(route.value?.nodes || [])].sort((a, b) => a.sequence - b.sequence))
const sourceNodes = computed(() => nodes.value.filter((node) => node.stage_type === 'cutting'))
const source = computed(() => nodes.value.find((node) => node.id === Number(form.sourceNode)) || null)
const targetNodes = computed(() => nodes.value.filter((node) => node.sequence > (source.value?.sequence || 0)))
const groups = computed(() => [...new Set(employees.value.map((item) => Number(item.group_no)).filter((value) => value > 0))].sort((a, b) => a - b))
const selectedTickets = computed(() => tickets.value.filter((ticket) => ticket.selected))
const totalQuantity = computed(() => selectedTickets.value.reduce((sum, ticket) => sum + (Number(ticket.input) || 0), 0))
const productionById = computed(() => new Map(productions.value.map((item) => [item.id, item])))

function routeLabel(item) {
  const production = productionById.value.get(item.production)
  return `${item.name || item.code}${production ? ` · ${production.bed_no}` : item.order ? ` · 订单 ${item.order}` : ''} · ${item.code}`
}
function targetName(index) {
  const target = form.targets[index]
  if (!target) return `目标 ${index + 1}`
  const employee = employees.value.find((item) => item.user_id === Number(target.receiver))
  if (employee) return `${employee.name}${target.group ? ` / ${target.group} 组` : ''}`
  return target.group ? `${target.group} 组` : `目标 ${index + 1}（未设置）`
}
function ticketLabel(ticket) {
  return `${ticket.bed_no} / ${ticket.bundle_no} 扎${ticket.color ? ` / ${ticket.color}` : ''}${ticket.size ? ` / ${ticket.size}` : ''}`
}
function transferable(ticket) {
  const row = balances.value.find((item) => item.ticket_id === ticket.id && item.node_id === Number(form.sourceNode))
  return row ? Number(row.balance?.transferable || 0) : null
}
async function fetchAll(path, params = {}) {
  const result = []
  let page = 1
  while (true) {
    const data = await api(path, { params: { ...params, page, page_size: 200 } })
    const current = rows(data)
    result.push(...current)
    if (!data.next || !current.length) return result
    page += 1
  }
}
async function loadContext() {
  loading.value = true
  error.value = ''
  try {
    const [routeData, employeeData, productionData] = await Promise.all([
      api('/manufacturing/routes', { params: { is_template: 0, is_active: 1 } }),
      api('/employees', { params: { tab: 'all', page_size: 200 } }),
      fetchAll('/productions'),
    ])
    routes.value = rows(routeData).filter((item) => item.nodes?.some((node) => node.stage_type === 'cutting'))
    employees.value = rows(employeeData).filter((item) => !item.is_suspended)
    productions.value = productionData
    if (!routes.value.length) error.value = '暂无包含裁剪节点的有效实例路线，请先在全流程生产中创建路线。'
  } catch (reason) { error.value = `交接基础数据读取失败：${reason.message}` } finally { loading.value = false }
}
async function selectRoute() {
  invalidate()
  form.sourceNode = ''
  form.targetNode = ''
  tickets.value = []
  balances.value = []
  error.value = ''
  if (!route.value) return
  ticketLoading.value = true
  try {
    if (route.value.production) {
      tickets.value = (await fetchAll('/tickets', { production_id: route.value.production })).map(decorateTicket)
    } else if (route.value.order) {
      const beds = productions.value.filter((item) => item.order === route.value.order)
      const groupsOfTickets = await Promise.all(beds.map((bed) => fetchAll('/tickets', { production_id: bed.id })))
      tickets.value = groupsOfTickets.flat().map(decorateTicket)
    }
    if (!tickets.value.length) error.value = '该实例路线没有可选择的菲票。'
  } catch (reason) { error.value = `菲票读取失败：${reason.message}` } finally { ticketLoading.value = false }
}
function decorateTicket(ticket) {
  return { ...ticket, selected: false, target: '', input: String(ticket.qty || 1) }
}
async function selectSource() {
  invalidate()
  form.targetNode = ''
  balances.value = []
  error.value = ''
  if (!route.value || !form.sourceNode) return
  ticketLoading.value = true
  try {
    balances.value = await api(`/manufacturing/routes/${route.value.id}/balances`)
  } catch (reason) { error.value = `节点可转数量读取失败：${reason.message}` } finally { ticketLoading.value = false }
}
function addTarget() {
  invalidate()
  form.targets.push(emptyTarget())
}
function removeTarget(index) {
  if (form.targets.length === 1) return
  invalidate()
  form.targets.splice(index, 1)
  tickets.value.forEach((ticket) => {
    const target = Number(ticket.target)
    if (ticket.target !== '' && target === index) ticket.target = ''
    else if (ticket.target !== '' && target > index) ticket.target = String(target - 1)
  })
}
function validate() {
  if (!route.value || !source.value || !form.targetNode) return '请选择实例路线、裁剪来源节点和后续接收节点。'
  if (!form.targets.length || form.targets.some((target) => !target.receiver && !target.group)) return '每个接收目标须选择在职接收人或目标组。'
  if (!selectedTickets.value.length) return '请至少选择一张菲票。'
  if (selectedTickets.value.some((ticket) => ticket.target === '' || !form.targets[Number(ticket.target)])) return '请为每张已选菲票分配接收目标。'
  if (form.targets.some((target, index) => !selectedTickets.value.some((ticket) => Number(ticket.target) === index))) return '每个接收目标至少分配一张菲票；空目标请移除。'
  for (const ticket of selectedTickets.value) {
    const quantity = Number(ticket.input)
    if (!Number.isInteger(quantity) || quantity <= 0) return `${ticketLabel(ticket)} 的交接数量须为正整数。`
    const available = transferable(ticket)
    if (available !== null && quantity > available) return `${ticketLabel(ticket)} 最多可转 ${available} 件；其他待交接预占由后端继续校验。`
  }
  return ''
}
async function submit() {
  error.value = validate()
  if (error.value || saving.value) return
  const grouped = form.targets.map(() => [])
  selectedTickets.value.forEach((ticket) => grouped[Number(ticket.target)].push({ ticket: ticket.id, quantity: Number(ticket.input) }))
  saving.value = true
  try {
    const result = await createHandovers({
      kind: 'cutting', request_id: requestId, source_node: Number(form.sourceNode),
      target_node: Number(form.targetNode), remark: form.remark.trim(),
      targets: form.targets.map((target, index) => ({
        receiver: target.receiver ? Number(target.receiver) : null,
        target_group_no: target.group ? Number(target.group) : null,
        items: grouped[index],
      })),
    })
    emit('toast', `交接发起成功，共 ${result.items?.length || 0} 张交接单`)
    emit('created', result)
  } catch (reason) {
    const rejected = reason.status >= 400 && reason.status < 500
    if (rejected) invalidate()
    error.value = `${reason.message}；${rejected ? '修正后重试将使用新幂等号。' : '网络或服务异常，重试沿用同一幂等号。'}`
  } finally { saving.value = false }
}
onMounted(loadContext)
</script>

<template>
  <AdminModal title="批量发起责任交接" description="每个接收目标生成一张独立交接单；同一张菲票不能跨目标重复分配。" :saving="saving" wide @close="emit('close')">
    <PageState :loading="loading" :error="error && !routes.length ? error : ''" @retry="loadContext">
      <form class="handover-create" @submit.prevent="submit" @input="invalidate" @change="invalidate">
        <div class="form-grid">
          <label class="field full"><span>实例路线 *</span><select v-model="form.routeId" :disabled="saving || ticketLoading" @change="selectRoute"><option value="">选择含裁剪节点的实例路线</option><option v-for="item in routes" :key="item.id" :value="String(item.id)">{{ routeLabel(item) }}</option></select></label>
          <label class="field"><span>交出节点（裁剪）*</span><select v-model="form.sourceNode" :disabled="!route || saving || ticketLoading" @change="selectSource"><option value="">选择裁剪节点</option><option v-for="node in sourceNodes" :key="node.id" :value="String(node.id)">{{ node.sequence }}. {{ node.stage_name }}</option></select></label>
          <label class="field"><span>接收节点（后续）*</span><select v-model="form.targetNode" :disabled="!source || saving || ticketLoading"><option value="">选择后续节点</option><option v-for="node in targetNodes" :key="node.id" :value="String(node.id)">{{ node.sequence }}. {{ node.stage_name }}</option></select></label>
        </div>

        <section class="create-section">
          <div class="create-section-head"><div><h3>接收目标</h3><p>支持指定在职员工、目标组，或两者同时指定。</p></div><button type="button" class="button secondary small" :disabled="saving" @click="addTarget">添加目标</button></div>
          <div class="target-grid">
            <article v-for="(target, index) in form.targets" :key="index" class="target-editor">
              <div class="target-editor-title"><strong>目标 {{ index + 1 }}</strong><button v-if="form.targets.length > 1" type="button" class="text-button" :disabled="saving" @click="removeTarget(index)">移除</button></div>
              <label class="field"><span>指定接收人</span><select v-model="target.receiver" :disabled="saving"><option value="">不指定个人</option><option v-for="employee in employees" :key="employee.user_id" :value="String(employee.user_id)">{{ employee.name }} · {{ employee.group_no }} 组 · {{ employee.post_name || '未设岗位' }}</option></select></label>
              <label class="field"><span>目标组</span><select v-model="target.group" :disabled="saving"><option value="">不指定组</option><option v-for="group in groups" :key="group" :value="String(group)">{{ group }} 组</option></select></label>
            </article>
          </div>
        </section>

        <section v-if="route" class="create-section">
          <div class="create-section-head"><div><h3>菲票与分配</h3><p>已选 {{ selectedTickets.length }} 张 · {{ totalQuantity }} 件；可转量来自来源节点流水，待交接预占由后端复核。</p></div></div>
          <PageState :loading="ticketLoading" :empty="!tickets.length" empty-text="该路线暂无菲票">
            <div class="table-wrap create-ticket-table"><table><thead><tr><th>选择</th><th>菲票</th><th class="numeric">扎数 / 可转</th><th>交接数量</th><th>分配给</th></tr></thead><tbody><tr v-for="ticket in tickets" :key="ticket.id"><td><input v-model="ticket.selected" class="check-input" type="checkbox" :disabled="saving" :aria-label="`选择${ticketLabel(ticket)}`" /></td><td><span class="cell-title">{{ ticketLabel(ticket) }}</span><small class="cell-subtitle">菲票 ID {{ ticket.id }} · {{ ticket.style_no || '—' }}</small></td><td class="numeric">{{ ticket.qty }} / {{ transferable(ticket) ?? '选择来源后读取' }}</td><td><input v-model="ticket.input" type="number" min="1" step="1" :disabled="!ticket.selected || saving" :aria-label="`${ticketLabel(ticket)}交接数量`" /></td><td><select v-model="ticket.target" :disabled="!ticket.selected || saving" :aria-label="`${ticketLabel(ticket)}接收目标`"><option value="">选择目标</option><option v-for="(target, index) in form.targets" :key="index" :value="String(index)">{{ targetName(index) }}</option></select></td></tr></tbody></table></div>
          </PageState>
        </section>
        <label class="field"><span>交接备注（选填）</span><textarea v-model="form.remark" maxlength="255" rows="3" :disabled="saving" placeholder="填写交接说明" /></label>
        <p v-if="error" class="form-error" role="alert">{{ error }}</p>
        <div class="form-actions"><button type="button" class="button secondary" :disabled="saving" @click="emit('close')">取消</button><button class="button primary" :disabled="saving || ticketLoading || !selectedTickets.length"><span v-if="saving" class="spinner light" />{{ saving ? '提交中…' : `确认批量发起（${form.targets.length} 个目标）` }}</button></div>
      </form>
    </PageState>
  </AdminModal>
</template>

<style scoped>
.handover-create{display:grid;gap:20px}.create-section{border-top:1px solid var(--line);padding-top:18px}.create-section-head{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;margin-bottom:14px}.create-section-head h3{font-size:15px;margin:0 0 4px}.create-section-head p{font-size:12px;color:var(--muted);margin:0}.target-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.target-editor{display:grid;grid-template-columns:1fr 1fr;gap:10px;padding:13px;border:1px solid var(--line);border-radius:10px;background:#faf9f7}.target-editor-title{grid-column:1/-1;display:flex;justify-content:space-between;align-items:center}.check-input{width:18px;min-height:18px}.create-ticket-table{border:1px solid var(--line);border-radius:10px;max-height:380px}.create-ticket-table th,.create-ticket-table td{padding:10px 12px}.create-ticket-table input[type=number]{min-width:90px}.create-ticket-table select{min-width:170px}
@media(max-width:760px){.target-grid{grid-template-columns:1fr}.target-editor{grid-template-columns:1fr}.target-editor-title{grid-column:auto}.create-section-head{flex-wrap:wrap}.create-ticket-table table{min-width:720px}}
</style>
