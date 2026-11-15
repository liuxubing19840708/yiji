/**
 * 订单产前准备：工艺资料、产前样、工艺问题、交底与一次正式审批。
 * 总状态和审批缺口完全以后端为准；Web 本期按已批准范围暂不同步。
 */
import { requireLogin } from '../../../utils/auth';
import { hasPerm } from '../../../utils/perm';
import { mediaUrl } from '../../../utils/media';
import {
  chooseDocument, chooseImage, uploadDocument, uploadImage
} from '../../../utils/upload';
import { fetchOrder } from '../../../services/orders';
import {
  BriefingConfirmation, CraftTemplate, CraftTemplateKind, PreproductionStatus,
  ReviewIssue, SampleRecord, TechMeasurement, TechPack, confirmBriefing,
  createCraftTemplate, createReviewIssue, createSampleTask, createTechPack,
  decideTechPack, fetchCraftTemplates, fetchPreproductionStatus, fetchTechPacks,
  recordSampleResult, reopenReviewIssue, resolveReviewIssue, updateCraftTemplate,
  updateTechPack
} from '../../../services/full-production';

interface Attachment {
  name: string;
  path: string;
}

interface MeasurementRow {
  size: string;
  point: string;
  unit: string;
  value: string;
  tolerancePlus: string;
  toleranceMinus: string;
}

type AttachmentTarget = 'patternFiles' | 'techFiles' | 'sampleFiles';
type FormField = 'patternNo' | 'requirements' | 'challenges' | 'commonMistakes' |
  'qualityStandards' | 'sampleNote' | 'issueDetail' | 'briefingNote' | 'versionNo';

const SAMPLE_RESULTS = ['合格', '不合格'];
const BASE_SIZES = ['XS', 'S', 'M', 'L', 'XL', '2XL', '3XL', '4XL', '均码'];
const BASE_POINTS = ['衣长', '胸围', '肩宽', '袖长', '袖口', '领围', '腰围', '臀围',
  '裤长', '裙长', '大腿围', '脚口'];
const TEXT_FIELDS = ['requirements', 'challenges', 'common_mistakes', 'quality_standards'] as const;
type TextTemplateField = typeof TEXT_FIELDS[number];

function textFieldName(kind: TextTemplateField): 'requirements' | 'challenges' | 'commonMistakes' | 'qualityStandards' {
  return ({ requirements: 'requirements', challenges: 'challenges',
    common_mistakes: 'commonMistakes', quality_standards: 'qualityStandards' } as const)[kind];
}

function numberOptions(max: number, step: number): string[] {
  const values: string[] = [];
  for (let value = 0; value <= max; value += step) values.push(Number(value.toFixed(1)).toString());
  return values;
}

function uniqueOptions(base: string[], extra: string[]): string[] {
  return base.concat(extra.filter((item) => item && base.indexOf(item) < 0));
}

function emptyMeasurement(): MeasurementRow {
  return { size: 'M', point: '衣长', unit: 'cm', value: '', tolerancePlus: '1', toleranceMinus: '1' };
}

function decimalText(value: string): boolean {
  return /^-?(?:\d+(?:\.\d+)?|\.\d+)$/.test(value.trim());
}

function dateTime(value?: string | null): string {
  return value ? value.replace('T', ' ').slice(0, 16) : '';
}

function toast(title: string): void {
  wx.showToast({ title, icon: 'none' });
}

