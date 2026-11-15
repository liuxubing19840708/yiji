/**
 * AI 问报：保留自然语言与语音输入，并提供受权限控制的动态报表、导出和定时运行中心。
 * 所有业务数字均由后端白名单计划计算；图表只是已执行结果的另一种展示。
 */
import { requireLogin } from '../../utils/auth';
import {
  queryAiReport, AiReportCard, AiHistoryTurn, AiCatalog, AiDatasetSpec,
  AiMetricChoice, AiQueryPlan, AiExecuteResult, AiSavedReport, AiExportTask,
  AiSchedule, AiScheduleRun, AiScheduleInput, fetchAiCatalog, executeAiPlan,
  fetchSavedAiReports, createSavedAiReport, createAiExportTask,
  fetchAiExportTasks, downloadAiExportTask, fetchAiSchedules, createAiSchedule,
  setAiScheduleEnabled, deleteAiSchedule, fetchAiScheduleRuns, createAiRequestId,
  runAiSchedule, retryAiScheduleRun
} from '../../services/ai';
import { getSpeechManager, SpeechManager } from '../../utils/speech';

interface ChatMessage {
  id: number;
  role: 'user' | 'ai';
  text?: string;
  report?: AiReportCard;
  plan?: AiQueryPlan;
  displayRows?: string[][];
  quickQuestions?: string[];
  timeText: string;
}

interface DisplayCellRow { cells: string[] }
interface ChartBar { label: string; value: string; width: number; negative: boolean }
interface ChartPoint { x: number; y: number; label: string; value: string }
interface ChartSegment { x: number; y: number; width: number; angle: number }
type ExportTaskView = AiExportTask & { statusText: string };
type ScheduleView = AiSchedule & { scheduleText: string; nextRunText: string; sourceText: string };
type RunView = AiScheduleRun & {
  kindText: string; statusText: string; scheduledText: string;
  startedText: string; finishedText: string; canRetry: boolean;
};

let msgSeq = 1;
let speech: SpeechManager | null = null;
let speechBound = false;
let exportTimer: ReturnType<typeof setTimeout> | null = null;

function nowText(): string {
  const d = new Date();
  const p = (n: number) => (n < 10 ? `0${n}` : `${n}`);
  return `${p(d.getHours())}:${p(d.getMinutes())}`;
}

function isoDate(date: Date): string {
  const p = (n: number) => String(n < 10 ? `0${n}` : n);
  return `${date.getFullYear()}-${p(date.getMonth() + 1)}-${p(date.getDate())}`;
}

function monthRange(): { from: string; to: string } {
  const now = new Date();
  return { from: isoDate(new Date(now.getFullYear(), now.getMonth(), 1)), to: isoDate(now) };
}

function errorText(error: unknown, fallback = '操作失败，请稍后重试'): string {
  return error instanceof Error && error.message ? error.message : fallback;
}

function dateTimeText(value: string | null): string {
  return value ? value.replace('T', ' ').slice(0, 19) : '—';
}

const runStatuses: Record<string, string> = {
  pending: '等待执行', running: '正在执行', success: '执行成功',
  denied: '权限拒绝', invalid: '计划无效', failed: '执行失败'
};
const runKinds: Record<string, string> = { natural: '自然调度', manual: '手动执行', retry: '失败重试' };
const exportStatuses: Record<string, string> = {
  pending: '等待处理', running: '正在生成', success: '可下载', failed: '生成失败', expired: '已过期'
};

