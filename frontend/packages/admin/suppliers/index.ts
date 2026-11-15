/**
 * 供应商管理页（warehouse 域 P2 采购补全）：
 * - 列表 keyword 搜索（后端模糊 编码/名称/联系人/电话）；
 * - 新建/编辑弹层：code 必填（空/重复后端 400，直显）；
 * - 删除：软删；被采购单/暂收单引用时后端 400（PROTECT 语义）——
 *   silent 请求 + 页面弹窗完整文案直显（toast 截 20 字会丢上下文）。
 * 权限：页面挂 warehouse.supplier（legacy 默认零授权，独立管控点）。
 */
import { requireLogin } from '../../../utils/auth';
import { hasPerm } from '../../../utils/perm';
import { ApiError } from '../../../utils/request';
import {
  fetchSuppliers, createSupplier, updateSupplier, deleteSupplier, Supplier
} from '../../../services/warehouse';

Page({
  data: {
    denied: false,
    keyword: '',
    rows: [] as Supplier[],
    loading: false,
    error: false,
    page: 1,
    hasMore: false,
    loaded: false,
    // ---- 新建/编辑弹层 ----
    editVisible: false,
    editSaving: false,
    editingId: 0,      // 0=新建
    formCode: '',
    formName: '',
    formContact: '',
    formPhone: '',
    formSettle: '',
    formRating: '',
    formRemark: ''
  },

  onShow() {
    if (!requireLogin()) return;
    const denied = !hasPerm('warehouse.supplier');
    this.setData({ denied });
    if (!denied) this.reload();
  },

  onPullDownRefresh() {
    this.reload();
    wx.stopPullDownRefresh();
  },

  onReachBottom() {
    if (this.data.hasMore && !this.data.loading) this.loadPage(this.data.page + 1);
  },

  // ============ 列表 ============

  onKeyword(e: WechatMiniprogram.Input) {
    this.setData({ keyword: e.detail.value });
  },

  onSearch() {
    this.reload();
  },

  reload() {
    this.loadPage(1);
  },

  loadPage(page: number) {
    this.setData({ loading: true, error: false });
    fetchSuppliers({
      page,
      page_size: 20,
      keyword: this.data.keyword.trim() || undefined
    })
      .then((res) => {
        const next = (res as { next?: string | null }).next;
        this.setData({
          loading: false,
          loaded: true,
          rows: page === 1 ? (res.results || []) : this.data.rows.concat(res.results || []),
          page,
          hasMore: !!next
        });
      })
      .catch(() => this.setData({ loading: false, error: true, loaded: true }));
  },

  // ============ 新建 / 编辑弹层 ============

  onOpenCreate() {
    this.setData({
      editVisible: true,
      editingId: 0,
      formCode: '',
      formName: '',
      formContact: '',
      formPhone: '',
      formSettle: '',
      formRating: '',
      formRemark: ''
    });
  },

  onOpenEdit(e: WechatMiniprogram.TouchEvent) {
    const id = Number(e.currentTarget.dataset.id);
    const row = this.data.rows.find((r) => r.id === id);
    if (!row) return;
    this.setData({
      editVisible: true,
      editingId: row.id,
      formCode: row.code,
      formName: row.name || '',
      formContact: row.contact_person || '',
      formPhone: row.phone || '',
      formSettle: row.settle_method || '',
      formRating: row.rating || '',
      formRemark: row.remark || ''
    });
  },

  onFormCode(e: WechatMiniprogram.Input) { this.setData({ formCode: e.detail.value }); },
  onFormName(e: WechatMiniprogram.Input) { this.setData({ formName: e.detail.value }); },
  onFormContact(e: WechatMiniprogram.Input) { this.setData({ formContact: e.detail.value }); },
  onFormPhone(e: WechatMiniprogram.Input) { this.setData({ formPhone: e.detail.value }); },
  onFormSettle(e: WechatMiniprogram.Input) { this.setData({ formSettle: e.detail.value }); },
  onFormRating(e: WechatMiniprogram.Input) { this.setData({ formRating: e.detail.value }); },
  onFormRemark(e: WechatMiniprogram.Input) { this.setData({ formRemark: e.detail.value }); },

  onCloseEdit() {
    this.setData({ editVisible: false });
  },

  noop() { /* 阻止冒泡 */ },

  onSaveEdit() {
    const code = this.data.formCode.trim();
    if (!code) {
      wx.showToast({ title: '供应商编码不能为空', icon: 'none' });
      return;
    }
    const body = {
      code,
      name: this.data.formName.trim(),
      contact_person: this.data.formContact.trim(),
      phone: this.data.formPhone.trim(),
      settle_method: this.data.formSettle.trim(),
      rating: this.data.formRating.trim(),
      remark: this.data.formRemark.trim()
    };
    this.setData({ editSaving: true });
    const req = this.data.editingId
      ? updateSupplier(this.data.editingId, body)
      : createSupplier(body);
    req
      .then(() => {
        this.setData({ editSaving: false, editVisible: false });
        wx.showToast({ title: this.data.editingId ? '已保存' : '已新增', icon: 'success' });
        this.reload();
      })
      .catch(() => this.setData({ editSaving: false }));
  },

  // ============ 删除（被引用 400 直显） ============

  onDelete(e: WechatMiniprogram.TouchEvent) {
    const id = Number(e.currentTarget.dataset.id);
    const row = this.data.rows.find((r) => r.id === id);
    if (!row) return;
    wx.showModal({
      title: '删除供应商',
      content: `删除「${row.code} ${row.name || ''}」（软删留痕）。确定？`,
      confirmColor: '#F53F3F',
      success: (m) => {
        if (!m.confirm) return;
        // silent：400（被引用）由页面弹窗完整直显，避免 toast 截断
        deleteSupplier(id, true)
          .then(() => {
            wx.showToast({ title: '已删除', icon: 'success' });
            this.reload();
          })
          .catch((err: unknown) => {
            const msg = err instanceof ApiError && err.message
              ? err.message : '删除失败';
            wx.showModal({ title: '无法删除', content: msg, showCancel: false });
          });
      }
    });
  }
});
