<script setup>
import { computed, nextTick, onUnmounted, reactive, ref } from 'vue'
import { api, download } from '../api'
import { money, monthRange, number } from '../format'
import AppIcon from '../components/AppIcon.vue'
import ReportChart from '../components/ReportChart.vue'
import ComparisonTable from '../components/ComparisonTable.vue'
import ScheduleManager from '../components/ScheduleManager.vue'

const emit = defineEmits(['toast'])
const quick = ['今天完成了多少件？', '本月各员工的计件工资是多少？', '哪些订单快到期了？', '最近生产进度怎么样？']
const nowText = () => new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', hour12: false })
const activeTab = ref('chat')
const question = ref('')
const loading = ref(false)
const log = ref(null)
const messages = ref([{ id: 'welcome', role: 'ai', content: '你好，我是报表助手。试试下面的问题，或直接输入：', timeText: nowText(), result: { quick_questions: quick } }])
let messageSeq = 0
const speechSupported = typeof window !== 'undefined' && Boolean(window.SpeechRecognition || window.webkitSpeechRecognition)
const speechState = ref('idle')
const speechMessage = ref('')
const speechError = ref('')
const speechActive = computed(() => speechState.value !== 'idle')
let recognition = null
let recognitionBase = ''
let recognitionGotResult = false
let recognitionHadError = false

async function ask(text) {
  const content = (text || question.value).trim()
  if (!content || loading.value || speechActive.value) return
  const history = messages.value.slice(-10).map((message) => ({ role: message.role, content: message.content.slice(0, 200) }))
  messages.value.push({ id: `message-${++messageSeq}`, role: 'user', content, timeText: nowText() })
  question.value = ''
  loading.value = true
  scroll()
  try {
    const result = await api('/ai/report/query', {
      method: 'POST',
      body: { question: content, history, mode: 'query_plan' },
    })
    messages.value.push({
      id: `message-${++messageSeq}`,
      role: 'ai',
      content: result.answer || result.report?.summary || (result.fallback ? '暂未识别这个问题，请尝试下面的快捷问题。' : '查询完成'),
      timeText: nowText(),
      result,
    })
  } catch (error) {
    messages.value.push({ id: `message-${++messageSeq}`, role: 'ai', content: error.message, timeText: nowText(), failed: true })
  } finally {
    loading.value = false
    scroll()
  }
}
async function scroll() {
  await nextTick()
  if (log.value) log.value.scrollTop = log.value.scrollHeight
}
function keydown(event) {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault()
    ask()
  }
}

function stopSpeech() {
  if (!recognition || speechState.value === 'idle') return
  try {
    recognition.stop()
    speechState.value = 'processing'
    speechMessage.value = '正在结束语音识别…'
  } catch {
    recognition.abort()
  }
}

function toggleSpeech() {
  if (speechActive.value) {
    stopSpeech()
    return
  }
  speechMessage.value = ''
  speechError.value = ''
  const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition
  if (!Recognition) {
    speechError.value = '当前浏览器不支持 Web Speech API 语音识别，请使用键盘输入，或改用支持该能力的浏览器'
    return
  }
  recognitionBase = question.value.trim()
  recognitionGotResult = false
  recognitionHadError = false
  try {
    recognition = new Recognition()
    recognition.lang = 'zh-CN'
    recognition.continuous = false
    recognition.interimResults = true
    recognition.maxAlternatives = 1
    recognition.onstart = () => {
      speechState.value = 'listening'
      speechMessage.value = '正在聆听，请说话；识别文字只填入输入框，不会自动发送'
    }
    recognition.onresult = (event) => {
      const transcript = Array.from(event.results).map((result) => result[0]?.transcript || '').join('').trim()
      if (!transcript) return
      recognitionGotResult = true
      const combined = [recognitionBase, transcript].filter(Boolean).join(' ')
      question.value = combined.slice(0, 200)
      speechMessage.value = combined.length > 200
        ? '识别文字已达到 200 字上限，超出部分未填入；请复核后手动发送'
        : '识别文字已填入输入框，请复核、修改后手动发送'
    }
    recognition.onerror = (event) => {
      recognitionHadError = true
      const errors = {
        'not-allowed': '麦克风权限被拒绝或浏览器禁止使用语音服务，未识别成功；请在网站设置中允许麦克风后重试',
        'service-not-allowed': '浏览器语音服务不可用或已被禁用，未识别成功；请改用键盘输入',
        'audio-capture': '未找到可用麦克风，未识别成功；请检查设备与系统权限',
        'no-speech': '本次未听到可识别的语音，未识别成功；请靠近麦克风重试',
        network: '浏览器语音服务连接失败，未识别成功；请检查网络或改用键盘输入',
        aborted: '语音识别已中止，未产生新的识别结果',
        'language-not-supported': '浏览器不支持中文语音识别，请改用键盘输入',
      }
      speechError.value = errors[event.error] || `语音识别失败（${event.error || '未知错误'}），请重试或改用键盘输入`
      speechMessage.value = ''
    }
    recognition.onend = () => {
      speechState.value = 'idle'
      recognition = null
      if (!recognitionHadError) {
        speechMessage.value = recognitionGotResult
          ? '语音识别已结束，请复核输入框内容后手动发送'
          : '本次未识别到文字，请重试或直接输入'
      }
    }
    speechState.value = 'processing'
    speechMessage.value = '正在请求麦克风并启动浏览器语音识别…'
    recognition.start()
  } catch (error) {
    speechState.value = 'idle'
    recognition = null
    speechMessage.value = ''
    speechError.value = `无法启动语音识别：${error.message || '浏览器拒绝启动'}。未识别成功，请检查麦克风权限后重试`
  }
}

