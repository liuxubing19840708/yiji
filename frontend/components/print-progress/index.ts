/**
 * 打印进度弹层组件：显示 done/total + 进度条 + 取消按钮。
 * 状态由宿主页面经 print-flow（utils/print-flow.ts）注入并驱动；
 * 取消点击 → triggerEvent('cancel') → 页面 onFlowCancel → cancelPrintFlow()。
 */
Component({
  properties: {
    /** 是否显示 */
    show: { type: Boolean, value: false },
    /** 已打印张数 */
    done: { type: Number, value: 0 },
    /** 总张数 */
    total: { type: Number, value: 0 }
  },

  methods: {
    noop() { /* 阻止冒泡 */ },

    onCancel() {
      this.triggerEvent('cancel');
    }
  }
});
