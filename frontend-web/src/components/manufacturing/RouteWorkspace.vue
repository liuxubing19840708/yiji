<script setup>
import { computed, inject, reactive, ref, watch } from 'vue'
import { api, createRequestKey, rows } from '../../api'
import { dateTime } from '../../format'
import AppIcon from '../AppIcon.vue'
import AdminModal from '../AdminModal.vue'
import PageState from '../PageState.vue'
import RouteTemplateDialog from './RouteTemplateDialog.vue'
import RouteInstanceDialog from './RouteInstanceDialog.vue'

const emit = defineEmits(['toast'])
const flow = inject('manufacturingFlow')
const balances = ref([])
const balanceLoading = ref(false)
const balanceError = ref('')
const ledger = ref({ count: 0, results: [], truncated: false })
const saving = ref(false)
const actionError = ref('')
const modal = ref('')
const templateMode = ref('create')
const stageSaving = ref(false)
const reverseKeys = reactive({})
const stageError = ref('')
const stageForm = reactive({ id: 0, code: '', name: '', stage_type: 'sewing', in_use: false })
const activeTemplates = computed(() => flow.state.routes.filter((route) => route.is_template && route.is_active !== false))
const form = reactive({ action: 'complete', toNodeId: '', quantity: 1, source: 'web-manufacturing', reason: '', requestId: createRequestKey() })
const actions = [['receive', '首节点接收'], ['complete', '完工判合格'], ['transfer', '合格转出'], ['return', '返修退回'], ['scrap', '报废']]
const eventNames = { received: '接收', qualified: '合格', rework: '返修入站', scrapped: '报废', transferred: '转出', reversal: '冲销' }
const stageTypes = { cutting: '裁剪', sewing: '车缝', semi_inspection: '半成品检验', special_machine: '专机', ironing: '整烫', final_inspection: '成品检验', packing: '包装', finished_inbound: '成品入库', other: '其他' }
const selectedBalance = computed(() => balances.value.find((row) => row.node_id === Number(flow.state.selectedNodeId))?.balance || {})
const selectedNode = computed(() => flow.nodeFor(flow.state.selectedNodeId))
const allowedActions = computed(() => {
  const type = selectedNode.value?.stage_type
  if (type === 'finished_inbound') return []
  return actions.filter(([action]) => {
    if (['semi_inspection', 'final_inspection'].includes(type) && ['complete', 'return', 'scrap'].includes(action)) return false
    if (type === 'packing' && ['complete', 'transfer'].includes(action)) return false
    return true
  })
})
const dedicatedHint = computed(() => {
  const type = selectedNode.value?.stage_type
  if (['semi_inspection', 'final_inspection'].includes(type)) return '检验节点的完工、返修和报废必须在“节点质检”登记并确认，通用动作仅保留接收与合格转出。'
  if (type === 'packing') return '包装完工必须经“包装入库”审核；从包装节点转出也必须通过成品入库审核。'
  if (type === 'finished_inbound') return '成品入库节点不开放通用动作，请通过“包装入库”审核入库单。'
  return ''
})
const targets = computed(() => flow.nodes.value.filter((node) => {
  if (form.action === 'transfer' && node.stage_type === 'finished_inbound') return false
  return form.action === 'return' ? node.sequence < (selectedNode.value?.sequence || 0) : node.sequence > (selectedNode.value?.sequence || 0)
}))
const maxQuantity = computed(() => {
  if (form.action === 'receive') return Math.max(0, Number(flow.ticket.value?.qty || 0) - balances.value.reduce((sum, row) => sum + Number(row.balance.available || 0), 0))
  if (form.action === 'complete') return Number(selectedBalance.value.qualification_remaining || 0)
  if (form.action === 'transfer') return Number(selectedBalance.value.transferable || 0)
  if (form.action === 'return') return Number(selectedBalance.value.available || 0)
  if (form.action === 'scrap') return Math.min(Number(selectedBalance.value.available || 0), Number(selectedBalance.value.qualification_remaining || 0))
  return 0
})
function balanceFor(nodeId) { return balances.value.find((row) => row.node_id === nodeId)?.balance || {} }
function actionName(action) { return actions.find((item) => item[0] === action)?.[1] || action }
function resetRequest() { form.requestId = createRequestKey() }
function actionChanged() {
  form.toNodeId = ''
  form.quantity = 1
  if (form.action === 'receive' && flow.nodes.value.length) flow.state.selectedNodeId = String(flow.nodes.value[0].id)
  resetRequest()
}
async function loadBalances() {
  balances.value = []
  balanceError.value = ''
  if (!flow.route.value || flow.route.value.is_template || !flow.ticket.value) return
  balanceLoading.value = true
  try { balances.value = await api(`/manufacturing/routes/${flow.route.value.id}/balances`, { params: { ticket_id: flow.ticket.value.id } }) }
  catch (error) { balanceError.value = error.message } finally { balanceLoading.value = false }
}
async function loadLedger() {
  if (!flow.route.value || flow.route.value.is_template || !flow.ticket.value) {
    ledger.value = { count: 0, results: [], truncated: false }
    return
  }
  try {
    const timeline = await api(`/manufacturing/routes/${flow.route.value.id}/timeline`, {
      params: { ticket_id: flow.ticket.value.id },
    })
    ledger.value = { count: timeline.total, results: timeline.events, truncated: timeline.truncated }
  } catch (error) { balanceError.value = error.message }
}
function refreshData() { return Promise.all([loadBalances(), loadLedger()]) }
async function submitAction() {
  actionError.value = ''
  const nodeId = Number(flow.state.selectedNodeId)
  const quantity = Number(form.quantity)
  if (!flow.ticket.value || !nodeId) { actionError.value = '请先选择实例路线、菲票和节点'; return }
  if (!allowedActions.value.some(([action]) => action === form.action)) { actionError.value = dedicatedHint.value || '当前节点不允许该通用动作'; return }
  if (form.action === 'transfer' && flow.nodeFor(form.toNodeId)?.stage_type === 'finished_inbound') { actionError.value = '转入成品入库必须通过“包装入库”审核，不允许通用转出'; return }
  if (!Number.isInteger(quantity) || quantity <= 0 || quantity > maxQuantity.value) { actionError.value = `数量须为 1~${maxQuantity.value} 的整数`; return }
  if (['transfer', 'return'].includes(form.action) && !form.toNodeId) { actionError.value = '请选择目标节点'; return }
  saving.value = true
  try {
    await api(`/manufacturing/actions/${form.action}`, { method: 'POST', body: {
      ticket_id: flow.ticket.value.id, node_id: nodeId,
      to_node_id: form.toNodeId ? Number(form.toNodeId) : undefined,
      quantity, request_id: form.requestId, source: form.source.trim(), reason: form.reason.trim(),
    } })
    emit('toast', `${actionName(form.action)}已登记 ${quantity} 件`)
    form.reason = ''
    form.quantity = 1
    resetRequest()
    await refreshData()
  } catch (error) {
    const rejected = error.status >= 400 && error.status < 500
    if (rejected) resetRequest()
    actionError.value = `${error.message}；${rejected ? '本次请求已被拒绝，修正后重试将使用新幂等号' : '网络或服务异常，可核对后重试，本次幂等号保持不变'}`
  } finally { saving.value = false }
}
async function reverse(entry) {
  const reason = window.prompt(`冲销流水 #${entry.id}（${eventNames[entry.event_type]} ${entry.quantity} 件）的原因：`)
  if (reason === null) return
  if (!reason.trim()) { balanceError.value = '冲销原因不能为空'; return }
  if (!window.confirm('冲销会新增一条不可变的反向流水，确认继续？')) return
  balanceError.value = ''
  reverseKeys[entry.id] ||= createRequestKey()
  try {
    await api(`/manufacturing/ledger/${entry.id}/reverse`, { method: 'POST', body: { request_id: reverseKeys[entry.id], reason: reason.trim() } })
    delete reverseKeys[entry.id]
    emit('toast', '数量流水已冲销')
    await refreshData()
  } catch (error) {
    const rejected = error.status >= 400 && error.status < 500
    if (rejected) reverseKeys[entry.id] = createRequestKey()
    balanceError.value = `${error.message}；${rejected ? '再次冲销将使用新幂等号' : '网络或服务异常可沿用原幂等号重试'}`
  }
}
function canReverse(entry) {
  return flow.can('manufacturing.reverse') && entry.event_type !== 'reversal'
    && !ledger.value.results.some((item) => item.reversal_of === entry.id)
    && !/^(quality_inspection:|cutting_handover:|finished_goods\.|shipment:)/.test(entry.source || '')
}
async function createStage() {
  stageError.value = ''
  if (!stageForm.code.trim() || !stageForm.name.trim()) { stageError.value = '节点编码和名称不能为空'; return }
  stageSaving.value = true
  try {
    const body = { code: stageForm.code.trim(), name: stageForm.name.trim(), stage_type: stageForm.stage_type }
    await api(stageForm.id ? `/manufacturing/stages/${stageForm.id}` : '/manufacturing/stages', { method: stageForm.id ? 'PUT' : 'POST', body })
    flow.state.stages = rows(await api('/manufacturing/stages'))
    resetStageForm()
    await flow.reloadRoutes()
    emit('toast', '标准生产节点已保存')
  } catch (error) { stageError.value = error.message } finally { stageSaving.value = false }
}
function resetStageForm() { Object.assign(stageForm, { id: 0, code: '', name: '', stage_type: 'sewing', in_use: false }) }
function editStage(stage) { Object.assign(stageForm, { id: stage.id, code: stage.code, name: stage.name, stage_type: stage.stage_type, in_use: !!stage.in_use }) }
async function toggleStage(stage) {
  stageError.value = ''
  stageSaving.value = true
  try {
    await api(`/manufacturing/stages/${stage.id}/status`, { method: 'POST', body: { is_active: stage.is_active === false } })
    flow.state.stages = rows(await api('/manufacturing/stages'))
    await flow.reloadRoutes()
    if (stageForm.id === stage.id) resetStageForm()
    emit('toast', stage.is_active === false ? '标准阶段已启用' : '标准阶段已停用')
  } catch (error) { stageError.value = error.message } finally { stageSaving.value = false }
}
function openTemplate(mode) { templateMode.value = mode; modal.value = 'template' }
async function toggleTemplate(route) {
  try {
    await api(`/manufacturing/routes/${route.id}/status`, { method: 'POST', body: { is_active: route.is_active === false } })
    await flow.reloadRoutes(route.id)
    emit('toast', route.is_active === false ? '路线模板已启用' : '路线模板已停用')
  } catch (error) { emit('toast', error.message) }
}
async function routeSaved(route, message) {
  modal.value = ''
  await flow.reloadRoutes(route.id)
  emit('toast', message)
}
watch(() => [flow.state.selectedRouteId, flow.state.selectedTicketId], () => { resetRequest(); refreshData() }, { immediate: true })
watch(() => [flow.state.selectedNodeId, form.toNodeId, form.quantity, form.source, form.reason], resetRequest)
watch(allowedActions, (available) => {
  if (!available.some(([action]) => action === form.action)) {
    const next = available.find(([action]) => action !== 'receive') || available[0]
    form.action = next?.[0] || ''
    form.toNodeId = ''
    form.quantity = 1
    resetRequest()
  }
})
</script>

