<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { api, createRequestKey, saveSession } from '../api'
import { money } from '../format'
import AppIcon from '../components/AppIcon.vue'
import { enqueueScan, flushOfflineQueue, offlineQueue, removeQueuedScan } from '../offline-queue'

const emit = defineEmits(['toast', 'navigate'])
const mode = ref('scan')
const flowState = ref('idle')
const cameraActive = ref(false)
const cameraError = ref('')
const working = ref(false)
const error = ref('')
const success = ref('')
const video = ref(null)
const imageInput = ref(null)
const queuedItems = ref([])
const queueBusy = ref(false)
const qr = ref('')
const manual = reactive({ bed_no: '', bundle_no: '' })
const candidates = ref([])
const ticket = ref(null)
const styleCraft = ref(null), styleCraftOpen = ref(false), styleCraftLoading = ref(false), styleCraftError = ref('')
const processes = ref([])
const selectedProcess = ref('')
const qty = ref(1)
const qtyEditing = ref(false)
const fixedQty = ref(false)
const assistants = ref([])
const selectedUser = ref('')
const todayQty = ref(null)
const todayAmount = ref('0.00')
const hideScanPrice = ref(false)
const largeFont = ref(localStorage.getItem('yisao.web.scan-large-font') === '1')
const speechEnabled = ref(localStorage.getItem('yisao.web.scan-speech') === '1')
const joinOpen = ref(false)
const joinCompanyNo = ref('')
const joinWorking = ref(false)
const joinError = ref('')
let stream, scanTimer, detector

const LARGE_QTY_THRESHOLD = 50
const maxQty = computed(() => Number(ticket.value?.ticket?.qty || ticket.value?.qty || 0))
const ticketView = computed(() => ticket.value?.ticket || ticket.value || {})
const productionId = computed(() => ticket.value?.production?.id || ticketView.value.production_id || '')
const selectedProcessView = computed(() => processes.value.find((item) => item.id === Number(selectedProcess.value)))
const isUnpriced = (process) => process.price === null || process.price === undefined || Number(process.price) <= 0

