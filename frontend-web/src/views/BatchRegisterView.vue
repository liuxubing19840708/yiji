<script setup>
import { computed, onMounted, ref } from 'vue'
import { api, rows } from '../api'
import PageState from '../components/PageState.vue'

const emit = defineEmits(['toast'])
const productions = ref([])
const employees = ref([])
const tickets = ref([])
const processes = ref([])
const productionId = ref('')
const userId = ref('')
const processId = ref('')
const loading = ref(false)
const initError = ref('')
const error = ref('')
const submitting = ref(false)
const result = ref(null)

const filledTickets = computed(() => tickets.value.filter((ticket) => String(ticket.input).trim() !== ''))
const registeredQty = (ticket) => Number(ticket.registered_qty || 0)
const remainingQty = (ticket) => Math.max(Number(ticket.qty || 0) - registeredQty(ticket), 0)

async function init() {
  loading.value = true
  initError.value = ''
  try {
    const [productionData, employeeData] = await Promise.all([
      api('/productions', { params: { page_size: 200, is_locked: 0 } }),
      api('/employees'),
    ])
    productions.value = rows(productionData)
    employees.value = rows(employeeData)
  } catch (e) { initError.value = e.message } finally { loading.value = false }
}
async function selectProduction() {
  tickets.value = []
  processes.value = []
  processId.value = ''
  result.value = null
  error.value = ''
  if (!productionId.value) return
  loading.value = true
  try {
    const data = await api(`/productions/${productionId.value}/bundles`)
    tickets.value = (data.results || []).map((ticket) => ({ ...ticket, input: '', result: '', resultText: '' }))
    if (tickets.value.length) await loadProcesses()
  } catch (e) { error.value = e.message } finally { loading.value = false }
}
async function loadProcesses() {
  if (!tickets.value.length) return
  error.value = ''
  try {
    const data = await api('/records/process-selector', {
      params: { ticket_id: tickets.value[0].id, user_id: userId.value || undefined },
    })
    processes.value = data.results || []
  } catch (e) { error.value = e.message }
}
function changeEmployee() {
  processId.value = ''
  result.value = null
  tickets.value = tickets.value.map((ticket) => ({ ...ticket, result: '', resultText: '' }))
  loadProcesses()
}
function changeProcess() {
  result.value = null
  error.value = ''
  tickets.value = tickets.value.map((ticket) => ({ ...ticket, result: '', resultText: '' }))
}
function editQty(ticket) {
  ticket.result = ''
  ticket.resultText = ''
  error.value = ''
}
function fillRemaining() {
  tickets.value.forEach((ticket) => {
    if (String(ticket.input).trim()) return
    ticket.input = String(remainingQty(ticket))
    ticket.result = ''
    ticket.resultText = ''
  })
  result.value = null
  error.value = ''
}
function clearInput() {
  tickets.value = tickets.value.map((ticket) => ({ ...ticket, input: '', result: '', resultText: '' }))
  result.value = null
  error.value = ''
}
function resultBundle(row) {
  return row.bundle_no || tickets.value.find((ticket) => ticket.id === row.ticket_id)?.bundle_no || row.ticket_id
}
async function submit() {
  if (submitting.value) return
  if (!productionId.value) { error.value = '请选择床次'; return }
  if (!processId.value) { error.value = '请选择工序'; return }
  if (!filledTickets.value.length) { error.value = '请至少填写一扎本次件数，留空行不会提交'; return }
  if (filledTickets.value.some((ticket) => !Number.isInteger(Number(ticket.input)) || Number(ticket.input) <= 0)) {
    error.value = '已填写的本次件数须为正整数；不登记的行请留空'
    return
  }
  const entries = filledTickets.value.map((ticket) => ({ ticket_id: ticket.id, qty: Number(ticket.input) }))
  if (entries.length > 50) { error.value = '单次最多登记 50 扎，请分批提交'; return }
  if (!window.confirm(`共 ${entries.length} 扎，确定提交登记？留空行不会提交。`)) return

  submitting.value = true
  error.value = ''
  try {
    const data = await api('/records/batch-register', {
      method: 'POST',
      body: {
        process_id: Number(processId.value),
        user_id: userId.value ? Number(userId.value) : undefined,
        entries,
      },
    })
    result.value = data
    const byTicket = new Map((data.results || []).filter((row) => row.ticket_id !== null).map((row) => [Number(row.ticket_id), row]))
    tickets.value = tickets.value.map((ticket) => {
      const row = byTicket.get(Number(ticket.id))
      if (!row) return ticket
      return {
        ...ticket,
        // 成功扎留空，避免“改失败行重提”时重复提交成功扎；累计量同步到本次响应。
        registered_qty: row.ok ? registeredQty(ticket) + Number(row.qty || 0) : registeredQty(ticket),
        input: row.ok ? '' : ticket.input,
        result: row.ok ? 'ok' : 'fail',
        resultText: row.ok
          ? `已登记 ${row.qty ?? ''} 件${row.amount ? ` · ${row.amount} 元` : ''}`
          : (row.error || '登记失败'),
      }
    })
    emit('toast', `登记完成：成功 ${data.ok}，失败 ${data.fail}`)
  } catch (e) { error.value = e.message } finally { submitting.value = false }
}
onMounted(init)
</script>

