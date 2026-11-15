export const MATRIX_VIEWS = [
  { key: 'h', label: '横向' },
  { key: 'v', label: '纵向' },
  { key: 'multi', label: '倍数' },
  { key: 'array', label: '排列' },
]

export function positiveInteger(value) {
  const number = Number(value)
  return Number.isInteger(number) && number > 0 ? number : 0
}

export function matrixModel(colors, sizes, cells, multiCounts, view, multiBase) {
  const byColor = view !== 'v'
  const rowKeys = byColor ? colors : sizes
  const columns = byColor ? sizes : colors
  const base = positiveInteger(multiBase)
  const rows = rowKeys.map((label, rowIndex) => {
    let total = 0
    const values = columns.map((column, columnIndex) => {
      const key = byColor ? `${label}|${column}` : `${column}|${label}`
      const count = positiveInteger(multiCounts[key])
      const qty = view === 'multi' ? base * count : positiveInteger(cells[key])
      total += qty
      return { key, qty, count, rowIndex, columnIndex }
    })
    return { label, values, total }
  })
  return { columns, rows, total: rows.reduce((sum, row) => sum + row.total, 0) }
}

/** 行/列的第一个已填值覆盖整行/列；cells 始终以 颜色|尺码 为键。 */
export function fillMatrixLine({ colors, sizes, cells, multiCounts = {}, view, axis, index, multiBase }) {
  const byColor = view !== 'v'
  const rowKeys = byColor ? colors : sizes
  const columns = byColor ? sizes : colors
  const multi = view === 'multi'
  const source = multi ? multiCounts : cells
  const lineKeys = axis === 'row'
    ? columns.map((column) => byColor ? `${rowKeys[index]}|${column}` : `${column}|${rowKeys[index]}`)
    : rowKeys.map((row) => byColor ? `${row}|${columns[index]}` : `${columns[index]}|${row}`)
  const sample = lineKeys.map((key) => positiveInteger(source[key])).find(Boolean) || 0
  if (!sample) return { cells, multiCounts, sample }
  const nextCells = { ...cells }
  const nextCounts = { ...multiCounts }
  lineKeys.forEach((key) => {
    if (multi) {
      nextCounts[key] = sample
      nextCells[key] = positiveInteger(multiBase) * sample
    } else nextCells[key] = sample
  })
  return { cells: nextCells, multiCounts: nextCounts, sample }
}

export function applyArrayQuantity(colors, sizes, cells, quantity) {
  const qty = positiveInteger(quantity)
  if (!qty) return cells
  const next = { ...cells }
  colors.forEach((color) => sizes.forEach((size) => { next[`${color}|${size}`] = qty }))
  return next
}

export function matrixItems(colors, sizes, cells) {
  const items = []
  colors.forEach((color) => sizes.forEach((size) => {
    const qty = positiveInteger(cells[`${color}|${size}`])
    if (qty) items.push({ color, size, qty })
  }))
  return items
}

/** 编菲预览只拆数量，不代替后端扎号分配及数量门禁。 */
export function splitBundleQuantities(items, limit) {
  const size = positiveInteger(limit)
  if (!size) return []
  return (items || []).filter((item) => positiveInteger(item.qty)).map((item) => {
    const qty = positiveInteger(item.qty)
    return { key: `${item.color}|${item.size}`, color: item.color, size: item.size,
      qty, full: Math.floor(qty / size), tail: qty % size }
  })
}

/** 按扎汇总色码裁货量、整扎完成量和余数；部分完成扎不计入已完成件数。 */
export function bundleCompletionMatrix(tickets) {
  const cells = new Map()
  ;(tickets || []).forEach((ticket) => {
    const key = `${ticket.color}|${ticket.size}`
    const cell = cells.get(key) || { key, color: ticket.color, size: ticket.size, cut: 0, done: 0, remaining: 0, tickets: 0 }
    const qty = Number(ticket.qty) || 0
    cell.cut += qty
    cell.done += ticket.state === 'done' ? qty : 0
    cell.tickets += 1
    cell.remaining = cell.cut - cell.done
    cells.set(key, cell)
  })
  return [...cells.values()]
}
