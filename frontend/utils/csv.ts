/**
 * 报表 CSV 导出（2026-11-02 评审 Top10 #9：报表 Excel 导出）。
 * - buildCsv：BOM 头（Excel 打开不乱码）+ 逗号/引号/换行转义（RFC 4180）；
 * - shareCsvFile：写用户目录后 wx.shareFileMessage 让用户发给自己/用 WPS 打开；
 *   开发者工具或低版本不支持分享时回落「已复制」剪贴板（TSV 粘贴 Excel 同样成表）。
 * 纯前端生成，不新增后端端点；导出范围=当前已加载行（分页补拉后自动扩）。
 */

/** 单元格转义：含分隔符/引号/换行时加引号（RFC 4180） */
function escCell(v: string, sep: string): string {
  const s = String(v ?? '');
  return new RegExp(`["${sep === ',' ? ',' : '\\t'}\\r\\n]`).test(s)
    ? `"${s.replace(/"/g, '""')}"` : s;
}

function buildDelimited(headers: string[], rows: string[][], sep: string, bom: boolean): string {
  const lines = [headers.map((h) => escCell(h, sep)).join(sep)];
  for (const r of rows) lines.push(r.map((c) => escCell(c, sep)).join(sep));
  return (bom ? '﻿' : '') + lines.join('\r\n');
}

/** 二维表 → CSV 字符串（含 BOM；headers 与 rows 等宽由调用方保证） */
export function buildCsv(headers: string[], rows: string[][]): string {
  return buildDelimited(headers, rows, ',', true);
}

/** 写文件并弹出系统分享（文件名须带 .csv）；不支持时回落剪贴板 */
export function shareCsvFile(filename: string, csv: string): Promise<void> {
  return new Promise((resolve, reject) => {
    const path = `${wx.env.USER_DATA_PATH}/${filename}`;
    const fs = wx.getFileSystemManager();
    fs.writeFile({
      filePath: path,
      data: csv,
      encoding: 'utf8',
      success: () => {
        if (wx.shareFileMessage) {
          wx.shareFileMessage({
            filePath: path,
            fileName: filename,
            success: () => resolve(),
            fail: () => fallbackCopy(csv, resolve, reject)
          });
        } else {
          fallbackCopy(csv, resolve, reject);
        }
      },
      fail: (e) => reject(e)
    });
  });
}

/** 剪贴板兜底：TSV 制表符分隔（直接粘贴进 Excel 即成表） */
function fallbackCopy(csv: string, resolve: () => void, reject: (e: unknown) => void): void {
  // 从 CSV 反解不可靠，直接按原始表重建——调用方传 rows 太重，
  // 这里退而求其次：把 CSV 行按 RFC 引号字段粗分（金额/单号不含逗号，够用）
  const lines = csv.replace(/^﻿/, '').split('\r\n');
  const tsvLines = lines.map((l) => splitCsvLine(l).join('\t'));
  wx.setClipboardData({
    data: tsvLines.join('\n'),
    success: () => {
      wx.showToast({ title: '已复制到剪贴板', icon: 'none' });
      resolve();
    },
    fail: (e) => reject(e)
  });
}

/** RFC 4180 粗分行：引号段内的逗号不分隔 */
function splitCsvLine(line: string): string[] {
  const out: string[] = [];
  let cur = '';
  let inQ = false;
  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (inQ) {
      if (ch === '"' && line[i + 1] === '"') { cur += '"'; i++; }
      else if (ch === '"') inQ = false;
      else cur += ch;
    } else if (ch === '"') {
      inQ = true;
    } else if (ch === ',') {
      out.push(cur); cur = '';
    } else {
      cur += ch;
    }
  }
  out.push(cur);
  return out;
}