// 动态报表设计器：目录只包含后端已授权字段；执行时后端再次校验。
const catalog = ref(null)
const catalogLoading = ref(false)
const catalogError = ref('')
const designerLoading = ref(false)
const exportLoading = ref(false)
const exportTasks = ref([])
const exportTaskLoading = ref(false)
let exportPollTimer = null
const designerError = ref('')
const report = ref(null)
const executedPlan = ref(null)
const savedReports = ref([])
const selectedSavedId = ref('')
const savedName = ref('')
const savedLoading = ref(false)
const shareTargets = ref({ users: [], posts: [] })
const shareEditor = reactive({ visible: false, visibility: 'private', userIds: [], postIds: [] })
const initialMonth = monthRange()
const dateRange = reactive({ from: initialMonth.date_from, to: initialMonth.date_to })
const designer = reactive({
  dataset: 'piecework_records',
  dimensions: [],
  metricChoices: [],
  filters: [],
  orderField: 'registered_qty',
  orderDirection: 'desc',
  limit: 100,
  includeDeleted: false,
  presentation: 'table',
  asOf: '',
  comparison: '',
})
const operatorLabels = {
  eq: '等于', neq: '不等于', in: '属于（多值）', contains: '包含',
  isnull: '是否为空', gte: '大于等于', lte: '小于等于', between: '日期区间',
}
const aggregationLabels = { sum: '合计', avg: '平均', min: '最小值', max: '最大值', count: '计数', count_distinct: '去重计数' }
const dataset = computed(() => catalog.value?.datasets?.find((item) => item.key === designer.dataset))
const dimensions = computed(() => dataset.value?.fields?.filter((field) => field.can_dimension) || [])
const filterFields = computed(() => dataset.value?.fields?.filter((field) => field.can_filter && field.key !== dataset.value?.time_field) || [])
const metrics = computed(() => dataset.value?.metrics || [])
const capabilities = computed(() => catalog.value?.capabilities || {})
const selectedSaved = computed(() => savedReports.value.find((item) => String(item.id) === selectedSavedId.value))
const visibilityLabels = { private: '仅自己', company: '全公司', selected: '指定分享' }
const exportStatusLabels = { pending: '等待处理', running: '正在生成', success: '可下载', failed: '生成失败', expired: '已过期' }
const requiredUnitDimension = computed(() => {
  const key = dataset.value?.unit_dimension
  if (!key) return ''
  return designer.metricChoices.some((item) => metricSpec(item.key)?.requires_unit) ? key : ''
})

