/**
 * 轻量二维码矩阵生成器（纯本地实现，零依赖，不引入 npm 包）。
 * 能力：Byte 模式、纠错等级 M、版本 1-6（容量 14~106 字节，覆盖企业编号场景）、
 * 8 种掩码按规则 1/2/4 惩罚度择优；版本 ≥7 的版本信息块未实现（超出容量即抛错）。
 *
 * 导出 make(text): boolean[][] —— true 为深色模块；不含静区（quiet zone），
 * 由绘制方自行留白（建议 2~4 模块）。
 *
 * 生成结果与标准 QR 解码器兼容（扫码 / 识别均可用）。
 */

// ============ GF(256) 伽罗瓦域（本原多项式 0x11d） ============

const EXP = new Array<number>(256);
const LOG = new Array<number>(256);
(function initGF() {
  let x = 1;
  for (let i = 0; i < 255; i++) {
    EXP[i] = x;
    LOG[x] = i;
    x <<= 1;
    if (x & 0x100) x ^= 0x11d;
  }
  EXP[255] = EXP[0];
})();

function gmul(a: number, b: number): number {
  if (a === 0 || b === 0) return 0;
  return EXP[(LOG[a] + LOG[b]) % 255];
}

// ============ 版本参数（纠错等级 M） ============

interface RSBlockSpec { count: number; dataPerBlock: number; }
interface VersionSpec { ecPerBlock: number; blocks: RSBlockSpec[]; }

/** ISO/IEC 18004 表 22~25：版本 1-6、纠错 M 的分块与每块纠错码字数 */
const VERSIONS: VersionSpec[] = [
  { ecPerBlock: 10, blocks: [{ count: 1, dataPerBlock: 16 }] }, // v1
  { ecPerBlock: 16, blocks: [{ count: 1, dataPerBlock: 28 }] }, // v2
  { ecPerBlock: 26, blocks: [{ count: 1, dataPerBlock: 44 }] }, // v3
  { ecPerBlock: 18, blocks: [{ count: 2, dataPerBlock: 32 }] }, // v4
  { ecPerBlock: 24, blocks: [{ count: 2, dataPerBlock: 43 }] }, // v5
  { ecPerBlock: 16, blocks: [{ count: 4, dataPerBlock: 27 }] }  // v6
];

/** 对齐图案中心坐标（v1 无） */
const ALIGN_CENTERS: number[][] = [
  [],
  [6, 18],
  [6, 22],
  [6, 26],
  [6, 30],
  [6, 34]
];

function dataCodewords(v: number): number {
  return VERSIONS[v - 1].blocks.reduce((n, b) => n + b.count * b.dataPerBlock, 0);
}

/** Byte 模式可容纳字节数（版本 1-9 模式指示 4bit + 计数 8bit + 终止符占用） */
function capacityBytes(v: number): number {
  return dataCodewords(v) - 2;
}

// ============ Reed-Solomon 纠错 ============

/** 生成多项式（首项系数 1，长度 n+1），∏(x - α^i) i=0..n-1 */
function rsGenPoly(n: number): number[] {
  let poly = [1];
  for (let i = 0; i < n; i++) {
    const next = new Array<number>(poly.length + 1).fill(0);
    for (let j = 0; j < poly.length; j++) {
      next[j] ^= poly[j];               // × x
      next[j + 1] ^= gmul(poly[j], EXP[i]); // × α^i
    }
    poly = next;
  }
  return poly;
}

/** 多项式除法求余，返回 ecLen 个纠错码字 */
function rsCompute(data: number[], gen: number[], ecLen: number): number[] {
  const buf = data.concat(new Array<number>(ecLen).fill(0));
  for (let i = 0; i < data.length; i++) {
    const coef = buf[i];
    if (coef !== 0) {
      for (let j = 0; j < gen.length; j++) {
        buf[i + j] ^= gmul(gen[j], coef);
      }
    }
  }
  return buf.slice(data.length);
}

// ============ 数据编码（Byte 模式） ============

/** UTF-8 编码（小程序无 TextEncoder，走 encodeURIComponent） */
function toUtf8(text: string): number[] {
  const s = encodeURIComponent(text);
  const out: number[] = [];
  for (let i = 0; i < s.length; i++) {
    if (s[i] === '%') {
      out.push(parseInt(s.substr(i + 1, 2), 16));
      i += 2;
    } else {
      out.push(s.charCodeAt(i));
    }
  }
  return out;
}

