<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { api, rows } from '../api'
import { fetchHandoverPolicies, saveHandoverPolicy } from '../handover'
import AdminModal from './AdminModal.vue'
import PageState from './PageState.vue'

const emit = defineEmits(['close', 'toast'])
const loading = ref(true)
const saving = ref(false)
const error = ref('')
const policies = ref([])
const selectedIndex = ref(0)
const routes = ref([])
const employees = ref([])
const form = reactive({ mode: 'off', sourceNode: '', targetNode: '', group: '', receiver: '', timeout: '120' })
const current = computed(() => policies.value[selectedIndex.value] || null)
const groups = computed(() => [...new Set(employees.value.map((item) => Number(item.group_no)).filter((value) => value > 0))].sort((a, b) => a - b))
const nodeOptions = computed(() => routes.value.flatMap((route) => (route.nodes || []).map((node) => ({ ...node, routeName: route.name || route.code }))))
const modes = [
  ['off', '关闭', '不强制交接，不生成待接收超时提醒。'],
  ['warn', '提醒（不阻断）', '超时提醒但不阻断普通裁片转出。'],
  ['strict', '严格（必须经过交接）', '阻断绕过交接的普通裁片转出；实际交接接收仍可完成。'],
]
function nodeName(id) {
  const node = nodeOptions.value.find((item) => item.id === Number(id))
  return node ? `${node.routeName} / ${node.stage_name}` : `节点 ${id}`
}
function policyLabel(policy) {
  if (!policy.source_node && !policy.target_node) return '公司默认'
  return `${policy.source_node ? nodeName(policy.source_node) : '全部交出节点'} → ${policy.target_node ? nodeName(policy.target_node) : '全部接收节点'}`
}
function apply(policy) {
  Object.assign(form, {
    mode: policy.mode, sourceNode: policy.source_node ? String(policy.source_node) : '',
    targetNode: policy.target_node ? String(policy.target_node) : '',
    group: policy.target_group_no ? String(policy.target_group_no) : '',
    receiver: policy.receiver ? String(policy.receiver) : '',
    timeout: String(policy.timeout_minutes || 120),
  })
}
async function load(preferredId = current.value?.id) {
  loading.value = true
  error.value = ''
  try {
    const [policyData, routeData, employeeData] = await Promise.all([
      fetchHandoverPolicies(), api('/manufacturing/routes'), api('/employees', { params: { tab: 'all', page_size: 200 } }),
    ])
    routes.value = rows(routeData)
    employees.value = rows(employeeData).filter((item) => !item.is_suspended)
    policies.value = rows(policyData).filter((item) => item.kind === 'cutting')
    if (!policies.value.length) policies.value = [{ id: 0, kind: 'cutting', mode: 'off', source_node: null, target_node: null, target_group_no: null, receiver: null, timeout_minutes: 120, updated_by_name: '' }]
    const preferred = policies.value.findIndex((item) => item.id === preferredId)
    const fallback = policies.value.findIndex((item) => !item.source_node && !item.target_node)
    selectedIndex.value = preferred >= 0 ? preferred : Math.max(0, fallback)
    apply(policies.value[selectedIndex.value])
  } catch (reason) { error.value = reason.message } finally { loading.value = false }
}
function choose() { error.value = ''; if (current.value) apply(current.value) }
function addOverride() {
  const policy = { id: 0, kind: 'cutting', mode: 'warn', source_node: null, target_node: null, target_group_no: null, receiver: null, timeout_minutes: 120, updated_by_name: '' }
  policies.value.push(policy)
  selectedIndex.value = policies.value.length - 1
  apply(policy)
  error.value = ''
}
async function save() {
  const timeout = Number(form.timeout)
  if (!Number.isInteger(timeout) || timeout <= 0) { error.value = '超时时间须为正整数分钟。'; return }
  if (current.value && !current.value.source_node && !current.value.target_node && (form.sourceNode || form.targetNode)) {
    // 默认规则也可直接改成覆盖规则；后端唯一约束负责拒绝重复范围。
  }
  saving.value = true
  error.value = ''
  try {
    const saved = await saveHandoverPolicy(current.value?.id || 0, {
      kind: 'cutting', mode: form.mode,
      source_node: form.sourceNode ? Number(form.sourceNode) : null,
      target_node: form.targetNode ? Number(form.targetNode) : null,
      target_group_no: form.group ? Number(form.group) : null,
      receiver: form.receiver ? Number(form.receiver) : null,
      timeout_minutes: timeout,
    })
    emit('toast', '交接策略已保存')
    await load(saved.id)
  } catch (reason) { error.value = reason.message } finally { saving.value = false }
}
onMounted(() => load())
</script>

