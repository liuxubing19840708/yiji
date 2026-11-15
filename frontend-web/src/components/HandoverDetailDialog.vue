<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { api, createRequestKey, rows } from '../api'
import { fullDateTime } from '../format'
import { hasPermission } from '../permissions'
import { fetchHandover, receiveHandover, rejectHandover, reassignHandover } from '../handover'
import AdminModal from './AdminModal.vue'
import PageState from './PageState.vue'

const props = defineProps({ id: { type: Number, required: true } })
const emit = defineEmits(['close', 'updated', 'open-batch', 'toast'])
const loading = ref(true)
const error = ref('')
const detail = ref(null)
const action = ref('')
const mode = ref('normal')
const receiveReason = ref('')
const rejectReason = ref('')
const reassignReason = ref('')
const reassignReceiver = ref('')
const employees = ref([])
const requestKeys = reactive({ receive: createRequestKey(), reject: createRequestKey(), reassign: createRequestKey() })
const differenceTypes = [
  ['shortage', '短少'], ['overage', '多出'], ['damage', '破损'],
  ['wrong_item', '错货'], ['quality', '质量问题'], ['other', '其他'],
]
const actionNames = { created: '发起交接', received: '接收', rejected: '拒收', reassigned: '改派', cancelled: '取消' }
const canReceive = computed(() => detail.value?.status === 'pending' && hasPermission('handover.receive'))
const canReassign = computed(() => detail.value?.status === 'pending' && hasPermission('handover.manage'))

function differenceName(type) { return differenceTypes.find(([value]) => value === type)?.[1] || type }
function targetText(item) { return item.receiver_name || (item.target_group_no !== null ? `目标组 ${item.target_group_no}` : '未指定接收方') }
function decorate(item) {
  return {
    ...item,
    items: (item.items || []).map((row) => ({ ...row, difference: false, receivedInput: String(row.quantity), editType: row.difference_type || 'shortage', editReason: '' })),
  }
}
async function load() {
  loading.value = true
  error.value = ''
  try {
    detail.value = decorate(await fetchHandover(props.id))
    mode.value = 'normal'
    receiveReason.value = ''
    rejectReason.value = ''
    reassignReason.value = ''
  } catch (reason) { error.value = reason.message } finally { loading.value = false }
}
function invalidate(name) { requestKeys[name] = createRequestKey() }
function actionFailure(name, reason) {
  const rejected = reason.status >= 400 && reason.status < 500
  if (rejected) invalidate(name)
  error.value = `${reason.message}；${rejected ? '修正后重试将使用新幂等号。' : '网络或服务异常，重试沿用同一幂等号。'}`
}
async function receiveAll() {
  if (!canReceive.value || action.value) return
  if (!window.confirm(`共 ${detail.value.total_quantity} 件，确认无数量、质量或货项差异并全部接收？`)) return
  action.value = 'receive'
  error.value = ''
  try {
    const updated = await receiveHandover(props.id, { request_id: requestKeys.receive })
    invalidate('receive')
    emit('toast', '交接已全部接收')
    emit('updated', updated)
    await load()
  } catch (reason) { actionFailure('receive', reason) } finally { action.value = '' }
}
function showDifferences() {
  mode.value = 'differences'
  error.value = ''
}
async function receiveDifferences() {
  if (!canReceive.value || action.value) return
  const differences = []
  for (const item of detail.value.items) {
    if (!item.difference) continue
    const quantity = Number(item.receivedInput)
    if (!/^\d+$/.test(String(item.receivedInput).trim()) || !Number.isInteger(quantity) || (item.editType !== 'overage' && quantity > item.quantity)) {
      error.value = item.editType === 'overage' ? `菲票 ${item.ticket} 多出时实收须为非负整数。` : `菲票 ${item.ticket} 实收须为 0 至 ${item.quantity} 的整数。`
      return
    }
    if (!item.editReason.trim()) { error.value = `菲票 ${item.ticket} 的差异原因必填。`; return }
    differences.push({ id: item.id, received_quantity: quantity, difference_type: item.editType, reason: item.editReason.trim() })
  }
  if (!differences.length) { error.value = '请至少展开一项差异，或返回直接全部接收。'; return }
  action.value = 'receive'
  error.value = ''
  try {
    const updated = await receiveHandover(props.id, { request_id: requestKeys.receive, reason: receiveReason.value.trim() || undefined, items: differences })
    invalidate('receive')
    emit('toast', '差异接收成功')
    emit('updated', updated)
    await load()
  } catch (reason) { actionFailure('receive', reason) } finally { action.value = '' }
}
async function reject() {
  if (!canReceive.value || action.value) return
  if (!rejectReason.value.trim()) { error.value = '请填写拒收原因。'; return }
  if (!window.confirm('确认拒收该交接单？拒收后不能再次接收。')) return
  action.value = 'reject'
  error.value = ''
  try {
    const updated = await rejectHandover(props.id, { request_id: requestKeys.reject, reason: rejectReason.value.trim() })
    invalidate('reject')
    emit('toast', '交接已拒收')
    emit('updated', updated)
    await load()
  } catch (reason) { actionFailure('reject', reason) } finally { action.value = '' }
}
async function showReassign() {
  mode.value = 'reassign'
  reassignReceiver.value = ''
  error.value = ''
  try { employees.value = rows(await api('/employees', { params: { tab: 'all', page_size: 200 } })).filter((item) => !item.is_suspended) }
  catch (reason) { error.value = `接收人选项读取失败：${reason.message}` }
}
async function reassign() {
  if (!canReassign.value || action.value) return
  if (!reassignReceiver.value || !reassignReason.value.trim()) { error.value = '请选择新接收人并填写改派原因。'; return }
  action.value = 'reassign'
  error.value = ''
  try {
    const updated = await reassignHandover(props.id, { request_id: requestKeys.reassign, receiver: Number(reassignReceiver.value), reason: reassignReason.value.trim() })
    invalidate('reassign')
    emit('toast', '交接已改派')
    emit('updated', updated)
    await load()
  } catch (reason) { actionFailure('reassign', reason) } finally { action.value = '' }
}
onMounted(load)
</script>

