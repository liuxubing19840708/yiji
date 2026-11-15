<script setup>
import { computed, onUnmounted, ref, watch } from 'vue'
import { getSession } from '../../api'
import { WebBluetoothPrinter, webBluetoothSupported } from '../../utils/webBluetooth'
import { buildWebTestLabel } from '../../utils/tsplLabel'
import { buildTicketLabel } from '../../utils/ticketBitmap'
import { resolveLabelStyle } from '../../utils/labelTemplate'
import { base64ToBytes, createLocalPrintJob, loadLocalPrintJobs, saveLocalPrintJobs } from '../../utils/localPrintQueue'

const props = defineProps({
  settings: { type: Object, default: () => ({}) },
  tickets: { type: Array, default: () => [] },
  production: { type: Object, default: () => ({}) },
  showTest: { type: Boolean, default: true },
})
const supported = ref(webBluetoothSupported())
const selected = ref(null)
const connectedDevice = ref(null)
const searching = ref(false)
const connecting = ref(false)
const busyJobId = ref('')
const error = ref('')
const statusText = ref('')
const queueWarning = ref('')
const jobs = ref([])
const storageKey = `yisao.web.ble-print-jobs.v1.${getSession().companyId || 'no-company'}`
const statusLabels = { writing: '正在写入', sent: '指令已写入 · 待现场确认', failed: '写入失败 · 可重试' }
const failedCount = computed(() => jobs.value.filter((job) => job.status === 'failed').length)
const sentCount = computed(() => jobs.value.filter((job) => job.status === 'sent').length)
const effectiveLabelStyle = computed(() => {
  const style = resolveLabelStyle(props.settings.print_label_style)
  let offsets = props.settings.print_label_offsets
  if (typeof offsets === 'string') {
    try { offsets = JSON.parse(offsets || '{}') } catch { offsets = {} }
  }
  const printerName = connectedDevice.value?.name || selected.value?.name || ''
  const override = offsets?.[printerName]
  if (typeof override === 'number' && Number.isFinite(override)) style.offsetMm = override
  return style
})
const testTemplate = computed(() => buildWebTestLabel(effectiveLabelStyle.value).style)
const hasTickets = computed(() => props.tickets.length > 0)
const selectedTicketId = ref('')
const selectedTicket = computed(() => props.tickets.find((ticket) => String(ticket.id) === selectedTicketId.value) || null)
const ticketPreview = ref('')
const ticketError = ref('')
const company = computed(() => {
  const session = getSession()
  return session.companies?.find((item) => item.id === session.companyId) || {}
})

watch(() => props.tickets, (tickets) => {
  if (!tickets.some((ticket) => String(ticket.id) === selectedTicketId.value)) {
    selectedTicketId.value = tickets.length ? String(tickets[0].id) : ''
  }
}, { immediate: true })

watch([selectedTicket, () => effectiveLabelStyle.value], () => {
  ticketPreview.value = ''
  ticketError.value = ''
  if (!selectedTicket.value) return
  try {
    ticketPreview.value = buildTicketLabel(
      effectiveLabelStyle.value,
      selectedTicket.value,
      props.production,
      company.value,
    ).dataUrl
  } catch (err) {
    ticketError.value = `中文菲票预览生成失败：${err.message}`
  }
}, { immediate: true })

const printer = new WebBluetoothPrinter((device) => {
  if (connectedDevice.value?.id === device.id) connectedDevice.value = null
  statusText.value = `与 ${device.name || '蓝牙设备'} 的连接已断开；未完成的写入可在重新连接后重试`
})

function persist() {
  try {
    saveLocalPrintJobs(storageKey, jobs.value)
    queueWarning.value = ''
  } catch {
    queueWarning.value = '浏览器未能保存本地队列；当前记录仅在本页暂存，刷新可能丢失'
  }
}

