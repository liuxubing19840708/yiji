// 小程序 templates.ts 的浏览器布局等价实现。坐标统一为 203dpi（8 点/mm），
// 屏幕预览与 TSPL BITMAP 使用同一批中文文本和同一组坐标。
export const DEFAULT_LABEL_STYLE = {
  preset: 'custom', widthMm: 50, heightMm: 80, gapMm: 2, qrUnit: 6,
  fields: ['styleNo', 'bedNo', 'bundleNo', 'color', 'size', 'qty', 'company'],
  bigQty: true, offsetMm: 0, qrPosition: 'top', fontMult: 1.5,
}

const FIELD_KEYS = new Set(['styleNo', 'styleName', 'bedNo', 'bundleNo', 'color', 'size', 'qty', 'company', 'date', 'sourceOrderNo', 'lotNo'])
const finite = (value, fallback) => Number.isFinite(Number(value)) ? Number(value) : fallback
const clamp = (value, min, max, fallback) => Math.max(min, Math.min(max, finite(value, fallback)))

export function resolveLabelStyle(raw) {
  let saved = raw
  if (typeof raw === 'string') {
    try { saved = JSON.parse(raw || '{}') } catch { saved = {} }
  }
  if (!saved || typeof saved !== 'object' || Array.isArray(saved)) saved = {}
  return {
    ...DEFAULT_LABEL_STYLE,
    ...saved,
    widthMm: clamp(saved.widthMm, 20, 120, 50),
    heightMm: clamp(saved.heightMm, 20, 180, 80),
    gapMm: clamp(saved.gapMm, 0, 20, 2),
    qrUnit: Math.round(clamp(saved.qrUnit, 2, 10, 6)),
    offsetMm: clamp(saved.offsetMm, -30, 30, 0),
    fontMult: clamp(saved.fontMult, 1, 3, 1.5),
    qrPosition: saved.qrPosition === 'bottom' ? 'bottom' : 'top',
    fields: Array.isArray(saved.fields) ? saved.fields.filter((key) => FIELD_KEYS.has(key)) : [...DEFAULT_LABEL_STYLE.fields],
    bigQty: saved.bigQty !== false,
  }
}

export function ticketData(ticket, production = {}) {
  return {
    ...ticket,
    style_no: ticket?.style_no || production.style_no || '',
    style_name: ticket?.style_name || production.style_name || '',
    bed_no: ticket?.bed_no || production.bed_no || '',
    source_order_no: ticket?.source_order_no || production.source_order_no || production.order_no || '',
    lot_no: ticket?.lot_no || production.lot_no || '',
  }
}

