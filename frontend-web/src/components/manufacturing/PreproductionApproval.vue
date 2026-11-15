<script setup>
import { computed, inject, onMounted, reactive, ref } from 'vue'
import { api, rows } from '../../api'
import { dateTime } from '../../format'
import AdminModal from '../AdminModal.vue'
import PageState from '../PageState.vue'

const emit = defineEmits(['toast'])
const flow = inject('manufacturingFlow')
const packs = ref([])
const selectedId = ref('')
const readiness = ref(null)
const loading = ref(true)
const statusLoading = ref(false)
const error = ref('')
const modal = ref('')
const saving = ref(false)
const decision = reactive({ reason: '' })
const formError = ref('')
const orders = ref([])
const employees = ref([])
const packForm = reactive({ order: '', pattern_no: '', requirements: '', measurements: '' })
const sampleForm = reactive({ title: '', assignee_id: '' })
const issueForm = reactive({ title: '', detail: '' })
const briefForm = reactive({ employee: '', note: '' })
const actionNotes = reactive({})
const manage = computed(() => flow.can('preproduction.manage'))
const activeEmployees = computed(() => employees.value.filter((item) => item.status === 'active'))
function measurementsToText(items) {
  return (items || []).map((row) => [row.point, row.size, row.value, row.tolerance_plus, row.tolerance_minus].join('|')).join('\n')
}
function parseMeasurements(text) {
  return text.split('\n').map((line) => line.trim()).filter(Boolean).map((line) => {
    const [point, size, value, tolerance_plus = '0', tolerance_minus = '0'] = line.split('|').map((v) => v.trim())
    return { point, size, value, tolerance_plus, tolerance_minus, unit: 'cm' }
  })
}
function openPack(edit = false) {
  const pack = edit ? selected.value : null
  Object.assign(packForm, { order: pack?.order || flow.selectedOrderId || orders.value[0]?.id || '', pattern_no: pack?.pattern_no || '', requirements: pack?.requirements || '', measurements: measurementsToText(pack?.measurements) })
  formError.value = ''
  modal.value = edit ? 'edit-pack' : 'create-pack'
}
async function submitPack() {
  saving.value = true
  formError.value = ''
  try {
    const body = { pattern_no: packForm.pattern_no, requirements: packForm.requirements, measurements: parseMeasurements(packForm.measurements), pattern_files: selected.value?.pattern_files || [], attachments: selected.value?.attachments || [], tolerances: selected.value?.tolerances || {} }
    if (modal.value === 'create-pack') await api('/preproduction/tech-packs', { method: 'POST', body: { ...body, order: Number(packForm.order) } })
    else await api(`/preproduction/tech-packs/${selected.value.id}`, { method: 'PUT', body })
    modal.value = ''
    emit('toast', '工艺版本已保存，关键内容变更后需重新审批')
    await load()
  } catch (reason) { formError.value = reason.message } finally { saving.value = false }
}
async function write(path, body, message, method = 'POST') {
  saving.value = true
  error.value = ''
  try { await api(path, { method, body }); emit('toast', message); await load() }
  catch (reason) { error.value = reason.message } finally { saving.value = false }
}
async function createSample() {
  if (!sampleForm.title.trim()) { error.value = '请填写样衣任务名称'; return }
  await write(`/preproduction/tech-packs/${selected.value.id}/samples`, { title: sampleForm.title, assignee_id: sampleForm.assignee_id || null }, '样衣任务已创建')
  sampleForm.title = ''
}
async function createIssue() {
  if (!issueForm.title.trim()) { error.value = '请填写评审问题'; return }
  await write(`/preproduction/tech-packs/${selected.value.id}/issues`, issueForm, '评审问题已登记')
  issueForm.title = ''; issueForm.detail = ''
}
async function createBriefing() {
  if (!briefForm.employee) { error.value = '请选择交底员工'; return }
  await write(`/preproduction/tech-packs/${selected.value.id}/briefing-targets`, briefForm, '交底对象已添加')
  briefForm.employee = ''; briefForm.note = ''
}
const selected = computed(() => packs.value.find((pack) => pack.id === Number(selectedId.value)) || null)
const latestApproval = computed(() => [...(selected.value?.approvals || [])].sort((a, b) => b.id - a.id)[0] || null)
const statusText = (status) => ({ approved: '已批准', rejected: '已驳回', pending: '待审批' }[status] || status || '未审批')
async function loadStatus() {
  readiness.value = null
  if (!selected.value) return
  statusLoading.value = true
  try { readiness.value = await api(`/preproduction/orders/${selected.value.order}/status`) }
  catch (reason) { error.value = reason.message } finally { statusLoading.value = false }
}
async function selectPack() { await loadStatus() }
async function load() {
  loading.value = true
  error.value = ''
  try {
    const [packResponse, orderResponse, employeeResponse] = await Promise.all([
      api('/preproduction/tech-packs'), api('/orders?page_size=200'), api('/employees?page_size=200'),
    ])
    packs.value = rows(packResponse)
    orders.value = rows(orderResponse)
    employees.value = rows(employeeResponse)
    const current = packs.value.find((pack) => pack.id === Number(selectedId.value))
      || packs.value.find((pack) => pack.is_current) || packs.value[0]
    selectedId.value = current ? String(current.id) : ''
    await loadStatus()
  } catch (reason) { error.value = reason.message } finally { loading.value = false }
}
function openDecision(action) { decision.reason = ''; formError.value = ''; modal.value = action }
async function submitDecision() {
  if (modal.value === 'reject' && !decision.reason.trim()) { formError.value = '驳回时必须填写原因'; return }
  saving.value = true
  formError.value = ''
  try {
    await api(`/preproduction/tech-packs/${selected.value.id}/approval`, { method: 'POST', body: { action: modal.value, reason: decision.reason.trim() } })
    emit('toast', modal.value === 'approve' ? '产前工艺包已批准' : '产前工艺包已驳回')
    modal.value = ''
    await load()
  } catch (reason) { formError.value = reason.message } finally { saving.value = false }
}
onMounted(load)
</script>

