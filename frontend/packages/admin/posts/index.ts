/**
 * 岗位管理：岗位列表（数量头、is_admin 徽标、成员数、权限点）、新建/编辑（名称 + 权限分组勾选）、
 * 卡片「⋯ 更多」菜单（编辑/复制岗位/查看成员/删除；系统岗位不可删，后端最终校验）。
 *
 * 权限点体系（Plan A 一期）：编辑弹窗按模块分组勾选细粒度权限点（GET /permissions 字典）；
 * 「全部权限」= 保存 '*'。旧 coarse key（styles/production/working/my_salary/customers）
 * 在回显时展开为其 legacy 覆盖的点（与后端 has_perm 兼容口径一致），保存后落细粒度点。
 */
import { requireLogin } from '../../../utils/auth';
import {
  fetchPosts, createPost, updatePost, deletePost, copyPost, fetchPermissionGroups
} from '../../../services/auth';
import { Post, PermissionGroup } from '../../../services/types';

/** 常用岗位词（痛点：岗位名手写各异；点选直接填充，仍可手写覆盖） */
const COMMON_POST_NAMES = ['平车工', '锁边', '烫工', '包装', '质检', '组长', '裁床', '后勤', '杂工'];

interface PostVM extends Post {
  /** 权限点展示文案（'*' 显示为「全部权限」） */
  permLabels: string[];
}

/** 编辑弹层的分组勾选 VM */
interface GroupVM {
  module: string;
  label: string;
  points: Array<{ key: string; label: string; checked: boolean }>;
}