function metricSpec(key) { return metrics.value.find((item) => item.key === key) }
function metricChoice(key) { return designer.metricChoices.find((item) => item.key === key) }
function fieldSpec(key) { return filterFields.value.find((item) => item.key === key) }
function metricSelected(key) { return Boolean(metricChoice(key)) }
function syncOrderField() {
  const selected = [...designer.dimensions, ...designer.metricChoices.map((item) => item.key)]
  if (!selected.includes(designer.orderField)) designer.orderField = selected[0] || ''
}
function toggleMetric(metric) {
  const index = designer.metricChoices.findIndex((item) => item.key === metric.key)
  if (index >= 0) designer.metricChoices.splice(index, 1)
  else {
    designer.metricChoices.push({ key: metric.key, aggregation: metric.default_aggregation })
    if (metric.requires_unit && dataset.value?.unit_dimension
        && !designer.dimensions.includes(dataset.value.unit_dimension)) {
      designer.dimensions.push(dataset.value.unit_dimension)
    }
  }
  syncOrderField()
}
function defaultFilterValue(field, operator) {
  if (operator === 'isnull') return true
  if (field?.choices?.length) return operator === 'in' ? [field.choices[0].value] : field.choices[0].value
  return ''
}
function addFilter() {
  const field = filterFields.value[0]
  if (!field) return
  designer.filters.push({ field: field.key, operator: field.operators[0], value: defaultFilterValue(field, field.operators[0]) })
}
function filterFieldChanged(row) {
  const field = fieldSpec(row.field)
  row.operator = field?.operators?.[0] || 'eq'
  row.value = defaultFilterValue(field, row.operator)
}
function filterOperatorChanged(row) {
  row.value = defaultFilterValue(fieldSpec(row.field), row.operator)
}
function isoDate(date) {
  const pad = (value) => String(value).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
}
function setDateRange(kind) {
  const now = new Date()
  let start = new Date(now)
  let end = new Date(now)
  if (kind === 'all') {
    dateRange.from = ''
    dateRange.to = ''
    if (designer.comparison) designerError.value = '全部时间不支持期间对比，请选择具体开始和结束日期，或改为不对比'
    return
  }
  if (kind === 'week') start = new Date(now.getFullYear(), now.getMonth(), now.getDate() - ((now.getDay() + 6) % 7))
  if (kind === 'month') start = new Date(now.getFullYear(), now.getMonth(), 1)
  if (kind === 'lastMonth') {
    start = new Date(now.getFullYear(), now.getMonth() - 1, 1)
    end = new Date(now.getFullYear(), now.getMonth(), 0)
  }
  dateRange.from = isoDate(start)
  dateRange.to = isoDate(end)
}
function resetDataset() {
  const current = dataset.value
  if (!current) return
  const allowedDimensions = new Set(current.fields.filter((field) => field.can_dimension).map((field) => field.key))
  designer.dimensions = current.default_dimensions.filter((key) => allowedDimensions.has(key))
  designer.metricChoices = current.default_metrics.map((key) => {
    const metric = current.metrics.find((item) => item.key === key)
    return { key, aggregation: metric.default_aggregation }
  })
  designer.filters = []
  if (current.key === 'warehouse_lots') setDateRange('all')
  designer.includeDeleted = false
  designer.asOf = ''
  designer.comparison = ''
  designer.orderField = designer.metricChoices[0]?.key || designer.dimensions[0] || ''
  report.value = null
  executedPlan.value = null
  designerError.value = ''
}
async function loadCatalog() {
  if (catalogLoading.value) return
  catalogLoading.value = true
  catalogError.value = ''
  loadSavedReports()
  try {
    catalog.value = await api('/ai/report/catalog')
    if (catalog.value.capabilities.export) loadExportTasks()
    if (!catalog.value.datasets.length) throw new Error('当前岗位暂无可用动态报表数据集')
    designer.dataset = catalog.value.datasets[0].key
    resetDataset()
  } catch (error) {
    catalogError.value = error.message
  } finally {
    catalogLoading.value = false
  }
}
function switchTab(tab) {
  if (tab !== 'chat') stopSpeech()
  activeTab.value = tab
  if (tab === 'designer' && !catalog.value) loadCatalog()
}
function presentationChanged() {
  if (designer.presentation === 'line') {
    designer.orderField = designer.dimensions[0] || ''
    designer.orderDirection = 'asc'
  }
}
function buildPlan() {
  if (designer.comparison && (!dateRange.from || !dateRange.to)) {
    throw new Error('期间对比不能用于全部时间或不完整日期，请选择具体的开始日期和结束日期')
  }
  if (designer.comparison && designer.asOf) {
    throw new Error('期间对比不支持库存截至日期，请清空截至日期或改为不对比')
  }
  const filters = []
  if (dateRange.from || dateRange.to) {
    if (!dateRange.from || !dateRange.to) throw new Error('请同时选择开始日期和结束日期，或选择全部时间')
    filters.push({ field: dataset.value.time_field, operator: 'between', value: [dateRange.from, dateRange.to] })
  }
  designer.filters.forEach((row) => {
    let value = row.value
    if (row.operator === 'in') {
      value = Array.isArray(value) ? value : String(value).split(/[,，]/).map((item) => item.trim()).filter(Boolean)
      if (!value.length) throw new Error('多值筛选请至少选择或填写一项')
    } else if (row.operator !== 'isnull' && String(value).trim() === '') {
      throw new Error('请填写全部筛选值，或删除不需要的筛选条件')
    }
    filters.push({ field: row.field, operator: row.operator, value })
  })
  if (!designer.metricChoices.length) throw new Error('请至少选择一个统计指标')
  return {
    dataset: designer.dataset,
    metrics: designer.metricChoices.map((item) => ({ key: item.key, aggregation: item.aggregation })),
    dimensions: [...designer.dimensions],
    filters,
    order_by: designer.orderField ? [{ field: designer.orderField, direction: designer.orderDirection }] : [],
    limit: Number(designer.limit),
    include_deleted: capabilities.value.full_data && dataset.value.supports_include_deleted && designer.includeDeleted,
    presentation: designer.presentation,
    ...(designer.comparison ? { comparison: designer.comparison } : {}),
    ...(dataset.value.supports_as_of && designer.asOf ? { as_of: designer.asOf } : {}),
  }
}
async function executeQuery() {
  if (designerLoading.value) return
  designerLoading.value = true
  designerError.value = ''
  try {
    const plan = buildPlan()
    const result = await api('/ai/report/execute', { method: 'POST', body: { plan } })
    report.value = result.report
    executedPlan.value = result.plan
  } catch (error) {
    designerError.value = error.message
    report.value = null
    executedPlan.value = null
  } finally {
    designerLoading.value = false
  }
}
async function loadSavedReports() {
  try {
    const result = await api('/ai/report/saved')
    savedReports.value = result.results || []
    if (selectedSavedId.value && !savedReports.value.some((item) => String(item.id) === selectedSavedId.value)) {
      selectedSavedId.value = ''
    }
  } catch (error) {
    designerError.value = error.message
  }
}
async function saveCurrentReport() {
  if (savedLoading.value) return
  designerError.value = ''
  savedLoading.value = true
  try {
    if (!savedName.value.trim()) throw new Error('请输入报表名称')
    const result = await api('/ai/report/saved', {
      method: 'POST', body: { name: savedName.value.trim(), plan: buildPlan() },
    })
    savedReports.value.unshift(result)
    selectedSavedId.value = String(result.id)
    savedName.value = result.name
    emit('toast', '报表已保存；每次打开仍会重新校验权限')
  } catch (error) {
    designerError.value = error.message
  } finally {
    savedLoading.value = false
  }
}
async function updateSelectedReport() {
  const saved = savedReports.value.find((item) => String(item.id) === selectedSavedId.value)
  if (!saved || savedLoading.value) return
  if (!window.confirm(`用当前设计器条件覆盖“${saved.name}”？`)) return
  savedLoading.value = true
  designerError.value = ''
  try {
    const result = await api(`/ai/report/saved/${saved.id}`, {
      method: 'PATCH', body: { name: saved.name, plan: buildPlan() },
    })
    const index = savedReports.value.findIndex((item) => item.id === saved.id)
    savedReports.value.splice(index, 1, result)
    savedName.value = `${result.name}副本`
    emit('toast', '所选报表已更新')
  } catch (error) {
    designerError.value = error.message
  } finally {
    savedLoading.value = false
  }
}
function applyPlanToDesigner(plan) {
  const current = catalog.value?.datasets?.find((item) => item.key === plan.dataset)
  if (!current) throw new Error('当前目录没有该数据集，请刷新目录后重试')
  designer.dataset = plan.dataset
  designer.dimensions = [...plan.dimensions]
  designer.metricChoices = plan.metrics.map((item) => ({ ...item }))
  const timeFilter = plan.filters.find((item) => item.field === current.time_field && item.operator === 'between')
  dateRange.from = timeFilter?.value?.[0] || ''
  dateRange.to = timeFilter?.value?.[1] || ''
  designer.filters = plan.filters.filter((item) => item !== timeFilter).map((item) => JSON.parse(JSON.stringify(item)))
  designer.orderField = plan.order_by[0]?.field || ''
  designer.orderDirection = plan.order_by[0]?.direction || 'desc'
  designer.limit = plan.limit
  designer.includeDeleted = capabilities.value.full_data && dataset.value.supports_include_deleted && plan.include_deleted
  designer.presentation = plan.presentation || 'table'
  designer.asOf = plan.as_of || ''
  designer.comparison = plan.comparison || ''
  report.value = null
  executedPlan.value = null
}
async function openSavedReport() {
  const saved = savedReports.value.find((item) => String(item.id) === selectedSavedId.value)
  if (!saved || savedLoading.value) return
  savedLoading.value = true
  designerError.value = ''
  try {
    applyPlanToDesigner(saved.plan)
    const result = await api(`/ai/report/saved/${saved.id}/execute`, { method: 'POST', body: {} })
    report.value = result.report
    executedPlan.value = result.plan
    savedName.value = `${saved.name}副本`
  } catch (error) {
    designerError.value = error.message
    report.value = null
    executedPlan.value = null
  } finally {
    savedLoading.value = false
  }
}
async function deleteSavedReport() {
  const saved = savedReports.value.find((item) => String(item.id) === selectedSavedId.value)
  if (!saved || savedLoading.value || !window.confirm(`确定删除报表“${saved.name}”？`)) return
  savedLoading.value = true
  designerError.value = ''
  try {
    await api(`/ai/report/saved/${saved.id}`, { method: 'DELETE' })
    savedReports.value = savedReports.value.filter((item) => item.id !== saved.id)
    selectedSavedId.value = ''
    savedName.value = ''
    emit('toast', '保存的报表已删除')
  } catch (error) {
    designerError.value = error.message
  } finally {
    savedLoading.value = false
  }
}
async function openShareEditor() {
  const saved = selectedSaved.value
  if (!saved?.is_owner || savedLoading.value) return
  shareEditor.visible = true
  shareEditor.visibility = saved.visibility || 'private'
  shareEditor.userIds = (saved.shares || []).filter((item) => item.user_id).map((item) => item.user_id)
  shareEditor.postIds = (saved.shares || []).filter((item) => item.post_id).map((item) => item.post_id)
  designerError.value = ''
  if (capabilities.value.share && !shareTargets.value.users.length && !shareTargets.value.posts.length) {
    savedLoading.value = true
    try {
      shareTargets.value = await api('/ai/report/saved/targets')
    } catch (error) {
      designerError.value = error.message
    } finally {
      savedLoading.value = false
    }
  }
}
async function saveShareSettings() {
  const saved = selectedSaved.value
  if (!saved?.is_owner || savedLoading.value) return
  savedLoading.value = true
  designerError.value = ''
  try {
    const result = await api(`/ai/report/saved/${saved.id}/share`, {
      method: 'POST',
      body: {
        visibility: shareEditor.visibility,
        user_ids: shareEditor.visibility === 'selected' ? shareEditor.userIds : [],
        post_ids: shareEditor.visibility === 'selected' ? shareEditor.postIds : [],
      },
    })
    const index = savedReports.value.findIndex((item) => item.id === saved.id)
    savedReports.value.splice(index, 1, result)
    shareEditor.visible = false
    emit('toast', result.visibility === 'private' ? '分享已收回' : '分享范围已更新；接收者仍需具备数据权限')
  } catch (error) {
    designerError.value = error.message
  } finally {
    savedLoading.value = false
  }
}
async function exportResult() {
  if (!executedPlan.value || exportLoading.value) return
  exportLoading.value = true
  designerError.value = ''
  try {
    await download('/ai/report/export', {}, { method: 'POST', body: { plan: executedPlan.value } })
    emit('toast', '动态报表已导出')
  } catch (error) {
    designerError.value = error.message
  } finally {
    exportLoading.value = false
  }
}
async function loadExportTasks() {
  try {
    const result = await api('/ai/report/exports')
    exportTasks.value = result.results || []
    if (exportTasks.value.some((task) => ['pending', 'running'].includes(task.status))) scheduleExportPoll()
  } catch (error) {
    designerError.value = error.message
  }
}
function scheduleExportPoll() {
  if (exportPollTimer) return
  exportPollTimer = window.setTimeout(async () => {
    exportPollTimer = null
    await loadExportTasks()
  }, 2000)
}
async function createExportTask() {
  if (!executedPlan.value || exportTaskLoading.value) return
  exportTaskLoading.value = true
  designerError.value = ''
  try {
    const task = await api('/ai/report/exports', {
      method: 'POST', body: { plan: executedPlan.value },
    })
    exportTasks.value = [task, ...exportTasks.value.filter((item) => item.id !== task.id)].slice(0, 20)
    scheduleExportPoll()
    emit('toast', '异步导出任务已创建，可继续操作；生成后在任务列表下载')
  } catch (error) {
    designerError.value = error.message
  } finally {
    exportTaskLoading.value = false
  }
}
async function downloadExportTask(task) {
  designerError.value = ''
  try {
    await download(`/ai/report/exports/${task.id}/download`)
    emit('toast', '异步导出文件已下载')
  } catch (error) {
    designerError.value = error.message
    if (error.status === 410) loadExportTasks()
  }
}
onUnmounted(() => {
  if (exportPollTimer) window.clearTimeout(exportPollTimer)
  if (recognition) {
    recognition.onstart = null
    recognition.onresult = null
    recognition.onerror = null
    recognition.onend = null
    try { recognition.abort() } catch { /* 页面销毁时浏览器可能已结束识别 */ }
    recognition = null
  }
})
function planSummary(plan, sourceReport = null) {
  if (!plan) return []
  const planDataset = catalog.value?.datasets?.find((item) => item.key === plan.dataset)
  const columns = sourceReport?.columns || []
  const labelFor = (key) => columns.find((column) => column.key === key)?.label
    || planDataset?.fields?.find((field) => field.key === key)?.label
    || planDataset?.metrics?.find((metric) => metric.key === key)?.label || key
  const filters = plan.filters || []
  const timeFilter = filters.find((item) => planDataset
    ? item.field === planDataset.time_field && item.operator === 'between'
    : item.operator === 'between')
  const ordinaryFilterCount = filters.length - (timeFilter ? 1 : 0)
  return [
    { key: 'dataset', label: '数据集', value: planDataset?.label || sourceReport?.title || plan.dataset },
    { key: 'period', label: '期间', value: timeFilter?.value?.join(' 至 ') || '全部时间' },
    { key: 'dimensions', label: '维度', value: plan.dimensions?.length ? plan.dimensions.map(labelFor).join('、') : '不分组（汇总）' },
    { key: 'metrics', label: '指标', value: plan.metrics?.map((item) => labelFor(item.key)).join('、') || '—' },
    { key: 'filters', label: '筛选', value: `${ordinaryFilterCount} 条（不含期间）` },
  ]
}
function choiceLabel(column, value) {
  return column.choices?.find((choice) => choice.value === value)?.label
}
function formatCell(value, column) {
  if (value === null || value === undefined || value === '') return '—'
  const choice = choiceLabel(column, value)
  if (choice !== undefined) return choice
  if (column.type === 'money') return money(value)
  if (column.type === 'integer') return number(value)
  if (column.type === 'decimal') return Number(value).toLocaleString('zh-CN', { maximumFractionDigits: 3 })
  if (column.type === 'group') return Number(value) ? `${value} 组` : '未分组'
  return value
}
</script>

