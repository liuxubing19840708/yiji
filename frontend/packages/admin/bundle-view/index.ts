/**
 * 按扎卡片视图（竞品对齐借鉴#1，2026-11-05，docs/功能对比_衣协同vs衣计.md）：
 * 床次内逐扎卡片 = 扎号/颜色/尺码/数量 + 状态徽标；展开逐工序行 = 工序价目
 * （床次>款式>默认计价链）+ 已登量/登满点；有 salary.view / records.revoke
 * 者可对单条登记行内改数（PUT /records，原因必填）与撤销（DELETE），
 * 门禁最终在后端（本人当日 / records.revoke / 当月开关 / 已结算拦截）。
 */
import { requireLogin, getUser } from '../../../utils/auth';
import { hasPerm } from '../../../utils/perm';
import { money } from '../../../utils/format';
import {
  fetchBundleCards, updateRecordQty, deleteRecord,
  BundleCardRow, BundleRecordItem
} from '../../../services/production';

const STATE_TABS = [
  { key: '', label: '全部' },
  { key: 'none', label: '未登记' },
  { key: 'partial', label: '部分登记' },
  { key: 'done', label: '完成' }
];

/** 工序行 VM：状态点 + 价目 + 计数 + 记录明细（改/撤按钮按 canAct 预显隐） */
interface ProcVM {
  process_id: number;
  name: string;
  priceText: string;
  regQty: number;
  done: boolean;
  hasReg: boolean;
  records: Array<BundleRecordItem & { canAct: boolean }>;
}

interface CardVM {
  id: number;
  bundleNo: string;
  color: string;
  size: string;
  qty: number;
  state: string;
  stateDisplay: string;
  stateClass: string;
  progressText: string;
  expanded: boolean;
  procs: ProcVM[];
}

/** 改数弹层上下文：定位被改记录 + 原数量展示 */
interface ModifyCtx {
  recordId: number;
  title: string;
  beforeQty: number;
  qty: string;
  reason: string;
}

const STATE_CLASS: Record<string, string> = {
  none: 'badge-gray',
  partial: 'badge',
  done: 'badge-green'
};