Page({
  data: {
    orderId: 0,
    orderNo: '',
    loading: true,
    error: '',
    canManage: false,
    canBrief: false,
    canApprove: false,
    status: null as PreproductionStatus | null,
    pack: null as TechPack | null,
    newVersion: false,
    versionNo: '',
    patternNo: '',
    requirements: '',
    challenges: '',
    commonMistakes: '',
    qualityStandards: '',
    measurements: [emptyMeasurement()] as MeasurementRow[],
    patternFiles: [] as Attachment[],
    techFiles: [] as Attachment[],
    sampleResultIndex: 0,
    sampleResults: SAMPLE_RESULTS,
    sampleNote: '',
    sampleFiles: [] as Attachment[],
    sampleHistory: [] as SampleRecord[],
    issueDetail: '',
    briefingNote: '',
    saving: false,
    actionId: 0,
    uploadingTarget: '',
    statusClass: 'badge-gray',
    activeTab: 'craft',
    measurementTemplates: [] as CraftTemplate[],
    requirementsTemplates: [] as CraftTemplate[],
    challengesTemplates: [] as CraftTemplate[],
    commonMistakesTemplates: [] as CraftTemplate[],
    qualityStandardsTemplates: [] as CraftTemplate[],
    sizeOptions: BASE_SIZES,
    pointOptions: BASE_POINTS,
    unitOptions: ['cm', 'mm'],
    valueOptions: numberOptions(200, 0.5),
    toleranceOptions: numberOptions(10, 0.1)
  },

  onLoad(options: Record<string, string>) {
    this.setData({ orderId: Number(options.id) || 0 });
  },

  onShow() {
    if (!requireLogin()) return;
    this.setData({
      canManage: hasPerm('preproduction.manage'),
      canBrief: hasPerm('preproduction.brief'),
      canApprove: hasPerm('preproduction.approve')
    });
    this.reload();
  },

  onPullDownRefresh() {
    this.reload().finally(() => wx.stopPullDownRefresh());
  },

  async reload(): Promise<void> {
    if (!this.data.orderId) {
      this.setData({ loading: false, error: '缺少订单参数' });
      return;
    }
    this.setData({ loading: true, error: '' });
    try {
      const [order, status, packs, templates] = await Promise.all([
        fetchOrder(this.data.orderId),
        fetchPreproductionStatus(this.data.orderId),
        fetchTechPacks(this.data.orderId),
        fetchCraftTemplates()
      ]);
      const pack = packs.find((item) => item.is_current) || null;
      this.setData({
        orderNo: order.order_no,
        status,
        pack,
        sampleHistory: this.flattenSamples(pack),
        measurementTemplates: templates.filter((item) => item.kind === 'measurements'),
        requirementsTemplates: templates.filter((item) => item.kind === 'requirements'),
        challengesTemplates: templates.filter((item) => item.kind === 'challenges'),
        commonMistakesTemplates: templates.filter((item) => item.kind === 'common_mistakes'),
        qualityStandardsTemplates: templates.filter((item) => item.kind === 'quality_standards'),
        statusClass: status.status === 'approved' ? 'badge-green' :
          (status.status === 'reconfirm' ? 'badge-red' : 'badge-gray'),
        loading: false
      });
      this.setPackForm(pack);
    } catch (error) {
      this.setData({ loading: false, error: '产前准备加载失败，请检查权限后重试' });
    }
  },

  setPackForm(pack: TechPack | null, copy = false) {
    const measurements = this.measurementRows(pack ? pack.measurements : []);
    this.setData({
      newVersion: copy,
      versionNo: '',
      patternNo: pack && !copy ? pack.pattern_no : '',
      requirements: pack ? pack.requirements : '',
      challenges: pack ? pack.challenges : '',
      commonMistakes: pack ? pack.common_mistakes : '',
      qualityStandards: pack ? pack.quality_standards : '',
      measurements,
      sizeOptions: uniqueOptions(BASE_SIZES, measurements.map((row) => row.size)),
      pointOptions: uniqueOptions(BASE_POINTS, measurements.map((row) => row.point)),
      unitOptions: uniqueOptions(['cm', 'mm'], measurements.map((row) => row.unit)),
      valueOptions: uniqueOptions(numberOptions(200, 0.5), measurements.map((row) => row.value)),
      toleranceOptions: uniqueOptions(numberOptions(10, 0.1), measurements.reduce(
        (all: string[], row) => all.concat([row.tolerancePlus, row.toleranceMinus]), [])),
      patternFiles: pack ? pack.pattern_files.map((path) => ({
        path, name: path.split('/').pop() || '纸样附件'
      })) : [],
      techFiles: pack ? pack.attachments.map((file) => ({
        path: file.path, name: file.name || file.path.split('/').pop() || '工艺附件'
      })) : []
    });
  },

  measurementRows(rows: TechMeasurement[]): MeasurementRow[] {
    if (!rows.length) return [emptyMeasurement()];
    return rows.map((row) => ({
      size: String(row.size), point: String(row.point), unit: String(row.unit || 'cm'),
      value: String(row.value), tolerancePlus: String(row.tolerance_plus),
      toleranceMinus: String(row.tolerance_minus)
    }));
  },

  flattenSamples(pack: TechPack | null): SampleRecord[] {
    if (!pack) return [];
    return pack.sample_tasks.reduce((all: SampleRecord[], task) => all.concat(task.records || []), [])
      .sort((a, b) => b.id - a.id);
  },

  onInput(e: WechatMiniprogram.Input) {
    const field = e.currentTarget.dataset.field as FormField;
    const allowed: FormField[] = [
      'patternNo', 'requirements', 'challenges', 'commonMistakes', 'qualityStandards',
      'sampleNote', 'issueDetail', 'briefingNote', 'versionNo'
    ];
    if (allowed.indexOf(field) >= 0) this.setData({ [field]: e.detail.value });
  },

  onSwitchTab(e: WechatMiniprogram.TouchEvent) {
    const tab = String(e.currentTarget.dataset.tab || '');
    if (['craft', 'sample', 'issues', 'approval'].indexOf(tab) >= 0) {
      this.setData({ activeTab: tab });
      wx.pageScrollTo({ scrollTop: 0, duration: 0 });
    }
  },

  onMeasurementSelect(e: WechatMiniprogram.PickerChange) {
    if (!this.data.canManage) return;
    const index = Number(e.currentTarget.dataset.index);
    const field = e.currentTarget.dataset.field as keyof MeasurementRow;
    const optionFields = {
      size: 'sizeOptions', point: 'pointOptions', unit: 'unitOptions', value: 'valueOptions',
      tolerancePlus: 'toleranceOptions', toleranceMinus: 'toleranceOptions'
    } as const;
    const optionField = optionFields[field];
    if (!optionField) return;
    const options = this.data[optionField] as string[];
    const value = options[Number(e.detail.value)];
    if (value !== undefined) this.setData({ [`measurements[${index}].${field}`]: value });
  },

  templatesFor(kind: CraftTemplateKind): CraftTemplate[] {
    if (kind === 'measurements') return this.data.measurementTemplates;
    if (kind === 'requirements') return this.data.requirementsTemplates;
    if (kind === 'challenges') return this.data.challengesTemplates;
    if (kind === 'common_mistakes') return this.data.commonMistakesTemplates;
    return this.data.qualityStandardsTemplates;
  },

  onApplyTemplate(e: WechatMiniprogram.PickerChange) {
    if (!this.data.canManage) return;
    const kind = e.currentTarget.dataset.kind as CraftTemplateKind;
    const template = this.templatesFor(kind)[Number(e.detail.value)];
    if (!template) return;
    if (kind === 'measurements') {
      this.setData({ measurements: this.measurementRows(template.content as TechMeasurement[]) });
    } else if (TEXT_FIELDS.indexOf(kind as TextTemplateField) >= 0) {
      const field = textFieldName(kind as TextTemplateField);
      this.setData({ [field]: String(template.content) });
    }
    toast(`已套用：${template.name}`);
  },

  async onSaveTemplate(e: WechatMiniprogram.TouchEvent) {
    if (!this.data.canManage || this.data.saving) return;
    const kind = e.currentTarget.dataset.kind as CraftTemplateKind;
    const modal = await wx.showModal({
      title: kind === 'measurements' ? '保存尺寸模板' : '保存工艺文本模板',
      editable: true, placeholderText: '模板名称；同名公司模板将更新'
    });
    if (!modal.confirm) return;
    const name = (modal.content || '').trim();
    if (!name) { toast('请填写模板名称'); return; }
    let content: TechMeasurement[] | string | null = null;
    if (kind === 'measurements') content = this.measurementPayload();
    else if (TEXT_FIELDS.indexOf(kind as TextTemplateField) >= 0) {
      const field = textFieldName(kind as TextTemplateField);
      content = String(this.data[field] || '').trim();
      if (!content) { toast('当前内容为空，不能保存模板'); return; }
    }
    if (!content) return;
    this.setData({ saving: true });
    try {
      const existing = this.templatesFor(kind).find((item) => !item.is_system && item.name === name);
      const payload = { kind, name, content };
      if (existing) await updateCraftTemplate(existing.id, payload);
      else await createCraftTemplate(payload);
      const templates = await fetchCraftTemplates();
      this.setData({
        measurementTemplates: templates.filter((item) => item.kind === 'measurements'),
        requirementsTemplates: templates.filter((item) => item.kind === 'requirements'),
        challengesTemplates: templates.filter((item) => item.kind === 'challenges'),
        commonMistakesTemplates: templates.filter((item) => item.kind === 'common_mistakes'),
        qualityStandardsTemplates: templates.filter((item) => item.kind === 'quality_standards')
      });
      wx.showToast({ title: existing ? '模板已更新' : '模板已保存', icon: 'success' });
    } catch (error) { /* 请求层提示 */ } finally {
      this.setData({ saving: false });
    }
  },

  onAddMeasurement() {
    if (!this.data.canManage) return;
    this.setData({ measurements: this.data.measurements.concat([emptyMeasurement()]) });
  },

  onRemoveMeasurement(e: WechatMiniprogram.TouchEvent) {
    if (!this.data.canManage || this.data.measurements.length <= 1) return;
    const index = Number(e.currentTarget.dataset.index);
    this.setData({ measurements: this.data.measurements.filter((_, i) => i !== index) });
  },

  onNewVersion() {
    if (!this.data.canManage || !this.data.pack) return;
    this.setPackForm(this.data.pack, true);
    wx.pageScrollTo({ scrollTop: 0 });
  },

  onCancelNewVersion() {
    this.setPackForm(this.data.pack);
  },

  measurementPayload(): TechMeasurement[] | null {
    const result: TechMeasurement[] = [];
    for (const row of this.data.measurements) {
      if (!row.size.trim() || !row.point.trim() || !row.value.trim()) {
        toast('请完整填写尺码、部位和尺寸'); return null;
      }
      if (!decimalText(row.value) || !decimalText(row.tolerancePlus) || !decimalText(row.toleranceMinus)) {
        toast('尺寸和公差须为数字'); return null;
      }
      result.push({
        size: row.size.trim(), point: row.point.trim(), unit: row.unit.trim() || 'cm',
        value: row.value.trim(), tolerance_plus: row.tolerancePlus.trim(),
        tolerance_minus: row.toleranceMinus.trim()
      });
    }
    return result;
  },

  async onSavePack() {
    if (!this.data.canManage || this.data.saving) return;
    if (!this.data.requirements.trim()) {
      toast('请选择或填写工艺要求'); return;
    }
    const measurements = this.measurementPayload();
    if (!measurements) return;
    const payload = {
      pattern_no: this.data.patternNo.trim(),
      requirements: this.data.requirements.trim(),
      challenges: this.data.challenges.trim(),
      common_mistakes: this.data.commonMistakes.trim(),
      quality_standards: this.data.qualityStandards.trim(),
      measurements,
      pattern_files: this.data.patternFiles.map((file) => file.path),
      tolerances: this.data.pack && !this.data.newVersion ? this.data.pack.tolerances : {},
      attachments: this.data.techFiles.map((file) => ({ name: file.name, path: file.path, note: '' }))
    };
    this.setData({ saving: true });
    try {
      if (this.data.pack && !this.data.newVersion) {
        await updateTechPack(this.data.pack.id, payload);
      } else {
        const rawVersion = this.data.versionNo.trim();
        if (rawVersion && (!/^\d+$/.test(rawVersion) || Number(rawVersion) <= 0)) {
          toast('版本号须为正整数'); return;
        }
        await createTechPack({
          ...payload, order: this.data.orderId,
          version_no: rawVersion ? Number(rawVersion) : undefined
        });
      }
      wx.showToast({ title: this.data.newVersion ? '新版本已创建' : '工艺资料已保存', icon: 'success' });
      await this.reload();
    } catch (error) { /* 请求层提示 */ } finally {
      this.setData({ saving: false });
    }
  },

  onSampleResultChange(e: WechatMiniprogram.PickerChange) {
    this.setData({ sampleResultIndex: Number(e.detail.value) });
  },

  async onSubmitSample() {
    const pack = this.data.pack;
    if (!pack || !this.data.canManage || this.data.saving) return;
    const note = this.data.sampleNote.trim();
    if (!note) { toast('请填写检验说明'); return; }
    this.setData({ saving: true });
    try {
      let task = pack.sample_tasks.slice().sort((a, b) => b.id - a.id)[0];
      if (!task) task = await createSampleTask(pack.id, { title: '大货产前样' });
      await recordSampleResult(task.id, {
        result: this.data.sampleResultIndex === 0 ? 'passed' : 'failed', note,
        attachments: this.data.sampleFiles.map((file) => file.path)
      });
      this.setData({ sampleNote: '', sampleFiles: [], sampleResultIndex: 0 });
      wx.showToast({ title: '产前样结果已追加', icon: 'success' });
      await this.reload();
    } catch (error) { /* 请求层提示 */ } finally {
      this.setData({ saving: false });
    }
  },

  async onCreateIssue() {
    const pack = this.data.pack;
    const detail = this.data.issueDetail.trim();
    if (!pack || !this.data.canManage || this.data.saving) return;
    if (!detail) { toast('请填写问题说明'); return; }
    this.setData({ saving: true });
    try {
      await createReviewIssue(pack.id, { title: detail.slice(0, 80), detail });
      this.setData({ issueDetail: '' });
      wx.showToast({ title: '问题已登记', icon: 'success' });
      await this.reload();
    } catch (error) { /* 请求层提示 */ } finally {
      this.setData({ saving: false });
    }
  },

  async onResolveIssue(e: WechatMiniprogram.TouchEvent) {
    const id = Number(e.currentTarget.dataset.id);
    if (!id || !this.data.canManage || this.data.actionId) return;
    const modal = await wx.showModal({
      title: '填写处理结果', editable: true, placeholderText: '处理结果（必填）'
    });
    if (!modal.confirm) return;
    const resolution = (modal.content || '').trim();
    if (!resolution) { toast('请填写处理结果'); return; }
    this.setData({ actionId: id });
    try {
      await resolveReviewIssue(id, resolution);
      wx.showToast({ title: '问题已解决', icon: 'success' });
      await this.reload();
    } catch (error) { /* 请求层提示 */ } finally {
      this.setData({ actionId: 0 });
    }
  },

  async onReopenIssue(e: WechatMiniprogram.TouchEvent) {
    const id = Number(e.currentTarget.dataset.id);
    if (!id || !this.data.canManage || this.data.actionId) return;
    const modal = await wx.showModal({
      title: '重开问题', editable: true, placeholderText: '重开原因（可选）'
    });
    if (!modal.confirm) return;
    this.setData({ actionId: id });
    try {
      await reopenReviewIssue(id, (modal.content || '').trim());
      wx.showToast({ title: '问题已重开', icon: 'success' });
      await this.reload();
    } catch (error) { /* 请求层提示 */ } finally {
      this.setData({ actionId: 0 });
    }
  },

  async onConfirmBriefing() {
    const pack = this.data.pack;
    if (!pack || !this.data.canBrief || this.data.saving) return;
    const modal = await wx.showModal({
      title: '确认班组交底',
      content: '请确认当前工艺资料已向生产班组完成交底。此操作记录当前登录账号和时间。',
      confirmText: '确认完成'
    });
    if (!modal.confirm) return;
    this.setData({ saving: true });
    try {
      await confirmBriefing(pack.id, this.data.briefingNote.trim());
      this.setData({ briefingNote: '' });
      wx.showToast({ title: '交底已确认', icon: 'success' });
      await this.reload();
    } catch (error) { /* 请求层提示 */ } finally {
      this.setData({ saving: false });
    }
  },

  async onApprove(e: WechatMiniprogram.TouchEvent) {
    const pack = this.data.pack;
    const approved = e.currentTarget.dataset.approved !== '0';
    if (!pack || !this.data.canApprove || this.data.actionId) return;
    const modal = await wx.showModal({
      title: approved ? '产前审批通过' : '驳回产前准备',
      content: approved ? '确认当前订单和工艺版本已具备大货生产条件？' : '请填写驳回意见。',
      editable: !approved, placeholderText: '审批意见'
    });
    if (!modal.confirm) return;
    const reason = (modal.content || '').trim();
    if (!approved && !reason) { toast('驳回必须填写意见'); return; }
    this.setData({ actionId: pack.id });
    try {
      await decideTechPack(pack.id, approved, reason);
      wx.showToast({ title: approved ? '审批已通过' : '已驳回', icon: 'success' });
      await this.reload();
    } catch (error) { /* 请求层提示 */ } finally {
      this.setData({ actionId: 0 });
    }
  },

  onAddAttachment(e: WechatMiniprogram.TouchEvent) {
    const target = e.currentTarget.dataset.target as AttachmentTarget;
    if (['patternFiles', 'techFiles', 'sampleFiles'].indexOf(target) < 0 || this.data.uploadingTarget) return;
    if (target !== 'sampleFiles' && !this.data.canManage) return;
    if (target === 'sampleFiles' && !this.data.canManage) return;
    wx.showActionSheet({
      itemList: ['拍照或相册图片', '从聊天选择附件'],
      success: (res) => {
        this.pickAndUpload(target, res.tapIndex).catch((error: Error) => {
          if (error.message.indexOf('未选择') < 0) toast(error.message || '附件上传失败');
        });
      }
    });
  },

  async pickAndUpload(target: AttachmentTarget, source: number): Promise<void> {
    this.setData({ uploadingTarget: target });
    try {
      let file: Attachment;
      if (source === 0) {
        const path = await chooseImage();
        file = { path: await uploadImage(path), name: `图片-${Date.now()}.jpg` };
      } else {
        const selected = await chooseDocument();
        const uploaded = await uploadDocument(selected.filePath, selected.name);
        file = { path: uploaded.url, name: uploaded.name };
      }
      this.setData({ [target]: (this.data[target] as Attachment[]).concat([file]) });
    } finally {
      this.setData({ uploadingTarget: '' });
    }
  },

  onRemoveAttachment(e: WechatMiniprogram.TouchEvent) {
    const target = e.currentTarget.dataset.target as AttachmentTarget;
    const index = Number(e.currentTarget.dataset.index);
    if (!this.data.canManage || ['patternFiles', 'techFiles', 'sampleFiles'].indexOf(target) < 0) return;
    this.setData({ [target]: (this.data[target] as Attachment[]).filter((_, i) => i !== index) });
  },

  async onOpenAttachment(e: WechatMiniprogram.TouchEvent) {
    const path = String(e.currentTarget.dataset.path || '');
    if (!path) return;
    const url = mediaUrl(path);
    if (/\.(jpe?g|png|gif|webp)(?:$|\?)/i.test(url)) {
      wx.previewImage({ current: url, urls: [url] });
      return;
    }
    wx.showLoading({ title: '打开附件' });
    try {
      const downloaded = await new Promise<WechatMiniprogram.DownloadFileSuccessCallbackResult>((resolve, reject) => {
        wx.downloadFile({ url, success: resolve, fail: reject });
      });
      if (downloaded.statusCode < 200 || downloaded.statusCode >= 300) throw new Error('download failed');
      await wx.openDocument({ filePath: downloaded.tempFilePath, showMenu: true });
    } catch (error) {
      toast('附件打开失败，请稍后重试');
    } finally {
      wx.hideLoading();
    }
  },

  issueTime(issue: ReviewIssue): string {
    return dateTime(issue.reviewed_at || issue.created_at);
  },

  confirmationTime(item: BriefingConfirmation): string {
    return dateTime(item.confirmed_at);
  }
});
