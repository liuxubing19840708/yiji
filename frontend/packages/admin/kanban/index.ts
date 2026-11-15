/**
 * 生产看板（可配置模板版）：表格由 GET /api/kanban/render 按模板列定义驱动渲染，
 * 顶部汇总卡/工序占比/登记排行/组排行仍来自 GET /api/dashboard?range= 聚合。
 * - 行粒度 = 款号+下单日期+颜色；日期分组行 + 款块（alt 交替底色）沿用旧看板观感；
 * - 列 = 模板列定义（标题/列宽/对齐/合并规则），支持任意纵向合并：
 *   merge=block（随款块整块一格）/ value（块内连续同值合并）；
 *   两级表头（列带 group）时表头渲染 group 跨列 + 叶子行，无组列纵向跨两行；
 * - 列线全部由真实格边框画出（cell 右/下边框），无百分比取整缝；
 * - 回落实底：渲染接口异常 → 用旧 dashboard plans 在客户端拼内置默认十列，
 *   绝不允许白屏（后端亦有无模板/失败回落内置模板的兜底）。
 */
import { requireLogin } from '../../../utils/auth';
import {
  fetchDashboard, fetchDashboardDim, DashboardResp, DashboardRange,
  DashboardGroupRank, DashboardProcess, DashboardDim,
  DashboardDimColumn, DashboardDimRow
} from '../../../services/dashboard';
import {
  fetchKanbanRender, fallbackRenderFromPlans,
  COLUMN_LINK_WHITELIST, COLUMN_TITLE_ALIAS,
  KanbanRenderResp, KanbanColumnDef, KanbanBlock
} from '../../../services/kanban-template';
import { fetchOrders } from '../../../services/orders';
import { fetchProductions } from '../../../services/production';
import { toBedProgressVM, BedProgressVM } from '../../../utils/bed-progress';
import { Paginated, Production, Order } from '../../../services/types';

/** 顶部时间范围 tab（key 与后端 range 参数对应；自定义=时间控件选起止日） */
const RANGE_TABS = [
  { key: 'yesterday', label: '昨日' },
  { key: 'today', label: '今日' },
  { key: 'week', label: '本周' },
  { key: 'month', label: '本月' },
  { key: 'custom', label: '自定义' }
] as Array<{ key: DashboardRange; label: string }>;

/** 计划表维度切换（2026-10-23 用户需求）：款式=模板计划表；
 *  订单/裁床/部门(岗位)/组/人 = /dashboard/dim 聚合表（部门暂按岗位归组） */
const DIM_TABS = [
  { key: 'style', label: '款式' },
  { key: 'order', label: '订单' },
  { key: 'bed', label: '裁床' },
  { key: 'post', label: '部门' },
  { key: 'group', label: '组' },
  { key: 'user', label: '人' },
  { key: 'process', label: '工序' }
] as Array<{ key: DashboardDim; label: string }>;

/** 表格行高（rpx，固定值保证跨列段对齐；床次两行内可完整显示） */
const ROW_H = 88;
/** 表头行高（rpx）；两级表头时占两行 */
const HEAD_H = 64;
/** 单级表头行高 */
const HEAD_SINGLE_H = 72;

// ---- 自适应列宽（width=0 = auto：按内容估宽 + 剩余空间均摊） ----
/** 自适应估宽护栏（rpx） */
const AUTO_MIN = 90;
const AUTO_MAX = 480;
/** 自适应估宽：CJK ≈24rpx/字、ASCII ≈13rpx/字符、左右内边距 28rpx */
const CJK_W = 24;
const ASCII_W = 13;
const CELL_PAD_W = 28;
/** 屏幕内容宽（rpx）＝windowWidth×2 − 卡片左右 padding 56 − 表格边框 4 */
function windowRpx(): number {
  try {
    const info = wx.getSystemInfoSync();
    return Math.max(600, Math.round((info.windowWidth || 375) * 2) - 60);
  } catch (e) {
    return 750 - 60;
  }
}

/** 单行文本估宽（rpx） */
function lineWidth(text: string): number {
  let w = 0;
  for (const ch of text || '') {
    w += ch.charCodeAt(0) > 255 ? CJK_W : ASCII_W;
  }
  return w;
}

/** 单元格文本估宽（多行文案取最长行） */
function cellWidth(text: string): number {
  const lines = String(text || '').split('\n');
  return Math.max(...lines.map(lineWidth), 0);
}

