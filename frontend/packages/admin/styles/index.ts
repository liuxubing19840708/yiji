/**
 * 款式管理（§五）：列表（搜索/分页）+ 新建/编辑/复制/删除款式。
 * 卡片图片为占位（款式图片上传 Wave 2 接入，见 TODO）。
 */
import { requireLogin } from '../../../utils/auth';
import { hasPerm } from '../../../utils/perm';
import { mediaUrl } from '../../../utils/media';
import { money } from '../../../utils/format';
import { chooseImage, uploadImage, chooseVideo, uploadVideo } from '../../../utils/upload';
import {
  fetchStyles, createStyle, updateStyle, deleteStyle, copyStyle,
  fetchCustomers
} from '../../../services/catalog';
import { fetchSettings } from '../../../services/ops';
import { fetchProductionHints } from '../../../services/production';
import { Style, Customer, Paginated, CompanySettings } from '../../../services/types';

const PAGE_SIZE = 20;
/** 工艺图片上限 */
const MAX_CRAFT_IMAGES = 9;

/** 内置常见面料池（2026-10-21 用户需求：款式面料点选），排在厂内历史面料之后兜底 */
const COMMON_FABRICS = ['纯棉', '涤棉', '棉麻', '亚麻', '真丝', '雪纺', '牛仔布', '灯芯绒',
  '针织', '蕾丝', '毛呢', '牛津布', '摇粒绒', '天丝', '涤纶', '锦纶'];

/** 图片 URL 归一化：相对路径补域名、127.0.0.1 旧数据换域名（真机裂图修） */
function absUrl(raw: string): string {
  return mediaUrl(raw);
}

/** 款式图片上限 */
const MAX_IMAGES = 9;

/** 列表视图模型：补齐后端聚合统计字段（types.ts 暂缺，防御性读取） */
interface StyleVM extends Style {
  customerCount?: string;
  cut_total: number;
  ticket_count: number;
  process_count: number;
  done_ticket_count: number;
  unfinished_ticket_count: number;
  priceText: string;
  imageUrl: string;
  progress: number; // 0-100
}

function toVM(s: Style): StyleVM {
  const raw = s as unknown as Record<string, unknown>;
  const ticketCount = Number(raw.ticket_count) || 0;
  const done = Number(raw.done_ticket_count) || 0;
  // 列表缩略图取多图首张（images 优先，存量单图 image 兜底）
  const images = Array.isArray(raw.images) && (raw.images as unknown[]).length
    ? (raw.images as unknown[]).map(String)
    : [];
  const image = images[0] || String(raw.image || '');
  return {
    ...s,
    cut_total: Number(raw.cut_total) || 0,
    ticket_count: ticketCount,
    process_count: Number(raw.process_count) || 0,
    done_ticket_count: done,
    unfinished_ticket_count: Number(raw.unfinished_ticket_count) || 0,
    priceText: money(s.price),
    imageUrl: absUrl(image),
    progress: ticketCount ? Math.min(100, Math.round((done / ticketCount) * 100)) : 0
  };
}

