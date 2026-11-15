import { api } from './api'

export function fetchHandovers(params) {
  return api('/handovers/', { params: { kind: 'cutting', ...params } })
}

export function fetchHandover(id) {
  return api(`/handovers/${id}/`)
}

export function createHandovers(body) {
  return api('/handovers/', { method: 'POST', body })
}

export function receiveHandover(id, body) {
  return api(`/handovers/${id}/receive`, { method: 'POST', body })
}

export function rejectHandover(id, body) {
  return api(`/handovers/${id}/reject`, { method: 'POST', body })
}

export function reassignHandover(id, body) {
  return api(`/handovers/${id}/reassign`, { method: 'POST', body })
}

export function fetchHandoverBatch(batchNo) {
  return api(`/handovers/batches/${encodeURIComponent(batchNo)}/`)
}

export function fetchHandoverPolicies() {
  return api('/handovers/policies')
}

export function saveHandoverPolicy(id, body) {
  return api(id ? `/handovers/policies/${id}` : '/handovers/policies', {
    method: id ? 'PUT' : 'POST',
    body,
  })
}