function clearTicket() {
  ticket.value = null
  processes.value = []
  selectedProcess.value = ''
  candidates.value = []
  fixedQty.value = false
  qtyEditing.value = false
  flowState.value = 'idle'
  styleCraftOpen.value = false
  styleCraft.value = null
  styleCraftError.value = ''
}
async function openStyleCraft() {
  const styleId = ticket.value?.style?.id || ticketView.value.style_id
  if (!styleId) { styleCraftError.value = '该菲票未关联款式'; styleCraftOpen.value = true; return }
  styleCraftOpen.value = true; styleCraftLoading.value = true; styleCraftError.value = ''
  try { styleCraft.value = await api(`/styles/${Number(styleId)}`) } catch (e) { styleCraftError.value = e.message } finally { styleCraftLoading.value = false }
}
function resetResult() {
  clearTicket()
  error.value = ''
  success.value = ''
}
function speak(text) {
  if (!speechEnabled.value || !('speechSynthesis' in window)) return
  window.speechSynthesis.cancel()
  const utterance = new SpeechSynthesisUtterance(text)
  utterance.lang = 'zh-CN'
  window.speechSynthesis.speak(utterance)
}
function toggleLargeFont() {
  largeFont.value = !largeFont.value
  localStorage.setItem('yisao.web.scan-large-font', largeFont.value ? '1' : '0')
}
function toggleSpeech() {
  if (!speechEnabled.value && !('speechSynthesis' in window)) {
    error.value = '当前浏览器不支持语音报数，请升级浏览器后重试'
    return
  }
  speechEnabled.value = !speechEnabled.value
  localStorage.setItem('yisao.web.scan-speech', speechEnabled.value ? '1' : '0')
  if (speechEnabled.value && qty.value) speak(`本次登记${Number(qty.value)}件`)
}
function chooseProcess(id) {
  const process = processes.value.find((item) => item.id === Number(id))
  if (!process) { selectedProcess.value = ''; return }
  if (isUnpriced(process)) { error.value = `工序【${process.name}】尚未配置工价，请联系管理员`; return }
  selectedProcess.value = process.id
  qty.value = maxQty.value || 1
  fixedQty.value = false
  qtyEditing.value = false
  error.value = ''
  flowState.value = 'qty'
  speak(`本次登记${Number(qty.value)}件`)
}
function applyProcessData(data) {
  processes.value = data.results || []
  const current = data.current_process
  const currentOption = current && processes.value.find((item) => item.id === current.id && !isUnpriced(item))
  if (currentOption) {
    selectedProcess.value = currentOption.id
    qty.value = Number(current.remaining) > 0 ? Number(current.remaining) : maxQty.value || 1
    fixedQty.value = Number(current.remaining) > 0
    flowState.value = 'qty'
    speak(`本次登记${Number(qty.value)}件`)
  } else if (data.single && processes.value.length && !isUnpriced(processes.value[0])) {
    chooseProcess(processes.value[0].id)
  } else {
    selectedProcess.value = ''
    fixedQty.value = false
    flowState.value = 'process'
    if (!processes.value.length) error.value = '暂无可选工序，请先配置固定工序或员工指定工序'
  }
}
async function loadScanSettings() {
  try {
    const settings = await api('/settings')
    hideScanPrice.value = settings.hide_scan_price === '1'
  } catch { /* 设置读取失败不阻断扫菲，默认沿用显示价格 */ }
}
function openJoinCompany(companyNo) {
  stopCamera()
  joinCompanyNo.value = companyNo
  joinError.value = ''
  joinOpen.value = true
}
async function submitJoinCompany() {
  const companyNo = joinCompanyNo.value.trim()
  if (!/^\d{1,8}$/.test(companyNo)) { joinError.value = '请输入 1–8 位公司编号'; return }
  joinWorking.value = true
  joinError.value = ''
  try {
    const result = await api('/companies/join', { method: 'POST', body: { company_no: companyNo } })
    const companies = await api('/companies/mine')
    saveSession({ companies })
    joinOpen.value = false
    success.value = result.message || '申请已提交，公司列表已刷新'
    emit('toast', '公司列表已刷新')
  } catch (e) { joinError.value = e.message } finally { joinWorking.value = false }
}
async function refreshToday() {
  try {
    const data = await api('/records/workbench')
    todayQty.value = (data.my_today || []).reduce((sum, record) => sum + Number(record.qty || 0), 0)
    todayAmount.value = data.my_today_amount || '0.00'
  } catch { /* 累计查询失败不阻断扫菲 */ }
}
async function parseCode(value) {
  if (!value?.trim()) { error.value = '请扫描或粘贴菲票二维码内容'; return }
  const code = value.trim()
  if (code.startsWith('JOIN:')) {
    openJoinCompany(code.slice(5).trim())
    qr.value = ''
    return
  }
  working.value = true
  error.value = ''
  success.value = ''
  try {
    const result = await api('/scan/parse', { method: 'POST', body: { qr: code } })
    await selectTicket(result)
    stopCamera()
    qr.value = ''
  } catch (e) { error.value = e.message } finally { working.value = false }
}
async function selectTicket(result) {
  ticket.value = result
  qty.value = Number(result.ticket?.qty || result.qty || 1)
  candidates.value = []
  error.value = ''
  success.value = ''
  qtyEditing.value = false
  fixedQty.value = false
  flowState.value = 'process'
  const data = await api('/records/process-selector', {
    params: { ticket_id: result.ticket?.id || result.id, user_id: selectedUser.value || undefined },
  })
  api('/employees').then((list) => { assistants.value = Array.isArray(list) ? list : list.results || [] }).catch(() => {})
  applyProcessData(data)
  await nextTick()
  document.getElementById('register-card')?.scrollIntoView({ behavior: 'smooth', block: 'center' })
}
async function lookup() {
  if (!manual.bed_no.trim() || !manual.bundle_no.trim()) { error.value = '请输入床次号和扎号'; return }
  working.value = true
  clearTicket()
  error.value = ''
  success.value = ''
  try {
    const result = await api('/tickets/lookup', { params: { bed_no: manual.bed_no.trim(), bundle_no: manual.bundle_no.trim() } })
    candidates.value = Array.isArray(result) ? result : result.results || []
    if (!candidates.value.length) error.value = '未找到对应菲票，请核对床次号和扎号'
    if (candidates.value.length === 1) await selectTicket(candidates.value[0])
  } catch (e) { error.value = e.message } finally { working.value = false }
}
async function refreshProcesses() {
  if (!ticket.value) return
  error.value = ''
  try {
    const data = await api('/records/process-selector', {
      params: { ticket_id: ticketView.value.id, user_id: selectedUser.value || undefined },
    })
    applyProcessData(data)
  } catch (e) { error.value = e.message }
}
async function startCamera() {
  cameraError.value = ''
  if (!window.isSecureContext) { cameraError.value = '浏览器相机要求 HTTPS 或 localhost，请使用下方手工方式。'; return }
  if (!('BarcodeDetector' in window)) { cameraError.value = '当前浏览器不支持二维码识别，请使用最新版 Chrome/Edge，或粘贴二维码内容。'; return }
  try {
    detector = new window.BarcodeDetector({ formats: ['qr_code'] })
    stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: { ideal: 'environment' } }, audio: false })
    cameraActive.value = true
    await nextTick()
    video.value.srcObject = stream
    await video.value.play()
    scanFrame()
  } catch (e) {
    cameraError.value = e.name === 'NotAllowedError' ? '未获得相机权限，请在浏览器设置中允许后重试。' : `相机启动失败：${e.message}`
    stopCamera()
  }
}
async function scanFrame() {
  if (!cameraActive.value || !video.value) return
  try {
    const codes = await detector.detect(video.value)
    if (codes.length) { await parseCode(codes[0].rawValue); return }
  } catch { /* 单帧失败继续 */ }
  scanTimer = setTimeout(scanFrame, 350)
}
function stopCamera() {
  cameraActive.value = false
  clearTimeout(scanTimer)
  stream?.getTracks().forEach((track) => track.stop())
  stream = null
}
function chooseAlbumImage() {
  cameraError.value = ''
  if (!('BarcodeDetector' in window)) {
    cameraError.value = '当前浏览器不支持相册图片二维码识别，请使用最新版 Chrome/Edge，或粘贴二维码内容。'
    return
  }
  imageInput.value?.click()
}
async function decodeImage(file) {
  if (typeof window.createImageBitmap === 'function') return window.createImageBitmap(file)
  const url = URL.createObjectURL(file)
  try {
    const image = new Image()
    image.src = url
    await image.decode()
    return image
  } finally {
    // Image 解码完成后已持有图片数据，可以释放本地 URL。
    URL.revokeObjectURL(url)
  }
}
async function recognizeAlbumImage(event) {
  const file = event.target.files?.[0]
  event.target.value = ''
  if (!file) return
  working.value = true
  cameraError.value = ''
  let bitmap
  try {
    const imageDetector = new window.BarcodeDetector({ formats: ['qr_code'] })
    bitmap = await decodeImage(file)
    const codes = await imageDetector.detect(bitmap)
    if (!codes.length || !codes[0].rawValue) {
      cameraError.value = '图片中未识别到二维码，请选择清晰、完整的菲票图片。'
      return
    }
    await parseCode(codes[0].rawValue)
  } catch (e) {
    cameraError.value = `图片二维码识别失败：${e.message || '请更换图片或使用手工方式'}`
  } finally {
    bitmap?.close?.()
    working.value = false
  }
}
function refreshOfflineQueue() {
  queuedItems.value = offlineQueue()
}
async function resendQueue() {
  if (queueBusy.value) return
  queueBusy.value = true
  try {
    const result = await flushOfflineQueue()
    refreshOfflineQueue()
    if (!result) return
    if (result.sent) {
      success.value = `已成功同步 ${result.sent} 条登记`
      emit('toast', `离线登记同步成功 ${result.sent} 条`)
      refreshToday()
    }
    if (result.rejected) error.value = `${result.rejected} 条登记被服务端拒绝，已保留在队列中，请查看原因`
    else if (result.networkFailed) error.value = '网络仍未恢复，待同步登记已保留'
    else if (result.sent) error.value = ''
  } finally {
    refreshOfflineQueue()
    queueBusy.value = false
  }
}
function discardQueueItem(item) {
  if (!window.confirm('仅从本机删除这条待同步登记，不代表撤销服务端已存在的记录。确定删除吗？')) return
  removeQueuedScan(item.idempotencyKey)
  refreshOfflineQueue()
}
async function register() {
  if (working.value) return
  if (!selectedProcess.value) { error.value = '请选择登记工序'; return }
  if (!Number.isInteger(Number(qty.value)) || Number(qty.value) <= 0 || Number(qty.value) > maxQty.value) {
    error.value = `数量须为 1–${maxQty.value} 的整数`
    return
  }
  if (Number(qty.value) >= LARGE_QTY_THRESHOLD && !window.confirm(`本次登记 ${Number(qty.value)} 件，数量较大，请确认无误后提交。`)) return
  working.value = true
  flowState.value = 'submitting'
  error.value = ''
  success.value = ''
  const payload = {
    ticket_id: ticketView.value.id,
    process_id: Number(selectedProcess.value),
    qty: Number(qty.value),
    user_id: selectedUser.value ? Number(selectedUser.value) : undefined,
  }
  const summary = {
    bedNo: ticketView.value.bed_no,
    bundleNo: ticketView.value.bundle_no,
    processName: selectedProcessView.value?.name || '所选工序',
    qty: Number(qty.value),
    employeeName: selectedUser.value
      ? assistants.value.find((item) => item.user_id === Number(selectedUser.value))?.name || '协助员工'
      : '本人',
  }
  let idempotencyKey
  try {
    idempotencyKey = createRequestKey()
    const result = await api('/records', {
      method: 'POST',
      body: payload,
      headers: { 'Idempotency-Key': idempotencyKey },
    })
    success.value = `登记成功：${result.process_name || '所选工序'} ${result.qty} 件，金额 ${money(result.amount)}`
    speak(`登记成功，${Number(qty.value)}件`)
    clearTicket()
    emit('toast', '扫菲登记成功')
    refreshToday()
  } catch (e) {
    // 仅 fetch 网络异常（无 HTTP status）入队；业务拒绝必须保留在页面并展示原因。
    if (e.network === true && idempotencyKey) {
      try {
        enqueueScan(payload, summary, idempotencyKey)
        refreshOfflineQueue()
        success.value = '网络连接失败，登记已保存到本机待同步队列；联网后请手动重发'
        clearTicket()
        emit('toast', '已保存到待同步队列')
      } catch (queueError) {
        error.value = `${e.message}，且离线保存失败：${queueError.message}。请保留当前信息并重试`
        flowState.value = ticket.value ? 'qty' : 'idle'
      }
    } else {
      error.value = e.message
      flowState.value = ticket.value ? 'qty' : 'idle'
    }
  } finally { working.value = false }
}
onMounted(() => {
  refreshToday()
  refreshOfflineQueue()
  window.addEventListener('online', resendQueue)
})
onBeforeUnmount(() => {
  stopCamera()
  window.speechSynthesis?.cancel()
  window.removeEventListener('online', resendQueue)
})
</script>