<template>
  <AdminModal :title="detail ? `交接详情 · ${detail.handover_no}` : '交接详情'" description="清点实物后接收；差异只提交展开项，其余菲票默认全收。" :saving="Boolean(action)" wide @close="emit('close')">
    <PageState :loading="loading" :error="error && !detail ? error : ''" @retry="load">
      <div v-if="detail" class="handover-detail">
        <section class="detail-summary">
          <div class="detail-title"><div><strong>{{ detail.source_node_name }} → {{ detail.target_node_name }}</strong><p>{{ targetText(detail) }} · 交出人 {{ detail.created_by_name }} · {{ fullDateTime(detail.created_at) }}</p></div><span class="badge" :class="detail.status === 'received' ? 'done' : detail.status === 'pending' ? 'producing' : 'cancelled'">{{ { pending: '待接收', received: '已接收', rejected: '已拒收', cancelled: '已取消' }[detail.status] || detail.status }}</span></div>
          <div class="detail-grid">
            <div class="detail-item"><small>应交 / 实收</small><strong>{{ detail.total_quantity }} / {{ detail.received_quantity }}</strong></div>
            <div class="detail-item"><small>当前策略 / 时限</small><strong>{{ detail.policy_mode }} · {{ detail.timeout_minutes }} 分钟</strong></div>
            <div class="detail-item"><small>应接收截止</small><strong>{{ fullDateTime(detail.due_at) }}</strong></div>
          </div>
          <p v-if="detail.is_overdue" class="overdue-note">待接收已超时 {{ detail.overdue_minutes }} 分钟，请尽快接收、拒收或改派。</p>
          <p v-if="detail.received_by_name" class="cell-subtitle">实际接收：{{ detail.received_by_name }} · {{ fullDateTime(detail.received_at) }}</p>
          <p v-if="detail.remark" class="cell-subtitle">备注：{{ detail.remark }}</p>
          <p v-if="detail.reject_reason" class="overdue-note">拒收原因：{{ detail.reject_reason }}</p>
          <button type="button" class="text-button" @click="emit('open-batch', detail.batch_no)">查看批次 {{ detail.batch_no }} ›</button>
        </section>

        <section>
          <div class="detail-section-head"><h3>菲票明细</h3><span v-if="detail.has_discrepancy" class="difference-tag">有差异</span></div>
          <article v-for="item in detail.items" :key="item.id" class="detail-ticket" :class="{ expanded: item.difference && mode === 'differences' }">
            <div class="detail-ticket-head"><div><strong>{{ item.ticket_label || `菲票 ${item.ticket}` }}</strong><small class="cell-subtitle">菲票 ID {{ item.ticket }} · 应交 {{ item.quantity }} · 实收 {{ item.received_quantity }}</small></div><button v-if="mode === 'differences' && canReceive" type="button" class="button ghost small" @click="item.difference = !item.difference; invalidate('receive')">{{ item.difference ? '收起' : '登记差异' }}</button></div>
            <p v-if="item.difference_type" class="saved-difference">{{ differenceName(item.difference_type) }} · {{ item.reason }}</p>
            <div v-if="mode === 'differences' && item.difference" class="difference-editor">
              <label class="field"><span>实收数量</span><input v-model="item.receivedInput" type="number" min="0" step="1" @input="invalidate('receive')" /></label>
              <label class="field"><span>差异类型</span><select v-model="item.editType" @change="invalidate('receive')"><option v-for="[value, label] in differenceTypes" :key="value" :value="value">{{ label }}</option></select></label>
              <label class="field full"><span>差异原因（必填）</span><textarea v-model="item.editReason" maxlength="255" rows="2" placeholder="说明短少、多出、破损等实际情况" @input="invalidate('receive')" /></label>
            </div>
          </article>
        </section>

        <p v-if="error" class="form-error" role="alert">{{ error }}</p>
        <section v-if="canReceive && mode === 'normal'" class="detail-actions"><button class="button primary" :disabled="Boolean(action)" @click="receiveAll">全部接收</button><button class="button secondary" :disabled="Boolean(action)" @click="showDifferences">有差异，逐项登记</button><button class="button danger" :disabled="Boolean(action)" @click="mode = 'reject'; error = ''">拒收</button><button v-if="canReassign" class="button secondary" :disabled="Boolean(action)" @click="showReassign">改派</button></section>
        <section v-else-if="canReceive && mode === 'differences'" class="difference-submit"><label class="field"><span>整单差异说明（选填）</span><textarea v-model="receiveReason" maxlength="255" rows="2" @input="invalidate('receive')" /></label><div class="detail-actions"><button class="button primary" :disabled="Boolean(action)" @click="receiveDifferences">确认差异并接收</button><button class="button secondary" :disabled="Boolean(action)" @click="mode = 'normal'; error = ''">返回正常接收</button></div></section>
        <section v-else-if="canReceive && mode === 'reject'" class="difference-submit"><label class="field"><span>拒收原因（必填）</span><textarea v-model="rejectReason" maxlength="255" rows="3" @input="invalidate('reject')" /></label><div class="detail-actions"><button class="button danger" :disabled="Boolean(action)" @click="reject">确认拒收</button><button class="button secondary" :disabled="Boolean(action)" @click="mode = 'normal'; error = ''">取消</button></div></section>
        <section v-if="canReassign && mode === 'reassign'" class="difference-submit"><div class="form-grid"><label class="field"><span>新接收人（在职）</span><select v-model="reassignReceiver" @change="invalidate('reassign')"><option value="">选择接收人</option><option v-for="employee in employees" :key="employee.user_id" :value="String(employee.user_id)">{{ employee.name }} · {{ employee.group_no }} 组 · {{ employee.post_name || '未设岗位' }}</option></select></label><label class="field"><span>改派原因（必填）</span><textarea v-model="reassignReason" maxlength="255" rows="2" @input="invalidate('reassign')" /></label></div><div class="detail-actions"><button class="button primary" :disabled="Boolean(action)" @click="reassign">确认改派</button><button class="button secondary" :disabled="Boolean(action)" @click="mode = 'normal'; error = ''">取消</button></div></section>
        <section v-else-if="canReassign && mode === 'normal' && !canReceive" class="detail-actions"><button class="button secondary" :disabled="Boolean(action)" @click="showReassign">改派接收人</button></section>

        <section><div class="detail-section-head"><h3>操作记录</h3></div><div v-if="!detail.events?.length" class="page-state">暂无操作记录</div><article v-for="event in detail.events" :key="event.id" class="event-row"><span class="event-dot" /><div><div class="event-title"><strong>{{ actionNames[event.action] || event.action }}</strong><time>{{ fullDateTime(event.created_at) }}</time></div><small class="cell-subtitle">操作账号：{{ event.actor_name }}<template v-if="event.actual_user_name"> · 实际责任人：{{ event.actual_user_name }}</template></small><p v-if="event.reason" class="event-reason">{{ event.reason }}</p></div></article></section>
      </div>
    </PageState>
  </AdminModal>
