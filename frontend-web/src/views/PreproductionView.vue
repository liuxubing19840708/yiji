<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { api, rows } from '../api'
import { dateTime, fullDateTime } from '../format'
import { hasPermission } from '../permissions'
import AppIcon from '../components/AppIcon.vue'
import AdminModal from '../components/AdminModal.vue'
import PageState from '../components/PageState.vue'
import {
  confirmBriefing, createReviewIssue, createSampleTask, createTechPack,
  decideTechPack, fetchCraftTemplates, fetchPreproductionStatus, fetchTechPacks,
  recordSampleResult, reopenReviewIssue, resolveReviewIssue, saveCraftTemplate,
  updateTechPack, uploadPreproductionFile,
} from '../preproduction-api'

const props = defineProps({
  orderId: { type: [Number, String], default: 0 },
  section: { type: String, default: '' },
})
const emit = defineEmits(['toast'])
const BASE_SIZES = ['XS', 'S', 'M', 'L', 'XL', '2XL', '3XL', '4XL', '均码']
const BASE_POINTS = ['衣长', '胸围', '肩宽', '袖长', '袖口', '领围', '腰围', '臀围', '裤长', '裙长', '大腿围', '脚口']
const TEXT_FIELDS = {
  requirements: 'requirements', challenges: 'challenges',
  common_mistakes: 'commonMistakes', quality_standards: 'qualityStandards',
}
const craftBlocks = [
  { kind: 'requirements', field: 'requirements', title: '工艺要求', required: true, placeholder: '选择模板后可微调，也可直接填写' },
  { kind: 'challenges', field: 'challenges', title: '工艺难点', placeholder: '需要重点控制的工艺难点' },
  { kind: 'common_mistakes', field: 'commonMistakes', title: '易错点', placeholder: '班组容易出错的地方' },
  { kind: 'quality_standards', field: 'qualityStandards', title: '品质标准', placeholder: '尺寸、公差和外观标准' },
]
const tabs = [
  ['craft', '工艺资料'], ['sample', '产前样'], ['issues', '问题处理'], ['approval', '交底审批'],
]
const canManage = computed(() => hasPermission('preproduction.manage'))
const canBrief = computed(() => hasPermission('preproduction.brief'))
const canApprove = computed(() => hasPermission('preproduction.approve'))
const orders = ref([])
const order = ref(null)
const selectedOrderId = ref('')
const status = ref(null)
const pack = ref(null)
const templates = ref([])
const loading = ref(true)
const error = ref('')
const activeTab = ref('craft')
const saving = ref(false)
const formError = ref('')
const actionError = ref('')
const uploadingTarget = ref('')
const modal = ref('')
const modalError = ref('')
const modalForm = reactive({ kind: '', name: '', issueId: 0, text: '', approved: false })
const form = reactive({
  newVersion: false, versionNo: '', patternNo: '', requirements: '', challenges: '',
  commonMistakes: '', qualityStandards: '', measurements: [], patternFiles: [], techFiles: [],
})
const sampleForm = reactive({ result: 'passed', note: '', files: [] })
const issueDetail = ref('')
const briefingNote = ref('')
let loadSerial = 0

function initialOrderId() {
  const hashQuery = window.location.hash.includes('?') ? window.location.hash.split('?')[1] : ''
  const params = new URLSearchParams(window.location.search)
  const hashParams = new URLSearchParams(hashQuery)
  return Number(props.orderId || params.get('order_id') || hashParams.get('order_id') || props.section) || ''
}
const selectedOrder = computed(() => orders.value.find((item) => item.id === Number(selectedOrderId.value)) || order.value)
const currentPackLabel = computed(() => pack.value ? `工艺 V${pack.value.version_no} · ${pack.value.style_no}` : '尚未创建工艺资料')
const sampleHistory = computed(() => (pack.value?.sample_tasks || [])
  .flatMap((task) => (task.records || []).map((record) => ({ ...record, taskTitle: task.title })))
  .sort((a, b) => b.id - a.id))
const templatesFor = (kind) => templates.value.filter((item) => item.kind === kind)
const statusClass = computed(() => ({ approved: 'done', reconfirm: 'overdue', pending_approval: 'producing' }[status.value?.status] || 'cancelled'))
const resultText = (result) => ({ passed: '合格', failed: '不合格', rework: '重做中' }[result] || result)
const approvalText = (value) => ({ approved: '通过', rejected: '驳回', invalidated: '已失效', pending: '待审批' }[value] || value)

