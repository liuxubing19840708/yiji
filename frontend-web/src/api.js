const API_ROOT = '/api'
const SESSION_KEY = 'yisao.web.session.v1'

export function getSession() {
  try { return JSON.parse(localStorage.getItem(SESSION_KEY)) || {} } catch { return {} }
}

export function saveSession(patch) {
  const next = { ...getSession(), ...patch }
  localStorage.setItem(SESSION_KEY, JSON.stringify(next))
  return next
}

export function clearSession() { localStorage.removeItem(SESSION_KEY) }

function errorMessage(data, status) {
  if (typeof data === 'string') return data || `请求失败（${status}）`
  if (!data) return `请求失败（${status}）`
  if (typeof data.detail === 'string') return data.detail
  const first = Object.values(data)[0]
  if (Array.isArray(first)) return String(first[0])
  if (typeof first === 'object' && first) return errorMessage(first, status)
  return String(first || data.message || `请求失败（${status}）`)
}

export async function api(path, options = {}) {
  const { method = 'GET', body, params, anonymous = false } = options
  const session = getSession()
  const query = new URLSearchParams()
  Object.entries(params || {}).forEach(([key, value]) => {
    if (value !== '' && value !== null && value !== undefined) query.set(key, String(value))
  })
  const headers = { Accept: 'application/json', ...(options.headers || {}) }
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  if (!anonymous && session.access) headers.Authorization = `Bearer ${session.access}`
  if (!anonymous && session.companyId) headers['X-Company-Id'] = String(session.companyId)
  let response
  try {
    response = await fetch(`${API_ROOT}${path}${query.size ? `?${query}` : ''}`, {
      method, headers, body: body === undefined ? undefined : JSON.stringify(body),
    })
  } catch {
    const error = new Error('网络连接失败，请检查私网连接或后端服务')
    error.network = true
    throw error
  }
  const contentType = response.headers.get('content-type') || ''
  const data = contentType.includes('json') ? await response.json() : await response.text()
  if (response.status === 401 && !anonymous) {
    clearSession()
    window.dispatchEvent(new CustomEvent('web-auth-expired'))
    throw new Error('登录已失效，请重新登录')
  }
  if (!response.ok) {
    const error = new Error(errorMessage(data, response.status))
    error.status = response.status
    error.data = data
    throw error
  }
  return data
}

export async function upload(path, file) {
  const session = getSession()
  const body = new FormData()
  body.append('file', file)
  let response
  try {
    response = await fetch(`${API_ROOT}${path}`, {
      method: 'POST', body,
      headers: { Accept: 'application/json', Authorization: `Bearer ${session.access}`, 'X-Company-Id': String(session.companyId) },
    })
  } catch {
    const error = new Error('网络连接失败，请检查私网连接或后端服务')
    error.network = true
    throw error
  }
  const contentType = response.headers.get('content-type') || ''
  const data = contentType.includes('json') ? await response.json() : await response.text()
  if (response.status === 401) {
    clearSession()
    window.dispatchEvent(new CustomEvent('web-auth-expired'))
    throw new Error('登录已失效，请重新登录')
  }
  if (!response.ok) {
    const error = new Error(errorMessage(data, response.status))
    error.status = response.status
    error.data = data
    throw error
  }
  return data
}

export function createRequestKey() {
  const cryptoApi = globalThis.crypto
  if (typeof cryptoApi?.randomUUID === 'function') return cryptoApi.randomUUID()
  if (typeof cryptoApi?.getRandomValues !== 'function') {
    throw new Error('当前浏览器不支持安全随机 UUID，请使用安全上下文或升级浏览器')
  }
  const bytes = new Uint8Array(16)
  cryptoApi.getRandomValues(bytes)
  bytes[6] = (bytes[6] & 0x0f) | 0x40
  bytes[8] = (bytes[8] & 0x3f) | 0x80
  const hex = Array.from(bytes, (value) => value.toString(16).padStart(2, '0'))
  return `${hex.slice(0, 4).join('')}-${hex.slice(4, 6).join('')}-${hex.slice(6, 8).join('')}-${hex.slice(8, 10).join('')}-${hex.slice(10).join('')}`
}

export async function download(path, params = {}, options = {}) {
  const session = getSession()
  const query = new URLSearchParams()
  Object.entries(params).forEach(([key, value]) => { if (value !== '' && value !== null && value !== undefined) query.set(key, String(value)) })
  const method = options.method || 'GET'
  const hasBody = options.body !== undefined
  const headers = { Authorization: `Bearer ${session.access}`, 'X-Company-Id': String(session.companyId) }
  if (hasBody) headers['Content-Type'] = 'application/json'
  const response = await fetch(`${API_ROOT}${path}${query.size ? `?${query}` : ''}`, {
    method,
    headers,
    body: hasBody ? JSON.stringify(options.body) : undefined,
  })
  if (!response.ok) {
    const data = await response.json().catch(() => null)
    throw new Error(errorMessage(data, response.status))
  }
  const blob = await response.blob()
  const disposition = response.headers.get('content-disposition') || ''
  const match = disposition.match(/filename\*?=(?:UTF-8''|\")?([^\";]+)/i)
  const link = document.createElement('a')
  link.href = URL.createObjectURL(blob)
  link.download = match ? decodeURIComponent(match[1]) : 'export.xlsx'
  link.click()
  setTimeout(() => URL.revokeObjectURL(link.href), 1000)
}

export const rows = (data) => Array.isArray(data) ? data : (data?.results || [])
export const companyId = () => getSession().companyId
