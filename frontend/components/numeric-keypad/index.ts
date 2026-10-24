/**
 * 数字软键盘（车间戴手套快速录入）。
 * input: { value }（字符串，未确认）  confirm: { value }
 * 表单页用受控模式：bindinput 后调 setValue() 回写显示。
 */
const KEYS = ['1', '2', '3', '4', '5', '6', '7', '8', '9', '.', '0', '00'];

Component({
  properties: {
    /** 允许小数点（金额用 true，件数用 false） */
    decimal: { type: Boolean, value: false },
    maxValue: { type: String, value: '' },
    placeholder: { type: String, value: '0' }
  },

  data: {
    keys: KEYS,
    display: ''
  },

  methods: {
    currentValue(): string {
      return this.data.display;
    },

    /** 受控回显（父页面统一输入源时用） */
    setValue(value: string) {
      this.setData({ display: value });
    },

    onKey(e: WechatMiniprogram.TouchEvent) {
      const key = String(e.currentTarget.dataset.key);
      let value = this.data.display;
      if (key === '.') {
        if (!this.properties.decimal) return;
        if (value.includes('.')) return;
        value = value === '' ? '0.' : value + '.';
      } else if (key === '00') {
        if (value === '' || value === '0') return;
        value += '00';
      } else {
        if (value === '0') {
          value = key;
        } else {
          value += key;
        }
      }
      // 位数上限与最大值保护
      if (value.replace('.', '').length > 10) return;
      const max = this.properties.maxValue;
      if (max && Number(value) > Number(max)) {
        value = max;
      }
      this.setData({ display: value });
      this.triggerEvent('input', { value });
    },

    onBackspace() {
      const value = this.data.display.slice(0, -1);
      this.setData({ display: value });
      this.triggerEvent('input', { value });
    },

    onClear() {
      this.setData({ display: '' });
      this.triggerEvent('input', { value: '' });
    },

    onConfirm() {
      this.triggerEvent('confirm', { value: this.data.display });
    }
  }
});