try {
  jobs.value = loadLocalPrintJobs(storageKey)
  // 刷新后不可能仍有进行中的真实写入，不能把这种任务显示为成功。
  let recovered = false
  jobs.value.forEach((job) => {
    if (job.status === 'writing') {
      job.status = 'failed'
      job.lastError = '页面关闭或刷新后无法确认上次写入是否完成，请现场核查后再决定是否重试，避免重复打印'
      job.updatedAt = new Date().toISOString()
      recovered = true
    }
  })
  if (recovered) persist()
} catch {
  queueWarning.value = '本地打印队列读取失败；可清空浏览器该站点存储后重试'
}

async function selectDevice() {
  if (searching.value || connecting.value || busyJobId.value) return
  error.value = ''
  statusText.value = ''
  searching.value = true
  try {
    selected.value = await printer.requestDevice()
    connectedDevice.value = null
    statusText.value = `已选择 ${selected.value.name || '未命名 BLE 设备'}，尚未连接`
  } catch (err) {
    error.value = err.message
  } finally {
    searching.value = false
  }
}

async function connect() {
  if (!selected.value || connecting.value || busyJobId.value) return
  error.value = ''
  statusText.value = ''
  connecting.value = true
  try {
    await printer.connect(selected.value)
    connectedDevice.value = selected.value
    statusText.value = `已连接 ${selected.value.name || '未命名 BLE 设备'}，并找到可写 GATT 通道`
  } catch (err) {
    connectedDevice.value = null
    error.value = err.message
  } finally {
    connecting.value = false
  }
}

function disconnect() {
  if (busyJobId.value) return
  printer.disconnect()
  connectedDevice.value = null
  error.value = ''
  statusText.value = '已主动断开蓝牙连接；设备选择仍保留，可重新连接'
}

async function runJob(job) {
  if (busyJobId.value) return
  if (!printer.connected || connectedDevice.value?.id !== job.deviceId) {
    error.value = '请先搜索并重新连接该任务原先使用的打印机；浏览器刷新后不会保留设备连接'
    return
  }
  error.value = ''
  statusText.value = ''
  busyJobId.value = job.id
  job.status = 'writing'
  job.attempts += 1
  job.updatedAt = new Date().toISOString()
  job.lastError = ''
  persist()
  try {
    await printer.write(base64ToBytes(job.payload))
    job.status = 'sent'
    job.sentAt = new Date().toISOString()
    job.updatedAt = job.sentAt
    statusText.value = `${job.kind === 'web-ble-ticket-bitmap' ? '中文菲票位图' : 'TSPL 测试指令'}已全部写入蓝牙通道。这不代表已物理出纸，请立即检查打印机；若已出纸，请勿重复重试。`
  } catch (err) {
    job.status = 'failed'
    job.lastError = err.message
    job.updatedAt = new Date().toISOString()
    error.value = `写入失败：${err.message}。请先现场检查是否已有完整或残张输出，再决定是否重试，避免重复打印。`
  } finally {
    persist()
    busyJobId.value = ''
  }
}

function sendTest() {
  if (busyJobId.value) return
  if (!printer.connected || !connectedDevice.value) {
    error.value = '请先选择并连接 BLE 打印机，再发送测试指令'
    return
  }
  let job
  try {
    const label = buildWebTestLabel(effectiveLabelStyle.value)
    job = createLocalPrintJob({ bytes: label.bytes, device: connectedDevice.value, style: label.style })
  } catch (err) {
    error.value = `测试指令生成失败：${err.message}`
    return
  }
  jobs.value.unshift(job)
  jobs.value = jobs.value.slice(0, 50)
  runJob(job)
}

function sendTicket() {
  if (busyJobId.value || !selectedTicket.value) return
  if (!printer.connected || !connectedDevice.value) {
    error.value = '请先选择并连接 BLE 打印机，再发送真实菲票'
    return
  }
  let job
  try {
    const ticket = selectedTicket.value
    const label = buildTicketLabel(effectiveLabelStyle.value, ticket, props.production, company.value)
    job = createLocalPrintJob({
      bytes: label.bytes,
      device: connectedDevice.value,
      style: label.style,
      kind: 'web-ble-ticket-bitmap',
      ticketId: ticket.id,
      label: `床次 ${ticket.bed_no || props.production.bed_no || '—'} · 第 ${ticket.bundle_no || '—'} 扎 · ${ticket.color || '—'}/${ticket.size || '—'} · ${ticket.qty} 件`,
    })
  } catch (err) {
    error.value = `中文菲票位图生成失败：${err.message}，未向打印机发送数据`
    return
  }
  jobs.value.unshift(job)
  jobs.value = jobs.value.slice(0, 50)
  runJob(job)
}