<template>
  <div class="scan-view" :class="`scan-${flowState}`">
    <div class="welcome"><div><h2>扫菲登记</h2><p>识别菲票后选择工序，登记数量不可超过该扎数量。</p></div><button type="button" class="button secondary" :aria-pressed="speechEnabled" @click="toggleSpeech">{{ speechEnabled ? '关闭语音报数' : '开启语音报数' }}</button></div>

    <section v-if="queuedItems.length" class="card scan-offline-queue" aria-label="待同步登记">
      <div class="scan-queue-header">
        <div><h3>待同步登记 <span class="badge producing">{{ queuedItems.length }} 条</span></h3><p>仅网络失败会进入本机队列；重发沿用首次幂等键，成功后才移除。</p></div>
        <button type="button" class="button primary" :disabled="queueBusy" @click="resendQueue">
          <span v-if="queueBusy" class="spinner light" />{{ queueBusy ? '同步中…' : '手动重发' }}
        </button>
      </div>
      <article v-for="item in queuedItems" :key="item.idempotencyKey" class="scan-queue-item">
        <div>
          <strong>床次 {{ item.summary?.bedNo }} · {{ item.summary?.bundleNo }} 扎 · {{ item.summary?.processName }} {{ item.summary?.qty }} 件</strong>
          <small>{{ item.summary?.employeeName || '本人' }} · 暂存于 {{ new Date(item.queuedAt).toLocaleString() }}</small>
          <p v-if="item.lastError" class="form-error" role="alert">{{ item.lastError }}</p>
        </div>
        <button type="button" class="text-button scan-queue-discard" :disabled="queueBusy" @click="discardQueueItem(item)">删除本机记录</button>
      </article>
      <p class="section-note">“删除本机记录”仅放弃本地重发，不代表撤销服务端记录；如结果不确定，请先查询登记记录。</p>
    </section>

    <section v-if="todayQty !== null && !selectedUser" class="card scan-today-bar" aria-label="今日累计">
      <span class="badge done">今日累计</span>
      <strong>{{ todayQty }} <small>件</small></strong>
      <span class="scan-today-amount">{{ money(todayAmount) }}</span>
    </section>

    <div class="scan-layout">
      <section class="card scan-source-card">
        <div class="segmented scan-tabs">
          <button :class="{ active: mode === 'scan' }" @click="mode = 'scan'">二维码识别</button>
          <button :class="{ active: mode === 'manual' }" @click="mode = 'manual'; stopCamera()">床次＋扎号</button>
        </div>
        <button v-if="mode === 'manual'" class="scan-mobile-back" @click="mode = 'scan'">← 返回扫码</button>
        <template v-if="mode === 'scan'">
          <div class="scanner-view scan-hero">
            <video v-show="cameraActive" ref="video" muted playsinline />
            <div v-if="!cameraActive" class="scanner-placeholder">
              <AppIcon name="camera" :size="42" />
              <h3>请扫描菲票</h3>
              <p>相机识别需要 HTTPS 或 localhost<br>也可直接粘贴菲票二维码内容</p>
              <button class="button primary" @click="startCamera"><AppIcon name="camera" :size="17" />启动相机</button>
              <button class="button secondary scan-album-button" :disabled="working" @click="chooseAlbumImage">从相册识别二维码</button>
              <input ref="imageInput" class="scan-image-input" type="file" accept="image/*" @change="recognizeAlbumImage">
            </div>
            <div v-if="cameraActive" class="scan-frame" />
          </div>
          <button v-if="cameraActive" class="button secondary scan-camera-close" @click="stopCamera">关闭相机</button>
          <p v-if="cameraError" class="form-error">{{ cameraError }}</p>
          <form class="scan-code-form" @submit.prevent="parseCode(qr)">
            <label class="field"><span>菲票二维码内容</span><textarea v-model="qr" rows="2" placeholder="扫码枪输入，或粘贴二维码原始内容" /></label>
            <button class="button secondary" :disabled="working">解析菲票</button>
          </form>
          <button class="scan-lookup-entry" @click="mode = 'manual'; stopCamera()">手输查找（床次＋扎号）</button>
        </template>
        <form v-else class="scan-manual-form" @submit.prevent="lookup">
          <div class="form-grid">
            <label class="field"><span>床次号</span><input v-model="manual.bed_no" placeholder="例如：C001"></label>
            <label class="field"><span>扎号</span><input v-model="manual.bundle_no" placeholder="例如：1"></label>
          </div>
          <button class="button primary" :disabled="working"><AppIcon name="search" :size="17" />查询菲票</button>
        </form>
        <div v-if="candidates.length" class="scan-candidates">
          <p class="field">找到 {{ candidates.length }} 张候选菲票，请选择：</p>
          <button v-for="item in candidates" :key="item.id" class="quick-question" @click="selectTicket(item)">
            {{ item.style_no || item.bed_no }} · 床次 {{ item.bed_no }} · {{ item.bundle_no }} 扎<br>
            <small>{{ item.color || '—' }}/{{ item.size || '—' }} · {{ item.qty }} 件</small>
          </button>
        </div>
        <p v-if="error && flowState === 'idle'" class="form-error scan-source-error" role="alert">{{ error }}</p>
      </section>

      <section class="card" id="register-card">
        <div class="card-header scan-register-header"><div><h3>登记信息</h3><p>核对菲票、工序和数量后提交</p></div></div>
        <div v-if="!ticket" class="page-state"><AppIcon name="scan" :size="36" /><p>请先扫描或查询一张菲票</p></div>
        <form v-else @submit.prevent="register">
          <div class="ticket-card scan-ticket-card">
            <div class="scan-ticket-title">
              <div><span class="eyebrow">当前菲票</span><h3 class="scan-ticket-style" title="双击查看款式与工艺" @dblclick="openStyleCraft">{{ ticket?.style?.style_no || ticketView.style_no || `${ticketView.bed_no} · ${ticketView.bundle_no} 扎` }}</h3><button type="button" class="text-button scan-craft-entry" @click="openStyleCraft">款式与工艺</button></div>
              <span class="badge producing">床次 {{ ticketView.bed_no }}</span>
            </div>
            <p v-if="ticket?.style?.name || ticketView.style_name" class="scan-style-name">{{ ticket?.style?.name || ticketView.style_name }}</p>
            <div class="scan-ticket-grid">
              <div><small>扎号</small><strong>{{ ticketView.bundle_no }}</strong></div>
              <div><small>颜色</small><strong>{{ ticketView.color || '—' }}</strong></div>
              <div><small>尺码</small><strong>{{ ticketView.size || '—' }}</strong></div>
              <div><small>菲票数量</small><strong>{{ maxQty }} 件</strong></div>
            </div>
            <p v-if="ticket?.production?.registered_qty !== undefined" class="scan-registered">该菲票已登记 {{ ticket.production.registered_qty }} 件</p>
          </div>

          <label v-if="assistants.length" class="field scan-assist-field">
            <span>被登记员工（协助扫菲）</span>
            <select v-model="selectedUser" :disabled="flowState === 'submitting'" @change="refreshProcesses">
              <option value="">本人</option>
              <option v-for="employee in assistants" :key="employee.user_id" :value="employee.user_id">{{ employee.name }} · {{ employee.group_no }} 组</option>
            </select>
          </label>

          <label class="field scan-process-select">
            <span>登记工序</span>
            <select v-model="selectedProcess" required :disabled="flowState === 'submitting'" @change="chooseProcess(selectedProcess)">
              <option value="" disabled>请选择工序</option>
              <option v-for="process in processes" :key="process.id" :value="process.id" :disabled="isUnpriced(process)">
                {{ process.name }}<template v-if="process.price !== undefined && process.price !== null"> · {{ money(process.price) }}</template>
              </option>
            </select>
          </label>

          <div v-if="flowState === 'process'" class="scan-stage scan-process-stage">
            <div class="scan-stage-heading"><h3>选择工序</h3><p>多工序时选择本次登记的工序</p></div>
            <div class="scan-process-grid">
              <button
                v-for="process in processes"
                :key="process.id"
                type="button"
                class="scan-process-chip"
                :class="{ on: Number(selectedProcess) === process.id, off: isUnpriced(process) }"
                :data-process-id="process.id"
                :disabled="isUnpriced(process)"
                @click="chooseProcess(process.id)"
              >
                <strong>{{ process.name }}</strong>
                <span v-if="process.registered_qty !== undefined" class="scan-process-cap" :class="{ full: process.cap && process.registered_qty >= process.cap }">
                  {{ process.registered_qty }}{{ process.cap !== null && process.cap !== undefined ? `/${process.cap}` : '' }}
                </span>
                <small v-if="isUnpriced(process)" class="scan-unpriced">未上价</small>
                <small v-else-if="process.price !== null && process.price !== undefined">{{ money(process.price) }}</small>
              </button>
            </div>
          </div>

          <div v-else-if="flowState === 'qty' || flowState === 'submitting'" class="scan-stage scan-qty-stage">
            <div class="scan-selected-process">
              <div><h3>登记工序</h3><span class="badge producing">{{ selectedProcessView?.name }}</span></div>
              <button v-if="processes.length > 1" type="button" class="text-button" :disabled="flowState === 'submitting'" @click="flowState = 'process'; fixedQty = false">切换工序</button>
            </div>
            <div class="scan-qty-fixed">
              <small>{{ fixedQty ? '本次登记件数（按工序流转自动推导）' : '本次登记件数' }}</small>
              <strong>{{ qty }}</strong>
              <button v-if="!fixedQty && !qtyEditing" type="button" class="text-button" @click="qtyEditing = true">修改数量</button>
            </div>
            <label class="field scan-qty-field" :class="{ editing: qtyEditing }">
              <span>登记数量（最多 {{ maxQty }}）</span>
              <input v-model.number="qty" type="number" inputmode="numeric" min="1" :max="maxQty" step="1" required :readonly="fixedQty" :disabled="flowState === 'submitting'">
            </label>
            <button class="button primary scan-submit" :disabled="working || !processes.length">
              <span v-if="working" class="spinner light" />{{ flowState === 'submitting' ? '登记中…' : '确认登记' }}
            </button>
          </div>
          <button type="button" class="button ghost scan-reset" :disabled="flowState === 'submitting'" @click="resetResult">重新选择菲票</button>
        </form>
        <p v-if="error" class="form-error" role="alert">{{ error }}</p>
        <p v-if="success" class="form-error scan-success" role="status">{{ success }}</p>
        <p class="section-note">网络失败时可暂存本机并稍后重发；服务端拒绝不会进入待同步队列。</p>
      </section>
    </div>
  </div>
  <Teleport to="body"><div v-if="styleCraftOpen" class="modal-backdrop" @click.self="styleCraftOpen = false"><section class="modal scan-craft-modal" role="dialog" aria-modal="true" aria-label="菲票款式与工艺"><div class="modal-header"><div><h2>{{ styleCraft?.style_no || ticket?.style?.style_no || ticketView.style_no || '款式与工艺' }}</h2><p>{{ styleCraft?.name || ticket?.style?.name || ticketView.style_name }}</p></div><button class="icon-button" aria-label="关闭款式与工艺" @click="styleCraftOpen = false"><AppIcon name="close" /></button></div><p v-if="styleCraftLoading" class="page-state"><span class="spinner" />正在读取款式详情…</p><p v-else-if="styleCraftError" class="form-error" role="alert">{{ styleCraftError }}</p><div v-else-if="styleCraft" class="scan-craft-content"><img v-if="styleCraft.image || styleCraft.images?.[0]" class="scan-style-image" :src="styleCraft.image || styleCraft.images[0]" :alt="`${styleCraft.style_no} 款式图`"><section><h3>工艺说明</h3><p class="scan-craft-note">{{ styleCraft.craft_note || '暂无工艺说明' }}</p></section><section v-if="styleCraft.craft_images?.length"><h3>工艺图片</h3><div class="scan-craft-images"><img v-for="(url,index) in styleCraft.craft_images" :key="`${url}-${index}`" :src="url" :alt="`工艺图片 ${index + 1}`"></div></section><section v-if="styleCraft.video"><h3>产品视频</h3><video class="scan-craft-video" :src="styleCraft.video" controls preload="metadata">当前浏览器不支持视频播放</video></section></div></section></div></Teleport>
