<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { api, companyId, rows } from '../api'
import { fullDateTime, number, pct } from '../format'
import AppIcon from '../components/AppIcon.vue'
import PageState from '../components/PageState.vue'
import AdminPagination from '../components/AdminPagination.vue'
import MobileLoadMore from '../components/MobileLoadMore.vue'

const emit = defineEmits(['toast'])
const props = defineProps({ section: { type: String, default: '' } })
const tabs = [['company', '公司资料'], ['settings', '系统设置'], ['audit', '操作日志'], ['client', '客户端日志']]
const active = ref(tabs.some(([key]) => key === props.section) ? props.section : 'company')
const standalone = computed(() => Boolean(props.section))
const pageTitle = computed(() => standalone.value
  ? ({ company: '公司信息', settings: '系统设置', audit: '操作日志', client: '客户端日志' }[active.value] || '公司与设置')
  : '公司与设置')
const pageDescription = computed(() => standalone.value
  ? ({ company: '公司资料、成员容量与版本套餐。', settings: '维护本公司的业务、显示与打印开关。', audit: '追溯价格、登记与业务档案的变更。', client: '排查小程序和客户端上报的问题。' }[active.value])
  : '查看公司资料与套餐，维护业务开关并追溯操作和客户端日志。')
const company = ref(null)
const companyLoading = ref(true)
const companyError = ref('')
const planDraft = ref('')
const planSaving = ref(false)
const planError = ref('')
const plans = [{ key: 'trial', label: '体验版', capacity: 20 }, { key: 'basic', label: '基础版', capacity: 50 }, { key: 'standard', label: '标准版', capacity: 150 }, { key: 'flagship', label: '旗舰版', capacity: 500 }]
const settingRows = [
  ['auto_cut_done', '默认裁完', '编菲保存后自动置为裁完'],
  ['revoke_current_month_only', '只能撤销当月登记', '撤销登记仅限当月记录'],
  ['hide_scan_price', '隐藏扫菲单价', '扫菲确认页不显示单价'],
  ['filter_zero_tickets', '过滤数量为 0 的菲票', '列表与统计剔除零数量菲票'],
  ['show_cut_total', '显示裁货总数', '生产卡片显示裁货总件数'],
  ['hide_style_total_price', '隐藏款式总单价', '款式列表不显示总单价'],
  ['hide_employee_salary', '隐藏员工工资', '按后端权限口径隐藏工资金额'],
  ['forbid_unapproved_register', '未审核禁止登记', '入职未审核的员工不能扫菲'],
  ['default_counter_on', '默认开启计数', '登记时默认打开计数器模式'],
  ['show_salary_style_image', '显示薪酬款式图片', '薪酬明细显示款式图片'],
  ['personalized_notify', '个性化通知', '接收站内及订阅消息通知'],
]
const defaults = { auto_cut_done: '0', revoke_current_month_only: '1', hide_scan_price: '0', filter_zero_tickets: '1', show_cut_total: '1', hide_style_total_price: '1', hide_employee_salary: '0', forbid_unapproved_register: '0', default_counter_on: '0', show_salary_style_image: '1', default_print_mode: 'cloud', cloud_printer: '', print_label_style: '{}', print_label_offsets: '{}', personalized_notify: '1' }
const settings = reactive({ ...defaults })
const settingsLoading = ref(false)
const settingsError = ref('')
const settingsSaving = ref(false)
const settingsFormError = ref('')
const loaded = reactive({ settings: false, audit: false, client: false })
const audit = ref({ count: 0 })
const auditLoading = ref(false)
const auditError = ref('')
const auditFilters = reactive({ type: '', keyword: '', date_from: '', date_to: '', page: 1 })
const clientLogs = ref({ count: 0 })
const clientLoading = ref(false)
const clientError = ref('')
const clientFilters = reactive({ level: '', user_id: '', date_from: '', date_to: '', page: 1 })
const auditLoadingMore = ref(false), auditAppendError = ref('')
const clientLoadingMore = ref(false), clientAppendError = ref('')
const auditHasMore = computed(() => rows(audit.value).length < Number(audit.value.count || 0))
const clientHasMore = computed(() => rows(clientLogs.value).length < Number(clientLogs.value.count || 0))
const employees = ref([])
const employeeError = ref('')
const auditTypes = [['', '全部类型'], ['price', '修改价格'], ['assist', '协助登记'], ['qty', '修改裁床数'], ['process', '修改工序'], ['revoke', '撤销登记'], ['other', '其他']]
let auditTimer

