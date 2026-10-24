/**
 * 统计卡轮播（管理员：完成件数/裁货数/工作中；员工：今日登记/收入/未结/已结/总工资）。
 * change: { start, end, quick } —— 日历页「应用」后触发（quick 恒为 ''）。
 */
export interface StatCard {
  title: string;
  value: string | number;
  unit?: string;
  sub?: string;
}

Component({
  properties: {
    cards: { type: Array, value: [] as StatCard[] },
    start: { type: String, value: '' },
    end: { type: String, value: '' }
  },

  data: {
    current: 0,
    pickStart: '',
    pickEnd: '',
    /** 按卡片数分行：≤3 项一行；>3 项 3+2 两行分组（数量/金额），swiper 高度联动 */
    rows: [] as StatCard[][],
    swiperH: 200
  },

  observers: {
    cards(list: StatCard[]) {
      const cards = list || [];
      if (cards.length <= 3) {
        this.setData({ rows: [cards], swiperH: 200 });
      } else {
        this.setData({ rows: [cards.slice(0, 3), cards.slice(3)], swiperH: 340 });
      }
    }
  },

  methods: {
    onSwiperChange(e: WechatMiniprogram.SwiperChange) {
      this.setData({ current: e.detail.current });
    },

    onStartPick(e: WechatMiniprogram.PickerChange) {
      this.setData({ pickStart: String(e.detail.value) });
    },

    onEndPick(e: WechatMiniprogram.PickerChange) {
      this.setData({ pickEnd: String(e.detail.value) });
    },

    onCalendarOk() {
      const start = this.data.pickStart || this.data.start;
      const end = this.data.pickEnd || this.data.end || start;
      if (!start) {
        wx.showToast({ title: '请选择开始日期', icon: 'none' });
        return;
      }
      this.triggerEvent('change', { start, end, quick: '' });
      this.setData({ current: 0 });
    }
  }
});
