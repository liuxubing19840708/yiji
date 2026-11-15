/**
 * 薪资条页（竞品对齐 GAP-4，2026-11-04）：按结算单逐员工展示薪资条，
 * 支持导出 xlsx（每员工工序明细+小计，真机用 WPS/微信打开打印）。
 * 入口：薪酬管理 → 结算记录卡片展开「薪资条」。
 */
import { requireLogin } from '../../../utils/auth';
import { money } from '../../../utils/format';
import { fetchPayslip, downloadPayslipExport, PayslipResponse } from '../../../services/salary';

/** 员工薪资条 VM */
interface SlipVM {
  name: string;
  amountText: string;
  fromSnapshot: boolean;
  items: Array<{ process: string; qty: number; amountText: string }>;
}

Page({
  data: {
    id: 0,
    loading: true,
    exporting: false,
    periodText: '',
    statusDisplay: '',
    totalText: '',
    employeeCount: 0,
    slips: [] as SlipVM[]
  },

  onLoad(query: Record<string, string | undefined>) {
    requireLogin();
    const id = Number(query.id || 0);
    this.setData({ id });
    if (!id) {
      wx.showToast({ title: '缺少结算单参数', icon: 'none' });
      return;
    }
    this.load();
  },

  load() {
    this.setData({ loading: true });
    fetchPayslip(this.data.id)
      .then((res: PayslipResponse) => {
        const s = res.settlement;
        const slips: SlipVM[] = (res.rows || []).map((r) => ({
          name: r.name,
          amountText: money(r.amount),
          fromSnapshot: r.snapshot_amount !== null,
          items: (r.items || []).map((it) => ({
            process: it.process || '（未挂工序）',
            qty: it.qty,
            amountText: money(it.amount)
          }))
        }));
        this.setData({
          loading: false,
          periodText: s.period_start === s.period_end
            ? s.period_start
            : `${s.period_start} ~ ${s.period_end}`,
          statusDisplay: s.status_display,
          totalText: money(res.total),
          employeeCount: Number(s.employee_count || slips.length),
          slips
        });
      })
      .catch(() => this.setData({ loading: false }));
  },

  async onExport() {
    if (this.data.exporting) return;
    this.setData({ exporting: true });
    try {
      await downloadPayslipExport(this.data.id);
    } catch (e) {
      wx.showToast({ title: '导出失败', icon: 'none' });
    } finally {
      this.setData({ exporting: false });
    }
  }
});