<template>
  <div class="route-workspace">
    <section class="card route-card">
      <div class="card-header"><div><h3>路线与节点</h3><p>{{ flow.route.value ? `${flow.route.value.code} · ${flow.route.value.is_template ? `V${flow.route.value.version || 1} · ` : ''}${flow.route.value.name} · ${flow.routeAnchor(flow.route.value)}` : '尚未选择生产路线' }}</p></div><div v-if="flow.can('manufacturing.manage')" class="route-admin-actions"><button class="button secondary small" @click="modal = 'stage'">标准阶段</button><button class="button secondary small" @click="openTemplate('create')">新建模板</button><template v-if="flow.route.value?.is_template"><button class="button secondary small" @click="openTemplate('edit')">编辑模板</button><button class="button secondary small" @click="openTemplate('copy')">复制</button><button class="button secondary small" @click="openTemplate('version')">新版本</button><button class="button secondary small" @click="toggleTemplate(flow.route.value)">{{ flow.route.value.is_active === false ? '启用模板' : '停用模板' }}</button></template><button v-if="activeTemplates.length" class="button primary small" @click="modal = 'instance'">{{ flow.route.value?.is_template ? '实例化当前模板' : '从模板新建实例' }}</button></div></div>
      <div v-if="flow.route.value?.is_template" class="template-meta"><span class="badge" :class="flow.route.value.is_active === false ? 'cancelled' : 'producing'">{{ flow.route.value.is_active === false ? '已停用' : '启用中' }}</span><span>已有 {{ flow.route.value.instance_count || 0 }} 个实例</span><span v-if="flow.route.value.previous_version">上一版本 #{{ flow.route.value.previous_version }}</span></div>
      <div v-if="flow.route.value?.is_template && flow.route.value.configuration_warnings?.length" class="configuration-warnings" role="alert"><strong>配置提醒</strong><ul><li v-for="warning in flow.route.value.configuration_warnings" :key="warning">{{ warning }}</li></ul></div>
      <PageState :loading="flow.state.loading" :error="flow.state.baseError" :empty="!flow.route.value" empty-text="暂无生产路线，请先创建模板并实例化" @retry="flow.reloadRoutes">
        <div class="route-node-list"><button v-for="node in flow.nodes.value" :key="node.id" :class="{ active: Number(flow.state.selectedNodeId) === node.id }" @click="flow.state.selectedNodeId = String(node.id)"><span>{{ node.sequence }}</span><strong>{{ node.stage_name }}</strong><small>{{ stageTypes[node.stage_type] || node.stage_type }}<template v-if="node.allow_parallel"> · 并行</template><template v-if="node.allow_skip"> · 可跳过</template></small></button></div>
      </PageState>
      <p v-if="flow.route.value?.is_template" class="section-note">模板只用于配置，不可直接报工。点击“实例化当前模板”绑定床次或订单。</p>
    </section>

    <template v-if="flow.route.value && !flow.route.value.is_template">
      <section class="card balance-card"><div class="card-header"><div><h3>所选菲票的节点余额</h3><p>在手 = 入站 − 转出 − 报废；可转量还受合格里程碑约束。</p></div><button class="button ghost small" @click="refreshData"><AppIcon name="refresh" :size="14" />刷新</button></div><PageState :loading="balanceLoading" :error="balanceError" :empty="!flow.ticket.value" empty-text="请选择菲票后查看节点余额" @retry="refreshData"><div class="table-wrap"><table><thead><tr><th>节点</th><th class="numeric">入站</th><th class="numeric">合格</th><th class="numeric">待判定</th><th class="numeric">在手</th><th class="numeric">可转出</th><th class="numeric">报废 / 转出</th></tr></thead><tbody><tr v-for="node in flow.nodes.value" :key="node.id" :class="{ 'selected-balance': Number(flow.state.selectedNodeId) === node.id }"><td><span class="cell-title">{{ node.sequence }}. {{ node.stage_name }}</span></td><td class="numeric">{{ balanceFor(node.id).inputs || 0 }}</td><td class="numeric">{{ balanceFor(node.id).qualified || 0 }}</td><td class="numeric">{{ balanceFor(node.id).qualification_remaining || 0 }}</td><td class="numeric balance-strong">{{ balanceFor(node.id).available || 0 }}</td><td class="numeric balance-strong">{{ balanceFor(node.id).transferable || 0 }}</td><td class="numeric">{{ balanceFor(node.id).scrapped || 0 }} / {{ balanceFor(node.id).transferred || 0 }}</td></tr></tbody></table></div></PageState></section>

      <section v-if="flow.can('manufacturing.manage')" class="card action-card"><div class="card-header"><div><h3>受控动作报工</h3><p>只开放后端允许的五种节点动作；动作、数量和目标均由当前余额约束。</p></div><span class="badge producing">安全 UUID 幂等</span></div><p v-if="dedicatedHint" class="section-note dedicated-hint">{{ dedicatedHint }}</p><form @submit.prevent="submitAction"><div class="action-form-grid"><label class="field"><span>动作 *</span><select v-model="form.action" @change="actionChanged"><option v-for="action in allowedActions" :key="action[0]" :value="action[0]">{{ action[1] }}</option></select></label><label class="field"><span>来源节点 *</span><select v-model="flow.state.selectedNodeId" :disabled="form.action === 'receive'"><option v-for="node in flow.nodes.value" :key="node.id" :value="String(node.id)" :disabled="form.action === 'receive' && node.sequence !== flow.nodes.value[0]?.sequence">{{ node.sequence }}. {{ node.stage_name }}</option></select></label><label v-if="['transfer', 'return'].includes(form.action)" class="field"><span>目标节点 *</span><select v-model="form.toNodeId" required><option value="">请选择{{ form.action === 'return' ? '前序' : '后续' }}节点</option><option v-for="node in targets" :key="node.id" :value="String(node.id)">{{ node.sequence }}. {{ node.stage_name }}</option></select></label><label class="field"><span>数量（当前上限 {{ maxQuantity }}）*</span><input v-model.number="form.quantity" type="number" min="1" :max="Math.max(maxQuantity, 1)" step="1" required /></label><label class="field"><span>来源标识</span><input v-model="form.source" maxlength="128" /></label><label class="field action-reason"><span>原因 / 备注</span><input v-model="form.reason" maxlength="255" :required="form.action === 'scrap'" placeholder="报废时请填写原因" /></label></div><p v-if="actionError" class="form-error" role="alert">{{ actionError }}</p><div class="form-actions"><button class="button primary" :disabled="saving || !flow.ticket.value || !maxQuantity || !allowedActions.length"><span v-if="saving" class="spinner light" />提交{{ actionName(form.action) }}</button></div></form></section>

      <section class="card ledger-card"><div class="card-header"><div><h3>统一数量时间线</h3><p>生产、质检、裁片、包装入库与出货共用同一事实流水；业务单证流水须从原单证处理。</p></div></div><div class="table-wrap"><table><thead><tr><th>流水 / 时间</th><th>节点 / 事件</th><th class="numeric">数量</th><th>颜色 / 尺码</th><th>来源 / 原因</th><th v-if="flow.can('manufacturing.reverse')"></th></tr></thead><tbody><tr v-for="entry in rows(ledger)" :key="entry.id"><td><span class="cell-title">#{{ entry.id }}</span><small class="cell-subtitle">{{ dateTime(entry.created_at) }}</small></td><td>{{ entry.node_name || flow.nodeFor(entry.node)?.stage_name || `节点 #${entry.node}` }}<small class="cell-subtitle">{{ entry.event_type_display || eventNames[entry.event_type] || entry.event_type }}<template v-if="entry.reversal_of"> · 原流水 #{{ entry.reversal_of }}</template></small></td><td class="numeric">{{ entry.quantity }}</td><td>{{ entry.color || flow.ticket.value?.color }} / {{ entry.size || flow.ticket.value?.size }}</td><td><span class="ledger-source">{{ entry.source || '—' }}</span><small class="cell-subtitle">{{ entry.reason || '—' }}</small></td><td v-if="flow.can('manufacturing.reverse')"><button v-if="canReverse(entry)" class="button danger small" @click="reverse(entry)">冲销</button></td></tr></tbody></table></div><p v-if="!rows(ledger).length" class="page-state">当前菲票暂无数量流水</p><p v-if="ledger.truncated" class="section-note">共 {{ ledger.count }} 条，当前按时间顺序展示前 500 条。</p></section>
    </template>

    <AdminModal v-if="modal === 'stage'" title="标准生产阶段" description="已使用阶段保留历史保护；停用不会修改既有路线和流水。" :saving="stageSaving" wide @close="modal = ''; resetStageForm()"><form @submit.prevent="createStage"><div class="card-header"><h3>{{ stageForm.id ? '编辑阶段' : '新建阶段' }}</h3><button v-if="stageForm.id" type="button" class="button ghost small" @click="resetStageForm">新建阶段</button></div><div class="form-grid"><label class="field"><span>阶段编码 *</span><input v-model="stageForm.code" maxlength="32" required :disabled="stageForm.in_use" /></label><label class="field"><span>阶段名称 *</span><input v-model="stageForm.name" maxlength="64" required /></label><label class="field full"><span>阶段类型 *</span><select v-model="stageForm.stage_type" :disabled="stageForm.in_use"><option v-for="(label, key) in stageTypes" :key="key" :value="key">{{ label }}</option></select></label></div><p v-if="stageForm.in_use" class="section-note">此阶段已被路线引用，编码和类型不可修改；历史节点保留名称和类型快照。</p><p v-if="stageError" class="form-error">{{ stageError }}</p><div class="form-actions"><button type="button" class="button secondary" @click="modal = ''; resetStageForm()">关闭</button><button class="button primary" :disabled="stageSaving">保存阶段</button></div></form><div class="table-wrap stage-list"><table><thead><tr><th>编码 / 名称</th><th>类型</th><th>状态</th><th>操作</th></tr></thead><tbody><tr v-for="stage in flow.state.stages" :key="stage.id"><td><span class="cell-title">{{ stage.code }} · {{ stage.name }}</span><small v-if="stage.in_use" class="cell-subtitle">已被路线引用，编码与类型受保护</small></td><td>{{ stageTypes[stage.stage_type] || stage.stage_type }}</td><td>{{ stage.is_active === false ? '已停用' : '启用中' }}</td><td><div class="route-admin-actions"><button class="button secondary small" @click="editStage(stage)">编辑</button><button class="button secondary small" :disabled="stageSaving" @click="toggleStage(stage)">{{ stage.is_active === false ? '启用' : '停用' }}</button></div></td></tr></tbody></table></div></AdminModal>
    <RouteTemplateDialog v-if="modal === 'template'" :stages="flow.state.stages" :template="flow.route.value?.is_template ? flow.route.value : null" :mode="templateMode" @close="modal = ''" @saved="routeSaved($event, templateMode === 'version' ? '路线新版本已创建' : templateMode === 'copy' ? '独立模板副本已创建' : '路线模板已保存')" />
    <RouteInstanceDialog v-if="modal === 'instance'" :template="flow.route.value?.is_template && flow.route.value.is_active !== false ? flow.route.value : activeTemplates[0]" :productions="flow.state.productions" :orders="flow.state.orders" @close="modal = ''" @saved="routeSaved($event, '生产路线已实例化')" />
  </div>
