Component({
  properties: {
    /** 说明文字：默认只显示 ⓘ 图标，点击图标后展开/收起 */
    text: { type: String, value: '' }
  },

  data: {
    open: false
  },

  methods: {
    onToggle() {
      this.setData({ open: !this.data.open });
    }
  }
});
