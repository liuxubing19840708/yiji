/**
 * 容错截图（各 UI 套件共用）：
 * 开发者工具 mp.screenshot 密集调用会偶发 UNKNOWN / fail to capture screenshot，
 * 且异常会顺带拖垮自动化桥接——重试一次，仍失败则跳过（截图是产物不是断言，不阻塞测试）。
 */
module.exports = async function shot(mp, fullPath) {
  for (let i = 0; i < 2; i++) {
    try {
      await mp.screenshot({ path: fullPath });
      return;
    } catch (e) {
      await new Promise((r) => setTimeout(r, 800));
    }
  }
  console.log('[warn] screenshot 失败(已跳过):', fullPath);
};