/** 编码为最终码字序列（数据块 + 纠错块交织）；容量不足抛错 */
function buildCodewords(text: string, v: number): number[] {
  const bytes = toUtf8(text);
  if (bytes.length > capacityBytes(v)) {
    throw new Error('内容超过该版本容量');
  }
  const spec = VERSIONS[v - 1];
  const totalData = dataCodewords(v);
  const capBits = totalData * 8;

  const bits: number[] = [];
  const put = (val: number, len: number) => {
    for (let i = len - 1; i >= 0; i--) bits.push((val >>> i) & 1);
  };
  put(0x4, 4); // 模式指示符：Byte（版本 ≤9 时计数 8 bit）
  put(bytes.length, 8);
  for (const b of bytes) put(b, 8);
  put(0, Math.min(4, capBits - bits.length)); // 终止符
  while (bits.length % 8 !== 0) bits.push(0); // 字节对齐
  const pad = [0xEC, 0x11];
  let pi = 0;
  while (bits.length < capBits) put(pad[pi++ % 2], 8); // 补齐码字

  const codewords: number[] = [];
  for (let i = 0; i < bits.length; i += 8) {
    let b = 0;
    for (let j = 0; j < 8; j++) b = (b << 1) | bits[i + j];
    codewords.push(b);
  }

  // 分块算纠错，再按标准交织（先数据后纠错）
  const gen = rsGenPoly(spec.ecPerBlock);
  const dataBlocks: number[][] = [];
  const ecBlocks: number[][] = [];
  for (const blk of spec.blocks) {
    for (let n = 0; n < blk.count; n++) {
      const data = codewords.splice(0, blk.dataPerBlock);
      dataBlocks.push(data);
      ecBlocks.push(rsCompute(data, gen, spec.ecPerBlock));
    }
  }
  const out: number[] = [];
  const maxLen = Math.max(...dataBlocks.map((b) => b.length));
  for (let i = 0; i < maxLen; i++) {
    for (const b of dataBlocks) if (i < b.length) out.push(b[i]);
  }
  for (let i = 0; i < spec.ecPerBlock; i++) {
    for (const b of ecBlocks) out.push(b[i]);
  }
  return out;
}

// ============ 矩阵构建 ============

const MASKS: Array<(i: number, j: number) => boolean> = [
  (i, j) => (i + j) % 2 === 0,
  (i) => i % 2 === 0,
  (_i, j) => j % 3 === 0,
  (i, j) => (i + j) % 3 === 0,
  (i, j) => (Math.floor(i / 2) + Math.floor(j / 3)) % 2 === 0,
  (i, j) => ((i * j) % 2) + ((i * j) % 3) === 0,
  (i, j) => (((i * j) % 2) + ((i * j) % 3)) % 2 === 0,
  (i, j) => (((i + j) % 2) + ((i * j) % 3)) % 2 === 0
];

/** BCH(15,5) 生成格式信息位（含 0x5412 掩码） */
function bch15(data: number): number {
  const G = 0x537;
  let d = data << 10;
  for (let i = 14; i >= 10; i--) {
    if ((d >>> i) & 1) d ^= G << (i - 10);
  }
  return ((data << 10) | d) ^ 0x5412;
}

type Cell = boolean | null;

/** 定位/对齐/时序图形 + 格式信息区占位（false），暗模块置 true */
function functionPattern(v: number): Cell[][] {
  const size = v * 4 + 17;
  const m: Cell[][] = [];
  for (let r = 0; r < size; r++) m.push(new Array<Cell>(size).fill(null));

  const setFinder = (row: number, col: number) => {
    for (let r = -1; r <= 7; r++) {
      if (row + r < 0 || row + r >= size) continue;
      for (let c = -1; c <= 7; c++) {
        if (col + c < 0 || col + c >= size) continue;
        const inCore = r >= 0 && r <= 6 && c >= 0 && c <= 6;
        const ring = r === 0 || r === 6 || c === 0 || c === 6;
        const center = r >= 2 && r <= 4 && c >= 2 && c <= 4;
        m[row + r][col + c] = inCore && (ring || center);
      }
    }
  };
  setFinder(0, 0);
  setFinder(0, size - 7);
  setFinder(size - 7, 0);

  // 对齐图案：中心已占用（与定位图形重叠）则跳过；中轴与时序图形交替规律一致
  for (const row of ALIGN_CENTERS[v - 1]) {
    for (const col of ALIGN_CENTERS[v - 1]) {
      if (m[row][col] !== null) continue;
      for (let dr = -2; dr <= 2; dr++) {
        for (let dc = -2; dc <= 2; dc++) {
          m[row + dr][col + dc] = Math.max(Math.abs(dr), Math.abs(dc)) !== 1;
        }
      }
    }
  }

  // 时序图形（行 6 / 列 6，交替）
  for (let i = 8; i < size - 8; i++) {
    if (m[6][i] === null) m[6][i] = i % 2 === 0;
    if (m[i][6] === null) m[i][6] = i % 2 === 0;
  }

  // 格式信息两份副本的位置先占位（最终按所选掩码写入），暗模块固定深色
  const reserve = (r: number, c: number) => {
    if (m[r][c] === null) m[r][c] = false;
  };
  for (let i = 0; i <= 8; i++) {
    reserve(i, 8);
    reserve(8, i);
  }
  for (let i = size - 8; i < size; i++) {
    reserve(i, 8);
    reserve(8, i);
  }
  m[size - 8][8] = true;
  return m;
}

