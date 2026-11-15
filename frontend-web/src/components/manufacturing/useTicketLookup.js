import { reactive } from 'vue'
import { api, rows } from '../../api'

export function useTicketLookup(flow) {
  const extraTickets = reactive({})
  function ticketFor(id) {
    return flow.ticketFor(id) || extraTickets[Number(id)] || null
  }
  async function hydrate(records) {
    const missing = [...new Map(records.filter((item) => item.ticket && !ticketFor(item.ticket)
      && (item.bed_no !== undefined || item.ticket_bed_no !== undefined))
      .map((item) => [item.ticket, item])).values()].slice(0, 40)
    const results = await Promise.allSettled(missing.map((item) => api('/tickets', { params: {
      bed_no: item.ticket_bed_no ?? item.bed_no,
      bundle_no: item.ticket_bundle_no ?? item.bundle_no,
      page_size: 100,
    } })))
    results.forEach((result, index) => {
      if (result.status !== 'fulfilled') return
      const expectedId = Number(missing[index].ticket)
      const match = rows(result.value).find((ticket) => ticket.id === expectedId)
      if (match) extraTickets[expectedId] = match
    })
  }
  function displayTicket(record) {
    return ticketFor(record.ticket) || {
      id: record.ticket,
      bed_no: record.ticket_bed_no ?? record.bed_no,
      bundle_no: record.ticket_bundle_no ?? record.bundle_no,
      color: record.color,
      size: record.size,
    }
  }
  return { ticketFor, hydrate, displayTicket }
}
