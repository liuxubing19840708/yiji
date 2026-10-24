/**
 * 展示格式化：后端金额一律字符串（防浮点误差），前端只做展示格式化，不参与计算。
 */
import { getCompanyId } from './auth';

/** 金额展示：'9638.00' → '9,638.00'；空值返 '0.00' */
export function money(value: string | number | null | undefined): string {
  const n = typeof value === 'number' ? value.toFixed(2) : (value || '0.00');
  const [int, dec] = String(n).split('.');
  const signed = int.startsWith('-');
  const digits = signed ? int.slice(1) : int;
  const grouped = digits.replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  return `${signed ? '-' : ''}${grouped}.${dec || '00'}`;
}

/** 件数等整数展示 */
export function count(value: number | null | undefined): string {
  const n = value || 0;
  return String(n).replace(/\B(?=(\d{3})+(?!\d))/g, ',');
}

/** date → 'YYYY-MM-DD'（本地时区） */
export function fmtDate(d: Date): string {
  const m = `${d.getMonth() + 1}`.padStart(2, '0');
  const day = `${d.getDate()}`.padStart(2, '0');
  return `${d.getFullYear()}-${m}-${day}`;
}

export function today(): string {
  return fmtDate(new Date());
}

export function yesterday(): string {
  const d = new Date();
  d.setDate(d.getDate() - 1);
  return fmtDate(d);
}

/** 本月第一天/最后一天 */
export function monthRange(): { start: string; end: string } {
  const now = new Date();
  const start = fmtDate(new Date(now.getFullYear(), now.getMonth(), 1));
  const end = fmtDate(new Date(now.getFullYear(), now.getMonth() + 1, 0));
  return { start, end };
}

export function lastMonthRange(): { start: string; end: string } {
  const now = new Date();
  const start = fmtDate(new Date(now.getFullYear(), now.getMonth() - 1, 1));
  const end = fmtDate(new Date(now.getFullYear(), now.getMonth(), 0));
  return { start, end };
}

/** 扫菲登记时间 → 展示时间（后端 ISO 带时区） */
export function fmtDateTime(iso: string): string {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const h = `${d.getHours()}`.padStart(2, '0');
  const m = `${d.getMinutes()}`.padStart(2, '0');
  return `${fmtDate(d)} ${h}:${m}`;
}

/** 当前公司 id（便捷取法，页面最常用） */
export function currentCompanyId(): number {
  return getCompanyId();
}
