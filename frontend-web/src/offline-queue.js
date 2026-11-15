import { api, createRequestKey, getSession } from './api'

const QUEUE_PREFIX = 'yisao.web.scan-offline.v1'
const MAX_ITEMS = 200

function storageKey(session = getSession()) {
  const companyId = session.companyId
  const userId = session.user?.id
  if (!companyId || !userId) return ''
  return `${QUEUE_PREFIX}.${companyId}.${userId}`
}

function read(key) {
  if (!key) return []
  try {
    const items = JSON.parse(localStorage.getItem(key) || '[]')
    return Array.isArray(items) ? items : []
  } catch {
    return []
  }
}

function write(key, items) {
  if (!key) return
  localStorage.setItem(key, JSON.stringify(items))
}

export function offlineQueue() {
  return read(storageKey())
}

export function enqueueScan(payload, summary, idempotencyKey = createRequestKey()) {
  const key = storageKey()
  if (!key) throw new Error('无法识别当前公司和用户，未保存离线登记')
  const item = {
    idempotencyKey,
    payload: { ...payload },
    summary: { ...summary },
    queuedAt: Date.now(),
    attempts: 0,
    lastError: '',
  }
  const items = [...read(key), item]
  if (items.length > MAX_ITEMS) throw new Error(`离线队列最多 ${MAX_ITEMS} 条，请先联网重发`)
  write(key, items)
  return item
}

export function removeQueuedScan(idempotencyKey) {
  const key = storageKey()
  write(key, read(key).filter((item) => item.idempotencyKey !== idempotencyKey))
}

export function updateQueuedScan(idempotencyKey, patch) {
  const key = storageKey()
  write(key, read(key).map((item) => item.idempotencyKey === idempotencyKey
    ? { ...item, ...patch }
    : item))
}

let flushing = false

/** 顺序重发；成功才删除，业务拒绝保留原因，网络失败停止本轮以避免无效重试。 */
export async function flushOfflineQueue() {
  if (flushing) return null
  flushing = true
  const result = { sent: 0, rejected: 0, networkFailed: false }
  const scopeKey = storageKey()
  try {
    // 每条请求前重新检查作用域，防止公司/登录态切换后误用另一账号令牌。
    for (const item of read(scopeKey)) {
      if (storageKey() !== scopeKey) break
      if (!item.idempotencyKey || !item.payload) {
        updateQueuedScan(item.idempotencyKey, { lastError: '本地队列数据不完整，请删除该条后重新登记' })
        result.rejected += 1
        continue
      }
      updateQueuedScan(item.idempotencyKey, { attempts: Number(item.attempts || 0) + 1 })
      try {
        await api('/records', {
          method: 'POST',
          body: item.payload,
          headers: { 'Idempotency-Key': item.idempotencyKey },
        })
        removeQueuedScan(item.idempotencyKey)
        result.sent += 1
      } catch (error) {
        const networkFailed = error.network === true
        updateQueuedScan(item.idempotencyKey, {
          lastAttemptAt: Date.now(),
          lastError: networkFailed ? `网络仍未恢复：${error.message}` : `服务端未接受：${error.message}`,
        })
        if (networkFailed) {
          result.networkFailed = true
          break
        }
        result.rejected += 1
      }
    }
    return result
  } finally {
    flushing = false
  }
}
