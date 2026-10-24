/**
 * chips 编辑器（颜色/尺码等）。
 * change: { index }（选中项变化）
 * add: {}（点＋，页面弹输入框后调 addChip(label)）
 * remove: { index }
 */
export interface ChipItem {
  label: string;
  badge?: number;
}

Component({
  properties: {
    items: { type: Array, value: [] as ChipItem[] },
    activeIndex: { type: Number, value: -1 },
    addable: { type: Boolean, value: false },
    removable: { type: Boolean, value: false }
  },

  methods: {
    onTap(e: WechatMiniprogram.TouchEvent) {
      const index = Number(e.currentTarget.dataset.index);
      this.setData({ activeIndex: index });
      this.triggerEvent('change', { index });
    },

    onRemove(e: WechatMiniprogram.TouchEvent) {
      const index = Number(e.currentTarget.dataset.index);
      this.triggerEvent('remove', { index });
    },

    onAddInput() {
      this.triggerEvent('add', {});
    },

    /** 页面在 add 事件里弹输入后调用，追加 chip */
    addChip(label: string) {
      const items = [...(this.data.items as ChipItem[]), { label }];
      this.setData({ items });
      this.triggerEvent('change', { index: items.length - 1 });
    }
  }
});
