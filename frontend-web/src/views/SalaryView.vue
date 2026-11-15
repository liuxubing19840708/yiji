<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { api, download, getSession, rows } from '../api'
import { dateTime, money, monthRange, number } from '../format'
import AppIcon from '../components/AppIcon.vue'
import PageState from '../components/PageState.vue'
import SalaryPayments from '../components/SalaryPayments.vue'
import SalaryDimensions from '../components/SalaryDimensions.vue'

const emit = defineEmits(['toast', 'navigate'])
const props = defineProps({ section: { type: String, default: '' } })
const filters = reactive(monthRange())
const loading = ref(true)
const error = ref('')
const summary = ref(null)
const employees = ref([])
const mine = ref(null)
const personalMode = ref(props.section === 'mine')
const orderSalaryMode = computed(() => props.section === 'orders')
const completionMode = computed(() => props.section === 'completion')
const standaloneMode = computed(() => ['mine', 'orders', 'completion'].includes(props.section))
const pageTitle = computed(() => orderSalaryMode.value ? '订单薪酬' : completionMode.value ? '完成汇总' : personalMode.value ? '我的薪酬' : '薪酬核算')
const pageDescription = computed(() => personalMode.value
  ? '查看我的计件、计数和固定工资。'
  : (orderSalaryMode.value || completionMode.value)
    ? '按日期、款式和工序查看完成数量与工资。'
    : '查询、调整、申请、审核、结算及完成汇总。')
const detail = ref(null)
const tab = ref(['completion', 'orders'].includes(props.section) ? 'completion' : ['settlements', 'adjusts', 'applies', 'payments', 'changes'].includes(props.section) ? props.section : 'overview')
const auxLoading = ref(false)
const auxError = ref('')
const auxData = ref({})
const dialog = ref('')
const saving = ref(false)
const actionError = ref('')
const payslip = ref(null)
const payslipSettlementId = ref(null)
const exportingPayslip = ref(false)
const completion = ref({ styles: [], processes: [], matrix: [], employees: [], tickets: [] })
const completionView = ref('style')
const dimensionTable = ref(null)
const drill = reactive({ style_id: '', process_id: '' })
const completionFilters = reactive({ user_id: '', customer_id: '' })
const completionCustomers = ref([])
const completionFiltersLoaded = ref(false)
const actionForm = reactive({ user_id: '', amount: '', reason: '', note: '' })
const mineMainTab = ref('pay')
const mineSubTab = ref('date')
const uid = getSession().user?.id

const myStats = computed(() => personalMode.value ? {
  total: mine.value?.summary?.amount,
  qty: mine.value?.summary?.qty,
  tickets: mine.value?.summary?.ticket_count,
} : null)
const minePieceAmount = computed(() => mineMainTab.value === 'count' ? 0 : mine.value?.summary?.amount)
const mineTotalAmount = computed(() => Number(minePieceAmount.value || 0) + Number(mine.value?.fixed?.amount || 0))
const completionTotalQty = computed(() => (completion.value?.styles || []).reduce((total, row) => total + Number(row.qty || 0), 0))
const currentCompletionStyle = computed(() => completion.value?.styles?.find((row) => String(row.style_id) === String(drill.style_id)))
const currentCompletionProcess = computed(() => completion.value?.processes?.find((row) => String(row.process_id) === String(drill.process_id)))