Page({
  data: {
    loading: true,
    list: [] as PostVM[],
    // 表单弹层
    formVisible: false,
    editingId: 0, // 0 = 新建
    name: '',
    nameChips: COMMON_POST_NAMES,
    groups: [] as GroupVM[],
    allPerms: false, // 「全部权限」开关 → 保存 '*'
    submitting: false
  },

  /** 权限点字典（onShow 拉取；标签渲染与勾选展开都用它） */
  dict: [] as PermissionGroup[],

  onShow() {
    if (!requireLogin()) return;
    this.loadDict();
    this.load();
  },

  onPullDownRefresh() {
    this.load();
    wx.stopPullDownRefresh();
  },

  /** 权限点字典：失败时用空字典兜底（标签回落原始 key，勾选不可用但可保存旧 key） */
  loadDict() {
    fetchPermissionGroups()
      .then((groups) => { this.dict = groups; this.renderLabels(); })
      .catch(() => { this.dict = []; });
  },

  /** 列表权限标签按字典重渲染（字典异步到达后刷新一次） */
  renderLabels() {
    if (!this.dict.length || !this.data.list.length) return;
    this.setData({
      list: this.data.list.map((p) => ({ ...p, permLabels: this.buildLabels(p.permissions) }))
    });
  },

  /** permissions → 展示文案：'*' / 旧 coarse key / 细粒度点 */
  buildLabels(perms: string[]): string[] {
    const labels: string[] = [];
    for (const key of perms || []) {
      if (key === '*') { labels.push('全部权限'); continue; }
      if (key.indexOf('.') < 0) {
        // 旧 coarse key：按模块名找组标签
        const g = this.dict.find((x) => x.module === key);
        labels.push(g ? `${g.label}·全部` : key);
        continue;
      }
      const hit = this.dict
        .map((g) => g.points.find((p) => p.key === key))
        .find((p) => !!p);
      labels.push(hit ? (hit as { label: string }).label : key);
    }
    return labels;
  },

  load() {
    this.setData({ loading: true });
    fetchPosts()
      .then((posts) => {
        const list = posts.map((p) => ({ ...p, permLabels: this.buildLabels(p.permissions) }));
        this.setData({ loading: false, list });
      })
      .catch(() => this.setData({ loading: false, list: [] }));
  },

  // ---- 卡片「⋯ 更多」菜单：编辑 / 复制岗位 / 查看成员 / 删除 ----

  onMore(e: WechatMiniprogram.TouchEvent) {
    const id = Number(e.currentTarget.dataset.id);
    const post = this.data.list.find((p) => p.id === id);
    if (!post) return;
    const items: string[] = ['编辑', '复制岗位', '查看成员'];
    // 系统岗位不可删，菜单里不出现「删除」（后端也终校）
    if (!post.is_system) items.push('删除');
    wx.showActionSheet({
      itemList: items,
      success: (r) => {
        const label = items[r.tapIndex];
        if (label === '编辑') this.openEditById(post.id);
        else if (label === '复制岗位') this.copy(post);
        else if (label === '查看成员') this.viewMembers(post);
        else if (label === '删除') this.confirmDelete(post);
      }
    });
  },

  openEditById(id: number) {
    const post = this.data.list.find((p) => p.id === id);
    if (!post) return;
    const perms = post.permissions || [];
    const hasAll = perms.indexOf('*') >= 0;
    this.setData({
      formVisible: true,
      editingId: post.id,
      name: post.name,
      allPerms: hasAll,
      // 旧 coarse key 展开为其 legacy 覆盖点（与后端 has_perm 口径一致）
      groups: this.dict.map((g) => ({
        module: g.module,
        label: g.label,
        points: g.points.map((pt) => {
          const legacyKey = pt.legacy_key || g.module;
          const checked = hasAll
            || perms.indexOf(pt.key) >= 0
            || (!!pt.legacy && perms.indexOf(legacyKey) >= 0);
          return { key: pt.key, label: pt.label, checked };
        })
      }))
    });
  },

  /** 复制岗位（后端自动「副本」去重命名） */
  copy(post: PostVM) {
    copyPost(post.id)
      .then(() => {
        wx.showToast({ title: '已复制岗位', icon: 'success' });
        this.load();
      })
      .catch(() => {});
  },

  /** 查看成员：跳员工管理并按该岗位过滤 */
  viewMembers(post: PostVM) {
    wx.navigateTo({ url: `/packages/admin/employees/index?post=${post.id}` });
  },

  confirmDelete(post: PostVM) {
    wx.showModal({
      title: '删除岗位',
      content: `确定删除岗位「${post.name}」吗？`,
      success: (r) => {
        if (!r.confirm) return;
        deletePost(post.id)
          .then(() => {
            wx.showToast({ title: '已删除', icon: 'success' });
            this.load();
          })
          .catch(() => {});
      }
    });
  },

  // ---- 新建 / 编辑 ----

  openCreate() {
    this.setData({
      formVisible: true,
      editingId: 0,
      name: '',
      allPerms: false,
      groups: this.dict.map((g) => ({
        module: g.module,
        label: g.label,
        points: g.points.map((pt) => ({ key: pt.key, label: pt.label, checked: false }))
      }))
    });
  },

  closeForm() {
    if (this.data.submitting) return;
    this.setData({ formVisible: false });
  },

  /** 面板事件截流（空处理器名 catchtap="" 不拦截冒泡，必须绑真实方法） */
  noop() {},

  onNameInput(e: WechatMiniprogram.Input) {
    this.setData({ name: e.detail.value });
  },

  /** 常用岗位词点选：直接填充名称（可再手改） */
  onPickNameChip(e: WechatMiniprogram.TouchEvent) {
    const name = String(e.currentTarget.dataset.name || '');
    if (name) this.setData({ name });
  },

  onToggleAllPerms() {
    this.setData({ allPerms: !this.data.allPerms });
  },

  onTogglePerm(e: WechatMiniprogram.TouchEvent) {
    const key = String(e.currentTarget.dataset.key);
    this.setData({
      groups: this.data.groups.map((g) => ({
        ...g,
        points: g.points.map((pt) => (pt.key === key ? { ...pt, checked: !pt.checked } : pt))
      }))
    });
  },

  onSubmit() {
    if (this.data.submitting) return;
    const name = this.data.name.trim();
    if (!name) {
      wx.showToast({ title: '请填写岗位名称', icon: 'none' });
      return;
    }
    let permissions: string[];
    if (this.data.allPerms) {
      permissions = ['*'];
    } else {
      permissions = this.data.groups.reduce((acc: string[], g) => {
        g.points.forEach((pt) => { if (pt.checked) acc.push(pt.key); });
        return acc;
      }, []);
      if (!permissions.length) {
        wx.showToast({ title: '请至少勾选一个权限点', icon: 'none' });
        return;
      }
    }
    this.setData({ submitting: true });
    const req = this.data.editingId
      ? updatePost(this.data.editingId, name, permissions)
      : createPost(name, permissions);
    req.then(() => {
        wx.showToast({ title: this.data.editingId ? '岗位已更新' : '岗位已创建', icon: 'success' });
        this.setData({ formVisible: false });
        this.load();
      })
      .catch(() => {})
      .then(() => this.setData({ submitting: false }));
  }
});
