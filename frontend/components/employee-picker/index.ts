/**
 * 员工选择器（规格 §11.3）：底部弹层 = 搜索框 + 角色 Tab + 3 列姓名网格 + 橙色确定。
 * 复用场景：协助扫菲选员工、完成汇总筛选、结算选择、发放奖励等；单选/多选参数化。
 *
 * Tab 说明：后端 GET /employees 的 tab 参数仅支持 all（在职，默认）/ pending / resigned；
 * 「车间 / 裁床 / 管理员」为在职员工的前端过滤（按 post_name / is_admin，系统三岗位命名见 §11.2）。
 *
 * properties:
 *   visible  是否显示弹层
 *   multi    单选(false)/多选(true)
 *   selected 已选 Employee.id 集合（多选回显用）
 * events:
 *   confirm  { users: Employee[] }（单选时长度 1）
 *   close    点蒙层/关闭
 */
import { fetchEmployees } from '../../services/auth';
import { Employee } from '../../services/types';

type EmpRow = Employee & { initial: string; picked: boolean };

/** 角色页签：serverTab 为空串时拉在职列表再前端过滤 */
const TABS = [
  { key: 'all', name: '全部', serverTab: 'all' as const, post: '' },
  { key: 'chejian', name: '车间', serverTab: 'all' as const, post: '车间' },
  { key: 'caichuang', name: '裁床', serverTab: 'all' as const, post: '裁床' },
  { key: 'admin', name: '管理员', serverTab: 'all' as const, post: '' },
  { key: 'resigned', name: '离职', serverTab: 'resigned' as const, post: '' }
];

Component({
  properties: {
    visible: { type: Boolean, value: false },
    multi: { type: Boolean, value: false },
    selected: { type: Array, value: [] as number[] }
  },

  data: {
    tabs: TABS.map((t) => ({ key: t.key, name: t.name })),
    curTab: 'all',
    keyword: '',
    list: [] as EmpRow[],
    loading: false,
    loadError: false,
    pickedIds: [] as number[],
    /** 跨 Tab 累积的选中员工（confirm 数据源） */
    pickedRows: [] as Employee[]
  },

  observers: {
    visible(v: boolean) {
      if (!v) return;
      // 每次打开重置：回到全部 Tab，回显已选
      this.setData({
        curTab: 'all',
        keyword: '',
        pickedIds: ((this.properties.selected as number[]) || []).slice(),
        pickedRows: []
      });
      this.load();
    }
  },

  methods: {
    load() {
      const conf = TABS.find((t) => t.key === this.data.curTab) || TABS[0];
      this.setData({ loading: true, loadError: false });
      fetchEmployees(conf.serverTab, this.data.keyword.trim() || undefined)
        .then((list) => {
          let rows = list || [];
          if (conf.post) {
            rows = rows.filter((e) => (e.post_name || '').indexOf(conf.post) >= 0);
          } else if (conf.key === 'admin') {
            rows = rows.filter((e) => e.is_admin);
          }
          const pickedIds = this.data.pickedIds;
          this.setData({
            loading: false,
            list: rows.map((e) => ({
              ...e,
              initial: (e.name || '·').slice(0, 1),
              picked: pickedIds.indexOf(e.id) >= 0
            }))
          });
        })
        .catch(() => this.setData({ loading: false, loadError: true }));
    },

    onRetry() { this.load(); },

    onTapTab(e: WechatMiniprogram.TouchEvent) {
      const key = String(e.currentTarget.dataset.key);
      if (!key || key === this.data.curTab) return;
      this.setData({ curTab: key });
      this.load();
    },

    onSearchInput(e: WechatMiniprogram.Input) {
      this.setData({ keyword: e.detail.value });
    },

    /** 搜索（服务端 keyword，services/auth#fetchEmployees 已透出） */
    onSearch() {
      this.load();
    },

    onClearKeyword() {
      this.setData({ keyword: '' });
      this.load();
    },

    onTapEmp(e: WechatMiniprogram.TouchEvent) {
      const id = Number(e.currentTarget.dataset.id);
      const row = this.data.list.find((r) => r.id === id);
      if (!row) return;
      if (this.properties.multi) {
        const pickedIds = this.data.pickedIds.slice();
        let pickedRows = this.data.pickedRows.slice();
        const at = pickedIds.indexOf(id);
        if (at >= 0) {
          pickedIds.splice(at, 1);
          pickedRows = pickedRows.filter((r) => r.id !== id);
        } else {
          pickedIds.push(id);
          pickedRows.push(row);
        }
        const idx = this.data.list.indexOf(row);
        this.setData({ pickedIds, pickedRows, [`list[${idx}].picked`]: at < 0 });
      } else {
        // 单选：点选即唯一选中（再点取消）
        const isCancel = this.data.pickedIds[0] === id;
        const pickedIds = isCancel ? [] : [id];
        this.setData({
          pickedIds,
          pickedRows: isCancel ? [] : [row],
          list: this.data.list.map((r) => ({ ...r, picked: r.id === id && !isCancel }))
        });
      }
    },

    onConfirm() {
      const { pickedIds, pickedRows } = this.data;
      if (!pickedIds.length) {
        wx.showToast({ title: this.properties.multi ? '请先选择员工' : '请选择一名员工', icon: 'none' });
        return;
      }
      // pickedRows 跨 Tab 累积；缺行的 id（selected 回显但未在本轮加载中出现）以 id 兜底
      const users = pickedRows.filter((r) => pickedIds.indexOf(r.id) >= 0);
      this.triggerEvent('confirm', { users, ids: pickedIds.slice() });
    },

    onClose() {
      this.triggerEvent('close');
    },

    noop() { /* 阻止冒泡 */ }
  }
});