function numberOptions(max, step) {
  const values = []
  for (let value = 0; value <= max; value += step) values.push(Number(value.toFixed(1)).toString())
  return values
}
function uniqueOptions(base, extra) {
  return [...new Set([...base, ...extra.filter((item) => item !== '' && item !== null && item !== undefined).map(String)])]
}
const sizeOptions = computed(() => uniqueOptions(BASE_SIZES, form.measurements.map((row) => row.size)))
const pointOptions = computed(() => uniqueOptions(BASE_POINTS, form.measurements.map((row) => row.point)))
const unitOptions = computed(() => uniqueOptions(['cm', 'mm'], form.measurements.map((row) => row.unit)))
const valueOptions = computed(() => uniqueOptions(numberOptions(200, 0.5), form.measurements.map((row) => row.value)))
const toleranceOptions = computed(() => uniqueOptions(numberOptions(10, 0.1), form.measurements.flatMap((row) => [row.tolerancePlus, row.toleranceMinus])))
function emptyMeasurement() {
  return { size: 'M', point: '衣长', unit: 'cm', value: '', tolerancePlus: '1', toleranceMinus: '1' }
}
function measurementRows(items) {
  return items?.length ? items.map((row) => ({
    size: String(row.size), point: String(row.point), unit: String(row.unit || 'cm'),
    value: String(row.value), tolerancePlus: String(row.tolerance_plus),
    toleranceMinus: String(row.tolerance_minus),
  })) : [emptyMeasurement()]
}
function setPackForm(source, copy = false) {
  Object.assign(form, {
    newVersion: copy, versionNo: '', patternNo: source && !copy ? source.pattern_no : '',
    requirements: source?.requirements || '', challenges: source?.challenges || '',
    commonMistakes: source?.common_mistakes || '', qualityStandards: source?.quality_standards || '',
    measurements: measurementRows(source?.measurements),
    patternFiles: (source?.pattern_files || []).map((path) => ({ path, name: path.split('/').pop() || '纸样附件' })),
    techFiles: (source?.attachments || []).map((file) => ({
      path: file.path, name: file.name || file.path.split('/').pop() || '工艺附件', note: file.note || '',
    })),
  })
  formError.value = ''
}
function applyTemplate(kind, event) {
  const template = templatesFor(kind).find((item) => item.id === Number(event.target.value))
  event.target.value = ''
  if (!template || !canManage.value) return
  if (kind === 'measurements') form.measurements = measurementRows(template.content)
  else form[TEXT_FIELDS[kind]] = String(template.content || '')
  emit('toast', `已套用模板：${template.name}`)
}
function measurementPayload() {
  if (!form.measurements.length) throw new Error('尺寸表至少保留一行')
  const seen = new Set()
  const result = form.measurements.map((row, index) => {
    if (!row.size || !row.point || !String(row.value).trim()) throw new Error(`第 ${index + 1} 行请完整选择尺码、部位和尺寸值`)
    const numbers = [row.value, row.tolerancePlus, row.toleranceMinus]
    if (numbers.some((value) => !/^-?(?:\d+(?:\.\d+)?|\.\d+)$/.test(String(value).trim()))) throw new Error(`第 ${index + 1} 行尺寸和公差须为数字`)
    const key = `${row.size}|${row.point}`
    if (seen.has(key)) throw new Error(`第 ${index + 1} 行与其他行尺码、部位重复`)
    seen.add(key)
    return {
      size: row.size, point: row.point, unit: row.unit || 'cm', value: String(row.value).trim(),
      tolerance_plus: String(row.tolerancePlus).trim(), tolerance_minus: String(row.toleranceMinus).trim(),
    }
  })
  return result
}
function packPayload() {
  return {
    pattern_no: form.patternNo.trim(), requirements: form.requirements.trim(),
    challenges: form.challenges.trim(), common_mistakes: form.commonMistakes.trim(),
    quality_standards: form.qualityStandards.trim(), measurements: measurementPayload(),
    pattern_files: form.patternFiles.map((file) => file.path),
    tolerances: pack.value && !form.newVersion ? (pack.value.tolerances || {}) : {},
    attachments: form.techFiles.map((file) => ({ name: file.name, path: file.path, note: file.note || '' })),
  }
}
async function loadOrders() {
  const result = await api('/orders', { params: { page_size: 200 } })
  orders.value = rows(result)
  const initial = initialOrderId()
  selectedOrderId.value = String(initial || orders.value[0]?.id || '')
}
async function load() {
  const serial = ++loadSerial
  if (!selectedOrderId.value) {
    status.value = null; pack.value = null; order.value = null; loading.value = false
    return
  }
  loading.value = true; error.value = ''; actionError.value = ''
  const orderId = Number(selectedOrderId.value)
  try {
    const [orderData, statusData, packs, templateData] = await Promise.all([
      api(`/orders/${orderId}`), fetchPreproductionStatus(orderId),
      fetchTechPacks(orderId), fetchCraftTemplates(),
    ])
    if (serial !== loadSerial) return
    order.value = orderData; status.value = statusData
    pack.value = rows(packs).find((item) => item.is_current) || null
    templates.value = rows(templateData)
    setPackForm(pack.value)
  } catch (reason) {
    if (serial === loadSerial) error.value = reason.message || '产前准备加载失败，请检查权限后重试'
  } finally {
    if (serial === loadSerial) loading.value = false
  }
}
function selectOrder() {
  form.newVersion = false; sampleForm.files = []; sampleForm.note = ''; issueDetail.value = ''; briefingNote.value = ''
  load()
}
async function savePack() {
  formError.value = ''
  if (!form.requirements.trim()) { formError.value = '请选择或填写工艺要求'; return }
  let payload
  try { payload = packPayload() } catch (reason) { formError.value = reason.message; return }
  if (form.newVersion && form.versionNo && (!/^\d+$/.test(form.versionNo) || Number(form.versionNo) <= 0)) {
    formError.value = '版本号须为正整数，也可留空自动递增'; return
  }
  saving.value = true
  try {
    if (pack.value && !form.newVersion) await updateTechPack(pack.value.id, payload)
    else await createTechPack({ ...payload, order: Number(selectedOrderId.value), version_no: form.versionNo || undefined })
    emit('toast', form.newVersion ? '新工艺版本已创建' : '工艺资料已保存')
    await load()
  } catch (reason) { formError.value = reason.message } finally { saving.value = false }
}
function openTemplate(kind) {
  Object.assign(modalForm, { kind, name: '', text: '', issueId: 0, approved: false })
  modalError.value = ''; modal.value = 'template'
}
async function submitTemplate() {
  modalError.value = ''
  const kind = modalForm.kind
  const name = modalForm.name.trim()
  if (!name) { modalError.value = '请填写模板名称'; return }
  try {
    const content = kind === 'measurements' ? measurementPayload() : form[TEXT_FIELDS[kind]].trim()
    if (!content) { modalError.value = '当前内容为空，不能保存模板'; return }
    const existing = templatesFor(kind).find((item) => !item.is_system && item.name === name)
    saving.value = true
    await saveCraftTemplate(existing, { kind, name, content })
    templates.value = rows(await fetchCraftTemplates())
    modal.value = ''; emit('toast', existing ? '公司模板已更新' : '模板已保存')
  } catch (reason) { modalError.value = reason.message } finally { saving.value = false }
}
async function addFile(target, event) {
  const file = event.target.files?.[0]
  event.target.value = ''
  if (!file || uploadingTarget.value) return
  uploadingTarget.value = target
  formError.value = ''; actionError.value = ''
  try {
    const result = await uploadPreproductionFile(file)
    if (!result.url?.startsWith('/media/')) throw new Error('服务器未返回有效媒体路径')
    const attachment = { path: result.url, name: result.name || file.name, note: '' }
    if (target === 'sampleFiles') sampleForm.files.push(attachment)
    else form[target].push(attachment)
  } catch (reason) {
    if (target === 'sampleFiles') actionError.value = reason.message
    else formError.value = reason.message
  } finally { uploadingTarget.value = '' }
}
function fileName(path) { return decodeURIComponent(path.split('/').pop() || '查看附件') }
async function submitSample() {
  actionError.value = ''
  if (!sampleForm.note.trim()) { actionError.value = '请填写检验说明'; return }
  saving.value = true
  try {
    let task = [...(pack.value.sample_tasks || [])].sort((a, b) => b.id - a.id)[0]
    if (!task) task = await createSampleTask(pack.value.id, { title: '大货产前样' })
    await recordSampleResult(task.id, {
      result: sampleForm.result, note: sampleForm.note.trim(),
      attachments: sampleForm.files.map((file) => file.path),
    })
    Object.assign(sampleForm, { result: 'passed', note: '', files: [] })
    emit('toast', '产前样结果已追加'); await load()
  } catch (reason) { actionError.value = reason.message } finally { saving.value = false }
}
async function submitIssue() {
  actionError.value = ''
  const detail = issueDetail.value.trim()
  if (!detail) { actionError.value = '请填写问题说明'; return }
  saving.value = true
  try {
    await createReviewIssue(pack.value.id, { title: detail.slice(0, 80), detail })
    issueDetail.value = ''; emit('toast', '问题已登记'); await load()
  } catch (reason) { actionError.value = reason.message } finally { saving.value = false }
}
function openIssueAction(issue, reopen = false) {
  Object.assign(modalForm, { issueId: issue.id, text: '', kind: reopen ? 'reopen' : 'resolve' })
  modalError.value = ''; modal.value = 'issue'
}
async function submitIssueAction() {
  modalError.value = ''
  if (modalForm.kind === 'resolve' && !modalForm.text.trim()) { modalError.value = '请填写处理结果'; return }
  saving.value = true
  try {
    if (modalForm.kind === 'resolve') await resolveReviewIssue(modalForm.issueId, modalForm.text.trim())
    else await reopenReviewIssue(modalForm.issueId, modalForm.text.trim())
    modal.value = ''; emit('toast', modalForm.kind === 'resolve' ? '问题已解决' : '问题已重开'); await load()
  } catch (reason) { modalError.value = reason.message } finally { saving.value = false }
}
async function submitBriefing() {
  if (!window.confirm('请确认当前工艺资料已向生产班组完成交底。此操作将记录当前登录账号和时间。')) return
  actionError.value = ''; saving.value = true
  try {
    await confirmBriefing(pack.value.id, briefingNote.value.trim())
    briefingNote.value = ''; emit('toast', '交底已确认'); await load()
  } catch (reason) { actionError.value = reason.message } finally { saving.value = false }
}
function openDecision(approved) {
  Object.assign(modalForm, { approved, text: '' }); modalError.value = ''; modal.value = 'decision'
}
async function submitDecision() {
  modalError.value = ''
  if (!modalForm.approved && !modalForm.text.trim()) { modalError.value = '驳回必须填写审批意见'; return }
  saving.value = true
  try {
    await decideTechPack(pack.value.id, modalForm.approved, modalForm.text.trim())
    modal.value = ''; emit('toast', modalForm.approved ? '产前审批已通过' : '产前准备已驳回'); await load()
  } catch (reason) { modalError.value = reason.message } finally { saving.value = false }
}
watch(() => props.orderId, (value) => {
  if (value && Number(value) !== Number(selectedOrderId.value)) { selectedOrderId.value = String(value); load() }
})
onMounted(async () => {
  loading.value = true
  try { await loadOrders(); await load() } catch (reason) { error.value = reason.message; loading.value = false }
})
</script>

