<script setup>
import { computed, inject, reactive, ref, watch } from 'vue'
import { api, createRequestKey, getSession, rows } from '../../api'
import { dateTime } from '../../format'
import AdminModal from '../AdminModal.vue'
import AdminPagination from '../AdminPagination.vue'
import PageState from '../PageState.vue'
import TicketIdentity from './TicketIdentity.vue'
import { useTicketLookup } from './useTicketLookup'

const emit = defineEmits(['toast'])
const flow = inject('manufacturingFlow')
const { hydrate, displayTicket } = useTicketLookup(flow)
const data = ref({ count: 0, results: [] })
const filters = reactive({ status: '', page: 1 })
const loading = ref(true)
const error = ref('')
const employees = ref([])
const modal = ref(false)
const saving = ref(false)
const busy = ref(0)
const formError = ref('')
const form = reactive({ source_node_id: '', target_node_id: '', receiver_id: '', quantity: 1, remark: '', requestId: '' })
const cuttingNodes = computed(() => flow.nodes.value.filter((node) => node.stage_type === 'cutting'))
const targetNodes = computed(() => flow.nodes.value.filter((node) => node.sequence > (flow.nodeFor(form.source_node_id)?.sequence || 0)))
const canCreate = computed(() => flow.can('cutting.manage') && flow.can('manufacturing.view') && flow.route.value && !flow.route.value.is_template)
async function load() {
  loading.value = true
  error.value = ''
  try {
    data.value = await api('/cutting/handovers/', { params: { status: filters.status, ticket_id: flow.ticket.value?.id, page: filters.page, page_size: 20 } })
    await hydrate(rows(data.value))
  } catch (reason) { error.value = reason.message; data.value = { count: 0, results: [] } } finally { loading.value = false }
}
async function openCreate() {
  const source = cuttingNodes.value.find((node) => node.id === Number(flow.state.selectedNodeId)) || cuttingNodes.value[0]
  Object.assign(form, { source_node_id: source ? String(source.id) : '', target_node_id: '', receiver_id: '', quantity: 1, remark: '', requestId: createRequestKey() })
  formError.value = ''
  modal.value = true
  try {
    employees.value = rows(await api('/employees', { params: { tab: 'all', page_size: 100 } }))
    const userId = getSession().user?.id
    if (employees.value.some((employee) => employee.user_id === userId)) form.receiver_id = String(userId)
  } catch (reason) { formError.value = `接收人选项读取失败：${reason.message}` }
}
async function createHandover() {
  formError.value = ''
  if (!flow.ticket.value || !form.source_node_id || !form.target_node_id || !form.receiver_id) { formError.value = '请选择菲票、裁剪节点、目标节点和接收人'; return }
  if (!Number.isInteger(Number(form.quantity)) || Number(form.quantity) <= 0) { formError.value = '交接数量须为正整数'; return }
  saving.value = true
  try {
    await api('/cutting/handovers/', { method: 'POST', body: {
      ticket_id: flow.ticket.value.id, source_node_id: Number(form.source_node_id), target_node_id: Number(form.target_node_id),
      receiver_id: Number(form.receiver_id), quantity: Number(form.quantity), request_id: form.requestId, remark: form.remark.trim(),
    } })
    modal.value = false
    emit('toast', '裁片交接单已创建，等待指定接收人签收')
    await load()
  } catch (reason) {
    const rejected = reason.status >= 400 && reason.status < 500
    if (rejected) form.requestId = createRequestKey()
    formError.value = `${reason.message}；${rejected ? '请求已被拒绝，修正后重试将使用新幂等号' : '网络或服务异常，重试沿用同一幂等号'}`
  } finally { saving.value = false }
}
function canSign(item) { return flow.can('cutting.manage') || Number(getSession().user?.id) === Number(item.receiver) }
async function sign(item) {
  if (!window.confirm(`确认签收 #${item.id} 交接单的 ${item.quantity} 件裁片？签收后将写入来源转出和目标接收流水。`)) return
  busy.value = item.id
  error.value = ''
  try { await api(`/cutting/handovers/${item.id}/sign/`, { method: 'POST', body: {} }); emit('toast', '裁片已签收'); await load() }
  catch (reason) { error.value = reason.message } finally { busy.value = 0 }
}
async function cancel(item) {
  if (!window.confirm(`确认取消待签收交接单 #${item.id}？`)) return
  busy.value = item.id
  error.value = ''
  try { await api(`/cutting/handovers/${item.id}/cancel/`, { method: 'POST', body: {} }); emit('toast', '待签收交接单已取消'); await load() }
  catch (reason) { error.value = reason.message } finally { busy.value = 0 }
}
watch(() => [flow.state.selectedTicketId, filters.status, filters.page], load, { immediate: true })
</script>