<template>
  <div class="ai-view">
    <div class="welcome"><div><h2>AI 问报</h2><p>自然语言问报与受权限控制的动态报表设计器。</p></div></div>
    <div class="segmented ai-mode-tabs" role="tablist" aria-label="问报模式">
      <button type="button" role="tab" :aria-selected="activeTab === 'chat'" :class="{ active: activeTab === 'chat' }" @click="switchTab('chat')">AI 问报</button>
      <button type="button" role="tab" :aria-selected="activeTab === 'designer'" :class="{ active: activeTab === 'designer' }" @click="switchTab('designer')">报表设计器</button>
    </div>

    <div v-if="activeTab === 'chat'" class="ai-layout">
      <section class="card chat-card">
        <div ref="log" class="chat-log" aria-live="polite">
          <div v-if="!messages.length" class="chat-empty"><AppIcon class="stat-icon" name="spark" :size="28" /><h3>想了解工厂的哪项数据？</h3><p>可询问产量、工资、订单进度等<br />支持基于上一轮内容继续追问</p></div>
          <div v-for="message in messages" :id="message.id" :key="message.id" class="chat-message" :class="[message.role, { failed: message.failed }]">
            <div class="message-stack">
              <div class="message-bubble">
                <span v-if="message.content" class="message-text">{{ message.content }}</span>
                <div v-if="message.result?.report" class="report-block">
                  <h4>{{ message.result.report.title }}</h4>
                  <ReportChart v-if="['bar', 'line'].includes(message.result.plan?.presentation)" :report="message.result.report" :type="message.result.plan.presentation" />
                  <section v-if="message.result.plan" class="executed-plan-summary chat-plan-summary" aria-label="本轮已执行计划条件">
                    <h4>已执行计划条件</h4>
                    <dl><div v-for="item in planSummary(message.result.plan, message.result.report)" :key="item.key"><dt>{{ item.label }}</dt><dd>{{ item.value }}</dd></div></dl>
                  </section>
                  <ComparisonTable v-if="message.result.report.comparison" :comparison="message.result.report.comparison" :report="message.result.report" :plan="message.result.plan" />
                  <div class="table-wrap"><table><thead><tr><th v-for="header in message.result.report.headers" :key="header">{{ header }}</th></tr></thead><tbody><tr v-for="(row, i) in message.result.report.rows" :key="i"><td v-for="(cell, j) in row" :key="j">{{ message.result.mode === 'query_plan' ? formatCell(cell, message.result.report.columns[j]) : cell }}</td></tr></tbody></table></div>
                  <ul v-if="message.result.mode === 'query_plan'" class="query-scope"><li v-for="item in message.result.report.scope" :key="item">{{ item }}</li></ul>
                  <p v-if="message.result.report.note" class="report-note">{{ message.result.report.note }}</p>
                </div>
                <div v-if="message.result?.quick_questions?.length" class="quick-questions"><button v-for="item in message.result.quick_questions" :key="item" class="quick-question" :disabled="loading || speechActive" @click="ask(item)">{{ item }}</button></div>
              </div>
              <time class="chat-time">{{ message.timeText }}</time>
            </div>
          </div>
          <div v-if="loading" class="chat-message ai"><div class="message-stack"><div class="message-bubble thinking-bubble"><span class="spinner" /> 正在查询报表…</div></div></div>
        </div>
        <form class="chat-input" @submit.prevent="ask()">
          <label class="sr-only" for="ai-question">输入业务问题</label>
          <textarea id="ai-question" v-model="question" rows="1" maxlength="200" placeholder="问点什么，如：今日产量多少" :disabled="loading || speechActive" @keydown="keydown" />
          <button type="button" class="button secondary speech-button" :class="{ listening: speechActive }" :disabled="loading || !speechSupported" :aria-pressed="speechActive" @click="toggleSpeech">{{ speechActive ? '停止识别' : '语音输入' }}</button>
          <button class="button primary chat-send" :disabled="loading || speechActive || !question.trim()">发送</button>
        </form>
        <div class="speech-feedback" aria-live="polite">
          <p v-if="!speechSupported" class="speech-error">当前浏览器不支持 Web Speech API（SpeechRecognition / webkitSpeechRecognition），请使用键盘输入或更换浏览器。</p>
          <p v-else-if="speechError" class="speech-error" role="alert">{{ speechError }}</p>
          <p v-else-if="speechMessage">{{ speechMessage }}</p>
          <p v-else>语音由浏览器实际识别，结果须经你复核后手动发送；浏览器可能使用其在线语音服务。</p>
        </div>
      </section>
      <aside class="card ai-aside"><div class="card-header"><div><h3>试试这样问</h3><p>数据仍受当前工厂与岗位权限限制</p></div></div><button v-for="item in quick" :key="item" class="quick-question" :disabled="loading || speechActive" @click="ask(item)">{{ item }}</button><p class="section-note">Enter 发送，Shift + Enter 换行。动态报表由系统按计划计算，未识别问题仅回落白名单报表或快捷问题。</p></aside>
    </div>

    <div v-else class="ai-designer">
      <section class="card designer-card">
        <div class="card-header"><div><h3>动态报表设计器</h3><p>无需等待 AI 识别，直接选择已授权的数据、统计口径和筛选条件。</p></div><button class="button ghost small" :disabled="catalogLoading" @click="loadCatalog">刷新目录</button></div>
        <p v-if="catalogLoading" class="designer-loading"><span class="spinner" />正在加载数据目录…</p>
        <p v-if="catalogError" class="form-error" role="alert">{{ catalogError }} <button class="button ghost small" @click="loadCatalog">重试</button></p>
        <template v-if="dataset">
          <div class="designer-section saved-reports-section">
            <div class="saved-report-save"><label class="field"><span>报表名称</span><input v-model="savedName" maxlength="80" aria-label="保存报表名称" placeholder="输入名称，保存当前条件" /></label><button type="button" class="button secondary small" :disabled="savedLoading || !savedName.trim()" @click="saveCurrentReport">保存当前</button></div>
            <div v-if="savedReports.length" class="saved-report-open"><select v-model="selectedSavedId" aria-label="已保存报表" @change="shareEditor.visible = false"><option value="">选择自己或同事分享的报表</option><option v-for="item in savedReports" :key="item.id" :value="String(item.id)">{{ item.name }} · {{ item.is_owner ? visibilityLabels[item.visibility] : `${item.owner.name}分享` }}</option></select><button type="button" class="button primary small" :disabled="!selectedSavedId || savedLoading" @click="openSavedReport">打开并执行</button><template v-if="selectedSaved?.is_owner"><button type="button" class="button secondary small" :disabled="savedLoading" @click="updateSelectedReport">覆盖所选</button><button type="button" class="button secondary small" :disabled="savedLoading" @click="openShareEditor">分享设置</button><button type="button" class="button ghost small" :disabled="savedLoading" @click="deleteSavedReport">删除</button></template></div>
            <div v-if="shareEditor.visible && selectedSaved?.is_owner" class="share-editor">
              <label class="field"><span>分享范围</span><select v-model="shareEditor.visibility" aria-label="分享范围"><option value="private">仅自己（收回分享）</option><option value="company" :disabled="!capabilities.share">全公司</option><option value="selected" :disabled="!capabilities.share">指定成员或岗位</option></select></label>
              <template v-if="shareEditor.visibility === 'selected'"><label class="field"><span>指定成员（可多选）</span><select v-model="shareEditor.userIds" multiple size="4"><option v-for="user in shareTargets.users" :key="user.id" :value="user.id">{{ user.name }}{{ user.post_name ? ` · ${user.post_name}` : '' }}</option></select></label><label class="field"><span>指定岗位（可多选）</span><select v-model="shareEditor.postIds" multiple size="4"><option v-for="post in shareTargets.posts" :key="post.id" :value="post.id">{{ post.name }}</option></select></label></template>
              <p v-if="!capabilities.share && shareEditor.visibility === 'private'" class="section-note">当前岗位不能新增分享，但仍可收回已有分享。</p>
              <div class="share-actions"><button type="button" class="button primary small" :disabled="savedLoading" @click="saveShareSettings">保存分享设置</button><button type="button" class="button ghost small" @click="shareEditor.visible = false">取消</button></div>
            </div>
            <p class="section-note">保存和分享的是查询条件而非结果。共享报表只读，可打开后另存副本；接收者无对应数据权限时不会看到报表或筛选条件。</p>
          </div>

          <div class="designer-section">
            <h4><span>1</span>数据集与统计指标</h4>
            <label class="field dataset-field"><span>数据集</span><select v-model="designer.dataset" aria-label="数据集" @change="resetDataset"><option v-for="item in catalog.datasets" :key="item.key" :value="item.key">{{ item.label }}</option></select><small>{{ dataset.description }}</small></label>
            <div class="metric-grid">
              <div v-for="metric in metrics" :key="metric.key" class="metric-option" :class="{ selected: metricSelected(metric.key) }">
                <label><input type="checkbox" :checked="metricSelected(metric.key)" @change="toggleMetric(metric)" /><span>{{ metric.label }}</span></label>
                <select v-if="metricSelected(metric.key) && metric.aggregations.length > 1" v-model="metricChoice(metric.key).aggregation" :aria-label="`${metric.label}聚合方式`"><option v-for="aggregation in metric.aggregations" :key="aggregation" :value="aggregation">{{ aggregationLabels[aggregation] }}</option></select>
              </div>
            </div>
          </div>

          <div class="designer-section">
            <h4><span>2</span>分组维度</h4>
            <p class="section-note">{{ requiredUnitDimension ? '数量指标必须保留单位分组，避免米、公斤等不同单位混加。' : '不选择分组时，返回当前筛选条件下的一行全公司汇总。' }}</p>
            <div class="option-grid"><label v-for="field in dimensions" :key="field.key" class="option-chip"><input v-model="designer.dimensions" type="checkbox" :value="field.key" :disabled="field.key === requiredUnitDimension" @change="syncOrderField" /><span>{{ field.label }}</span></label></div>
          </div>

          <div class="designer-section">
            <h4><span>3</span>日期与筛选</h4>
            <div class="range-presets"><button v-for="item in [['today', '今日'], ['week', '本周'], ['month', '本月'], ['lastMonth', '上月'], ['all', '全部时间']]" :key="item[0]" type="button" class="button ghost small" @click="setDateRange(item[0])">{{ item[1] }}</button></div>
            <div class="form-grid designer-date-grid"><label class="field"><span>开始日期</span><input v-model="dateRange.from" type="date" :max="dateRange.to || undefined" /></label><label class="field"><span>结束日期</span><input v-model="dateRange.to" type="date" :min="dateRange.from || undefined" /></label><label class="field comparison-choice"><span>期间对比</span><select v-model="designer.comparison" aria-label="期间对比"><option value="">不对比</option><option value="previous">上一期</option><option value="year_ago">去年同期</option></select><small>须选择完整日期范围；不支持库存截至日期。</small></label><label v-if="dataset.supports_as_of" class="field"><span>库存截至日期（可选）</span><input v-model="designer.asOf" type="date" :max="new Date().toLocaleDateString('en-CA')" /><small>按当前留存流水重算，不是历史审计快照。</small></label></div>
            <div v-for="(row, index) in designer.filters" :key="index" class="filter-editor-row">
              <select v-model="row.field" :aria-label="`筛选字段 ${index + 1}`" @change="filterFieldChanged(row)"><option v-for="field in filterFields" :key="field.key" :value="field.key">{{ field.label }}</option></select>
              <select v-model="row.operator" :aria-label="`筛选操作 ${index + 1}`" @change="filterOperatorChanged(row)"><option v-for="operator in fieldSpec(row.field)?.operators || []" :key="operator" :value="operator">{{ operatorLabels[operator] }}</option></select>
              <select v-if="row.operator === 'isnull'" v-model="row.value" :aria-label="`筛选值 ${index + 1}`"><option :value="true">是</option><option :value="false">否</option></select>
              <select v-else-if="fieldSpec(row.field)?.choices?.length && row.operator !== 'in'" v-model="row.value" :aria-label="`筛选值 ${index + 1}`"><option v-for="choice in fieldSpec(row.field).choices" :key="String(choice.value)" :value="choice.value">{{ choice.label }}</option></select>
              <select v-else-if="fieldSpec(row.field)?.choices?.length" v-model="row.value" multiple :aria-label="`筛选值 ${index + 1}`"><option v-for="choice in fieldSpec(row.field).choices" :key="String(choice.value)" :value="choice.value">{{ choice.label }}</option></select>
              <input v-else v-model="row.value" :aria-label="`筛选值 ${index + 1}`" :placeholder="row.operator === 'in' ? '多个值用逗号分隔' : '输入筛选值'" />
              <button type="button" class="button ghost small" :aria-label="`删除筛选 ${index + 1}`" @click="designer.filters.splice(index, 1)">删除</button>
            </div>
            <button type="button" class="button secondary small add-filter" :disabled="!filterFields.length" @click="addFilter">添加筛选</button>
          </div>

          <div class="designer-section">
            <h4><span>4</span>展示、排序与数据范围</h4>
            <div class="form-grid designer-options-grid">
              <label class="field"><span>展示方式</span><select v-model="designer.presentation" aria-label="展示方式" @change="presentationChanged"><option value="table">明细表格</option><option value="bar">柱状图＋表格</option><option value="line">折线图＋表格</option></select><small v-if="designer.presentation !== 'table'">图表使用一个指标和至少一个维度；折线图首维度须为日、周或月。</small></label>
              <label class="field"><span>排序字段</span><select v-model="designer.orderField"><option v-for="key in [...designer.dimensions, ...designer.metricChoices.map((item) => item.key)]" :key="key" :value="key">{{ fieldSpec(key)?.label || dataset.fields.find((field) => field.key === key)?.label || metricSpec(key)?.label }}</option></select></label>
              <label class="field"><span>排序方向</span><select v-model="designer.orderDirection"><option value="desc">降序</option><option value="asc">升序</option></select></label>
              <label class="field"><span>返回行数</span><input v-model.number="designer.limit" type="number" min="1" max="500" step="1" /></label>
              <label v-if="capabilities.full_data && dataset.supports_include_deleted" class="deleted-option"><input v-model="designer.includeDeleted" type="checkbox" /><span>包含{{ dataset.deleted_label }}</span><small>默认排除；开启后仅用于历史核查，不与正常经营报表混用。</small></label>
            </div>
          </div>

          <p v-if="designerError" class="form-error designer-error" role="alert">{{ designerError }}</p>
          <div class="designer-actions"><button class="button primary" :disabled="designerLoading || !designer.metricChoices.length" @click="executeQuery"><span v-if="designerLoading" class="spinner light" />执行查询</button><button v-if="capabilities.export" class="button secondary" :disabled="!executedPlan || exportLoading" @click="exportResult"><span v-if="exportLoading" class="spinner" />同步导出</button><button v-if="capabilities.export" class="button secondary" :disabled="!executedPlan || exportTaskLoading" @click="createExportTask">创建异步导出</button></div>
          <section v-if="executedPlan" class="executed-plan-summary" aria-label="设计器已执行计划条件">
            <h4>已执行计划条件</h4>
            <dl><div v-for="item in planSummary(executedPlan, report)" :key="item.key"><dt>{{ item.label }}</dt><dd>{{ item.value }}</dd></div></dl>
          </section>
          <p class="section-note">预览最多返回 500 行；导出使用当前已预览的同一份计划，单次最多 5000 行。异步任务由后台 worker 生成，下载时仍会重新校验权限。</p>
          <div v-if="exportTasks.length" class="export-tasks"><div class="export-task-header"><h4>最近导出任务</h4><button type="button" class="button ghost small" @click="loadExportTasks">刷新</button></div><div v-for="task in exportTasks" :key="task.id" class="export-task"><div><strong>{{ task.file_name || 'AI动态报表' }}</strong><span>{{ exportStatusLabels[task.status] || task.status }} · {{ task.total_count }} 行</span><small v-if="task.error_message">{{ task.error_message }}</small></div><button v-if="task.status === 'success'" type="button" class="button secondary small" @click="downloadExportTask(task)">下载</button></div></div>
          <ScheduleManager :saved-reports="savedReports" :selected-saved-id="selectedSavedId" :get-plan="buildPlan" />
        </template>
      </section>

      <section v-if="report" class="card designer-result" aria-live="polite">
        <div class="card-header"><div><h3>{{ report.title }}</h3><p>{{ report.summary }}</p></div></div>
        <ul class="query-scope"><li v-for="item in report.scope" :key="item">{{ item }}</li></ul>
        <ReportChart v-if="['bar', 'line'].includes(executedPlan?.presentation)" :report="report" :type="executedPlan.presentation" />
        <ComparisonTable v-if="report.comparison" :comparison="report.comparison" :report="report" :plan="executedPlan" />
        <div class="table-wrap dynamic-report-wrap"><table class="dynamic-report-table"><thead><tr><th v-for="column in report.columns" :key="column.key" :class="{ numeric: ['integer', 'decimal', 'money'].includes(column.type) }">{{ column.label }}</th></tr></thead><tbody><tr v-for="(row, rowIndex) in report.rows" :key="rowIndex"><td v-for="(column, columnIndex) in report.columns" :key="column.key" :class="{ numeric: ['integer', 'decimal', 'money'].includes(column.type) }">{{ formatCell(row[columnIndex], column) }}</td></tr></tbody></table></div>
        <p class="report-note">{{ report.note }}</p>
      </section>
    </div>
  </div>
