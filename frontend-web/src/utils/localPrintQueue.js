// 仅记录本页直接发起的 BLE 测试任务。菲票/出货单打印次数仍走各自后端接口，
// 这里绝不调用业务打印上报接口来冒充现场出纸成功。
function bytesToBase64(bytes) {
  let binary = ''
  for (let offset = 0; offset < bytes.length; offset += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + 0x8000))
  }
  return btoa(binary)
}

export function base64ToBytes(value) {
  const binary = atob(value)
  const bytes = new Uint8Array(binary.length)
  for (let index = 0; index < binary.length; index++) bytes[index] = binary.charCodeAt(index)
  return bytes
}

export function loadLocalPrintJobs(storageKey) {
  const raw = localStorage.getItem(storageKey)
  if (!raw) return []
  const jobs = JSON.parse(raw)
  if (!Array.isArray(jobs)) throw new Error('本地打印队列格式无效')
  return jobs.filter((job) => job && typeof job.id === 'string' && typeof job.payload === 'string').slice(0, 50)
}

export function saveLocalPrintJobs(storageKey, jobs) {
  localStorage.setItem(storageKey, JSON.stringify(jobs.slice(0, 50)))
}

export function createLocalPrintJob({ bytes, device, style, kind = 'web-ble-tspl-test', label = '', ticketId = null }) {
  return {
    id: globalThis.crypto?.randomUUID?.() || `web-print-${Date.now()}-${Math.random().toString(16).slice(2)}`,
    kind,
    label,
    ticketId,
    status: 'writing',
    deviceId: device.id,
    deviceName: device.name || '未命名 BLE 设备',
    widthMm: style.widthMm,
    heightMm: style.heightMm,
    payload: bytesToBase64(bytes),
    attempts: 0,
    createdAt: new Date().toISOString(),
    updatedAt: new Date().toISOString(),
    sentAt: '',
    lastError: '',
  }
}