</template>

<style scoped>
.route-workspace{display:grid;gap:16px}.route-admin-actions{display:flex;gap:7px;flex-wrap:wrap;justify-content:flex-end}.route-node-list{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:8px}.route-node-list button{position:relative;display:flex;flex-direction:column;align-items:flex-start;gap:4px;min-width:0;border:1px solid var(--line);border-radius:10px;background:#FAFBFC;padding:13px;text-align:left;color:var(--ink)}.route-node-list button.active{border-color:var(--orange);background:var(--orange-soft);box-shadow:inset 0 0 0 1px var(--orange)}.route-node-list button>span{position:absolute;right:9px;top:9px;color:var(--muted);font-size:11px}.route-node-list strong{font-size:13px}.route-node-list small{color:var(--muted);font-size:10px}.balance-card,.ledger-card{padding:0;overflow:hidden}.balance-card .card-header,.ledger-card .card-header{padding:18px 20px 0}.balance-card :deep(.page-state){min-height:120px}.selected-balance{background:#FFFAF5}.balance-strong{color:var(--orange-dark);font-weight:650}.action-form-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}.action-reason{grid-column:span 2}.ledger-source{display:block;max-width:230px;overflow-wrap:anywhere;font-size:12px}.action-card .form-error{margin-top:14px}.template-meta{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-top:12px;color:var(--text-secondary);font-size:12px}.configuration-warnings{margin-top:12px;padding:12px 14px;border:1px solid #F2C48D;border-radius:9px;background:#FFF8ED;color:#855313;font-size:12px}.configuration-warnings ul{margin:6px 0 0;padding-left:20px}.configuration-warnings li+li{margin-top:4px}.stage-list{margin-top:22px}.stage-list td:last-child{min-width:130px}
@media(max-width:900px){.action-form-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:760px){.route-card .card-header{align-items:flex-start;flex-direction:column}.route-admin-actions{justify-content:flex-start}.route-node-list{display:flex;overflow-x:auto;padding-bottom:4px}.route-node-list button{min-width:125px}.action-form-grid{grid-template-columns:1fr}.action-reason{grid-column:auto}.balance-card .card-header,.ledger-card .card-header{padding:14px 12px}.balance-card .card-header>p{display:none}}
</style>