</template>

<style scoped>
.handover-detail{display:grid;gap:22px}.detail-summary{padding-bottom:18px;border-bottom:1px solid var(--line)}.detail-title,.detail-ticket-head,.event-title,.detail-section-head{display:flex;justify-content:space-between;align-items:flex-start;gap:12px}.detail-title p{font-size:12px;color:var(--muted);margin:6px 0 16px}.detail-grid{margin-bottom:12px}.overdue-note,.saved-difference{color:var(--danger);font-size:12px;margin:8px 0}.detail-section-head{align-items:center;margin-bottom:10px}.detail-section-head h3{font-size:15px;margin:0}.difference-tag{font-size:11px;color:var(--danger);background:#fff0ef;border-radius:999px;padding:4px 9px}.detail-ticket{border:1px solid var(--line);border-radius:10px;padding:13px;margin-bottom:9px}.detail-ticket.expanded{border-color:#edbda3;background:#fffaf7}.difference-editor{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:14px}.difference-editor .full{grid-column:1/-1}.detail-actions{display:flex;gap:9px;flex-wrap:wrap}.difference-submit{display:grid;gap:14px;padding:14px;background:#faf9f7;border-radius:10px}.event-row{display:grid;grid-template-columns:10px 1fr;gap:10px;padding:10px 0;border-bottom:1px solid var(--line)}.event-dot{width:7px;height:7px;border-radius:50%;background:var(--orange);margin-top:7px}.event-title{align-items:center}.event-title time{font-size:11px;color:var(--muted)}.event-reason{font-size:12px;margin:6px 0 0;color:var(--text-secondary)}
@media(max-width:760px){.detail-grid{grid-template-columns:1fr 1fr}.difference-editor{grid-template-columns:1fr}.difference-editor .full{grid-column:auto}.detail-title{flex-wrap:wrap}.detail-actions .button{flex:1;white-space:nowrap;padding-inline:10px;font-size:12px}.event-title{align-items:flex-start;flex-direction:column;gap:4px}}
</style>