async function loadCompany() {
  companyLoading.value = true; companyError.value = ''; planError.value = ''
  try {
    const list = rows(await api('/companies/mine'))
    company.value = list.find((item) => item.id === Number(companyId())) || list[0] || null
    planDraft.value = company.value?.plan || ''
  } catch (e) { companyError.value = e.message; company.value = null } finally { companyLoading.value = false }
}
async function copyCompanyNo() {
  if (!company.value?.company_no) return
  const text = String(company.value.company_no)
  try {
    if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(text)
    else {
      const input = document.createElement('textarea')
      input.value = text; input.style.position = 'fixed'; input.style.opacity = '0'
      document.body.appendChild(input); input.select()
      if (!document.execCommand('copy')) throw new Error('copy failed')
      input.remove()
    }
    emit('toast', '公司编号已复制')
  } catch { emit('toast', `公司编号：${text}`) }
}
async function savePlan() {
  if (!company.value || !planDraft.value || planDraft.value === company.value.plan) return
  const plan = plans.find((item) => item.key === planDraft.value)
  if (!window.confirm(`确定将套餐变更为${plan?.label || planDraft.value}（容量 ${plan?.capacity} 人）吗？降档时在职人数不能超过新容量。`)) return
  planSaving.value = true; planError.value = ''
  try {
    const result = await api('/companies/plan', { method: 'PUT', body: { plan: planDraft.value } })
    company.value = { ...company.value, plan: result.plan, plan_display: result.plan_display, capacity: result.capacity }
    emit('toast', `已切换${result.plan_display}`)
  } catch (e) { planError.value = e.message; planDraft.value = company.value.plan } finally { planSaving.value = false }
}
async function loadSettings() {
  settingsLoading.value = true; settingsError.value = ''; settingsFormError.value = ''
  try { Object.assign(settings, defaults, await api('/settings')); loaded.settings = true } catch (e) { settingsError.value = e.message } finally { settingsLoading.value = false }
}
function validJsonObject(key) {
  const value = String(settings[key] || '').trim()
  if (key === 'cloud_printer' && !value) return true
  try { const parsed = JSON.parse(value || '{}'); return parsed && typeof parsed === 'object' && !Array.isArray(parsed) } catch { return false }
}
async function saveSettings() {
  settingsFormError.value = ''
  for (const key of ['cloud_printer', 'print_label_style', 'print_label_offsets']) {
    if (!validJsonObject(key)) { settingsFormError.value = `${key} 必须是 JSON 对象${key === 'cloud_printer' ? '，也可留空' : ''}`; return }
  }
  settingsSaving.value = true
  try {
    const result = await api('/settings', { method: 'PUT', body: { ...settings } })
    Object.assign(settings, defaults, result); emit('toast', '系统设置已保存')
  } catch (e) { settingsFormError.value = e.message } finally { settingsSaving.value = false }
}
async function loadAudit(append = false) {
  if (!append && window.matchMedia('(max-width: 760px)').matches) auditFilters.page = 1
  const requestedPage = auditFilters.page
  if (append) { auditLoadingMore.value = true; auditAppendError.value = '' }
  else { auditLoading.value = true; auditError.value = ''; auditAppendError.value = '' }
  try {
    const result = await api('/audit-logs', { params: { ...auditFilters, page_size: 20 } })
    audit.value = append ? { ...result, results: [...rows(audit.value), ...rows(result)] } : result
    loaded.audit = true
  } catch (e) {
    if (append) { auditAppendError.value = e.message; auditFilters.page = Math.max(1, requestedPage - 1) }
    else { auditError.value = e.message; audit.value = { count: 0 } }
  } finally { auditLoading.value = false; auditLoadingMore.value = false }
}
function loadMoreAudit() {
  if (!auditHasMore.value || auditLoadingMore.value || auditLoading.value) return
  auditFilters.page += 1
  return loadAudit(true)
}
function searchAudit() { clearTimeout(auditTimer); auditTimer = setTimeout(() => { auditFilters.page = 1; loadAudit() }, 300) }
function resetAuditPage() { auditFilters.page = 1; loadAudit() }
async function loadClientEmployees() {
  employeeError.value = ''
  try { employees.value = rows(await api('/employees', { params: { tab: 'all' } })) } catch (e) { employeeError.value = e.message; employees.value = [] }
}
async function loadClientLogs(append = false) {
  if (!append && window.matchMedia('(max-width: 760px)').matches) clientFilters.page = 1
  const requestedPage = clientFilters.page
  if (append) { clientLoadingMore.value = true; clientAppendError.value = '' }
  else { clientLoading.value = true; clientError.value = ''; clientAppendError.value = '' }
  try {
    const result = await api('/client-logs', { params: { ...clientFilters, page_size: 20 } })
    clientLogs.value = append ? { ...result, results: [...rows(clientLogs.value), ...rows(result)] } : result
    loaded.client = true
  } catch (e) {
    if (append) { clientAppendError.value = e.message; clientFilters.page = Math.max(1, requestedPage - 1) }
    else { clientError.value = e.message; clientLogs.value = { count: 0 } }
  } finally { clientLoading.value = false; clientLoadingMore.value = false }
}
function loadMoreClientLogs() {
  if (!clientHasMore.value || clientLoadingMore.value || clientLoading.value) return
  clientFilters.page += 1
  return loadClientLogs(true)
}
function resetClientPage() { clientFilters.page = 1; loadClientLogs() }
function switchTab(tab) {
  active.value = tab
  if (tab === 'settings' && !loaded.settings && !settingsLoading.value) loadSettings()
  if (tab === 'audit' && !loaded.audit && !auditLoading.value) loadAudit()
  if (tab === 'client' && !loaded.client && !clientLoading.value) { loadClientLogs(); if (!employees.value.length) loadClientEmployees() }
}
function refresh() {
  if (active.value === 'company') loadCompany()
  else if (active.value === 'settings') loadSettings()
  else if (active.value === 'audit') loadAudit()
  else loadClientLogs()
}
function pretty(value) { return value === null || value === undefined ? '—' : typeof value === 'string' ? value : JSON.stringify(value, null, 2) }
function levelClass(level) { return level === 'error' ? 'cancelled' : level === 'warn' ? 'producing' : 'done' }
onMounted(() => { loadCompany(); switchTab(active.value) })
</script>