<template>
  <AdminModal title="交接策略与超时" description="节点越具体优先级越高：来源+接收节点 > 仅来源 > 仅接收 > 公司默认。" :saving="saving" wide @close="emit('close')">
    <PageState :loading="loading" :error="error && !policies.length ? error : ''" @retry="load()">
      <div class="policy-layout">
        <aside class="policy-list"><div class="policy-list-head"><strong>规则</strong><button class="button secondary small" @click="addOverride">新增节点覆盖</button></div><button v-for="(policy, index) in policies" :key="`${policy.id}-${index}`" :class="{ active: selectedIndex === index }" @click="selectedIndex = index; choose()"><strong>{{ policyLabel(policy) }}</strong><small>{{ policy.mode }} · {{ policy.timeout_minutes }} 分钟{{ policy.id ? '' : ' · 尚未保存' }}</small></button></aside>
        <section v-if="current" class="policy-editor">
          <div class="policy-notice">{{ modes.find(([value]) => value === form.mode)?.[2] }} 超时通知由服务端计划任务生成，每位相关接收人每天最多一条。</div>
          <div class="form-grid">
            <label class="field full"><span>策略模式</span><select v-model="form.mode"><option v-for="[value, label] in modes" :key="value" :value="value">{{ value }} · {{ label }}</option></select></label>
            <label class="field"><span>交出节点（留空不限）</span><select v-model="form.sourceNode"><option value="">全部交出节点</option><option v-for="node in nodeOptions" :key="node.id" :value="String(node.id)">{{ node.routeName }} / {{ node.sequence }}. {{ node.stage_name }}</option></select></label>
            <label class="field"><span>接收节点（留空不限）</span><select v-model="form.targetNode"><option value="">全部接收节点</option><option v-for="node in nodeOptions" :key="node.id" :value="String(node.id)">{{ node.routeName }} / {{ node.sequence }}. {{ node.stage_name }}</option></select></label>
            <label class="field"><span>目标组（选填）</span><select v-model="form.group"><option value="">不指定</option><option v-for="group in groups" :key="group" :value="String(group)">{{ group }} 组</option></select></label>
            <label class="field"><span>指定接收人（选填）</span><select v-model="form.receiver"><option value="">不指定</option><option v-for="employee in employees" :key="employee.user_id" :value="String(employee.user_id)">{{ employee.name }} · {{ employee.group_no }} 组</option></select></label>
            <label class="field full"><span>超时时间（分钟）</span><input v-model="form.timeout" type="number" min="1" step="1" /></label>
          </div>
          <p class="section-note">目标组和接收人随规则保存；当前后端的节点匹配优先级决定交接单实际策略。相同节点范围不可重复。</p>
          <p v-if="current.updated_by_name" class="cell-subtitle">最近保存：{{ current.updated_by_name }}</p>
          <p v-if="error" class="form-error" role="alert">{{ error }}</p>
          <div class="form-actions"><button class="button primary" :disabled="saving" @click="save"><span v-if="saving" class="spinner light" />{{ saving ? '保存中…' : '保存策略' }}</button></div>
        </section>
      </div>
    </PageState>
  </AdminModal>
</template>

<style scoped>
.policy-layout{display:grid;grid-template-columns:minmax(210px,.8fr) minmax(320px,1.5fr);gap:20px}.policy-list{display:flex;flex-direction:column;gap:7px}.policy-list-head{display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:5px}.policy-list>button{border:1px solid var(--line);background:#fff;border-radius:9px;padding:11px;text-align:left;color:var(--ink)}.policy-list>button.active{border-color:var(--orange);background:var(--orange-soft)}.policy-list strong,.policy-list small{display:block;overflow-wrap:anywhere}.policy-list strong{font-size:12px}.policy-list small{color:var(--muted);font-size:10px;margin-top:5px}.policy-notice{background:#fff8e8;color:#8a5300;padding:11px 13px;border-radius:9px;font-size:12px;line-height:1.6;margin-bottom:17px}
@media(max-width:760px){.policy-layout{grid-template-columns:1fr}.policy-list{max-height:210px;overflow:auto}.policy-list-head{position:sticky;top:0;background:#fff;padding:3px 0}}
</style>
