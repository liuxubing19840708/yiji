/**
 * 报价回填对比页（quotation 域 P2，D3 软提醒只读）：
 * - 报价假设（材料/工价人工 base 不加倍率/附加费 → cost_assumed）vs
 *   OrderCost 实际（材料/人工/总成本/理论收入）并排；
 * - 逐项 diff（实际−假设）+ diff_rate（假设 0 → null 显「—」）；
 * - message 软提醒横幅（未关联订单/未重算成本/数据过期）——只提醒不拦截；
 * - 权限：挂 quotation.view（与详情页同点入口）。
 */
import { requireLogin } from '../../../utils/auth';
import { hasPerm } from '../../../utils/perm';
import { fetchBackfillCompare, BackfillCompare, BackfillDiffItem } from '../../../services/quotation';

interface DiffRowVM {
  label: string;
  assumed: string;
  actual: string;
  diff: string;
  diffRate: string;
  /** 差异方向徽标：up 实际高于假设 / down 低于 / flat 持平 */
  dir: 'up' | 'down' | 'flat';
}

function diffRow(label: string, d: BackfillDiffItem): DiffRowVM {
  const diffNum = Number(d.diff);
  return {
    label,
    assumed: d.assumed,
    actual: d.actual,
    diff: d.diff,
    diffRate: d.diff_rate === null || d.diff_rate === undefined
      ? '—' : `${d.diff_rate}%`,
    dir: diffNum > 0 ? 'up' : diffNum < 0 ? 'down' : 'flat'
  };
}

Page({
  data: {
    denied: false,
    id: 0,
    loading: true,
    error: false,
    rep: null as BackfillCompare | null,
    diffRows: [] as DiffRowVM[],
    actualMissing: false
  },

  onLoad(query: Record<string, string | undefined>) {
    this.setData({
      id: Number(query.id || 0),
      denied: !hasPerm('quotation.view')
    });
  },

  onShow() {
    if (!requireLogin()) return;
    if (this.data.denied || !this.data.id) return;
    this.load();
  },

  onPullDownRefresh() {
    this.load();
    wx.stopPullDownRefresh();
  },

  load() {
    this.setData({ loading: true, error: false });
    fetchBackfillCompare(this.data.id)
      .then((rep) => {
        const diffRows: DiffRowVM[] = [];
        if (rep.diff) {
          diffRows.push(diffRow('材料', rep.diff.material));
          diffRows.push(diffRow('人工（工价口径）', rep.diff.labor));
          diffRows.push(diffRow('成本合计', rep.diff.total));
          diffRows.push(diffRow('收入（报价 vs 理论）', rep.diff.revenue));
        }
        this.setData({
          loading: false,
          rep,
          diffRows,
          actualMissing: !rep.actual
        });
      })
      .catch(() => this.setData({ loading: false, error: true }));
  }
});