export function ticketFieldText(key, ticket, company = {}, labeled = false) {
  switch (key) {
    case 'styleNo': return ticket.style_no ? `款号:${ticket.style_no}` : ''
    case 'styleName': return ticket.style_name || ''
    case 'bedNo': return ticket.bed_no ? `床次:${ticket.bed_no}` : ''
    case 'bundleNo': return ticket.bundle_no ? `扎:${ticket.bundle_no}` : ''
    case 'color': return ticket.color ? (labeled ? `颜色:${ticket.color}` : ticket.color) : ''
    case 'size': return ticket.size ? (labeled ? `尺码:${ticket.size}` : ticket.size) : ''
    case 'qty': return labeled ? `数量:${ticket.qty}` : `x${ticket.qty}`
    case 'sourceOrderNo': return ticket.source_order_no ? `单号:${ticket.source_order_no}` : ''
    case 'lotNo': return ticket.lot_no ? `批:${ticket.lot_no}` : ''
    case 'company': return company?.name || ''
    case 'date': {
      const date = new Date()
      return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`
    }
    default: return ''
  }
}

function qrModules(content) {
  let bytes = 0
  for (let index = 0; index < content.length; index++) bytes += content.charCodeAt(index) > 255 ? 3 : 1
  return bytes <= 32 ? 29 : bytes <= 48 ? 33 : 37
}

export function textDotWidth(text, multiplier = 1) {
  let width = 0
  for (const char of text) width += (char.charCodeAt(0) > 255 ? 24 : 12) * multiplier
  return width
}

function truncate(text, maxWidth, multiplier) {
  let output = ''
  let width = 0
  for (const char of text) {
    const charWidth = (char.charCodeAt(0) > 255 ? 24 : 12) * multiplier
    if (width + charWidth > maxWidth) break
    output += char
    width += charWidth
  }
  return output
}

export function computeTicketLayout(rawStyle, sourceTicket, company = {}) {
  const style = resolveLabelStyle(rawStyle)
  const ticket = ticketData(sourceTicket)
  const fontMult = style.fontMult
  const width = Math.round(style.widthMm * 8)
  const height = Math.round(style.heightMm * 8)
  const qrBox = qrModules(ticket.qr_code || '') * style.qrUnit
  const offset = Math.max(-Math.round(height * 0.9), Math.min(Math.round(height * 0.9), Math.round(style.offsetMm * 8)))
  const elements = []

  if (style.heightMm > style.widthMm && style.qrPosition === 'bottom') {
    const qrY = Math.max(4, height - 8 - qrBox + offset)
    const mainKeys = style.fields.filter((key) => key !== 'company' && key !== 'date')
    const putText = (output, row, x, y, maxWidth) => {
      const text = truncate(row.text, maxWidth, row.xmul)
      if (!text) return
      const textWidth = textDotWidth(text, row.xmul)
      output.push({ type: 'text', x: Math.max(4, x + Math.round((maxWidth - textWidth) / 2)), y, w: textWidth, h: 24 * row.ymul, text, xmul: row.xmul, ymul: row.ymul })
    }
    const layoutMain = (multiplier, rowGap, bottom) => {
      const output = []
      const items = []
      for (const key of mainKeys) {
        const text = ticketFieldText(key, ticket, company, true)
        if (!text) continue
        const actualMultiplier = key === 'qty' && style.bigQty && multiplier < 2 ? multiplier * 2 : multiplier
        items.push({ text, xmul: actualMultiplier, ymul: actualMultiplier })
      }
      const maxWidth = width - 24
      let y = Math.max(16, 144 + offset)
      let index = 0
      while (index < items.length) {
        const first = items[index]
        const firstWidth = textDotWidth(first.text, first.xmul)
        const second = items[index + 1]
        const secondWidth = second ? textDotWidth(second.text, second.xmul) : 0
        const rowHeight = Math.max(24 * first.ymul, second ? 24 * second.ymul : 0)
        if (y + rowHeight > bottom) return { output, dropped: true }
        if (second && firstWidth + secondWidth + 16 <= maxWidth) {
          output.push({ type: 'text', x: 12, y, w: firstWidth, h: 24 * first.ymul, text: first.text, xmul: first.xmul, ymul: first.ymul })
          output.push({ type: 'text', x: width - 12 - secondWidth, y, w: secondWidth, h: 24 * second.ymul, text: second.text, xmul: second.xmul, ymul: second.ymul })
          y += rowHeight + rowGap
          index += 2
        } else {
          putText(output, first, 12, y, maxWidth)
          y += 24 * first.ymul + rowGap
          index += 1
        }
      }
      return { output, dropped: false }
    }
    const footBottom = (multiplier) => qrY - 2 - 48 * multiplier - 8
    let mainMult = fontMult
    let main = layoutMain(mainMult, 4, footBottom(mainMult))
    if (main.dropped && mainMult > 1.5) {
      mainMult = 1.5
      main = layoutMain(mainMult, 4, footBottom(mainMult))
    }
    elements.push(...main.output)
    const dateY = Math.max(4, qrY - 2 - 24 * mainMult)
    const companyY = Math.max(4, dateY - 4 - 24 * mainMult)
    putText(elements, { text: ticketFieldText('company', ticket, company, true), xmul: mainMult, ymul: mainMult }, 12, companyY, width - 24)
    putText(elements, { text: ticketFieldText('date', ticket, company, true), xmul: mainMult, ymul: mainMult }, 12, dateY, width - 24)
    elements.push({ type: 'qr', x: Math.max(8, Math.round((width - qrBox) / 2)), y: qrY, w: qrBox, h: qrBox, qrUnit: style.qrUnit, content: ticket.qr_code || '' })
    return { style, widthMm: style.widthMm, heightMm: style.heightMm, width, height, elements }
  }

  const portrait = style.heightMm > style.widthMm
  const qrX = portrait ? Math.max(8, Math.round((width - qrBox) / 2)) : 8
  const textX = portrait ? 12 : Math.min(width - 60, 8 + qrBox + 12)
  const textMaxWidth = portrait ? width - 24 : width - 8 - textX
  const lineGap = portrait ? 20 : 8
  elements.push({ type: 'qr', x: qrX, y: Math.max(0, 14 + offset), w: qrBox, h: qrBox, qrUnit: style.qrUnit, content: ticket.qr_code || '' })
  let y = (portrait ? 14 + qrBox + 16 : 12) + offset
  for (const key of style.fields) {
    const sourceText = ticketFieldText(key, ticket, company)
    if (!sourceText) continue
    const multiplier = key === 'qty' && style.bigQty ? 2 : 1
    const text = truncate(sourceText, textMaxWidth, multiplier)
    if (!text) continue
    const textWidth = textDotWidth(text, multiplier)
    const x = portrait ? Math.max(12, Math.round((width - textWidth) / 2)) : textX
    elements.push({ type: 'text', x, y, w: textWidth, h: 24 * multiplier, text, xmul: multiplier, ymul: multiplier })
    y += 24 * multiplier + lineGap
    if (y + 24 > height - 8) break
  }
  return { style, widthMm: style.widthMm, heightMm: style.heightMm, width, height, elements }
}