Page({
  data: {
    activeTab: 'chat' as 'chat' | 'designer' | 'schedules',
    messages: [] as ChatMessage[],
    input: '', sending: false, scrollInto: '', voiceEnabled: false,
    recording: false, recognizeText: '',

    catalog: null as AiCatalog | null,
    catalogLoading: false, catalogError: '',
    datasetIndex: 0, dataset: null as AiDatasetSpec | null,
    dimensions: [] as string[], metricChoices: [] as AiMetricChoice[], metricKeys: [] as string[],
    dateFrom: monthRange().from, dateTo: monthRange().to,
    presentation: 'table' as 'table' | 'bar' | 'line',
    presentationIndex: 0, presentations: [
      { value: 'table', label: '明细表格' },
      { value: 'bar', label: '柱状图＋表格' },
      { value: 'line', label: '折线图＋表格' }
    ],
    limit: '100', includeDeleted: false, asOf: '',
    executing: false, designerError: '',
    result: null as AiExecuteResult | null,
    displayRows: [] as DisplayCellRow[],
    chartBars: [] as ChartBar[], chartPoints: [] as ChartPoint[], chartSegments: [] as ChartSegment[],
    chartPointString: '', chartMetricLabel: '', chartMin: '0', chartMax: '0',
    savedName: '', savingReport: false, savedReports: [] as AiSavedReport[], ownedReports: [] as AiSavedReport[],
    exportLoading: false, exportTasks: [] as ExportTaskView[],

    schedules: [] as ScheduleView[], runs: [] as RunView[],
    schedulesLoading: false, runsLoading: false,
    scheduleError: '', runsError: '', scheduleSubmitting: false,
    scheduleActionId: 0, runActionKey: '',
    scheduleForm: {
      name: '', source: 'current', savedIndex: 0,
      frequency: 'daily', frequencyIndex: 0,
      hour: '9', minute: '0', weekdayIndex: 0, dayOfMonth: '1', enabled: true
    },
    frequencies: [{ value: 'daily', label: '每天' }, { value: 'weekly', label: '每周' }, { value: 'monthly', label: '每月' }],
    scheduleSourceOptions: ['当前设计器条件', '自己拥有的已保存报表'],
    weekdays: ['周一', '周二', '周三', '周四', '周五', '周六', '周日']
  },

  onLoad() {
    this.setData({ voiceEnabled: !!getSpeechManager() });
  },

  onShow() {
    if (!requireLogin()) return;
    const bar = this.getTabBar && this.getTabBar();
    if (bar) bar.setSelected(-1);
    if (this.data.messages.length === 0) {
      this.pushAiFallback('你好，我是报表助手。试试下面的问题，或直接输入：', null);
    }
  },

  onUnload() {
    if (exportTimer) clearTimeout(exportTimer);
    exportTimer = null;
  },

  onInput(e: WechatMiniprogram.Input) { this.setData({ input: e.detail.value }); },

  onTabTap(e: WechatMiniprogram.TouchEvent) {
    const tab = (e.currentTarget.dataset as { tab: 'chat' | 'designer' | 'schedules' }).tab;
    this.setData({ activeTab: tab });
    if (tab === 'designer' && !this.data.catalog && !this.data.catalogLoading) this.loadCatalog();
    if (tab === 'schedules') {
      if (!this.data.catalog && !this.data.catalogLoading) this.loadCatalog();
      this.loadSavedReports();
      this.refreshSchedules();
    }
  },

  // ================= 自然语言问报与语音（原有能力） =================
  bindSpeechEvents() {
    if (speechBound || !speech) return;
    speechBound = true;
    speech.onRecognize((res) => {
      if (this.data.recording) this.setData({ recognizeText: res.result || '' });
    });
    speech.onStop((res) => {
      const text = (res.result || '').trim();
      if (!text) { wx.showToast({ title: '没听清，再试一次', icon: 'none' }); return; }
      this.setData({ input: (this.data.input + text).slice(0, 200) });
    });
    speech.onError((res) => {
      this.setData({ recording: false, recognizeText: '' });
      const msg = res.msg || '';
      if (msg.indexOf('auth') >= 0 || msg.indexOf('deny') >= 0) {
        wx.showModal({
          title: '需要麦克风权限', content: '请在设置中允许使用麦克风后再试', confirmText: '去设置',
          success: (r) => { if (r.confirm) wx.openSetting({}); }
        });
      } else wx.showToast({ title: '识别失败，请重试', icon: 'none' });
    });
    speech.onEnd(() => this.setData({ recording: false, recognizeText: '' }));
  },

  onMicStart() {
    if (this.data.sending) return;
    if (!speech) speech = getSpeechManager();
    if (!speech) { wx.showToast({ title: '当前环境不支持语音', icon: 'none' }); return; }
    this.bindSpeechEvents();
    this.setData({ recording: true, recognizeText: '' });
    speech.start({ lang: 'zh_CN', duration: 30000 });
  },

  onMicEnd() { if (this.data.recording && speech) speech.stop(); },
  onHide() {
    if (this.data.recording && speech) speech.stop();
    if (exportTimer) clearTimeout(exportTimer);
    exportTimer = null;
  },
  onQuickTap(e: WechatMiniprogram.TouchEvent) {
    this.send((e.currentTarget.dataset as { q: string }).q);
  },
  onSendTap() { this.send(this.data.input.trim()); },

  buildHistory(): AiHistoryTurn[] {
    return this.data.messages.slice(-4).map(m => ({
      role: m.role,
      content: m.report ? `${m.report.title}：${m.report.summary}`.slice(0, 200) : (m.text || '').slice(0, 200)
    })).filter(t => t.content);
  },

  async send(question: string) {
    if (!question || this.data.sending) return;
    this.setData({ sending: true, input: '' });
    const history = this.buildHistory();
    this.pushUser(question);
    const thinking = this.pushAiThinking();
    const thinkTimer = setTimeout(() => {
      const i = this.data.messages.findIndex(m => m.id === thinking.id);
      if (i >= 0) this.updateMessage(i, { text: '快好了，正在汇总数据…' });
    }, 4000);
    try {
      const res = await queryAiReport(question, history);
      const idx = this.data.messages.findIndex(m => m.id === thinking.id);
      if (res.fallback) {
        this.updateMessage(idx, { text: '这个问题我还没学会，先从下面这些问起吧：', quickQuestions: res.quick_questions || [] });
      } else if (res.free && res.answer) {
        this.updateMessage(idx, { text: res.answer });
      } else {
        this.updateMessage(idx, {
          report: res.report, plan: res.plan,
          displayRows: res.report ? this.makeDisplayRows(res.report) : []
        });
      }
    } catch (e) {
      const idx = this.data.messages.findIndex(m => m.id === thinking.id);
      this.updateMessage(idx, { text: errorText(e, '网络开小差了，请稍后再试。') });
    } finally {
      clearTimeout(thinkTimer);
      this.setData({ sending: false });
    }
  },

  pushUser(text: string) {
    this.appendMessage({ id: msgSeq++, role: 'user', text, timeText: nowText() });
  },
  pushAiThinking() {
    const msg: ChatMessage = { id: msgSeq++, role: 'ai', text: '正在查数…', timeText: nowText() };
    this.appendMessage(msg);
    return msg;
  },
  pushAiFallback(text: string, quickQuestions: string[] | null) {
    this.appendMessage({
      id: msgSeq++, role: 'ai', text,
      quickQuestions: quickQuestions || ['今日产量多少', '本月产量多少', '订单进度怎么样', '哪些订单快到交期了', '我这个月做了多少工资'],
      timeText: nowText()
    });
  },
  appendMessage(msg: ChatMessage) {
    this.setData({ messages: this.data.messages.concat(msg), scrollInto: `msg-${msg.id}` });
  },
  updateMessage(idx: number, patch: Partial<ChatMessage>) {
    if (idx < 0) return;
    this.setData({ [`messages[${idx}]`]: { ...this.data.messages[idx], ...patch } } as Record<string, unknown>);
  },

  // ================= 动态报表计划 =================
  async loadCatalog() {
    if (this.data.catalogLoading) return;
    this.setData({ catalogLoading: true, catalogError: '' });
    try {
      const catalog = await fetchAiCatalog();
      if (!catalog.datasets.length) throw new Error('当前岗位暂无可用动态报表数据集');
      this.setData({ catalog, datasetIndex: 0 });
      this.resetDataset(catalog.datasets[0]);
      if (catalog.capabilities.export) this.loadExportTasks();
      this.loadSavedReports();
    } catch (e) {
      this.setData({ catalogError: errorText(e, '数据目录加载失败') });
    } finally {
      this.setData({ catalogLoading: false });
    }
  },

  resetDataset(dataset: AiDatasetSpec) {
    const allowedDimensions = new Set(dataset.fields.filter(f => f.can_dimension).map(f => f.key));
    const dimensions = dataset.default_dimensions.filter(k => allowedDimensions.has(k));
    const metricChoices = dataset.default_metrics.map(key => {
      const metric = dataset.metrics.find(m => m.key === key);
      return metric ? { key, aggregation: metric.default_aggregation } : null;
    }).filter((m): m is AiMetricChoice => !!m);
    const range = monthRange();
    this.setData({
      dataset, dimensions, metricChoices, metricKeys: metricChoices.map(m => m.key), dateFrom: range.from, dateTo: range.to,
      includeDeleted: false, asOf: '', result: null, displayRows: [], designerError: '',
      chartBars: [], chartPoints: [], chartPointString: ''
    });
  },

  onDatasetChange(e: WechatMiniprogram.CustomEvent) {
    if (!this.data.catalog) return;
    const index = Number(e.detail.value);
    const dataset = this.data.catalog.datasets[index];
    if (dataset) this.setData({ datasetIndex: index }, () => this.resetDataset(dataset));
  },

  onDimensionTap(e: WechatMiniprogram.TouchEvent) {
    const key = (e.currentTarget.dataset as { key: string }).key;
    const dataset = this.data.dataset;
    if (!dataset) return;
    const selectedMetric = this.data.metricChoices.some(choice =>
      dataset.metrics.find(m => m.key === choice.key)?.requires_unit);
    if (selectedMetric && dataset.unit_dimension === key) {
      wx.showToast({ title: '数量指标必须保留单位分组', icon: 'none' });
      return;
    }
    const dimensions = this.data.dimensions.includes(key)
      ? this.data.dimensions.filter(k => k !== key) : this.data.dimensions.concat(key);
    this.setData({ dimensions });
  },

  onMetricTap(e: WechatMiniprogram.TouchEvent) {
    const dataset = this.data.dataset;
    const key = (e.currentTarget.dataset as { key: string }).key;
    if (!dataset) return;
    const metric = dataset.metrics.find(m => m.key === key);
    if (!metric) return;
    const existing = this.data.metricChoices.some(m => m.key === key);
    let metricChoices = existing
      ? this.data.metricChoices.filter(m => m.key !== key)
      : this.data.metricChoices.concat({ key, aggregation: metric.default_aggregation });
    if (this.data.presentation !== 'table') metricChoices = metricChoices.slice(0, 1);
    const dimensions = this.data.dimensions.slice();
    if (!existing && metric.requires_unit && dataset.unit_dimension && !dimensions.includes(dataset.unit_dimension)) {
      dimensions.push(dataset.unit_dimension);
    }
    this.setData({ metricChoices, metricKeys: metricChoices.map(m => m.key), dimensions });
  },

  onPresentationChange(e: WechatMiniprogram.CustomEvent) {
    const index = Number(e.detail.value);
    const selected = this.data.presentations[index];
    if (!selected) return;
    const presentation = selected.value as 'table' | 'bar' | 'line';
    this.setData({
      presentationIndex: index, presentation,
      metricChoices: presentation === 'table' ? this.data.metricChoices : this.data.metricChoices.slice(0, 1),
      metricKeys: (presentation === 'table' ? this.data.metricChoices : this.data.metricChoices.slice(0, 1)).map(m => m.key)
    });
  },

  onDateInput(e: WechatMiniprogram.CustomEvent) {
    const field = (e.currentTarget.dataset as { field: 'dateFrom' | 'dateTo' | 'asOf' }).field;
    this.setData({ [field]: String(e.detail.value) } as Record<string, unknown>);
  },
  onLimitInput(e: WechatMiniprogram.Input) { this.setData({ limit: e.detail.value }); },
  onIncludeDeletedChange(e: WechatMiniprogram.SwitchChange) { this.setData({ includeDeleted: e.detail.value }); },
  onSavedNameInput(e: WechatMiniprogram.Input) { this.setData({ savedName: e.detail.value }); },

  setDateRange(e: WechatMiniprogram.TouchEvent) {
    const kind = (e.currentTarget.dataset as { kind: string }).kind;
    const now = new Date();
    let start = new Date(now);
    let end = new Date(now);
    if (kind === 'all') { this.setData({ dateFrom: '', dateTo: '' }); return; }
    if (kind === 'week') start = new Date(now.getFullYear(), now.getMonth(), now.getDate() - ((now.getDay() + 6) % 7));
    if (kind === 'month') start = new Date(now.getFullYear(), now.getMonth(), 1);
    if (kind === 'lastMonth') {
      start = new Date(now.getFullYear(), now.getMonth() - 1, 1);
      end = new Date(now.getFullYear(), now.getMonth(), 0);
    }
    this.setData({ dateFrom: isoDate(start), dateTo: isoDate(end) });
  },

  buildPlan(): AiQueryPlan {
    const dataset = this.data.dataset;
    if (!dataset) throw new Error('请先选择数据集');
    if (!this.data.metricChoices.length) throw new Error('请至少选择一个统计指标');
    if (this.data.presentation !== 'table' && this.data.metricChoices.length !== 1) {
      throw new Error('图表模式请只选择一个统计指标');
    }
    if (this.data.presentation !== 'table' && !this.data.dimensions.length) {
      throw new Error('图表模式请至少选择一个分组维度');
    }
    if (this.data.presentation === 'line') {
      const first = dataset.fields.find(f => f.key === this.data.dimensions[0]);
      if (!first || !['date', 'datetime'].includes(first.type)) {
        throw new Error('折线图首维度须为日、周或月');
      }
    }
    if (!!this.data.dateFrom !== !!this.data.dateTo) throw new Error('请同时选择开始日期和结束日期，或选择全部时间');
    const filters = [];
    if (this.data.dateFrom && this.data.dateTo) {
      if (this.data.dateFrom > this.data.dateTo) throw new Error('开始日期不能晚于结束日期');
      filters.push({ field: dataset.time_field, operator: 'between', value: [this.data.dateFrom, this.data.dateTo] });
    }
    const limit = Number(this.data.limit);
    if (!Number.isInteger(limit) || limit < 1 || limit > 500) throw new Error('返回行数须为 1 至 500 的整数');
    const orderField = this.data.presentation === 'line'
      ? this.data.dimensions[0] : (this.data.metricChoices[0]?.key || this.data.dimensions[0] || '');
    const plan: AiQueryPlan = {
      dataset: dataset.key, metrics: this.data.metricChoices.map(m => ({ ...m })),
      dimensions: this.data.dimensions.slice(), filters, limit,
      order_by: orderField ? [{ field: orderField, direction: this.data.presentation === 'line' ? 'asc' : 'desc' }] : [],
      presentation: this.data.presentation
    };
    // 关键权限边界：不支持的数据集（含统一经营现金流）绝不发送 include_deleted。
    if (this.data.catalog?.capabilities.full_data && dataset.supports_include_deleted && this.data.includeDeleted) {
      plan.include_deleted = true;
    }
    if (dataset.supports_as_of && this.data.asOf) plan.as_of = this.data.asOf;
    return plan;
  },

  async executePlan() {
    if (this.data.executing) return;
    this.setData({ executing: true, designerError: '' });
    try {
      const result = await executeAiPlan(this.buildPlan());
      this.setData({ result, displayRows: this.makeDisplayRows(result.report).map(cells => ({ cells })) });
      this.buildChart(result);
    } catch (e) {
      this.setData({ result: null, displayRows: [], designerError: errorText(e, '计划执行失败') });
    } finally {
      this.setData({ executing: false });
    }
  },

  makeDisplayRows(report: AiReportCard): string[][] {
    const columns = report.columns || [];
    return report.rows.map(row => row.map((value, index) => {
      if (value === null || value === undefined || value === '') return '—';
      const column = columns[index];
      const choice = column?.choices?.find(c => c.value === value);
      if (choice) return choice.label;
      if (column && ['money', 'decimal', 'integer', 'percent'].includes(column.type) && typeof value !== 'boolean') {
        const number = Number(value);
        if (Number.isFinite(number)) {
          const text = number.toLocaleString('zh-CN', { maximumFractionDigits: column.type === 'money' ? 2 : 3 });
          return column.type === 'money' ? `¥${text}` : column.type === 'percent' ? `${text}%` : text;
        }
      }
      return String(value);
    }));
  },

  buildChart(result: AiExecuteResult) {
    const presentation = result.plan.presentation;
    if (presentation === 'table') {
      this.setData({ chartBars: [], chartPoints: [], chartPointString: '' });
      return;
    }
    const dimensionCount = result.plan.dimensions.length;
    const metric = result.plan.metrics[0];
    const rows = result.report.rows.slice(0, 30);
    const values = rows.map(row => Number(row[dimensionCount]) || 0);
    const labels = rows.map((row, index) => dimensionCount
      ? result.plan.dimensions.map((_, i) => String(row[i] ?? '—')).join(' / ')
      : `汇总 ${index + 1}`);
    const formatted = this.makeDisplayRows(result.report).slice(0, 30).map(row => row[dimensionCount] || '0');
    const maxAbs = Math.max(1, ...values.map(v => Math.abs(v)));
    const chartBars = rows.map((_, i) => ({
      label: labels[i], value: formatted[i], width: Math.max(1, Math.round(Math.abs(values[i]) / maxAbs * 100)), negative: values[i] < 0
    }));
    const min = Math.min(0, ...values);
    const max = Math.max(0, ...values);
    const span = max - min || 1;
    const chartPoints = rows.map((_, i) => ({
      x: rows.length === 1 ? 300 : 20 + i * 560 / (rows.length - 1),
      y: 190 - (values[i] - min) / span * 160,
      label: labels[i], value: formatted[i]
    }));
    const chartSegments = chartPoints.slice(1).map((point, index) => {
      const previous = chartPoints[index];
      const dx = point.x - previous.x;
      const dy = point.y - previous.y;
      return {
        x: previous.x, y: previous.y, width: Math.sqrt(dx * dx + dy * dy),
        angle: Math.atan2(dy, dx) * 180 / Math.PI
      };
    });
    this.setData({
      chartBars, chartPoints, chartSegments, chartPointString: chartPoints.map(p => `${p.x},${p.y}`).join(' '),
      chartMetricLabel: result.report.columns[dimensionCount]?.label || metric.key,
      chartMin: String(min), chartMax: String(max)
    });
  },

  onChartPointTap(e: WechatMiniprogram.TouchEvent) {
    const data = e.currentTarget.dataset as { label: string; value: string };
    wx.showToast({ title: `${data.label}：${data.value}`.slice(0, 30), icon: 'none' });
  },

  async loadSavedReports() {
    try {
      const result = await fetchSavedAiReports();
      const savedReports = result.results || [];
      this.setData({ savedReports, ownedReports: savedReports.filter(r => r.is_owner) });
    } catch (e) {
      this.setData({ designerError: errorText(e, '已保存报表加载失败') });
    }
  },

  async saveCurrentPlan() {
    if (this.data.savingReport) return;
    this.setData({ savingReport: true, designerError: '' });
    try {
      const name = this.data.savedName.trim();
      if (!name) throw new Error('请输入报表名称');
      const saved = await createSavedAiReport(name, this.buildPlan());
      const savedReports = [saved].concat(this.data.savedReports);
      this.setData({ savedReports, ownedReports: savedReports.filter(r => r.is_owner), savedName: '' });
      wx.showToast({ title: '查询条件已保存', icon: 'none' });
    } catch (e) {
      this.setData({ designerError: errorText(e, '保存报表失败') });
    } finally {
      this.setData({ savingReport: false });
    }
  },

  // ================= 异步导出 =================
  async loadExportTasks() {
    try {
      const result = await fetchAiExportTasks();
      const exportTasks = (result.results || []).map(task => ({ ...task, statusText: exportStatuses[task.status] || task.status }));
      this.setData({ exportTasks });
    } catch (e) {
      this.setData({ designerError: errorText(e, '导出任务加载失败') });
    }
  },

  async exportCurrentPlan() {
    if (this.data.exportLoading || !this.data.result) return;
    this.setData({ exportLoading: true, designerError: '' });
    try {
      const task = await createAiExportTask(this.data.result.plan);
      this.setData({ exportTasks: [{ ...task, statusText: exportStatuses[task.status] || task.status }].concat(this.data.exportTasks).slice(0, 20) });
      wx.showToast({ title: '正在后台生成 Excel', icon: 'none' });
      this.pollExportTask(task.id);
    } catch (e) {
      this.setData({ designerError: errorText(e, '创建导出任务失败') });
    } finally {
      this.setData({ exportLoading: false });
    }
  },

  pollExportTask(id: string) {
    if (exportTimer) clearTimeout(exportTimer);
    exportTimer = setTimeout(async () => {
      exportTimer = null;
      try {
        const result = await fetchAiExportTasks();
        const exportTasks = (result.results || []).map(task => ({ ...task, statusText: exportStatuses[task.status] || task.status }));
        this.setData({ exportTasks });
        const task = exportTasks.find(item => item.id === id);
        if (task && ['pending', 'running'].includes(task.status)) this.pollExportTask(id);
        else if (task?.status === 'success') {
          await downloadAiExportTask(id);
          wx.showToast({ title: 'Excel 已打开，可转发保存', icon: 'none' });
        } else if (task) this.setData({ designerError: task.error_message || '导出失败，请重新创建任务' });
      } catch (e) {
        this.setData({ designerError: errorText(e, '导出状态查询失败，可刷新任务') });
      }
    }, 2000);
  },

  async onDownloadExport(e: WechatMiniprogram.TouchEvent) {
    const id = (e.currentTarget.dataset as { id: string }).id;
    try { await downloadAiExportTask(id); }
    catch (e) { this.setData({ designerError: errorText(e, '下载失败') }); }
  },

  // ================= 定时任务与运行中心 =================
  async refreshSchedules() {
    await Promise.all([this.loadSchedules(), this.loadRuns()]);
  },

  async loadSchedules() {
    if (this.data.schedulesLoading) return;
    this.setData({ schedulesLoading: true, scheduleError: '' });
    try {
      const result = await fetchAiSchedules();
      const schedules = (result.results || []).map(item => ({
        ...item, scheduleText: this.scheduleText(item),
        nextRunText: item.enabled ? `下次执行：${dateTimeText(item.next_run_at)}` : '已停用；启用后重新计算执行时间',
        sourceText: item.saved_report ? `引用：${item.saved_report.name}` : '独立查询计划'
      }));
      this.setData({ schedules });
    } catch (e) {
      this.setData({ scheduleError: errorText(e, '定时任务加载失败') });
    } finally {
      this.setData({ schedulesLoading: false });
    }
  },

  scheduleText(item: AiSchedule): string {
    const time = `${String(item.hour).padStart(2, '0')}:${String(item.minute).padStart(2, '0')}`;
    if (item.frequency === 'weekly') return `每${this.data.weekdays[item.weekday || 0]} ${time}`;
    if (item.frequency === 'monthly') return `每月 ${item.day_of_month} 日 ${time}`;
    return `每天 ${time}`;
  },

  async loadRuns() {
    if (this.data.runsLoading) return;
    this.setData({ runsLoading: true, runsError: '' });
    try {
      const result = await fetchAiScheduleRuns(50);
      this.setData({ runs: (result.results || []).map(run => this.runView(run)) });
    } catch (e) {
      this.setData({ runsError: errorText(e, '运行记录加载失败') });
    } finally {
      this.setData({ runsLoading: false });
    }
  },

  runView(run: AiScheduleRun): RunView {
    return {
      ...run, kindText: runKinds[run.kind] || run.kind,
      statusText: runStatuses[run.status] || '未知状态',
      scheduledText: dateTimeText(run.scheduled_at), startedText: dateTimeText(run.started_at),
      finishedText: dateTimeText(run.finished_at), canRetry: ['failed', 'denied', 'invalid'].includes(run.status)
    };
  },

  onScheduleNameInput(e: WechatMiniprogram.Input) { this.setData({ 'scheduleForm.name': e.detail.value }); },
  onScheduleTimeInput(e: WechatMiniprogram.Input) {
    const field = (e.currentTarget.dataset as { field: 'hour' | 'minute' | 'dayOfMonth' }).field;
    this.setData({ [`scheduleForm.${field}`]: e.detail.value } as Record<string, unknown>);
  },
  onScheduleFrequencyChange(e: WechatMiniprogram.CustomEvent) {
    const frequencyIndex = Number(e.detail.value);
    const frequency = this.data.frequencies[frequencyIndex]?.value || 'daily';
    this.setData({ 'scheduleForm.frequencyIndex': frequencyIndex, 'scheduleForm.frequency': frequency });
  },
  onScheduleWeekdayChange(e: WechatMiniprogram.CustomEvent) {
    this.setData({ 'scheduleForm.weekdayIndex': Number(e.detail.value) });
  },
  onScheduleEnabledChange(e: WechatMiniprogram.SwitchChange) {
    this.setData({ 'scheduleForm.enabled': e.detail.value });
  },
  onScheduleSourceChange(e: WechatMiniprogram.CustomEvent) {
    const source = Number(e.detail.value) === 1 ? 'saved' : 'current';
    if (source === 'saved' && !this.data.savedReports.some(r => r.is_owner)) {
      wx.showToast({ title: '暂无可引用的自有报表', icon: 'none' });
      return;
    }
    this.setData({ 'scheduleForm.source': source });
  },
  onScheduleSavedChange(e: WechatMiniprogram.CustomEvent) {
    this.setData({ 'scheduleForm.savedIndex': Number(e.detail.value) });
  },

  async createSchedule() {
    if (this.data.scheduleSubmitting) return;
    this.setData({ scheduleSubmitting: true, scheduleError: '' });
    try {
      const form = this.data.scheduleForm;
      const name = form.name.trim();
      const hour = Number(form.hour);
      const minute = Number(form.minute);
      if (!name) throw new Error('请输入定时任务名称');
      if (!Number.isInteger(hour) || hour < 0 || hour > 23 || !Number.isInteger(minute) || minute < 0 || minute > 59) {
        throw new Error('小时须为 0–23、分钟须为 0–59 的整数');
      }
      const body: AiScheduleInput = {
        name, frequency: form.frequency as 'daily' | 'weekly' | 'monthly', hour, minute, enabled: form.enabled
      };
      if (body.frequency === 'weekly') body.weekday = form.weekdayIndex;
      if (body.frequency === 'monthly') {
        const day = Number(form.dayOfMonth);
        if (!Number.isInteger(day) || day < 1 || day > 31) throw new Error('每月执行日须为 1 至 31 的整数');
        body.day_of_month = day;
      }
      if (form.source === 'saved') {
        const owned = this.data.savedReports.filter(r => r.is_owner);
        const saved = owned[form.savedIndex];
        if (!saved) throw new Error('请选择自己拥有的已保存报表');
        body.saved_report_id = saved.id;
      } else body.plan = this.buildPlan();
      const created = await createAiSchedule(body);
      this.setData({ 'scheduleForm.name': '' });
      await this.loadSchedules();
      wx.showToast({ title: created.enabled ? '定时报表已创建并启用' : '定时报表已创建', icon: 'none' });
    } catch (e) {
      this.setData({ scheduleError: errorText(e, '创建定时任务失败') });
    } finally {
      this.setData({ scheduleSubmitting: false });
    }
  },

  async onScheduleToggle(e: WechatMiniprogram.TouchEvent) {
    const id = Number((e.currentTarget.dataset as { id: number }).id);
    const item = this.data.schedules.find(s => s.id === id);
    if (!item || this.data.scheduleActionId) return;
    this.setData({ scheduleActionId: id, scheduleError: '' });
    try {
      await setAiScheduleEnabled(id, !item.enabled);
      await this.loadSchedules();
    } catch (e) { this.setData({ scheduleError: errorText(e, '任务状态更新失败') }); }
    finally { this.setData({ scheduleActionId: 0 }); }
  },

  onScheduleDelete(e: WechatMiniprogram.TouchEvent) {
    const id = Number((e.currentTarget.dataset as { id: number }).id);
    const item = this.data.schedules.find(s => s.id === id);
    if (!item || this.data.scheduleActionId) return;
    wx.showModal({
      title: '删除定时任务', content: `确定删除“${item.name}”？历史运行记录可能一并删除。`,
      success: async (res) => {
        if (!res.confirm) return;
        this.setData({ scheduleActionId: id, scheduleError: '' });
        try {
          await deleteAiSchedule(id);
          await this.refreshSchedules();
        } catch (err) { this.setData({ scheduleError: errorText(err, '删除失败') }); }
        finally { this.setData({ scheduleActionId: 0 }); }
      }
    });
  },

  upsertRun(run: AiScheduleRun) {
    const view = this.runView(run);
    const index = this.data.runs.findIndex(r => r.id === run.id);
    const runs = this.data.runs.slice();
    if (index >= 0) runs.splice(index, 1, view); else runs.unshift(view);
    this.setData({ runs: runs.slice(0, 50) });
  },

  async onScheduleRun(e: WechatMiniprogram.TouchEvent) {
    const id = Number((e.currentTarget.dataset as { id: number }).id);
    if (this.data.runActionKey) return;
    this.setData({ runActionKey: `run-${id}`, runsError: '' });
    try {
      const run = await runAiSchedule(id, createAiRequestId());
      this.upsertRun(run);
      wx.showToast({ title: run.status === 'success' ? '执行完成，请从通知查看报表' : '执行未成功，请查看运行记录', icon: 'none' });
    } catch (err) { this.setData({ runsError: errorText(err, '手动执行失败') }); }
    finally { this.setData({ runActionKey: '' }); }
  },

  async onRunRetry(e: WechatMiniprogram.TouchEvent) {
    const id = Number((e.currentTarget.dataset as { id: number }).id);
    if (this.data.runActionKey) return;
    this.setData({ runActionKey: `retry-${id}`, runsError: '' });
    try {
      const run = await retryAiScheduleRun(id, createAiRequestId());
      this.upsertRun(run);
      wx.showToast({ title: run.status === 'success' ? '重试完成' : '重试未成功，已保留新记录', icon: 'none' });
    } catch (err) { this.setData({ runsError: errorText(err, '重试失败') }); }
    finally { this.setData({ runActionKey: '' }); }
  }
});
