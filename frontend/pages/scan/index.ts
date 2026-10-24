/**
 * 扫菲页（产品最高频路径）：
 * 进入自动调起扫码 → parseScan 解析菲票 → 工序选择（单工序免选直登）→ 件数录入 → createRecord(offline)。
 * 连续扫码（规格 §21.1）：登记完成后自动回到扫码态并再次调起相机。
 * 即时反馈（需求调研-续 §8.3 竞品最大差评点）：登记成功后并行查询本人今日累计
 *   （fetchEmployeeSalary，date_from=date_to=今天），顶部常驻条 + 追加 toast 显示
 *   「今日累计 X 件 ¥Y」；查询不 await、不阻塞 900ms 连续扫码节奏。
 *   协助模式（aid 他人）跳过金额显示仅提示成功；离线入队时请求层已 toast「已存本地待同步」。
 * 协助扫菲：assist 页选中员工后暂存 storage（switchTab 不携带参数），本页 onShow 读取进入协助模式。
 * 手输码兜底（规格 §2.7.2，P0）：码破损/扫码失败时按 床次号+扎号 查候选菲票（GET /tickets/lookup），
 *   选中走与扫码成功完全相同的登记流程（enterTicketFlow）；0 命中 404 → 「未找到菲票」提示。
 * 满扎徽标（规格 §2.7.1 P0）：工序 chip 显示「累计/扎数」（selector registered_qty/cap），
 *   后端未升级（字段缺失）时隐藏徽标不报错；cap=null 只显示累计数。
 * 离线显式暂存（规格 §2.7.3 P3）：idle 态可查看离线队列条目摘要并手动重发（复用 utils/offline-queue）。
 */
import { parseScan, createRecord, fetchRecordProcessSelector, lookupTickets } from '../../services/production';
import { fetchStyle } from '../../services/catalog';
import { joinCompany } from '../../services/auth';
import { fetchSettings } from '../../services/ops';
import { fetchEmployeeSalary } from '../../services/salary';
import { money, today } from '../../utils/format';
import { requireLogin, getUser } from '../../utils/auth';
import { hasPerm } from '../../utils/perm';
import { offlineCount, offlineItems, flushOfflineQueue } from '../../utils/offline-queue';
import { logger } from '../../utils/logger';

/** 协助目标员工在页面间的暂存键（switchTab 丢参，故走 storage） */
const ASSIST_KEY = 'yisao_assist_target';

/** 后端 ScanParseView 实际返回（services/types.ScanParseResult 的后端子集，以后端为准） */
interface ScanResp {
  ticket: {
    id: number;
    bed_no: string;
    bundle_no: string;
    color: string;
    size: string;
    qty: number;
  };
  style: { id: number; style_no?: string; name?: string } | null;
  production: {
    id: number;
    is_locked?: boolean;
    is_cut_done?: boolean;
    registered_qty?: number;
    process_count?: number;
  } | null;
}

interface ProcessOption {
  id: number;
  name: string;
  price: string;
  priceText: string;
  /** 未配置工价（痛点 #2 调研 §8.3）：chip 禁选 + 「未上价」角标 */
  unpriced: boolean;
  /** 满扎徽标（§2.7.1 P0）：null=后端未升级 → 隐藏徽标 */
  registeredQty: number | null;
  /** 扎数上限：null=只显示累计数 */
  capQty: number | null;
}

interface TicketView {
  id: number;
  styleId: number;
  styleNo: string;
  styleName: string;
  bedNo: string;
  bundleNo: string;
  color: string;
  size: string;
  qty: number;
  registeredQty: number | null;
  /** 所属床次 id（M6 扫码补打入口跳床次详情用；手输查找行无此信息时为 0） */
  prodId: number;
  /** 打印门槛（H4）：print.manage ∪ production.manage，控制「查看菲票/补打」显隐 */
  canPrint: boolean;
}

/** 手输查找候选行（视图层驼峰） */
interface LookupRowView {
  id: number;
  styleId: number;
  styleNo: string;
  styleName: string;
  bedNo: string;
  bundleNo: string;
  color: string;
  size: string;
  qty: number;
  isLocked: boolean;
}

/** 离线暂存列表行摘要 */
interface OfflineRowView {
  time: string;
  ticketId: number | string;
  qty: number | string;
}

