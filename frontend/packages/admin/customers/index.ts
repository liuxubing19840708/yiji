/**
 * 客户管理（§十二）：双 Tab —— 客户分组 / 全部客户。
 * 分组：新增/重命名/删除（服务端无分组搜索参数，搜索为本地过滤）。
 * 客户：关键字搜索 + 分组筛选 + 分页；新建/编辑/删除客户。
 */
import { requireLogin } from '../../../utils/auth';
import {
  fetchCustomerGroups, createCustomerGroup, updateCustomerGroup, deleteCustomerGroup,
  fetchCustomers, createCustomer, updateCustomer, deleteCustomer
} from '../../../services/catalog';
import { Customer, CustomerGroup, Paginated } from '../../../services/types';

const PAGE_SIZE = 20;

/** 兼容处理：后端实际返回分页结构，services 契约声明为数组（已上报主会话） */
function unwrapList<T>(res: T[] | Paginated<T>): T[] {
  return Array.isArray(res) ? res : ((res as Paginated<T>).results || []);
}

Page({
  data: {
    tab: 'groups' as 'groups' | 'customers',
    // ---- 分组 ----
    groups: [] as CustomerGroup[],
    groupView: [] as CustomerGroup[],
    groupKeyword: '',
    groupFilter: 0,
    groupIndex: 0,
    // ---- 客户 ----
    customers: [] as Customer[],
    keyword: '',
    groupNames: ['全部'] as string[],
    page: 1,
    hasMore: false,
    // ---- 通用状态 ----
    loading: false,
    error: false,
    // ---- 客户编辑弹层 ----
    formVisible: false,
    form: {
      id: 0,
      customer_no: '',
      name: '',
      phone: '',
      remark: '',
      group: 0, // 0 = 不分组（picker 索引）
      groupIndex: 0
    } as { id: number; customer_no: string; name: string; phone: string; remark: string; group: number; groupIndex: number },
    saving: false
  },

  onShow() {
    if (!requireLogin()) return;
    this.reloadGroups();
    this.reloadCustomers();
  },

  // ============ Tab 切换 ============

  onTab(e: WechatMiniprogram.TouchEvent) {
    const tab = e.currentTarget.dataset.tab as 'groups' | 'customers';
    this.setData({ tab });
  },

  // ============ 客户分组 ============

  reloadGroups() {
    this.setData({ loading: true, error: false });
    fetchCustomerGroups()
      .then((res) => {
        // customer_count 由后端序列化器提供，types.ts 暂缺该字段（已上报），此处防御性读取
        const list = unwrapList<CustomerGroup>(res).map((g) => {
          const extra = g as unknown as { customer_count?: number };
          return Object.assign({}, g, { customerCount: extra.customer_count || 0 });
        });
        this.setData({ groups: list, loading: false });
        this.applyGroupFilter();
        this.refreshGroupNames();
      })
      .catch(() => this.setData({ loading: false, error: true }));
  },

  /** 本地过滤分组（后端无该筛选参数） */
  applyGroupFilter() {
    const kw = this.data.groupKeyword.trim();
    const list = kw
      ? this.data.groups.filter((g) => g.name.indexOf(kw) >= 0)
      : this.data.groups;
    this.setData({ groupView: list });
  },

  onGroupKeyword(e: WechatMiniprogram.Input) {
    this.setData({ groupKeyword: e.detail.value });
    this.applyGroupFilter();
  },

  refreshGroupNames() {
    this.setData({ groupNames: ['全部'].concat(this.data.groups.map((g) => g.name)) });
  },

  onAddGroup() {
    wx.showModal({
      title: '添加分组',
      editable: true,
      placeholderText: '分组名称（如：内销 / 外单 / 淘宝 / 批发）',
      success: (res) => {
        const name = (res.content || '').trim();
        if (!res.confirm || !name) return;
        createCustomerGroup(name)
          .then(() => {
            wx.showToast({ title: '已添加', icon: 'success' });
            this.reloadGroups();
          })
          .catch(() => { /* 错误 toast 已由请求层统一弹出 */ });
      }
    });
  },

  onEditGroup(e: WechatMiniprogram.TouchEvent) {
    // 列表按 groupView（搜索过滤后）渲染，索引需从 groupView 取
    const group = this.data.groupView[Number(e.currentTarget.dataset.index)];
    if (!group) return;
    wx.showActionSheet({
      itemList: ['重命名', '删除分组'],
      success: (res) => {
        if (res.tapIndex === 0) {
          wx.showModal({
            title: '重命名分组',
            editable: true,
            placeholderText: group.name,
            success: (m) => {
              const name = (m.content || '').trim();
              if (!m.confirm || !name || name === group.name) return;
              updateCustomerGroup(group.id, name)
                .then(() => {
                  wx.showToast({ title: '已保存', icon: 'success' });
                  this.reloadGroups();
                  this.reloadCustomers();
                })
                .catch(() => { /* toast 已由请求层弹出 */ });
            }
          });
        } else if (res.tapIndex === 1) {
          wx.showModal({
            title: '删除分组',
            content: `确定删除「${group.name}」？该组客户将变为未分组。`,
            success: (m) => {
              if (!m.confirm) return;
              deleteCustomerGroup(group.id)
                .then(() => {
                  wx.showToast({ title: '已删除', icon: 'success' });
                  if (this.data.groupFilter === group.id) {
                    this.setData({ groupFilter: 0 });
                  }
                  this.reloadGroups();
                  this.reloadCustomers();
                })
                .catch(() => { /* toast 已由请求层弹出 */ });
            }
          });
        }
      }
    });
  },

  // ============ 全部客户 ============

  reloadCustomers() {
    const params: Record<string, unknown> = {
      page: 1,
      page_size: PAGE_SIZE
    };
    const kw = this.data.keyword.trim();
    if (kw) params.keyword = kw;
    if (this.data.groupFilter) params.group = this.data.groupFilter;
    this.setData({ loading: true, error: false, page: 1, customers: [] });
    fetchCustomers(params as Parameters<typeof fetchCustomers>[0])
      .then((res) => {
        this.setData({
          customers: res.results || [],
          hasMore: !!res.next,
          loading: false
        });
      })
      .catch(() => this.setData({ loading: false, error: true }));
  },

  loadMoreCustomers() {
    if (!this.data.hasMore || this.data.loading) return;
    const nextPage = this.data.page + 1;
    const params: Record<string, unknown> = { page: nextPage, page_size: PAGE_SIZE };
    const kw = this.data.keyword.trim();
    if (kw) params.keyword = kw;
    if (this.data.groupFilter) params.group = this.data.groupFilter;
    this.setData({ loading: true });
    fetchCustomers(params as Parameters<typeof fetchCustomers>[0])
      .then((res) => {
        this.setData({
          customers: this.data.customers.concat(res.results || []),
          hasMore: !!res.next,
          page: nextPage,
          loading: false
        });
      })
      .catch(() => this.setData({ loading: false }));
  },

  onReachBottom() {
    if (this.data.tab === 'customers') this.loadMoreCustomers();
  },

  onKeyword(e: WechatMiniprogram.Input) {
    this.setData({ keyword: e.detail.value });
  },

  onSearch() {
    this.reloadCustomers();
  },

  onGroupFilter(e: WechatMiniprogram.PickerChange) {
    const idx = Number(e.detail.value);
    const group = idx > 0 ? (this.data.groups[idx - 1] || null) : null;
    this.setData({ groupFilter: group ? group.id : 0, groupIndex: idx });
    this.reloadCustomers();
  },

  // ============ 客户新建 / 编辑 / 删除 ============

  onAddCustomer() {
    this.setData({
      formVisible: true,
      form: { id: 0, customer_no: '', name: '', phone: '', remark: '', group: 0, groupIndex: 0 }
    });
  },

  onEditCustomer(e: WechatMiniprogram.TouchEvent) {
    const c = this.data.customers[Number(e.currentTarget.dataset.index)];
    if (!c) return;
    const idx = c.group ? this.data.groups.findIndex((g) => g.id === c.group) + 1 : 0;
    this.setData({
      formVisible: true,
      form: {
        id: c.id,
        customer_no: c.customer_no || '',
        name: c.name || '',
        phone: c.phone || '',
        remark: c.remark || '',
        group: c.group || 0,
        groupIndex: idx
      }
    });
  },

  onFormNo(e: WechatMiniprogram.Input) { this.setData({ 'form.customer_no': e.detail.value }); },
  onFormName(e: WechatMiniprogram.Input) { this.setData({ 'form.name': e.detail.value }); },
  onFormPhone(e: WechatMiniprogram.Input) { this.setData({ 'form.phone': e.detail.value }); },
  onFormRemark(e: WechatMiniprogram.Input) { this.setData({ 'form.remark': e.detail.value }); },

  onFormGroup(e: WechatMiniprogram.PickerChange) {
    const idx = Number(e.detail.value);
    const group = idx > 0 ? (this.data.groups[idx - 1] || null) : null;
    this.setData({ 'form.groupIndex': idx, 'form.group': group ? group.id : 0 });
  },

  onCloseForm() {
    this.setData({ formVisible: false });
  },

  noop() { /* 阻止冒泡 */ },

  onSaveCustomer() {
    const f = this.data.form;
    if (!f.name.trim()) {
      wx.showToast({ title: '请输入客户名称', icon: 'none' });
      return;
    }
    const payload: Partial<Customer> = {
      customer_no: f.customer_no.trim(),
      name: f.name.trim(),
      phone: f.phone.trim(),
      remark: f.remark.trim(),
      group: f.group || null
    };
    this.setData({ saving: true });
    const done = () => {
      this.setData({ saving: false, formVisible: false });
      wx.showToast({ title: '已保存', icon: 'success' });
      this.reloadCustomers();
      this.reloadGroups();
    };
    if (f.id) {
      updateCustomer(f.id, payload).then(done).catch(() => this.setData({ saving: false }));
    } else {
      createCustomer(payload).then(done).catch(() => this.setData({ saving: false }));
    }
  },

  onDeleteCustomer() {
    const f = this.data.form;
    if (!f.id) return;
    wx.showModal({
      title: '删除客户',
      content: `确定删除「${f.name}」？`,
      confirmColor: '#F53F3F',
      success: (m) => {
        if (!m.confirm) return;
        deleteCustomer(f.id)
          .then(() => {
            this.setData({ formVisible: false });
            wx.showToast({ title: '已删除', icon: 'success' });
            this.reloadCustomers();
            this.reloadGroups();
          })
          .catch(() => { /* toast 已由请求层弹出 */ });
      }
    });
  }
});