<template>
  <div class="preproduction-layout">
    <section class="card pack-list-card"><div class="card-header"><div><h3>工艺包版本</h3><p>选择订单工艺包查看审批入口与放行状态。</p></div><div class="form-actions"><button v-if="manage" class="button primary small" @click="openPack(false)">新建版本</button><button class="button ghost small" @click="load">刷新</button></div></div><PageState :loading="loading" :error="error" :empty="!packs.length" empty-text="暂无产前工艺包" @retry="load"><div class="pack-list"><button v-for="pack in packs" :key="pack.id" :class="{ active: Number(selectedId) === pack.id }" @click="selectedId = String(pack.id); selectPack()"><strong>{{ pack.order_no }} · {{ pack.style_no }}</strong><span>V{{ pack.version_no }} · {{ pack.pattern_no || '未填纸样号' }}</span><small>{{ pack.is_current ? '当前版本' : '历史版本' }} · {{ dateTime(pack.updated_at) }}</small></button></div></PageState></section>
    <section v-if="selected" class="card approval-card"><div class="card-header"><div><h3>{{ selected.order_no }} · {{ selected.style_no }} · V{{ selected.version_no }}</h3><p>工艺版本 #{{ selected.id }} · 内容摘要 {{ selected.content_hash || '—' }}</p></div><span class="badge" :class="readiness?.approved ? 'done' : latestApproval?.status === 'rejected' ? 'overdue' : 'producing'">{{ readiness?.approved ? '已放行' : statusText(latestApproval?.status) }}</span></div><div class="preproduction-stats"><div><small>样衣任务</small><strong>{{ selected.sample_tasks.length }}</strong><span>{{ selected.sample_tasks.filter((task) => task.status === 'done').length }} 已完成</span></div><div><small>评审问题</small><strong>{{ selected.review_issues.length }}</strong><span>{{ selected.review_issues.filter((issue) => issue.status === 'closed').length }} 已关闭</span></div><div><small>交底签收</small><strong>{{ selected.briefing_targets.filter((target) => target.receipt).length }}/{{ selected.briefing_targets.length }}</strong><span>已签 / 应签</span></div></div><div v-if="statusLoading" class="page-state"><span class="spinner" />正在读取放行状态…</div><template v-else-if="readiness"><div class="readiness-result" :class="{ approved: readiness.approved }"><strong>{{ readiness.approved ? '产前条件已满足，可进入生产' : '暂未满足生产放行条件' }}</strong><p v-if="readiness.approval">审批 #{{ readiness.approval.id }} · {{ dateTime(readiness.approval.decided_at) }}</p></div><ul v-if="readiness.problems?.length" class="readiness-problems"><li v-for="problem in readiness.problems" :key="problem">{{ problem }}</li></ul><p v-else class="section-note">当前没有待解决的放行问题。</p></template><section v-if="selected.review_issues.length" class="preproduction-issues"><h4>评审问题</h4><article v-for="issue in selected.review_issues" :key="issue.id"><strong>{{ issue.title }}</strong><span class="badge" :class="issue.status === 'closed' ? 'done' : 'producing'">{{ issue.status === 'closed' ? '已关闭' : issue.status }}</span><p>{{ issue.detail }}</p></article></section><section v-if="manage" class="maintenance-workspace">
      <div class="section-heading"><h4>产前资料维护</h4><button v-if="selected.is_current" class="button ghost small" @click="openPack(true)">编辑当前工艺与尺寸表</button></div>
      <div class="measurement-preview"><strong>结构化尺寸表</strong><span v-if="!selected.measurements?.length">尚未录入尺寸</span><div v-for="row in selected.measurements" :key="`${row.point}-${row.size}`">{{ row.point }} · {{ row.size }}：{{ row.value }}{{ row.unit }}（+{{ row.tolerance_plus }}/-{{ row.tolerance_minus }}）</div></div>
      <div class="maintenance-grid">
        <section><h4>样衣任务</h4><div class="inline-form"><input v-model="sampleForm.title" placeholder="样衣任务名称 *" maxlength="100"><select v-model="sampleForm.assignee_id"><option value="">不指定负责人</option><option v-for="employee in activeEmployees" :key="employee.user_id" :value="employee.user_id">{{ employee.name }}</option></select><button class="button primary small" :disabled="saving" @click="createSample">添加</button></div><article v-for="task in selected.sample_tasks" :key="task.id" class="maintenance-item"><strong>{{ task.title }} · {{ task.status }}</strong><small>{{ task.assignee_name || '未指定负责人' }}</small><div class="inline-form"><select v-model="actionNotes[`sample-result-${task.id}`]"><option value="passed">合格</option><option value="failed">不合格</option><option value="rework">重做</option></select><input v-model="actionNotes[`sample-note-${task.id}`]" placeholder="检验/重做说明"><button class="button ghost small" @click="write(`/preproduction/samples/${task.id}/records`, { result: actionNotes[`sample-result-${task.id}`] || 'passed', note: actionNotes[`sample-note-${task.id}`] || '' }, '样衣结果已登记')">登记结果</button></div></article></section>
        <section><h4>评审整改</h4><div class="inline-form"><input v-model="issueForm.title" placeholder="问题标题 *" maxlength="200"><input v-model="issueForm.detail" placeholder="问题说明"><button class="button primary small" @click="createIssue">登记</button></div><article v-for="issue in selected.review_issues" :key="issue.id" class="maintenance-item"><strong>{{ issue.title }} · {{ issue.status }}</strong><p v-if="issue.rectification">整改：{{ issue.rectification }}</p><div v-if="issue.status === 'open'" class="inline-form"><input v-model="actionNotes[`rectify-${issue.id}`]" placeholder="整改说明 *"><button class="button ghost small" @click="write(`/preproduction/issues/${issue.id}`, { action: 'rectify', rectification: actionNotes[`rectify-${issue.id}`] || '' }, '整改已提交', 'PUT')">提交整改</button></div><div v-if="issue.status === 'rectified'" class="inline-form"><input v-model="actionNotes[`review-${issue.id}`]" placeholder="复审意见"><button class="button ghost small" @click="write(`/preproduction/issues/${issue.id}`, { action: 'review', close: false, review_note: actionNotes[`review-${issue.id}`] || '' }, '已退回整改', 'PUT')">退回</button><button class="button primary small" @click="write(`/preproduction/issues/${issue.id}`, { action: 'review', close: true, review_note: actionNotes[`review-${issue.id}`] || '' }, '复审已关闭', 'PUT')">复审关闭</button></div></article></section>
      </div>
      <section class="briefing-section"><h4>工艺交底与员工签收</h4><div class="inline-form"><select v-model="briefForm.employee"><option value="">选择交底员工 *</option><option v-for="employee in activeEmployees" :key="employee.user_id" :value="employee.user_id">{{ employee.name }}</option></select><input v-model="briefForm.note" placeholder="交底说明"><button class="button primary small" @click="createBriefing">添加交底</button></div><article v-for="target in selected.briefing_targets" :key="target.id" class="maintenance-item"><strong>{{ target.employee_name }}</strong><span>{{ target.note || '无补充说明' }} · {{ target.receipt ? `已签 ${dateTime(target.receipt.signed_at)}` : '待签收' }}</span><div v-if="!target.receipt" class="inline-form"><input v-model="actionNotes[`sign-${target.id}`]" placeholder="本人签收备注"><button class="button ghost small" @click="write(`/preproduction/briefing-targets/${target.id}/sign`, { remark: actionNotes[`sign-${target.id}`] || '' }, '交底已签收')">本人签收</button></div></article></section>
    </section>
    <div v-if="flow.can('preproduction.approve')" class="form-actions"><button class="button danger" @click="openDecision('reject')">驳回工艺包</button><button class="button primary" @click="openDecision('approve')">批准并放行</button></div></section>
  </div>
  <AdminModal v-if="modal === 'create-pack' || modal === 'edit-pack'" :title="modal === 'create-pack' ? '新建工艺版本' : '编辑当前工艺版本'" description="尺寸表每行：部位|尺码|尺寸|正公差|负公差，例如 衣长|M|68|1|1。" :saving="saving" @close="modal = ''"><form @submit.prevent="submitPack"><label v-if="modal === 'create-pack'" class="field"><span>客户订单 *</span><select v-model="packForm.order" required><option value="" disabled>选择已关联款式的订单</option><option v-for="order in orders" :key="order.id" :value="order.id">{{ order.order_no }} · {{ order.style_no }}</option></select></label><label class="field"><span>纸样编号</span><input v-model="packForm.pattern_no" maxlength="64"></label><label class="field"><span>结构化尺寸表</span><textarea v-model="packForm.measurements" rows="7" placeholder="衣长|M|68|1|1"></textarea></label><label class="field"><span>工艺要求</span><textarea v-model="packForm.requirements" rows="5"></textarea></label><p v-if="formError" class="form-error">{{ formError }}</p><div class="form-actions"><button type="button" class="button secondary" @click="modal = ''">取消</button><button class="button primary" :disabled="saving">保存工艺版本</button></div></form></AdminModal>
  <AdminModal v-if="modal === 'approve' || modal === 'reject'" :title="modal === 'approve' ? '批准产前工艺包' : '驳回产前工艺包'" description="审批会绑定当前工艺内容摘要；工艺变更后需重新审批。" :saving="saving" @close="modal = ''"><form @submit.prevent="submitDecision"><label class="field"><span>{{ modal === 'approve' ? '审批意见（选填）' : '驳回原因 *' }}</span><textarea v-model="decision.reason" rows="4" maxlength="255" :required="modal === 'reject'" /></label><p v-if="formError" class="form-error">{{ formError }}</p><div class="form-actions"><button type="button" class="button secondary" @click="modal = ''">取消</button><button class="button" :class="modal === 'approve' ? 'primary' : 'danger'" :disabled="saving">确认{{ modal === 'approve' ? '批准' : '驳回' }}</button></div></form></AdminModal>
