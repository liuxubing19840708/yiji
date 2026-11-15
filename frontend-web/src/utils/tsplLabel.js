import { resolveLabelStyle } from './labelTemplate.js'

// 复用小程序模板的纸张尺寸、DIRECTION 0、REFERENCE 与 CRLF 约定。
// Web 测试签只使用 ASCII；真实菲票请走 ticketBitmap.js 的中文位图路径。

export function buildWebTestLabel(rawStyle) {
  const style = resolveLabelStyle(rawStyle)
  const width = Math.round(style.widthMm * 8)
  const height = Math.round(style.heightMm * 8)
  const qrUnit = Math.max(2, Math.min(style.widthMm >= 40 ? 5 : 3, Math.floor((height - 120) / 33)))
  const qrSize = 33 * qrUnit
  const qrX = Math.max(8, Math.round((width - qrSize) / 2))
  const qrY = Math.max(52, Math.min(64, height - qrSize - 44))
  const footerY = Math.max(qrY + qrSize + 8, height - 36)
  const lines = [
    `SIZE ${style.widthMm} mm,${style.heightMm} mm`,
    'DIRECTION 0',
    'REFERENCE 0,0',
    'CODEPAGE 936',
    'CLS',
    'TEXT 8,16,"TSS24.BF2",0,1,1,"WEB BLE TEST"',
    `QRCODE ${qrX},${qrY},M,${qrUnit},A,0,"YISAO-WEB-BLE-TEST"`,
    `TEXT 8,${footerY},"TSS24.BF2",0,1,1,"CHECK OUTPUT"`,
    'PRINT 1',
  ]
  const command = `${lines.join('\r\n')}\r\n`
  return { style, command, bytes: new TextEncoder().encode(command) }
}
