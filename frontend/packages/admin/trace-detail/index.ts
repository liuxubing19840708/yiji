/**
 * 追溯详情页（P0，2026-10-18）：三种视角共用。
 * - mode=ticket      终端追溯：一张菲票的全链路（菲票/床次/批次/登记时间线/打印痕迹）
 * - mode=lot         正向追溯：批次 → 受影响床次/菲票 → 客户/来源单号
 * - mode=production  床次视角：批次血缘 + 菲票汇总 + 领料流水
 */
import { requireLogin } from '../../../utils/auth';
import {
  fetchTraceTicket, fetchTraceLot, fetchTraceProduction,
  TraceTicketResult, TraceLotResult, TraceProductionResult
} from '../../../services/traceability';

interface RecordVM {
  process: string;
  user: string;
  operator: string;
  qty: number;
  time: string;
}

Page({
  data: {
    mode: 'ticket' as 'ticket' | 'lot' | 'production',
    id: 0,
    loading: true,
    error: false,
    ticket: null as TraceTicketResult['ticket'] | null,
    production: null as TraceTicketResult['production'] | null,
    lots: [] as Array<{ id: number; lot_no: string; fabric_name?: string; color?: string; qty?: string; batch_date?: string | null }>,
    records: [] as RecordVM[],
    print: null as TraceTicketResult['print'] | null,
    lotProductions: [] as TraceLotResult['productions'],
    impact: null as TraceLotResult['impact'] | null,
    lotInfo: null as TraceLotResult['lot'] | null,
    issueRows: [] as TraceProductionResult['issues'],
    ticketCount: 0,
    qtyTotal: 0
  },

  onLoad(options: Record<string, string>) {
    const mode = (options.mode || 'ticket') as 'ticket' | 'lot' | 'production';
    this.setData({ mode, id: Number(options.id) || 0 });
  },

  onShow() {
    if (!requireLogin()) return;
    this.load();
  },

  load() {
    const { mode, id } = this.data;
    if (!id) return;
    this.setData({ loading: true, error: false });
    if (mode === 'ticket') {
      fetchTraceTicket(id)
        .then((res) => {
          this.setData({
            loading: false,
            ticket: res.ticket,
            production: res.production,
            lots: res.lots || [],
            records: (res.records || []).map((r) => ({
              process: r.process,
              user: r.user,
              operator: r.operator || '',
              qty: r.qty,
              time: String(r.registered_at || '').slice(0, 16)
            })),
            print: res.print
          });
          wx.setNavigationBarTitle({ title: `菲票 ${res.ticket.bed_no} 第${res.ticket.bundle_no}扎` });
        })
        .catch(() => this.setData({ loading: false, error: true }));
    } else if (mode === 'lot') {
      fetchTraceLot(id)
        .then((res) => this.setData({
          loading: false,
          lotInfo: res.lot,
          lotProductions: res.productions || [],
          impact: res.impact
        }))
        .catch(() => this.setData({ loading: false, error: true }));
    } else {
      fetchTraceProduction(id)
        .then((res) => this.setData({
          loading: false,
          production: res.production,
          lots: res.lots || [],
          ticketCount: res.ticket_count,
          qtyTotal: res.qty_total,
          issueRows: res.issues || []
        }))
        .catch(() => this.setData({ loading: false, error: true }));
    }
  }
});