</template>

<style scoped>
.preproduction-layout{display:grid;grid-template-columns:minmax(250px,340px) minmax(0,1fr);gap:16px}.pack-list-card{padding:0;overflow:hidden}.pack-list-card>.card-header{padding:18px 18px 0}.pack-list{display:flex;flex-direction:column}.pack-list button{display:flex;flex-direction:column;align-items:flex-start;gap:5px;border:0;border-top:1px solid var(--line);background:#fff;padding:14px 18px;text-align:left;color:var(--ink)}.pack-list button.active{background:var(--orange-soft);box-shadow:inset 3px 0 var(--orange)}.pack-list strong{font-size:13px}.pack-list span,.pack-list small{color:var(--muted);font-size:11px}.approval-card{align-self:start}.preproduction-stats{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}.preproduction-stats>div{padding:12px;border-radius:9px;background:#F7F8FA}.preproduction-stats small,.preproduction-stats strong,.preproduction-stats span{display:block}.preproduction-stats small,.preproduction-stats span{color:var(--muted);font-size:11px}.preproduction-stats strong{margin:5px 0;font-size:23px}.readiness-result{margin-top:16px;padding:13px;border-radius:9px;background:var(--orange-soft);color:var(--orange-dark)}.readiness-result.approved{background:#EAF7EF;color:var(--good)}.readiness-result p{margin:5px 0 0;font-size:11px}.readiness-problems{padding:12px 12px 12px 30px;margin:12px 0 0;border:1px solid #F3D3C1;border-radius:9px;color:var(--text-secondary);font-size:12px;line-height:1.8}.preproduction-issues{margin-top:18px}.preproduction-issues h4{font-size:13px}.preproduction-issues article{padding:10px 0;border-top:1px solid var(--line)}.preproduction-issues article>strong{margin-right:8px;font-size:12px}.preproduction-issues article p{margin:7px 0 0;color:var(--muted);font-size:12px}
.maintenance-workspace{margin:18px 0;padding-top:16px;border-top:1px solid var(--line)}.section-heading{display:flex;align-items:center;justify-content:space-between;gap:12px}.section-heading h4,.maintenance-workspace h4{margin:0 0 10px}.measurement-preview{display:flex;flex-wrap:wrap;gap:7px 14px;padding:12px;background:var(--surface-subtle,#F7F8FA);border-radius:8px;font-size:12px;color:var(--text-secondary)}.measurement-preview strong{width:100%;color:var(--ink)}.maintenance-grid{display:grid;grid-template-columns:1fr 1fr;gap:18px;margin-top:18px}.maintenance-item{padding:10px 0;border-top:1px solid var(--line);font-size:12px}.maintenance-item strong,.maintenance-item small,.maintenance-item span{display:block;margin-bottom:5px}.maintenance-item span,.maintenance-item small{color:var(--muted)}.inline-form{display:flex;flex-wrap:wrap;gap:7px;margin:7px 0}.inline-form input,.inline-form select{min-width:120px;flex:1;padding:7px 9px;border:1px solid var(--line);border-radius:7px;background:var(--surface,#fff);color:var(--ink)}.briefing-section{margin-top:18px}
@media(max-width:900px){.maintenance-grid{grid-template-columns:1fr}.preproduction-layout{grid-template-columns:1fr}.pack-list{max-height:260px;overflow:auto}}
@media(max-width:760px){.preproduction-stats{gap:7px}.preproduction-stats>div{padding:9px}.approval-card .form-actions .button{flex:1}}
</style>
