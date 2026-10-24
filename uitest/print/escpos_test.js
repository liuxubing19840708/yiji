/**
 * ESC/POS 试打脚本：复刻 frontend/utils/bluetooth/templates.ts 的 buildEscPos + encodeUtf8，
 * 产出与小程序蓝牙写入完全一致的字节流。TSPL 无出纸/乱码时的备选指令集。
 * 用法：node escpos_test.js [USB001]
 */
const fs = require('fs');

const ticket = {
  id: 27,
  qr_code: '100001|1|ST001|1|黄色|M|35',
  bed_no: '1',
  bundle_no: '1',
  color: '黄色',
  size: 'M',
  qty: 35,
  style_no: 'ST001',
  style_name: '女装黄色半身裙'
};
const company = { name: '衣计演示厂' };

// ---- 与前端 buildEscPos 完全一致（逐行对照 templates.ts）----
function buildEscPos(ticket, company) {
  const ESC = '\x1b';
  const GS = '\x1d';
  let s = '';
  s += ESC + '@';                 // 初始化
  s += ESC + '!\x00';            // 普通字号
  s += `款号:${ticket.style_no || ''}\n`;
  s += `床次:${ticket.bed_no}  扎号:${ticket.bundle_no}\n`;
  s += `颜色:${ticket.color || ''}  尺码:${ticket.size || ''}\n`;
  // ---- 二维码（GS ( k，cn=49 '1'）----
  const data = ticket.qr_code || '';
  s += GS + '(k\x04\x00' + '1A\x32\x00';            // fn65 选模型2
  s += GS + '(k\x03\x00' + '1C\x06';                // fn67 模块 6 点
  s += GS + '(k\x03\x00' + '1E\x31';                // fn69 纠错 M
  const len = data.length + 3;                      // fn80 数据段长度 = 数据+3
  const pL = String.fromCharCode(len % 256);
  const pH = String.fromCharCode(Math.floor(len / 256) % 256);
  s += GS + '(k' + pL + pH + '1P\x30' + data;       // fn80 存数据
  s += GS + '(k\x03\x00' + '1Q\x30';                // fn81 打印
  // ---- 数量放大 ----
  s += ESC + '!\x30';            // 倍宽倍高
  s += `x${ticket.qty}\n`;
  s += ESC + '!\x00';            // 恢复
  if (company && company.name) {
    s += `${company.name}\n`;
  }
  s += '\n\n\n';
  return s;
}

// ---- 与前端 encodeUtf8 完全一致 ----
function encodeUtf8(str) {
  const bytes = [];
  for (let i = 0; i < str.length; i++) {
    let code = str.charCodeAt(i);
    if (code >= 0xd800 && code <= 0xdbff && i + 1 < str.length) {
      const low = str.charCodeAt(i + 1);
      if (low >= 0xdc00 && low <= 0xdfff) {
        code = 0x10000 + ((code - 0xd800) << 10) + (low - 0xdc00);
        i++;
      }
    }
    if (code < 0x80) bytes.push(code);
    else if (code < 0x800) bytes.push(0xc0 | (code >> 6), 0x80 | (code & 0x3f));
    else if (code < 0x10000) bytes.push(0xe0 | (code >> 12), 0x80 | ((code >> 6) & 0x3f), 0x80 | (code & 0x3f));
    else bytes.push(0xf0 | (code >> 18), 0x80 | ((code >> 12) & 0x3f), 0x80 | ((code >> 6) & 0x3f), 0x80 | (code & 0x3f));
  }
  return Buffer.from(bytes);
}

const cmd = buildEscPos(ticket, company);
const buf = encodeUtf8(cmd);
const port = process.argv[2] || 'USB001';
fs.writeFileSync(__dirname + '/escpos_test.bin', buf);
console.log('bytes:', buf.length, '->', __dirname + '\\escpos_test.bin');