function retry(job) {
  if (job.status !== 'failed') return
  runJob(job)
}

function clearSentHistory() {
  if (busyJobId.value || !sentCount.value || !window.confirm('清空本浏览器中“已写入”的蓝牙打印历史？失败待重试任务会保留。')) return
  jobs.value = jobs.value.filter((job) => job.status !== 'sent')
  persist()
}

function timeText(value) {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '—' : date.toLocaleString('zh-CN', { hour12: false })
}

onUnmounted(() => {
  printer.disconnect()
})
</script>

<template>
  <section class="card ble-panel" aria-labelledby="web-ble-title">
    <div class="card-header">
      <div><h3 id="web-ble-title">Web Bluetooth 标签打印机</h3><p>{{ hasTickets ? '真实中文菲票位图' : 'TSPL2 测试签' }} · BLE GATT · 本地失败重试</p></div>
      <span class="capability-badge" :class="{ unavailable: !supported }">{{ supported ? '浏览器 API 可用' : '当前浏览器不支持' }}</span>
    </div>

    <div v-if="!supported" class="browser-limit" role="status">
      <strong>无法在此浏览器使用 Web Bluetooth</strong>
      <p>请改用支持该 API 的 Chrome / Edge 桌面或 Android 版本，并通过 HTTPS 或 localhost 访问。iPhone / iPad 的 Safari 通常不提供此能力。菲票和出货单仍可使用下方独立的浏览器打印入口。</p>
    </div>
    <template v-else>
      <p class="section-note">点击搜索后由浏览器弹出设备选择器，网页不能后台枚举或自动配对设备。请选择已开机的 BLE 标签打印机；仅经典蓝牙、ESC/POS 或未开放可写 GATT 服务的设备不适用。</p>
      <div class="device-row">
        <div class="device-summary">
          <small>{{ connectedDevice ? '已连接设备' : selected ? '已选择设备（未连接）' : '尚未选择设备' }}</small>
          <strong>{{ connectedDevice?.name || selected?.name || '—' }}</strong>
          <span v-if="selected">设备 ID：{{ selected.id }}</span>
        </div>
        <div class="device-actions">
          <button type="button" class="button secondary" :disabled="searching || connecting || Boolean(busyJobId)" @click="selectDevice">{{ searching ? '等待选择设备…' : '搜索 / 选择设备' }}</button>
          <button v-if="!connectedDevice" type="button" class="button primary" :disabled="!selected || connecting || Boolean(busyJobId)" @click="connect">{{ connecting ? '连接中…' : '连接' }}</button>
          <button v-else type="button" class="button secondary" :disabled="Boolean(busyJobId)" @click="disconnect">断开</button>
        </div>
      </div>
      <section v-if="hasTickets" class="ticket-bluetooth-print" aria-labelledby="ticket-bluetooth-title">
        <div class="ticket-print-heading"><div><h4 id="ticket-bluetooth-title">发送真实中文菲票</h4><p>选择当前生产床次的真实菲票；整签中文文字和二维码由浏览器绘制为位图，不依赖打印机中文字库。</p></div><label class="field"><span>选择菲票</span><select v-model="selectedTicketId" :disabled="Boolean(busyJobId)"><option v-for="ticket in tickets" :key="ticket.id" :value="String(ticket.id)">第 {{ ticket.bundle_no }} 扎 · {{ ticket.color }}/{{ ticket.size }} · {{ ticket.qty }} 件</option></select></label></div>
        <div class="ticket-print-body">
          <div class="bitmap-preview"><img v-if="ticketPreview" :src="ticketPreview" alt="中文菲票位图打印预览" /><p v-else>正在生成中文位图预览…</p></div>
          <div class="ticket-print-detail"><strong>床次 {{ production.bed_no }} · {{ production.style_no }}</strong><span>{{ production.style_name }}</span><small>{{ testTemplate.widthMm }} × {{ testTemplate.heightMm }} mm · 203 dpi · 纠错 M 二维码</small><p>预览与发送使用同一中文布局。发送后不会自动增加打印计数，也不会自动调用后端成功上报。</p><button type="button" class="button primary" :disabled="!connectedDevice || !selectedTicket || !ticketPreview || Boolean(busyJobId)" @click="sendTicket">{{ busyJobId ? '正在写入…' : '通过蓝牙发送当前菲票' }}</button></div>
        </div>
        <p v-if="ticketError" class="form-error" role="alert">{{ ticketError }}</p>
      </section>
      <div v-if="showTest" class="test-print-row">
        <div><strong>发送 TSPL2 测试签</strong><p>纸张沿用公司标签尺寸：{{ testTemplate.widthMm }} × {{ testTemplate.heightMm }} mm。内容为英文测试文字和测试二维码，不是可登记的真实菲票；20 字节分包串行写入。</p></div>
        <button type="button" class="button primary" :disabled="!connectedDevice || Boolean(busyJobId)" @click="sendTest">{{ busyJobId ? '正在写入…' : '测试打印' }}</button>
      </div>
    </template>

    <p v-if="statusText" class="print-notice" role="status">{{ statusText }}</p>
    <p v-if="error" class="form-error" role="alert">{{ error }}</p>
    <p class="physical-check"><strong>现场核查要求：</strong>“已连接”仅表示 GATT 通道可用；“指令已写入”仅表示浏览器写调用完成。两者都不证明走纸、打印完整或标签可扫，必须现场检查。</p>

    <section class="local-queue" aria-labelledby="local-queue-title">
      <div class="card-header">
        <div><h4 id="local-queue-title">本地蓝牙队列与历史</h4><p>{{ failedCount }} 项失败待处理 · {{ sentCount }} 项已写入历史 · 最多保留 50 项</p></div>
        <button type="button" class="button ghost small" :disabled="!sentCount || Boolean(busyJobId)" @click="clearSentHistory">清空已写入历史</button>
      </div>
      <p class="section-note">数据仅保存在当前公司、当前浏览器的 localStorage，不上传为后端菲票/出货单打印记录，也不增加业务打印计数。刷新后须重新授权连接原设备才能重试；已写入项不自动重发。</p>
      <p v-if="queueWarning" class="form-error" role="alert">{{ queueWarning }}</p>
      <p v-if="!jobs.length" class="empty-queue">暂无本地蓝牙打印任务</p>
      <div v-else class="queue-list">
        <article v-for="job in jobs" :key="job.id" class="queue-item" :class="job.status">
          <div class="queue-main"><strong>{{ job.label || 'TSPL2 测试签' }}</strong><span>{{ job.deviceName }} · {{ job.widthMm }}×{{ job.heightMm }} mm</span><span>{{ statusLabels[job.status] || job.status }} · 尝试 {{ job.attempts }} 次</span><small>创建 {{ timeText(job.createdAt) }}<template v-if="job.sentAt"> · 最近写入 {{ timeText(job.sentAt) }}</template></small><p v-if="job.lastError">{{ job.lastError }}</p></div>
          <button v-if="job.status === 'failed'" type="button" class="button secondary small" :disabled="Boolean(busyJobId)" @click="retry(job)">重试写入</button>
          <span v-else-if="job.status === 'sent'" class="check-output">待现场检查</span>
        </article>
      </div>
    </section>
  </section>
