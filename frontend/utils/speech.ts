/**
 * 微信同声传译插件封装（语音 → 文字）。
 * 插件：微信同声传译（appid wx069ba97219f66d99，app.json plugins 声明），
 * 实时流式识别：start → onRecognize（中间结果）→ stop → onStop（最终结果）。
 * 插件缺失/基础库不支持时 getSpeechManager() 返回 null，页面据此降级（toast 提示不支持语音）。
 */

/** 插件识别管理器（仅按需窄化用到的成员） */
export interface SpeechManager {
  start(options: { lang?: 'zh_CN' | 'en_US'; duration?: number }): void;
  stop(): void;
  /** 中间结果（边说边出字） */
  onRecognize(cb: (res: { result?: string }) => void): void;
  /** 最终结果（stop 后一次性给全量文字） */
  onStop(cb: (res: { result?: string }) => void): void;
  onError(cb: (res: { msg?: string }) => void): void;
  /** 识别会话结束（stop/超时/出错后都会走，收尾 UI 状态用） */
  onEnd(cb: () => void): void;
}

let cached: SpeechManager | null | undefined;

/** 懒加载识别管理器：插件未声明/开发工具未装时 requirePlugin 抛错 → 返回 null */
export function getSpeechManager(): SpeechManager | null {
  if (cached !== undefined) return cached;
  try {
    const plugin = requirePlugin('WechatSI') as {
      getRecordRecognitionManager: () => SpeechManager;
    };
    cached = plugin.getRecordRecognitionManager();
  } catch {
    cached = null;
  }
  return cached;
}