/** 写入格式信息（纠错等级 M = 00） */
function setFormat(m: Cell[][], size: number, maskNo: number) {
  const bits = bch15((0 << 3) | maskNo);
  for (let i = 0; i < 15; i++) {
    const on = ((bits >>> i) & 1) === 1;
    // 副本一：左上定位图形周围
    if (i < 6) m[i][8] = on;
    else if (i < 8) m[i + 1][8] = on;
    else m[size - 15 + i][8] = on;
    // 副本二：右上 + 左下
    if (i < 8) m[8][size - 1 - i] = on;
    else if (i === 8) m[8][7] = on;
    else m[8][14 - i] = on;
  }
  m[size - 8][8] = true; // 暗模块
}

/** 之字形放置数据 + 掩码，并写格式信息 */
function placeData(base: Cell[][], codewords: number[], maskNo: number): Cell[][] {
  const size = base.length;
  const m: Cell[][] = base.map((row) => row.slice());
  const totalBits = codewords.length * 8;
  let bitIdx = 0;
  let upward = true;
  for (let right = size - 1; right >= 1; right -= 2) {
    if (right === 6) right = 5;
    for (let vert = 0; vert < size; vert++) {
      const row = upward ? size - 1 - vert : vert;
      for (let j = 0; j < 2; j++) {
        const col = right - j;
        if (m[row][col] !== null) continue;
        let dark = false;
        if (bitIdx < totalBits) {
          dark = ((codewords[bitIdx >> 3] >>> (7 - (bitIdx & 7))) & 1) === 1;
        }
        bitIdx++;
        if (MASKS[maskNo](row, col)) dark = !dark;
        m[row][col] = dark;
      }
    }
    upward = !upward;
  }
  setFormat(m, size, maskNo);
  return m;
}

// ============ 掩码评估（规则 1/2/4；规则 3 略，不影响可解码性） ============

function penaltyScore(m: boolean[][], size: number): number {
  let score = 0;
  // 规则 1：同行/列连续同色 ≥5，罚 3+(n-5)
  for (let dir = 0; dir < 2; dir++) {
    for (let a = 0; a < size; a++) {
      let run = 1;
      for (let b = 1; b < size; b++) {
        const cur = dir === 0 ? m[a][b] : m[b][a];
        const prev = dir === 0 ? m[a][b - 1] : m[b - 1][a];
        if (cur === prev) {
          run++;
        } else {
          if (run >= 5) score += 3 + (run - 5);
          run = 1;
        }
      }
      if (run >= 5) score += 3 + (run - 5);
    }
  }
  // 规则 2：2×2 同色块
  for (let r = 0; r < size - 1; r++) {
    for (let c = 0; c < size - 1; c++) {
      const v = m[r][c];
      if (m[r][c + 1] === v && m[r + 1][c] === v && m[r + 1][c + 1] === v) score += 3;
    }
  }
  // 规则 4：深色占比偏离 50%，每 ±5% 罚 10
  let dark = 0;
  for (let r = 0; r < size; r++) {
    for (let c = 0; c < size; c++) if (m[r][c]) dark++;
  }
  const pct = Math.floor((dark * 100) / (size * size));
  score += Math.floor(Math.abs(pct - 50) / 5) * 10;
  return score;
}

// ============ 对外入口 ============

export function make(text: string): boolean[][] {
  let lastErr: Error = new Error('二维码生成失败');
  for (let v = 1; v <= VERSIONS.length; v++) {
    try {
      return makeWithVersion(text, v);
    } catch (e) {
      lastErr = e as Error;
    }
  }
  throw lastErr;
}

function makeWithVersion(text: string, v: number): boolean[][] {
  const codewords = buildCodewords(text, v);
  const base = functionPattern(v);
  let best: boolean[][] | null = null;
  let bestScore = Infinity;
  for (let mask = 0; mask < 8; mask++) {
    const placed = placeData(base, codewords, mask);
    const solid = placed.map((row) => row.map((c) => c === true));
    const s = penaltyScore(solid, solid.length);
    if (s < bestScore) {
      bestScore = s;
      best = solid;
    }
  }
  return best as boolean[][];
}
