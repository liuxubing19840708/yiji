<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { api, rows } from '../api'
import { dateTime, money } from '../format'
import CommercialModal from './CommercialModal.vue'
import CommercialPagination from './CommercialPagination.vue'
import PageState from './PageState.vue'

const props = defineProps({ dateFrom: { type: String, default: '' }, dateTo: { type: String, default: '' } })
const emit = defineEmits(['toast'])
const data = ref({ results: [], count: 0 })
const employees = ref([])
const loading = ref(true)
const error = ref('')
const filters = reactive({ employee_id: '', status: '', page: 1 })
const dialog = ref('')
const saving = ref(false)
const actionError = ref('')
const detail = ref(null)
const detailLoading = ref(false)
const voidTarget = ref(null)
const voidReason = ref('')
const settlements = ref([])
const paymentHistory = ref([])
const sourcesLoading = ref(false)
const sourcesError = ref('')
const selectedSettlementId = ref('')
const allocations = ref([])
const methods = [['bank', '银行转账'], ['cash', '现金'], ['wechat', '微信'], ['alipay', '支付宝'], ['other', '其他']]
const form = reactive({ employee_id: '', amount: '', paid_at: '', method: 'bank', transaction_ref: '', note: '', allocation_mode: 'none' })

const paymentRows = computed(() => rows(data.value))
const cents = (value) => Math.round(Number(value || 0) * 100)
const amountText = (value) => (Number(value || 0)).toFixed(2)
const allocationTotal = computed(() => allocations.value.reduce((total, row) => total + cents(row.amount), 0))
const allocationDifference = computed(() => cents(form.amount) - allocationTotal.value)
const availableSettlements = computed(() => {
  const uid = String(form.employee_id || '')
  if (!uid) return []
  return settlements.value.filter((item) => item.status === 'settled').map((item) => {
    let employeeAmount = null
    if (item.user !== null && item.user !== undefined) {
      if (String(item.user) === uid) employeeAmount = item.amount
    } else if (item.wage_detail && item.wage_detail[uid] !== undefined) {
      employeeAmount = item.wage_detail[uid]
    }
    if (employeeAmount === null) return null
    const paid = paymentHistory.value.filter((payment) => payment.status === 'active' && String(payment.employee) === uid)
      .flatMap((payment) => payment.allocations || [])
      .filter((allocation) => Number(allocation.settlement) === Number(item.id))
      .reduce((total, allocation) => total + cents(allocation.amount), 0)
    return { ...item, employee_amount: employeeAmount, paid: amountText(paid / 100), outstanding: amountText((cents(employeeAmount) - paid) / 100) }
  }).filter((item) => item && cents(item.outstanding) > 0)
})
const selectableSettlements = computed(() => availableSettlements.value.filter((item) => !allocations.value.some((row) => Number(row.settlement_id) === Number(item.id))))