Page({
  data: {
    bedNo: '',
    styleNo: '',
    loading: true,
    error: false,
    count: 0,
    processCount: 0,
    canSeeRecords: false,
    stateTabs: STATE_TABS,
    filterIndex: 0,
    keyword: '',
    cards: [] as CardVM[],
    // ---- 改数弹层 ----
    modifyShow: false,
    modify: { recordId: 0, title: '', beforeQty: 0, qty: '', reason: '' } as ModifyCtx,
    modifySubmitting: false
  },

  productionId: 0,

  onLoad(options: Record<string, string>) {
    if (!requireLogin()) return;
    this.productionId = Number(options.production_id) || 0;
    this.setData({
      bedNo: options.bed_no ? decodeURIComponent(options.bed_no) : '',
      styleNo: options.style_no ? decodeURIComponent(options.style_no) : ''
    });
    this.reload();
  },

  onPullDownRefresh() {
    this.reload().then(() => wx.stopPullDownRefresh());
  },

  reload(): Promise<void> {
    if (!this.productionId) {
      this.setData({ loading: false, error: true });
      return Promise.resolve();
    }
    this.setData({ loading: true, error: false });
    const params: { keyword?: string; state?: string } = {};
    const kw = this.data.keyword.trim();
    if (kw) params.keyword = kw;
    const st = STATE_TABS[this.data.filterIndex].key;
    if (st) params.state = st;
    const myId = (getUser() || { id: 0 }).id;
    const canRevoke = hasPerm('records.revoke');
    return fetchBundleCards(this.productionId, params)
      .then((res) => {
        const cards: CardVM[] = (res.results || []).map((row: BundleCardRow) => ({
          id: row.id,
          bundleNo: row.bundle_no,
          color: row.color || '—',
          size: row.size || '—',
          qty: row.qty,
          state: row.state,
          stateDisplay: row.state_display,
          stateClass: STATE_CLASS[row.state] || 'badge-gray',
          progressText: `${row.registered_process_count}/${row.process_count}`,
          expanded: false,
          procs: (row.processes || []).map((p) => ({
            process_id: p.process_id,
            name: p.name,
            priceText: money(p.price || '0'),
            regQty: p.registered_qty,
            done: p.done,
            hasReg: p.registered_qty > 0 || (p.records || []).length > 0,
            records: (p.records || []).map((r) => ({
              ...r,
              // 预显隐：已结算不可动；撤销点或本人当日可动（最终门禁在后端）
              canAct: !r.settled && (canRevoke || (r.is_today && r.user_id === myId))
            }))
          }))
        }));
        this.setData({
          loading: false,
          count: res.count || 0,
          processCount: res.process_count || 0,
          canSeeRecords: !!res.can_see_records,
          cards
        });
      })
      .catch(() => this.setData({ loading: false, error: true }));
  },

  onStateTab(e: WechatMiniprogram.TouchEvent) {
    const idx = Number(e.currentTarget.dataset.index);
    if (idx === this.data.filterIndex) return;
    this.setData({ filterIndex: idx });
    this.reload();
  },

  onKeyword(e: WechatMiniprogram.Input) {
    this.setData({ keyword: e.detail.value });
  },

  onSearch() {
    this.reload();
  },

  onToggleCard(e: WechatMiniprogram.TouchEvent) {
    const idx = Number(e.currentTarget.dataset.index);
    const card = this.data.cards[idx];
    if (!card) return;
    this.setData({ [`cards[${idx}].expanded`]: !card.expanded });
  },

  // ============ 行内改数 / 撤销（竞品 f_035 扎卡操作对齐） ============

  onRecordTap(e: WechatMiniprogram.TouchEvent) {
    const ci = Number(e.currentTarget.dataset.ci);
    const pi = Number(e.currentTarget.dataset.pi);
    const ri = Number(e.currentTarget.dataset.ri);
    const rec = this.data.cards[ci]?.procs[pi]?.records[ri];
    if (!rec) return;
    if (!rec.canAct) {
      wx.showToast({ title: rec.settled ? '已结算，不可操作' : '仅可操作本人当日登记', icon: 'none' });
      return;
    }
    const card = this.data.cards[ci];
    wx.showActionSheet({
      itemList: ['修改数量', '撤销登记'],
      success: (res) => {
        if (res.tapIndex === 0) {
          this.setData({
            modifyShow: true,
            modify: {
              recordId: rec.id,
              title: `${card.bundleNo} · ${card.color}/${card.size} · ${this.data.cards[ci].procs[pi].name} · ${rec.user_name}`,
              beforeQty: rec.qty,
              qty: String(rec.qty),
              reason: ''
            }
          });
        } else {
          this.undoRecord(rec.id, rec.user_name, rec.qty);
        }
      }
    });
  },

  undoRecord(recordId: number, userName: string, qty: number) {
    wx.showModal({
      title: '撤销登记',
      content: `撤销 ${userName} 的 ${qty} 件登记？撤销后不可恢复。`,
      success: (res) => {
        if (!res.confirm) return;
        deleteRecord(recordId)
          .then(() => {
            wx.showToast({ title: '已撤销', icon: 'success' });
            this.reload();
          })
          .catch(() => { /* toast 已由请求层弹出 */ });
      }
    });
  },

  onModifyQty(e: WechatMiniprogram.Input) {
    this.setData({ 'modify.qty': e.detail.value });
  },

  onModifyReason(e: WechatMiniprogram.Input) {
    this.setData({ 'modify.reason': e.detail.value });
  },

  onCancelModify() {
    this.setData({ modifyShow: false });
  },

  onConfirmModify() {
    if (this.data.modifySubmitting) return;
    const m = this.data.modify;
    const qty = Number(m.qty);
    if (!qty || qty <= 0 || Math.floor(qty) !== qty) {
      wx.showToast({ title: '数量须为正整数', icon: 'none' });
      return;
    }
    const reason = m.reason.trim();
    if (!reason) {
      wx.showToast({ title: '请填写修改原因', icon: 'none' });
      return;
    }
    this.setData({ modifySubmitting: true });
    updateRecordQty(m.recordId, { qty, reason })
      .then(() => {
        wx.showToast({ title: '已修改', icon: 'success' });
        this.setData({ modifyShow: false, modifySubmitting: false });
        this.reload();
      })
      .catch(() => this.setData({ modifySubmitting: false }));
  }
});
