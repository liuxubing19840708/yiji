export const money = (value) => `¥${Number(value || 0).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
export const number = (value) => Number(value || 0).toLocaleString('zh-CN')
export const dateTime = (value) => value ? new Date(value).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }) : '—'
export const fullDateTime = (value) => value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '—'
export const statusText = (value) => ({ producing: '生产中', done: '已完成', cancelled: '已取消', settled: '已结算', revoked: '已撤销' }[value] || value || '—')
export const pct = (value) => Math.max(0, Math.min(100, Number(value || 0)))
export function monthRange() {
  const now = new Date()
  const pad = (n) => String(n).padStart(2, '0')
  const first = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-01`
  const last = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`
  return { date_from: first, date_to: last }
}
