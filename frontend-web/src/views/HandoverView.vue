<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { createRequestKey } from '../api'
import { fullDateTime } from '../format'
import { hasPermission } from '../permissions'
import { fetchHandoverBatch, fetchHandovers, receiveHandover } from '../handover'
import AppIcon from '../components/AppIcon.vue'
import AdminModal from '../components/AdminModal.vue'
import AdminPagination from '../components/AdminPagination.vue'
import PageState from '../components/PageState.vue'
import HandoverCreateDialog from '../components/HandoverCreateDialog.vue'
import HandoverDetailDialog from '../components/HandoverDetailDialog.vue'
import HandoverPolicyDialog from '../components/HandoverPolicyDialog.vue'

const emit = defineEmits(['toast'])
const allowed = hasPermission('handover.view')
const canCreate = hasPermission('handover.create')
const canReceive = hasPermission('handover.receive')
const canManage = hasPermission('handover.manage')
const box = ref('mine')
const status = ref('')
const page = ref(1)
const data = ref({ count: 0, results: [] })
const loading = ref(false)
const error = ref('')
const busy = ref(0)
const detailId = ref(0)
const createOpen = ref(false)
const policyOpen = ref(false)
const batchNo = ref('')
const batch = ref(null)
const batchLoading = ref(false)
const batchError = ref('')
const receiveKeys = reactive({})
const list = computed(() => data.value.results || [])
const statusNames = { pending: '待接收', received: '已接收', rejected: '已拒收', cancelled: '已取消' }
const policyNames = { off: '关闭', warn: '提醒', strict: '严格' }

function statusClass(item) { return item.status === 'received' ? 'done' : item.status === 'pending' ? (item.is_overdue ? 'overdue' : 'producing') : 'cancelled' }
function targetText(item) { return item.receiver_name || (item.target_group_no !== null ? `目标组 ${item.target_group_no}` : '未指定接收方') }
function differenceName(type) { return { shortage: '短少', overage: '多出', damage: '破损', wrong_item: '错货', quality: '质量问题', other: '其他' }[type] || type }
async function load() {
  if (!allowed) return
  loading.value = true
  error.value = ''
  try {
    data.value = await fetchHandovers({ box: box.value, status: box.value === 'mine' ? 'pending' : status.value || undefined, page: page.value, page_size: 20 })
  } catch (reason) { error.value = reason.message; data.value = { count: 0, results: [] } } finally { loading.value = false }
}
function switchBox(value) {
  if (box.value === value) return
  box.value = value
  page.value = 1
  load()
}
function changeStatus() { page.value = 1; load() }
function openDetail(id) { detailId.value = Number(id) }
async function receiveAll(item) {
  if (!canReceive || item.status !== 'pending' || busy.value) return
  if (!window.confirm(`将按交接单数量全量接收 ${item.total_quantity} 件；如有短少、多出、破损等差异，请进入详情登记。确认继续？`)) return
  busy.value = item.id
  error.value = ''
  receiveKeys[item.id] ||= createRequestKey()
  try {
    await receiveHandover(item.id, { request_id: receiveKeys[item.id] })
    delete receiveKeys[item.id]
    emit('toast', '交接已全部接收')
    await load()
    if (batchNo.value) await loadBatch()
  } catch (reason) {
    const rejected = reason.status >= 400 && reason.status < 500
    if (rejected) delete receiveKeys[item.id]
    error.value = `${reason.message}；${rejected ? '本次请求已拒绝，再次操作将使用新幂等号。' : '网络或服务异常，重试沿用同一幂等号。'}`
  } finally { busy.value = 0 }
}
async function loadBatch() {
  if (!batchNo.value) return
  batchLoading.value = true
  batchError.value = ''
  try { batch.value = await fetchHandoverBatch(batchNo.value) }
  catch (reason) { batchError.value = reason.message; batch.value = null } finally { batchLoading.value = false }
}
function openBatch(no) { detailId.value = 0; batchNo.value = no; batch.value = null; loadBatch() }
function closeBatch() { batchNo.value = ''; batch.value = null; batchError.value = '' }
function created(result) {
  createOpen.value = false
  box.value = 'created'
  page.value = 1
  load()
  if (result.batch_no) openBatch(result.batch_no)
}
async function updated() {
  await load()
  if (batchNo.value) await loadBatch()
}
onMounted(load)
</script>