<template>
  <div class="preproduction-view">
    <div class="welcome">
      <div><h2>产前准备</h2><p>按工艺资料、产前样、问题闭环、交底审批四步完成大货放行。</p></div>
      <button class="button secondary" :disabled="loading" @click="load"><AppIcon name="refresh" :size="16" />刷新</button>
    </div>

    <section class="card preproduction-order-bar">
      <label class="field"><span>客户订单</span><select v-model="selectedOrderId" aria-label="选择产前准备订单" @change="selectOrder"><option value="" disabled>请选择订单</option><option v-for="item in orders" :key="item.id" :value="String(item.id)">{{ item.order_no }} · {{ item.style_no || '未关联款式' }} · {{ item.customer_name || '未指定客户' }}</option></select></label>
      <div v-if="selectedOrder" class="preproduction-order-meta"><span>款号 <strong>{{ selectedOrder.style_no || '—' }}</strong></span><span>客户 <strong>{{ selectedOrder.customer_name || '—' }}</strong></span><span>交期 <strong>{{ selectedOrder.delivery_date || '未设置' }}</strong></span></div>
    </section>

    <PageState :loading="loading && !status" :error="error" :empty="!selectedOrderId" empty-text="暂无可选订单" @retry="load">
      <template v-if="status">
        <section class="card preproduction-summary">
          <div class="preproduction-summary-head"><div><h3>{{ selectedOrder?.order_no || order?.order_no }}</h3><p>{{ currentPackLabel }}</p></div><span class="badge" :class="statusClass">{{ status.status_display }}</span></div>
          <div v-if="status.missing_items?.length" class="preproduction-missing"><strong>还需完成</strong><ul><li v-for="item in status.missing_items" :key="item">{{ item }}</li></ul></div>
          <p v-else-if="status.status === 'pending_approval'" class="preproduction-ready">资料已齐，等待产前审批。</p>
          <p v-else-if="status.status === 'approved'" class="preproduction-ready">当前订单和工艺版本已有有效审批。</p>
        </section>

        <nav class="preproduction-tabs" aria-label="产前准备步骤"><button v-for="(tab, index) in tabs" :key="tab[0]" :class="{ active: activeTab === tab[0] }" @click="activeTab = tab[0]"><span>{{ index + 1 }}</span>{{ tab[1] }}</button></nav>
        <p v-if="actionError" class="form-error preproduction-action-error" role="alert">{{ actionError }}</p>

        <section v-if="activeTab === 'craft'" class="card preproduction-section">
          <div class="card-header"><div><h3>工艺资料</h3><p>选择模板后可按本单调整，保存后生效。纸样编号由后端自动生成。</p></div><button v-if="canManage && pack && !form.newVersion" class="button ghost small" @click="setPackForm(pack, true)">新建版本</button><button v-if="form.newVersion" class="button ghost small" @click="setPackForm(pack)">取消新版本</button></div>
          <p v-if="form.newVersion" class="preproduction-version-tip">将复制当前资料创建新版本；旧审批保留但不会自动恢复。</p>
          <div class="preproduction-craft-grid">
            <label v-if="form.newVersion" class="field"><span>版本号（留空自动递增）</span><input v-model="form.versionNo" type="number" min="1" step="1" placeholder="例如 2" /></label>
            <div class="field"><span>纸样编号</span><div class="preproduction-readonly">{{ form.newVersion || !pack ? '保存后自动生成' : form.patternNo }}</div></div>
          </div>

          <section class="preproduction-form-section">
            <h4>纸样与附件</h4>
            <div v-for="group in [{ target: 'patternFiles', title: '纸样附件' }, { target: 'techFiles', title: '工艺单附件' }]" :key="group.target" class="preproduction-attachments">
              <div class="preproduction-section-line"><strong>{{ group.title }}</strong><label v-if="canManage" class="button ghost small preproduction-upload">{{ uploadingTarget === group.target ? '上传中…' : '上传附件' }}<input type="file" accept=".pdf,.doc,.docx,.xls,.xlsx,.jpg,.jpeg,.png,.gif,.webp" :disabled="!!uploadingTarget" @change="addFile(group.target, $event)" /></label></div>
              <p v-if="!form[group.target].length" class="section-note">暂无附件</p>
              <div v-for="(file, index) in form[group.target]" :key="`${file.path}-${index}`" class="preproduction-file"><a :href="file.path" target="_blank" rel="noopener">{{ file.name }}</a><button v-if="canManage" type="button" class="icon-button" aria-label="移除附件" @click="form[group.target].splice(index, 1)"><AppIcon name="close" :size="15" /></button></div>
            </div>
            <p class="section-note">支持 PDF、Word、Excel 和常见图片，单文件不超过 20MB；移除后须保存工艺资料才生效。</p>
          </section>

          <section class="preproduction-form-section">
            <div class="preproduction-section-line"><h4>结构化尺寸表 *</h4><div><select v-if="canManage && templatesFor('measurements').length" aria-label="选择尺寸模板" @change="applyTemplate('measurements', $event)"><option value="">选择尺寸模板</option><option v-for="item in templatesFor('measurements')" :key="item.id" :value="item.id">{{ item.name }}{{ item.is_system ? '（标准）' : '' }}</option></select><button v-if="canManage" class="button ghost small" @click="openTemplate('measurements')">存为模板</button></div></div>
            <p class="section-note">全部通过选择填写；尺寸步长 0.5，公差步长 0.1。</p>
            <div class="preproduction-measurements">
              <article v-for="(row, index) in form.measurements" :key="index" class="preproduction-measurement">
                <div class="preproduction-measurement-head"><strong>尺寸行 {{ index + 1 }}</strong><button v-if="canManage && form.measurements.length > 1" class="button ghost small" @click="form.measurements.splice(index, 1)">删除</button></div>
                <div class="preproduction-measurement-grid">
                  <label class="field"><span>尺码</span><select v-model="row.size" :disabled="!canManage"><option v-for="item in sizeOptions" :key="item" :value="item">{{ item }}</option></select></label>
                  <label class="field"><span>部位</span><select v-model="row.point" :disabled="!canManage"><option v-for="item in pointOptions" :key="item" :value="item">{{ item }}</option></select></label>
                  <label class="field"><span>尺寸值</span><select v-model="row.value" :disabled="!canManage"><option value="">请选择</option><option v-for="item in valueOptions" :key="item" :value="item">{{ item }}</option></select></label>
                  <label class="field"><span>单位</span><select v-model="row.unit" :disabled="!canManage"><option v-for="item in unitOptions" :key="item" :value="item">{{ item }}</option></select></label>
                  <label class="field"><span>正公差</span><select v-model="row.tolerancePlus" :disabled="!canManage"><option v-for="item in toleranceOptions" :key="item" :value="item">＋{{ item }}</option></select></label>
                  <label class="field"><span>负公差</span><select v-model="row.toleranceMinus" :disabled="!canManage"><option v-for="item in toleranceOptions" :key="item" :value="item">－{{ item }}</option></select></label>
                </div>
              </article>
            </div>
            <button v-if="canManage" class="button secondary small" @click="form.measurements.push(emptyMeasurement())">添加尺寸行</button>
          </section>

          <section v-for="block in craftBlocks" :key="block.kind" class="preproduction-form-section">
            <div class="preproduction-section-line"><h4>{{ block.title }}{{ block.required ? ' *' : '' }}</h4><div><select v-if="canManage && templatesFor(block.kind).length" :aria-label="`选择${block.title}模板`" @change="applyTemplate(block.kind, $event)"><option value="">选择标准或公司模板</option><option v-for="item in templatesFor(block.kind)" :key="item.id" :value="item.id">{{ item.name }}{{ item.is_system ? '（标准）' : '' }}</option></select><button v-if="canManage" class="button ghost small" @click="openTemplate(block.kind)">存为模板</button></div></div>
            <textarea v-model="form[block.field]" rows="4" :disabled="!canManage" :placeholder="block.placeholder" maxlength="10000" />
          </section>
          <p v-if="formError" class="form-error" role="alert">{{ formError }}</p>
          <div v-if="canManage" class="form-actions"><button class="button primary" :disabled="saving || !!uploadingTarget" @click="savePack">{{ saving ? '保存中…' : (form.newVersion ? '创建新工艺版本' : '保存工艺资料') }}</button></div>
          <p v-else class="section-note">当前账号仅可查看，工艺资料由工艺负责人维护。</p>
        </section>

        <section v-else-if="activeTab === 'sample'" class="card preproduction-section">
          <div class="card-header"><div><h3>产前样</h3><p>每次检验追加记录，不覆盖历史。</p></div></div>
          <div v-if="status.current_sample" class="preproduction-current"><span>当前结果</span><strong :class="status.current_sample.result === 'passed' ? 'text-good' : 'text-danger'">{{ resultText(status.current_sample.result) }}</strong><small>{{ status.current_sample.operator_name }} · {{ fullDateTime(status.current_sample.occurred_at) }}</small></div>
          <p v-else class="section-note">尚未记录产前样检验结果。</p>
          <form v-if="canManage && pack" class="preproduction-sample-form" @submit.prevent="submitSample"><div class="form-grid"><label class="field"><span>追加检验结果</span><select v-model="sampleForm.result"><option value="passed">合格</option><option value="failed">不合格</option></select></label><label class="field full"><span>检验说明 *</span><textarea v-model="sampleForm.note" rows="3" required maxlength="10000" placeholder="记录样衣检验情况" /></label></div><div class="preproduction-section-line"><strong>样衣图片或附件</strong><label class="button ghost small preproduction-upload">{{ uploadingTarget === 'sampleFiles' ? '上传中…' : '上传附件' }}<input type="file" accept=".pdf,.doc,.docx,.xls,.xlsx,.jpg,.jpeg,.png,.gif,.webp" :disabled="!!uploadingTarget" @change="addFile('sampleFiles', $event)" /></label></div><div v-for="(file, index) in sampleForm.files" :key="file.path" class="preproduction-file"><a :href="file.path" target="_blank" rel="noopener">{{ file.name }}</a><button type="button" class="icon-button" aria-label="移除样衣附件" @click="sampleForm.files.splice(index, 1)"><AppIcon name="close" :size="15" /></button></div><div class="form-actions"><button class="button primary" :disabled="saving || !!uploadingTarget">追加产前样结果</button></div></form>
          <p v-else-if="!pack" class="section-note">请先在“工艺资料”页保存当前工艺。</p><p v-else class="section-note">当前账号仅可查看，产前样由工艺负责人登记。</p>
          <div class="preproduction-history"><div class="preproduction-section-line"><h4>检验历史（{{ sampleHistory.length }}）</h4></div><p v-if="!sampleHistory.length" class="section-note">暂无历史记录</p><article v-for="record in sampleHistory" :key="record.id"><div class="preproduction-history-head"><strong :class="record.result === 'passed' ? 'text-good' : 'text-danger'">{{ resultText(record.result) }}</strong><time>{{ fullDateTime(record.occurred_at) }}</time></div><p>{{ record.note || '无补充说明' }}</p><small>{{ record.taskTitle }} · 确认人：{{ record.operator_name || '—' }}</small><div v-if="record.attachments?.length" class="preproduction-history-files"><a v-for="path in record.attachments" :key="path" :href="path" target="_blank" rel="noopener">{{ fileName(path) }}</a></div></article></div>
        </section>

        <section v-else-if="activeTab === 'issues'" class="card preproduction-section">
          <div class="card-header"><div><h3>工艺问题与处理</h3><p>问题全部解决后才可审批。</p></div><span v-if="status.unresolved_issue_count" class="badge overdue">未解决 {{ status.unresolved_issue_count }}</span></div>
          <form v-if="canManage && pack" class="preproduction-issue-create" @submit.prevent="submitIssue"><label class="field"><span>登记问题</span><textarea v-model="issueDetail" rows="3" required maxlength="10000" placeholder="说明问题现象、位置和要求" /></label><button class="button secondary" :disabled="saving">登记问题</button></form>
          <p v-if="!pack" class="section-note">请先保存工艺资料。</p><p v-else-if="!pack.review_issues?.length" class="section-note">暂无工艺问题。</p>
          <div class="preproduction-issues"><article v-for="issue in pack?.review_issues || []" :key="issue.id"><div class="preproduction-history-head"><strong>{{ issue.detail || issue.title }}</strong><span class="badge" :class="issue.status === 'closed' ? 'done' : 'overdue'">{{ issue.status === 'closed' ? '已解决' : '未解决' }}</span></div><p v-if="issue.rectification" class="preproduction-resolution">处理结果：{{ issue.rectification }}</p><p v-if="issue.review_note && issue.status !== 'closed'" class="section-note">重开原因：{{ issue.review_note }}</p><small v-if="issue.handled_by_name">处理人：{{ issue.handled_by_name }} · {{ fullDateTime(issue.reviewed_at) }}</small><small v-else>提出人：{{ issue.raised_by_name || '—' }} · {{ fullDateTime(issue.created_at) }}</small><div v-if="canManage" class="preproduction-issue-actions"><button v-if="issue.status !== 'closed'" class="button primary small" :disabled="saving" @click="openIssueAction(issue)">填写处理结果并解决</button><button v-else class="button ghost small" :disabled="saving" @click="openIssueAction(issue, true)">重开问题</button></div></article></div>
        </section>

        <section v-else class="card preproduction-section">
          <div class="card-header"><div><h3>交底与产前确认</h3><p>负责人交底确认后，由审批人正式放行。</p></div></div>
          <div v-if="status.briefing_confirmation" class="preproduction-confirmation"><strong class="text-good">生产负责人已确认班组交底</strong><span>{{ status.briefing_confirmation.confirmed_by_name }} · {{ fullDateTime(status.briefing_confirmation.confirmed_at) }}</span><p v-if="status.briefing_confirmation.note">说明：{{ status.briefing_confirmation.note }}</p></div>
          <form v-else-if="canBrief && pack" class="preproduction-briefing-form" @submit.prevent="submitBriefing"><label class="field"><span>交底说明（可选）</span><textarea v-model="briefingNote" rows="3" maxlength="10000" /></label><button class="button primary" :disabled="saving">确认已完成班组交底</button></form>
          <p v-else class="section-note">等待生产负责人确认当前工艺已完成班组交底。</p>
          <section class="preproduction-approval-panel"><h4>正式审批</h4><div v-if="status.approval" class="preproduction-confirmation"><strong class="text-good">当前审批有效</strong><span>{{ status.approval.decided_by_name }} · {{ fullDateTime(status.approval.decided_at) }}</span></div><p v-else class="section-note">审批将绑定当前工艺和产前事实快照，后续变更需重新审批。</p><div v-if="canApprove && pack && !status.approved" class="form-actions"><button class="button danger" :disabled="saving" @click="openDecision(false)">驳回</button><button class="button primary" :disabled="saving || !!status.missing_items?.length" :title="status.missing_items?.length ? '请先完成后端列出的产前准备事项' : ''" @click="openDecision(true)">审批通过</button></div></section>
          <div v-if="pack?.briefing_confirmations?.length" class="preproduction-history compact"><h4>交底确认历史</h4><article v-for="item in pack.briefing_confirmations" :key="item.id"><strong>{{ item.confirmed_by_name }}</strong><small>{{ fullDateTime(item.confirmed_at) }}<template v-if="item.note"> · {{ item.note }}</template></small></article></div>
          <div v-if="pack?.approvals?.length" class="preproduction-history"><h4>审批历史</h4><article v-for="item in [...pack.approvals].sort((a, b) => b.id - a.id)" :key="item.id"><div class="preproduction-history-head"><strong>{{ approvalText(item.status) }} · {{ item.decided_by_name || '—' }}</strong><time>{{ fullDateTime(item.decided_at) }}</time></div><p v-if="item.reason">意见：{{ item.reason }}</p></article></div>
        </section>
      </template>
    </PageState>

    <AdminModal v-if="modal === 'template'" :title="modalForm.kind === 'measurements' ? '保存尺寸模板' : '保存工艺文本模板'" description="同名公司模板将更新；系统标准模板不会被修改。" :saving="saving" @close="modal = ''"><form @submit.prevent="submitTemplate"><label class="field"><span>模板名称 *</span><input v-model="modalForm.name" required maxlength="80" autofocus /></label><p v-if="modalError" class="form-error">{{ modalError }}</p><div class="form-actions"><button type="button" class="button secondary" @click="modal = ''">取消</button><button class="button primary" :disabled="saving">保存模板</button></div></form></AdminModal>
    <AdminModal v-if="modal === 'issue'" :title="modalForm.kind === 'resolve' ? '填写处理结果' : '重开问题'" :saving="saving" @close="modal = ''"><form @submit.prevent="submitIssueAction"><label class="field"><span>{{ modalForm.kind === 'resolve' ? '处理结果 *' : '重开原因（可选）' }}</span><textarea v-model="modalForm.text" rows="5" :required="modalForm.kind === 'resolve'" maxlength="10000" /></label><p v-if="modalError" class="form-error">{{ modalError }}</p><div class="form-actions"><button type="button" class="button secondary" @click="modal = ''">取消</button><button class="button primary" :disabled="saving">确认</button></div></form></AdminModal>
    <AdminModal v-if="modal === 'decision'" :title="modalForm.approved ? '产前审批通过' : '驳回产前准备'" :description="modalForm.approved ? '确认当前订单和工艺版本已具备大货生产条件？' : '请填写驳回意见，当前审批结果将保留在历史中。'" :saving="saving" @close="modal = ''"><form @submit.prevent="submitDecision"><label class="field"><span>{{ modalForm.approved ? '审批意见（可选）' : '审批意见 *' }}</span><textarea v-model="modalForm.text" rows="5" :required="!modalForm.approved" maxlength="255" /></label><p v-if="modalError" class="form-error">{{ modalError }}</p><div class="form-actions"><button type="button" class="button secondary" @click="modal = ''">取消</button><button class="button" :class="modalForm.approved ? 'primary' : 'danger'" :disabled="saving">确认{{ modalForm.approved ? '通过' : '驳回' }}</button></div></form></AdminModal>
  </div>