<template>
  <div class="batch-register">
    <div class="welcome"><div><h2>批量协助登记</h2><p>按员工、床次工序、逐扎件数三步登记；后端逐扎返回结果。</p></div></div>
    <PageState :loading="loading && !productions.length" :error="initError" @retry="init">
      <section class="card batch-step">
        <h3 class="batch-step-title">1 · 登记给谁</h3>
        <label class="field"><span>被登记员工（留空为本人）</span><select v-model="userId" :disabled="submitting" @change="changeEmployee"><option value="">本人（不代登）</option><option v-for="employee in employees" :key="employee.user_id" :value="employee.user_id">{{ employee.name }} · {{ employee.group_no }} 组</option></select></label>
      </section>

      <section class="card batch-step">
        <h3 class="batch-step-title">2 · 床次与工序</h3>
        <div class="form-grid">
          <label class="field"><span>生产床次</span><select v-model="productionId" :disabled="submitting" @change="selectProduction"><option value="">选择床次</option><option v-for="production in productions" :key="production.id" :value="production.id">{{ production.bed_no }} · {{ production.style_no }} · {{ production.style_name }}</option></select></label>
          <label class="field"><span>登记工序</span><select v-model="processId" :disabled="submitting || !tickets.length" @change="changeProcess"><option value="">选择工序</option><option v-for="process in processes" :key="process.id" :value="process.id">{{ process.name }}<template v-if="process.price !== null && process.price !== undefined"> · ¥{{ process.price }}</template></option></select></label>
        </div>
      </section>

      <section v-if="productionId" class="card table-card batch-ticket-card">
        <div class="card-header batch-ticket-header">
          <div><h3 class="batch-step-title">3 · 逐扎填件数</h3><p>已填 {{ filledTickets.length }} 扎，留空行不提交</p></div>
          <div class="batch-quick-actions"><button type="button" class="button small secondary" :disabled="submitting || !tickets.length" @click="fillRemaining">一键填剩余</button><button type="button" class="button small ghost" :disabled="submitting || !tickets.length" @click="clearInput">清空</button></div>
        </div>
        <PageState :loading="loading" :empty="!tickets.length" empty-text="该床次暂无菲票">
          <div class="table-wrap">
            <table class="bundle-table">
              <thead><tr><th>扎号</th><th>颜色 / 尺码</th><th class="numeric">已登记 / 扎数</th><th class="batch-extra">已登记工序</th><th class="batch-extra">状态</th><th>本次件数</th><th class="batch-result-column">逐扎结果</th></tr></thead>
              <tbody>
                <template v-for="ticket in tickets" :key="ticket.id">
                  <tr class="batch-ticket-row">
                    <td class="cell-title">{{ ticket.bundle_no }}</td>
                    <td>{{ ticket.color || '—' }} / {{ ticket.size || '—' }}</td>
                    <td class="numeric">{{ registeredQty(ticket) }} / {{ ticket.qty }}</td>
                    <td class="batch-extra">{{ ticket.registered_process_count }}/{{ ticket.process_count }}</td>
                    <td class="batch-extra">{{ ticket.state_display }}</td>
                    <td><input v-model="ticket.input" type="number" inputmode="numeric" min="1" step="1" placeholder="留空" :aria-label="`第 ${ticket.bundle_no} 扎本次件数`" :data-ticket-id="ticket.id" :class="{ 'batch-input-error': ticket.result === 'fail', 'batch-input-done': ticket.result === 'ok' }" :disabled="submitting" @input="editQty(ticket)"></td>
                    <td class="batch-result-column" :class="ticket.result === 'ok' ? 'batch-ok-text' : 'batch-error-text'">{{ ticket.resultText || '—' }}</td>
                  </tr>
                  <tr v-if="ticket.result" class="batch-mobile-result-line"><td colspan="4" :class="ticket.result === 'ok' ? 'batch-ok-text' : 'batch-error-text'">{{ ticket.resultText }}</td></tr>
                </template>
              </tbody>
            </table>
          </div>
          <p class="section-note batch-ticket-tip">留空行不提交；单扎失败（超限／工序不在配置）不影响其余扎，失败行可改数后重提。</p>
        </PageState>
      </section>

      <p v-if="error" class="form-error" role="alert">{{ error }}</p>
      <section v-if="productionId && tickets.length" class="card batch-submit-card">
        <button class="button primary" :disabled="submitting || !filledTickets.length || !processId || loading" @click="submit"><span v-if="submitting" class="spinner light" />{{ submitting ? '提交中…' : `提交批量登记（已填 ${filledTickets.length} 扎）` }}</button>
      </section>

      <section v-if="result" class="card table-card batch-result-card">
        <div class="card-header batch-result-header"><h3>登记结果：成功 {{ result.ok }} 扎 · 失败 {{ result.fail }} 扎</h3></div>
        <div class="table-wrap"><table><thead><tr><th>扎号</th><th>结果</th><th class="numeric">数量 / 金额</th><th>原因</th></tr></thead><tbody><tr v-for="(row, index) in result.results" :key="`${row.ticket_id}-${index}`"><td>{{ resultBundle(row) }}</td><td><span class="badge" :class="row.ok ? 'done' : 'overdue'">{{ row.ok ? '成功' : '失败' }}</span></td><td class="numeric">{{ row.qty ?? '—' }} / {{ row.amount ?? '—' }}</td><td>{{ row.error || '—' }}</td></tr></tbody></table></div>
      </section>
    </PageState>
  </div>