<template>
  <div class="welcome"><div><h2>{{ pageTitle }}</h2><p>{{ pageDescription }}</p></div><button class="button secondary" @click="refresh"><AppIcon name="refresh" :size="16" />刷新</button></div>
  <div v-if="!standalone" class="filter-bar"><div class="segmented admin-settings-tabs"><button v-for="tab in tabs" :key="tab[0]" :class="{ active: active === tab[0] }" @click="switchTab(tab[0])">{{ tab[1] }}</button></div></div>

  <template v-if="active === 'company'">
    <PageState :loading="companyLoading" :error="companyError" :empty="!company" empty-text="未找到当前公司资料" @retry="loadCompany"><section v-if="company" class="card desktop-company"><div class="card-header"><div><h3>{{ company.name }}</h3><p>公司编号 {{ company.company_no }} · 创建于 {{ (company.created_at || '').slice(0, 10) || '—' }}</p></div><span class="badge done">{{ company.status_display || '在职' }}</span></div><div class="detail-grid"><div class="detail-item"><small>公司名称</small><strong>{{ company.name }}</strong></div><div class="detail-item"><small>城市</small><strong>{{ company.city || '未填写' }}</strong></div><div class="detail-item"><small>当前岗位</small><strong>{{ company.post?.name || '未设置' }}</strong></div><div class="detail-item"><small>版本套餐</small><strong>{{ company.plan_display }}</strong></div><div class="detail-item"><small>在职人数 / 容量</small><strong>{{ number(company.member_count) }} / {{ number(company.capacity) }} 人</strong></div><div class="detail-item"><small>容量使用率</small><strong>{{ company.capacity ? pct(company.member_count / company.capacity * 100).toFixed(0) : 0 }}%</strong></div></div><div class="meter-track"><div class="meter-fill" :style="{ width: `${company.capacity ? pct(company.member_count / company.capacity * 100) : 0}%` }" /></div><form class="admin-plan-form" @submit.prevent="savePlan"><label class="field"><span>变更套餐</span><select v-model="planDraft"><option v-for="plan in plans" :key="plan.key" :value="plan.key">{{ plan.label }} · {{ plan.capacity }} 人</option></select></label><button class="button primary" :disabled="planSaving || !planDraft || planDraft === company.plan">确认变更套餐</button></form><p v-if="planError" class="form-error">{{ planError }}</p><p class="section-note">公司名称、城市等资料当前仅有读取契约，Web 不提供伪造的编辑入口；套餐变更使用 /companies/plan。</p></section><div v-if="company" class="company-mobile"><section class="card company-mobile-head"><h2>{{ company.name }}</h2><span class="badge done">{{ company.status_display || '在职' }}</span><p>员工可凭公司编号申请加入本公司</p></section><section class="card company-mobile-details"><button class="company-mobile-row" @click="copyCompanyNo"><span>公司编号</span><strong class="num">{{ company.company_no }}</strong><em>复制</em></button><div class="company-mobile-row"><span>所在城市</span><strong>{{ company.city || '未填写' }}</strong></div><div class="company-mobile-row"><span>版本套餐</span><strong>{{ company.plan_display || '基础版' }}</strong></div><form class="company-mobile-plan" @submit.prevent="savePlan"><select v-model="planDraft" aria-label="变更套餐"><option v-for="plan in plans" :key="plan.key" :value="plan.key">{{ plan.label }} · {{ plan.capacity }} 人</option></select><button class="button secondary small" :disabled="planSaving || !planDraft || planDraft === company.plan">变更</button></form><div class="company-mobile-row"><span>成员数</span><strong>{{ number(company.member_count) }} / {{ number(company.capacity) }} 人</strong></div><div class="meter-track"><div class="meter-fill" :style="{ width: `${company.capacity ? pct(company.member_count / company.capacity * 100) : 0}%` }" /></div><div class="company-mobile-row"><span>创建时间</span><strong>{{ (company.created_at || '').slice(0, 10) || '—' }}</strong></div><p v-if="planError" class="form-error">{{ planError }}</p></section></div></PageState>
  </template>

  <template v-else-if="active === 'settings'">
    <PageState :loading="settingsLoading" :error="settingsError" @retry="loadSettings"><form class="card" @submit.prevent="saveSettings"><div class="card-header"><div><h3>业务与显示开关</h3><p>11 项开关，保存后由本公司各业务端读取。</p></div><button class="button primary" :disabled="settingsSaving"><span v-if="settingsSaving" class="spinner light" />保存全部设置</button></div><div class="admin-setting-list"><label v-for="row in settingRows" :key="row[0]" class="admin-setting-row"><span><strong>{{ row[1] }}</strong><small>{{ row[2] }}</small></span><span class="admin-switch"><input v-model="settings[row[0]]" type="checkbox" true-value="1" false-value="0" :aria-label="row[1]" /><i /></span></label></div><h3 class="admin-subheading">打印与高级配置</h3><div class="form-grid"><label class="field"><span>默认打印方式</span><select v-model="settings.default_print_mode"><option value="cloud">云打印</option><option value="bluetooth">蓝牙打印</option></select></label><label class="field full"><span>云打印机配置（JSON，可留空）</span><textarea v-model="settings.cloud_printer" rows="3" placeholder='例如 {"sn":"...","key":"..."}' /></label><label class="field full"><span>菲票标签模板（JSON 对象）</span><textarea v-model="settings.print_label_style" rows="4" /></label><label class="field full"><span>打印机对位偏移（JSON 对象）</span><textarea v-model="settings.print_label_offsets" rows="4" /></label></div><p class="section-note">打印配置按后端 KV 契约保存；Web 本次不接入蓝牙/云打印设备调试。</p><p v-if="settingsFormError" class="form-error">{{ settingsFormError }}</p><div class="form-actions"><button class="button primary" :disabled="settingsSaving">保存全部设置</button></div></form></PageState>
  </template>

  <template v-else-if="active === 'audit'">
    <form class="filter-bar" @submit.prevent="resetAuditPage"><select v-model="auditFilters.type" aria-label="日志类型" @change="resetAuditPage"><option v-for="type in auditTypes" :key="type[0]" :value="type[0]">{{ type[1] }}</option></select><label class="search-box"><AppIcon name="search" :size="17" /><input v-model="auditFilters.keyword" placeholder="搜索款号、床次、扎号或内容" @input="searchAudit" /></label><input v-model="auditFilters.date_from" type="date" aria-label="开始日期" @change="resetAuditPage" /><input v-model="auditFilters.date_to" type="date" aria-label="结束日期" @change="resetAuditPage" /></form>
    <section class="card table-card"><PageState :loading="auditLoading" :error="auditError" :empty="!rows(audit).length" empty-text="没有符合条件的操作日志" @retry="loadAudit"><div class="table-wrap desktop-table"><table><thead><tr><th>时间 / 类型</th><th>操作内容</th><th>操作人 / 对象</th><th>业务定位</th><th>变更</th></tr></thead><tbody><tr v-for="log in rows(audit)" :key="log.id"><td>{{ fullDateTime(log.created_at) }}<small class="cell-subtitle">{{ log.type_display }}</small></td><td class="admin-log-message">{{ log.message }}</td><td>{{ log.operator_name || '—' }}<small class="cell-subtitle">对象：{{ log.target_user_name || '—' }}</small></td><td>{{ log.style_no || '—' }}<small class="cell-subtitle">{{ log.bed_no }} {{ log.bundle_no ? `第 ${log.bundle_no} 扎` : '' }}</small></td><td><details><summary>查看前后值</summary><pre class="admin-log-detail">变更前：{{ pretty(log.before) }}