</template>

<style scoped>
.ble-panel{padding:22px;margin-top:18px}.capability-badge{flex:0 0 auto;padding:5px 10px;border-radius:999px;background:#e8f7ee;color:#237a45;font-size:12px}.capability-badge.unavailable{background:#f2f3f5;color:var(--muted)}.browser-limit{margin-top:16px;padding:14px;border:1px solid #eed6a8;border-radius:10px;background:#fff9e9}.browser-limit p{margin:6px 0 0;color:#71591f;line-height:1.65;font-size:13px}.device-row,.test-print-row{display:flex;align-items:center;justify-content:space-between;gap:18px;margin-top:16px;padding:15px;border:1px solid var(--line);border-radius:10px;background:#fff}.device-summary{display:grid;gap:4px;min-width:0}.device-summary small,.device-summary span{color:var(--muted);font-size:12px;overflow-wrap:anywhere}.device-actions{display:flex;flex-wrap:wrap;gap:8px;flex:0 0 auto}.test-print-row>div{min-width:0}.test-print-row p{margin:5px 0 0;color:var(--muted);font-size:12px;line-height:1.6}.test-print-row>.button{flex:0 0 auto}.print-notice{padding:10px 12px;border-radius:8px;background:#edf7ff;color:#245c82;font-size:13px;line-height:1.6}.physical-check{margin:14px 0 0;padding:11px 12px;border-left:3px solid var(--orange);background:var(--orange-soft);font-size:12px;line-height:1.65}.local-queue{margin-top:22px;padding-top:18px;border-top:1px solid var(--line)}.local-queue h4{margin:0;font-size:14px}.local-queue .card-header p{margin:4px 0 0}.queue-list{display:grid;gap:8px;margin-top:14px}.queue-item{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:12px;border:1px solid var(--line);border-radius:9px}.queue-item.failed{border-color:#efc2bd;background:#fff9f8}.queue-main{display:grid;gap:4px;min-width:0}.queue-main span,.queue-main small{color:var(--muted);font-size:12px}.queue-main p{margin:2px 0 0;color:var(--danger);font-size:12px;line-height:1.5}.queue-item>.button,.check-output{flex:0 0 auto}.check-output{color:var(--orange);font-size:12px}.empty-queue{color:var(--muted);font-size:13px}
.ticket-bluetooth-print{margin-top:16px;padding:15px;border:1px solid var(--orange);border-radius:10px;background:#fff}.ticket-print-heading{display:flex;align-items:flex-end;justify-content:space-between;gap:16px}.ticket-print-heading h4{margin:0;font-size:14px}.ticket-print-heading p{margin:5px 0 0;color:var(--muted);font-size:12px;line-height:1.6}.ticket-print-heading .field{flex:0 0 260px}.ticket-print-heading select{min-height:38px;padding:8px;border:1px solid var(--line);border-radius:8px;background:#fff}.ticket-print-body{display:flex;align-items:center;gap:20px;margin-top:14px}.bitmap-preview{display:grid;place-items:center;flex:0 0 190px;min-height:220px;padding:8px;border:1px dashed var(--line);border-radius:8px;background:#f7f7f7;overflow:hidden}.bitmap-preview img{display:block;max-width:174px;max-height:280px;width:auto;height:auto;box-shadow:0 1px 6px rgba(0,0,0,.12)}.bitmap-preview p{color:var(--muted);font-size:12px}.ticket-print-detail{display:flex;flex-direction:column;align-items:flex-start;gap:7px;min-width:0}.ticket-print-detail>span,.ticket-print-detail>small{color:var(--muted)}.ticket-print-detail p{margin:2px 0 7px;color:var(--muted);font-size:12px;line-height:1.6}
@media(max-width:700px){.ble-panel{padding:15px}.ble-panel>.card-header{align-items:flex-start}.device-row,.test-print-row,.queue-item,.ticket-print-heading{align-items:stretch;flex-direction:column}.ticket-print-heading .field{flex-basis:auto}.ticket-print-body{align-items:stretch;flex-direction:column}.bitmap-preview{flex-basis:auto;min-height:180px}.device-actions .button,.test-print-row>.button{flex:1}.queue-item>.button{align-self:flex-start}}
</style>
