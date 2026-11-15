/** 全局配置：SAOFEI_ENV 切换后端地址——发布前切 'prod'（HTTPS 合法域名，需 ICP 备案）。
 *  dev：本机后端 0.0.0.0:8000，真机调试用局域网 IP（IP 变动时同步改这里）或开「不校验合法域名」。 */
const SAOFEI_ENV: 'dev' | 'prod' = 'dev';

const BASE_URLS: Record<'dev' | 'prod', string> = {
  dev: 'http://192.168.10.16:8000/api',
  prod: 'https://api.yourdomain.com/api', // 上线时改为实际域名，与 backend/deploy/nginx-saofei.conf 一致
};

export const BASE_URL = BASE_URLS[SAOFEI_ENV];

/** 菲票二维码内容分隔符（编菲生成的二维码按此解析，见 docs/CONVENTIONS.md） */
export const QR_SEPARATOR = '|';