变更后：{{ pretty(log.after) }}</pre></details></td></tr></tbody></table></div><div class="mobile-cards"><article v-for="log in rows(audit)" :key="log.id" class="mobile-row"><div class="mobile-row-head"><span class="badge">{{ log.type_display }}</span><time class="cell-subtitle">{{ fullDateTime(log.created_at) }}</time></div><p class="admin-client-message">{{ log.message }}</p><div class="mobile-meta"><span>操作人 <strong>{{ log.operator_name || '—' }}</strong></span><span>业务 <strong>{{ log.style_no || log.bed_no || '—' }}</strong></span></div><details class="admin-details"><summary>查看变更前后值</summary><pre class="admin-log-detail">变更前：{{ pretty(log.before) }}
变更后：{{ pretty(log.after) }}</pre></details></article></div></PageState><MobileLoadMore :loading="auditLoadingMore" :error="auditAppendError" :has-more="auditHasMore" :count="rows(audit).length" @more="loadMoreAudit" /><AdminPagination class="desktop-pagination" :page="auditFilters.page" :count="audit.count || 0" :disabled="auditLoading" @change="auditFilters.page = $event; loadAudit()" /></section>
  </template>

  <template v-else>
    <form class="filter-bar" @submit.prevent="resetClientPage"><select v-model="clientFilters.level" aria-label="日志级别" @change="resetClientPage"><option value="">全部级别</option><option value="error">错误</option><option value="warn">告警</option><option value="info">信息</option></select><select v-model="clientFilters.user_id" aria-label="上报员工" @change="resetClientPage"><option value="">全部员工</option><option v-for="employee in employees" :key="employee.user_id" :value="employee.user_id">{{ employee.name }}</option></select><input v-model="clientFilters.date_from" type="date" aria-label="开始日期" @change="resetClientPage" /><input v-model="clientFilters.date_to" type="date" aria-label="结束日期" @change="resetClientPage" /><button class="button secondary">查询</button></form><p v-if="employeeError" class="section-note">员工选项加载失败：{{ employeeError }} <button class="button ghost small" @click="loadClientEmployees">重试</button></p>
    <section class="card table-card"><PageState :loading="clientLoading" :error="clientError" :empty="!rows(clientLogs).length" empty-text="没有符合条件的客户端日志" @retry="loadClientLogs"><div class="table-wrap desktop-table"><table><thead><tr><th>时间 / 级别</th><th>页面</th><th>摘要</th><th>上报人</th><th>详情</th></tr></thead><tbody><tr v-for="log in rows(clientLogs)" :key="log.id"><td>{{ fullDateTime(log.created_at) }}<small class="cell-subtitle"><span class="badge" :class="levelClass(log.level)">{{ log.level }}</span></small></td><td>{{ log.page || '—' }}</td><td class="admin-log-message">{{ log.message }}</td><td>{{ log.user_name || '未知' }}<small class="cell-subtitle">{{ log.user_phone }}</small></td><td><details><summary>展开</summary><pre class="admin-log-detail">{{ log.detail || '无详情' }}</pre></details></td></tr></tbody></table></div><div class="mobile-cards"><article v-for="log in rows(clientLogs)" :key="log.id" class="mobile-row"><div class="mobile-row-head"><span class="badge" :class="levelClass(log.level)">{{ log.level }}</span><time class="cell-subtitle">{{ fullDateTime(log.created_at) }}</time></div><strong>{{ log.message }}</strong><small class="cell-subtitle">{{ log.page || '未知页面' }} · {{ log.user_name || '未知用户' }}</small><details class="admin-details"><summary>异常栈 / 参数详情</summary><pre class="admin-log-detail">{{ log.detail || '无详情' }}</pre></details></article></div></PageState><MobileLoadMore :loading="clientLoadingMore" :error="clientAppendError" :has-more="clientHasMore" :count="rows(clientLogs).length" @more="loadMoreClientLogs" /><AdminPagination class="desktop-pagination" :page="clientFilters.page" :count="clientLogs.count || 0" :disabled="clientLoading" @change="clientFilters.page = $event; loadClientLogs()" /></section>
  </template>