async function loadEmployees() {
  if (employees.value.length) return
  employees.value = await api('/salary/company/employees', { params: filters })
}
async function loadCompletionFilters() {
  if (completionFiltersLoaded.value) return
  const [employeeData, customerData] = await Promise.all([
    employees.value.length ? Promise.resolve(employees.value) : api('/salary/company/employees', { params: filters }),
    api('/customers', { params: { page_size: 200 } }),
  ])
  employees.value = employeeData
  completionCustomers.value = rows(customerData)
  completionFiltersLoaded.value = true
}
async function loadOverview() {
  loading.value = true
  error.value = ''
  try {
    if (!personalMode.value) {
      try {
        const [s, e] = await Promise.all([
          api('/salary/company', { params: filters }),
          api('/salary/company/employees', { params: filters }),
        ])
        summary.value = s
        employees.value = e
        return
      } catch (e) {
        if (e.status !== 403 || !uid) throw e
        personalMode.value = true
      }
    }
    const params = { ...filters, ...(mineMainTab.value === 'count' ? { tab: 'count' } : {}) }
    mine.value = await api(`/salary/employee/${uid}`, { params })
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}
async function loadAux() {
  auxLoading.value = true
  auxError.value = ''
  try {
    if (tab.value === 'completion') {
      if (completionView.value === 'dim') {
        await dimensionTable.value?.load()
        return
      }
      const params = { ...filters, ...completionFilters, ...drill }
      const [result] = await Promise.all([
        api('/salary/summary/completion', { params }),
        loadCompletionFilters(),
      ])
      completion.value = result
    } else if (tab.value === 'payments') {
      return
    } else if (tab.value === 'changes') {
      auxData.value = await api('/audit-logs', { params: { ...filters, scope: 'mine', page_size: 100 } })
    } else {
      const path = { settlements: '/salary/settlements', adjusts: '/salary/adjust', applies: '/salary/settle-applies' }[tab.value]
      auxData.value = await api(path, { params: { ...filters, page_size: 100 } })
    }
  } catch (e) {
    auxError.value = e.message
  } finally {
    auxLoading.value = false
  }
}
function load() {
  tab.value === 'overview' ? loadOverview() : loadAux()
}
function changeTab(value) {
  tab.value = value
  dialog.value = ''
  load()
}
function changeCompletionView(value) {
  if (completionView.value === value) return
  completionView.value = value
  if (value === 'style') loadAux()
}
function changeCompletionFilters() {
  loadAux()
}
function navigateFromDimensions(route, section) {
  emit('navigate', route, section)
}
function changeMineMainTab(value) {
  if (mineMainTab.value === value) return
  mineMainTab.value = value
  loadOverview()
}
async function open(employee) {
  detail.value = null
  try {
    detail.value = await api(`/salary/employee/${employee.user_id}`, { params: filters })
  } catch (e) {
    error.value = e.message
  }
}
async function openAction(type) {
  actionError.value = ''
  Object.assign(actionForm, { user_id: '', amount: '', reason: '', note: '' })
  dialog.value = type
  if (type === 'settle' || type === 'adjust') {
    try { await loadEmployees() } catch (e) { actionError.value = e.message }
  }
}
async function submitAction() {
  saving.value = true
  actionError.value = ''
  try {
    if (dialog.value === 'settle') {
      if (!confirm(`确认结算 ${filters.date_from} 至 ${filters.date_to} 的工资？结算会锁定对应记录。`)) return
      await api('/salary/settlements', {
        method: 'POST',
        body: {
          period_start: filters.date_from,
          period_end: filters.date_to,
          user_id: actionForm.user_id ? Number(actionForm.user_id) : undefined,
        },
      })
    } else if (dialog.value === 'adjust') {
      await api('/salary/adjust', {
        method: 'POST',
        body: { user_id: Number(actionForm.user_id), amount: actionForm.amount, reason: actionForm.reason },
      })
    } else {
      await api('/salary/settle-applies', {
        method: 'POST',
        body: { period_start: filters.date_from, period_end: filters.date_to, note: actionForm.note },
      })
    }
    dialog.value = null
    emit('toast', '操作成功')
    load()
  } catch (e) {
    actionError.value = e.message
  } finally {
    saving.value = false
  }
}
async function revokeSettlement(row) {
  if (!confirm(`确认撤销结算单 #${row.id} 并解锁对应记录？`)) return
  try {
    await api(`/salary/settlements/${row.id}/revoke`, { method: 'POST', body: {} })
    emit('toast', '结算单已撤销')
    loadAux()
  } catch (e) {
    emit('toast', e.message)
  }
}
async function showPayslip(row) {
  payslip.value = null
  payslipSettlementId.value = row.id
  actionError.value = ''
  dialog.value = 'payslip'
  try {
    payslip.value = await api(`/salary/settlements/${row.id}/payslip`)
  } catch (e) {
    actionError.value = e.message
  }
}
async function exportPayslip() {
  if (!payslipSettlementId.value || exportingPayslip.value) return
  exportingPayslip.value = true
  actionError.value = ''
  try {
    await download(`/salary/settlements/${payslipSettlementId.value}/payslip/export`)
    emit('toast', '薪资条 XLSX 已导出')
  } catch (e) {
    actionError.value = e.message
  } finally {
    exportingPayslip.value = false
  }
}
async function applyAction(row, action) {
  let reason = ''
  if (['reject', 'void'].includes(action)) {
    reason = prompt(action === 'reject' ? '请输入拒绝原因' : '请输入作废原因') || ''
    if (!reason) return
  } else if (!confirm(action === 'approve' ? '确认通过该结算申请？' : '确认发放工资并生成结算单？')) return
  const path = action === 'approve' || action === 'reject' ? 'review' : action
  try {
    await api(`/salary/settle-applies/${row.id}/${path}`, {
      method: 'PUT',
      body: { action, reason, payer: getSession().user?.real_name || '' },
    })
    emit('toast', '申请状态已更新')
    loadAux()
  } catch (e) {
    emit('toast', e.message)
  }
}
async function drillDown(styleId, processId = '') {
  drill.style_id = styleId
  drill.process_id = processId
  await loadAux()
}
async function backToProcesses() {
  drill.process_id = ''
  await loadAux()
}
function detailTotalAmount(data) {
  return Number(data?.summary?.amount || 0) + Number(data?.fixed?.amount || 0)
}
onMounted(load)
</script>

<template>
  <div class="salary-view">
    <div class="welcome salary-welcome">
      <div>
        <h2>{{ pageTitle }}</h2>
        <p>{{ pageDescription }}</p>
      </div>
      <div style="display:flex;gap:8px;flex-wrap:wrap">
        <button v-if="!orderSalaryMode && !completionMode" class="button secondary" @click="changeTab('changes')">与我相关的变更</button>
        <button v-if="!orderSalaryMode && !completionMode" class="button secondary" @click="openAction('apply')">申请结算</button>
        <template v-if="!personalMode && !orderSalaryMode && !completionMode">
          <button class="button secondary" @click="openAction('adjust')">工资调整</button>
          <button class="button primary" @click="openAction('settle')">发起结算</button>
        </template>
      </div>
    </div>

    <div v-if="!standaloneMode" class="filter-bar salary-tab-bar">
      <div class="segmented salary-main-tabs">
        <button
          v-for="t in [['overview','工资汇总'],['completion','完成情况'],['settlements','结算单'],['adjusts','调整记录'],['applies','结算申请'],['changes','我的变更'],['payments','工资实付']]"
          :key="t[0]"
          :class="{ active: tab === t[0] }"
          @click="changeTab(t[0])"
        >{{ t[1] }}</button>
      </div>
    </div>

    <form class="filter-bar salary-filters" @submit.prevent="load">
      <label class="field"><span>开始日期</span><input v-model="filters.date_from" type="date" /></label>
      <label class="field"><span>结束日期</span><input v-model="filters.date_to" type="date" /></label>
      <button class="button primary" style="align-self:flex-end">查询</button>
    </form>

    <template v-if="tab === 'overview'">
      <PageState :loading="loading" :error="error" @retry="load">
        <!-- 桌面保留原统计卡和表格 DOM。 -->
        <section v-if="personalMode" class="stats-grid salary-desktop">
          <article class="stat-card salary-highlight"><div class="stat-label">本期计件工资</div><div class="stat-value">{{ money(myStats.total) }}</div></article>
          <article class="stat-card"><div class="stat-label">本期完成工序量</div><div class="stat-value">{{ number(myStats.qty) }}</div><p class="stat-note">共 {{ myStats.tickets || 0 }} 扎</p></article>
          <article class="stat-card"><div class="stat-label">历史已结算</div><div class="stat-value">{{ money(mine.settled) }}</div></article>
          <article class="stat-card"><div class="stat-label">当前未结算</div><div class="stat-value">{{ money(mine.unsettled) }}</div></article>
        </section>
        <section v-else class="stats-grid salary-desktop">
          <article class="stat-card salary-highlight"><div class="stat-label">本期应付工资</div><div class="stat-value">{{ money(summary.total) }}</div><p class="stat-note">后端四类工资制合计</p></article>
          <article class="stat-card"><div class="stat-label">未结计件工资</div><div class="stat-value">{{ money(summary.unsettled?.amount) }}</div><p class="stat-note">{{ summary.unsettled?.users || 0 }} 人</p></article>
          <article class="stat-card"><div class="stat-label">未结工序量</div><div class="stat-value">{{ number(summary.unsettled?.qty) }}</div></article>
          <article class="stat-card"><div class="stat-label">固定工资</div><div class="stat-value">{{ money(summary.fixed_total) }}</div></article>
        </section>

        <div class="salary-mobile salary-overview-mobile">
          <section v-if="personalMode" class="card salary-summary-card">
            <div class="salary-duo">
              <div class="salary-duo-item"><span>已结</span><strong class="settled">{{ money(mine.settled) }}</strong></div>
              <div class="salary-duo-item"><span>未结</span><strong class="unsettled">{{ money(mine.unsettled) }}</strong></div>
            </div>
            <span v-if="mine.salary_type_display" class="salary-type-tag">{{ mine.salary_type_display }}</span>
            <div class="salary-trio">
              <div><span>计件工资</span><strong>{{ money(minePieceAmount) }}</strong></div>
              <div><span>固定工资</span><strong>{{ money(mine.fixed?.amount) }}</strong></div>
              <div><span>总工资</span><strong>{{ money(mineTotalAmount) }}</strong></div>
            </div>
            <div class="salary-summary-links">
              <span>完成工序数：{{ mine.summary?.processes || 0 }}</span>
              <span>无单价工序数：{{ mine.summary?.no_price_count || 0 }}</span>
            </div>
            <div class="salary-summary-actions two">
              <button type="button" @click="changeTab('applies')">申请记录</button>
              <button type="button" @click="openAction('apply')">发起申请</button>
            </div>
          </section>

          <section v-else class="card salary-summary-card">
            <div class="salary-duo">
              <div class="salary-duo-item"><span>已结</span><strong class="settled">{{ money(summary.settled) }}</strong></div>
              <div class="salary-duo-item"><span>未结</span><strong class="unsettled">{{ money(summary.unsettled?.amount) }}</strong></div>
            </div>
            <div class="salary-trio">
              <div><span>计件工资</span><strong>{{ money(summary.unsettled?.amount) }}</strong></div>
              <div><span>固定工资</span><strong>{{ money(summary.fixed_total) }}</strong></div>
              <div><span>总工资</span><strong>{{ money(summary.total) }}</strong></div>
            </div>
            <div class="salary-summary-links">
              <button type="button" @click="changeTab('completion')">完成工序数：{{ summary.unsettled?.processes || 0 }} <span>›</span></button>
              <span>无单价工序数：{{ summary.unsettled?.no_price_processes || 0 }}</span>
            </div>
            <div class="salary-summary-actions">
              <button type="button" @click="openAction('settle')">工资结算</button>
              <button type="button" @click="changeTab('settlements')">结算记录</button>
              <button type="button" @click="changeTab('applies')">申请记录</button>
            </div>
          </section>

          <template v-if="personalMode">
            <div class="segmented salary-mobile-main-tabs" aria-label="我的薪酬类型">
              <button v-for="item in [['pay','工资'],['count','计数'],['fixed','固定工资']]" :key="item[0]" type="button" :class="{ active: mineMainTab === item[0] }" @click="changeMineMainTab(item[0])">{{ item[1] }}</button>
            </div>
            <div v-if="mineMainTab !== 'fixed'" class="salary-sub-tabs" aria-label="薪酬汇总方式">
              <button v-for="item in [['date','按日期查看'],['bed','按裁床查看'],['style','汇总查看']]" :key="item[0]" type="button" :class="{ active: mineSubTab === item[0] }" @click="mineSubTab = item[0]">{{ item[1] }}</button>
            </div>
            <section v-if="mineMainTab !== 'fixed'" class="card salary-period-summary">
              <span>期间 {{ number(mine.summary?.qty) }} 件 / {{ number(mine.summary?.ticket_count) }} 扎</span>
              <strong v-if="mineMainTab === 'pay'">{{ money(mine.summary?.amount) }}</strong>
            </section>

            <section v-if="mineMainTab !== 'fixed' && mineSubTab === 'date'" class="card salary-mobile-list-card">
              <div v-if="!mine.by_date?.length" class="salary-mobile-empty">{{ mineMainTab === 'pay' ? '暂无工资明细' : '暂无完成计数' }}</div>
              <div v-for="row in mine.by_date" :key="row.date" class="salary-date-row">
                <div><strong>{{ row.date }}</strong><span>{{ number(row.qty) }} 件 / {{ number(row.tickets) }} 扎</span></div>
                <strong v-if="mineMainTab === 'pay'" class="salary-amount">{{ money(row.amount) }}</strong>
              </div>
            </section>

            <div v-else-if="mineMainTab !== 'fixed' && mineSubTab === 'bed'" class="salary-mobile-card-list">
              <div v-if="!mine.by_bed?.length" class="card salary-mobile-empty">该期间没有扫菲登记</div>
              <article v-for="row in mine.by_bed" :key="row.production_id" class="card salary-bed-card">
                <div class="salary-card-heading">
                  <div><strong>床次：{{ row.bed_no }}</strong><span>款号：{{ row.style_no }} {{ row.style_name }}</span></div>
                  <div v-if="mineMainTab === 'pay'" class="salary-heading-amount"><strong>{{ money(row.amount) }}</strong><span>{{ Number(row.settled || 0) > 0 ? '已结算' : '未结算' }}</span></div>
                </div>
                <div class="salary-bed-grid" :class="{ count: mineMainTab === 'count' }">
                  <div><strong>{{ number(row.bed_total) }}</strong><span>总件数</span></div>
                  <div><strong>{{ number(row.qty) }}</strong><span>完成件数</span></div>
                  <template v-if="mineMainTab === 'pay'">
                    <div><strong>{{ money(row.amount) }}</strong><span>总金额</span></div>
                    <div><strong>{{ money(row.settled) }}</strong><span>已结算</span></div>
                  </template>
                </div>
              </article>
            </div>

            <div v-else-if="mineMainTab !== 'fixed'" class="salary-mobile-card-list">
              <div v-if="!mine.by_style?.length" class="card salary-mobile-empty">该期间没有扫菲登记</div>
              <article v-for="row in mine.by_style" :key="row.style_id" class="card salary-style-card">
                <div class="salary-card-heading"><div><strong>款号：{{ row.style_no }}</strong><span>名称：{{ row.style_name || '—' }}</span></div></div>
                <div class="salary-style-grid" :class="{ count: mineMainTab === 'count' }">
                  <div><span>完成件数</span><strong>{{ number(row.qty) }}</strong></div>
                  <template v-if="mineMainTab === 'pay'">
                    <div><span>平均单价</span><strong>{{ money(row.avg_price) }}</strong></div>
                    <div><span>金额</span><strong class="salary-amount">{{ money(row.amount) }}</strong></div>
                  </template>
                </div>
              </article>
            </div>

            <section v-else class="card salary-fixed-card">
              <div v-if="!mine.fixed" class="salary-mobile-empty">未设置固定工资<span>请联系管理员在员工管理中配置</span></div>
              <template v-else>
                <div class="salary-definition-row"><span>固定工资</span><strong>{{ money(mine.fixed.fixed_salary) }} / {{ mine.fixed.fixed_type_display }}</strong></div>
                <div class="salary-definition-row"><span>期间出勤</span><strong>{{ number(mine.fixed.worked_days) }} 天</strong></div>
                <div class="salary-definition-row"><span>计薪月数</span><strong>{{ number(mine.fixed.months) }} 个月</strong></div>
                <div class="salary-definition-row"><span>期间固定工资</span><strong class="salary-amount">{{ money(mine.fixed.amount) }}</strong></div>
              </template>
            </section>
          </template>

          <template v-else>
            <div class="salary-section-heading"><h3>选择员工</h3><span>{{ employees.length }} 人</span></div>
            <div v-if="!employees.length" class="card salary-mobile-empty">暂无员工</div>
            <button v-for="e in employees" :key="e.user_id" type="button" class="card salary-employee-card" @click="open(e)">
              <div class="salary-employee-title">
                <div><strong>{{ e.real_name }}</strong><span>{{ e.post_name || '未设岗位' }} · 本期完成 {{ number(e.done_qty) }} 件</span></div>
                <span v-if="e.status === 2" class="badge">{{ e.status_display }}</span>
                <span v-else class="salary-type-tag">{{ e.salary_type_display }}</span>
              </div>
              <div class="salary-duo">
                <div class="salary-duo-item"><span>已结</span><strong class="settled">{{ money(e.settled) }}</strong></div>
                <div class="salary-duo-item"><span>未结</span><strong class="unsettled">{{ money(e.unsettled) }}</strong></div>
              </div>
              <div class="salary-trio">
                <div><span>计件工资</span><strong>{{ money(e.piece_amount) }}</strong></div>
                <div><span>固定工资</span><strong>{{ money(e.fixed_amount) }}</strong></div>
                <div><span>总工资</span><strong>{{ money(e.total_amount) }}</strong></div>
              </div>
              <div class="salary-employee-stats"><span>完成工序数：{{ e.process_count || 0 }}</span><span>无单价工序数：{{ e.no_price_count || 0 }}</span><AppIcon name="chevron" :size="16" /></div>
            </button>
          </template>
        </div>

        <section class="card table-card salary-desktop">
          <div class="table-wrap">
            <table>
              <thead><tr><th>{{ personalMode ? '工序' : '员工' }}</th><th class="numeric">数量</th><th class="numeric">计件金额</th><th v-if="!personalMode" class="numeric">固定 / 合计</th><th class="numeric">当前未结</th><th v-if="!personalMode"></th></tr></thead>
              <tbody v-if="personalMode">
                <tr v-for="row in mine.by_process" :key="row.process_id"><td>{{ row.name }}</td><td class="numeric">{{ number(row.qty) }}</td><td class="numeric">{{ money(row.amount) }}</td><td class="numeric">—</td></tr>
                <tr v-if="!mine.by_process?.length"><td colspan="4" class="page-state">暂无记录</td></tr>
              </tbody>
              <tbody v-else>
                <tr v-for="e in employees" :key="e.user_id" class="clickable" @click="open(e)"><td><span class="cell-title">{{ e.real_name }}</span><small class="cell-subtitle">{{ e.post_name || '未设岗位' }}</small></td><td class="numeric">{{ number(e.done_qty) }}</td><td class="numeric">{{ money(e.piece_amount) }}</td><td class="numeric">{{ money(e.fixed_amount) }} / {{ money(e.total_amount) }}</td><td class="numeric">{{ money(e.unsettled) }}</td><td><AppIcon name="chevron" :size="16" /></td></tr>
              </tbody>
            </table>
          </div>
        </section>
      </PageState>
    </template>

    <template v-else>
      <PageState :loading="auxLoading" :error="auxError" @retry="loadAux">
        <!-- 实付工资使用独立筛选与分页，日期沿用薪酬页期间。 -->
        <SalaryPayments v-if="tab === 'payments'" :date-from="filters.date_from" :date-to="filters.date_to" @toast="emit('toast', $event)" />

        <!-- 结算单：桌面表格 / 手机独立卡。 -->
        <template v-if="tab === 'settlements'">
          <section class="card table-card salary-desktop">
            <div class="table-wrap"><table><thead><tr><th>结算单</th><th>期间</th><th class="numeric">人数</th><th class="numeric">金额</th><th>状态</th><th>操作</th></tr></thead><tbody><tr v-for="row in rows(auxData)" :key="row.id"><td>#{{ row.id }}<small class="cell-subtitle">{{ row.user_name || '全员' }} · {{ row.created_by_name }}</small></td><td>{{ row.period_start }} 至 {{ row.period_end }}</td><td class="numeric">{{ row.employee_count }}</td><td class="numeric">{{ money(row.amount) }}</td><td><span class="badge" :class="row.status">{{ row.status_display }}</span></td><td><button class="button ghost small" @click="showPayslip(row)">薪资条</button><button v-if="row.status === 'settled'" class="button ghost small" @click="revokeSettlement(row)">撤销</button></td></tr></tbody></table></div>
          </section>
          <div class="salary-mobile salary-mobile-card-list">
            <div v-if="!rows(auxData).length" class="card salary-mobile-empty">暂无结算记录</div>
            <article v-for="row in rows(auxData)" :key="row.id" class="card salary-record-card salary-settlement-card">
              <div class="salary-record-head"><div><strong>{{ money(row.amount) }}</strong><span>结算单 #{{ row.id }} · {{ row.user_name || '全员' }}</span></div><span class="badge" :class="row.status">{{ row.status_display }}</span></div>
              <div class="salary-record-fields">
                <div class="salary-definition-row"><span>结算期间</span><strong>{{ row.period_start }} 至 {{ row.period_end }}</strong></div>
                <div class="salary-definition-row"><span>员工数</span><strong>{{ row.employee_count }} 人</strong></div>
                <div class="salary-definition-row"><span>操作人</span><strong>{{ row.created_by_name || '—' }}</strong></div>
                <div v-if="row.created_at" class="salary-definition-row"><span>结算时间</span><strong>{{ dateTime(row.created_at) }}</strong></div>
              </div>
              <div class="salary-card-actions"><button class="button secondary small" @click="showPayslip(row)">薪资条</button><button v-if="row.status === 'settled'" class="button secondary small danger-text" @click="revokeSettlement(row)">撤销</button></div>
            </article>
          </div>
        </template>

        <!-- 调整记录：手机采用与“与我相关的变更”一致的审计卡。 -->
        <template v-else-if="tab === 'adjusts'">
          <section class="card table-card salary-desktop">
            <div class="table-wrap"><table><thead><tr><th>时间</th><th>员工</th><th class="numeric">调整金额</th><th>原因</th><th>操作人</th></tr></thead><tbody><tr v-for="row in rows(auxData)" :key="row.id"><td>{{ dateTime(row.created_at) }}</td><td>{{ row.user_name }}</td><td class="numeric">{{ money(row.amount) }}</td><td>{{ row.reason }}</td><td>{{ row.operator_name }}</td></tr></tbody></table></div>
          </section>
          <div class="salary-mobile salary-mobile-card-list">
            <div v-if="!rows(auxData).length" class="card salary-mobile-empty">暂无工资调整记录</div>
            <article v-for="row in rows(auxData)" :key="row.id" class="card salary-change-card">
              <div class="salary-record-head"><span class="badge">工资调整</span><time>{{ dateTime(row.created_at) }}</time></div>
              <div class="salary-change-employee"><strong>{{ row.user_name }}</strong><strong class="salary-amount">{{ money(row.amount) }}</strong></div>
              <p>{{ row.reason }}</p>
              <span class="salary-muted">操作人：{{ row.operator_name || '—' }}</span>
            </article>
          </div>
        </template>

        <!-- 个人审计与小程序 changes 共用 GET /audit-logs?scope=mine，覆盖全部变更类型。 -->
        <template v-else-if="tab === 'changes'">
          <section class="card table-card salary-desktop">
            <div class="card-header" style="padding:18px 18px 0"><h3>与我相关的变更</h3></div>
            <div class="table-wrap"><table><thead><tr><th>时间</th><th>类型</th><th>变更内容</th><th>操作人</th></tr></thead><tbody>
              <tr v-for="row in rows(auxData)" :key="row.id"><td>{{ dateTime(row.created_at) }}</td><td><span class="badge">{{ row.type_display || '其他' }}</span></td><td>{{ row.message }}</td><td>{{ row.operator_name || '—' }}</td></tr>
              <tr v-if="!rows(auxData).length"><td colspan="4" class="page-state">暂无与我相关的变更</td></tr>
            </tbody></table></div>
          </section>
          <div class="salary-mobile salary-mobile-card-list">
            <div v-if="!rows(auxData).length" class="card salary-mobile-empty">暂无与我相关的变更<span>涉及你的登记、价格与撤销记录都会在这里留痕</span></div>
            <article v-for="row in rows(auxData)" :key="row.id" class="card salary-change-card">
              <div class="salary-record-head"><span class="badge">{{ row.type_display || '其他' }}</span><time>{{ dateTime(row.created_at) }}</time></div>
              <p>{{ row.message }}</p>
              <span class="salary-muted">操作人：{{ row.operator_name || '—' }}</span>
            </article>
          </div>
        </template>

        <!-- 申请审核、发放与作废条件与桌面完全一致。 -->
        <template v-else-if="tab === 'applies'">
          <section class="card table-card salary-desktop">
            <div class="table-wrap"><table><thead><tr><th>员工 / 期间</th><th class="numeric">申请金额</th><th>状态</th><th>说明</th><th>操作</th></tr></thead><tbody><tr v-for="row in rows(auxData)" :key="row.id"><td>{{ row.user_name }}<small class="cell-subtitle">{{ row.period_start }} 至 {{ row.period_end }}</small></td><td class="numeric">{{ money(row.amount) }}</td><td><span class="badge producing">{{ row.status_display }}</span><small class="cell-subtitle">{{ row.reviewer_hint }}</small></td><td>{{ row.note || row.reject_reason || row.void_reason || '—' }}</td><td><template v-if="row.status === 0"><button class="button ghost small" @click="applyAction(row,'approve')">通过</button><button class="button ghost small" @click="applyAction(row,'reject')">拒绝</button></template><button v-if="row.status === 1" class="button ghost small" @click="applyAction(row,'pay')">发放</button><button v-if="[0,1].includes(row.status)" class="button ghost small" @click="applyAction(row,'void')">作废</button></td></tr></tbody></table></div>
          </section>
          <div class="salary-mobile salary-mobile-card-list">
            <div v-if="!rows(auxData).length" class="card salary-mobile-empty">暂无申请记录</div>
            <article v-for="row in rows(auxData)" :key="row.id" class="card salary-record-card salary-apply-card">
              <div class="salary-record-head"><div><strong>申请结算</strong><span>{{ row.user_name }}<template v-if="row.created_at"> · {{ dateTime(row.created_at) }}</template></span></div><span class="badge producing">{{ row.status_display }}</span></div>
              <div class="salary-apply-amount"><span>申请金额</span><strong>{{ money(row.amount) }}</strong></div>
              <div class="salary-definition-row"><span>申请期间</span><strong>{{ row.period_start }} 至 {{ row.period_end }}</strong></div>
              <p v-if="row.reviewer_hint" class="salary-review-hint">{{ row.reviewer_hint }}</p>
              <p v-if="row.note || row.reject_reason || row.void_reason" class="salary-record-note">说明：{{ row.note || row.reject_reason || row.void_reason }}</p>
              <div v-if="[0,1].includes(row.status)" class="salary-card-actions">
                <template v-if="row.status === 0"><button class="button primary small" @click="applyAction(row,'approve')">通过</button><button class="button secondary small" @click="applyAction(row,'reject')">拒绝</button></template>
                <button v-if="row.status === 1" class="button primary small" @click="applyAction(row,'pay')">发放</button>
                <button v-if="[0,1].includes(row.status)" class="button secondary small danger-text" @click="applyAction(row,'void')">作废</button>
              </div>
            </article>
          </div>
        </template>

        <!-- 完成情况：桌面三级表格 / 手机款式卡、工序卡、矩阵和员工卡。 -->
        <template v-else-if="tab === 'completion'">
          <div class="filter-bar salary-completion-view-bar">
            <div class="segmented" aria-label="完成汇总查看方式">
              <button type="button" :class="{ active: completionView === 'style' }" :aria-pressed="completionView === 'style'" @click="changeCompletionView('style')">款式钻取</button>
              <button type="button" :class="{ active: completionView === 'dim' }" :aria-pressed="completionView === 'dim'" @click="changeCompletionView('dim')">维度汇总</button>
            </div>
          </div>
          <template v-if="completionView === 'style'">
          <div class="filter-bar salary-completion-filters">
            <label class="field"><span>员工</span><select v-model="completionFilters.user_id" aria-label="完成汇总员工" @change="changeCompletionFilters"><option value="">全部员工</option><option v-for="e in employees" :key="e.user_id" :value="e.user_id">{{ e.real_name }}</option></select></label>
            <label class="field"><span>客户</span><select v-model="completionFilters.customer_id" aria-label="完成汇总客户" @change="changeCompletionFilters"><option value="">全部客户</option><option v-for="customer in completionCustomers" :key="customer.id" :value="customer.id">{{ customer.name }}</option></select></label>
          </div>
          <section class="card table-card salary-desktop">
            <div class="card-header" style="padding:18px 18px 0"><h3>完成情况三级钻取</h3><button v-if="drill.style_id" class="button ghost small" @click="drillDown('')">返回款式</button></div>
            <div class="table-wrap">
              <table v-if="!drill.style_id"><thead><tr><th>款式</th><th class="numeric">工序量</th><th class="numeric">均价</th><th class="numeric">金额</th><th class="numeric">扎数 / 工序数</th></tr></thead><tbody><tr v-for="row in completion.styles" :key="row.style_id" class="clickable" @click="drillDown(row.style_id)"><td>{{ row.style_no }} · {{ row.style_name }}</td><td class="numeric">{{ number(row.qty) }}</td><td class="numeric">{{ money(row.avg_price) }}</td><td class="numeric">{{ money(row.amount) }}</td><td class="numeric">{{ row.ticket_count }} / {{ row.process_count }}</td></tr></tbody></table>
              <table v-else-if="!drill.process_id"><thead><tr><th>工序</th><th class="numeric">数量</th><th class="numeric">均价 / 金额</th><th class="numeric">扎数 / 人数</th></tr></thead><tbody><tr v-for="row in completion.processes" :key="row.process_id" class="clickable" @click="drillDown(drill.style_id,row.process_id)"><td>{{ row.name }}</td><td class="numeric">{{ number(row.qty) }}</td><td class="numeric">{{ money(row.avg_price) }} / {{ money(row.amount) }}</td><td class="numeric">{{ row.ticket_count }} / {{ row.users }}</td></tr></tbody></table>
              <table v-else><thead><tr><th>床次 / 扎号</th><th>色码</th><th class="numeric">数量</th><th class="numeric">金额</th></tr></thead><tbody><tr v-for="row in completion.tickets" :key="row.ticket_id"><td>{{ row.bed_no }} / {{ row.bundle_no }}</td><td>{{ row.color }} / {{ row.size }}</td><td class="numeric">{{ row.qty }}</td><td class="numeric">{{ money(row.amount) }}</td></tr></tbody></table>
            </div>
            <div v-if="drill.process_id" style="padding:18px"><h3>员工汇总</h3><p v-for="e in completion.employees" :key="e.user_id">{{ e.user_name }}：{{ e.qty }} 件 · {{ money(e.amount) }}</p></div>
          </section>

          <div class="salary-mobile salary-completion-mobile">
            <section class="card salary-completion-head"><span>完成总数（件）</span><strong>{{ number(completionTotalQty) }}</strong></section>
            <div v-if="drill.style_id" class="salary-breadcrumb">
              <button type="button" @click="drillDown('')">‹ 款式列表</button>
              <strong>{{ currentCompletionStyle?.style_no }}</strong>
            </div>
            <div v-if="drill.process_id" class="salary-breadcrumb">
              <button type="button" @click="backToProcesses">‹ 工序列表</button>
              <span>工序：{{ currentCompletionProcess?.name }}</span>
            </div>

            <div v-if="!drill.style_id" class="salary-mobile-card-list">
              <div v-if="!completion.styles?.length" class="card salary-mobile-empty">该期间暂无完成登记<span>切换日期后重试</span></div>
              <button v-for="row in completion.styles" :key="row.style_id" type="button" class="card salary-completion-card" @click="drillDown(row.style_id)">
                <div class="salary-record-head"><div><strong>款号：{{ row.style_no }}</strong><span>{{ row.style_name || '—' }}</span></div><span class="salary-qty-badge">{{ number(row.qty) }} 件</span></div>
                <div class="salary-completion-numbers"><span>均价：{{ money(row.avg_price) }}</span><strong>总额：{{ money(row.amount) }}</strong></div>
                <div class="salary-completion-foot"><span>{{ row.process_count }} 道工序 · {{ row.ticket_count }} 扎</span><span>查看工序 ›</span></div>
              </button>
            </div>

            <div v-else-if="!drill.process_id" class="salary-mobile-card-list">
              <div v-if="!completion.processes?.length" class="card salary-mobile-empty">该款式暂无完成登记</div>
              <button v-for="row in completion.processes" :key="row.process_id" type="button" class="card salary-completion-card" @click="drillDown(drill.style_id,row.process_id)">
                <div class="salary-record-head"><strong>工序：{{ row.name }}</strong><span class="salary-process-qty">数量：{{ number(row.qty) }} ›</span></div>
                <div class="salary-completion-numbers"><span>均价：{{ money(row.avg_price) }} · {{ row.users }} 人参与</span><strong>{{ money(row.amount) }}</strong></div>
                <div class="salary-completion-foot"><span>{{ row.ticket_count }} 扎</span><span>查看颜色尺码与员工工资</span></div>
              </button>
            </div>

            <template v-else>
              <section class="card salary-matrix-card">
                <h3>颜色 × 尺码</h3>
                <div v-if="!completion.matrix?.length" class="salary-mobile-empty">暂无矩阵数据</div>
                <div v-for="row in completion.matrix" :key="row.color" class="salary-matrix-row">
                  <strong>{{ row.color }}</strong>
                  <div><span v-for="size in row.sizes" :key="size.size">{{ size.size }} <b>{{ number(size.qty) }}</b></span></div>
                </div>
              </section>
              <section class="salary-mobile-card-list salary-completion-employees">
                <div class="salary-section-heading"><h3>逐员工工资</h3><span>{{ completion.employees?.length || 0 }} 人</span></div>
                <div v-if="!completion.employees?.length" class="card salary-mobile-empty">暂无员工明细</div>
                <article v-for="employee in completion.employees" :key="employee.user_id" class="card salary-completion-employee">
                  <strong>{{ employee.user_name }}</strong>
                  <div class="salary-trio">
                    <div><span>数量</span><strong>{{ number(employee.qty) }}</strong></div>
                    <div><span>平均单价</span><strong>{{ money(employee.avg_price) }}</strong></div>
                    <div><span>金额</span><strong class="salary-amount">{{ money(employee.amount) }}</strong></div>
                  </div>
                </article>
              </section>
              <section class="salary-mobile-card-list salary-completion-tickets">
                <div class="salary-section-heading"><h3>菲票明细</h3><span>{{ completion.tickets?.length || 0 }} 条</span></div>
                <div v-if="!completion.tickets?.length" class="card salary-mobile-empty">暂无菲票明细</div>
                <article v-for="row in completion.tickets" :key="row.ticket_id" class="card salary-ticket-detail-card">
                  <div class="salary-record-head"><strong>床次 {{ row.bed_no }} / 扎号 {{ row.bundle_no }}</strong><strong class="salary-amount">{{ money(row.amount) }}</strong></div>
                  <div class="salary-ticket-meta"><span>{{ row.color }} / {{ row.size }}</span><span>{{ number(row.qty) }} 件</span><span>单价 {{ money(row.price) }}</span></div>
                </article>
              </section>
            </template>
          </div>
          </template>
          <SalaryDimensions
            v-else
            ref="dimensionTable"
            :date-from="filters.date_from"
            :date-to="filters.date_to"
            @navigate="navigateFromDimensions"
            @toast="emit('toast', $event)"
          />
        </template>
      </PageState>
    </template>

    <Teleport to="body">
      <div v-if="detail" class="modal-backdrop" @click.self="detail = null">
        <section class="modal salary-modal">
          <div class="modal-header"><h2>员工薪酬明细</h2><button class="icon-button" @click="detail = null"><AppIcon name="close" /></button></div>
          <div class="salary-desktop">
            <div class="detail-grid"><div class="detail-item"><small>本期工序量</small><strong>{{ number(detail.summary?.qty) }}</strong></div><div class="detail-item"><small>本期计件金额</small><strong>{{ money(detail.summary?.amount) }}</strong></div><div class="detail-item"><small>当前未结</small><strong>{{ money(detail.unsettled) }}</strong></div></div>
            <table><thead><tr><th>工序</th><th class="numeric">数量</th><th class="numeric">金额</th></tr></thead><tbody><tr v-for="row in detail.by_process" :key="row.process_id"><td>{{ row.name }}</td><td class="numeric">{{ row.qty }}</td><td class="numeric">{{ money(row.amount) }}</td></tr></tbody></table>
          </div>
          <div class="salary-mobile salary-detail-mobile">
            <section class="salary-summary-card">
              <div class="salary-duo">
                <div class="salary-duo-item"><span>已结</span><strong class="settled">{{ money(detail.settled) }}</strong></div>
                <div class="salary-duo-item"><span>未结</span><strong class="unsettled">{{ money(detail.unsettled) }}</strong></div>
              </div>
              <div class="salary-trio">
                <div><span>计件工资</span><strong>{{ money(detail.summary?.amount) }}</strong></div>
                <div><span>固定工资</span><strong>{{ money(detail.fixed?.amount) }}</strong></div>
                <div><span>总工资</span><strong>{{ money(detailTotalAmount(detail)) }}</strong></div>
              </div>
              <div class="salary-summary-links"><span>完成工序数：{{ detail.summary?.processes || 0 }}</span><span>无单价工序数：{{ detail.summary?.no_price_count || 0 }}</span></div>
            </section>
            <section class="salary-detail-period"><span>本期完成</span><strong>{{ number(detail.summary?.qty) }} 件 / {{ number(detail.summary?.ticket_count) }} 扎</strong></section>
            <div class="salary-mobile-card-list">
              <div v-if="!detail.by_process?.length" class="salary-mobile-empty">暂无工序明细</div>
              <article v-for="row in detail.by_process" :key="row.process_id" class="salary-detail-process">
                <div><strong>{{ row.name }}</strong><span>{{ number(row.qty) }} 件</span></div><strong class="salary-amount">{{ money(row.amount) }}</strong>
              </article>
            </div>
            <section v-if="detail.fixed" class="salary-detail-fixed">
              <h3>固定工资</h3>
              <div class="salary-definition-row"><span>工资标准</span><strong>{{ money(detail.fixed.fixed_salary) }} / {{ detail.fixed.fixed_type_display }}</strong></div>
              <div class="salary-definition-row"><span>出勤 / 计薪</span><strong>{{ number(detail.fixed.worked_days) }} 天 / {{ number(detail.fixed.months) }} 个月</strong></div>
              <div class="salary-definition-row"><span>期间固定工资</span><strong class="salary-amount">{{ money(detail.fixed.amount) }}</strong></div>
            </section>
          </div>
        </section>
      </div>

      <div v-if="dialog" class="modal-backdrop" @click.self="dialog = null">
        <section class="modal salary-modal">
          <div class="modal-header"><h2>{{ { settle:'发起工资结算', adjust:'新增工资调整', apply:'申请结算', payslip:'薪资条' }[dialog] }}</h2><div class="payslip-modal-actions"><button v-if="dialog === 'payslip' && payslip" class="button secondary small" :disabled="exportingPayslip" @click="exportPayslip"><span v-if="exportingPayslip" class="spinner" />{{ exportingPayslip ? '导出中…' : '导出 XLSX' }}</button><button class="icon-button" aria-label="关闭" @click="dialog = null"><AppIcon name="close" /></button></div></div>
          <form v-if="dialog !== 'payslip'" @submit.prevent="submitAction">
            <p v-if="dialog !== 'adjust'">结算期间：{{ filters.date_from }} 至 {{ filters.date_to }}</p>
            <label v-if="dialog === 'settle'" class="field"><span>结算员工</span><select v-model="actionForm.user_id"><option value="">全部有可结工资员工</option><option v-for="e in employees" :key="e.user_id" :value="e.user_id">{{ e.real_name }}</option></select></label>
            <template v-if="dialog === 'adjust'">
              <label class="field"><span>员工</span><select v-model="actionForm.user_id" required><option value="">选择员工</option><option v-for="e in employees" :key="e.user_id" :value="e.user_id">{{ e.real_name }}</option></select></label>
              <label class="field" style="margin-top:12px"><span>金额（正数增加，负数扣减）</span><input v-model="actionForm.amount" type="number" step="0.01" required /></label>
            </template>
            <label v-if="dialog !== 'settle'" class="field" style="margin-top:12px"><span>{{ dialog === 'adjust' ? '调整原因' : '申请备注' }}</span><textarea v-model="actionForm[dialog === 'adjust' ? 'reason' : 'note']" :required="dialog === 'adjust'" /></label>
            <p v-if="actionError" class="form-error">{{ actionError }}</p>
            <div class="form-actions"><button type="button" class="button secondary" @click="dialog = null">取消</button><button class="button primary" :disabled="saving">确认提交</button></div>
          </form>
          <template v-else>
            <div v-if="!payslip" class="page-state"><span class="spinner" />正在读取薪资条</div>
            <template v-else>
              <div class="salary-desktop salary-payslip-desktop">
                <p>{{ payslip.settlement.period_start }} 至 {{ payslip.settlement.period_end }} · 合计 {{ money(payslip.total) }}</p>
                <div class="table-wrap"><table><thead><tr><th>员工</th><th class="numeric">金额</th><th>工序明细</th></tr></thead><tbody><tr v-for="row in payslip.rows" :key="row.user_id"><td>{{ row.name }}</td><td class="numeric">{{ money(row.amount) }}</td><td><span v-for="item in row.items" :key="item.process" style="display:block">{{ item.process }} {{ item.qty }} 件 / {{ money(item.amount) }}</span></td></tr></tbody></table></div>
              </div>
              <div class="salary-mobile salary-payslip-mobile">
                <section class="salary-payslip-head">
                  <div><strong>{{ money(payslip.total) }}</strong><span>{{ payslip.settlement.period_start }} 至 {{ payslip.settlement.period_end }} · {{ payslip.settlement.employee_count }} 人</span></div>
                  <span class="badge" :class="payslip.settlement.status">{{ payslip.settlement.status_display }}</span>
                </section>
                <div v-if="!payslip.rows.length" class="salary-mobile-empty">该结算单无登记明细<span>可能为固定工资或已撤销的单据</span></div>
                <article v-for="row in payslip.rows" :key="row.user_id" class="salary-slip-card">
                  <div class="salary-slip-head"><strong>{{ row.name }}</strong><strong>{{ money(row.amount) }}</strong></div>
                  <span v-if="row.snapshot_amount !== null && row.snapshot_amount !== undefined" class="salary-snapshot-tag">金额为结算快照</span>
                  <div v-for="(item, index) in row.items" :key="`${item.process}-${index}`" class="salary-slip-item"><strong>{{ item.process }}</strong><span>{{ number(item.qty) }} 件</span><b>{{ money(item.amount) }}</b></div>
                </article>
              </div>
            </template>
            <p v-if="actionError" class="form-error">{{ actionError }}</p>
          </template>
        </section>
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
.salary-mobile { display: none; }
.payslip-modal-actions { display: flex; align-items: center; gap: 8px; }
.payslip-modal-actions .spinner { width: 13px; height: 13px; }
.salary-completion-view-bar { justify-content: flex-start; }
.salary-completion-view-bar .segmented { display: flex; }
.salary-completion-filters { justify-content: flex-start; margin-bottom: 12px; }
.salary-completion-filters .field { min-width: 180px; }
.salary-sub-tabs,
.salary-mobile-main-tabs { display: flex; }
.salary-sub-tabs button,
.salary-mobile-main-tabs button { border: 0; }
.salary-duo,
.salary-trio,
.salary-summary-links,
.salary-summary-actions,
.salary-record-head,
.salary-card-heading,
.salary-employee-title,
.salary-employee-stats,
.salary-definition-row,
.salary-card-actions,
.salary-completion-numbers,
.salary-completion-foot,
.salary-breadcrumb,
.salary-ticket-meta,
.salary-slip-head,
.salary-slip-item { display: flex; align-items: center; }
.salary-duo-item,
.salary-trio > div { min-width: 0; }

@media (max-width: 760px) {
  .salary-view .salary-desktop,
  .salary-modal .salary-desktop { display: none !important; }
  .salary-view .salary-mobile,
  .salary-modal .salary-mobile { display: block; }
  .salary-view { min-width: 0; color: var(--ink); }
  .salary-tab-bar { margin-bottom: 12px; }
  .salary-main-tabs {
    display: grid;
    grid-template-columns: repeat(7, minmax(0, 1fr));
    gap: 3px;
    width: 100%;
    padding: 4px;
    overflow: visible;
    border-radius: 12px;
    background: #e9ecef;
  }
  .salary-main-tabs button {
    min-width: 0;
    min-height: 36px;
    padding: 7px 2px;
    border-radius: 9px;
    font-size: 11px;
    white-space: nowrap;
  }
  .salary-main-tabs button.active,
  .salary-mobile-main-tabs button.active { background: var(--orange); color: #fff; box-shadow: none; }
  .salary-filters {
    margin-bottom: 12px;
    padding: 13px;
    border-radius: 12px;
    background: #fff;
    box-shadow: 0 2px 8px rgba(31,35,41,.06);
  }
  .salary-filters .field { gap: 5px; font-size: 11px; }
  .salary-filters input { min-width: 0; padding: 8px 5px; font-size: 12px; }
  .salary-filters > .button { min-height: 38px; }
  .salary-overview-mobile,
  .salary-completion-mobile { min-width: 0; }
  .salary-summary-card { position: relative; margin-bottom: 12px; padding: 18px 14px 0; overflow: hidden; }
  .salary-duo { position: relative; align-items: stretch; }
  .salary-duo-item { flex: 1; display: flex; flex-direction: column; align-items: center; gap: 5px; text-align: center; }
  .salary-duo-item + .salary-duo-item { border-left: 1px solid var(--line); }
  .salary-duo-item > span { color: var(--text-secondary); font-size: 13px; font-weight: 600; }
  .salary-duo-item > strong { max-width: 100%; font-size: clamp(17px, 5vw, 22px); line-height: 1.2; font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }
  .salary-duo-item > strong.settled { color: var(--good); }
  .salary-duo-item > strong.unsettled { color: var(--orange); }
  .salary-type-tag { display: inline-flex; margin-top: 9px; padding: 3px 8px; border-radius: 6px; background: var(--orange-soft); color: var(--orange-dark); font-size: 10px; font-weight: 500; }
  .salary-summary-card > .salary-type-tag { position: absolute; top: 8px; left: 50%; margin: 0; transform: translateX(-50%); }
  .salary-trio { margin-top: 16px; padding-top: 15px; border-top: 1px dashed var(--line); }
  .salary-trio > div { flex: 1; display: flex; flex-direction: column; align-items: center; gap: 6px; text-align: center; }
  .salary-trio span { color: var(--muted); font-size: 11px; }
  .salary-trio strong { max-width: 100%; font-size: 14px; line-height: 1.25; font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }
  .salary-summary-links { margin-top: 15px; padding: 13px 0; border-top: 1px dashed var(--line); color: var(--muted); font-size: 11px; }
  .salary-summary-links > span,
  .salary-summary-links > button { flex: 1; min-width: 0; text-align: center; }
  .salary-summary-links > * + * { border-left: 1px solid var(--line); }
  .salary-summary-links button { padding: 0; border: 0; background: transparent; color: var(--orange-dark); font-size: inherit; }
  .salary-summary-actions { margin: 0 -14px; border-top: 1px solid var(--line); }
  .salary-summary-actions button { flex: 1; min-width: 0; min-height: 44px; padding: 10px 4px; border: 0; background: transparent; color: var(--text-primary); font-size: 12px; }
  .salary-summary-actions button + button { border-left: 1px solid var(--line); }
  .salary-summary-actions button:active { background: var(--orange-soft); }
  .salary-mobile-main-tabs { gap: 3px; margin: 12px 0; padding: 4px; border-radius: 12px; background: #e9ecef; }
  .salary-mobile-main-tabs button { flex: 1; min-width: 0; min-height: 34px; padding: 7px 3px; border-radius: 9px; background: transparent; color: var(--text-secondary); font-size: 12px; }
  .salary-sub-tabs { gap: 8px; margin-bottom: 12px; overflow-x: auto; scrollbar-width: none; }
  .salary-sub-tabs::-webkit-scrollbar { display: none; }
  .salary-sub-tabs button { flex: 1 0 auto; min-height: 32px; padding: 6px 13px; border-radius: 999px; background: #e9ecef; color: var(--text-secondary); font-size: 11px; white-space: nowrap; }
  .salary-sub-tabs button.active { background: var(--orange-soft); color: var(--orange-dark); font-weight: 600; }
  .salary-period-summary { display: flex; align-items: center; justify-content: space-between; gap: 10px; margin-bottom: 12px; padding: 13px 15px; font-size: 12px; }
  .salary-period-summary > span { color: var(--muted); font-variant-numeric: tabular-nums; }
  .salary-period-summary > strong { color: var(--orange); font-size: 16px; }
  .salary-mobile-list-card { padding: 3px 15px; }
  .salary-date-row { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 15px 0; border-bottom: 1px solid var(--line); }
  .salary-date-row:last-child { border-bottom: 0; }
  .salary-date-row > div { display: flex; flex-direction: column; gap: 6px; }
  .salary-date-row > div > strong { font-size: 13px; font-variant-numeric: tabular-nums; }
  .salary-date-row > div > span { color: var(--text-secondary); font-size: 11px; }
  .salary-amount { color: var(--orange); font-variant-numeric: tabular-nums; }
  .salary-date-row > .salary-amount { flex-shrink: 0; font-size: 14px; }
  .salary-view .salary-mobile-card-list { display: flex; flex-direction: column; gap: 12px; min-width: 0; }
  .salary-card-heading { justify-content: space-between; align-items: flex-start; gap: 12px; }
  .salary-card-heading > div { min-width: 0; display: flex; flex-direction: column; gap: 6px; }
  .salary-card-heading > div > strong { font-size: 14px; }
  .salary-card-heading > div > span { color: var(--muted); font-size: 11px; overflow-wrap: anywhere; }
  .salary-heading-amount { flex-shrink: 0; text-align: right; }
  .salary-heading-amount > strong { color: var(--orange); font-size: 15px; }
  .salary-bed-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); margin-top: 14px; padding-top: 14px; border-top: 1px dashed var(--line); }
  .salary-bed-grid.count { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .salary-bed-grid > div { min-width: 0; text-align: center; }
  .salary-bed-grid strong,
  .salary-bed-grid span { display: block; }
  .salary-bed-grid strong { font-size: 12px; font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }
  .salary-bed-grid span { margin-top: 5px; color: var(--muted); font-size: 10px; }
  .salary-style-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); margin-top: 14px; padding-top: 14px; border-top: 1px dashed var(--line); }
  .salary-style-grid.count { grid-template-columns: 1fr; }
  .salary-style-grid > div { min-width: 0; display: flex; flex-direction: column; gap: 6px; }
  .salary-style-grid span { color: var(--muted); font-size: 10px; }
  .salary-style-grid strong { font-size: 13px; overflow-wrap: anywhere; }
  .salary-fixed-card { padding: 4px 15px; }
  .salary-definition-row { justify-content: space-between; gap: 12px; padding: 12px 0; border-bottom: 1px solid var(--line); font-size: 12px; }
  .salary-definition-row:last-child { border-bottom: 0; }
  .salary-definition-row > span { flex-shrink: 0; color: var(--muted); }
  .salary-definition-row > strong { min-width: 0; text-align: right; font-weight: 500; overflow-wrap: anywhere; }
  .salary-definition-row > strong.salary-amount { font-size: 15px; font-weight: 650; }
  .salary-mobile-empty { padding: 28px 12px; color: var(--muted); text-align: center; font-size: 12px; }
  .salary-mobile-empty > span { display: block; margin-top: 7px; font-size: 11px; }
  .salary-section-heading { display: flex; align-items: center; justify-content: space-between; margin: 18px 3px 10px; }
  .salary-section-heading h3 { margin: 0; font-size: 15px; }
  .salary-section-heading > span { color: var(--muted); font-size: 11px; }
  .salary-employee-card { display: block; width: 100%; margin-bottom: 12px; padding: 16px 14px; border: 0; color: inherit; text-align: left; }
  .salary-employee-card:active,
  .salary-completion-card:active { opacity: .72; }
  .salary-employee-title { justify-content: space-between; align-items: flex-start; gap: 8px; }
  .salary-employee-title > div { min-width: 0; display: flex; flex-direction: column; gap: 5px; }
  .salary-employee-title > div > strong { font-size: 16px; }
  .salary-employee-title > div > span { color: var(--muted); font-size: 11px; }
  .salary-employee-title > .salary-type-tag { flex-shrink: 0; margin-top: 0; }
  .salary-employee-card .salary-duo { margin-top: 15px; }
  .salary-employee-card .salary-duo-item { gap: 4px; }
  .salary-employee-card .salary-duo-item > strong { font-size: 17px; }
  .salary-employee-stats { gap: 8px; margin-top: 14px; padding-top: 12px; border-top: 1px dashed var(--line); color: var(--muted); font-size: 10px; }
  .salary-employee-stats > span { flex: 1; min-width: 0; }
  .salary-employee-stats > svg { flex-shrink: 0; }
  .salary-record-card { padding: 16px 15px; }
  .salary-record-head { justify-content: space-between; align-items: flex-start; gap: 10px; }
  .salary-record-head > div { min-width: 0; display: flex; flex-direction: column; gap: 6px; }
  .salary-record-head > div > strong { font-size: 16px; overflow-wrap: anywhere; }
  .salary-record-head > div > span { color: var(--muted); font-size: 11px; overflow-wrap: anywhere; }
  .salary-record-head > .badge { flex-shrink: 0; }
  .salary-settlement-card .salary-record-head > div > strong { color: var(--orange); font-size: 20px; }
  .salary-record-fields { margin-top: 10px; }
  .salary-record-fields .salary-definition-row { padding: 9px 0; }
  .salary-card-actions { gap: 10px; margin-top: 12px; padding-top: 12px; border-top: 1px solid var(--line); }
  .salary-card-actions .button { flex: 1; min-width: 0; }
  .danger-text { color: var(--danger) !important; }
  .salary-change-card { padding: 16px; }
  .salary-change-card time { color: var(--muted); font-size: 11px; }
  .salary-change-employee { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-top: 15px; font-size: 15px; }
  .salary-change-card > p { margin: 13px 0 10px; color: var(--text-primary); font-size: 13px; line-height: 1.6; overflow-wrap: anywhere; }
  .salary-muted { color: var(--muted); font-size: 11px; }
  .salary-apply-amount { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-top: 15px; font-size: 12px; }
  .salary-apply-amount > span { color: var(--muted); }
  .salary-apply-amount > strong { color: var(--orange); font-size: 18px; }
  .salary-review-hint,
  .salary-record-note { margin: 11px 0 0; color: var(--text-secondary); font-size: 12px; line-height: 1.5; overflow-wrap: anywhere; }
  .salary-record-note { color: var(--muted); }
  .salary-completion-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; padding: 15px; }
  .salary-completion-head > span { color: var(--text-secondary); font-size: 13px; }
  .salary-completion-head > strong { font-size: 20px; font-variant-numeric: tabular-nums; }
  .salary-breadcrumb { justify-content: space-between; gap: 10px; margin: 12px 3px; font-size: 12px; }
  .salary-breadcrumb button { padding: 0; border: 0; background: transparent; color: var(--orange-dark); font-size: 12px; }
  .salary-breadcrumb > strong,
  .salary-breadcrumb > span { min-width: 0; text-align: right; overflow-wrap: anywhere; }
  .salary-completion-card { display: block; width: 100%; padding: 16px 15px; border: 0; color: inherit; text-align: left; }
  .salary-qty-badge { flex-shrink: 0; padding: 4px 10px; border-radius: 999px; background: var(--orange-soft); color: var(--orange-dark); font-size: 11px; font-variant-numeric: tabular-nums; }
  .salary-completion-numbers { justify-content: space-between; gap: 12px; margin-top: 14px; font-size: 11px; }
  .salary-completion-numbers > span { min-width: 0; color: var(--muted); overflow-wrap: anywhere; }
  .salary-completion-numbers > strong { flex-shrink: 0; color: var(--orange); font-size: 13px; }
  .salary-completion-foot { justify-content: space-between; gap: 10px; margin-top: 12px; padding-top: 11px; border-top: 1px dashed var(--line); color: var(--muted); font-size: 10px; }
  .salary-completion-foot > span:last-child { text-align: right; }
  .salary-process-qty { flex-shrink: 0; font-size: 12px; font-variant-numeric: tabular-nums; }
  .salary-matrix-card { margin-bottom: 16px; padding: 16px 15px; }
  .salary-matrix-card h3,
  .salary-detail-fixed h3 { margin: 0 0 10px; font-size: 15px; }
  .salary-matrix-row { display: grid; grid-template-columns: minmax(58px, .6fr) minmax(0, 2fr); align-items: center; gap: 10px; padding: 12px 0; border-bottom: 1px solid var(--line); }
  .salary-matrix-row:last-child { border-bottom: 0; }
  .salary-matrix-row > strong { font-size: 12px; overflow-wrap: anywhere; }
  .salary-matrix-row > div { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 6px; }
  .salary-matrix-row > div > span { padding: 5px 8px; border-radius: 7px; background: #f5f6f8; color: var(--text-secondary); font-size: 11px; }
  .salary-matrix-row b { margin-left: 4px; color: var(--ink); font-variant-numeric: tabular-nums; }
  .salary-completion-employees,
  .salary-completion-tickets { margin-top: 4px; }
  .salary-completion-tickets { margin-top: 18px; }
  .salary-completion-employee { padding: 14px; }
  .salary-completion-employee > strong { font-size: 14px; }
  .salary-completion-employee .salary-trio { margin-top: 12px; padding-top: 12px; }
  .salary-ticket-detail-card { padding: 14px; }
  .salary-ticket-detail-card .salary-record-head > strong { min-width: 0; font-size: 12px; overflow-wrap: anywhere; }
  .salary-ticket-detail-card .salary-record-head > strong:last-child { flex-shrink: 0; }
  .salary-ticket-meta { flex-wrap: wrap; gap: 8px 14px; margin-top: 11px; color: var(--muted); font-size: 11px; }
  .salary-detail-mobile { color: var(--ink); }
  .salary-detail-mobile .salary-summary-card { padding: 16px 0 0; box-shadow: none; }
  .salary-detail-mobile .salary-summary-links { padding-bottom: 0; }
  .salary-detail-period { display: flex; justify-content: space-between; gap: 10px; margin: 16px 0 10px; padding: 12px; border-radius: 10px; background: #f5f6f8; font-size: 12px; }
  .salary-detail-period > span { color: var(--muted); }
  .salary-detail-process { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 13px 0; border-bottom: 1px solid var(--line); }
  .salary-detail-process > div { min-width: 0; display: flex; flex-direction: column; gap: 5px; }
  .salary-detail-process > div > strong { font-size: 13px; }
  .salary-detail-process > div > span { color: var(--muted); font-size: 11px; }
  .salary-detail-process > strong { flex-shrink: 0; font-size: 13px; }
  .salary-detail-fixed { margin-top: 18px; }
  .salary-payslip-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 10px; padding: 15px; border-radius: 12px; background: #f5f6f8; }
  .salary-payslip-head > div { min-width: 0; display: flex; flex-direction: column; gap: 7px; }
  .salary-payslip-head > div > strong { color: var(--orange); font-size: 23px; font-variant-numeric: tabular-nums; }
  .salary-payslip-head > div > span { color: var(--muted); font-size: 11px; line-height: 1.5; }
  .salary-payslip-head > .badge { flex-shrink: 0; }
  .salary-slip-card { margin-top: 12px; padding: 15px; border-radius: 12px; background: #fff; box-shadow: 0 2px 8px rgba(31,35,41,.06); }
  .salary-slip-head { justify-content: space-between; gap: 12px; font-size: 16px; }
  .salary-slip-head > strong:last-child { color: var(--orange); font-variant-numeric: tabular-nums; }
  .salary-snapshot-tag { display: block; margin-top: 6px; color: var(--muted); font-size: 10px; }
  .salary-slip-item { display: grid; grid-template-columns: minmax(0, 1fr) auto minmax(76px, auto); gap: 10px; padding: 12px 0; border-bottom: 1px solid var(--line); font-size: 12px; }
  .salary-slip-item:last-child { border-bottom: 0; padding-bottom: 0; }
  .salary-slip-item > strong { min-width: 0; font-weight: 500; overflow-wrap: anywhere; }
  .salary-slip-item > span { color: var(--muted); white-space: nowrap; }
  .salary-slip-item > b { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
}

@media (max-width: 350px) {
  .salary-main-tabs button { font-size: 10px; }
  .salary-summary-card { padding-inline: 11px; }
  .salary-summary-actions { margin-inline: -11px; }
  .salary-trio strong { font-size: 12px; }
  .salary-summary-links { font-size: 10px; }
  .salary-bed-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px 0; }
  .salary-completion-numbers { align-items: flex-start; flex-direction: column; gap: 7px; }
  .salary-completion-numbers > strong { flex-shrink: 1; }
  .salary-slip-item { grid-template-columns: minmax(0, 1fr) auto; }
  .salary-slip-item > b { grid-column: 1 / -1; }
}
</style>