/** 自适应列：内容估宽（含表头，护栏 90–480），不足屏宽时剩余空间均摊补齐 */
function computeWidths(columns: KanbanColumnDef[],
                       groups: KanbanRenderResp['groups']): KanbanColumnDef[] {
  const cols = columns.map((c) => ({ ...c }));
  const autoIdx: number[] = [];
  cols.forEach((c, i) => {
    if (!c.width || c.width <= 0) autoIdx.push(i);
  });
  if (autoIdx.length) {
    // 每列采样所有行文本（cells 按列序对齐，防御性按 col id 兜底）
    for (const i of autoIdx) {
      const c = cols[i];
      let est = cellWidth(c.title);
      for (const g of groups || []) {
        for (const b of g.blocks || []) {
          for (const r of b.rows || []) {
            const cell = r.cells.length === cols.length
              ? r.cells[i]
              : r.cells.find((x) => x.col === c.id);
            if (cell) est = Math.max(est, cellWidth(cell.text));
          }
        }
      }
      c.width = Math.min(AUTO_MAX, Math.max(AUTO_MIN, Math.round(est)));
    }
    // 全部自适应列合计仍不足屏宽 → 剩余空间均摊补齐（铺满不留白）
    const total = cols.reduce((s, c) => s + c.width, 0);
    const avail = windowRpx();
    if (total < avail) {
      const share = Math.floor((avail - total) / autoIdx.length);
      if (share > 0) {
        for (const i of autoIdx) cols[i].width += share;
      }
    }
  }
  return cols;
}

