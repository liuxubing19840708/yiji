/**
 * TSPL 试打脚本：复刻 frontend/utils/bluetooth/templates.ts 的 buildTspl + encodeUtf8，
 * 产出与小程序蓝牙写入完全一致的字节流，写到 \\.\USB001 验证模板本身。
 * 用法：node tspl_test.js [USB001] —— 第二参数可换端口名。
 */
const fs = require('fs');

// ---- 演示库菲票 27（api 取 qr_code）----
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

// ---- 与前端 tsplEscape 完全一致 ----
function tsplEscape(text) {
  return String(text || '').replace(/\\/g, '\\\\').replace(/"/g, '\\"');
}

// ---- 与前端 buildTspl 完全一致（逐行对照 templates.ts）----
function buildTspl(ticket, company) {
  const qr = tsplEscape(ticket.qr_code || '');
  const styleNo = tsplEscape(ticket.style_no || '');
  const color = tsplEscape(ticket.color || '');
  const size = tsplEscape(ticket.size || '');
  const companyNo = tsplEscape(company && company.name || '');
  const lines = [
    'SIZE 40 mm,30 mm',
    'GAP 2 mm,0',
    'DIRECTION 1',
    'CODEPAGE 65001',
    'CLS',
    `QRCODE 8,16,M,4,A,0,"${qr}"`,
    `TEXT 150,16,"TSS24.BF2",0,1,1,"款号:${styleNo}"`,
    `TEXT 150,46,"TSS24.BF2",0,1,1,"床次:${tsplEscape(ticket.bed_no)} 扎:${tsplEscape(ticket.bundle_no)}"`,
    `TEXT 150,76,"TSS24.BF2",0,1,1,"${color} ${size}"`,
    `TEXT 150,110,"TSS24.BF2",0,2,2,"x${ticket.qty}"`,
    `TEXT 150,180,"TSS24.BF2",0,1,1,"扎:${tsplEscape(ticket.bundle_no)} 数:${ticket.qty}"`,
    `TEXT 8,208,"TSS24.BF2",0,1,1,"${companyNo}"`,
    'PRINT 1'
  ];
  return lines.join('\r\n') + '\r\n';
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

const cmd = buildTspl(ticket, company);
const buf = encodeUtf8(cmd);
const port = process.argv[2] || 'USB001';
fs.writeFileSync(__dirname + '/tspl_test.bin', buf);
console.log('bytes:', buf.length, '->', __dirname + '\\tspl_test.bin');
console.log(cmd.split('\r\n').join(' / '));
