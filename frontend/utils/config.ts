/** 全局配置：以后端实际部署地址为准；真机调试需 HTTPS 或开启「不校验合法域名」。
 *  192.168.10.16 为本机局域网 IP，后端以 0.0.0.0:8000 监听供真机访问；IP 变动时同步改这里。 */
export const BASE_URL = 'http://192.168.10.16:8000/api';

/** 菲票二维码内容分隔符（编菲生成的二维码按此解析，见 docs/CONVENTIONS.md） */
export const QR_SEPARATOR = '|';