</template>

<style scoped>
.preproduction-order-bar{display:flex;align-items:end;gap:24px;margin-bottom:16px;padding:16px 20px}.preproduction-order-bar>.field{width:min(480px,100%);margin:0}.preproduction-order-meta{display:flex;flex-wrap:wrap;gap:12px 24px;padding-bottom:9px;color:var(--muted);font-size:12px}.preproduction-order-meta strong{margin-left:5px;color:var(--ink);font-weight:500}.preproduction-summary{padding:20px;margin-bottom:14px}.preproduction-summary-head{display:flex;align-items:flex-start;justify-content:space-between;gap:16px}.preproduction-summary h3{margin:0;font-size:18px}.preproduction-summary p{margin:6px 0 0;color:var(--muted);font-size:12px}.preproduction-missing{margin-top:16px;padding:13px 16px;border-radius:9px;background:var(--orange-soft);color:var(--orange-dark);font-size:12px}.preproduction-missing ul{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:5px 24px;margin:8px 0 0;padding-left:18px}.preproduction-ready{padding:12px 16px;background:#EAF7EF;color:var(--good)!important;border-radius:9px;margin-top:16px!important}.preproduction-tabs{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-bottom:14px}.preproduction-tabs button{display:flex;align-items:center;justify-content:center;gap:8px;min-height:44px;border:1px solid var(--line);border-radius:10px;background:#fff;color:var(--text-secondary)}.preproduction-tabs button span{display:grid;place-items:center;width:22px;height:22px;border-radius:50%;background:#F2F3F5;font-size:11px}.preproduction-tabs button.active{border-color:var(--orange);background:var(--orange-soft);color:var(--orange-dark);font-weight:600}.preproduction-tabs button.active span{background:var(--orange);color:#fff}.preproduction-section{padding:22px}.preproduction-section .card-header{margin-bottom:18px}.preproduction-section .card-header h3{margin:0}.preproduction-section h4{margin:0;font-size:14px}.preproduction-version-tip{padding:11px 14px;border-radius:8px;background:var(--orange-soft);color:var(--orange-dark);font-size:12px}.preproduction-craft-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px;max-width:700px}.preproduction-readonly{min-height:40px;padding:10px 12px;border-radius:8px;background:#F5F6F8;color:var(--text-secondary)}.preproduction-form-section{padding:20px 0;border-bottom:1px solid var(--line)}.preproduction-form-section:last-of-type{border-bottom:0}.preproduction-form-section>textarea{width:100%;margin-top:12px;padding:11px 12px;border:1px solid var(--line);border-radius:8px;resize:vertical;background:#fff;color:var(--ink)}.preproduction-form-section>textarea:disabled{background:#F7F8FA;color:var(--text-secondary)}.preproduction-section-line{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap}.preproduction-section-line>div{display:flex;align-items:center;gap:8px;flex-wrap:wrap}.preproduction-section-line select{max-width:260px;min-height:32px;padding:5px 9px;border:1px solid var(--line);border-radius:7px;background:#fff;color:var(--ink)}.preproduction-attachments{margin-top:16px}.preproduction-file{display:flex;align-items:center;gap:8px;padding:8px 0;border-bottom:1px dashed var(--line);font-size:12px}.preproduction-file a{min-width:0;flex:1;color:var(--orange-dark);overflow-wrap:anywhere;text-decoration:none}.preproduction-file .icon-button{width:28px;height:28px}.preproduction-upload{position:relative;overflow:hidden;cursor:pointer}.preproduction-upload input{position:absolute;inset:0;width:100%;height:100%;opacity:0;cursor:pointer}.preproduction-measurements{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;margin:14px 0}.preproduction-measurement{padding:14px;border:1px solid var(--line);border-radius:10px;background:#FCFBFA}.preproduction-measurement-head{display:flex;align-items:center;justify-content:space-between;margin-bottom:10px;font-size:12px}.preproduction-measurement-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:9px}.preproduction-measurement-grid .field{margin:0}.preproduction-measurement-grid select{width:100%;min-width:0;padding:8px}.preproduction-current{display:flex;align-items:center;gap:12px;flex-wrap:wrap;padding:14px;border-radius:9px;background:#F7F8FA}.preproduction-current>span{color:var(--muted);font-size:12px}.preproduction-current strong{font-size:18px}.preproduction-current small{margin-left:auto;color:var(--muted)}.text-good{color:var(--good)}.text-danger{color:var(--danger)}.preproduction-sample-form,.preproduction-issue-create,.preproduction-briefing-form{margin-top:18px;padding:16px;border:1px solid var(--line);border-radius:10px}.preproduction-sample-form .form-grid{margin-bottom:14px}.preproduction-sample-form .form-actions,.preproduction-issue-create>.button,.preproduction-briefing-form>.button{margin-top:12px}.preproduction-issue-create>.field textarea,.preproduction-briefing-form textarea{width:100%}.preproduction-history{margin-top:22px}.preproduction-history>h4{margin-bottom:10px}.preproduction-history article,.preproduction-issues article{padding:14px 0;border-top:1px solid var(--line)}.preproduction-history article p,.preproduction-issues article p{margin:8px 0;color:var(--text-secondary);font-size:13px;white-space:pre-wrap;overflow-wrap:anywhere}.preproduction-history article small,.preproduction-issues article small{color:var(--muted);font-size:11px}.preproduction-history-head{display:flex;align-items:flex-start;justify-content:space-between;gap:12px}.preproduction-history-head strong{overflow-wrap:anywhere}.preproduction-history-head time{flex:none;color:var(--muted);font-size:11px}.preproduction-history-files{display:flex;gap:8px;flex-wrap:wrap;margin-top:9px}.preproduction-history-files a{color:var(--orange-dark);font-size:11px}.preproduction-resolution{padding:10px 12px;border-radius:8px;background:#F7F8FA}.preproduction-issue-actions{margin-top:12px}.preproduction-confirmation{display:flex;flex-direction:column;gap:7px;padding:15px;border:1px solid #B7EBC5;border-radius:10px;background:#F0FAF2;font-size:13px}.preproduction-confirmation p{margin:0;color:var(--text-secondary)}.preproduction-approval-panel{margin-top:20px;padding:18px;border:1px solid var(--line);border-radius:10px}.preproduction-approval-panel .section-note{margin-top:10px}.compact article{padding:10px 0}.compact article strong,.compact article small{display:block;margin-bottom:5px}.preproduction-action-error{margin-bottom:14px}.form-error{white-space:pre-wrap}
@media(max-width:1000px){.preproduction-measurements{grid-template-columns:1fr}.preproduction-order-bar{align-items:stretch;flex-direction:column;gap:12px}.preproduction-order-bar>.field{width:100%}.preproduction-order-meta{padding-bottom:0}}
@media(max-width:760px){.preproduction-order-bar,.preproduction-summary,.preproduction-section{padding:15px}.preproduction-order-meta{gap:8px 14px}.preproduction-tabs{gap:5px;position:sticky;top:0;z-index:4;padding:5px 0;background:#F5F6F8}.preproduction-tabs button{flex-direction:column;gap:3px;min-height:54px;padding:6px 2px;font-size:11px}.preproduction-tabs button span{width:19px;height:19px}.preproduction-missing ul{grid-template-columns:1fr}.preproduction-craft-grid{grid-template-columns:1fr}.preproduction-measurement-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.preproduction-section-line{align-items:flex-start}.preproduction-section-line>div{justify-content:flex-end}.preproduction-section-line select{max-width:170px}.preproduction-current small{width:100%;margin-left:0}.preproduction-history-head{flex-wrap:wrap}.preproduction-history-head time{width:100%}.preproduction-form-section{padding:16px 0}}
</style>