</template>

<style scoped>
.batch-step{margin-bottom:18px}.batch-step-title{margin:0 0 16px;font-size:16px}.batch-ticket-card{margin-top:18px}.batch-ticket-header{padding:18px 18px 0;align-items:flex-start}.batch-ticket-header .batch-step-title{margin-bottom:0}.batch-ticket-header p{margin:5px 0 0;color:var(--muted);font-size:12px}.batch-quick-actions{display:flex;gap:5px;flex:none}.bundle-table input{min-width:88px;text-align:center}.bundle-table .batch-input-error{border-color:var(--danger)}.bundle-table .batch-input-done{border-color:var(--good);background:#f0faf3}.batch-result-column{max-width:260px;white-space:normal;font-size:12px}.batch-ok-text{color:var(--good)}.batch-error-text{color:var(--danger);overflow-wrap:anywhere}.batch-mobile-result-line{display:none}.batch-ticket-tip{padding:0 18px 16px}.batch-submit-card{margin-top:18px;padding:14px 18px}.batch-submit-card .button{width:100%;min-height:46px}.batch-result-card{margin-top:18px}.batch-result-header{padding:18px 18px 0}.batch-result-header h3{margin:0;font-size:16px}.batch-result-card td:last-child{white-space:normal;overflow-wrap:anywhere}
@media(max-width:760px){
  .batch-register>.welcome{display:none}.batch-step{margin-bottom:12px}.batch-step .form-grid{grid-template-columns:1fr;gap:16px}.batch-ticket-card{margin-top:12px;padding:0}.batch-result-card{padding:0}.batch-ticket-header{padding:17px 14px 0;gap:8px;flex-wrap:wrap}.batch-ticket-header>div:first-child{min-width:120px}.batch-quick-actions{margin-left:auto}.batch-quick-actions .button{font-size:11px;min-height:32px}.batch-register .table-wrap>table{min-width:100%}.bundle-table{table-layout:fixed}.bundle-table th,.bundle-table td{padding:11px 5px;font-size:12px;white-space:normal;overflow-wrap:anywhere}.bundle-table th:nth-child(1){width:14%}.bundle-table th:nth-child(2){width:25%}.bundle-table th:nth-child(3){width:24%}.bundle-table th:nth-child(6){width:37%}.bundle-table .batch-extra,.bundle-table .batch-result-column{display:none}.bundle-table input{min-width:0;width:100%;min-height:42px;padding:7px 4px;font-size:15px}.batch-mobile-result-line{display:table-row}.batch-mobile-result-line td{padding-top:0;text-align:right;border-bottom:1px solid var(--line)}.batch-ticket-row:has(+ .batch-mobile-result-line) td{border-bottom:0}.batch-ticket-tip{padding:0 14px 14px;font-size:11px}.batch-submit-card{margin-top:12px;padding:14px 17px}.batch-result-card{margin-top:12px}.batch-result-header{padding:17px 14px 0}.batch-result-card th,.batch-result-card td{padding:11px 8px;white-space:normal;font-size:12px}.batch-result-card th:nth-child(1){width:14%}.batch-result-card th:nth-child(2){width:18%}.batch-result-card th:nth-child(3){width:25%}
}
</style>
