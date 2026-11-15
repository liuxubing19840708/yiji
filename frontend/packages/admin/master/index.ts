import { requireLogin } from '../../../utils/auth';
import { hasPerm } from '../../../utils/perm';
import { mediaUrl } from '../../../utils/media';
import { uploadImage, uploadVideo, chooseVideo } from '../../../utils/upload';
import {
  fetchStyles, createStyle, updateStyle, deleteStyle, copyStyle, syncStyleProcessesToTickets,
  fetchProcesses, fetchProcessSelector, createProcess, updateProcess, deleteProcess,
  fetchCustomers, createCustomer, updateCustomer, deleteCustomer,
  fetchCustomerGroups, createCustomerGroup, updateCustomerGroup, deleteCustomerGroup
} from '../../../services/catalog';
import {
  fetchProcessTemplates, createProcessTemplate, updateProcessTemplate, deleteProcessTemplate
} from '../../../services/process-template';
import { Customer, CustomerGroup, Process, ProcessTemplate, Style, StyleProcessItem } from '../../../services/types';

type Tab = 'styles' | 'processes' | 'templates' | 'customers' | 'groups';
type Item = Record<string, any>;
const tabs: Array<{ key: Tab; label: string }> = [
  { key: 'styles', label: '款式' }, { key: 'processes', label: '工序' },
  { key: 'templates', label: '工序模板' }
];
const emptyForm = () => ({ id: 0 });
function listOf<T>(value: T[] | { results?: T[] }): T[] {
  return Array.isArray(value) ? value : (value.results || []);
}