<template>
  <div class="handover-view">
    <div class="welcome"><div><h2>通用责任交接</h2><p>裁片按责任目标交接，逐项记录实收差异；超时与策略均取服务端实时结果。</p></div><div class="welcome-actions"><button v-if="canManage" class="button secondary" @click="policyOpen = true">交接策略</button><button class="button secondary" :disabled="loading" @click="load"><AppIcon name="refresh" :size="16" />刷新</button><button v-if="canCreate" class="button primary" @click="createOpen = true">批量发起</button></div></div>
    <PageState v-if="!allowed" error="暂无交接查看权限，请联系管理员。" />
    <template v-else>
      <div class="filter-bar handover-filter"><div class="segmented"><button :class="{ active: box === 'mine' }" @click="switchBox('mine')">待我接收</button><button :class="{ active: box === 'created' }" @click="switchBox('created')">我发起</button></div><select v-if="box === 'created'" v-model="status" aria-label="交接状态" @change="changeStatus"><option value="">全部状态</option><option value="pending">待接收</option><option value="received">已接收</option><option value="rejected">已拒收</option><option value="cancelled">已取消</option></select></div>
      <p v-if="error" class="form-error handover-error" role="alert">{{ error }}</p>
      <section class="card table-card">
        <PageState :loading="loading" :empty="!list.length" :empty-text="box === 'mine' ? '暂无待接收交接' : '暂无发起的交接'" @retry="load">
          <div class="table-wrap desktop-handover-table"><table><thead><tr><th>交接单 / 路线</th><th>接收目标 / 发起人</th><th class="numeric">应交 / 实收</th><th>策略 / 超时</th><th>状态</th><th>操作</th></tr></thead><tbody><tr v-for="item in list" :key="item.id"><td><button class="handover-link" @click="openDetail(item.id)">{{ item.handover_no }}</button><small class="cell-subtitle">{{ item.source_node_name }} → {{ item.target_node_name }}</small><button class="batch-link" @click="openBatch(item.batch_no)">批次 {{ item.batch_no }}</button></td><td>{{ targetText(item) }}<small class="cell-subtitle">{{ item.created_by_name }} · {{ fullDateTime(item.created_at) }}</small></td><td class="numeric"><strong>{{ item.total_quantity }} / {{ item.received_quantity }}</strong><small v-if="item.has_discrepancy" class="cell-subtitle difference-text">有差异</small></td><td><span class="policy-chip" :class="`policy-${item.policy_mode}`">{{ item.policy_mode }} · {{ policyNames[item.policy_mode] }}</span><small class="cell-subtitle">{{ item.timeout_minutes }} 分钟 · {{ fullDateTime(item.due_at) }}</small><small v-if="item.is_overdue" class="cell-subtitle difference-text">已超时 {{ item.overdue_minutes }} 分钟</small></td><td><span class="badge" :class="statusClass(item)">{{ statusNames[item.status] || item.status }}</span><small v-if="item.reject_reason" class="cell-subtitle difference-text">{{ item.reject_reason }}</small></td><td><div class="handover-actions"><button v-if="canReceive && item.status === 'pending'" class="button primary small" :disabled="Boolean(busy)" @click="receiveAll(item)">{{ busy === item.id ? '接收中…' : '全部接收' }}</button><button class="button secondary small" @click="openDetail(item.id)">详情</button></div></td></tr></tbody></table></div>
          <div class="handover-mobile-list"><article v-for="item in list" :key="item.id" class="handover-mobile-card"><div class="mobile-row-head"><button class="handover-link" @click="openDetail(item.id)">{{ item.handover_no }}</button><span class="badge" :class="statusClass(item)">{{ statusNames[item.status] || item.status }}</span></div><p class="mobile-route">{{ item.source_node_name }} → {{ item.target_node_name }}</p><div class="mobile-meta"><span>接收目标<strong>{{ targetText(item) }}</strong></span><span>应交 / 实收<strong>{{ item.total_quantity }} / {{ item.received_quantity }}</strong></span><span>策略<strong>{{ item.policy_mode }} · {{ item.timeout_minutes }} 分钟</strong></span><span>发起时间<strong>{{ fullDateTime(item.created_at) }}</strong></span></div><p v-if="item.is_overdue" class="difference-text mobile-notice-text">已超时 {{ item.overdue_minutes }} 分钟，请尽快处理</p><p v-if="item.reject_reason" class="difference-text mobile-notice-text">拒收原因：{{ item.reject_reason }}</p><div class="mobile-card-actions"><button class="batch-link" @click="openBatch(item.batch_no)">批次汇总</button><button v-if="canReceive && item.status === 'pending'" class="button primary small" :disabled="Boolean(busy)" @click="receiveAll(item)">全部接收</button><button class="button secondary small" @click="openDetail(item.id)">详情</button></div></article></div>
          <AdminPagination :page="page" :count="data.count || 0" :disabled="loading" @change="page = $event; load()" />
        </PageState>
      </section>
    </template>

    <HandoverCreateDialog v-if="createOpen" @close="createOpen = false" @created="created" @toast="emit('toast', $event)" />
    <HandoverDetailDialog v-if="detailId" :id="detailId" @close="detailId = 0" @updated="updated" @open-batch="openBatch" @toast="emit('toast', $event)" />
    <HandoverPolicyDialog v-if="policyOpen" @close="policyOpen = false" @toast="emit('toast', $event)" />

    <AdminModal v-if="batchNo" :title="`批次汇总 · ${batchNo}`" description="批次只聚合独立交接单，不虚构批次主单状态。" wide @close="closeBatch">
      <PageState :loading="batchLoading" :error="batchError" @retry="loadBatch">
        <div v-if="batch" class="batch-content">
          <div class="batch-stats"><div><strong>{{ batch.summary.handover_count }}</strong><span>交接单</span></div><div><strong>{{ batch.summary.received_count }}</strong><span>已接收</span></div><div><strong>{{ batch.summary.pending_count }}</strong><span>待接收</span></div><div><strong>{{ batch.summary.rejected_count }}</strong><span>已拒收</span></div><div><strong>{{ batch.summary.total_quantity }}</strong><span>应交合计</span></div><div><strong>{{ batch.summary.received_quantity }}</strong><span>实收合计</span></div></div>
          <div class="table-wrap"><table><thead><tr><th>交接单 / 路线</th><th>接收目标</th><th class="numeric">应交 / 实收</th><th>状态 / 差异</th><th>操作</th></tr></thead><tbody><tr v-for="item in batch.items" :key="item.id"><td><strong>{{ item.handover_no }}</strong><small class="cell-subtitle">{{ item.source_node_name }} → {{ item.target_node_name }}</small></td><td>{{ targetText(item) }}</td><td class="numeric">{{ item.total_quantity }} / {{ item.received_quantity }}</td><td><span class="badge" :class="statusClass(item)">{{ statusNames[item.status] || item.status }}</span><small v-if="item.is_overdue" class="cell-subtitle difference-text">超时 {{ item.overdue_minutes }} 分钟</small><small v-for="row in item.items.filter((entry) => entry.difference_type)" :key="row.id" class="cell-subtitle difference-text">{{ differenceName(row.difference_type) }}：{{ row.reason }}</small></td><td><button class="button secondary small" @click="openDetail(item.id)">详情</button></td></tr></tbody></table></div>
        </div>
      </PageState>
    </AdminModal>
  </div>