</template>

<style scoped>
.message-stack{display:flex;flex-direction:column;align-items:flex-start;max-width:92%}.chat-message.user .message-stack{align-items:flex-end}.message-text{white-space:pre-wrap}.chat-time{margin-top:4px;color:#c9cdd4;font-size:10px}.quick-questions{display:flex;flex-wrap:wrap;gap:8px;margin-top:12px}.quick-questions .quick-question{display:inline-flex;width:auto;padding:7px 13px;border:0;border-radius:999px;background:var(--orange-soft);color:var(--orange);font-size:12px}.quick-questions .quick-question:hover{background:#ffe5cf}.chat-message.failed .message-bubble{color:var(--danger)}.thinking-bubble{display:flex;align-items:center;gap:8px}.chat-send{flex:0 0 auto;align-self:flex-end}.speech-button{flex:0 0 auto;align-self:flex-end;white-space:nowrap}.speech-button.listening{border-color:var(--orange);color:var(--orange)}.speech-feedback{padding:7px 3px 0;color:var(--muted);font-size:11px;line-height:1.55}.speech-feedback p{margin:0}.speech-feedback .speech-error{color:var(--danger)}
.ai-mode-tabs{display:inline-flex;margin-bottom:16px}.ai-mode-tabs button{min-width:116px}.ai-view{width:100%;max-width:1200px;min-width:0;margin:0 auto}.ai-designer{display:grid;gap:16px;min-width:0}.designer-card,.designer-section,.designer-result{min-width:0}.designer-card{padding:24px}.designer-card>.card-header>div{min-width:0}.designer-card>.card-header>.button{flex:0 0 auto;white-space:nowrap}.saved-report-save,.saved-report-open{display:flex;align-items:flex-end;gap:8px;flex-wrap:wrap}.saved-report-save .field{flex:1 1 260px;min-width:0}.saved-report-open{margin-top:10px}.saved-report-open select{flex:1 1 240px;min-width:0;padding:9px 10px;border:1px solid var(--line);border-radius:8px;background:#fff}.saved-reports-section .section-note{margin:10px 0 0}.designer-section{padding:18px 0;border-bottom:1px solid var(--line)}.designer-section:first-of-type{padding-top:12px}.designer-section h4{display:flex;align-items:center;gap:8px;margin:0 0 12px;font-size:14px}.designer-section h4>span{display:inline-grid;place-items:center;width:22px;height:22px;border-radius:50%;background:var(--orange-soft);color:var(--orange);font-size:12px}.dataset-field{max-width:520px;min-width:0}.designer-date-grid .field,.designer-options-grid .field{min-width:0}.dataset-field small{color:var(--muted);line-height:1.5}.metric-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,240px),1fr));gap:10px;margin-top:14px}.metric-option{display:flex;align-items:center;justify-content:space-between;gap:10px;min-width:0;min-height:48px;padding:10px 12px;border:1px solid var(--line);border-radius:10px;background:#fff}.metric-option.selected{border-color:var(--orange);background:var(--orange-soft)}.metric-option label{display:flex;align-items:center;gap:8px;min-width:0;cursor:pointer;white-space:nowrap}.metric-option label span,.option-chip span{white-space:nowrap}.metric-option input[type="checkbox"],.option-chip input[type="checkbox"],.deleted-option input[type="checkbox"]{flex:0 0 16px;width:16px;height:16px;min-height:0;margin:0;padding:0}.metric-option select{flex:0 0 116px;width:116px;min-width:0;max-width:116px;min-height:32px;padding:5px 8px;border:1px solid var(--line);border-radius:6px;background:#fff;white-space:nowrap}.option-grid{display:flex;flex-wrap:wrap;gap:8px}.option-chip{display:inline-flex;align-items:center;gap:6px;max-width:100%;padding:8px 12px;border:1px solid var(--line);border-radius:999px;background:#fff;cursor:pointer;font-size:12px}.option-chip:has(input:checked){border-color:var(--orange);background:var(--orange-soft);color:var(--orange)}.range-presets{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:12px}.designer-date-grid{width:100%;max-width:620px;grid-template-columns:repeat(2,minmax(0,1fr))}.designer-date-grid input,.designer-options-grid input,.designer-options-grid select{min-width:0}.filter-editor-row{display:grid;grid-template-columns:minmax(130px,1fr) minmax(120px,.8fr) minmax(160px,1.6fr) auto;gap:8px;min-width:0;margin-top:10px}.filter-editor-row select,.filter-editor-row input{min-width:0;padding:9px 10px;border:1px solid var(--line);border-radius:8px;background:#fff}.add-filter{margin-top:10px}.designer-options-grid{grid-template-columns:repeat(auto-fit,minmax(min(100%,180px),1fr))}.deleted-option{display:grid;grid-template-columns:16px minmax(0,1fr);align-items:start;gap:7px;font-size:13px}.deleted-option>span{white-space:nowrap}.deleted-option small{grid-column:2;color:var(--muted);line-height:1.5}.designer-actions{display:flex;flex-wrap:wrap;gap:10px;padding-top:18px}.designer-error{margin-top:14px}.designer-loading{display:flex;align-items:center;gap:8px;color:var(--muted)}.designer-result{padding:20px}.query-scope{display:flex;flex-wrap:wrap;gap:6px 18px;margin:0 0 14px;padding:0;list-style:none;color:var(--muted);font-size:12px}.dynamic-report-wrap{max-height:560px;overflow:auto}.dynamic-report-table{min-width:520px}.dynamic-report-table th{position:sticky;top:0;z-index:1;background:#fff}.dynamic-report-table td.numeric,.dynamic-report-table th.numeric{text-align:right;font-variant-numeric:tabular-nums}.report-note{margin-bottom:0;color:var(--muted);font-size:12px;line-height:1.6}
.executed-plan-summary{margin-top:16px;padding:13px 14px;border:1px solid var(--line);border-radius:10px;background:var(--surface-muted,#fafafa)}.executed-plan-summary h4{margin:0 0 9px;font-size:13px}.executed-plan-summary dl{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,180px),1fr));gap:9px 18px;margin:0}.executed-plan-summary dl>div{min-width:0}.executed-plan-summary dt{margin-bottom:3px;color:var(--muted);font-size:11px}.executed-plan-summary dd{margin:0;font-size:12px;line-height:1.55;overflow-wrap:anywhere}.chat-plan-summary{margin:12px 0}.chat-plan-summary dl{grid-template-columns:repeat(2,minmax(0,1fr))}
.export-tasks{padding-top:18px}.export-task-header,.export-task{display:flex;align-items:center;justify-content:space-between;gap:12px}.export-task-header h4{margin:0;font-size:14px}.export-task{padding:10px 0;border-bottom:1px solid var(--line)}.export-task>div{display:grid;gap:3px;min-width:0}.export-task span,.export-task small{color:var(--muted);font-size:12px}.export-task .button{flex:0 0 auto}
.share-editor{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,220px),1fr));gap:12px;margin-top:14px;padding:14px;border:1px solid var(--line);border-radius:10px;background:var(--surface-muted,#fafafa)}.share-editor select{min-width:0;padding:8px;border:1px solid var(--line);border-radius:8px;background:#fff}.share-editor select[multiple]{width:100%}.share-editor>.section-note,.share-actions{grid-column:1/-1}.share-actions{display:flex;flex-wrap:wrap;gap:8px}
@media(max-width:900px){.filter-editor-row{grid-template-columns:repeat(2,minmax(0,1fr))}.filter-editor-row>:nth-child(3){grid-column:1/-1}.filter-editor-row button{grid-column:1/-1;justify-self:end}}
@media(max-width:760px){.ai-mode-tabs{display:flex;margin-bottom:10px}.ai-mode-tabs button{flex:1;min-width:0}.ai-view>.welcome{display:none}.ai-layout{display:block}.chat-card.card{padding:0;border:0;background:transparent;box-shadow:none;overflow:visible}.chat-log{height:calc(100dvh - 252px);min-height:260px;padding:3px 0 12px;background:transparent;scroll-behavior:smooth}.chat-message{margin-bottom:14px}.chat-message.ai .message-stack{width:94%;max-width:94%}.chat-message.user .message-stack{max-width:78%}.message-bubble{max-width:100%;padding:11px 14px;border:0;border-radius:14px 14px 14px 4px;box-shadow:0 2px 8px rgba(31,35,41,.06);font-size:13px;line-height:1.65;overflow-wrap:anywhere}.chat-message.ai .message-bubble{width:100%}.chat-message.user .message-bubble{padding:10px 15px;border-radius:14px 4px 14px 14px;box-shadow:none}.chat-time{font-size:9px}.report-block{width:100%;min-width:0;margin-top:10px}.report-block h4{font-size:14px}.report-block .table-wrap{overflow:hidden}.report-block .table-wrap table{width:100%;min-width:0!important;table-layout:fixed;font-size:10px}.report-block th,.report-block td{padding:7px 5px;white-space:normal;overflow-wrap:anywhere;word-break:break-word}.report-note{line-height:1.5}.quick-questions{gap:7px;margin-top:11px}.quick-questions .quick-question{padding:7px 12px;font-size:11px;line-height:1.5;text-align:left}.chat-input{gap:9px;padding:9px 0 0;border-top:0;background:transparent}.chat-input textarea{height:44px;min-height:44px;max-height:44px;padding:11px 17px;overflow-y:auto;resize:none;border:0;border-radius:999px;background:#fff;box-shadow:0 2px 8px rgba(31,35,41,.06);font-size:13px;line-height:20px;white-space:nowrap}.chat-input textarea::placeholder{color:var(--muted)}.chat-send,.speech-button{height:44px;min-height:44px;padding:0 14px;border-radius:999px;font-size:13px}.ai-aside{display:none}.designer-card,.designer-result{padding:14px}.metric-grid{grid-template-columns:minmax(0,1fr)}.metric-option{align-items:center;flex-direction:row}.filter-editor-row{grid-template-columns:repeat(2,minmax(0,1fr))}.filter-editor-row>:nth-child(3){grid-column:1/-1}.filter-editor-row button{grid-column:1/-1;justify-self:end}.designer-options-grid{grid-template-columns:1fr 1fr}.deleted-option{grid-column:1/-1}.designer-actions{position:sticky;bottom:8px;z-index:2;padding:10px;border-radius:12px;background:rgba(255,255,255,.96);box-shadow:0 4px 18px rgba(31,35,41,.12)}.designer-actions .button{flex:1}.dynamic-report-wrap{margin-inline:-14px;padding-inline:14px}.dynamic-report-table{font-size:12px}.query-scope{display:grid;gap:5px}}
@media(max-width:480px){.designer-date-grid,.designer-options-grid{grid-template-columns:minmax(0,1fr)}.saved-report-save,.saved-report-open{align-items:stretch}.saved-report-save .button{width:100%}.saved-report-open select{flex-basis:100%}.designer-actions .button{min-width:0;padding-inline:10px}}
@media(max-width:350px){.chat-log{height:calc(100dvh - 242px)}.chat-input{gap:7px}.chat-input textarea{padding-inline:14px;font-size:12px}.chat-send{padding-inline:15px}.chat-message.ai .message-stack{width:97%;max-width:97%}.quick-questions .quick-question{padding-inline:10px}.metric-grid{grid-template-columns:1fr}.designer-options-grid{grid-template-columns:1fr}}
@media(prefers-reduced-motion:reduce){.chat-log{scroll-behavior:auto}}
</style>
