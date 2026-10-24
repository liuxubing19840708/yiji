/**
 * 图片 URL 归一化（2026-10-17 真机修）：
 * 上传接口返回 request.build_absolute_uri(...)——域名取自上传时的请求，
 * 开发者工具里传的图存的是 http://127.0.0.1:8000/...，手机上 127.0.0.1 指向手机自己 → 图全裂。
 * mediaUrl 统一处理：相对路径补 BASE_URL 域名；127.0.0.1/localhost 旧数据换域名；其余原样。
 * 各页渲染图片一律走这里（styles/productions/production-detail/salary 等）。
 */
import { BASE_URL } from './config';

const ORIGIN = BASE_URL.replace(/\/api$/, '');

const LOCAL_HOST = /^https?:\/\/(127\.0\.0\.1|localhost)(:\d+)?/i;

export function mediaUrl(raw?: string): string {
  const u = (raw || '').trim();
  if (!u) return '';
  if (LOCAL_HOST.test(u)) {
    return u.replace(LOCAL_HOST, ORIGIN);
  }
  if (u.indexOf('http') === 0) return u;
  return ORIGIN + (u.indexOf('/') === 0 ? u : `/${u}`);
}