/** YYYY-MM-DD（本地日期） */
function isoDate(d: Date): string {
  const p = (n: number): string => (n < 10 ? '0' + n : String(n));
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

/** 表头项：group 非空 = 组头（跨叶子列，colspan 由宽度求和体现）；
 *  tall = 无组列，纵向跨两行表头 */
interface HeadItemVM {
  key: string;
  group: string;            // 组名（tall 时为叶子标题）
  tall: boolean;
  width: number;            // 组总宽 = 叶子宽度之和
  leaves: Array<{ id: number; title: string; width: number }>;
}

/** 列段视图模型：一块内某列的纵向格子序列（合并格 height = span×ROW_H） */
interface SegVM {
  colId: number;
  width: number;
  cls: string;              // 对齐 + 占位样式（ta-right/ta-center/hold/merged）
  blockMerge: boolean;      // block 合并列：整段一格拉伸块高
  cells: Array<{ text: string; height: number; nav: string }>;
}

/** 款块视图模型：alt 交替底色 + 各列段 */
interface BlockVM {
  key: string;
  alt: boolean;
  segs: SegVM[];
}

/** 日期分组视图模型 */
interface GroupVM {
  date: string;
  label: string;
  blocks: BlockVM[];
}

/** 工序占比条视图模型 */
interface ProcVM extends DashboardProcess {
  barWidth: string; // 占比条宽度
}

/** 订单卡片视图模型（订单页签）：环形完成率 + 交期灯 + 落后色码 chip */
interface OrderVM {
  id: number;
  orderNo: string;
  statusText: string;
  statusCls: string;          // ord-badge-producing / ord-badge-done / ord-badge-cancelled
  customerName: string;
  deliveryText: string;       // 交期展示（无交期=「未设交期」）
  lightCls: string;           // 交期灯：lamp-green / lamp-yellow / lamp-red / lamp-gray
  lightText: string;          // 灯语义文案（沿用 §2.9：绿=充裕 黄=≤7天 红=逾期）
  progress: number;           // 0-100 完成率
  ringStyle: string;          // 完成率环形 conic-gradient 内联样式
  behind: boolean;            // 落后色码 chip（决策：完成率 <80% 且仍在生产中）
  totalQty: number;
}

/** 未挂单床次汇总（订单页签卡头）：床次未挂订单（order FK 空，SA1 落地后字段可见） */
interface UnlinkedBeds {
  count: number;
  qty: number;
}

/** 下钻 nav 特殊值：模板列 link=order → 看板内切到「订单」页签（非页面跳转） */
const NAV_ORDER_TAB = 'tab:orders';

/** 大屏页签「今日登记排行」条数上限（§4.1 车间大屏：今日登记排行 TopN） */
const SCREEN_RANK_TOP = 10;

/**
 * 看板筛选缓存（全局兜底）：navigateBack 时页面 data 天然保留（页面实例未销毁），
 * 这里再存一份模块级缓存，覆盖「页面被销毁重建」场景，onLoad 时恢复——
 * 落实 §2.9「返回时保留筛选状态：防点进去丢上下文」。
 */
interface KanbanFilterCache {
  range: DashboardRange;
  rangeLabel: string;
  customStart: string;
  customEnd: string;
  boardTab: 'plan' | 'orders' | 'screen';
  dim: DashboardDim;
}
let filterCache: KanbanFilterCache | null = null;

/** HH:MM（两位补零） */
function nowHM(): string {
  const d = new Date();
  const p = (n: number): string => (n < 10 ? '0' + n : String(n));
  return `${p(d.getHours())}:${p(d.getMinutes())}`;
}

/** 顶部日期行：M月D日 周X */
function dateText(): string {
  const d = new Date();
  const wd = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'][d.getDay()];
  return `${d.getMonth() + 1}月${d.getDate()}日 ${wd}`;
}

/** 下单日期分组标题：2026-10-15 → 10月15日（异常值原样回显） */
function groupLabel(date: string): string {
  const m = /^(\d{4})-(\d{1,2})-(\d{1,2})$/.exec(date || '');
  return m ? `${parseInt(m[2], 10)}月${parseInt(m[3], 10)}日` : date || '未填日期';
}

/**
 * 列下钻 link 解析（§四契约：extra.link 白名单 production|ticket|order，非法值忽略）：
 * 1. 显式 link（列定义顶层或 extra.link，兼容新旧两种下发形态）命中白名单 → 采用；
 * 2. 未配置时的开箱即用兜底：款号列（field_key=style_no / 标题=款号）视为 production——
 *    保证 SA2 模板配置补 link 之前，默认模板就能走「行 tap → 床次列表」下钻主链路；
 * 3. 其余一律返回空串（不可点）。
 */
function resolveLink(c: KanbanColumnDef): string {
  const raw = String(c.link || (c.extra && c.extra.link) || '');
  if ((COLUMN_LINK_WHITELIST as readonly string[]).indexOf(raw) >= 0) return raw;
  if (!raw && (c.field_key === 'style_no' || c.title === '款号')) return 'production';
  return '';
}

/**
 * 由款块上下文构造下钻 nav（每级预过滤）：
 * - production / ticket → 床次列表页（/productions 支持 style_id 过滤，款号兜底 keyword）。
 *   ticket 与 production 同落点：菲票挂在床次下，行粒度（款+色）无法直达单张菲票，
 *   由用户在床次列表 → 床次详情（菲票列表）→ 菲票（登记明细）继续逐级下钻；
 * - order → 看板内切「订单」页签（NAV_ORDER_TAB 特殊值，onCellTap 消费）。
 * 款 id 取自款块 key（后端 bkey=`${style_id}-${date}`，与客户端回落同构），解析失败用 0（仅款号过滤）。
 */
function buildDrillNav(link: string, blockKey: string, styleNo: string): string {
  const m = /^(\d+)-/.exec(blockKey || '');
  const styleId = m ? parseInt(m[1], 10) : 0;
  const no = String(styleNo || '').trim();
  if (!styleId && !no) return '';
  const qs = [
    styleId ? `styleId=${styleId}` : '',
    no ? `styleNo=${encodeURIComponent(no)}` : ''
  ].filter(Boolean).join('&');
  if (link === 'order') return NAV_ORDER_TAB;
  return `/packages/admin/productions/index?${qs}`;
}

/** 订单状态 → 展示（后端 status_display 缺失时前端兜底字典） */
function orderStatusVM(o: Order): { statusText: string; statusCls: string } {
  if (o.status_display) {
    const clsMap: Record<string, string> = {
      producing: 'ord-badge-producing', done: 'ord-badge-done', cancelled: 'ord-badge-cancelled'
    };
    return { statusText: o.status_display, statusCls: clsMap[o.status] || 'ord-badge-producing' };
  }
  const map: Record<string, { t: string; c: string }> = {
    producing: { t: '生产中', c: 'ord-badge-producing' },
    done: { t: '已完成', c: 'ord-badge-done' },
    cancelled: { t: '已取消', c: 'ord-badge-cancelled' }
  };
  const v = map[o.status] || map.producing;
  return { statusText: v.t, statusCls: v.c };
}

/** 交期灯（决策 2026-10-20：黄=距交期≤7天，红=逾期，绿=充裕，灰=未设交期） */
function orderLight(o: Order): { lightCls: string; lightText: string } {
  const dl = o.days_left;
  if (dl === null || dl === undefined) return { lightCls: 'lamp-gray', lightText: '未设交期' };
  if (dl < 0) return { lightCls: 'lamp-red', lightText: `逾期 ${Math.abs(dl)} 天` };
  if (dl <= 7) return { lightCls: 'lamp-yellow', lightText: `剩 ${dl} 天` };
  return { lightCls: 'lamp-green', lightText: `剩 ${dl} 天` };
}

/** Order → 页面视图模型（完成率环形用 conic-gradient 内联样式，中心白圆 + 百分比；
 *  统计字段 total_qty/progress 等后端可能缺省，全部兜底 0） */
function toOrderVM(o: Order): OrderVM {
  const progress = Math.max(0, Math.min(Math.round(Number(o.progress) || 0), 100));
  const st = orderStatusVM(o);
  const light = orderLight(o);
  return {
    id: o.id,
    orderNo: o.order_no || `#${o.id}`,
    statusText: st.statusText,
    statusCls: st.statusCls,
    customerName: o.customer_name || '',
    deliveryText: o.delivery_date ? o.delivery_date : '未设交期',
    lightCls: light.lightCls,
    lightText: light.lightText,
    progress,
    ringStyle: `background: conic-gradient(#FF7A00 ${progress}%, #EDEEF0 0deg);`,
    behind: o.status === 'producing' && progress < 80,
    totalQty: Number(o.total_qty) || 0
  };
}

Page({
  data: {
    loading: false,
    error: false,
    clock: '',
    dateText: '',
    // ---- 顶层页签：计划表 / 订单（§2.9 P1 订单页签）/ 大屏（P3B §4.1 车间大屏） ----
    boardTab: 'plan' as 'plan' | 'orders' | 'screen',
    orders: [] as OrderVM[],
    ordersLoading: false,
    ordersError: false,              // 接口未就绪/失败 → 「服务升级中」空态（不白屏）
    ordersLoaded: false,             // 惰性加载标记：首次切到订单页签才拉
    unlinkedBeds: { count: 0, qty: 0 } as UnlinkedBeds,
    // ---- 大屏页签（P3B：进行中床次进度卡 + 今日登记排行 TopN，无汇总统计区） ----
    screenRankTop: SCREEN_RANK_TOP,
    screenBeds: [] as BedProgressVM[],   // 进行中床次（BedProgressVM，共用组件渲染）
    screenRank: [] as DashboardResp['rank'], // 今日登记排行（dashboard range=today 截 TopN）
    screenLoading: false,
    screenLoaded: false,             // 惰性加载标记：首次切到大屏页签才拉
    screenError: false,              // 拉取失败 → 重试空态（不白屏）
    // ---- 计划表筛选（页面 data 常驻 + 模块缓存全局兜底，返回自动恢复） ----
    range: 'month' as DashboardRange, // 当前时间范围，默认本月
    rangeTabs: RANGE_TABS,
    rangeLabel: '本月',               // 当前范围中文标签（汇总卡/统计块文案用）
    customStart: '',                 // 自定义区间起（YYYY-MM-DD，选自定义 tab 时预填）
    customEnd: '',                   // 自定义区间止
    summary: null as DashboardResp['summary'] | null,
    // ---- 维度切换（2026-10-23）：style=模板计划表；其余走 /dashboard/dim 扁平表 ----
    dim: 'style' as DashboardDim,
    dimTabs: DIM_TABS,
    // GAP-8 关键工序聚焦（2026-11-04）：仅 dim=process 生效
    dimKeyOnly: false,
    dimCols: [] as DashboardDimColumn[],
    dimRows: [] as DashboardDimRow[],
    dimLoading: false,
    dimError: false,
    // ---- 模板表格（列驱动） ----
    tplName: '',                     // 当前模板名（内置回落显示「默认看板」）
    columns: [] as KanbanColumnDef[],
    headItems: [] as HeadItemVM[],
    hasGroups: false,                // 存在两级表头（group 列）
    headH: HEAD_H,                   // 两级表头：组头/叶子行行高
    headTallH: HEAD_H * 2,           // 无组列跨两行表头的格高
    headSingleH: HEAD_SINGLE_H,      // 单级表头行高
    groups: [] as GroupVM[],
    rowH: ROW_H,
    processes: [] as ProcVM[],
    rank: [] as DashboardResp['rank'],
    groupRank: [] as DashboardGroupRank[]
  },

  timer: null as ReturnType<typeof setInterval> | null,

  onLoad() {
    // 筛选恢复（全局缓存兜底）：navigateBack 场景 data 本就保留，这里覆盖重建场景
    if (filterCache) {
      this.setData({
        boardTab: filterCache.boardTab,
        range: filterCache.range,
        rangeLabel: filterCache.rangeLabel,
        customStart: filterCache.customStart,
        customEnd: filterCache.customEnd,
        dim: filterCache.dim
      });
    }
    this.setData({ clock: nowHM(), dateText: dateText() });
    this.timer = setInterval(() => {
      this.setData({ clock: nowHM() });
    }, 30 * 1000);
  },

  onUnload() {
    if (this.timer) {
      clearInterval(this.timer);
      this.timer = null;
    }
  },

  onShow() {
    if (!requireLogin()) return;
    this.rememberFilter();
    if (this.data.boardTab === 'screen') {
      // 停在大屏页签返回/重进：续拉大屏数据（计划表统计不拉，避免无用请求）
      this.loadScreen();
      return;
    }
    this.reload();
    // 订单页签惰性加载：返回/重进时若停在订单页签，续拉（已加载过则静默跳过）
    if (this.data.boardTab === 'orders' && !this.data.ordersLoaded && !this.data.ordersLoading) {
      this.loadOrders();
    }
  },

  /** 筛选状态写入全局缓存（页面 data 为第一真相源，缓存只作重建兜底） */
  rememberFilter() {
    filterCache = {
      range: this.data.range,
      rangeLabel: this.data.rangeLabel,
      customStart: this.data.customStart,
      customEnd: this.data.customEnd,
      boardTab: this.data.boardTab,
      dim: this.data.dim
    };
  },

  // ================= 顶层页签：计划表 / 订单 / 大屏 =================

  /** 切换顶层页签；订单/大屏页签首次进入才拉数据（失败→各自空态，不白屏） */
  onBoardTab(e: WechatMiniprogram.TouchEvent) {
    const key = String((e.currentTarget.dataset.key || 'plan'));
    if (key !== 'plan' && key !== 'orders' && key !== 'screen') return;
    if (key === this.data.boardTab) return;
    this.setData({ boardTab: key as KanbanFilterCache['boardTab'] });
    this.rememberFilter();
    if (key === 'orders' && !this.data.ordersLoaded && !this.data.ordersLoading) {
      this.loadOrders();
    }
    if (key === 'screen' && !this.data.screenLoaded && !this.data.screenLoading) {
      this.loadScreen();
    }
  },

  /** 订单列表 + 未挂单床次汇总（SA1 接口未就绪时 ordersError=true，显示「服务升级中」） */
  loadOrders() {
    this.setData({ ordersLoading: true, ordersError: false });
    const orders = fetchOrders({ page: 1, page_size: 100 })
      .then((res) => {
        this.setData({
          orders: (res.results || []).map(toOrderVM),
          ordersLoaded: true,
          ordersLoading: false,
          ordersError: false
        });
      })
      .catch(() => {
        // 后端 /orders 未上线（404/500 等）→ 空态占位，绝不允许白屏
        this.setData({ ordersLoading: false, ordersError: true, ordersLoaded: true });
      });
    // 未挂单床次汇总：床次 order FK 为空即未挂单（字段由 SA1 迁移下发，旧后端读不到 → 0）
    // 失败静默：汇总块显示 0，不影响订单卡片墙
    fetchProductions({ page: 1, page_size: 100 } as Parameters<typeof fetchProductions>[0])
      .then((res: Paginated<Production>) => {
        const rows = (res.results || []) as unknown as Array<Record<string, unknown>>;
        const unlinked = rows.filter((p) => !p.order && !p.order_id);
        this.setData({
          unlinkedBeds: {
            count: unlinked.length,
            qty: unlinked.reduce((s, p) => s + (Number(p.total_qty) || 0), 0)
          }
        });
      })
      .catch(() => undefined);
    return orders;
  },

  /** 订单页签「重新加载」（服务升级中空态按钮） */
  onRetryOrders() {
    if (this.data.ordersLoading) return;
    this.loadOrders();
  },

  /** 订单卡片 tap → 订单详情（SF1 的 orders 页；未上线时 fail 兜底 toast，不白屏不崩溃） */
  onOrderTap(e: WechatMiniprogram.TouchEvent) {
    const o = this.data.orders[Number(e.currentTarget.dataset.index)];
    if (!o) return;
    wx.navigateTo({
      url: `/packages/admin/order-detail/index?id=${o.id}`,
      fail: (r) => {
        // 例外教训：fail 回调读 errMsg，自定义容错必须留出口（详情页由 SF1 并行交付）
        console.warn('order detail navigate fail:', r && r.errMsg);
        wx.showToast({ title: '订单详情页尚未开通', icon: 'none' });
      }
    });
  },

  // ================= 大屏页签（P3B §4.1 车间大屏） =================

  /**
   * 大屏数据：进行中床次（/productions，未裁完且未锁定，进度口径已在
   * toBedProgressVM 内与工作台统一）+ 今日登记排行 TopN（dashboard range=today）。
   * 无汇总统计区（§4.1：砍掉与生产计划表重复的汇总区）。
   */
  loadScreen() {
    if (this.data.screenLoading) return Promise.resolve();
    this.setData({ screenLoading: true, screenError: false });
    return Promise.all([
      fetchProductions({ page: 1, page_size: 200 } as Parameters<typeof fetchProductions>[0]),
      fetchDashboard('today')
    ]).then(([prodRes, dash]) => {
      this.setData({
        screenBeds: (prodRes.results || [])
          .filter((p) => !p.is_cut_done && !p.is_locked)
          .map(toBedProgressVM),
        screenRank: (dash.rank || []).slice(0, SCREEN_RANK_TOP),
        screenLoading: false,
        screenLoaded: true,
        screenError: false
      });
    }).catch(() => {
      // 请求层已 toast；这里切重试空态，保留已加载数据避免闪白
      this.setData({ screenLoading: false, screenLoaded: true, screenError: true });
    });
  },

  /** 大屏「重新加载」（失败空态按钮） */
  onRetryScreen() {
    if (this.data.screenLoading) return;
    this.loadScreen();
  },

  /** 大屏床次卡 tap → 床次详情（复用 navigateTo，未就绪 fail 兜底 toast，不白屏不崩溃）；
   *  参数来自组件 bedtap 事件 detail: {id, index}（components/bed-progress 上抛） */
  onScreenBedTap(e: WechatMiniprogram.CustomEvent<{ id: number; index: number }>) {
    const detail = e.detail || { id: 0, index: -1 };
    const bed = this.data.screenBeds[Number(detail.index)] as BedProgressVM | undefined;
    if (!bed || !bed.id) return;
    wx.navigateTo({
      url: `/packages/admin/production-detail/index?id=${bed.id}`,
      fail: (r) => {
        console.warn('screen bed navigate fail:', r && r.errMsg);
        wx.showToast({ title: '页面未就绪', icon: 'none' });
      }
    });
  },

  // ================= 行 tap 下钻（§2.9 P0） =================

  /**
   * 计划表单元格 tap：列配了 extra.link（或款号列兜底）才带参跳转，非法值已忽略。
   * nav 为空 = 普通格，直接返回（不 toast 不打断浏览）。
   */
  onCellTap(e: WechatMiniprogram.TouchEvent) {
    const nav = String((e.currentTarget.dataset.nav || ''));
    if (!nav) return;
    if (nav === NAV_ORDER_TAB) {
      this.onBoardTab({
        currentTarget: { dataset: { key: 'orders' } }
      } as unknown as WechatMiniprogram.TouchEvent);
      return;
    }
    wx.navigateTo({
      url: nav,
      fail: (r) => {
        console.warn('drill navigate fail:', r && r.errMsg);
        wx.showToast({ title: '页面未就绪', icon: 'none' });
      }
    });
  },

  onPullDownRefresh() {
    // 大屏页签下拉：随计划表一起刷新大屏数据（其余页签维持原 reload）
    const jobs: Array<Promise<unknown>> = [this.reload()];
    if (this.data.boardTab === 'screen') jobs.push(this.loadScreen());
    Promise.all(jobs).then(() => wx.stopPullDownRefresh());
  },

  /** 切换时间范围 tab：重新拉取，汇总卡/柱图/占比/排行与计划表筛选随 range 刷新；
   *  自定义 tab 预填「本月 1 日 ~ 今天」并立即查询，用户可再改起止日 */
  onRangeTap(e: WechatMiniprogram.TouchEvent) {
    const key = (e.currentTarget.dataset.key || '') as DashboardRange;
    if (!key || key === this.data.range) return;
    const tab = RANGE_TABS.find((t) => t.key === key);
    const label = tab ? tab.label : '本月';
    if (key === 'custom') {
      const now = new Date();
      this.setData({
        range: key,
        rangeLabel: label,
        customStart: this.data.customStart || isoDate(new Date(now.getFullYear(), now.getMonth(), 1)),
        customEnd: this.data.customEnd || isoDate(now)
      });
      this.reload();
      return;
    }
    this.setData({ range: key, rangeLabel: label });
    this.reload();
  },

  /** 自定义起止日（picker mode=date）：改任一端即重查 */
  onCustomStart(e: WechatMiniprogram.CustomEvent<{ value: string }>) {
    this.setData({ customStart: String(e.detail.value || '') });
    this.reload();
  },

  onCustomEnd(e: WechatMiniprogram.CustomEvent<{ value: string }>) {
    this.setData({ customEnd: String(e.detail.value || '') });
    this.reload();
  },

  // ================= 维度切换（2026-10-23：订单/裁床/部门/组/人） =================

  /** 切维度：款式=模板计划表（已渲染，直接切回）；其余拉 /dashboard/dim 聚合表 */
  onDimTap(e: WechatMiniprogram.TouchEvent) {
    const key = String(e.currentTarget.dataset.key || 'style') as DashboardDim;
    if (!DIM_TABS.some((t) => t.key === key) || key === this.data.dim) return;
    this.setData({ dim: key, dimKeyOnly: false });
    this.rememberFilter();
    if (key !== 'style') this.loadDim();
  },

  /** 关键工序聚焦开关（仅 dim=process 显示；GAP-8，2026-11-04） */
  onDimKeyToggle() {
    if (this.data.dim !== 'process') return;
    this.setData({ dimKeyOnly: !this.data.dimKeyOnly });
    this.loadDim();
  },

  /** 维度聚合表（随 range/自定义区间刷新；失败切重试态，保留旧数据防闪白） */
  loadDim(): Promise<void> {
    if (this.data.dim === 'style') return Promise.resolve();
    this.setData({ dimLoading: true, dimError: false });
    return fetchDashboardDim(this.data.dim, this.data.range, {
      start: this.data.customStart,
      end: this.data.customEnd,
      keyOnly: this.data.dimKeyOnly
    })
      .then((res) => {
        this.setData({
          dimLoading: false,
          dimCols: res.columns || [],
          dimRows: res.rows || [],
          dimError: false
        });
      })
      .catch(() => this.setData({ dimLoading: false, dimError: true }));
  },

  /** 维度表「重新加载」（失败空态按钮） */
  onRetryDim() {
    if (this.data.dimLoading) return;
    this.loadDim();
  },

  /** 维度行下钻：订单→订单详情、裁床→床次详情（后端 nav 白名单，其余不可点） */
  onDimRowTap(e: WechatMiniprogram.TouchEvent) {
    const row = this.data.dimRows[Number(e.currentTarget.dataset.index)];
    if (!row || !row.nav) return;
    const url = row.nav === 'order'
      ? `/packages/admin/order-detail/index?id=${row.key}`
      : `/packages/admin/production-detail/index?id=${row.key}`;
    wx.navigateTo({
      url,
      fail: (r) => {
        console.warn('dim row navigate fail:', r && r.errMsg);
        wx.showToast({ title: '页面未就绪', icon: 'none' });
      }
    });
  },

  // ================= 模板表格：列驱动渲染 =================

  /** 表头视图模型：无 group 列 → 单行表头（headItems 为空，wxml 走 columns 直渲）；
   *  有 group 列 → 连续同名列归一组（组头跨列），无组列纵向跨两行 */
  buildHeadItems(columns: KanbanColumnDef[]): HeadItemVM[] {
    if (!columns.some((c) => c.group)) return [];
    const items: HeadItemVM[] = [];
    for (const c of columns) {
      const leaf = { id: c.id, title: c.title, width: c.width };
      const prev = items[items.length - 1];
      if (c.group && prev && !prev.tall && prev.group === c.group) {
        prev.leaves.push(leaf);
        prev.width += c.width;
      } else if (c.group) {
        items.push({ key: `g-${c.id}`, group: c.group, tall: false, width: c.width, leaves: [leaf] });
      } else {
        items.push({ key: `s-${c.id}`, group: c.title, tall: true, width: c.width, leaves: [leaf] });
      }
    }
    return items;
  },

  /** 款块 → 列段视图模型：按列定义计算每列的纵向合并（任意 merge 泛化点）。
   *  merge=block：整块一格（height=行数×ROW_H，内容垂直居中）；
   *  merge=value：块内连续同值合并成一格；
   *  其余：每行一格（height=ROW_H，床次等多行文案用 pre-line 折行） */
  buildBlock(blk: KanbanBlock, columns: KanbanColumnDef[]): BlockVM {
    // cells 已按列序对齐（后端保证）；防御性：长度不一致时按 col id 对齐
    const aligned = blk.rows.length > 0 &&
      blk.rows.every((r) => r.cells.length === columns.length);
    const matrix: string[][] = columns.map((c, j) =>
      blk.rows.map((r) => {
        if (aligned) return (r.cells[j] || {}).text || '';
        const cell = r.cells.find((x) => x.col === c.id);
        return cell ? cell.text : '';
      }));
    // 款块级下钻上下文：款号文本（款号列首行）+ 款块 key（含 style_id）→ 各 link 列共用
    const styleColIdx = columns.findIndex(
      (c) => c.field_key === 'style_no' || c.title === '款号'
    );
    const styleNo = styleColIdx >= 0 ? (matrix[styleColIdx][0] || '') : '';
    const links = columns.map(resolveLink);
    const segs: SegVM[] = columns.map((c, j) => {
      const texts = matrix[j];
      // 该列的下钻目标（production/ticket→床次列表；order→订单页签；空=不可点）
      const nav = links[j] ? buildDrillNav(links[j], blk.key, styleNo) : '';
      const cells: Array<{ text: string; height: number; nav: string }> = [];
      const merge = c.merge || 'none';
      if (merge === 'block') {
        cells.push({ text: texts[0] || '', height: Math.max(blk.rows.length, 1) * ROW_H, nav });
      } else if (merge === 'value') {
        let run = 1;
        for (let i = 0; i < texts.length; i++) {
          const same = i + 1 < texts.length && texts[i + 1] === texts[i] && texts[i] !== '';
          if (same) { run += 1; continue; }
          cells.push({ text: texts[i] || '', height: run * ROW_H, nav });
          run = 1;
        }
      } else {
        for (const t of texts) cells.push({ text: t || '', height: ROW_H, nav });
      }
      const cls = ['ta-' + (c.align || 'left')];
      if (cells.every((x) => x.text === (c.placeholder || '—'))) cls.push('hold');
      if (nav) cls.push('link');
      return {
        colId: c.id,
        width: c.width,
        cls: cls.join(' '),
        blockMerge: merge === 'block',
        cells
      };
    });
    return { key: blk.key, alt: !!blk.alt, segs };
  },

  /** 渲染接口响应 → 页面视图模型（width=0 的列先做自适应估宽再参与布局；
   *  列标题过显示别名字典：旧后端/存量模板的「订单数量」在显示端统一为「菲票数量」，
   *  公式字段 order_qty 语义不动；下钻 link 在 buildBlock 时按列解析） */
  applyRender(res: KanbanRenderResp) {
    const columns = computeWidths(res.columns || [], res.groups || []).map((c) => ({
      ...c,
      title: COLUMN_TITLE_ALIAS[c.title] || c.title
    }));
    const headItems = this.buildHeadItems(columns);
    const groups: GroupVM[] = (res.groups || []).map((g) => ({
      date: g.date,
      label: g.label || groupLabel(g.date),
      blocks: (g.blocks || []).map((b) => this.buildBlock(b, columns))
    }));
    this.setData({
      tplName: (res.template && res.template.name) || '默认看板',
      columns,
      headItems,
      hasGroups: headItems.length > 0,
      groups
    });
  },

  async reload() {
    if (this.data.loading) return;
    this.setData({ loading: true, error: false });
    try {
      // 统计块走 dashboard；表格走模板渲染（两接口互不阻塞）
      const [dash, renderRes] = await Promise.all([
        fetchDashboard(this.data.range, {
          start: this.data.customStart,
          end: this.data.customEnd
        }),
        fetchKanbanRender(this.data.range, {
          start: this.data.customStart,
          end: this.data.customEnd
        }).catch((e) => {
          console.warn('kanban render failed, fallback to dashboard plans', e);
          return null;
        })
      ]);
      const processes: ProcVM[] = dash.processes.map((p) => ({
        ...p,
        barWidth: `${Math.min(p.percent, 100)}%`
      }));
      this.setData({
        loading: false,
        summary: dash.summary,
        processes,
        rank: dash.rank || [],
        groupRank: dash.group_rank || []
      });
      // 非款式维度：range/自定义区间变化后同步刷新维度聚合表（失败由 loadDim 自吞）
      if (this.data.dim !== 'style') this.loadDim();
      if (renderRes) {
        this.applyRender(renderRes);
      } else {
        // 客户端回落：旧 dashboard plans 拼内置默认十列（绝不允许白屏）
        this.applyRender(fallbackRenderFromPlans(dash.plans || []));
      }
    } catch (e) {
      // 错误 toast 已由请求层弹出，这里只切错误态
      this.setData({ loading: false, error: true });
    }
  }
});