Page({
  data: {
    keyword: '',
    styles: [] as StyleVM[],
    page: 1,
    hasMore: false,
    loading: false,
    error: false,
    hideStylePrice: false, // 系统设置「隐藏款式总单价」（§15）
    // ---- 编辑弹层 ----
    formVisible: false,
    saving: false,
    /** 面料点选 chips：厂内历史面料（建床用过）在前 + 内置常见面料兜底 */
    fabricOptions: [] as string[],
    form: {
      id: 0,
      style_no: '',
      name: '',
      customerIndex: 0,
      customer: 0,
      remark: '',
      fabric: '',
      image: '',
      imageUrl: '',
      uploading: false,
      // ---- 款式图片多图（≤9 张；image 恒= 首张由后端同步） ----
      images: [] as string[],
      imageUrls: [] as string[],
      // ---- 工艺信息（F1/B1 契约：craft_note/craft_images/video） ----
      craft_note: '',
      craftImages: [] as string[],
      /** 展示用（mediaUrl 补域名，旧相对路径数据真机不裂图） */
      craftImageUrls: [] as string[],
      craftUploading: false,
      video: '',
      videoUrl: '',
      videoUploading: false
    } as {
      id: number; style_no: string; name: string; customerIndex: number;
      customer: number; remark: string; fabric: string; image: string; imageUrl: string; uploading: boolean;
      images: string[]; imageUrls: string[];
      craft_note: string; craftImages: string[]; craftImageUrls: string[]; craftUploading: boolean;
      video: string; videoUrl: string; videoUploading: boolean
    },
    customers: [] as Customer[],
    customerNames: ['不关联客户'] as string[]
  },

  onShow() {
    if (!requireLogin()) return;
    this.loadSettings();
    this.loadCustomers();
    this.loadFabricOptions();
    this.reload();
  },

  onPullDownRefresh() {
    this.reload();
    wx.stopPullDownRefresh();
  },

  // ============ 系统设置 ============

  loadSettings() {
    // TODO: 设置项建议缓存到 globalData，避免每个页面重复拉取（Wave 2 统一处理）
    fetchSettings()
      .then((s: CompanySettings) => {
        this.setData({ hideStylePrice: s.hide_style_total_price === '1' });
      })
      .catch(() => { /* 读取失败按显示处理，不阻塞列表 */ });
  },

  /** 面料点选数据源：厂内历史面料（/production-hints 按用量倒序）+ 常见面料池去重 */
  loadFabricOptions() {
    fetchProductionHints()
      .then((res) => {
        const seen = new Set<string>();
        const names: string[] = [];
        for (const n of (res.fabrics || []).map((f) => f.name).concat(COMMON_FABRICS)) {
          if (n && !seen.has(n)) { seen.add(n); names.push(n); }
        }
        this.setData({ fabricOptions: names.slice(0, 24) });
      })
      .catch(() => { /* 历史面料拉不到就用内置面料池 */ });
  },

  onFormFabric(e: WechatMiniprogram.Input) { this.setData({ 'form.fabric': e.detail.value }); },

  /** 点面料 chip：再点已选中的取消选择（清空回归自定义输入） */
  onPickFabric(e: WechatMiniprogram.TouchEvent) {
    const name = String((e.currentTarget.dataset as { name?: string }).name || '');
    if (!name) return;
    this.setData({ 'form.fabric': this.data.form.fabric === name ? '' : name });
  },

  loadCustomers() {
    fetchCustomers({ page: 1, page_size: 100 })
      .then((res) => {
        const customers = res.results || [];
        this.setData({
          customers,
          customerNames: ['不关联客户'].concat(customers.map((c) => c.name))
        });
      })
      .catch(() => { /* 客户下拉失败不阻塞款式列表 */ });
  },

  // ============ 列表 ============

  reload() {
    const kw = this.data.keyword.trim();
    const params: Record<string, unknown> = { page: 1, page_size: PAGE_SIZE };
    if (kw) params.keyword = kw;
    this.setData({ loading: true, error: false, page: 1, styles: [] });
    fetchStyles(params as Parameters<typeof fetchStyles>[0])
      .then((res: Paginated<Style>) => {
        this.setData({
          styles: (res.results || []).map(toVM),
          hasMore: !!res.next,
          loading: false
        });
      })
      .catch(() => this.setData({ loading: false, error: true }));
  },

  loadMore() {
    if (!this.data.hasMore || this.data.loading) return;
    const nextPage = this.data.page + 1;
    const kw = this.data.keyword.trim();
    const params: Record<string, unknown> = { page: nextPage, page_size: PAGE_SIZE };
    if (kw) params.keyword = kw;
    this.setData({ loading: true });
    fetchStyles(params as Parameters<typeof fetchStyles>[0])
      .then((res: Paginated<Style>) => {
        this.setData({
          styles: this.data.styles.concat((res.results || []).map(toVM)),
          hasMore: !!res.next,
          page: nextPage,
          loading: false
        });
      })
      .catch(() => this.setData({ loading: false }));
  },

  onReachBottom() {
    this.loadMore();
  },

  onKeyword(e: WechatMiniprogram.Input) {
    this.setData({ keyword: e.detail.value });
  },

  onSearch() {
    this.reload();
  },

  // ============ 卡片操作 ============

  /** 卡片单击进款式详情页（2026-10-22 用户需求：单击即显详情）；⋮ 菜单为 catchtap 不受影响 */
  onCardTap(e: WechatMiniprogram.TouchEvent) {
    const s = this.data.styles[Number(e.currentTarget.dataset.index)];
    if (!s) return;
    wx.navigateTo({ url: `/packages/admin/style-detail/index?id=${s.id}` });
  },

  /**
   * 卡片 ⋮ 菜单：编辑/复制属 styles.manage（进本页已过门），删除 → styles.delete
   *（高危点，legacy coarse 'styles' 不覆盖，须显式授权或 '*'）。
   */
  onCardMenu(e: WechatMiniprogram.TouchEvent) {
    const idx = Number(e.currentTarget.dataset.index);
    const s = this.data.styles[idx];
    if (!s) return;
    const actions: Array<{ label: string; run: () => void }> = [
      { label: '编辑款式', run: () => this.openForm(s) },
      { label: '复制款式', run: () => this.onCopy(s) }
    ];
    if (hasPerm('styles.delete')) {
      actions.push({ label: '款式删除', run: () => this.onDelete(s) });
    }
    wx.showActionSheet({
      itemList: actions.map((a) => a.label),
      success: (res) => {
        const act = actions[res.tapIndex];
        if (act) act.run();
      }
    });
  },

  onCopy(s: StyleVM) {
    copyStyle(s.id)
      .then((created) => {
        wx.showToast({ title: `已复制为 ${created.style_no}`, icon: 'none' });
        this.reload();
      })
      .catch(() => { /* toast 已由请求层弹出 */ });
  },

  onDelete(s: StyleVM) {
    wx.showModal({
      title: '删除款式',
      content: `确定删除「${s.style_no} ${s.name}」？已有菲票的款式无法删除。`,
      confirmColor: '#F53F3F',
      success: (m) => {
        if (!m.confirm) return;
        deleteStyle(s.id)
          .then(() => {
            wx.showToast({ title: '已删除', icon: 'success' });
            this.reload();
          })
          .catch(() => { /* toast 已由请求层弹出 */ });
      }
    });
  },

  // ============ 新建 / 编辑 ============

  onAdd() {
    this.openForm(null);
  },

  openForm(s: StyleVM | null) {
    const idx = s && s.customer
      ? this.data.customers.findIndex((c) => c.id === s.customer) + 1
      : 0;
    const raw = (s || {}) as unknown as Record<string, unknown>;
    const rawImage = String(raw.image || '');
    // 款式图片多图（≤9）：images 数组优先，存量单图 image 兜底为首张
    const images = Array.isArray(raw.images) && (raw.images as unknown[]).length
      ? (raw.images as unknown[]).map(String)
      : (rawImage ? [rawImage] : []);
    // 工艺信息（可能缺省，全部兜底）
    const craftImages = Array.isArray(raw.craft_images) ? (raw.craft_images as string[]).map(String) : [];
    const video = String(raw.video || '');
    this.setData({
      formVisible: true,
      form: {
        id: s ? s.id : 0,
        style_no: s ? s.style_no : '',
        name: s ? s.name : '',
        customerIndex: idx,
        customer: s ? (s.customer || 0) : 0,
        remark: s ? s.remark : '',
        fabric: String(raw.fabric || ''),
        // image 存 URL 串（后端 Style.image 为 CharField，恒=images 首张）
        image: images[0] || '',
        imageUrl: images[0] ? absUrl(images[0]) : '',
        uploading: false,
        images,
        imageUrls: images.map((u) => absUrl(u)),
        craft_note: String(raw.craft_note || ''),
        craftImages,
        craftImageUrls: craftImages.map((u) => absUrl(u)),
        craftUploading: false,
        video,
        videoUrl: absUrl(video),
        videoUploading: false
      }
    });
  },

  onFormNo(e: WechatMiniprogram.Input) { this.setData({ 'form.style_no': e.detail.value }); },
  onFormName(e: WechatMiniprogram.Input) { this.setData({ 'form.name': e.detail.value }); },
  // 备注输入已按用户要求下架（2026-10-22）：form.remark 保留透传（编辑旧款式不丢已有备注）
  onFormCustomer(e: WechatMiniprogram.PickerChange) {
    const idx = Number(e.detail.value);
    const c = idx > 0 ? this.data.customers[idx - 1] : null;
    this.setData({ 'form.customerIndex': idx, 'form.customer': c ? c.id : 0 });
  },

  /** 款式图片多图（≤9 张）：多选 → 逐张压缩上传 → images 数组（image 恒=首张由后端同步） */
  onFormImage() {
    if (this.data.form.uploading) return;
    const left = MAX_IMAGES - this.data.form.images.length;
    if (left <= 0) {
      wx.showToast({ title: `款式图片最多 ${MAX_IMAGES} 张`, icon: 'none' });
      return;
    }
    wx.chooseMedia({
      count: left,
      mediaType: ['image'],
      sizeType: ['compressed'],
      success: (res) => {
        const paths = (res.tempFiles || []).map((f) => f.tempFilePath).filter(Boolean);
        if (!paths.length) return;
        this.uploadStyleImages(paths);
      },
      fail: () => { /* 用户取消 */ }
    });
  },

  async uploadStyleImages(paths: string[]) {
    this.setData({ 'form.uploading': true });
    wx.showLoading({ title: '上传图片…', mask: true });
    let done = 0;
    try {
      for (const p of paths) {
        if (this.data.form.images.length >= MAX_IMAGES) break;
        const url = await uploadImage(p);
        done++;
        this.setData({
          'form.images': this.data.form.images.concat([url]),
          'form.imageUrls': this.data.form.imageUrls.concat([absUrl(url)])
        });
      }
    } catch (e) {
      const msg = (e as Error).message || '上传失败';
      wx.showToast({ title: done ? `已传 ${done} 张，${msg}` : msg, icon: 'none' });
    } finally {
      wx.hideLoading();
      this.setData({ 'form.uploading': false, 'form.image': this.data.form.images[0] || '' });
    }
  },

  onPreviewStyleImage(e: WechatMiniprogram.TouchEvent) {
    const idx = Number(e.currentTarget.dataset.index);
    const urls = this.data.form.imageUrls;
    if (!urls.length) return;
    wx.previewImage({ current: urls[idx] || urls[0], urls });
  },

  onRemoveFormImage(e: WechatMiniprogram.TouchEvent) {
    if (this.data.form.uploading) return;
    const idx = Number(e.currentTarget.dataset.index);
    const imgs = this.data.form.images.slice();
    const urls = this.data.form.imageUrls.slice();
    if (idx < 0 || idx >= imgs.length) return;
    imgs.splice(idx, 1);
    urls.splice(idx, 1);
    this.setData({
      'form.images': imgs,
      'form.imageUrls': urls,
      'form.image': imgs[0] || '',
      'form.imageUrl': urls[0] || ''
    });
  },

  // ---- 工艺说明（多行） ----
  onFormCraftNote(e: WechatMiniprogram.Input) {
    this.setData({ 'form.craft_note': e.detail.value });
  },

  // ---- 工艺图片（≤9 张多选，压缩上传，点图预览） ----
  onFormCraftImages() {
    if (this.data.form.craftUploading) return;
    const left = MAX_CRAFT_IMAGES - this.data.form.craftImages.length;
    if (left <= 0) {
      wx.showToast({ title: `最多 ${MAX_CRAFT_IMAGES} 张`, icon: 'none' });
      return;
    }
    wx.chooseMedia({
      count: left,
      mediaType: ['image'],
      sizeType: ['compressed'],
      success: (res) => {
        const paths = (res.tempFiles || []).map((f) => f.tempFilePath).filter(Boolean);
        if (!paths.length) return;
        this.uploadCraftImages(paths);
      },
      fail: () => { /* 用户取消 */ }
    });
  },

  async uploadCraftImages(paths: string[]) {
    this.setData({ 'form.craftUploading': true });
    wx.showLoading({ title: '上传图片…', mask: true });
    let done = 0;
    try {
      for (const p of paths) {
        if (this.data.form.craftImages.length >= MAX_CRAFT_IMAGES) break;
        const url = await uploadImage(p);
        done++;
        this.setData({
          'form.craftImages': this.data.form.craftImages.concat([url]),
          'form.craftImageUrls': this.data.form.craftImageUrls.concat([absUrl(url)])
        });
      }
    } catch (e) {
      const msg = (e as Error).message || '上传失败';
      wx.showToast({ title: done ? `已传 ${done} 张，${msg}` : msg, icon: 'none' });
    } finally {
      wx.hideLoading();
      this.setData({ 'form.craftUploading': false });
    }
  },

  onPreviewCraftImage(e: WechatMiniprogram.TouchEvent) {
    const idx = Number(e.currentTarget.dataset.index);
    const urls = this.data.form.craftImageUrls;
    if (!urls.length) return;
    wx.previewImage({ current: urls[idx] || urls[0], urls });
  },

  onRemoveCraftImage(e: WechatMiniprogram.TouchEvent) {
    if (this.data.form.craftUploading) return;
    const idx = Number(e.currentTarget.dataset.index);
    const imgs = this.data.form.craftImages.slice();
    const urls = this.data.form.craftImageUrls.slice();
    if (idx < 0 || idx >= imgs.length) return;
    imgs.splice(idx, 1);
    urls.splice(idx, 1);
    this.setData({ 'form.craftImages': imgs, 'form.craftImageUrls': urls });
  },

  // ---- 产品视频（chooseMedia video → 预校验 mp4/100MB → /uploads/video） ----
  onFormVideo() {
    if (this.data.form.videoUploading) return;
    chooseVideo()
      .then((tempPath: string) => {
        this.setData({ 'form.videoUploading': true });
        wx.showLoading({ title: '视频上传中…', mask: true });
        return uploadVideo(tempPath);
      })
      .then((url: string) => {
        this.setData({ 'form.video': url, 'form.videoUrl': url });
        wx.showToast({ title: '视频已上传', icon: 'success' });
      })
      .catch((err: Error) => {
        if (err && err.message && err.message !== '未选择视频') {
          wx.showToast({ title: err.message, icon: 'none' });
        }
      })
      .then(() => {
        wx.hideLoading();
        this.setData({ 'form.videoUploading': false });
      });
  },

  onRemoveFormVideo() {
    if (this.data.form.videoUploading) return;
    this.setData({ 'form.video': '', 'form.videoUrl': '' });
  },

  onCloseForm() {
    this.setData({ formVisible: false });
  },

  noop() { /* 阻止冒泡 */ },

  onSave() {
    const f = this.data.form;
    if (!f.style_no.trim()) {
      wx.showToast({ title: '请输入款号', icon: 'none' });
      return;
    }
    if (!f.name.trim()) {
      wx.showToast({ title: '请输入款式名称', icon: 'none' });
      return;
    }
    const payload: Partial<Style> = {
      style_no: f.style_no.trim(),
      name: f.name.trim(),
      remark: f.remark.trim(),
      fabric: f.fabric.trim(),
      customer: f.customer || null,
      // 款式图片多图：images 数组为主，image 兜底首张（后端也会同步）
      images: f.images,
      image: f.images[0] || '',
      // 工艺信息（F1/B1 契约；空值原样提交，后端可清空）
      craft_note: f.craft_note.trim(),
      craft_images: f.craftImages,
      video: f.video
    };
    this.setData({ saving: true });
    const done = () => {
      this.setData({ saving: false, formVisible: false });
      wx.showToast({ title: '已保存', icon: 'success' });
      this.reload();
    };
    if (f.id) {
      updateStyle(f.id, payload).then(done).catch(() => this.setData({ saving: false }));
    } else {
      createStyle(payload).then(done).catch(() => this.setData({ saving: false }));
    }
  }
});
