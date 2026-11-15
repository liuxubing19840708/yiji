<script setup>
import { computed, inject, reactive, ref, watch } from 'vue'
import { api, createRequestKey, rows } from '../../api'
import { dateTime } from '../../format'
import AdminModal from '../AdminModal.vue'
import AdminPagination from '../AdminPagination.vue'
import PageState from '../PageState.vue'
import TicketIdentity from './TicketIdentity.vue'
import { useTicketLookup } from './useTicketLookup'

const emit = defineEmits(['toast'])
const flow = inject('manufacturingFlow')
const { hydrate, displayTicket, ticketFor } = useTicketLookup(flow)
const data = ref({ count: 0, results: [] })
const filters = reactive({ status: '', page: 1 })
const loading = ref(true)
const error = ref('')
const modal = ref(false)
const saving = ref(false)
const formError = ref('')
const busy = ref(0)
const confirmKeys = reactive({})
const form = reactive({ node: '', inspection_type: 'semi_inspection', inspected: 1, qualified: 1, rework: 0, scrapped: 0, rework_node: '', reinspection_of: '', reason: '', createRequestId: createRequestKey() })
const inspectionNodes = computed(() => flow.nodes.value.filter((node) => ['semi_inspection', 'final_inspection'].includes(node.stage_type)))
const reworkNodes = computed(() => flow.nodes.value.filter((node) => node.sequence < (flow.nodeFor(form.node)?.sequence || 0)))
const previousInspections = ref([])
const canCreate = computed(() => flow.can('quality.manage') && flow.can('manufacturing.view') && flow.route.value && !flow.route.value.is_template)
async function load() {
  loading.value = true
  error.value = ''
  try {
    data.value = await api('/quality/inspections', { params: { status: filters.status, ticket_id: flow.ticket.value?.id, page: filters.page, page_size: 20 } })
    await hydrate(rows(data.value))
  } catch (reason) { error.value = reason.message; data.value = { count: 0, results: [] } } finally { loading.value = false }
}
async function loadPreviousInspections() {
  previousInspections.value = []
  if (!flow.ticket.value || !form.node) return
  try {
    const previous = await api('/quality/inspections', { params: { status: 'confirmed', ticket_id: flow.ticket.value.id, node_id: Number(form.node), page_size: 100 } })
    previousInspections.value = rows(previous).filter((item) => item.rework > 0)
  } catch (reason) { formError.value = `原检验单读取失败：${reason.message}` }
}
function nodeChanged() {
  if (form.inspection_type !== 'reinspection') form.inspection_type = flow.nodeFor(form.node)?.stage_type || 'semi_inspection'
  form.rework_node = ''
  form.reinspection_of = ''
  loadPreviousInspections()
}
function openCreate() {
  const first = inspectionNodes.value.find((node) => node.id === Number(flow.state.selectedNodeId)) || inspectionNodes.value[0]
  Object.assign(form, { node: first ? String(first.id) : '', inspection_type: first?.stage_type || 'semi_inspection', inspected: 1, qualified: 1, rework: 0, scrapped: 0, rework_node: '', reinspection_of: '', reason: '', createRequestId: createRequestKey() })
  formError.value = ''
  modal.value = true
  loadPreviousInspections()
}
async function createInspection() {
  formError.value = ''
  const values = ['inspected', 'qualified', 'rework', 'scrapped'].map((key) => Number(form[key]))
  if (!flow.ticket.value || !form.node) { formError.value = '请选择实例路线、菲票和检验节点'; return }
  if (values.some((value) => !Number.isInteger(value) || value < 0) || values[0] <= 0) { formError.value = '检验数量须为正整数，其余数量须为非负整数'; return }
  if (values[0] !== values[1] + values[2] + values[3]) { formError.value = '检验数必须等于合格 + 返修 + 报废'; return }
  if (form.rework > 0 && !form.rework_node) { formError.value = '有返修数量时必须选择前序返修节点'; return }
  if ((form.rework > 0 || form.scrapped > 0) && !form.reason.trim()) { formError.value = '返修或报废时必须填写原因'; return }
  if (form.inspection_type === 'reinspection' && !form.reinspection_of) { formError.value = '二次复检必须选择已确认的原检验单'; return }
  saving.value = true
  try {
    await api('/quality/inspections', { method: 'POST', body: {
      ticket: flow.ticket.value.id, node: Number(form.node), inspection_type: form.inspection_type,
      inspected: values[0], qualified: values[1], rework: values[2], scrapped: values[3],
      rework_node: form.rework > 0 ? Number(form.rework_node) : null,
      reinspection_of: form.inspection_type === 'reinspection' ? Number(form.reinspection_of) : null,
      reason: form.reason.trim(), create_request_id: form.createRequestId,
    } })
    modal.value = false
    emit('toast', '节点检验单已创建，请核对后确认')
    await load()
  } catch (reason) {
    if (reason.status >= 400 && reason.status < 500) form.createRequestId = createRequestKey()
    formError.value = reason.message
  } finally { saving.value = false }
}
async function confirmInspection(inspection) {
  if (!window.confirm(`确认检验单 #${inspection.id}？确认后将按合格、返修、报废数量写入生产流水。`)) return
  busy.value = inspection.id
  error.value = ''
  confirmKeys[inspection.id] ||= createRequestKey()
  try {
    await api(`/quality/inspections/${inspection.id}/confirm`, { method: 'POST', body: { request_id: confirmKeys[inspection.id] } })
    delete confirmKeys[inspection.id]
    emit('toast', '检验结果已确认并写入数量流水')
    await load()
  } catch (reason) {
    const rejected = reason.status >= 400 && reason.status < 500
    if (rejected) confirmKeys[inspection.id] = createRequestKey()
    error.value = `${reason.message}；${rejected ? '请求已被拒绝，修正后再次确认将使用新幂等号' : '网络或服务异常，再次确认将沿用同一幂等号'}`
  } finally { busy.value = 0 }
}
watch(() => [flow.state.selectedTicketId, filters.status, filters.page], load, { immediate: true })
</script>

