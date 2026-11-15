/**
 * 全流程生产：路线实例驱动的节点、质检、裁片、包装、入库与产前工作台。
 * 不使用本地模拟业务数据；空列表只代表当前公司/路线的真实接口结果。
 */
import { requireLogin } from '../../../utils/auth';
import { ApiError } from '../../../utils/request';
import { hasPerm } from '../../../utils/perm';
import { fetchEmployees } from '../../../services/auth';
import { fetchOrder, fetchOrders } from '../../../services/orders';
import { fetchProduction, fetchProductions } from '../../../services/production';
import { fetchTickets } from '../../../services/catalog';
import { Employee, Order, Production, Paginated, Ticket } from '../../../services/types';
import {
  CuttingHandover, FinishedInbound, FinishedInventoryRow, FlowDashboard, OperationsDashboard, PackingTask,
  PreproductionStatus, ProductionRoute, ProductionStage, QualityInspection, QuantityLedger,
  RouteBalanceRow, RouteNode, StageAction, TechPack,
  addBriefingTarget, approveInbound, approvePackingTask, cancelHandover, confirmInspection,
  createHandover, createInbound, createInspection, createPackingTask, createReviewIssue,
  createRequestId, createSampleTask, createTechPack, decideTechPack, fetchFinishedInventory, fetchHandovers,
  fetchInbounds, fetchInspections, fetchOperationsDashboard, fetchPackingTasks, fetchRouteDashboard,
  fetchPackingTask, fetchInbound,
  fetchPreproductionStatus, fetchRouteBalances, fetchRouteTimeline, fetchRoutes, fetchTechPacks,
  instantiateRoute, postStageAction, recordSampleResult, rectifyReviewIssue, reverseLedger,
  reviewReviewIssue, reverseInbound, reversePackingTask, saveRouteTemplate, saveStage,
  setStageStatus, setRouteTemplateStatus, copyRouteTemplate,
  signBriefingTarget, signHandover, updateTechPack, fetchStages
} from '../../../services/full-production';

interface TicketOption {
  id: number;
  label: string;
  bed_no: string;
  bundle_no: string;
  color: string;
  size: string;
  style_no: string;
}

interface BalanceView extends RouteBalanceRow {
  stage_name: string;
  bed_no: string;
  style_no: string;
}

interface QualityView extends QualityInspection {
  color: string;
  size: string;
  style_no: string;
}

interface HandoverView extends CuttingHandover {
  color: string;
  size: string;
  style_no: string;
  can_sign: boolean;
}

function shouldRetrySameIntent(error: unknown): boolean {
  return !(error instanceof ApiError) || error.status === 0 || error.status >= 500;
}

async function fetchAllPages<T>(fetchPage: (page: number) => Promise<Paginated<T>>): Promise<T[]> {
  const result: T[] = [];
  let page = 1;
  let hasNext = true;
  while (hasNext) {
    const response = await fetchPage(page);
    result.push(...(response.results || []));
    hasNext = !!response.next;
    page += 1;
  }
  return result;
}

function isDocumentManagedLedger(source: string): boolean {
  return ['quality_inspection:', 'cutting_handover:', 'finished_goods.', 'shipment:']
    .some((prefix) => source.indexOf(prefix) === 0);
}

interface PackingBoxEditor {
  boxNo: string;
  rows: EditableLine[];
}

interface EditableLine {
  ticketIndex?: number;
  optionIndex?: number;
  quantity: string;
}

interface MeasurementEditorRow {
  item: string;
  point: string;
  size: string;
  unit: string;
  value: string;
  tolerancePlus: string;
  toleranceMinus: string;
}

const STAGE_TYPES = [
  { key: 'cutting', label: '裁剪' }, { key: 'sewing', label: '车缝' },
  { key: 'semi_inspection', label: '半成品检验' }, { key: 'special_machine', label: '专机' },
  { key: 'ironing', label: '整烫' }, { key: 'final_inspection', label: '成品检验' },
  { key: 'packing', label: '包装' }, { key: 'finished_inbound', label: '成品入库' },
  { key: 'other', label: '其他' }
];

interface RouteNodeEditorRow {
  stageIndex: number;
  required: boolean;
  allowParallel: boolean;
  allowSkip: boolean;
}

const SAMPLE_RESULTS = [
  { key: 'passed' as const, label: '合格' },
  { key: 'failed' as const, label: '不合格' },
  { key: 'rework' as const, label: '返工' }
];

function emptyMeasurement(): MeasurementEditorRow {
  return { item: '', point: '', size: '', unit: 'cm', value: '', tolerancePlus: '0', toleranceMinus: '0' };
}

function decimalText(value: string): boolean {
  return /^-?(?:\d+(?:\.\d+)?|\.\d+)$/.test(value.trim());
}

interface InboundOption {
  boxId: number;
  ticketId: number;
  label: string;
  quantity: number;
}

type TabKey = 'balance' | 'dashboard' | 'report' | 'quality' | 'cutting' | 'packing' | 'inventory' | 'preproduction';

const TABS: Array<{ key: TabKey; label: string }> = [
  { key: 'balance', label: '节点余额' },
  { key: 'dashboard', label: '流程看板' },
  { key: 'report', label: '节点报工' },
  { key: 'quality', label: '质量检验' },
  { key: 'cutting', label: '裁片签收' },
  { key: 'packing', label: '包装入库' },
  { key: 'inventory', label: '成品库存' },
  { key: 'preproduction', label: '产前状态' }
];

export type ProductionEntry = 'all' | 'preproduction' | 'onsite' | 'quality' | 'packing' | 'operations';

const ENTRY_CONFIG: Record<ProductionEntry, { title: string; tabs: TabKey[] }> = {
  all: { title: '全流程生产', tabs: TABS.map((item) => item.key) },
  preproduction: { title: '产前工作台', tabs: ['preproduction'] },
  onsite: { title: '现场报工工作台', tabs: ['report', 'balance', 'dashboard'] },
  quality: { title: '质量工作台', tabs: ['quality'] },
  packing: { title: '包装入库工作台', tabs: ['packing', 'inventory'] },
  operations: { title: '生产运营看板', tabs: [] }
};

const TAB_PERMISSIONS: Record<TabKey, string> = {
  balance: 'manufacturing.view',
  dashboard: 'manufacturing.view',
  report: 'manufacturing.view',
  quality: 'quality.view',
  cutting: 'cutting.view',
  packing: 'finished_goods.view',
  inventory: 'finished_goods.view',
  preproduction: 'preproduction.view'
};

function isProductionEntry(value: string): value is ProductionEntry {
  return Object.prototype.hasOwnProperty.call(ENTRY_CONFIG, value);
}

const ACTIONS: Array<{ key: StageAction; label: string }> = [
  { key: 'receive', label: '首节点接收' },
  { key: 'complete', label: '完工合格' },
  { key: 'transfer', label: '向后转出' },
  { key: 'return', label: '返修退回' },
  { key: 'scrap', label: '报废' }
];

const INSPECTION_TYPES = [
  { key: 'semi_inspection', label: '半检' },
  { key: 'final_inspection', label: '尾查' },
  { key: 'reinspection', label: '复检' }
];

const EVENT_NAMES: Record<string, string> = {
  received: '接收', qualified: '合格', rework: '返修入站', scrapped: '报废',
  transferred: '转出', reversal: '冲销'
};

// 幂等号只跟随一次用户意图：接口失败不换新，明确成功后立即轮换。
const intentIds = { report: '', handover: '', packing: '', inbound: '' };
const approvalIds: Record<string, string> = {};
let loadSequence = 0;

function positiveInt(value: string): number | null {
  const number = Number(value);
  return Number.isInteger(number) && number > 0 ? number : null;
}

function nonNegativeInt(value: string): number | null {
  const number = Number(value);
  return Number.isInteger(number) && number >= 0 ? number : null;
}

function toast(title: string): void {
  wx.showToast({ title, icon: 'none' });
}