function localDateTime() {
  const now = new Date()
  const offset = now.getTimezoneOffset() * 60000
  return new Date(now.getTime() - offset).toISOString().slice(0, 16)
}
async function loadEmployees() {
  employees.value = await api('/salary/company/employees')
}
async function load() {
  loading.value = true
  error.value = ''
  try {
    data.value = await api('/salary/payments', {
      params: { ...filters, date_from: props.dateFrom, date_to: props.dateTo, page_size: 20 },
    })
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}
function search() {
  filters.page = 1
  load()
}
async function fetchAll(path, params = {}) {
  const result = []
  let page = 1
  let response
  do {
    response = await api(path, { params: { ...params, page, page_size: 100 } })
    result.push(...rows(response))
    page += 1
  } while (response.next && page <= 100)
  return result
}
async function loadSources() {
  sourcesLoading.value = true
  sourcesError.value = ''
  try {
    const [settlementRows, payments] = await Promise.all([
      fetchAll('/salary/settlements'),
      fetchAll('/salary/payments'),
    ])
    settlements.value = settlementRows
    paymentHistory.value = payments
  } catch (e) {
    settlements.value = []
    paymentHistory.value = []
    sourcesError.value = `${e.message}。仍可选择“无结算支付”，或关闭后重试。`
  } finally {
    sourcesLoading.value = false
  }
}
async function openCreate() {
  Object.assign(form, { employee_id: '', amount: '', paid_at: localDateTime(), method: 'bank', transaction_ref: '', note: '', allocation_mode: 'none' })
  allocations.value = []
  selectedSettlementId.value = ''
  actionError.value = ''
  sourcesError.value = ''
  dialog.value = 'create'
  try {
    await loadEmployees()
  } catch (e) {
    actionError.value = e.message
    return
  }
  loadSources()
}
function changeEmployee() {
  allocations.value = []
  selectedSettlementId.value = ''
}
function addAllocation() {
  const settlement = selectableSettlements.value.find((item) => Number(item.id) === Number(selectedSettlementId.value))
  if (!settlement) return
  allocations.value.push({ settlement_id: settlement.id, settlement, amount: settlement.outstanding })
  selectedSettlementId.value = ''
}
function removeAllocation(id) {
  allocations.value = allocations.value.filter((row) => Number(row.settlement_id) !== Number(id))
}
function validatePayment() {
  if (!form.employee_id) return '请选择员工'
  if (!form.paid_at) return '请选择实付时间'
  if (!form.transaction_ref.trim()) return '请输入外部流水号'
  if (cents(form.amount) <= 0) return '实付金额必须大于 0'
  if (form.allocation_mode === 'none') return ''
  if (!allocations.value.length) return '请添加结算分摊，或明确选择无结算支付'
  if (allocations.value.some((row) => cents(row.amount) <= 0)) return '每笔分摊金额必须大于 0'
  for (const row of allocations.value) {
    if (cents(row.amount) > cents(row.settlement.outstanding)) return `结算单 #${row.settlement_id} 的分摊不能超过当前可付余额`
  }
  if (allocationDifference.value !== 0) return '分摊金额合计必须等于实付金额'
  return ''
}
async function submitPayment() {
  if (saving.value) return
  actionError.value = validatePayment()
  if (actionError.value) return
  saving.value = true
  try {
    await api('/salary/payments', {
      method: 'POST',
      body: {
        employee_id: Number(form.employee_id),
        paid_at: new Date(form.paid_at).toISOString(),
        method: form.method,
        transaction_ref: form.transaction_ref.trim(),
        note: form.note,
        amount: amountText(cents(form.amount) / 100),
        allocations: form.allocation_mode === 'settlement'
          ? allocations.value.map((row) => ({ settlement_id: Number(row.settlement_id), amount: amountText(cents(row.amount) / 100) }))
          : [],
      },
    })
    dialog.value = ''
    emit('toast', '实付工资已登记')
    await load()
  } catch (e) {
    actionError.value = e.message
  } finally {
    saving.value = false
  }
}
async function openDetail(row) {
  dialog.value = 'detail'
  detail.value = null
  detailLoading.value = true
  actionError.value = ''
  try {
    detail.value = await api(`/salary/payments/${row.id}`)
  } catch (e) {
    actionError.value = e.message
  } finally {
    detailLoading.value = false
  }
}
function openVoid(row) {
  voidTarget.value = row
  voidReason.value = ''
  actionError.value = ''
  dialog.value = 'void'
}
async function submitVoid() {
  if (saving.value) return
  if (!voidReason.value.trim()) {
    actionError.value = '请填写作废原因'
    return
  }
  saving.value = true
  actionError.value = ''
  try {
    const updated = await api(`/salary/payments/${voidTarget.value.id}/void`, { method: 'POST', body: { reason: voidReason.value.trim() } })
    dialog.value = ''
    if (detail.value?.id === updated.id) detail.value = updated
    emit('toast', '支付已作废，记录与分摊仍保留')
    await load()
  } catch (e) {
    actionError.value = e.message
  } finally {
    saving.value = false
  }
}
function closeDialog() {
  if (!saving.value) dialog.value = ''
}
watch(() => [props.dateFrom, props.dateTo], search)
onMounted(async () => {
  await load()
  try { await loadEmployees() } catch { /* 无管理权限时仍允许查看本人支付 */ }
})
</script>

<template>
  <section class="salary-payments" aria-label="工资实付">
    <div class="payment-toolbar">
      <form class="payment-filters" aria-label="支付筛选" @submit.prevent="search">
        <label class="field"><span>员工</span><select v-model="filters.employee_id" aria-label="筛选员工"><option value="">全部可见员工</option><option v-for="employee in employees" :key="employee.user_id" :value="employee.user_id">{{ employee.real_name }}</option></select></label>
        <label class="field"><span>支付状态</span><select v-model="filters.status"><option value="">全部状态</option><option value="active">有效</option><option value="void">已作废</option></select></label>
        <button class="button secondary" type="submit">筛选</button>
      </form>
      <button class="button primary" type="button" @click="openCreate">登记实付</button>
    </div>
    <p class="payment-immutable">支付是实际付款事实，不支持编辑或删除；错误记录只能填写作废原因后作废。</p>

    <PageState :loading="loading" :error="error" :empty="!paymentRows.length" empty-text="当前条件下暂无支付记录" @retry="load">
      <section class="card table-card payment-desktop">
        <div class="table-wrap"><table>
          <thead><tr><th>实付时间 / 员工</th><th class="numeric">金额</th><th>方式 / 流水号</th><th>结算分摊</th><th>状态</th><th>操作</th></tr></thead>
          <tbody><tr v-for="row in paymentRows" :key="row.id">
            <td>{{ dateTime(row.paid_at) }}<small class="cell-subtitle">{{ row.employee_name }}</small></td>
            <td class="numeric payment-amount">{{ money(row.amount) }}</td>
            <td>{{ row.method }}<small class="cell-subtitle payment-ref">{{ row.transaction_ref }}</small></td>
            <td>{{ row.allocations?.length ? `${row.allocations.length} 笔` : '无结算支付' }}</td>
            <td><span class="badge" :class="row.status">{{ row.status_display }}</span></td>
            <td><button class="button ghost small" type="button" @click="openDetail(row)">详情</button><button v-if="row.status === 'active'" class="button ghost small danger-text" type="button" @click="openVoid(row)">作废</button></td>
          </tr></tbody>
        </table></div>
      </section>
      <div class="payment-mobile">
        <article v-for="row in paymentRows" :key="row.id" class="card payment-card">
          <div class="payment-card-head"><div><strong>{{ money(row.amount) }}</strong><span>{{ row.employee_name }} · {{ dateTime(row.paid_at) }}</span></div><span class="badge" :class="row.status">{{ row.status_display }}</span></div>
          <div class="payment-definition"><span>支付方式</span><strong>{{ row.method }}</strong></div>
          <div class="payment-definition"><span>外部流水号</span><strong>{{ row.transaction_ref }}</strong></div>
          <div class="payment-definition"><span>结算分摊</span><strong>{{ row.allocations?.length ? `${row.allocations.length} 笔` : '无结算支付' }}</strong></div>
          <div class="payment-actions"><button class="button secondary small" type="button" @click="openDetail(row)">查看详情</button><button v-if="row.status === 'active'" class="button secondary small danger-text" type="button" @click="openVoid(row)">作废</button></div>
        </article>
      </div>
      <CommercialPagination :page="filters.page" :count="data.count || paymentRows.length" :page-size="20" :disabled="loading" @change="filters.page = $event; load()" />
    </PageState>

    <CommercialModal v-if="dialog === 'create'" wide title="登记实付工资" subtitle="记录已经发生的付款；金额和分摊将在后端再次校验" @close="closeDialog">
      <form class="payment-form" @submit.prevent="submitPayment">
        <div class="payment-form-grid">
          <label class="field"><span>员工</span><select v-model="form.employee_id" required @change="changeEmployee"><option value="">请选择员工</option><option v-for="employee in employees" :key="employee.user_id" :value="employee.user_id">{{ employee.real_name }}{{ employee.status_display ? `（${employee.status_display}）` : '' }}</option></select></label>
          <label class="field"><span>实付金额（元）</span><input v-model="form.amount" type="number" min="0.01" step="0.01" inputmode="decimal" required /></label>
          <label class="field"><span>实付时间</span><input v-model="form.paid_at" type="datetime-local" required /></label>
          <label class="field"><span>支付方式</span><select v-model="form.method" required><option v-for="method in methods" :key="method[0]" :value="method[0]">{{ method[1] }}</option></select></label>
          <label class="field payment-wide-field"><span>外部流水号</span><input v-model.trim="form.transaction_ref" maxlength="100" autocomplete="off" required placeholder="填写银行或支付渠道的真实流水号" /></label>
          <label class="field payment-wide-field"><span>备注</span><textarea v-model="form.note" maxlength="500" placeholder="选填，最多 500 字" /></label>
        </div>

        <fieldset class="allocation-fieldset">
          <legend>结算归属</legend>
          <label class="allocation-choice"><input v-model="form.allocation_mode" type="radio" value="settlement" />分摊到已有结算单</label>
          <label class="allocation-choice"><input v-model="form.allocation_mode" type="radio" value="none" />无结算支付（不抵扣任何结算单）</label>
          <template v-if="form.allocation_mode === 'settlement'">
            <p v-if="sourcesLoading" class="payment-source-state" role="status"><span class="spinner" />正在读取结算及有效支付记录…</p>
            <p v-else-if="sourcesError" class="form-error" role="alert">{{ sourcesError }}</p>
            <template v-else>
              <div class="allocation-picker">
                <label class="field"><span>可支付结算单</span><select v-model="selectedSettlementId" aria-label="选择可支付结算单" :disabled="!form.employee_id || !selectableSettlements.length"><option value="">{{ form.employee_id ? (selectableSettlements.length ? '选择结算单' : '该员工暂无可支付结算单') : '请先选择员工' }}</option><option v-for="item in selectableSettlements" :key="item.id" :value="item.id">#{{ item.id }} · {{ item.period_start }} 至 {{ item.period_end }} · 可付 {{ money(item.outstanding) }}</option></select></label>
                <button class="button secondary" type="button" :disabled="!selectedSettlementId" @click="addAllocation">添加分摊</button>
              </div>
              <p class="payment-source-note">可付余额按结算单中的该员工快照减去有效实付分摊计算；提交时以后端锁定后的余额为准。</p>
              <div v-if="allocations.length" class="allocation-list">
                <div v-for="row in allocations" :key="row.settlement_id" class="allocation-row">
                  <div><strong>结算单 #{{ row.settlement_id }}</strong><span>{{ row.settlement.period_start }} 至 {{ row.settlement.period_end }} · 快照 {{ money(row.settlement.employee_amount) }} · 已付 {{ money(row.settlement.paid) }}</span></div>
                  <label class="field"><span>分摊金额（元）</span><input v-model="row.amount" type="number" min="0.01" :max="row.settlement.outstanding" step="0.01" inputmode="decimal" required :aria-label="`结算单 ${row.settlement_id} 分摊金额`" /></label>
                  <button class="button ghost small" type="button" :aria-label="`移除结算单 ${row.settlement_id}`" @click="removeAllocation(row.settlement_id)">移除</button>
                </div>
              </div>
              <div v-else class="payment-allocation-empty">尚未添加结算分摊</div>
              <div class="allocation-total" :class="{ mismatch: allocationDifference !== 0 }" role="status"><span>分摊合计 {{ money(allocationTotal / 100) }} / 实付 {{ money(form.amount) }}</span><strong>{{ allocationDifference === 0 ? '金额一致' : `还差 ${money(allocationDifference / 100)}` }}</strong></div>
            </template>
          </template>
        </fieldset>
        <p v-if="actionError" class="form-error" role="alert">{{ actionError }}</p>
        <div class="form-actions"><button class="button secondary" type="button" :disabled="saving" @click="closeDialog">取消</button><button class="button primary" type="submit" :disabled="saving">{{ saving ? '正在提交…' : '确认登记实付' }}</button></div>
      </form>
    </CommercialModal>

    <CommercialModal v-if="dialog === 'detail'" wide title="支付详情" :subtitle="detail ? `支付记录 #${detail.id} · 只读` : '正在读取支付记录'" @close="closeDialog">
      <div v-if="detailLoading" class="page-state" role="status"><span class="spinner" />正在读取支付详情…</div>
      <p v-else-if="actionError" class="form-error" role="alert">{{ actionError }}</p>
      <template v-else-if="detail">
        <div class="payment-detail-grid">
          <div><span>员工</span><strong>{{ detail.employee_name }}</strong></div><div><span>实付金额</span><strong class="payment-amount">{{ money(detail.amount) }}</strong></div>
          <div><span>实付时间</span><strong>{{ dateTime(detail.paid_at) }}</strong></div><div><span>状态</span><strong>{{ detail.status_display }}</strong></div>
          <div><span>支付方式</span><strong>{{ detail.method }}</strong></div><div><span>外部流水号</span><strong>{{ detail.transaction_ref }}</strong></div>
          <div><span>操作人</span><strong>{{ detail.operator_name || '—' }}</strong></div><div><span>创建时间</span><strong>{{ dateTime(detail.created_at) }}</strong></div>
        </div>
        <p class="payment-detail-note">备注：{{ detail.note || '—' }}</p>
        <section v-if="detail.status === 'void'" class="payment-void-info" aria-label="作废信息"><strong>作废原因</strong><p>{{ detail.void_reason }}</p><span>{{ detail.voided_by_name || '—' }} · {{ dateTime(detail.voided_at) }}</span></section>
        <section class="payment-detail-allocations"><h3>结算分摊</h3><div v-if="!detail.allocations?.length" class="payment-allocation-empty">无结算支付，不抵扣结算单</div><div v-for="item in detail.allocations" :key="item.id" class="payment-definition"><span>结算单 #{{ item.settlement }} · 结算总额 {{ money(item.settlement_amount) }}</span><strong>{{ money(item.amount) }}</strong><small>该员工有效累计实付 {{ money(item.paid_amount) }} · 当前未付 {{ money(item.outstanding) }}</small></div></section>
        <div class="form-actions"><button v-if="detail.status === 'active'" class="button secondary danger-text" type="button" @click="openVoid(detail)">作废此支付</button><button class="button primary" type="button" @click="closeDialog">关闭</button></div>
      </template>
    </CommercialModal>

    <CommercialModal v-if="dialog === 'void'" title="作废支付" :subtitle="voidTarget ? `${voidTarget.employee_name} · ${money(voidTarget.amount)} · ${voidTarget.transaction_ref}` : ''" @close="closeDialog">
      <form @submit.prevent="submitVoid">
        <p>作废后该支付不再抵扣结算余额，但支付与分摊记录会保留，且不可恢复。</p>
        <label class="field"><span>作废原因</span><textarea v-model="voidReason" maxlength="500" required autofocus placeholder="请说明实际作废原因" /></label>
        <p v-if="actionError" class="form-error" role="alert">{{ actionError }}</p>
        <div class="form-actions"><button class="button secondary" type="button" :disabled="saving" @click="closeDialog">取消</button><button class="button primary" type="submit" :disabled="saving">{{ saving ? '正在作废…' : '确认作废' }}</button></div>
      </form>
    </CommercialModal>
  </section>
</template>

<style scoped>
.payment-toolbar,.payment-filters,.payment-card-head,.payment-definition,.payment-actions,.allocation-picker,.allocation-row,.allocation-total{display:flex;align-items:center}
.payment-toolbar{justify-content:space-between;gap:16px;margin-bottom:10px}.payment-filters{flex-wrap:wrap;gap:10px}.payment-filters .button{align-self:flex-end}.payment-immutable{margin:0 0 14px;color:var(--muted);font-size:12px}.payment-amount{color:var(--orange);font-weight:650}.payment-ref{display:block;max-width:190px;overflow-wrap:anywhere}.danger-text{color:var(--danger)!important}.payment-mobile{display:none}.payment-form-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}.payment-wide-field{grid-column:1/-1}.allocation-fieldset{min-width:0;margin:18px 0;padding:15px;border:1px solid var(--line);border-radius:12px}.allocation-fieldset legend{padding:0 7px;font-weight:650}.allocation-choice{display:inline-flex;align-items:center;gap:7px;margin:0 22px 14px 0;font-size:13px}.allocation-picker{align-items:flex-end;gap:10px}.allocation-picker .field{flex:1}.payment-source-note{margin:10px 0;color:var(--muted);font-size:12px;line-height:1.6}.payment-source-state{display:flex;align-items:center;gap:8px}.allocation-list{margin-top:12px}.allocation-row{gap:12px;padding:12px 0;border-top:1px solid var(--line)}.allocation-row>div{flex:1;min-width:0}.allocation-row>div strong,.allocation-row>div span{display:block}.allocation-row>div span{margin-top:5px;color:var(--muted);font-size:11px;line-height:1.5}.allocation-row .field{width:170px;flex-shrink:0}.allocation-total{justify-content:space-between;gap:12px;margin-top:12px;padding:12px;border-radius:9px;background:#f3f7f4;color:var(--good);font-size:13px}.allocation-total.mismatch{background:var(--orange-soft);color:var(--orange-dark)}.payment-allocation-empty{padding:18px;color:var(--muted);text-align:center;font-size:12px}.payment-detail-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}.payment-detail-grid>div{min-width:0}.payment-detail-grid span,.payment-detail-grid strong{display:block}.payment-detail-grid span{color:var(--muted);font-size:12px}.payment-detail-grid strong{margin-top:6px;overflow-wrap:anywhere}.payment-detail-note{overflow-wrap:anywhere}.payment-void-info{padding:13px;border-radius:10px;background:#fff2f0}.payment-void-info p{margin:7px 0;overflow-wrap:anywhere}.payment-void-info span{color:var(--muted);font-size:12px}.payment-detail-allocations{margin-top:18px}.payment-detail-allocations h3{font-size:15px}.payment-definition{justify-content:space-between;flex-wrap:wrap;gap:8px;padding:11px 0;border-bottom:1px solid var(--line);font-size:13px}.payment-definition>span{color:var(--muted)}.payment-definition>strong{overflow-wrap:anywhere}.payment-definition>small{flex-basis:100%;color:var(--muted)}
@media(max-width:760px){.payment-desktop{display:none}.payment-mobile{display:flex;flex-direction:column;gap:12px}.payment-toolbar{align-items:stretch;flex-direction:column}.payment-filters{display:grid;grid-template-columns:repeat(2,minmax(0,1fr))}.payment-filters .field{min-width:0}.payment-filters .button{grid-column:1/-1}.payment-card{padding:15px}.payment-card-head{justify-content:space-between;align-items:flex-start;gap:10px;margin-bottom:8px}.payment-card-head>div{min-width:0}.payment-card-head strong,.payment-card-head span{display:block}.payment-card-head>div>strong{color:var(--orange);font-size:20px}.payment-card-head>div>span{margin-top:6px;color:var(--muted);font-size:11px}.payment-actions{gap:10px;margin-top:12px}.payment-actions .button{flex:1}.payment-form-grid{grid-template-columns:1fr}.payment-wide-field{grid-column:auto}.allocation-choice{display:flex;margin-right:0;line-height:1.5}.allocation-picker{align-items:stretch;flex-direction:column}.allocation-row{align-items:stretch;flex-wrap:wrap}.allocation-row>div{flex-basis:100%}.allocation-row .field{flex:1;width:auto}.allocation-total{align-items:flex-start;flex-direction:column}.payment-detail-grid{grid-template-columns:1fr}.payment-form textarea{min-height:80px}}
</style>