Page({
  data: {
    tabs, active: 'styles' as Tab, canManage: false,
    loading: false, error: '', keyword: '', saving: false, busyId: 0,
    styles: [] as Item[], processes: [] as Item[], templates: [] as Item[],
    customers: [] as Item[], customerOptions: [] as Item[], groups: [] as Item[],
    customerNames: ['全部客户'] as string[], customerFilterIndex: 0, customerFilter: 0,
    groupNames: ['全部分组'] as string[], groupFilterIndex: 0, groupFilter: 0,
    fixedNames: ['全部工序', '固定工序', '非固定工序'], fixedFilterIndex: 0,
    processNames: ['请选择工序'] as string[],
    formVisible: false, formType: '' as Tab | '', form: emptyForm() as Item,
    formCustomerIndex: 0, formGroupIndex: 0,
    hasMore: false, page: 1
  },

  onLoad(options: Record<string, string>) {
    const active = tabs.some((t) => t.key === options.tab) ? options.tab as Tab : 'styles';
    const canManage = hasPerm('styles.manage');
    this.setData({ active: canManage ? active : 'processes', canManage, tabs: canManage ? tabs : tabs.filter((t) => t.key === 'processes') });
  },
  onShow() {
    if (!requireLogin()) return;
    this.loadOptions().then(() => this.reload());
  },
  onPullDownRefresh() { this.reload().finally(() => wx.stopPullDownRefresh()); },
  onReachBottom() { this.loadMore(); },

  async loadOptions() {
    // 普通员工只读取本人可登记工序，不请求完整档案、客户及分组选项。
    if (!this.data.canManage) return;
    const [customers, groups, processes] = await Promise.all([
      fetchCustomers({ page: 1, page_size: 200 }).catch(() => ({ results: [], count: 0, next: null, previous: null })),
      fetchCustomerGroups().catch(() => [] as CustomerGroup[]),
      fetchProcesses().catch(() => [] as Process[])
    ]);
    this.setData({
      customers: customers.results || [], customerOptions: customers.results || [], groups,
      processes: processes as Item[],
      customerNames: ['全部客户'].concat((customers.results || []).map((c) => c.name)),
      groupNames: ['全部分组'].concat(groups.map((g) => g.name)),
      processNames: ['请选择工序'].concat(processes.map((p) => p.name))
    });
  },

  async reload() {
    this.setData({ loading: true, error: '', page: 1 });
    try {
      const active = this.data.active as Tab;
      const keyword = this.data.keyword.trim();
      if (active === 'styles') {
        const res = await fetchStyles({ page: 1, page_size: 20, keyword: keyword || undefined, customer_id: this.data.customerFilter || undefined });
        this.setData({ styles: (res.results || []) as Item[], hasMore: Boolean(res.next) });
      } else if (active === 'customers') {
        const params: Item = { page: 1, page_size: 20, keyword: keyword || undefined, group: this.data.groupFilter || undefined };
        const res = await fetchCustomers(params);
        this.setData({ customers: (res.results || []) as Item[], hasMore: Boolean(res.next) });
      } else if (active === 'processes') {
        const fixed = this.data.fixedFilterIndex;
        if (this.data.canManage) {
          const list = await fetchProcesses({ keyword: keyword || undefined, is_fixed: fixed ? fixed === 1 : undefined });
          this.setData({ processes: list as Item[], hasMore: false });
        } else {
          // catalog /process-selector：固定工序 ∪ 指定给本人的工序；搜索仅在可登记集合内过滤。
          const list = await fetchProcessSelector();
          const normalized = keyword.toLowerCase();
          this.setData({
            processes: list.filter((p) => !normalized || p.name.toLowerCase().includes(normalized)) as Item[],
            hasMore: false
          });
        }
      } else if (active === 'templates') {
        const res = await fetchProcessTemplates() as unknown as ProcessTemplate[] | { results?: ProcessTemplate[] };
        this.setData({ templates: listOf(res) as Item[], hasMore: false });
      } else {
        const list = await fetchCustomerGroups();
        this.setData({ groups: list as Item[], hasMore: false });
      }
      this.setData({ loading: false });
    } catch (e) {
      this.setData({ loading: false, error: (e as Error).message || '档案加载失败' });
    }
  },

  async loadMore() {
    if (!this.data.hasMore || this.data.loading || !['styles', 'customers'].includes(this.data.active)) return;
    const page = this.data.page + 1;
    this.setData({ loading: true });
    try {
      const keyword = this.data.keyword.trim();
      if (this.data.active === 'styles') {
        const res = await fetchStyles({ page, page_size: 20, keyword: keyword || undefined, customer_id: this.data.customerFilter || undefined });
        this.setData({ styles: this.data.styles.concat(res.results || []), hasMore: Boolean(res.next), page });
      } else {
        const res = await fetchCustomers({ page, page_size: 20, keyword: keyword || undefined, group: this.data.groupFilter || undefined } as Item);
        this.setData({ customers: this.data.customers.concat(res.results || []), hasMore: Boolean(res.next), page });
      }
    } finally { this.setData({ loading: false }); }
  },

  onTab(e: WechatMiniprogram.TouchEvent) {
    this.setData({ active: e.currentTarget.dataset.tab, keyword: '', error: '', hasMore: false });
    this.reload();
  },
  onKeyword(e: WechatMiniprogram.Input) { this.setData({ keyword: e.detail.value }); },
  onSearch() { this.reload(); },
  onCustomerFilter(e: WechatMiniprogram.PickerChange) {
    const index = Number(e.detail.value);
    this.setData({ customerFilterIndex: index, customerFilter: index ? this.data.customerOptions[index - 1]?.id || 0 : 0 });
    this.reload();
  },
  onGroupFilter(e: WechatMiniprogram.PickerChange) {
    const index = Number(e.detail.value);
    this.setData({ groupFilterIndex: index, groupFilter: index ? this.data.groups[index - 1]?.id || 0 : 0 });
    this.reload();
  },
  onFixedFilter(e: WechatMiniprogram.PickerChange) {
    this.setData({ fixedFilterIndex: Number(e.detail.value) });
    this.reload();
  },

  onCreate() { this.openForm(this.data.active as Tab); },
  onEdit(e: WechatMiniprogram.TouchEvent) {
    const item = this.currentList()[Number(e.currentTarget.dataset.index)];
    if (item) this.openForm(this.data.active as Tab, item);
  },
  currentList(): Item[] { return this.data[this.data.active] as Item[]; },

  openForm(type: Tab, item?: Item) {
    const form: Item = item ? { ...item } : emptyForm();
    if (type === 'styles') {
      form.process_items = ((item?.processes || []) as StyleProcessItem[]).map((p) => ({
        process_id: p.process, processIndex: this.data.processes.findIndex((x) => x.id === p.process) + 1,
        name: p.process_name, price: String(p.price ?? '0'), sort: p.sort || 0, allow_skip: p.allow_skip !== false
      }));
      form.images = [...(item?.images || (item?.image ? [item.image] : []))];
      form.craft_images = [...(item?.craft_images || [])];
      form.imageUrls = form.images.map((x: string) => mediaUrl(x));
      form.craftImageUrls = form.craft_images.map((x: string) => mediaUrl(x));
      form.videoUrl = mediaUrl(form.video || '');
      this.setData({ formCustomerIndex: item?.customer ? this.data.customerOptions.findIndex((c) => c.id === item.customer) + 1 : 0 });
    }
    if (type === 'processes') {
      form.default_price = String(item?.default_price ?? '0');
      form.sort = item?.sort ?? this.data.processes.length + 1;
    }
    if (type === 'templates') {
      form.editItems = (item?.items || []).map((p: Item) => ({
        ...p, processIndex: this.data.processes.findIndex((x) => x.id === p.process_id) + 1,
        name: p.process_name || this.data.processes.find((x) => x.id === p.process_id)?.name || ''
      }));
    }
    if (type === 'customers') this.setData({ formGroupIndex: item?.group ? this.data.groups.findIndex((g) => g.id === item.group) + 1 : 0 });
    this.setData({ formVisible: true, formType: type, form });
  },
  closeForm() { if (!this.data.saving) this.setData({ formVisible: false, formType: '', form: emptyForm() }); },
  noop() {},
  onInput(e: WechatMiniprogram.Input) {
    this.setData({ [`form.${e.currentTarget.dataset.field}`]: e.detail.value });
  },
  onSwitch(e: WechatMiniprogram.SwitchChange) {
    this.setData({ [`form.${e.currentTarget.dataset.field}`]: e.detail.value });
  },
  onFormCustomer(e: WechatMiniprogram.PickerChange) {
    const index = Number(e.detail.value);
    this.setData({ formCustomerIndex: index, 'form.customer': index ? this.data.customerOptions[index - 1].id : null });
  },
  onFormGroup(e: WechatMiniprogram.PickerChange) {
    const index = Number(e.detail.value);
    this.setData({ formGroupIndex: index, 'form.group': index ? this.data.groups[index - 1].id : null });
  },

  addProcessLine() {
    const key = this.data.formType === 'styles' ? 'process_items' : 'editItems';
    const lines = [...(this.data.form[key] || []), { process_id: 0, processIndex: 0, price: '', sort: (this.data.form[key] || []).length + 1, allow_skip: true }];
    this.setData({ [`form.${key}`]: lines });
  },
  onLineProcess(e: WechatMiniprogram.PickerChange) {
    const index = Number(e.currentTarget.dataset.index);
    const selected = Number(e.detail.value);
    const process = selected ? this.data.processes[selected - 1] : null;
    const key = this.data.formType === 'styles' ? 'process_items' : 'editItems';
    this.setData({
      [`form.${key}[${index}].processIndex`]: selected,
      [`form.${key}[${index}].process_id`]: process?.id || 0,
      [`form.${key}[${index}].name`]: process?.name || '',
      [`form.${key}[${index}].price`]: this.data.form[key][index].price || String(process?.default_price ?? '0')
    });
  },
  onLineInput(e: WechatMiniprogram.Input) {
    const key = this.data.formType === 'styles' ? 'process_items' : 'editItems';
    this.setData({ [`form.${key}[${e.currentTarget.dataset.index}].${e.currentTarget.dataset.field}`]: e.detail.value });
  },
  onLineSwitch(e: WechatMiniprogram.SwitchChange) {
    this.setData({ [`form.process_items[${e.currentTarget.dataset.index}].allow_skip`]: e.detail.value });
  },
  removeLine(e: WechatMiniprogram.TouchEvent) {
    const key = this.data.formType === 'styles' ? 'process_items' : 'editItems';
    const lines = [...this.data.form[key]];
    lines.splice(Number(e.currentTarget.dataset.index), 1);
    this.setData({ [`form.${key}`]: lines });
  },
  validateLines(lines: Item[]): string {
    const ids = new Set<number>();
    for (let i = 0; i < lines.length; i++) {
      if (!lines[i].process_id) return `第 ${i + 1} 行请选择工序`;
      if (ids.has(lines[i].process_id)) return `第 ${i + 1} 行工序重复`;
      ids.add(lines[i].process_id);
      if (lines[i].price === '' || !Number.isFinite(Number(lines[i].price)) || Number(lines[i].price) < 0) return `第 ${i + 1} 行工价不合法`;
    }
    return '';
  },

  async chooseImages(e: WechatMiniprogram.TouchEvent) {
    const field = e.currentTarget.dataset.field as 'images' | 'craft_images';
    const left = 9 - (this.data.form[field] || []).length;
    if (!left) return;
    const res = await wx.chooseMedia({ count: left, mediaType: ['image'], sizeType: ['compressed'] });
    wx.showLoading({ title: '上传图片…', mask: true });
    try {
      for (const file of res.tempFiles) {
        const url = await uploadImage(file.tempFilePath);
        this.setData({ [`form.${field}`]: [...this.data.form[field], url] });
      }
      const urls = this.data.form[field].map((x: string) => mediaUrl(x));
      this.setData({ [`form.${field === 'images' ? 'imageUrls' : 'craftImageUrls'}`]: urls });
    } finally { wx.hideLoading(); }
  },
  removeImage(e: WechatMiniprogram.TouchEvent) {
    const field = e.currentTarget.dataset.field;
    const images = [...this.data.form[field]];
    images.splice(Number(e.currentTarget.dataset.index), 1);
    this.setData({ [`form.${field}`]: images, [`form.${field === 'images' ? 'imageUrls' : 'craftImageUrls'}`]: images.map((x: string) => mediaUrl(x)) });
  },
  async chooseCraftVideo() {
    if (this.data.form.video) return;
    const path = await chooseVideo();
    wx.showLoading({ title: '上传视频…', mask: true });
    try {
      const url = await uploadVideo(path);
      this.setData({ 'form.video': url, 'form.videoUrl': mediaUrl(url) });
    } finally { wx.hideLoading(); }
  },
  removeVideo() { this.setData({ 'form.video': '', 'form.videoUrl': '' }); },

  async save() {
    if (this.data.saving) return;
    const type = this.data.formType as Tab;
    const f = this.data.form;
    if (type !== 'styles' && !String(f.name || '').trim()) { wx.showToast({ title: '请输入名称', icon: 'none' }); return; }
    if (type === 'styles' && (!String(f.style_no || '').trim() || !String(f.name || '').trim())) { wx.showToast({ title: '款号和名称不能为空', icon: 'none' }); return; }
    const lines = type === 'styles' ? (f.process_items || []) : type === 'templates' ? (f.editItems || []) : [];
    const invalid = this.validateLines(lines);
    if (invalid) { wx.showToast({ title: invalid, icon: 'none' }); return; }
    this.setData({ saving: true });
    try {
      if (type === 'styles') {
        const body: Item = {
          style_no: f.style_no.trim(), name: f.name.trim(), customer: f.customer || null,
          fabric: f.fabric || '', remark: f.remark || '', craft_note: f.craft_note || '',
          images: f.images || [], craft_images: f.craft_images || [], video: f.video || '',
          process_items: lines.map((x: Item, i: number) => ({ process_id: x.process_id, price: String(x.price), sort: Number(x.sort) || i + 1, allow_skip: x.allow_skip !== false }))
        };
        if (f.id) await updateStyle(f.id, body); else await createStyle(body);
      } else if (type === 'processes') {
        if (!Number.isFinite(Number(f.default_price)) || Number(f.default_price) < 0) throw new Error('默认工价须为非负数字');
        const body = { name: f.name.trim(), default_price: String(f.default_price), sort: Number(f.sort) || 0, is_fixed: Boolean(f.is_fixed), is_key: Boolean(f.is_key), hide_price: Boolean(f.hide_price) } as Partial<Process>;
        if (f.id) await updateProcess(f.id, body); else await createProcess(body);
      } else if (type === 'templates') {
        const body = { name: f.name.trim(), items: lines.map((x: Item) => ({ process_id: x.process_id, price: String(x.price) })) };
        if (f.id) await updateProcessTemplate(f.id, body); else await createProcessTemplate(body);
      } else if (type === 'customers') {
        const body = { customer_no: f.customer_no || '', name: f.name.trim(), phone: f.phone || '', group: f.group || null, remark: f.remark || '' } as Partial<Customer>;
        if (f.id) await updateCustomer(f.id, body); else await createCustomer(body);
      } else {
        if (f.id) await updateCustomerGroup(f.id, f.name.trim()); else await createCustomerGroup(f.name.trim());
      }
      wx.showToast({ title: '已保存', icon: 'success' });
      this.setData({ formVisible: false, formType: '', form: emptyForm() });
      await this.loadOptions();
      await this.reload();
    } catch (e) {
      wx.showToast({ title: (e as Error).message || '保存失败', icon: 'none' });
    } finally { this.setData({ saving: false }); }
  },

  async onCopy(e: WechatMiniprogram.TouchEvent) {
    const style = this.data.styles[Number(e.currentTarget.dataset.index)];
    if (!style) return;
    const created = await copyStyle(style.id);
    wx.showToast({ title: `已复制为 ${created.style_no}`, icon: 'none' });
    this.reload();
  },
  async onSync(e: WechatMiniprogram.TouchEvent) {
    const style = this.data.styles[Number(e.currentTarget.dataset.index)];
    if (!style) return;
    let result = await syncStyleProcessesToTickets(style.id);
    if (result.need_confirm) {
      const modal = await wx.showModal({ title: '同步工序', content: `该款式有 ${result.ticket_count || 0} 张已登记菲票，确定同步并记录操作日志吗？` });
      if (!modal.confirm) return;
      result = await syncStyleProcessesToTickets(style.id, true);
    }
    wx.showToast({ title: `已同步 ${result.ticket_count || 0} 张菲票`, icon: 'none' });
  },
  async onDelete(e: WechatMiniprogram.TouchEvent) {
    const item = this.currentList()[Number(e.currentTarget.dataset.index)];
    if (!item) return;
    const type = this.data.active as Tab;
    const label = item.style_no ? `${item.style_no} ${item.name}` : item.name;
    const modal = await wx.showModal({ title: '删除档案', content: `确定删除“${label}”吗？已有业务引用的档案可能无法删除。`, confirmColor: '#F53F3F' });
    if (!modal.confirm) return;
    if (type === 'styles') await deleteStyle(item.id);
    else if (type === 'processes') await deleteProcess(item.id);
    else if (type === 'templates') await deleteProcessTemplate(item.id);
    else if (type === 'customers') await deleteCustomer(item.id);
    else await deleteCustomerGroup(item.id);
    wx.showToast({ title: '已删除', icon: 'success' });
    await this.loadOptions();
    await this.reload();
  }
});
