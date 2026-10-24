/**
 * 进行中床次进度卡（共用渲染组件，P3B「beds 合并」）：
 * - 消费方：工作中页（工作台「进行中床次」精简版）+ 看板「大屏」页签（§4.1 车间大屏）；
 * - 输入形状统一为 utils/bed-progress.ts 的 BedProgressVM（口径已由后端 stats.py 唯一化）；
 * - 渲染字段（两处一致不丢）：床次号 / 款号 / 款名 + 登记进度条 +
 *   已登记 x / y 件 + 完成 a / b 扎；
 * - clickable=true 时卡片可点，tap 上抛 bedtap 事件（detail: {id, index}），
 *   下钻目标由页面决定（大屏：床次详情；工作台：保持原样不可点）。
 */
import { BedProgressVM } from '../../utils/bed-progress';

Component({
  options: { addGlobalClass: true },

  properties: {
    beds: { type: Array, value: [] as BedProgressVM[] },
    /** 卡片是否可点（大屏=true 上抛 bedtap；工作台=false 保持原浏览行为） */
    clickable: { type: Boolean, value: false }
  },

  methods: {
    /** 卡片 tap：只上抛（带 id/index），不直接导航（跳转归页面管，便于各页兜底） */
    onBedTap(e: WechatMiniprogram.TouchEvent) {
      if (!this.data.clickable) return;
      const index = Number(e.currentTarget.dataset.index);
      const bed = (this.data.beds as BedProgressVM[])[index];
      if (!bed) return;
      this.triggerEvent('bedtap', { id: bed.id, index });
    }
  }
});