<template>
  <section class="card inspection-card">
    <div class="card-header"><div><h3>节点质量检验</h3><p>先登记检验事实，再确认写入合格、返修和报废流水。</p></div><button v-if="canCreate" class="button primary" :disabled="!flow.ticket.value || !inspectionNodes.length" @click="openCreate">登记检验</button></div>
    <div class="inspection-filter segmented"><button :class="{ active: !filters.status }" @click="filters.status = ''; filters.page = 1">全部</button><button :class="{ active: filters.status === 'pending' }" @click="filters.status = 'pending'; filters.page = 1">待确认</button><button :class="{ active: filters.status === 'confirmed' }" @click="filters.status = 'confirmed'; filters.page = 1">已确认</button></div>
    <PageState :loading="loading" :error="error" :empty="!rows(data).length" empty-text="当前筛选下暂无节点检验单" @retry="load">
      <div class="table-wrap"><table><thead><tr><th>床次 / 菲票 / 款色码</th><th>检验节点 / 类型</th><th class="numeric">检验 / 合格 / 返修 / 报废</th><th>状态 / 检验人</th><th>原因与操作</th></tr></thead><tbody><tr v-for="item in rows(data)" :key="item.id"><td><span class="cell-title">{{ displayTicket(item).bed_no }} · #{{ item.ticket }} · 第{{ item.ticket_bundle_no }}扎</span><small class="cell-subtitle">{{ displayTicket(item).style_no || '—' }} · {{ displayTicket(item).color || '—' }} / {{ displayTicket(item).size || '—' }}</small></td><td>{{ item.node_sequence }}. {{ item.node_name }}<small class="cell-subtitle">{{ item.inspection_type_display }}<template v-if="item.rework_node_name"> · 返至 {{ item.rework_node_name }}</template></small></td><td class="numeric">{{ item.inspected }} / {{ item.qualified }} / {{ item.rework }} / {{ item.scrapped }}</td><td><span class="badge" :class="item.status === 'confirmed' ? 'done' : 'producing'">{{ item.status_display }}</span><small class="cell-subtitle">{{ item.inspector_name || '—' }} · {{ dateTime(item.inspected_at) }}</small></td><td><span class="inspection-reason">{{ item.reason || '—' }}</span><button v-if="flow.can('quality.manage') && item.status === 'pending'" class="button primary small" :disabled="busy === item.id" @click="confirmInspection(item)">确认并记账</button></td></tr></tbody></table></div>
      <AdminPagination :page="filters.page" :count="data.count || 0" :disabled="loading" @change="filters.page = $event" />
    </PageState>
    <p v-if="flow.can('quality.manage') && !flow.can('manufacturing.view')" class="section-note">登记检验还需要 manufacturing.view 权限以读取路线节点；已有检验单仍可查看。</p>
  </section>

  <AdminModal v-if="modal" title="登记节点检验" description="检验单保存后为待确认状态，此步骤尚不修改数量余额。" :saving="saving" wide @close="modal = false">
    <form @submit.prevent="createInspection"><TicketIdentity v-if="flow.ticket.value" :ticket="flow.ticket.value" /><div class="form-grid inspection-form"><label class="field"><span>检验节点 *</span><select v-model="form.node" required @change="nodeChanged"><option value="">请选择</option><option v-for="node in inspectionNodes" :key="node.id" :value="String(node.id)">{{ node.sequence }}. {{ node.stage_name }}</option></select></label><label class="field"><span>检验类型 *</span><select v-model="form.inspection_type"><option value="semi_inspection" :disabled="flow.nodeFor(form.node)?.stage_type !== 'semi_inspection'">半成品检验</option><option value="final_inspection" :disabled="flow.nodeFor(form.node)?.stage_type !== 'final_inspection'">成品尾查</option><option value="reinspection">返修后二次复检</option></select></label><label v-if="form.inspection_type === 'reinspection'" class="field full"><span>原检验单 *</span><select v-model="form.reinspection_of" required><option value="">请选择同票同节点的已确认返修单</option><option v-for="item in previousInspections" :key="item.id" :value="item.id">#{{ item.id }} · 返修 {{ item.rework }} 件 · {{ dateTime(item.inspected_at) }}</option></select></label><label class="field"><span>检验数量 *</span><input v-model.number="form.inspected" type="number" min="1" step="1" required /></label><label class="field"><span>合格数量 *</span><input v-model.number="form.qualified" type="number" min="0" step="1" required /></label><label class="field"><span>返修数量 *</span><input v-model.number="form.rework" type="number" min="0" step="1" required /></label><label class="field"><span>报废数量 *</span><input v-model.number="form.scrapped" type="number" min="0" step="1" required /></label><label v-if="form.rework > 0" class="field full"><span>返修目标节点（前序）*</span><select v-model="form.rework_node" required><option value="">请选择</option><option v-for="node in reworkNodes" :key="node.id" :value="String(node.id)">{{ node.sequence }}. {{ node.stage_name }}</option></select></label><label class="field full"><span>原因 / 备注</span><textarea v-model="form.reason" maxlength="255" rows="3" :required="form.rework > 0 || form.scrapped > 0" /></label></div><p class="section-note">数量校验：{{ form.inspected || 0 }} = {{ form.qualified || 0 }} + {{ form.rework || 0 }} + {{ form.scrapped || 0 }}</p><p v-if="formError" class="form-error">{{ formError }}</p><div class="form-actions"><button type="button" class="button secondary" :disabled="saving" @click="modal = false">取消</button><button class="button primary" :disabled="saving">保存待确认检验单</button></div></form>
  </AdminModal>
</template>

<style scoped>
.inspection-card{padding:0;overflow:hidden}.inspection-card>.card-header{padding:20px 20px 0}.inspection-filter{margin:0 20px 16px;width:max-content}.inspection-reason{display:block;max-width:240px;margin-bottom:8px;white-space:normal;font-size:12px}.inspection-form{margin-top:16px}.inspection-card :deep(.pagination){border-top:1px solid var(--line)}
@media(max-width:760px){.inspection-card>.card-header{padding:14px 12px 0}.inspection-filter{margin:0 12px 12px;width:auto}.inspection-filter button{flex:1;padding-inline:8px}.inspection-card>.card-header .button{padding-inline:12px;font-size:12px}}
</style>