Page({
  data: {
    /** idle 扫码态 / process 选工序 / qty 录件数 / submitting 提交中 */
    state: 'idle' as 'idle' | 'process' | 'qty' | 'submitting',
    hidePrice: false,
    offlineCount: 0,
    /** 协助模式：被代登记员工 */
    assistUserId: 0,
    assistName: '',
    ticket: null as TicketView | null,
    processes: [] as ProcessOption[],
    selectedId: 0,
    selectedName: '',
    qtyValue: '',
    /** 工序流转推导（2026-10-21 需求）：后端按款式工序序推导的当前应登记工序；
     *  命中时免选直达 + 件数锁定（fixedQty，不可修改） */
    currentProcess: null as { id: number; name: string; remaining: number } | null,
    fixedQty: false,
    /** 默认不弹键盘（2026-10-22 用户需求）：直接展示默认件数，点「修改数量」才展开键盘 */
    qtyEditing: false,
    /** 本人今日累计（null=尚未拉到）；协助模式下不显示 */
    todayQty: null as number | null,
    todayAmount: '',
    /** 手输码兜底弹层（§2.7.2） */
    lookupShow: false,
    lookupBed: '',
    lookupBundle: '',
    lookupLoading: false,
    lookupError: '',
    lookupRows: [] as LookupRowView[],
    /** 离线暂存列表弹层（§2.7.3 P3） */
    offlineShow: false,
    offlineRows: [] as OfflineRowView[],
    resending: false,
    /** 双击款号 → 款式信息弹层（style-craft：款式图/工艺说明/工艺图/视频点播） */
    styleSheet: false,
    styleCraft: null as Record<string, unknown> | null
  },

  /** 相机打开期间标记，防止重入 */
  _scanning: false,
  /** 本次驻留是否已开过相机：关相机（✕）返回会再触发 onShow，未置位会导致相机反复弹出、永远退不出去 */
  _cameraDone: false,

  onLoad(options: Record<string, string | undefined>) {
    // 兼容带参进入（如未来页面形态调整）：?mode=assist&userId=xxx&name=xxx
    if (options.userId) {
      wx.setStorageSync(ASSIST_KEY, { userId: Number(options.userId), name: options.name || '' });
    }
  },

  onShow() {
    if (!requireLogin()) return;
    const tabBar = (this as unknown as { getTabBar?: () => { setSelected: (n: number) => void } }).getTabBar?.();
    if (tabBar) tabBar.setSelected(2);

    const stash = (wx.getStorageSync(ASSIST_KEY) || null) as { userId: number; name: string } | null;
    this.setData({
      assistUserId: stash ? stash.userId : 0,
      assistName: stash ? stash.name : ''
    });
    this.loadSettings();
    this.setData({ offlineCount: offlineCount() });

    // 顶部常驻条初始化（本人模式；协助模式下不显示金额条）
    if (!this.data.assistUserId) {
      this.refreshToday(false);
    }

    if (this.data.state !== 'idle') {
      // 离开本页时流程未走完（工序选择/件数确认还挂着）——回页不留上次的遗留窗口
      this.resetToIdle();
    }
    if (this.data.state === 'idle' && !this._cameraDone) {
      this.autoScan();
    }
  },

  onUnload() {
    this._scanning = false;
    this._cameraDone = false;
  },

  /** 离开本页（switchTab/navigateTo 均会触发）：重置相机标记，下次进入恢复自动扫码 */
  onHide() {
    this._cameraDone = false;
  },

  // ============ 扫码 ============

  /** 自动调起扫码（onShow / 登记完成后的连续扫码共用） */
  autoScan() {
    if (this._scanning) return;
    this._scanning = true;
    wx.scanCode({
      success: (res) => {
        this._scanning = false;
        this._cameraDone = true;
        this.handleCode(res.result || '');
      },
      fail: (err) => {
        this._scanning = false;
        this._cameraDone = true;
        const msg = (err && err.errMsg) || '';
        if (msg.indexOf('cancel') >= 0) {
          // 用户点 ✕ 关闭相机：本人模式直接回首页（关相机=离开）；协助模式留在本页以便「退出协助」
          if (!this.data.assistUserId) {
            wx.switchTab({ url: '/pages/index/index' });
          }
          return;
        }
        // 设备不支持/权限等其他失败：停留扫码态，保留手动按钮
        wx.showToast({ title: '扫码不可用，可点下方按钮重试', icon: 'none' });
      }
    });
  },

  onTapScan() {
    this.autoScan();
  },

  /** 相册识码（调研 §8.3 痛点 #6）：二维码破损/光线差/照片打印件时从相册选图补扫。
   *  wx.scanCode sourceType=album 原生支持，result 与相机扫码同构，复用 handleCode 管线 */
  onTapScanAlbum() {
    if (this._scanning) return;
    this._scanning = true;
    wx.scanCode({
      onlyFromCamera: false,
      sourceType: ['album'],
      success: (res) => {
        this._scanning = false;
        this._cameraDone = true;
        this.handleCode(res.result || '');
      },
      fail: (err) => {
        this._scanning = false;
        const msg = (err && err.errMsg) || '';
        if (msg.indexOf('cancel') < 0) {
          wx.showToast({ title: '未识别到菲票二维码，请对准二维码拍摄', icon: 'none' });
        }
        // 取消选择：静默停留扫码态
      }
    });
  },

  // ============ 解析 ============

  async handleCode(code: string) {
    const qr = (code || '').trim();
    if (!qr) return;
    // 公司二维码（JOIN:<company_no>，约定见 packages/admin/company-qr）：一键申请加入
    if (qr.indexOf('JOIN:') === 0) {
      const no = qr.slice(5).trim();
      if (/^\d{1,8}$/.test(no)) {
        this.handleJoinCode(no);
      } else {
        wx.showToast({ title: '公司二维码编号异常', icon: 'none' });
      }
      return;
    }
    wx.showLoading({ title: '解析中', mask: true });
    try {
      const res = await parseScan(qr) as unknown as ScanResp;
      const prod = res.production;
      if (prod && prod.is_locked) {
        wx.hideLoading();
        wx.showToast({ title: '床次已锁定，禁止登记', icon: 'none' });
        return;
      }
      // 2026-10-18 拍板：「裁完」=裁剪完成标记，不再拦截登记；
      // 超扎数/跳序/工序不在配置等业务拒绝由后端 400 返回，请求层 toast 具体原因
      const t = res.ticket;
      const ticket: TicketView = {
        id: t.id,
        styleId: (res.style && res.style.id) || 0,
        styleNo: (res.style && res.style.style_no) || '',
        styleName: (res.style && res.style.name) || '',
        bedNo: t.bed_no,
        bundleNo: t.bundle_no,
        color: t.color,
        size: t.size,
        qty: t.qty,
        registeredQty: prod && typeof prod.registered_qty === 'number' ? prod.registered_qty : null,
        prodId: prod ? prod.id : 0,
        canPrint: hasPerm('print.manage') || hasPerm('production.manage')
      };
      await this.enterTicketFlow(ticket);
    } catch (e) {
      wx.hideLoading();
      // 解析失败（格式/跨公司/菲票不存在）：请求层已 toast，保持扫码态可重扫
    }
  },

  /**
   * 扫码 / 手输查找共用的「菲票进入登记流程」：
   * 有工序流转推导（款式配置了工序序）→ 免选直达当前工序 + 件数锁定；
   * 否则退回旧交互（工价拦截 → 单工序免选 / 多工序手选）。
   * 调用方负责先 showLoading（本方法负责 hideLoading）；请求层错误已 toast，此处静默。
   */
  async enterTicketFlow(ticket: TicketView) {
    try {
      const processes = await this.loadProcesses(ticket.id);
      wx.hideLoading();
      const cur = this.data.currentProcess;
      if (cur) {
        if (processes.every((p) => p.unpriced)) {
          wx.showToast({ title: '该菲票工序尚未配置工价，请联系管理员', icon: 'none', duration: 2200 });
          return;
        }
        const hit = processes.find((p) => p.id === cur.id);
        if (hit && !hit.unpriced) {
          // 流转直达：件数=后端推导的应登余量，锁定不可修改
          this.setData({
            ticket,
            processes,
            selectedId: hit.id,
            selectedName: hit.name,
            qtyValue: cur.remaining > 0 ? String(cur.remaining) : '',
            fixedQty: true,
            state: 'qty'
          });
          return;
        }
        // 推导工序不在可选集/未上价：提示原因后仍进手选，不把工人锁死
        wx.showToast({
          title: hit
            ? `工序【${hit.name}】未上价，请选择其他工序`
            : `当前应登记工序【${cur.name}】不在你的可选工序中，请手动选择`,
          icon: 'none',
          duration: 2600
        });
      }
      if (!processes.length) {
        wx.showToast({ title: '暂无可选工序，请先在「工序」页开启固定工序', icon: 'none' });
        return;
      }
      if (processes.every((p) => p.unpriced)) {
        // 工价拦截第一关（调研 §8.3 痛点 #2）：全部工序未上价，不进流程
        wx.showToast({ title: '该菲票工序尚未配置工价，请联系管理员', icon: 'none', duration: 2200 });
        return;
      }
      if (processes.length === 1) {
        if (processes[0].unpriced) {
          wx.showToast({ title: `工序【${processes[0].name}】未上价，请联系管理员`, icon: 'none', duration: 2200 });
          return;
        }
        // 单工序免选（提效关键，规格 §9.1）：直接进件数确认，件数缺省=菲票扎数
        this.setData({
          ticket,
          processes,
          selectedId: processes[0].id,
          selectedName: processes[0].name,
          qtyValue: ticket.qty > 0 ? String(ticket.qty) : '',
          fixedQty: false,
          qtyEditing: false,
          state: 'qty'
        });
      } else {
        this.setData({ ticket, processes, selectedId: 0, selectedName: '', qtyValue: '', fixedQty: false, state: 'process' });
      }
      wx.nextTick(() => this.syncKeypad());
    } catch (e) {
      wx.hideLoading();
    }
  },

  /** 工序选择器（固定 ∪ 指定）：传 ticket_id 让单价按该床次订单工序解析，并带出
   *  工序流转推导的 current_process（无款式序配置/全部完成 → null）。兼容 {results,single} 形态。
   *  registered_qty/cap：满扎徽标（§2.7.1 P0）；后端未升级时字段缺失 → null → 徽标隐藏 */
  async loadProcesses(ticketId?: number): Promise<ProcessOption[]> {
    const hidePrice = this.data.hidePrice;
    try {
      const res = await fetchRecordProcessSelector(ticketId ? { ticket_id: ticketId } : undefined) as unknown as
        | Array<{ id: number; name: string; price: string; registered_qty?: number | null; cap?: number | null }>
        | { results?: Array<{ id: number; name: string; price: string; registered_qty?: number | null; cap?: number | null }>;
            current_process?: { id: number; name: string; remaining: number } | null };
      const list = Array.isArray(res) ? res : (res.results || []);
      const current = (!Array.isArray(res) && res.current_process) || null;
      this.setData({
        currentProcess: current,
        fixedQty: false
      });
      return list.map((p) => {
        const unpriced = Number(p.price) <= 0;
        return {
          id: p.id,
          name: p.name,
          price: p.price,
          priceText: hidePrice ? '' : money(p.price),
          unpriced,
          registeredQty: typeof p.registered_qty === 'number' ? p.registered_qty : null,
          capQty: typeof p.cap === 'number' ? p.cap : null
        };
      });
    } catch (e) {
      this.setData({ currentProcess: null, fixedQty: false });
      return [];
    }
  },

  /** 扫码加入公司 */
  async handleJoinCode(companyNo: string) {
    const res = await wx.showModal({
      title: '加入公司',
      content: `识别到公司编号 ${companyNo}，确定申请加入？`,
      confirmText: '申请加入'
    });
    if (!res.confirm) return;
    try {
      const r = await joinCompany(companyNo);
      wx.showToast({ title: (r && r.message) || '申请已提交，等待审核', icon: 'none', duration: 2400 });
    } catch (e) {
      // 已是成员/公司不存在等由请求层 toast 具体原因
    }
  },

  async loadSettings() {
    try {
      const s = await fetchSettings();
      this.setData({ hidePrice: s.hide_scan_price === '1' });
    } catch (e) {
      // 设置拉取失败不影响主流程：默认展示单价
    }
  },

  // ============ 工序选择 ============

  /** 件数确认页切回工序选择（自动推导不符/工人做其他工序时的出口；再选后回件数态） */
  onSwitchProcess() {
    this.setData({ state: 'process', fixedQty: false, qtyValue: '', qtyEditing: false });
  },

  onChooseProcess(e: WechatMiniprogram.TouchEvent) {
    const id = Number(e.currentTarget.dataset.id);
    const proc = this.data.processes.find((p) => p.id === id);
    if (!proc || this.data.state === 'submitting') return;
    if (proc.unpriced) {
      // 工价拦截第一关：未上价工序 chip 禁选（后端报工仍兜底拦截）
      wx.showToast({ title: `工序【${proc.name}】尚未配置工价，请联系管理员`, icon: 'none' });
      return;
    }
    const ticket = this.data.ticket;
    this.setData({
      selectedId: id,
      selectedName: proc.name,
      qtyValue: ticket && ticket.qty > 0 ? String(ticket.qty) : '',
      fixedQty: false,
      qtyEditing: false,
      state: 'qty'
    });
  },

  /** 「修改数量」：展开数字键盘（默认不弹，2026-10-22 用户需求） */
  onEditQty() {
    this.setData({ qtyEditing: true });
    wx.nextTick(() => this.syncKeypad());
  },

  // ============ 件数录入与提交 ============

  onQtyInput(e: WechatMiniprogram.CustomEvent<{ value: string }>) {
    this.setData({ qtyValue: e.detail.value });
  },

  onQtyConfirm() {
    this.submit();
  },

  onTapSubmit() {
    this.submit();
  },

  async submit() {
    if (this.data.state === 'submitting') return;
    const qty = Number(this.data.qtyValue);
    if (!qty || qty <= 0) {
      wx.showToast({ title: '请输入登记件数', icon: 'none' });
      return;
    }
    if (!this.data.selectedId || !this.data.ticket) {
      wx.showToast({ title: '请先选择工序', icon: 'none' });
      return;
    }
    this.setData({ state: 'submitting' });
    const assist = !!this.data.assistUserId;
    try {
      await createRecord({
        ticket_id: this.data.ticket.id,
        process_id: this.data.selectedId,
        user_id: this.data.assistUserId || undefined,
        qty
      }, { offline: true });
      // 成功 / 弱网入离线队列均视为完成（后者请求层已 toast「已存入本地待同步」）
      wx.showToast({ title: assist ? '协助登记成功' : '登记成功', icon: 'success' });
      logger.info(assist ? '协助登记' : '扫菲登记',
        `ticket=${this.data.ticket.id} process=${this.data.selectedId} qty=${qty}`);
      if (!assist) {
        // 即时反馈（调研 §8.3）：并行查今日累计，不阻塞连续扫码
        this.refreshToday(true);
      }
    } catch (e) {
      // 业务拒绝（重复登记/锁定/裁完）：请求层已 toast 具体原因；静默回扫码态
      // 弱网入离线队列（ApiError.status===0）：请求层已 toast「已存入本地待同步」
    }
    this.resetToIdle();
    // 连续扫码（规格 §21.1）：登记完成后自动回到扫码
    setTimeout(() => {
      if (this.data.state === 'idle') this.autoScan();
    }, 900);
  },

  resetToIdle() {
    this.setData({
      state: 'idle',
      ticket: null,
      processes: [],
      selectedId: 0,
      selectedName: '',
      qtyValue: '',
      currentProcess: null,
      fixedQty: false,
      qtyEditing: false,
      styleSheet: false,
      styleCraft: null,
      offlineCount: offlineCount()
    });
  },

  // ============ 查看菲票 / 补打入口（M6，2026-10-24） ============

  /** 解析成功且有打印权限（print.manage ∪ production.manage）时展示：
   *  跳床次详情页，在菲票列表「查看菲票」回看票面并补打 */
  onGotoTicketPrint() {
    const t = this.data.ticket;
    if (!t || !t.prodId) return;
    wx.navigateTo({
      url: `/packages/admin/production-detail/index?id=${t.prodId}`,
      fail: () => wx.showToast({ title: '页面未就绪', icon: 'none' })
    });
  },

  // ============ 双击款号看款式信息（2026-10-21 需求） ============

  /** 350ms 内两次点按款号 → 拉款式详情弹层（款式图直显、产品视频点击即播） */
  onTicketStyleTap() {
    const now = Date.now();
    const self = this as unknown as { _lastStyleTap?: number };
    const last = self._lastStyleTap || 0;
    self._lastStyleTap = now;
    if (now - last > 350) return;
    const t = this.data.ticket;
    if (!t || !t.styleId) {
      wx.showToast({ title: '该菲票未关联款式', icon: 'none' });
      return;
    }
    fetchStyle(t.styleId)
      .then((s) => {
        const raw = s as unknown as Record<string, unknown>;
        this.setData({
          styleSheet: true,
          styleCraft: {
            style_no: raw.style_no || t.styleNo,
            name: raw.name || t.styleName,
            image: raw.image || '',
            craft_note: raw.craft_note || '',
            craft_images: Array.isArray(raw.craft_images) ? raw.craft_images : [],
            video: raw.video || ''
          }
        });
      })
      .catch(() => wx.showToast({ title: '款式信息加载失败', icon: 'none' }));
  },

  onCloseStyleSheet() {
    this.setData({ styleSheet: false });
  },

  /**
   * 本人今日累计（顶部常驻条 + 登记成功后追加 toast）。
   * afterRegister=true 时拉到后追加 toast「今日累计 X 件 ¥Y」；
   * 仅在扫码态时弹（避免盖掉流程中的其他提示），不 await、不阻塞回扫码。
   */
  refreshToday(afterRegister: boolean) {
    const uid = (getUser() || { id: 0 }).id;
    if (!uid) return;
    const day = today();
    fetchEmployeeSalary(uid, { date_from: day, date_to: day })
      .then((res) => {
        const qty = (res.summary && res.summary.qty) || 0;
        const amount = (res.summary && res.summary.amount) || '0.00';
        this.setData({ todayQty: qty, todayAmount: money(amount) });
        if (afterRegister && this.data.state === 'idle' && !this.data.assistUserId) {
          // 系统设置「隐藏扫菲单价」时只报件数
          const tail = this.data.hidePrice ? '' : ` ¥${this.data.todayAmount}`;
          wx.showToast({ title: `今日累计 ${qty} 件${tail}`, icon: 'none', duration: 1400 });
        }
      })
      .catch(() => {
        // 拉取失败不打扰扫码主流程：常驻条保持上次值
      });
  },

  /** 受控回显数字键盘显示值 */
  syncKeypad() {
    const keypad = this.selectComponent('#qty-keypad') as WechatMiniprogram.Component.TrivialInstance | null;
    if (keypad && typeof (keypad as unknown as { setValue?: (v: string) => void }).setValue === 'function') {
      (keypad as unknown as { setValue: (v: string) => void }).setValue(this.data.qtyValue);
    }
  },

  // ============ 协助模式 ============

  onSwitchEmployee() {
    wx.navigateTo({ url: '/packages/admin/assist/index' });
  },

  onExitAssist() {
    wx.removeStorageSync(ASSIST_KEY);
    this.setData({ assistUserId: 0, assistName: '' });
    wx.showToast({ title: '已退出协助模式', icon: 'none' });
    // 退出协助后恢复本人今日累计条
    this.refreshToday(false);
  },

  // ============ 手输码兜底（规格 §2.7.2，P0） ============

  /** 遮罩/弹层占位：catchtap 必须绑定真实方法才能拦住冒泡（catchtap="" 不拦） */
  noop() {
    // intentionally empty
  },

  onTapLookupOpen() {
    this.setData({ lookupShow: true, lookupBed: '', lookupBundle: '', lookupError: '', lookupRows: [] });
  },

  onLookupClose() {
    this.setData({ lookupShow: false });
  },

  /** 点遮罩关闭（sheet 自身 catchtap=noop 防误触冒泡） */
  onLookupMaskTap() {
    this.setData({ lookupShow: false });
  },

  onLookupBedInput(e: WechatMiniprogram.CustomEvent<{ value: string }>) {
    this.setData({ lookupBed: e.detail.value });
  },

  onLookupBundleInput(e: WechatMiniprogram.CustomEvent<{ value: string }>) {
    this.setData({ lookupBundle: e.detail.value });
  },

  /** 床次号+扎号 → /tickets/lookup 候选列表；0 命中 404 → 「未找到菲票」提示（接口未就绪同样走此分支，不白屏） */
  async onLookupSearch() {
    if (this.data.lookupLoading) return;
    const bed = (this.data.lookupBed || '').trim();
    const bundle = (this.data.lookupBundle || '').trim();
    if (!bed || !bundle) {
      wx.showToast({ title: '请输入床次号和扎号', icon: 'none' });
      return;
    }
    this.setData({ lookupLoading: true, lookupError: '' });
    try {
      const rows = await lookupTickets(bed, bundle);
      const view: LookupRowView[] = rows.map((r) => ({
        id: r.id,
        styleId: (r as unknown as { style_id?: number }).style_id || 0,
        styleNo: r.style_no || '',
        styleName: r.style_name || '',
        bedNo: r.bed_no,
        bundleNo: r.bundle_no,
        color: r.color || '',
        size: r.size || '',
        qty: r.qty,
        isLocked: !!r.is_locked || r.status === 'locked'  // SA3 按床次派生 status，与 is_locked 等价
      }));
      this.setData({
        lookupLoading: false,
        lookupRows: view,
        lookupError: view.length ? '' : '未找到菲票，请核对床次号和扎号'
      });
    } catch (e) {
      const status = (e as { status?: number }).status || 0;
      const msg = status === 404
        ? '未找到菲票，请核对床次号和扎号'
        : status === 0
          ? '网络异常，请检查网络'
          : (e as Error).message || '查询失败，请稍后重试';
      this.setData({ lookupLoading: false, lookupError: msg, lookupRows: [] });
    }
  },

  /** 选中候选票 → 关弹层 → 走与扫码成功等价的登记流程 */
  async onPickLookupRow(e: WechatMiniprogram.TouchEvent) {
    const id = Number(e.currentTarget.dataset.id);
    const row = this.data.lookupRows.find((r) => r.id === id);
    if (!row || this.data.lookupLoading) return;
    if (row.isLocked) {
      wx.showToast({ title: '床次已锁定，禁止登记', icon: 'none' });
      return;
    }
    this.setData({ lookupShow: false });
    const ticket: TicketView = {
      id: row.id,
      styleId: row.styleId,
      styleNo: row.styleNo,
      styleName: row.styleName,
      bedNo: row.bedNo,
      bundleNo: row.bundleNo,
      color: row.color,
      size: row.size,
      qty: row.qty,
      registeredQty: null,
      prodId: 0, // 手输候选行不带床次 id：不显示补打入口
      canPrint: hasPerm('print.manage') || hasPerm('production.manage')
    };
    wx.showLoading({ title: '解析中', mask: true });
    await this.enterTicketFlow(ticket);
  },

  // ============ 离线显式暂存（规格 §2.7.3，P3 增强） ============

  onTapOfflineOpen() {
    this.setData({ offlineShow: true, offlineRows: this.buildOfflineRows() });
  },

  onOfflineClose() {
    this.setData({ offlineShow: false });
  },

  onOfflineMaskTap() {
    this.setData({ offlineShow: false });
  },

  /** 手动重发：复用 flushOfflineQueue（逐条补发、成功移除），2s 后回读队列刷新列表 */
  onOfflineResend() {
    if (this.data.resending) return;
    this.setData({ resending: true });
    flushOfflineQueue();
    setTimeout(() => {
      const remain = offlineCount();
      this.setData({
        resending: false,
        offlineCount: remain,
        offlineRows: this.buildOfflineRows()
      });
      wx.showToast({
        title: remain ? `重发完成，剩余 ${remain} 条待同步` : '重发完成，暂存已清空',
        icon: 'none',
        duration: 1800
      });
    }, 2000);
  },

  buildOfflineRows(): OfflineRowView[] {
    return offlineItems().map((it) => {
      const d = new Date(it.ts);
      const pad = (n: number) => (n < 10 ? `0${n}` : String(n));
      const data = (it.data || {}) as { ticket_id?: number; qty?: number };
      return {
        time: `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`,
        ticketId: data.ticket_id !== undefined ? data.ticket_id : '-',
        qty: data.qty !== undefined ? data.qty : '-'
      };
    });
  },

  /** 离开扫菲页：tab 页无法 navigateBack，用 switchTab 回首页（相机 ✕ 只关摄像头） */
  onBackHome() {
    wx.switchTab({ url: '/pages/index/index' });
  }
});
