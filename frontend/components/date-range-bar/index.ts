/**
 * 日期区间条。
 * change 事件：{ start, end, quick } —— quick 为 'today'|'yesterday'|'month'|'last_month'|''（自定义时为空）。
 * 快捷 tab 与手动日历互斥：手动改日期后 quick 置空（规格 §4.2）。
 */
import { today, yesterday, monthRange, lastMonthRange } from '../../utils/format';

const TABS = [
  { key: 'today', label: '今日' },
  { key: 'yesterday', label: '昨日' },
  { key: 'month', label: '本月' },
  { key: 'last_month', label: '上月' }
] as const;

type QuickKey = typeof TABS[number]['key'] | '';

Component({
  properties: {
    start: { type: String, value: '' },
    end: { type: String, value: '' },
    quick: { type: String, value: '' }
  },

  data: {
    tabs: TABS
  },

  methods: {
    emit(start: string, end: string, quick: QuickKey) {
      this.setData({ start, end, quick });
      this.triggerEvent('change', { start, end, quick });
    },

    onQuick(e: WechatMiniprogram.TouchEvent) {
      const quick = (e.currentTarget.dataset.quick || '') as QuickKey;
      if (quick === this.data.quick) return;
      if (quick === 'today') {
        this.emit(today(), today(), 'today');
      } else if (quick === 'yesterday') {
        this.emit(yesterday(), yesterday(), 'yesterday');
      } else if (quick === 'month') {
        const r = monthRange();
        this.emit(r.start, r.end, 'month');
      } else if (quick === 'last_month') {
        const r = lastMonthRange();
        this.emit(r.start, r.end, 'last_month');
      }
    },

    /** 手动改起止日期：快捷 tab 取消高亮 */
    onStartChange(e: WechatMiniprogram.PickerChange) {
      const start = String(e.detail.value);
      let end = this.data.end;
      if (end && end < start) {
        end = start;
      }
      this.emit(start, end || start, '');
    },

    onEndChange(e: WechatMiniprogram.PickerChange) {
      const end = String(e.detail.value);
      let start = this.data.start;
      if (start && start > end) {
        start = end;
      }
      this.emit(start || end, end, '');
    }
  }
});