export function registerFullProductionPage(fixedEntry: ProductionEntry = 'all'): void {
Page({
  data: {
    entry: 'all' as ProductionEntry,
    pageTitle: ENTRY_CONFIG.all.title,
    showRouteSetup: true,
    entryPermissionError: '',
    tabs: TABS,
    activeTab: 'balance' as TabKey,
    actions: ACTIONS,
    inspectionTypes: INSPECTION_TYPES,
    loading: false,
    listLoading: false,
    loadError: '',
    routes: [] as ProductionRoute[],
    routeLabels: [] as string[],
    routeIndex: 0,
    route: null as ProductionRoute | null,
    nodes: [] as RouteNode[],
    nodeLabels: [] as string[],
    tickets: [] as TicketOption[],
    ticketLabels: [] as string[],
    styleNo: '—',
    bedNo: '—',
    orderNo: '—',
    orderId: 0,
    canMfgView: false,
    canMfgManage: false,
    canReverse: false,
    canQualityView: false,
    canQualityManage: false,
    canCuttingView: false,
    canCuttingManage: false,
    canHandoverCreate: false,
    canFinishedView: false,
    canFinishedManage: false,
    canPreView: false,
    canPreManage: false,
    canPreApprove: false,

    balances: [] as BalanceView[],
    dashboard: null as FlowDashboard | null,
    filteredBalances: [] as BalanceView[],
    balanceTicketLabels: ['全部菲票'] as string[],
    balanceTicketIndex: 0,

    reportTicketIndex: 0,
    reportNodeIndex: 0,
    actionIndex: 0,
    targetNodeLabels: ['请选择目标节点'] as string[],
    targetNodes: [] as RouteNode[],
    targetNodeIndex: 0,
    reportQuantity: '',
    reportReason: '',
    reportSaving: false,
    ledger: [] as Array<QuantityLedger & { event_name: string; node_name: string; bundle_no: string; can_reverse: boolean }>,
    timelineTotal: 0,
    timelineTruncated: false,

    qualityTicketIndex: 0,
    qualityNodeIndex: 0,
    qualityNodes: [] as RouteNode[],
    qualityNodeLabels: [] as string[],
    inspectionTypeIndex: 0,
    inspected: '',
    qualified: '0',
    rework: '0',
    showReworkNode: false,
    scrapped: '0',
    reworkNodeIndex: 0,
    reworkNodeLabels: ['请选择前序返修节点'] as string[],
    reworkNodes: [] as RouteNode[],
    reinspectionOf: '',
    qualityReason: '',
    qualitySaving: false,
    qualityCreateRequestId: '',
    inspections: [] as QualityView[],
    confirmingId: 0,

    handoverTicketIndex: 0,
    handoverSourceIndex: 0,
    handoverSources: [] as RouteNode[],
    handoverSourceLabels: [] as string[],
    handoverTargetIndex: 0,
    handoverTargets: [] as RouteNode[],
    handoverTargetLabels: ['请选择后续节点'] as string[],
    employees: [] as Employee[],
    receiverLabels: ['请选择接收人'] as string[],
    receiverIndex: 0,
    receiverId: '',
    handoverQuantity: '',
    handoverRemark: '',
    handoverSaving: false,
    handovers: [] as HandoverView[],
    handoverActionId: 0,

    taskNo: '',
    packingRemark: '',
    packingBoxes: [{ boxNo: '', rows: [{ ticketIndex: 0, quantity: '' }] }] as PackingBoxEditor[],
    packingSaving: false,
    packingTasks: [] as PackingTask[],
    packingApprovalId: 0,
    documentNo: '',
    warehouseName: '',
    locationName: '',
    inboundRemark: '',
    inboundOptions: [] as InboundOption[],
    inboundOptionLabels: ['请选择已审核箱内明细'] as string[],
    inboundRows: [{ optionIndex: 0, quantity: '' }] as EditableLine[],
    inboundSaving: false,
    inbounds: [] as FinishedInbound[],
    inboundApprovalId: 0,
    documentReverseKey: '',
    finishedDetailKey: '',
    finishedDetailLoading: false,
    finishedDetail: null as PackingTask | FinishedInbound | null,

    inventory: [] as FinishedInventoryRow[],
    inventoryTotal: 0,
    inventoryBedNo: '',
    inventoryColor: '',
    inventorySize: '',
    includeZero: false,

    preStatus: null as PreproductionStatus | null,
    techPacks: [] as TechPack[],
    currentTechPack: null as TechPack | null,
    currentUserId: 0,
    techPackMode: 'create' as 'create' | 'update',
    techPackVersion: '',
    techPackPatternNo: '',
    techPackRequirements: '',
    measurementRows: [emptyMeasurement()] as MeasurementEditorRow[],
    techPackSaving: false,
    preEmployeeLabels: ['请选择员工'] as string[],
    sampleTitle: '',
    sampleAssigneeIndex: 0,
    sampleSaving: false,
    sampleResultTaskId: 0,
    sampleResults: SAMPLE_RESULTS,
    sampleResultIndex: 0,
    sampleResultNote: '',
    sampleResultSaving: false,
    issueTitle: '',
    issueDetail: '',
    issueSaving: false,
    briefingEmployeeIndex: 0,
    briefingNote: '',
    briefingSaving: false,
    preActionId: 0,
    preApprovalId: 0,

    operationsOpen: false,
    operationsLoading: false,
    operations: null as OperationsDashboard | null,
    operationsRows: [] as OperationsDashboard['results'],
    operationsPage: 1,
    operationsPageSize: 20,
    operationsThresholdIndex: 1,
    operationsThresholds: [1, 24, 72, 168],
    operationsThresholdLabels: ['1 小时', '24 小时', '72 小时', '7 天'],
    operationsOrders: [] as Order[],
    operationsProductions: [] as Production[],
    operationsCustomers: [] as Array<{ id: number; name: string }>,
    operationsOrderLabels: ['全部订单'] as string[],
    operationsProductionLabels: ['全部床次'] as string[],
    operationsCustomerLabels: ['全部客户'] as string[],
    operationsOrderIndex: 0,
    operationsProductionIndex: 0,
    operationsCustomerIndex: 0,
    operationsStyleNo: '',
    operationsKeyword: '',
    operationsOrderOptionKeyword: '',
    operationsProductionOptionKeyword: '',
    operationsFiltersLoaded: false,
    operationsFiltersError: '',
    onlyStalled: false,
    stageFormOpen: false,
    stages: [] as ProductionStage[],
    stageTypes: STAGE_TYPES,
    stageTypeLabels: STAGE_TYPES.map((item) => item.label),
    stageFormId: 0,
    stageFormInUse: false,
    stageCode: '',
    stageName: '',
    stageTypeIndex: 0,
    stageSaving: false,
    routeEditorOpen: false,
    routeEditorId: 0,
    routeEditorMode: 'edit' as 'edit' | 'copy' | 'version',
    routeCopySourceId: 0,
    routeEditorCode: '',
    routeEditorName: '',
    routeEditorNodes: [] as RouteNodeEditorRow[],
    routeEditorSaving: false,
    templateFormOpen: false,
    templates: [] as ProductionRoute[],
    instanceTemplates: [] as ProductionRoute[],
    templateLabels: [] as string[],
    templateIndex: 0,
    targetTypeIndex: 0,
    targetTypeLabels: ['绑定订单', '绑定床次'],
    targetOrders: [] as Order[],
    targetProductions: [] as Production[],
    targetLabels: ['请选择绑定对象'] as string[],
    targetIndex: 0,
    instanceCode: '',
    instanceName: '',
    instantiating: false
  },

  onLoad(options: Record<string, string>) {
    const requestedEntry = options.entry || 'all';
    const entry = fixedEntry !== 'all'
      ? fixedEntry
      : (isProductionEntry(requestedEntry) ? requestedEntry : 'all');
    const config = ENTRY_CONFIG[entry];
    this.setData({
      entry,
      pageTitle: config.title,
      showRouteSetup: entry === 'all',
      activeTab: config.tabs.length ? config.tabs[0] : 'balance',
      operationsOpen: entry === 'operations',
      stageFormOpen: false,
      routeEditorOpen: false,
      templateFormOpen: false
    });
    wx.setNavigationBarTitle({ title: config.title });
  },

  onShow() {
    if (!requireLogin()) return;
    if (!intentIds.report) {
      intentIds.report = createRequestId('wx-stage');
      intentIds.handover = createRequestId('wx-cut');
      intentIds.packing = createRequestId('wx-pack');
      intentIds.inbound = createRequestId('wx-inbound');
    }
    this.refreshPermissions();
    this.initialize();
  },

  refreshPermissions() {
    const permissions = {
      canMfgView: hasPerm('manufacturing.view'),
      canMfgManage: hasPerm('manufacturing.manage'),
      canReverse: hasPerm('manufacturing.reverse'),
      canQualityView: hasPerm('quality.view'),
      canQualityManage: hasPerm('quality.manage'),
      canCuttingView: hasPerm('cutting.view'),
      canCuttingManage: hasPerm('cutting.manage'),
      canHandoverCreate: hasPerm('handover.create'),
      canFinishedView: hasPerm('finished_goods.view'),
      canFinishedManage: hasPerm('finished_goods.manage'),
      canPreView: hasPerm('preproduction.view'),
      canPreManage: hasPerm('preproduction.manage'),
      canPreApprove: hasPerm('preproduction.approve')
    };
    const entry = this.data.entry as ProductionEntry;
    const configuredTabs = ENTRY_CONFIG[entry].tabs;
    const tabs = TABS.filter((item) => configuredTabs.includes(item.key) && hasPerm(TAB_PERMISSIONS[item.key]));
    const activeTab = tabs.some((item) => item.key === this.data.activeTab)
      ? this.data.activeTab
      : (tabs.length ? tabs[0].key : this.data.activeTab);
    const requiredPermissions = configuredTabs
      .map((tab) => TAB_PERMISSIONS[tab])
      .filter((permission, index, all) => all.indexOf(permission) === index);
    const noPermission = entry === 'operations' ? !permissions.canMfgView : tabs.length === 0;
    const permissionSummary = entry === 'operations' ? 'manufacturing.view' : requiredPermissions.join(' / ');
    this.setData({
      ...permissions,
      tabs,
      activeTab,
      entryPermissionError: noPermission
        ? `当前岗位缺少 ${permissionSummary || '生产模块查看权限'}，无法使用${ENTRY_CONFIG[entry].title}，请联系管理员授权`
        : ''
    });
  },

  async initialize() {
    if (this.data.entry === 'operations') {
      this.setData({ operationsOpen: true, loadError: '' });
      await this.loadOperations();
      return;
    }
    if (!this.data.tabs.length) {
      ++loadSequence;
      this.setData({ loading: false, loadError: '', routes: [], route: null });
      return;
    }
    if (this.data.entry === 'preproduction' && this.data.canPreView) {
      this.setData({ loading: false, loadError: '', routes: [], route: null });
      this.loadPreproduction();
      return;
    }
    if (!this.data.canMfgView) {
      this.setData({ loadError: '当前岗位缺少 manufacturing.view，无法读取生产路线' });
      return;
    }
    const sequence = ++loadSequence;
    this.setData({ loading: true, loadError: '' });
    try {
      const routes = await fetchRoutes(false);
      if (sequence !== loadSequence) return;
      const previousId = this.data.route && this.data.route.id;
      let index = routes.findIndex((item) => item.id === previousId);
      if (index < 0) index = 0;
      this.setData({
        routes,
        routeLabels: routes.map((item) => `${item.code} · ${item.name}`),
        routeIndex: index,
        loading: false
      });
      if (routes.length) await this.selectRoute(routes[index]);
      else this.setData({ route: null, nodes: [], tickets: [], filteredBalances: [] });
    } catch (error) {
      if (sequence === loadSequence) this.setData({ loading: false, loadError: '生产路线加载失败，请稍后重试' });
    }
  },

  onRouteChange(e: WechatMiniprogram.PickerChange) {
    const route = this.data.routes[Number(e.detail.value)];
    if (route) {
      this.setData({ routeIndex: Number(e.detail.value) });
      this.selectRoute(route);
    }
  },

  async selectRoute(route: ProductionRoute) {
    const sequence = ++loadSequence;
    this.setData({
      route,
      nodes: route.nodes || [],
      nodeLabels: (route.nodes || []).map((node) => `${node.sequence}. ${node.stage_name}`),
      balances: [], filteredBalances: [], tickets: [], ticketLabels: [],
      ledger: [], inspections: [], handovers: [], packingTasks: [], inbounds: [], inventory: [],
      finishedDetailKey: '', finishedDetail: null,
      loadError: '', listLoading: true
    });
    try {
      const [balances, ticketPage] = await Promise.all([
        fetchRouteBalances(route.id),
        route.production
          ? fetchTickets({ production_id: route.production, page: 1, page_size: 100 })
          : Promise.resolve({ results: [] })
      ]);
      if (sequence !== loadSequence) return;
      const context = await this.loadRouteContext(route);
      if (sequence !== loadSequence) return;
      const tickets = this.deriveTickets(balances, context, ticketPage.results || []);
      const ticketLabels = tickets.map((item) => item.label);
      const nodes = route.nodes || [];
      const views = balances.map((row) => {
        const node = nodes.find((item) => item.id === row.node_id);
        const ticket = tickets.find((item) => item.id === row.ticket_id);
        return {
          ...row,
          stage_name: node ? node.stage_name : `节点 ${row.sequence}`,
          bed_no: context.bedNo,
          style_no: context.styleNo
        } as BalanceView;
      });
      this.setData({
        ...context,
        tickets,
        ticketLabels,
        balanceTicketLabels: ['全部菲票'].concat(ticketLabels),
        balances: views,
        filteredBalances: views,
        balanceTicketIndex: 0,
        reportTicketIndex: 0,
        reportNodeIndex: 0,
        qualityTicketIndex: 0,
        handoverTicketIndex: 0,
        listLoading: false
      });
      this.updateReportTargets();
      this.updateQualityNodes();
      this.updateHandoverNodes();
      this.loadLedger();
      this.loadActiveTab();
    } catch (error) {
      if (sequence === loadSequence) this.setData({ listLoading: false, loadError: '路线节点余额加载失败' });
    }
  },

  async loadRouteContext(route: ProductionRoute): Promise<{
    styleNo: string; bedNo: string; orderNo: string; orderId: number;
  }> {
    const fallback = { styleNo: '—', bedNo: '—', orderNo: '—', orderId: route.order || 0 };
    try {
      if (route.production) {
        const production = await fetchProduction(route.production) as Production & { order?: number; order_no?: string };
        return {
          styleNo: production.style_no || '—', bedNo: production.bed_no || '—',
          orderNo: production.order_no || '—', orderId: production.order || 0
        };
      }
      if (route.order) {
        const order = await fetchOrder(route.order);
        return {
          styleNo: order.style_no || '—', bedNo: '订单路线', orderNo: order.order_no || '—', orderId: order.id
        };
      }
    } catch (error) { /* 路线余额仍可用，仅上下文展示降级 */ }
    return fallback;
  },

  deriveTickets(
    rows: RouteBalanceRow[],
    context: { styleNo: string; bedNo: string },
    allTickets: Ticket[] = []
  ): TicketOption[] {
    const map: Record<number, TicketOption> = {};
    const put = (id: number, bundleNo: string | number, color: string, size: string) => {
      if (!map[id]) {
        map[id] = {
          id,
          bed_no: context.bedNo,
          bundle_no: String(bundleNo),
          color,
          size,
          style_no: context.styleNo,
          label: `${context.styleNo} · ${context.bedNo} · 第${bundleNo}扎 · ${color || '—'}/${size || '—'}`
        };
      }
    };
    allTickets.forEach((ticket) => put(ticket.id, ticket.bundle_no, ticket.color, ticket.size));
    rows.forEach((row) => put(row.ticket_id, row.bundle_no, row.color, row.size));
    return Object.keys(map).map((key) => map[Number(key)]).sort((a, b) => Number(a.bundle_no) - Number(b.bundle_no));
  },

  currentTicket(indexKey: 'reportTicketIndex' | 'qualityTicketIndex' | 'handoverTicketIndex'): TicketOption | null {
    return this.data.tickets[this.data[indexKey]] || null;
  },

  onBalanceTicketChange(e: WechatMiniprogram.PickerChange) {
    const index = Number(e.detail.value);
    const ticket = index > 0 ? this.data.tickets[index - 1] : null;
    this.setData({
      balanceTicketIndex: index,
      filteredBalances: ticket ? this.data.balances.filter((row) => row.ticket_id === ticket.id) : this.data.balances
    });
  },

  onTabTap(e: WechatMiniprogram.TouchEvent) {
    const tab = e.currentTarget.dataset.key as TabKey;
    this.setData({ activeTab: tab });
    this.loadActiveTab();
  },

  async loadDashboard() {
    if (!this.data.route) return;
    this.setData({ listLoading: true });
    try {
      const dashboard = await fetchRouteDashboard(this.data.route.id);
      this.setData({ dashboard });
    } catch (error) { /* 请求层统一提示 */ } finally {
      this.setData({ listLoading: false });
    }
  },

  loadActiveTab() {
    if (!this.data.route) return;
    const loaders: Record<TabKey, () => void> = {
      balance: () => undefined,
      dashboard: () => this.loadDashboard(),
      report: () => this.loadLedger(),
      quality: () => this.loadInspections(),
      cutting: () => this.loadHandovers(),
      packing: () => this.loadPacking(),
      inventory: () => this.loadInventory(),
      preproduction: () => this.loadPreproduction()
    };
    loaders[this.data.activeTab]();
  },

  onRefresh() {
    this.refreshPermissions();
    this.initialize();
  },

  // ================= 跨路线运营看板 =================
  async onOpenOperations() {
    if (!this.data.canMfgView) return;
    this.setData({ operationsOpen: !this.data.operationsOpen });
    if (this.data.operationsOpen) await this.loadOperations();
  },

  onOperationsThresholdChange(e: WechatMiniprogram.PickerChange) {
    this.setData({ operationsThresholdIndex: Number(e.detail.value), operationsPage: 1 });
    this.loadOperations();
  },

  onOnlyStalledChange(e: WechatMiniprogram.SwitchChange) {
    this.setData({ onlyStalled: e.detail.value });
    this.updateOperationsRows();
  },

  updateOperationsRows() {
    const operations = this.data.operations;
    this.setData({
      operationsRows: operations ? operations.results.filter((row) => !this.data.onlyStalled || row.stalled_nodes.length > 0) : []
    });
  },

  onOperationsOrderChange(e: WechatMiniprogram.PickerChange) {
    this.setData({ operationsOrderIndex: Number(e.detail.value), operationsProductionIndex: 0 });
  },

  onOperationsProductionChange(e: WechatMiniprogram.PickerChange) {
    this.setData({ operationsProductionIndex: Number(e.detail.value), operationsOrderIndex: 0 });
  },

  onOperationsCustomerChange(e: WechatMiniprogram.PickerChange) {
    this.setData({ operationsCustomerIndex: Number(e.detail.value) });
  },

  onOperationsStyleInput(e: WechatMiniprogram.Input) {
    this.setData({ operationsStyleNo: e.detail.value });
  },

  onOperationsKeywordInput(e: WechatMiniprogram.Input) {
    this.setData({ operationsKeyword: e.detail.value });
  },

  onOperationsOrderOptionInput(e: WechatMiniprogram.Input) {
    this.setData({ operationsOrderOptionKeyword: e.detail.value });
  },

  onOperationsProductionOptionInput(e: WechatMiniprogram.Input) {
    this.setData({ operationsProductionOptionKeyword: e.detail.value });
  },

  async onSearchOperationsOptions() {
    this.setData({
      operationsOrderIndex: 0, operationsProductionIndex: 0, operationsCustomerIndex: 0,
      operationsPage: 1
    });
    await this.loadOperationsFilterOptions(true);
  },

  onResetOperationsFilters() {
    this.setData({
      operationsOrderIndex: 0, operationsProductionIndex: 0, operationsCustomerIndex: 0,
      operationsStyleNo: '', operationsKeyword: '', onlyStalled: false, operationsPage: 1
    });
    this.loadOperations();
  },

  async loadOperationsFilterOptions(force = false) {
    if (this.data.operationsFiltersLoaded && !force) return;
    try {
      const orderKeyword = this.data.operationsOrderOptionKeyword.trim();
      const productionKeyword = this.data.operationsProductionOptionKeyword.trim();
      const [orderRows, productionRows] = await Promise.all([
        fetchAllPages((page) => fetchOrders({ page, page_size: 100, keyword: orderKeyword || undefined })),
        fetchAllPages((page) => fetchProductions({ page, page_size: 100, keyword: productionKeyword || undefined }))
      ]);
      const customers: Array<{ id: number; name: string }> = [];
      orderRows.forEach((order) => {
        if (order.customer && !customers.some((item) => item.id === order.customer)) {
          customers.push({ id: order.customer as number, name: order.customer_name || `客户 ${order.customer}` });
        }
      });
      this.setData({
        operationsOrders: orderRows,
        operationsProductions: productionRows,
        operationsCustomers: customers,
        operationsOrderLabels: ['全部订单'].concat(orderRows.map((item) => `${item.order_no} · ${item.style_no || '—'}`)),
        operationsProductionLabels: ['全部床次'].concat(productionRows.map((item) => `${item.bed_no} · ${item.style_no || '—'}`)),
        operationsCustomerLabels: ['全部客户'].concat(customers.map((item) => item.name)),
        operationsFiltersLoaded: true,
        operationsFiltersError: ''
      });
    } catch (error) {
      this.setData({ operationsFiltersError: '订单、床次或客户选项加载失败，仍可使用款号和关键字查询' });
    }
  },

  async loadOperations() {
    if (!this.data.canMfgView || this.data.operationsLoading) return;
    this.setData({ operationsLoading: true });
    await this.loadOperationsFilterOptions();
    const order = this.data.operationsOrders[this.data.operationsOrderIndex - 1];
    const production = this.data.operationsProductions[this.data.operationsProductionIndex - 1];
    const customer = this.data.operationsCustomers[this.data.operationsCustomerIndex - 1];
    try {
      const operations = await fetchOperationsDashboard(
        this.data.operationsThresholds[this.data.operationsThresholdIndex], {
          order_id: order ? order.id : undefined,
          production_id: production ? production.id : undefined,
          customer_id: customer ? customer.id : undefined,
          style_no: this.data.operationsStyleNo.trim() || undefined,
          q: this.data.operationsKeyword.trim() || undefined,
          page: this.data.operationsPage, page_size: this.data.operationsPageSize
        });
      this.setData({ operations });
      this.updateOperationsRows();
    } catch (error) { /* 请求层统一提示，保留已有结果 */ } finally {
      this.setData({ operationsLoading: false });
    }
  },

  onQueryOperations() {
    this.setData({ operationsPage: 1 });
    this.loadOperations();
  },

  onOperationsPreviousPage() {
    if (!this.data.operations || !this.data.operations.has_previous || this.data.operationsLoading) return;
    this.setData({ operationsPage: Math.max(1, this.data.operationsPage - 1) });
    this.loadOperations();
  },

  onOperationsNextPage() {
    if (!this.data.operations || !this.data.operations.has_next || this.data.operationsLoading) return;
    this.setData({ operationsPage: this.data.operationsPage + 1 });
    this.loadOperations();
  },

  async onOpenOperationsRoute(e: WechatMiniprogram.TouchEvent) {
    const id = Number(e.currentTarget.dataset.id);
    const route = this.data.routes.find((item) => item.id === id);
    if (!route) { toast('当前路线列表未加载该实例，请先刷新'); return; }
    this.setData({ operationsOpen: false });
    await this.selectRoute(route);
  },

  // ================= 标准阶段与路线模板 =================
  async loadRouteSetup() {
    const [stages, templates] = await Promise.all([fetchStages(), fetchRoutes(true)]);
    const activeTemplates = templates.filter((item) => item.is_active !== false);
    this.setData({
      stages, templates, instanceTemplates: activeTemplates,
      templateLabels: activeTemplates.map((item) => `${item.code} · V${item.version || 1} ${item.name}`),
      templateIndex: Math.min(this.data.templateIndex, Math.max(0, activeTemplates.length - 1))
    });
  },

  async onOpenStageForm() {
    if (!this.data.canMfgManage) return;
    const open = !this.data.stageFormOpen;
    this.setData({ stageFormOpen: open, routeEditorOpen: false, templateFormOpen: false });
    if (open) {
      try { await this.loadRouteSetup(); } catch (error) { /* 请求层统一提示，可关闭后重试 */ }
    }
  },

  onStageCodeInput(e: WechatMiniprogram.Input) { this.setData({ stageCode: e.detail.value }); },
  onStageNameInput(e: WechatMiniprogram.Input) { this.setData({ stageName: e.detail.value }); },
  onStageTypeChange(e: WechatMiniprogram.PickerChange) {
    if (!this.data.stageFormInUse) this.setData({ stageTypeIndex: Number(e.detail.value) });
  },

  onNewStage() {
    this.setData({ stageFormId: 0, stageFormInUse: false, stageCode: '', stageName: '', stageTypeIndex: 0 });
  },

  onEditStage(e: WechatMiniprogram.TouchEvent) {
    const stage = this.data.stages.find((item) => item.id === Number(e.currentTarget.dataset.id));
    if (!stage) return;
    const index = Math.max(0, STAGE_TYPES.findIndex((item) => item.key === stage.stage_type));
    this.setData({
      stageFormId: stage.id, stageFormInUse: !!stage.in_use,
      stageCode: stage.code, stageName: stage.name, stageTypeIndex: index
    });
  },

  async onToggleStageStatus(e: WechatMiniprogram.TouchEvent) {
    if (!this.data.canMfgManage) return;
    const stage = this.data.stages.find((item) => item.id === Number(e.currentTarget.dataset.id));
    if (!stage) return;
    try {
      await setStageStatus(stage.id, stage.is_active === false);
      await this.loadRouteSetup();
      wx.showToast({ title: stage.is_active === false ? '阶段已启用' : '阶段已停用', icon: 'success' });
    } catch (error) { /* 请求层统一提示 */ }
  },

  async onSaveStage() {
    if (!this.data.canMfgManage || this.data.stageSaving) return;
    const code = this.data.stageCode.trim();
    const name = this.data.stageName.trim();
    if (!code || !name) { toast('请填写阶段编码和名称'); return; }
    this.setData({ stageSaving: true });
    try {
      await saveStage({
        id: this.data.stageFormId || undefined, code, name,
        stage_type: STAGE_TYPES[this.data.stageTypeIndex].key
      });
      await this.loadRouteSetup();
      this.onNewStage();
      wx.showToast({ title: '阶段已保存', icon: 'success' });
    } catch (error) { /* 请求层统一提示，保留原表单 */ } finally {
      this.setData({ stageSaving: false });
    }
  },

  async onOpenRouteEditor() {
    if (!this.data.canMfgManage) return;
    const open = !this.data.routeEditorOpen;
    this.setData({ routeEditorOpen: open, stageFormOpen: false, templateFormOpen: false });
    if (open) {
      try { await this.loadRouteSetup(); } catch (error) { /* 请求层统一提示，可关闭后重试 */ }
    }
  },

  onNewRouteTemplate() {
    this.setData({
      routeEditorId: 0, routeEditorMode: 'edit', routeCopySourceId: 0,
      routeEditorCode: '', routeEditorName: '',
      routeEditorNodes: [{ stageIndex: 0, required: true, allowParallel: false, allowSkip: false }]
    });
  },

  openRouteEditor(route: ProductionRoute, mode: 'edit' | 'copy' | 'version') {
    this.setData({
      routeEditorId: mode === 'edit' ? route.id : 0,
      routeEditorMode: mode,
      routeCopySourceId: mode === 'edit' ? 0 : route.id,
      routeEditorCode: mode === 'edit' ? route.code : `${route.code}${mode === 'version' ? '-V' + ((route.version || 1) + 1) : '-COPY'}`,
      routeEditorName: mode === 'edit' ? route.name : `${route.name}${mode === 'version' ? ' 新版本' : ' 副本'}`,
      routeEditorNodes: (route.nodes || []).map((node) => ({
        stageIndex: Math.max(0, this.data.stages.findIndex((stage) => stage.id === node.stage)),
        required: node.required, allowParallel: node.allow_parallel, allowSkip: node.allow_skip
      }))
    });
  },

  onEditRouteTemplate(e: WechatMiniprogram.TouchEvent) {
    const route = this.data.templates.find((item) => item.id === Number(e.currentTarget.dataset.id));
    if (route) this.openRouteEditor(route, 'edit');
  },

  onCopyRouteTemplate(e: WechatMiniprogram.TouchEvent) {
    const route = this.data.templates.find((item) => item.id === Number(e.currentTarget.dataset.id));
    if (route) this.openRouteEditor(route, 'copy');
  },

  onNewRouteVersion(e: WechatMiniprogram.TouchEvent) {
    const route = this.data.templates.find((item) => item.id === Number(e.currentTarget.dataset.id));
    if (route) this.openRouteEditor(route, 'version');
  },

  async onToggleRouteTemplateStatus(e: WechatMiniprogram.TouchEvent) {
    if (!this.data.canMfgManage) return;
    const route = this.data.templates.find((item) => item.id === Number(e.currentTarget.dataset.id));
    if (!route) return;
    try {
      await setRouteTemplateStatus(route.id, route.is_active === false);
      await this.loadRouteSetup();
      wx.showToast({ title: route.is_active === false ? '模板已启用' : '模板已停用', icon: 'success' });
    } catch (error) { /* 请求层统一提示 */ }
  },

  onRouteEditorCodeInput(e: WechatMiniprogram.Input) { this.setData({ routeEditorCode: e.detail.value }); },
  onRouteEditorNameInput(e: WechatMiniprogram.Input) { this.setData({ routeEditorName: e.detail.value }); },

  onAddRouteEditorNode() {
    const nodes = this.data.routeEditorNodes.concat([
      { stageIndex: 0, required: true, allowParallel: false, allowSkip: false }
    ]);
    this.setData({ routeEditorNodes: nodes });
  },

  onRemoveRouteEditorNode(e: WechatMiniprogram.TouchEvent) {
    if (this.data.routeEditorNodes.length <= 1) { toast('路线至少保留一个节点'); return; }
    const nodes = this.data.routeEditorNodes.slice();
    nodes.splice(Number(e.currentTarget.dataset.index), 1);
    this.setData({ routeEditorNodes: nodes });
  },

  onRouteEditorStageChange(e: WechatMiniprogram.PickerChange) {
    const index = Number(e.currentTarget.dataset.index);
    this.setData({ [`routeEditorNodes[${index}].stageIndex`]: Number(e.detail.value) });
  },

  onRouteEditorFlagChange(e: WechatMiniprogram.TouchEvent) {
    const index = Number(e.currentTarget.dataset.index);
    const field = e.currentTarget.dataset.field as 'required' | 'allowParallel' | 'allowSkip';
    const node = this.data.routeEditorNodes[index];
    if (node) this.setData({ [`routeEditorNodes[${index}].${field}`]: !node[field] });
  },

  async onSaveRouteTemplate() {
    if (!this.data.canMfgManage || this.data.routeEditorSaving) return;
    const code = this.data.routeEditorCode.trim();
    const name = this.data.routeEditorName.trim();
    if (!code || !name) { toast('请填写路线编码和名称'); return; }
    if (!this.data.stages.length || !this.data.routeEditorNodes.length) {
      toast('请先创建标准阶段并添加路线节点'); return;
    }
    this.setData({ routeEditorSaving: true });
    const mode = this.data.routeEditorMode;
    try {
      const payload = {
        id: this.data.routeEditorId || undefined, code, name,
        nodes: this.data.routeEditorNodes.map((node, index) => ({
          stage_id: this.data.stages[node.stageIndex].id, sequence: index + 1,
          required: node.required, allow_parallel: node.allowParallel, allow_skip: node.allowSkip
        }))
      };
      if (mode === 'edit') {
        await saveRouteTemplate(payload);
      } else {
        await copyRouteTemplate(this.data.routeCopySourceId, mode, { code, name, nodes: payload.nodes });
      }
      await this.loadRouteSetup();
      this.onNewRouteTemplate();
      wx.showToast({ title: mode === 'version' ? '新版本已创建' : '路线模板已保存', icon: 'success' });
    } catch (error) { /* 请求层统一提示，保留原表单 */ } finally {
      this.setData({ routeEditorSaving: false });
    }
  },

  // ================= 路线实例 =================
  async onOpenTemplateForm() {
    if (!this.data.canMfgManage) return;
    this.setData({ templateFormOpen: !this.data.templateFormOpen, stageFormOpen: false, routeEditorOpen: false });
    if (!this.data.templateFormOpen || this.data.templates.length) return;
    try {
      const [templates, orders, productions] = await Promise.all([
        fetchRoutes(true), fetchOrders({ page_size: 100 }), fetchProductions({ page_size: 100 })
      ]);
      const activeTemplates = templates.filter((item) => item.is_active !== false);
      this.setData({
        templates, instanceTemplates: activeTemplates,
        templateLabels: activeTemplates.map((item) => `${item.code} · V${item.version || 1} ${item.name}`),
        targetOrders: orders.results || [],
        targetProductions: productions.results || []
      });
      this.updateInstanceTargets();
    } catch (error) { /* 请求层提示，保留表单供重试 */ }
  },

  onTemplateChange(e: WechatMiniprogram.PickerChange) { this.setData({ templateIndex: Number(e.detail.value) }); },
  onTargetTypeChange(e: WechatMiniprogram.PickerChange) {
    this.setData({ targetTypeIndex: Number(e.detail.value), targetIndex: 0 });
    this.updateInstanceTargets();
  },
  onTargetChange(e: WechatMiniprogram.PickerChange) { this.setData({ targetIndex: Number(e.detail.value) }); },
  onInstanceCodeInput(e: WechatMiniprogram.Input) { this.setData({ instanceCode: e.detail.value }); },
  onInstanceNameInput(e: WechatMiniprogram.Input) { this.setData({ instanceName: e.detail.value }); },

  updateInstanceTargets() {
    const orderMode = this.data.targetTypeIndex === 0;
    const labels = orderMode
      ? this.data.targetOrders.map((item) => `${item.order_no} · ${item.style_no || ''}`)
      : this.data.targetProductions.map((item) => `${item.bed_no} · ${item.style_no || ''}`);
    this.setData({ targetLabels: labels.length ? labels : ['请选择绑定对象'] });
  },

  async onInstantiate() {
    if (!this.data.canMfgManage || this.data.instantiating) return;
    const template = this.data.instanceTemplates[this.data.templateIndex];
    const orderMode = this.data.targetTypeIndex === 0;
    const target = orderMode ? this.data.targetOrders[this.data.targetIndex] : this.data.targetProductions[this.data.targetIndex];
    if (!template || !target) { toast('请选择路线模板和绑定对象'); return; }
    this.setData({ instantiating: true });
    try {
      const route = await instantiateRoute(template.id, {
        order_id: orderMode ? target.id : undefined,
        production_id: orderMode ? undefined : target.id,
        code: this.data.instanceCode.trim() || undefined,
        name: this.data.instanceName.trim() || undefined
      });
      wx.showToast({ title: '路线已创建', icon: 'success' });
      this.setData({ templateFormOpen: false, instanceCode: '', instanceName: '' });
      await this.initialize();
      const index = this.data.routes.findIndex((item) => item.id === route.id);
      if (index >= 0) this.onRouteChange({ detail: { value: String(index) } } as WechatMiniprogram.PickerChange);
    } catch (error) { /* 请求层提示 */ } finally {
      this.setData({ instantiating: false });
    }
  },

  // ================= 节点报工 =================
  onReportTicketChange(e: WechatMiniprogram.PickerChange) { this.setData({ reportTicketIndex: Number(e.detail.value) }); },
  onReportNodeChange(e: WechatMiniprogram.PickerChange) {
    this.setData({ reportNodeIndex: Number(e.detail.value), targetNodeIndex: 0 });
    this.updateReportTargets();
  },
  onActionChange(e: WechatMiniprogram.PickerChange) {
    this.setData({ actionIndex: Number(e.detail.value), targetNodeIndex: 0 });
    this.updateReportTargets();
  },
  onTargetNodeChange(e: WechatMiniprogram.PickerChange) { this.setData({ targetNodeIndex: Number(e.detail.value) }); },
  onReportQuantityInput(e: WechatMiniprogram.Input) { this.setData({ reportQuantity: e.detail.value }); },
  onReportReasonInput(e: WechatMiniprogram.Input) { this.setData({ reportReason: e.detail.value }); },

  updateReportTargets() {
    const node = this.data.nodes[this.data.reportNodeIndex];
    const action = ACTIONS[this.data.actionIndex].key;
    let targets: RouteNode[] = [];
    if (node && (action === 'transfer' || action === 'return')) {
      targets = this.data.nodes.filter((item) => action === 'transfer'
        ? item.sequence > node.sequence : item.sequence < node.sequence);
    }
    this.setData({
      targetNodes: targets,
      targetNodeLabels: ['请选择目标节点'].concat(targets.map((item) => `${item.sequence}. ${item.stage_name}`)),
      targetNodeIndex: 0
    });
  },

  async onSubmitReport() {
    if (!this.data.canMfgManage || this.data.reportSaving || !this.data.route) return;
    const ticket = this.currentTicket('reportTicketIndex');
    const node = this.data.nodes[this.data.reportNodeIndex];
    const action = ACTIONS[this.data.actionIndex].key;
    const quantity = positiveInt(this.data.reportQuantity);
    if (!ticket || !node) { toast('请选择菲票和节点'); return; }
    if (!quantity) { toast('数量须为正整数'); return; }
    if (action === 'receive' && this.data.reportNodeIndex !== 0) { toast('直接接收仅限路线首节点'); return; }
    const target = this.data.targetNodes[this.data.targetNodeIndex - 1];
    if ((action === 'transfer' || action === 'return') && !target) { toast('请选择目标节点'); return; }
    if ((node.stage_type === 'semi_inspection' || node.stage_type === 'final_inspection')
      && (action === 'complete' || action === 'return' || action === 'scrap')) {
      toast('请改用质量检验单确认'); return;
    }
    if (node.stage_type === 'packing' && action === 'complete') {
      toast('请改用包装任务审核'); return;
    }
    if (node.stage_type === 'finished_inbound') {
      toast('成品库存须由入库或出货单变更'); return;
    }
    if (action === 'transfer'
      && (node.stage_type === 'packing' || (target && target.stage_type === 'finished_inbound'))) {
      toast('请改用成品入库单审核'); return;
    }
    this.setData({ reportSaving: true });
    try {
      await postStageAction(action, {
        ticket_id: ticket.id, node_id: node.id, to_node_id: target ? target.id : undefined,
        quantity, request_id: intentIds.report, source: 'wechat.full-production',
        reason: this.data.reportReason.trim()
      });
      intentIds.report = createRequestId('wx-stage');
      wx.showToast({ title: '节点报工成功', icon: 'success' });
      this.setData({ reportQuantity: '', reportReason: '' });
      const route = this.data.route;
      if (route) await this.selectRoute(route);
    } catch (error) {
      // 传输结果未知才沿用同键；明确 4xx 后允许用户修改内容并重新提交。
      if (!shouldRetrySameIntent(error)) intentIds.report = createRequestId('wx-stage');
    } finally {
      this.setData({ reportSaving: false });
    }
  },

  async loadLedger() {
    if (!this.data.route || !this.data.canMfgView) return;
    try {
      const res = await fetchRouteTimeline(this.data.route.id);
      const reversedIds = (res.events || []).filter((row) => row.reversal_of).map((row) => row.reversal_of);
      const rows = (res.events || []).map((row) => {
        const node = this.data.nodes.find((item) => item.id === row.node);
        const ticket = this.data.tickets.find((item) => item.id === row.ticket);
        return {
          ...row, event_name: row.event_type_display || EVENT_NAMES[row.event_type] || row.event_type,
          node_name: row.node_name || (node ? node.stage_name : String(row.node)),
          bundle_no: row.bundle_no || (ticket ? ticket.bundle_no : String(row.ticket)),
          can_reverse: row.event_type !== 'reversal' && reversedIds.indexOf(row.id) < 0
            && !isDocumentManagedLedger(row.source)
        };
      });
      this.setData({ ledger: rows, timelineTotal: res.total, timelineTruncated: res.truncated });
    } catch (error) { /* 保留旧列表 */ }
  },

  async onReverseLedger(e: WechatMiniprogram.TouchEvent) {
    if (!this.data.canReverse) return;
    const id = Number(e.currentTarget.dataset.id);
    const row = this.data.ledger.find((item) => item.id === id);
    if (!row || row.event_type === 'reversal') return;
    const modal = await wx.showModal({ title: '冲销数量流水', editable: true, placeholderText: '冲销原因（可选）' });
    if (!modal.confirm) return;
    const key = `reverse-${id}`;
    if (!approvalIds[key]) approvalIds[key] = createRequestId('wx-reverse');
    try {
      await reverseLedger(id, approvalIds[key], (modal.content || '').trim());
      delete approvalIds[key];
      wx.showToast({ title: '已冲销', icon: 'success' });
      const route = this.data.route;
      if (route) await this.selectRoute(route);
    } catch (error) {
      if (!shouldRetrySameIntent(error)) delete approvalIds[key];
    }
  },

  // ================= 半检 / 尾查 / 复检 =================
  onQualityTicketChange(e: WechatMiniprogram.PickerChange) { this.setData({ qualityTicketIndex: Number(e.detail.value) }); },
  onInspectionTypeChange(e: WechatMiniprogram.PickerChange) {
    this.setData({ inspectionTypeIndex: Number(e.detail.value), qualityNodeIndex: 0, reworkNodeIndex: 0 });
    this.updateQualityNodes();
  },
  onQualityNodeChange(e: WechatMiniprogram.PickerChange) {
    this.setData({ qualityNodeIndex: Number(e.detail.value), reworkNodeIndex: 0 });
    this.updateReworkNodes();
  },
  onInspectedInput(e: WechatMiniprogram.Input) { this.setData({ inspected: e.detail.value }); },
  onQualifiedInput(e: WechatMiniprogram.Input) { this.setData({ qualified: e.detail.value }); },
  onReworkInput(e: WechatMiniprogram.Input) { this.setData({ rework: e.detail.value, showReworkNode: Number(e.detail.value) > 0 }); },
  onScrappedInput(e: WechatMiniprogram.Input) { this.setData({ scrapped: e.detail.value }); },
  onReworkNodeChange(e: WechatMiniprogram.PickerChange) { this.setData({ reworkNodeIndex: Number(e.detail.value) }); },
  onReinspectionOfInput(e: WechatMiniprogram.Input) { this.setData({ reinspectionOf: e.detail.value }); },
  onQualityReasonInput(e: WechatMiniprogram.Input) { this.setData({ qualityReason: e.detail.value }); },

  updateQualityNodes() {
    const type = INSPECTION_TYPES[this.data.inspectionTypeIndex].key;
    const nodes = type === 'reinspection'
      ? this.data.nodes.filter((node) => node.stage_type === 'semi_inspection' || node.stage_type === 'final_inspection')
      : this.data.nodes.filter((node) => node.stage_type === type);
    this.setData({
      qualityNodes: nodes,
      qualityNodeLabels: nodes.map((node) => `${node.sequence}. ${node.stage_name}`),
      qualityNodeIndex: 0
    });
    this.updateReworkNodes();
  },

  updateReworkNodes() {
    const node = this.data.qualityNodes[this.data.qualityNodeIndex];
    const nodes = node ? this.data.nodes.filter((item) => item.sequence < node.sequence) : [];
    this.setData({
      reworkNodes: nodes,
      reworkNodeLabels: ['请选择前序返修节点'].concat(nodes.map((item) => `${item.sequence}. ${item.stage_name}`)),
      reworkNodeIndex: 0
    });
  },

  async onSubmitInspection() {
    if (!this.data.canQualityManage || this.data.qualitySaving) return;
    const ticket = this.currentTicket('qualityTicketIndex');
    const node = this.data.qualityNodes[this.data.qualityNodeIndex];
    const inspected = positiveInt(this.data.inspected);
    const qualified = nonNegativeInt(this.data.qualified);
    const rework = nonNegativeInt(this.data.rework);
    const scrapped = nonNegativeInt(this.data.scrapped);
    if (!ticket || !node) { toast('请选择菲票和检验节点'); return; }
    if (!inspected || qualified === null || rework === null || scrapped === null) { toast('检验数量须为非负整数，检验数须大于零'); return; }
    if (inspected !== qualified + rework + scrapped) { toast('检验数须等于合格、返修、报废之和'); return; }
    const reworkNode = this.data.reworkNodes[this.data.reworkNodeIndex - 1];
    if (rework > 0 && !reworkNode) { toast('返修数量大于零时请选择前序节点'); return; }
    if (rework > 0 && !this.data.qualityReason.trim()) { toast('返修或报废须填写原因'); return; }
    if (scrapped > 0 && !this.data.qualityReason.trim()) { toast('返修或报废须填写原因'); return; }
    const type = INSPECTION_TYPES[this.data.inspectionTypeIndex].key;
    const previousId = positiveInt(this.data.reinspectionOf);
    if (type === 'reinspection' && !previousId) { toast('复检须填写原检验单 ID'); return; }
    const requestId = this.data.qualityCreateRequestId || createRequestId('quality-create');
    this.setData({ qualitySaving: true, qualityCreateRequestId: requestId });
    try {
      await createInspection({
        ticket: ticket.id, node: node.id, inspection_type: type, inspected, qualified, rework, scrapped,
        rework_node: rework > 0 && reworkNode ? reworkNode.id : undefined,
        reinspection_of: type === 'reinspection' && previousId ? previousId : undefined,
        reason: this.data.qualityReason.trim(), create_request_id: requestId
      });
      wx.showToast({ title: '检验单已创建', icon: 'success' });
      this.setData({ inspected: '', qualified: '0', rework: '0', scrapped: '0', reinspectionOf: '', qualityReason: '', qualityCreateRequestId: '' });
      this.loadInspections();
    } catch (error) {
      const status = (error as { status?: number }).status;
      if (status && status >= 400 && status < 500) this.setData({ qualityCreateRequestId: '' });
    } finally {
      this.setData({ qualitySaving: false });
    }
  },

  async loadInspections() {
    if (!this.data.route || !this.data.canQualityView) return;
    this.setData({ listLoading: true });
    try {
      const rows = await fetchInspections(this.data.route.id);
      this.setData({
        inspections: rows.map((row) => {
          const ticket = this.data.tickets.find((item) => item.id === row.ticket);
          return { ...row, color: ticket ? ticket.color : '', size: ticket ? ticket.size : '', style_no: ticket ? ticket.style_no : '' };
        }),
        listLoading: false
      });
    } catch (error) { this.setData({ listLoading: false }); }
  },

  async onConfirmInspection(e: WechatMiniprogram.TouchEvent) {
    if (!this.data.canQualityManage || this.data.confirmingId) return;
    const id = Number(e.currentTarget.dataset.id);
    const key = `quality-${id}`;
    if (!approvalIds[key]) approvalIds[key] = createRequestId('wx-quality');
    this.setData({ confirmingId: id });
    try {
      await confirmInspection(id, approvalIds[key]);
      delete approvalIds[key];
      wx.showToast({ title: '检验已确认', icon: 'success' });
      const route = this.data.route;
      if (route) await this.selectRoute(route);
      this.setData({ activeTab: 'quality' });
      await this.loadInspections();
    } catch (error) {
      if (!shouldRetrySameIntent(error)) delete approvalIds[key];
    } finally {
      this.setData({ confirmingId: 0 });
    }
  },

  // ================= 裁片下发与签收 =================
  onHandoverTicketChange(e: WechatMiniprogram.PickerChange) { this.setData({ handoverTicketIndex: Number(e.detail.value) }); },
  onHandoverSourceChange(e: WechatMiniprogram.PickerChange) {
    this.setData({ handoverSourceIndex: Number(e.detail.value), handoverTargetIndex: 0 });
    this.updateHandoverNodes();
  },
  onHandoverTargetChange(e: WechatMiniprogram.PickerChange) { this.setData({ handoverTargetIndex: Number(e.detail.value) }); },
  onReceiverChange(e: WechatMiniprogram.PickerChange) { this.setData({ receiverIndex: Number(e.detail.value), receiverId: '' }); },
  onReceiverIdInput(e: WechatMiniprogram.Input) { this.setData({ receiverId: e.detail.value, receiverIndex: 0 }); },
  onHandoverQuantityInput(e: WechatMiniprogram.Input) { this.setData({ handoverQuantity: e.detail.value }); },
  onHandoverRemarkInput(e: WechatMiniprogram.Input) { this.setData({ handoverRemark: e.detail.value }); },

  updateHandoverNodes() {
    const sources = this.data.nodes.filter((node) => node.stage_type === 'cutting');
    const source = sources[this.data.handoverSourceIndex] || sources[0];
    const targets = source ? this.data.nodes.filter((node) => node.sequence > source.sequence) : [];
    this.setData({
      handoverSources: sources,
      handoverSourceLabels: sources.map((node) => `${node.sequence}. ${node.stage_name}`),
      handoverTargets: targets,
      handoverTargetLabels: ['请选择后续节点'].concat(targets.map((node) => `${node.sequence}. ${node.stage_name}`)),
      handoverTargetIndex: 0
    });
  },

  async loadHandovers() {
    if (!this.data.route || !this.data.canCuttingView) return;
    this.setData({ listLoading: true });
    if (!this.data.employees.length && (this.data.canCuttingManage || this.data.canHandoverCreate)) {
      fetchEmployees().then((employees) => this.setData({
        employees,
        receiverLabels: ['请选择接收人'].concat(employees.map((item) => `${item.name}（用户 ${item.user_id}）`))
      })).catch(() => undefined);
    }
    try {
      const rows = await fetchHandovers(this.data.nodes.map((node) => node.id));
      const currentUser = getApp<IAppOption>().globalData.user;
      const currentUserId = currentUser ? currentUser.id : 0;
      this.setData({
        handovers: rows.map((row) => {
          const ticket = this.data.tickets.find((item) => item.id === row.ticket);
          return {
            ...row, color: ticket ? ticket.color : '', size: ticket ? ticket.size : '',
            style_no: ticket ? ticket.style_no : '',
            can_sign: this.data.canCuttingManage || currentUserId === row.receiver
          };
        }),
        listLoading: false
      });
    } catch (error) { this.setData({ listLoading: false }); }
  },

  onOpenResponsibleHandover() {
    if (!this.data.canHandoverCreate) return;
    const ticket = this.currentTicket('handoverTicketIndex');
    const source = this.data.handoverSources[this.data.handoverSourceIndex];
    const target = this.data.handoverTargets[this.data.handoverTargetIndex - 1];
    const employee = this.data.employees[this.data.receiverIndex - 1];
    const receiverId = employee ? employee.user_id : positiveInt(this.data.receiverId);
    const quantity = positiveInt(this.data.handoverQuantity);
    if (!ticket || !source || !target || !receiverId) {
      toast('请完整选择菲票、节点和接收人');
      return;
    }
    if (!quantity) { toast('下发数量须为正整数'); return; }
    const payload = {
      kind: 'cutting', source_node: source.id, target_node: target.id,
      source_node_name: source.stage_name, target_node_name: target.stage_name,
      remark: this.data.handoverRemark.trim(),
      targets: [{
        receiver: receiverId, target_group_no: null,
        receiver_name: employee ? employee.name : `用户 ${receiverId}`,
        items: [{ ticket: ticket.id, quantity, ticket_label: ticket.label }]
      }]
    };
    wx.navigateTo({
      url: `/packages/admin/handovers/create/index?payload=${encodeURIComponent(JSON.stringify(payload))}`
    });
  },

  async onSubmitHandover() {
    if (!this.data.canCuttingManage || this.data.handoverSaving) return;
    const ticket = this.currentTicket('handoverTicketIndex');
    const source = this.data.handoverSources[this.data.handoverSourceIndex];
    const target = this.data.handoverTargets[this.data.handoverTargetIndex - 1];
    const employee = this.data.employees[this.data.receiverIndex - 1];
    const receiverId = employee ? employee.user_id : positiveInt(this.data.receiverId);
    const quantity = positiveInt(this.data.handoverQuantity);
    if (!ticket || !source || !target || !receiverId) { toast('请完整选择菲票、节点和接收人'); return; }
    if (!quantity) { toast('下发数量须为正整数'); return; }
    this.setData({ handoverSaving: true });
    try {
      await createHandover({
        ticket_id: ticket.id, source_node_id: source.id, target_node_id: target.id,
        receiver_id: receiverId, quantity, request_id: intentIds.handover,
        remark: this.data.handoverRemark.trim()
      });
      intentIds.handover = createRequestId('wx-cut');
      wx.showToast({ title: '裁片已下发', icon: 'success' });
      this.setData({ handoverQuantity: '', handoverRemark: '' });
      this.loadHandovers();
    } catch (error) {
      if (!shouldRetrySameIntent(error)) intentIds.handover = createRequestId('wx-cut');
    } finally {
      this.setData({ handoverSaving: false });
    }
  },

  async onSignHandover(e: WechatMiniprogram.TouchEvent) {
    const id = Number(e.currentTarget.dataset.id);
    const row = this.data.handovers.find((item) => item.id === id);
    if (!row || !row.can_sign || this.data.handoverActionId) return;
    this.setData({ handoverActionId: id });
    try {
      await signHandover(id);
      wx.showToast({ title: '裁片已签收', icon: 'success' });
      const route = this.data.route;
      if (route) await this.selectRoute(route);
      this.setData({ activeTab: 'cutting' });
      await this.loadHandovers();
    } catch (error) { /* 请求层提示 */ } finally {
      this.setData({ handoverActionId: 0 });
    }
  },

  async onCancelHandover(e: WechatMiniprogram.TouchEvent) {
    if (!this.data.canCuttingManage || this.data.handoverActionId) return;
    const id = Number(e.currentTarget.dataset.id);
    const modal = await wx.showModal({ title: '取消裁片下发', content: '仅待签收且未产生流水的单据可取消。' });
    if (!modal.confirm) return;
    this.setData({ handoverActionId: id });
    try {
      await cancelHandover(id);
      wx.showToast({ title: '已取消', icon: 'success' });
      this.loadHandovers();
    } catch (error) { /* 请求层提示 */ } finally {
      this.setData({ handoverActionId: 0 });
    }
  },

  // ================= 包装与成品入库 =================
  onTaskNoInput(e: WechatMiniprogram.Input) { this.setData({ taskNo: e.detail.value }); },
  onPackingRemarkInput(e: WechatMiniprogram.Input) { this.setData({ packingRemark: e.detail.value }); },
  onBoxNoInput(e: WechatMiniprogram.Input) {
    const boxIndex = Number(e.currentTarget.dataset.boxIndex);
    this.setData({ [`packingBoxes[${boxIndex}].boxNo`]: e.detail.value });
  },
  onPackingTicketChange(e: WechatMiniprogram.PickerChange) {
    const boxIndex = Number(e.currentTarget.dataset.boxIndex);
    const rowIndex = Number(e.currentTarget.dataset.rowIndex);
    this.setData({ [`packingBoxes[${boxIndex}].rows[${rowIndex}].ticketIndex`]: Number(e.detail.value) });
  },
  onPackingQuantityInput(e: WechatMiniprogram.Input) {
    const boxIndex = Number(e.currentTarget.dataset.boxIndex);
    const rowIndex = Number(e.currentTarget.dataset.rowIndex);
    this.setData({ [`packingBoxes[${boxIndex}].rows[${rowIndex}].quantity`]: e.detail.value });
  },
  onAddPackingBox() {
    this.setData({ packingBoxes: this.data.packingBoxes.concat([
      { boxNo: '', rows: [{ ticketIndex: 0, quantity: '' }] }
    ]) });
  },
  onRemovePackingBox(e: WechatMiniprogram.TouchEvent) {
    if (this.data.packingBoxes.length <= 1) { toast('包装任务至少保留一箱'); return; }
    const boxes = this.data.packingBoxes.slice();
    boxes.splice(Number(e.currentTarget.dataset.boxIndex), 1);
    this.setData({ packingBoxes: boxes });
  },
  onAddPackingRow(e: WechatMiniprogram.TouchEvent) {
    const boxIndex = Number(e.currentTarget.dataset.boxIndex);
    const boxes = this.data.packingBoxes.slice();
    boxes[boxIndex] = { ...boxes[boxIndex], rows: boxes[boxIndex].rows.concat([{ ticketIndex: 0, quantity: '' }]) };
    this.setData({ packingBoxes: boxes });
  },
  onRemovePackingRow(e: WechatMiniprogram.TouchEvent) {
    const boxIndex = Number(e.currentTarget.dataset.boxIndex);
    const rowIndex = Number(e.currentTarget.dataset.rowIndex);
    const box = this.data.packingBoxes[boxIndex];
    if (!box || box.rows.length <= 1) { toast('每箱至少保留一条菲票明细'); return; }
    const boxes = this.data.packingBoxes.slice();
    boxes[boxIndex] = { ...box, rows: box.rows.filter((_, index) => index !== rowIndex) };
    this.setData({ packingBoxes: boxes });
  },

  async onSubmitPacking() {
    if (!this.data.canFinishedManage || this.data.packingSaving || !this.data.route) return;
    const boxNumbers: string[] = [];
    const boxes: Array<{ box_no: string; items: Array<{ ticket_id: number; quantity: number }> }> = [];
    for (const box of this.data.packingBoxes) {
      const boxNo = box.boxNo.trim();
      if (!boxNo) { toast('请填写每箱箱号'); return; }
      if (boxNumbers.indexOf(boxNo) >= 0) { toast('同一任务内箱号不能重复'); return; }
      boxNumbers.push(boxNo);
      const seen: number[] = [];
      const items: Array<{ ticket_id: number; quantity: number }> = [];
      for (const row of box.rows) {
        const ticket = this.data.tickets[row.ticketIndex || 0];
        const quantity = positiveInt(row.quantity);
        if (!ticket || !quantity) { toast('请完整填写包装菲票和正整数数量'); return; }
        if (seen.indexOf(ticket.id) >= 0) { toast('同一箱内菲票不能重复'); return; }
        seen.push(ticket.id);
        items.push({ ticket_id: ticket.id, quantity });
      }
      boxes.push({ box_no: boxNo, items });
    }
    this.setData({ packingSaving: true });
    try {
      await createPackingTask({
        route_id: this.data.route.id, task_no: this.data.taskNo.trim(), remark: this.data.packingRemark.trim(),
        boxes, request_id: intentIds.packing
      });
      intentIds.packing = createRequestId('wx-pack');
      wx.showToast({ title: '包装任务已创建', icon: 'success' });
      this.setData({
        taskNo: '', packingRemark: '',
        packingBoxes: [{ boxNo: '', rows: [{ ticketIndex: 0, quantity: '' }] }]
      });
      this.loadPacking();
    } catch (error) {
      if (!shouldRetrySameIntent(error)) intentIds.packing = createRequestId('wx-pack');
    } finally {
      this.setData({ packingSaving: false });
    }
  },

  async onApprovePacking(e: WechatMiniprogram.TouchEvent) {
    if (!this.data.canFinishedManage || this.data.packingApprovalId) return;
    const id = Number(e.currentTarget.dataset.id);
    const key = `packing-${id}`;
    if (!approvalIds[key]) approvalIds[key] = createRequestId('wx-pack-ok');
    this.setData({ packingApprovalId: id });
    try {
      await approvePackingTask(id, approvalIds[key]);
      delete approvalIds[key];
      wx.showToast({ title: '包装已审核', icon: 'success' });
      const route = this.data.route;
      if (route) await this.selectRoute(route);
      this.setData({ activeTab: 'packing' });
      await this.loadPacking();
    } catch (error) {
      if (!shouldRetrySameIntent(error)) delete approvalIds[key];
    } finally {
      this.setData({ packingApprovalId: 0 });
    }
  },

  onDocumentNoInput(e: WechatMiniprogram.Input) { this.setData({ documentNo: e.detail.value }); },
  onWarehouseNameInput(e: WechatMiniprogram.Input) { this.setData({ warehouseName: e.detail.value }); },
  onLocationNameInput(e: WechatMiniprogram.Input) { this.setData({ locationName: e.detail.value }); },
  onInboundRemarkInput(e: WechatMiniprogram.Input) { this.setData({ inboundRemark: e.detail.value }); },
  onInboundOptionChange(e: WechatMiniprogram.PickerChange) {
    const index = Number(e.currentTarget.dataset.index);
    this.setData({ [`inboundRows[${index}].optionIndex`]: Number(e.detail.value) });
  },
  onInboundQuantityInput(e: WechatMiniprogram.Input) {
    const index = Number(e.currentTarget.dataset.index);
    this.setData({ [`inboundRows[${index}].quantity`]: e.detail.value });
  },
  onAddInboundRow() { this.setData({ inboundRows: this.data.inboundRows.concat([{ optionIndex: 0, quantity: '' }]) }); },
  onRemoveInboundRow(e: WechatMiniprogram.TouchEvent) {
    const index = Number(e.currentTarget.dataset.index);
    if (this.data.inboundRows.length <= 1) return;
    this.setData({ inboundRows: this.data.inboundRows.filter((_, rowIndex) => rowIndex !== index) });
  },

  async loadPacking() {
    if (!this.data.route || !this.data.canFinishedView) return;
    this.setData({ listLoading: true });
    try {
      const [packingTasks, inbounds] = await Promise.all([
        fetchPackingTasks(this.data.route.id), fetchInbounds(this.data.route.id)
      ]);
      const options: InboundOption[] = [];
      const reserved: Record<string, number> = {};
      inbounds.filter((document) => document.status !== 'reversed').forEach((document) => document.lines.forEach((line) => {
        const key = `${line.box}-${line.ticket}`;
        reserved[key] = (reserved[key] || 0) + line.quantity;
      }));
      packingTasks.filter((task) => task.status === 'approved').forEach((task) => {
        task.boxes.forEach((box) => box.lines.forEach((line) => {
          const remaining = line.quantity - (reserved[`${box.id}-${line.ticket}`] || 0);
          if (remaining > 0) options.push({
            boxId: box.id, ticketId: line.ticket, quantity: remaining,
            label: `${task.task_no} · ${box.box_no} · 第${line.bundle_no}扎 ${line.color}/${line.size} · 可入${remaining}`
          });
        }));
      });
      this.setData({
        packingTasks, inbounds, inboundOptions: options,
        inboundOptionLabels: ['请选择已审核箱内明细'].concat(options.map((item) => item.label)),
        listLoading: false
      });
    } catch (error) { this.setData({ listLoading: false }); }
  },

  async onSubmitInbound() {
    if (!this.data.canFinishedManage || this.data.inboundSaving || !this.data.route) return;
    const seen: string[] = [];
    const lines: Array<{ box_id: number; ticket_id: number; quantity: number }> = [];
    for (const row of this.data.inboundRows) {
      const option = this.data.inboundOptions[(row.optionIndex || 0) - 1];
      const quantity = positiveInt(row.quantity);
      if (!option || !quantity) { toast('请选择箱内明细并填写正整数数量'); return; }
      if (quantity > option.quantity) { toast(`本次入库不能超过可入数量 ${option.quantity}`); return; }
      const key = `${option.boxId}-${option.ticketId}`;
      if (seen.indexOf(key) >= 0) { toast('同一箱同一菲票不能重复'); return; }
      seen.push(key);
      lines.push({ box_id: option.boxId, ticket_id: option.ticketId, quantity });
    }
    this.setData({ inboundSaving: true });
    try {
      await createInbound({
        route_id: this.data.route.id, document_no: this.data.documentNo.trim(), lines,
        warehouse_name: this.data.warehouseName.trim(), location_name: this.data.locationName.trim(),
        remark: this.data.inboundRemark.trim(), request_id: intentIds.inbound
      });
      intentIds.inbound = createRequestId('wx-inbound');
      wx.showToast({ title: '入库单已创建', icon: 'success' });
      this.setData({ documentNo: '', warehouseName: '', locationName: '', inboundRemark: '', inboundRows: [{ optionIndex: 0, quantity: '' }] });
      this.loadPacking();
    } catch (error) {
      if (!shouldRetrySameIntent(error)) intentIds.inbound = createRequestId('wx-inbound');
    } finally {
      this.setData({ inboundSaving: false });
    }
  },

  async onApproveInbound(e: WechatMiniprogram.TouchEvent) {
    if (!this.data.canFinishedManage || this.data.inboundApprovalId) return;
    const id = Number(e.currentTarget.dataset.id);
    const key = `inbound-${id}`;
    if (!approvalIds[key]) approvalIds[key] = createRequestId('wx-in-ok');
    this.setData({ inboundApprovalId: id });
    try {
      await approveInbound(id, approvalIds[key]);
      delete approvalIds[key];
      wx.showToast({ title: '成品已入库', icon: 'success' });
      const route = this.data.route;
      if (route) await this.selectRoute(route);
      this.setData({ activeTab: 'packing' });
      await this.loadPacking();
    } catch (error) {
      if (!shouldRetrySameIntent(error)) delete approvalIds[key];
    } finally {
      this.setData({ inboundApprovalId: 0 });
    }
  },

  async onShowFinishedDetail(e: WechatMiniprogram.TouchEvent) {
    const id = Number(e.currentTarget.dataset.id);
    const kind = e.currentTarget.dataset.kind as 'packing' | 'inbound';
    const key = `${kind}-${id}`;
    if (this.data.finishedDetailKey === key) {
      this.setData({ finishedDetailKey: '', finishedDetail: null });
      return;
    }
    this.setData({ finishedDetailKey: key, finishedDetail: null, finishedDetailLoading: true });
    try {
      const detail = kind === 'packing' ? await fetchPackingTask(id) : await fetchInbound(id);
      if (this.data.finishedDetailKey === key) this.setData({ finishedDetail: detail });
    } catch (error) { /* 请求层统一提示 */ } finally {
      if (this.data.finishedDetailKey === key) this.setData({ finishedDetailLoading: false });
    }
  },

  async onReverseFinishedDocument(e: WechatMiniprogram.TouchEvent) {
    if (!this.data.canFinishedManage || this.data.documentReverseKey) return;
    const id = Number(e.currentTarget.dataset.id);
    const kind = e.currentTarget.dataset.kind as 'packing' | 'inbound';
    const modal = await wx.showModal({
      title: kind === 'packing' ? '冲销包装任务' : '冲销成品入库单',
      content: '', editable: true, placeholderText: '请输入冲销原因（保留原单及流水）',
      confirmText: '确认冲销'
    });
    const reason = (modal.content || '').trim();
    if (!modal.confirm) return;
    if (!reason) { toast('冲销原因不能为空'); return; }
    const key = `document-reverse-${kind}-${id}`;
    if (!approvalIds[key]) approvalIds[key] = createRequestId('wx-doc-rev');
    this.setData({ documentReverseKey: key });
    try {
      if (kind === 'packing') await reversePackingTask(id, approvalIds[key], reason);
      else await reverseInbound(id, approvalIds[key], reason);
      delete approvalIds[key];
      wx.showToast({ title: '单证已冲销', icon: 'success' });
      const route = this.data.route;
      if (route) await this.selectRoute(route);
      this.setData({ activeTab: 'packing' });
      await this.loadPacking();
    } catch (error) {
      if (!shouldRetrySameIntent(error)) delete approvalIds[key];
    } finally {
      this.setData({ documentReverseKey: '' });
    }
  },

  // ================= 成品库存 =================
  onInventoryBedInput(e: WechatMiniprogram.Input) { this.setData({ inventoryBedNo: e.detail.value }); },
  onInventoryColorInput(e: WechatMiniprogram.Input) { this.setData({ inventoryColor: e.detail.value }); },
  onInventorySizeInput(e: WechatMiniprogram.Input) { this.setData({ inventorySize: e.detail.value }); },
  onIncludeZeroChange(e: WechatMiniprogram.SwitchChange) { this.setData({ includeZero: e.detail.value }); },

  async loadInventory() {
    if (!this.data.route || !this.data.canFinishedView) return;
    this.setData({ listLoading: true });
    try {
      const res = await fetchFinishedInventory({
        route_id: this.data.route.id,
        bed_no: this.data.inventoryBedNo.trim() || undefined,
        color: this.data.inventoryColor.trim() || undefined,
        size: this.data.inventorySize.trim() || undefined,
        include_zero: this.data.includeZero
      });
      this.setData({ inventory: res.results || [], inventoryTotal: res.total_available || 0, listLoading: false });
    } catch (error) { this.setData({ listLoading: false }); }
  },

  // ================= 产前工艺、样衣、评审与交底 =================
  setTechPackForm(pack: TechPack | null) {
    this.setData({
      techPackMode: pack ? 'update' : 'create',
      techPackVersion: '',
      techPackPatternNo: pack ? pack.pattern_no : '',
      techPackRequirements: pack ? pack.requirements : '',
      measurementRows: pack && pack.measurements.length ? pack.measurements.map((row) => {
        const parts = row.point.split(' / ');
        return {
          item: parts.length > 1 ? parts.shift() || '' : '',
          point: parts.join(' / ') || row.point,
          size: row.size,
          unit: row.unit || 'cm',
          value: String(row.value),
          tolerancePlus: String(row.tolerance_plus),
          toleranceMinus: String(row.tolerance_minus)
        };
      }) : [emptyMeasurement()]
    });
  },

  async loadPreproduction() {
    if (!this.data.orderId || !this.data.canPreView) return;
    this.setData({ listLoading: true });
    if (this.data.canPreManage && !this.data.employees.length) {
      fetchEmployees().then((employees) => this.setData({
        employees,
        preEmployeeLabels: ['请选择员工'].concat(employees.map((item) => `${item.name}（用户 ${item.user_id}）`))
      })).catch(() => undefined);
    } else if (this.data.employees.length) {
      this.setData({
        preEmployeeLabels: ['请选择员工'].concat(this.data.employees.map((item) => `${item.name}（用户 ${item.user_id}）`))
      });
    }
    try {
      const [preStatus, techPacks] = await Promise.all([
        fetchPreproductionStatus(this.data.orderId), fetchTechPacks(this.data.orderId)
      ]);
      const currentTechPack = techPacks.find((item) => item.is_current) || null;
      const currentUser = getApp<IAppOption>().globalData.user;
      this.setData({
        preStatus, techPacks, currentTechPack,
        currentUserId: currentUser ? currentUser.id : 0,
        listLoading: false
      });
      this.setTechPackForm(currentTechPack);
    } catch (error) { this.setData({ listLoading: false }); }
  },

  onOpenPreproduction() {
    this.setData({ activeTab: 'preproduction' });
    this.loadPreproduction();
  },

  onNewTechPack() {
    if (!this.data.canPreManage) return;
    this.setTechPackForm(null);
  },

  onEditCurrentTechPack() {
    if (!this.data.canPreManage) return;
    this.setTechPackForm(this.data.currentTechPack);
  },

  onTechPackVersionInput(e: WechatMiniprogram.Input) { this.setData({ techPackVersion: e.detail.value }); },
  onTechPackPatternNoInput(e: WechatMiniprogram.Input) { this.setData({ techPackPatternNo: e.detail.value }); },
  onTechPackRequirementsInput(e: WechatMiniprogram.Input) { this.setData({ techPackRequirements: e.detail.value }); },
  onMeasurementInput(e: WechatMiniprogram.Input) {
    const index = Number(e.currentTarget.dataset.index);
    const field = e.currentTarget.dataset.field as keyof MeasurementEditorRow;
    if (!['item', 'point', 'size', 'unit', 'value', 'tolerancePlus', 'toleranceMinus'].includes(field)) return;
    this.setData({ [`measurementRows[${index}].${field}`]: e.detail.value });
  },
  onAddMeasurementRow() {
    if (!this.data.canPreManage) return;
    this.setData({ measurementRows: this.data.measurementRows.concat([emptyMeasurement()]) });
  },
  onRemoveMeasurementRow(e: WechatMiniprogram.TouchEvent) {
    if (!this.data.canPreManage || this.data.measurementRows.length <= 1) return;
    const index = Number(e.currentTarget.dataset.index);
    this.setData({ measurementRows: this.data.measurementRows.filter((_, rowIndex) => rowIndex !== index) });
  },

  async onSaveTechPack() {
    if (!this.data.canPreManage || this.data.techPackSaving || !this.data.orderId) return;
    if (!this.data.techPackPatternNo.trim() || !this.data.techPackRequirements.trim()) {
      toast('请填写纸样号和工艺要求'); return;
    }
    const measurements = [];
    for (const row of this.data.measurementRows) {
      const point = row.item.trim() && row.point.trim()
        ? `${row.item.trim()} / ${row.point.trim()}` : row.item.trim() || row.point.trim();
      if (!point || !row.size.trim() || !row.value.trim()) {
        toast('请完整填写测量项/部位、尺码和尺寸'); return;
      }
      if (!decimalText(row.value) || !decimalText(row.tolerancePlus) || !decimalText(row.toleranceMinus)) {
        toast('尺寸和公差须为数字'); return;
      }
      measurements.push({
        point, size: row.size.trim(), unit: row.unit.trim() || 'cm', value: row.value.trim(),
        tolerance_plus: row.tolerancePlus.trim(), tolerance_minus: row.toleranceMinus.trim()
      });
    }
    const current = this.data.currentTechPack;
    const payload = {
      pattern_no: this.data.techPackPatternNo.trim(),
      requirements: this.data.techPackRequirements.trim(),
      challenges: current ? current.challenges : '',
      common_mistakes: current ? current.common_mistakes : '',
      quality_standards: current ? current.quality_standards : '',
      measurements,
      pattern_files: this.data.techPackMode === 'update' && current ? current.pattern_files : [],
      tolerances: this.data.techPackMode === 'update' && current ? current.tolerances : {},
      attachments: this.data.techPackMode === 'update' && current ? current.attachments : []
    };
    this.setData({ techPackSaving: true });
    try {
      if (this.data.techPackMode === 'update' && current) {
        await updateTechPack(current.id, payload);
      } else {
        const versionNo = this.data.techPackVersion.trim() ? positiveInt(this.data.techPackVersion) : null;
        if (this.data.techPackVersion.trim() && !versionNo) { toast('版本号须为正整数'); return; }
        await createTechPack({ ...payload, order: this.data.orderId, version_no: versionNo || undefined });
      }
      wx.showToast({ title: this.data.techPackMode === 'update' ? '工艺已保存' : '工艺版本已创建', icon: 'success' });
      await this.loadPreproduction();
    } catch (error) { /* 请求层提示 */ } finally {
      this.setData({ techPackSaving: false });
    }
  },

  onSampleTitleInput(e: WechatMiniprogram.Input) { this.setData({ sampleTitle: e.detail.value }); },
  onSampleAssigneeChange(e: WechatMiniprogram.PickerChange) { this.setData({ sampleAssigneeIndex: Number(e.detail.value) }); },
  async onCreateSampleTask() {
    const pack = this.data.currentTechPack;
    if (!this.data.canPreManage || this.data.sampleSaving || !pack) return;
    if (!this.data.sampleTitle.trim()) { toast('请填写样衣任务名称'); return; }
    const employee = this.data.employees[this.data.sampleAssigneeIndex - 1];
    this.setData({ sampleSaving: true });
    try {
      await createSampleTask(pack.id, { title: this.data.sampleTitle.trim(), assignee_id: employee ? employee.user_id : undefined });
      wx.showToast({ title: '样衣任务已创建', icon: 'success' });
      this.setData({ sampleTitle: '', sampleAssigneeIndex: 0 });
      await this.loadPreproduction();
    } catch (error) { /* 请求层提示 */ } finally {
      this.setData({ sampleSaving: false });
    }
  },

  onOpenSampleResult(e: WechatMiniprogram.TouchEvent) {
    if (!this.data.canPreManage) return;
    const id = Number(e.currentTarget.dataset.id);
    this.setData({
      sampleResultTaskId: this.data.sampleResultTaskId === id ? 0 : id,
      sampleResultIndex: 0, sampleResultNote: ''
    });
  },
  onSampleResultChange(e: WechatMiniprogram.PickerChange) { this.setData({ sampleResultIndex: Number(e.detail.value) }); },
  onSampleResultNoteInput(e: WechatMiniprogram.Input) { this.setData({ sampleResultNote: e.detail.value }); },
  async onSubmitSampleResult(e: WechatMiniprogram.TouchEvent) {
    if (!this.data.canPreManage || this.data.sampleResultSaving) return;
    const taskId = Number(e.currentTarget.dataset.id);
    const result = SAMPLE_RESULTS[this.data.sampleResultIndex].key;
    const note = this.data.sampleResultNote.trim();
    if (result !== 'rework' && !note) { toast('合格/不合格结果须填写说明'); return; }
    this.setData({ sampleResultSaving: true });
    try {
      await recordSampleResult(taskId, { result, note, attachments: [] });
      wx.showToast({ title: '样衣结果已记录', icon: 'success' });
      this.setData({ sampleResultTaskId: 0, sampleResultNote: '', sampleResultIndex: 0 });
      await this.loadPreproduction();
    } catch (error) { /* 请求层提示 */ } finally {
      this.setData({ sampleResultSaving: false });
    }
  },

  onIssueTitleInput(e: WechatMiniprogram.Input) { this.setData({ issueTitle: e.detail.value }); },
  onIssueDetailInput(e: WechatMiniprogram.Input) { this.setData({ issueDetail: e.detail.value }); },
  async onCreateReviewIssue() {
    const pack = this.data.currentTechPack;
    if (!this.data.canPreManage || this.data.issueSaving || !pack) return;
    if (!this.data.issueTitle.trim()) { toast('请填写评审问题标题'); return; }
    this.setData({ issueSaving: true });
    try {
      await createReviewIssue(pack.id, { title: this.data.issueTitle.trim(), detail: this.data.issueDetail.trim() });
      wx.showToast({ title: '评审问题已创建', icon: 'success' });
      this.setData({ issueTitle: '', issueDetail: '' });
      await this.loadPreproduction();
    } catch (error) { /* 请求层提示 */ } finally {
      this.setData({ issueSaving: false });
    }
  },

  async onRectifyReviewIssue(e: WechatMiniprogram.TouchEvent) {
    if (!this.data.canPreManage || this.data.preActionId) return;
    const id = Number(e.currentTarget.dataset.id);
    const modal = await wx.showModal({ title: '提交整改', editable: true, placeholderText: '整改说明（必填）' });
    if (!modal.confirm) return;
    const rectification = (modal.content || '').trim();
    if (!rectification) { toast('请填写整改说明'); return; }
    this.setData({ preActionId: id });
    try {
      await rectifyReviewIssue(id, rectification);
      wx.showToast({ title: '整改已提交', icon: 'success' });
      await this.loadPreproduction();
    } catch (error) { /* 请求层提示 */ } finally {
      this.setData({ preActionId: 0 });
    }
  },

  async onReviewReviewIssue(e: WechatMiniprogram.TouchEvent) {
    if (!this.data.canPreManage || this.data.preActionId) return;
    const id = Number(e.currentTarget.dataset.id);
    const close = e.currentTarget.dataset.close !== '0';
    const modal = await wx.showModal({
      title: close ? '复审并关闭' : '复审退回', editable: true,
      placeholderText: close ? '复审意见（可选）' : '退回说明（可选）'
    });
    if (!modal.confirm) return;
    this.setData({ preActionId: id });
    try {
      await reviewReviewIssue(id, close, (modal.content || '').trim());
      wx.showToast({ title: close ? '问题已关闭' : '已退回整改', icon: 'success' });
      await this.loadPreproduction();
    } catch (error) { /* 请求层提示 */ } finally {
      this.setData({ preActionId: 0 });
    }
  },

  onBriefingEmployeeChange(e: WechatMiniprogram.PickerChange) { this.setData({ briefingEmployeeIndex: Number(e.detail.value) }); },
  onBriefingNoteInput(e: WechatMiniprogram.Input) { this.setData({ briefingNote: e.detail.value }); },
  async onAddBriefingTarget() {
    const pack = this.data.currentTechPack;
    if (!this.data.canPreManage || this.data.briefingSaving || !pack) return;
    const employee = this.data.employees[this.data.briefingEmployeeIndex - 1];
    if (!employee) { toast('请选择交底员工'); return; }
    this.setData({ briefingSaving: true });
    try {
      await addBriefingTarget(pack.id, employee.user_id, this.data.briefingNote.trim());
      wx.showToast({ title: '交底员工已添加', icon: 'success' });
      this.setData({ briefingEmployeeIndex: 0, briefingNote: '' });
      await this.loadPreproduction();
    } catch (error) { /* 请求层提示 */ } finally {
      this.setData({ briefingSaving: false });
    }
  },

  async onSignBriefingTarget(e: WechatMiniprogram.TouchEvent) {
    if (this.data.preActionId) return;
    const id = Number(e.currentTarget.dataset.id);
    const target = this.data.currentTechPack && this.data.currentTechPack.briefing_targets.find((item) => item.id === id);
    if (!target || target.employee !== this.data.currentUserId || target.receipt) return;
    const modal = await wx.showModal({ title: '本人签收交底', editable: true, placeholderText: '签收备注（可选）' });
    if (!modal.confirm) return;
    this.setData({ preActionId: id });
    try {
      await signBriefingTarget(id, (modal.content || '').trim());
      wx.showToast({ title: '交底已签收', icon: 'success' });
      await this.loadPreproduction();
    } catch (error) { /* 请求层提示 */ } finally {
      this.setData({ preActionId: 0 });
    }
  },

  async onApproveTechPack(e: WechatMiniprogram.TouchEvent) {
    if (!this.data.canPreApprove || this.data.preApprovalId) return;
    const id = Number(e.currentTarget.dataset.id);
    const approved = e.currentTarget.dataset.approved !== '0';
    const modal = await wx.showModal({
      title: approved ? '批准产前工艺' : '驳回产前工艺',
      content: approved ? '确认当前工艺版本及产前闭环均已满足？' : '请填写驳回原因。',
      editable: !approved,
      placeholderText: '驳回原因'
    });
    if (!modal.confirm) return;
    if (!approved && !(modal.content || '').trim()) { toast('请填写驳回原因'); return; }
    this.setData({ preApprovalId: id });
    try {
      await decideTechPack(id, approved, (modal.content || '').trim());
      wx.showToast({ title: approved ? '已批准' : '已驳回', icon: 'success' });
      await this.loadPreproduction();
    } catch (error) { /* 请求层提示 */ } finally {
      this.setData({ preApprovalId: 0 });
    }
  }
});
}
