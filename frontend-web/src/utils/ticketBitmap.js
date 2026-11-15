import QRCode from 'qrcode'
import { computeTicketLayout, ticketData } from './labelTemplate.js'

function drawQr(context, content, element) {
  if (!content) throw new Error('菲票缺少二维码内容，不能生成蓝牙标签')
  const qr = QRCode.create(content, { errorCorrectionLevel: 'M' })
  const size = qr.modules.size
  const data = qr.modules.data
  const unit = element.qrUnit || 6
  context.fillStyle = '#000000'
  for (let row = 0; row < size; row++) {
    for (let column = 0; column < size; column++) {
      if (data[row * size + column]) {
        context.fillRect(element.x + column * unit, element.y + row * unit, unit, unit)
      }
    }
  }
}

// 与小程序 labelBitmap.ts 同口径：白底、中文 Canvas 字形、QR 本地矩阵、
// 1 位单色 MSB 打包，TSPL BITMAP 中亮像素置 1、暗像素置 0。
export function buildTicketLabel(rawStyle, sourceTicket, production = {}, company = {}, suppliedCanvas = null) {
  const ticket = ticketData(sourceTicket, production)
  const layout = computeTicketLayout(rawStyle, ticket, company)
  const canvas = suppliedCanvas || document.createElement('canvas')
  canvas.width = layout.width
  canvas.height = layout.height
  const context = canvas.getContext('2d', { willReadFrequently: true })
  if (!context) throw new Error('当前浏览器无法创建 Canvas 2D，未生成菲票位图')
  context.fillStyle = '#ffffff'
  context.fillRect(0, 0, layout.width, layout.height)
  context.fillStyle = '#000000'
  context.textBaseline = 'top'
  for (const element of layout.elements) {
    if (element.type === 'qr') {
      drawQr(context, element.content, element)
    } else {
      context.save()
      context.translate(element.x, element.y)
      // 显式使用中文字体栈，并通过位图发送字形，不依赖打印机中文 CODEPAGE。
      context.font = '24px "Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC", sans-serif'
      context.scale(element.xmul || 1, element.ymul || 1)
      context.fillText(element.text || '', 0, 24 * 0.12)
      context.restore()
    }
  }

  const pixels = context.getImageData(0, 0, layout.width, layout.height).data
  const rowBytes = layout.width >> 3
  const bits = new Uint8Array(rowBytes * layout.height)
  for (let y = 0; y < layout.height; y++) {
    const row = y * rowBytes
    const pixelRow = y * layout.width * 4
    for (let x = 0; x < layout.width; x++) {
      const index = pixelRow + x * 4
      const gray = (pixels[index] + pixels[index + 1] + pixels[index + 2]) / 3
      if (pixels[index + 3] === 0 || gray >= 190) bits[row + (x >> 3)] |= 0x80 >> (x & 7)
    }
  }
  const head = `SIZE ${layout.widthMm} mm,${layout.heightMm} mm\r\n`
    + 'DIRECTION 0\r\nREFERENCE 0,0\r\nCLS\r\n'
    + `BITMAP 0,0,${rowBytes},${layout.height},0,`
  const tail = '\r\nPRINT 1\r\n'
  const encoder = new TextEncoder()
  const headBytes = encoder.encode(head)
  const tailBytes = encoder.encode(tail)
  const bytes = new Uint8Array(headBytes.length + bits.length + tailBytes.length)
  bytes.set(headBytes, 0)
  bytes.set(bits, headBytes.length)
  bytes.set(tailBytes, headBytes.length + bits.length)
  return {
    bytes,
    style: layout.style,
    layout,
    dataUrl: typeof canvas.toDataURL === 'function' ? canvas.toDataURL('image/png') : '',
  }
}
