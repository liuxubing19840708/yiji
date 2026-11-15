import { api, upload } from './api'

// 与 backend/apps/preproduction/urls.py 对齐。页面不自行推导审批状态。
export const CRAFT_KINDS = ['measurements', 'requirements', 'challenges', 'common_mistakes', 'quality_standards']

export const fetchPreproductionStatus = (orderId) => api(`/preproduction/orders/${orderId}/status`)
export const fetchTechPacks = (orderId) => api('/preproduction/tech-packs', { params: { order_id: orderId } })
export const fetchCraftTemplates = () => api('/preproduction/templates')

export function createTechPack(data) {
  return api('/preproduction/tech-packs', { method: 'POST', body: data })
}
export function updateTechPack(id, data) {
  return api(`/preproduction/tech-packs/${id}`, { method: 'PUT', body: data })
}
export function saveCraftTemplate(template, data) {
  return api(template ? `/preproduction/templates/${template.id}` : '/preproduction/templates', {
    method: template ? 'PUT' : 'POST', body: data,
  })
}
export function createSampleTask(packId, data) {
  return api(`/preproduction/tech-packs/${packId}/samples`, { method: 'POST', body: data })
}
export function recordSampleResult(taskId, data) {
  return api(`/preproduction/samples/${taskId}/records`, { method: 'POST', body: data })
}
export function createReviewIssue(packId, data) {
  return api(`/preproduction/tech-packs/${packId}/issues`, { method: 'POST', body: data })
}
export function resolveReviewIssue(id, resolution) {
  return api(`/preproduction/issues/${id}`, { method: 'PUT', body: { action: 'resolve', resolution } })
}
export function reopenReviewIssue(id, reason) {
  return api(`/preproduction/issues/${id}`, { method: 'PUT', body: { action: 'reopen', reason } })
}
export function confirmBriefing(packId, note) {
  return api(`/preproduction/tech-packs/${packId}/briefing-confirmation`, { method: 'POST', body: { note } })
}
export function decideTechPack(packId, approved, reason) {
  return api(`/preproduction/tech-packs/${packId}/approval`, {
    method: 'POST', body: { action: approved ? 'approve' : 'reject', reason },
  })
}
export function uploadPreproductionFile(file) {
  return upload('/uploads/files', file)
}