</template>

<style scoped>
.company-mobile{display:none}.admin-settings-tabs{max-width:100%;overflow:auto}.admin-plan-form{display:flex;align-items:flex-end;gap:12px;margin-top:24px;max-width:520px}.admin-plan-form .field{flex:1}.admin-setting-list{border:1px solid var(--line);border-radius:11px;overflow:hidden}.admin-setting-row{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:14px 16px;border-bottom:1px solid var(--line)}.admin-setting-row:last-child{border:0}.admin-setting-row strong,.admin-setting-row small{display:block}.admin-setting-row strong{font-size:13px}.admin-setting-row small{font-size:11px;color:var(--muted);margin-top:4px}.admin-switch{position:relative;width:40px;height:23px;flex:none}.admin-switch input{position:absolute;opacity:0;width:100%;height:100%;margin:0;z-index:1;cursor:pointer}.admin-switch i{display:block;width:40px;height:23px;border-radius:20px;background:#C9CDD4;transition:.15s}.admin-switch i:after{content:'';display:block;width:19px;height:19px;border-radius:50%;background:#fff;margin:2px;transition:.15s;box-shadow:0 1px 3px #0002}.admin-switch input:checked+i{background:var(--orange)}.admin-switch input:checked+i:after{transform:translateX(17px)}.admin-switch input:focus-visible+i{outline:3px solid #FF7A0044;outline-offset:2px}.admin-subheading{font-size:15px;margin:25px 0 16px}.admin-log-message{min-width:240px;max-width:520px;white-space:normal;line-height:1.6}.admin-log-detail{white-space:pre-wrap;word-break:break-word;max-width:420px;max-height:300px;overflow:auto;background:#F5F6F8;padding:12px;border-radius:8px;font:11px/1.6 ui-monospace,SFMono-Regular,Consolas,monospace}.admin-details{margin-top:12px;font-size:12px}.admin-client-message{font-size:13px;line-height:1.6;margin:10px 0}.form-error{margin-top:15px}summary{cursor:pointer;color:var(--orange-dark)}textarea{resize:vertical;font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:12px}
@media(max-width:760px){.desktop-pagination{display:none}.desktop-company{display:none}.company-mobile{display:block}.company-mobile-head h2{font-size:20px;margin:0 0 10px}.company-mobile-head p{font-size:12px;color:var(--muted);margin:10px 0 0}.company-mobile-details{padding:0 12px}.company-mobile-row{display:flex;align-items:center;gap:10px;width:100%;min-height:48px;padding:0;border:0;border-bottom:1px solid var(--line);background:transparent;text-align:left;font-size:14px}.company-mobile-row>span{color:var(--muted)}.company-mobile-row>strong{margin-left:auto;font-weight:400}.company-mobile-row>em{font-style:normal;color:var(--orange);font-size:13px}.company-mobile-plan{display:flex;gap:8px;padding:10px 0;border-bottom:1px solid var(--line)}.company-mobile-plan select{flex:1;min-width:0}.company-mobile-details>.meter-track{margin:12px 0 0}.admin-plan-form{align-items:stretch;flex-direction:column}.admin-setting-list{border:0;border-radius:0}.admin-setting-row{padding:12px 0}.admin-setting-row:first-child{padding-top:0}.admin-setting-row strong{font-size:15px}.admin-setting-row small{font-size:12px}.filter-bar input[type=date]{width:calc(50% - 4px)}.card-header{align-items:flex-start}.card-header>.button{font-size:12px;padding:8px 10px}}
</style>