<template>
  <section class="card handover-card">
    <div class="card-header"><div><h3>裁片交接签收</h3><p>建单只预占可转数量，指定人签收后才生成生产流转。</p></div><button v-if="canCreate" class="button primary" :disabled="!flow.ticket.value || !cuttingNodes.length" @click="openCreate">新建交接单</button></div>
    <div class="segmented handover-filter"><button :class="{ active: !filters.status }" @click="filters.status = ''; filters.page = 1">全部</button><button :class="{ active: filters.status === 'pending' }" @click="filters.status = 'pending'; filters.page = 1">待签收</button><button :class="{ active: filters.status === 'signed' }" @click="filters.status = 'signed'; filters.page = 1">已签收</button><button :class="{ active: filters.status === 'cancelled' }" @click="filters.status = 'cancelled'; filters.page = 1">已取消</button></div>
    <PageState :loading="loading" :error="error" :empty="!rows(data).length" empty-text="暂无裁片交接单" @retry="load"><div class="table-wrap"><table><thead><tr><th>单号 / 床次 / 菲票</th><th>款号 / 颜色 / 尺码</th><th>裁剪 → 目标节点</th><th class="numeric">数量</th><th>接收人 / 状态</th><th>操作</th></tr></thead><tbody><tr v-for="item in rows(data)" :key="item.id"><td><span class="cell-title">#{{ item.id }} · {{ item.bed_no }}</span><small class="cell-subtitle">菲票 #{{ item.ticket }} · 第{{ item.bundle_no }}扎 · {{ item.ticket_no }}</small></td><td>{{ displayTicket(item).style_no || '—' }}<small class="cell-subtitle">{{ displayTicket(item).color || '—' }} / {{ displayTicket(item).size || '—' }}</small></td><td>{{ item.source_node_name }} → {{ item.target_node_name }}</td><td class="numeric">{{ item.quantity }}</td><td>{{ item.receiver_name }}<small class="cell-subtitle"><span class="badge" :class="item.status === 'signed' ? 'done' : item.status === 'pending' ? 'producing' : 'cancelled'">{{ item.status === 'signed' ? '已签收' : item.status === 'pending' ? '待签收' : '已取消' }}</span> · {{ dateTime(item.signed_at || item.created_at) }}</small></td><td><div class="handover-actions"><button v-if="item.status === 'pending' && canSign(item)" class="button primary small" :disabled="busy === item.id" @click="sign(item)">签收</button><button v-if="flow.can('cutting.manage') && item.status === 'pending'" class="button danger small" :disabled="busy === item.id" @click="cancel(item)">取消</button></div></td></tr></tbody></table></div><AdminPagination :page="filters.page" :count="data.count || 0" :disabled="loading" @change="filters.page = $event" /></PageState>
  </section>

  <AdminModal v-if="modal" title="新建裁片交接单" description="待签收数量会占用来源节点的可转量；签收后不可取消。" :saving="saving" wide @close="modal = false"><form @submit.prevent="createHandover"><TicketIdentity v-if="flow.ticket.value" :ticket="flow.ticket.value" /><div class="form-grid handover-form"><label class="field"><span>裁剪来源节点 *</span><select v-model="form.source_node_id" required @change="form.target_node_id = ''"><option value="">请选择</option><option v-for="node in cuttingNodes" :key="node.id" :value="String(node.id)">{{ node.sequence }}. {{ node.stage_name }}</option></select></label><label class="field"><span>后续目标节点 *</span><select v-model="form.target_node_id" required><option value="">请选择</option><option v-for="node in targetNodes" :key="node.id" :value="String(node.id)">{{ node.sequence }}. {{ node.stage_name }}</option></select></label><label class="field"><span>指定接收人 *</span><select v-model="form.receiver_id" required><option value="">请选择本公司员工</option><option v-for="employee in employees" :key="employee.user_id" :value="String(employee.user_id)">{{ employee.name }} · {{ employee.post_name || '未设岗位' }}</option></select></label><label class="field"><span>交接数量 *</span><input v-model.number="form.quantity" type="number" min="1" step="1" required /></label><label class="field full"><span>备注</span><textarea v-model="form.remark" maxlength="255" rows="3" /></label></div><p v-if="formError" class="form-error">{{ formError }}</p><div class="form-actions"><button type="button" class="button secondary" :disabled="saving" @click="modal = false">取消</button><button class="button primary" :disabled="saving">创建待签收单</button></div></form></AdminModal>
</template>

<style scoped>
.handover-card{padding:0;overflow:hidden}.handover-card>.card-header{padding:20px 20px 0}.handover-filter{margin:0 20px 16px;width:max-content}.handover-actions{display:flex;gap:6px}.handover-form{margin-top:16px}
@media(max-width:760px){.handover-card>.card-header{padding:14px 12px 0}.handover-filter{margin:0 12px 12px;width:auto}.handover-filter button{flex:1;padding-inline:7px;font-size:11px}.handover-card>.card-header .button{padding-inline:12px;font-size:12px}}
</style>