</template>

<style scoped>
.welcome-actions{display:flex;gap:8px;flex-wrap:wrap}.handover-filter{margin-bottom:14px}.handover-error{margin-bottom:14px}.handover-link,.batch-link{border:0;background:none;padding:0;text-align:left;color:var(--orange-dark);font-size:13px;font-weight:600;overflow-wrap:anywhere}.batch-link{display:block;margin-top:6px;color:var(--muted);font-size:10px;font-weight:400}.policy-chip{font-size:11px;padding:3px 7px;border-radius:999px;background:#f2f3f5;color:var(--text-secondary);white-space:nowrap}.policy-warn{background:#fff7e6;color:#ad6800}.policy-strict{background:#fff0ef;color:var(--danger)}.difference-text{color:var(--danger);overflow-wrap:anywhere;max-width:220px}.handover-actions{display:flex;gap:6px;white-space:nowrap}.handover-mobile-list{display:none}.batch-content{display:grid;gap:18px}.batch-stats{display:grid;grid-template-columns:repeat(6,1fr);background:#faf9f7;border-radius:10px;padding:14px 4px}.batch-stats>div{text-align:center;border-right:1px solid var(--line)}.batch-stats>div:last-child{border:0}.batch-stats strong,.batch-stats span{display:block}.batch-stats strong{font-size:22px}.batch-stats span{font-size:11px;color:var(--muted);margin-top:4px}
@media(max-width:760px){.welcome-actions{width:100%}.welcome-actions .button{flex:1;padding-inline:10px;font-size:12px}.desktop-handover-table{display:none}.handover-mobile-list{display:block}.handover-mobile-card{padding:14px 12px;border-bottom:1px solid var(--line)}.handover-mobile-card:last-child{border:0}.mobile-route{font-size:12px;color:var(--text-secondary);margin:0 0 12px}.mobile-notice-text{font-size:11px;margin:10px 0 0}.mobile-card-actions{display:flex;align-items:center;justify-content:flex-end;gap:7px;margin-top:13px}.mobile-card-actions .batch-link{margin:0 auto 0 0}.handover-filter .segmented{flex:1}.handover-filter .segmented button{flex:1}.handover-filter select{width:100%}.batch-stats{grid-template-columns:repeat(3,1fr);gap:12px 0}.batch-stats>div:nth-child(3){border-right:0}.batch-content .table-wrap table{min-width:680px}}
</style>
