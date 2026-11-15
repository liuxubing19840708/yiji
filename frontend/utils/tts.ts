/**
 * 文字转语音报数（评审 Top10 #7，2026-11-02）：扫菲确认页「语音报数」。
 * 微信同声传译插件（WechatSI）textToSpeech 合成音频 → innerAudioContext 播放；
 * 插件未声明（MP 后台未授权）/合成失败时静默降级（返回 false，页面不提示不打扰扫码节奏）。
 * 与 utils/speech.ts（语音→文字）同一插件、方向相反。
 * 连续报数：前一段未播完时 stop 掉再播新的（扫码节奏快，旧语音无意义）。
 */

let audioCtx: WechatMiniprogram.InnerAudioContext | null = null;
/** 上一段合成出的本地音频路径（innerAudioContext.src 复用，避免临时文件堆积） */
let lastFile = '';
/** 会话级开关（storage 持久化由页面管） */
let enabled = false;

/** 运行环境是否具备 TTS 能力（插件缺失 → false，调用方静默跳过） */
export function ttsAvailable(): boolean {
  try {
    const plugin = requirePlugin('WechatSI') as {
      textToSpeech?: (opts: Record<string, unknown>) => void;
    };
    return typeof plugin.textToSpeech === 'function';
  } catch {
    return false;
  }
}

export function setTtsEnabled(on: boolean) {
  enabled = on;
  if (!on) stopSpeaking();
}

export function isTtsEnabled(): boolean {
  return enabled;
}

function ensureCtx(): WechatMiniprogram.InnerAudioContext {
  if (!audioCtx) {
    audioCtx = wx.createInnerAudioContext();
    audioCtx.obeyMuteSwitch = false; // 报数不随静音键（车间嘈杂场景用户主动开启）
  }
  return audioCtx;
}

export function stopSpeaking() {
  if (audioCtx) {
    try { audioCtx.stop(); } catch { /* 忽略 */ }
  }
}

/** 报读一段文字（数字直接传阿拉伯数字，TTS 自然读出）。能力缺失/未开启/合成失败均静默。 */
export function speak(text: string) {
  if (!enabled || !text) return;
  let plugin: { textToSpeech: (opts: Record<string, unknown>) => void };
  try {
    const p = requirePlugin('WechatSI') as { textToSpeech?: (o: Record<string, unknown>) => void };
    if (!p || typeof p.textToSpeech !== 'function') return;
    plugin = p as { textToSpeech: (o: Record<string, unknown>) => void };
  } catch {
    return;
  }
  // 前一段还在播：先停（扫码连续作业，旧语音过时无用）
  const ctx = ensureCtx();
  try { ctx.stop(); } catch { /* 忽略 */ }
  plugin.textToSpeech({
    lang: 'zh_CN',
    content: text.slice(0, 150), // 插件单次合成上限保护
    success: (res: { filename?: string }) => {
      if (!res || !res.filename) return;
      lastFile = res.filename;
      ctx.src = lastFile;
      ctx.play();
    },
    fail: () => { /* 合成失败静默：报数是增强，不是主流程依赖 */ }
  });
}