</template>

<style scoped>
.scan-source-error{display:none}.scan-ticket-style{cursor:pointer}.scan-craft-entry{margin-top:6px;padding:0}.scan-craft-modal{max-width:760px}.scan-craft-content{display:grid;gap:18px}.scan-craft-content h3{margin:0 0 8px}.scan-style-image{display:block;width:100%;max-height:360px;border-radius:10px;object-fit:contain;background:#f5f6f8}.scan-craft-note{margin:0;white-space:pre-wrap;line-height:1.75;color:var(--text-secondary)}.scan-craft-images{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}.scan-craft-images img{width:100%;aspect-ratio:1;object-fit:cover;border:1px solid var(--line);border-radius:9px}.scan-craft-video{display:block;width:100%;max-height:420px;background:#111;border-radius:10px}.scan-offline-queue{margin-bottom:18px;padding:18px}.scan-queue-header{display:flex;align-items:center;justify-content:space-between;gap:18px}.scan-queue-header h3{margin:0}.scan-queue-header p{margin:6px 0 0;color:var(--muted);font-size:12px}.scan-queue-header .button{flex:none}.scan-queue-item{display:flex;align-items:flex-start;justify-content:space-between;gap:16px;padding:14px 0;border-top:1px solid var(--line)}.scan-queue-item:first-of-type{margin-top:16px}.scan-queue-item strong,.scan-queue-item small{display:block}.scan-queue-item small{margin-top:5px;color:var(--muted)}.scan-queue-item .form-error{margin:8px 0 0;padding:8px;font-size:12px}.scan-queue-discard{flex:none;color:var(--danger)}.scan-offline-queue>.section-note{margin-bottom:0}.scan-album-button{margin-top:10px}.scan-image-input{display:none}.scan-today-bar{display:flex;align-items:center;gap:14px;margin-bottom:18px;padding:14px 18px}.scan-today-bar strong{font-size:26px;color:var(--orange);font-variant-numeric:tabular-nums}.scan-today-bar strong small{font-size:14px;color:var(--text-secondary)}.scan-today-amount{margin-left:auto;font-size:22px;font-weight:650;color:var(--orange);font-variant-numeric:tabular-nums}.scan-camera-close{margin-top:12px}.scan-code-form{margin-top:16px}.scan-code-form .button{margin-top:10px}.scan-manual-form>.button{margin-top:16px}.scan-lookup-entry,.scan-mobile-back{display:none}.scan-candidates{margin-top:18px}.scan-candidates small{color:var(--muted)}.scan-ticket-card{margin-top:0}.scan-ticket-title{display:flex;justify-content:space-between;align-items:flex-start;gap:12px}.scan-ticket-title h3{margin:5px 0 0;overflow-wrap:anywhere}.scan-style-name{margin:7px 0 0;color:var(--text-secondary);font-size:13px}.scan-ticket-grid{display:grid;grid-template-columns:1fr 1fr;gap:14px 22px;margin-top:18px}.scan-ticket-grid>div{display:flex;align-items:baseline;gap:10px;min-width:0}.scan-ticket-grid small{color:var(--muted);min-width:56px}.scan-ticket-grid strong{overflow-wrap:anywhere}.scan-registered{margin:14px 0 0;color:var(--muted);font-size:12px}.scan-assist-field,.scan-process-select{margin-top:16px}.scan-stage{margin-top:18px}.scan-stage-heading h3,.scan-selected-process h3{margin:0;font-size:16px}.scan-stage-heading p{margin:5px 0 0;color:var(--muted);font-size:12px}.scan-process-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:16px}.scan-process-chip{min-width:0;min-height:84px;padding:14px 10px;border:2px solid var(--line);border-radius:10px;background:#f7f8fa;color:var(--ink);display:flex;flex-direction:column;align-items:center;justify-content:center;gap:6px}.scan-process-chip.on{border-color:var(--orange);background:var(--orange-soft);color:var(--orange-dark)}.scan-process-chip.off{border-style:dashed;border-color:var(--danger);opacity:.6}.scan-process-chip strong{font-size:15px;overflow-wrap:anywhere}.scan-process-chip small{color:var(--muted)}.scan-process-chip .scan-unpriced{color:var(--danger)}.scan-process-cap{padding:1px 8px;border-radius:999px;background:#e6f6ec;color:var(--good);font-size:11px}.scan-process-cap.full{background:#fff0ef;color:var(--danger)}.scan-selected-process{display:flex;align-items:center;justify-content:space-between;gap:12px}.scan-selected-process>div{display:flex;align-items:center;gap:10px}.scan-qty-fixed{padding:18px 0 4px;text-align:center}.scan-qty-fixed small{display:block;color:var(--muted)}.scan-qty-fixed strong{display:block;margin-top:5px;font-size:46px;line-height:1.15;font-variant-numeric:tabular-nums}.scan-qty-fixed .text-button{display:none}.scan-qty-field{margin-top:12px}.scan-submit{width:100%;margin-top:18px;min-height:46px}.scan-reset{width:100%;margin-top:6px}.scan-success{color:var(--good);background:#eaf5ef}
@media(max-width:760px){
  .scan-view>.welcome{display:none}.scan-source-error{display:block}.scan-today-bar{margin-bottom:12px;padding:12px 16px}.scan-today-bar strong{font-size:24px}.scan-today-amount{font-size:20px}.scan-layout{display:block}.scan-view:not(.scan-idle) .scan-source-card{display:none}.scan-idle #register-card{display:none}.scan-source-card{padding:17px}.scan-tabs{display:none}.scan-mobile-back{display:block;border:0;background:transparent;color:var(--orange);padding:0 0 14px}.scan-hero{min-height:270px}.scan-hero .scanner-placeholder{padding:28px 16px}.scan-hero .scanner-placeholder h3{font-size:21px;margin:14px 0 8px}.scan-hero .scanner-placeholder .button{min-width:180px;min-height:46px}.scan-code-form{margin-top:14px}.scan-code-form .field>span{font-size:12px}.scan-code-form textarea{min-height:58px}.scan-code-form .button{width:100%}.scan-lookup-entry{display:block;width:100%;border:0;background:transparent;color:#1677ff;padding:16px 0 0;font-size:14px}.scan-manual-form .form-grid{grid-template-columns:1fr 1fr;gap:10px}.scan-manual-form>.button{width:100%;min-height:46px}.scan-candidates .quick-question{padding:15px 0}.scan-view:not(.scan-idle) #register-card{padding:0;background:transparent;border:0;box-shadow:none}.scan-register-header,.scan-view:not(.scan-idle) #register-card>.section-note{display:none}.scan-ticket-card{padding:17px;background:#fff;border:0;box-shadow:0 2px 8px rgba(31,35,41,.06)}.scan-ticket-grid{gap:14px 12px}.scan-ticket-grid>div{gap:6px}.scan-ticket-grid small{min-width:52px;font-size:11px}.scan-assist-field,.scan-process-select,.scan-stage{padding:17px;background:#fff;border-radius:12px;box-shadow:0 2px 8px rgba(31,35,41,.06)}.scan-assist-field{margin-top:12px}.scan-process-select{display:none}.scan-stage{margin-top:12px}.scan-process-grid{gap:12px}.scan-process-chip{min-height:92px;padding:14px 8px}.scan-qty-fixed strong{font-size:58px}.scan-qty-fixed .text-button{display:flex;margin:8px auto 0}.scan-qty-field{display:none}.scan-qty-field.editing{display:flex}.scan-submit{min-height:50px;font-size:16px}.scan-reset{margin:5px 0 0}.scan-view:not(.scan-idle) #register-card>.form-error{margin-top:12px}
}
</style>
