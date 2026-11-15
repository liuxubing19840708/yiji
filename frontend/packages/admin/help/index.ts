/**
 * 使用帮助（§2.11 P3，纯文档静态页）：按角色三篇短指引（老板/裁床/车位），
 * 每篇 = 核心动线 + 常见问题 两段，替代一对一培训。
 * - profile「使用帮助」进入，默认角色按当前岗位猜测（管理员→老板，其余→车位）；
 * - 支持带参直达：?role=boss|cutter|sewer；
 * - 纯静态内容，无接口依赖（断网可看）。
 */
import { requireLogin } from '../../../utils/auth';

/** 角色文章结构：wxml 用 wx:for 渲染段落，内容集中在本文件便于统一维护 */
interface HelpSection {
  title: string;
  lines: string[];
}

interface HelpArticle {
  key: 'boss' | 'cutter' | 'sewer';
  tab: string;
  icon: string;
  sections: HelpSection[];
}

const ARTICLES: HelpArticle[] = [
  {
    key: 'boss',
    tab: '老板',
    icon: '💼',
    sections: [
      {
        title: '核心动线',
        lines: [
          '1. 下单：先建「订单」（单号 SO 开头自动生成），录入客户、交期与颜色尺码数量，订单卡片可随时看完成率与交期灯。',
          '2. 开裁：款式管理建款上工序价 → 生产管理开床编菲 → 打印菲票下发车间。',
          '3. 看进度：首页「生产看板」按日/周/月看汇总与计划表；点表格里的款号可一直下钻到床次、菲票、每扎登记明细，返回时筛选不丢。',
          '4. 看板里的「订单」页签：完成率环形、交期灯（绿充裕/黄≤7天/红逾期）、落后标红，未挂单的床次会在卡头汇总提醒。',
          '5. 发工资：薪酬管理按期间结算，单价快照锁价，改价不影响历史。'
        ]
      },
      {
        title: '常见问题',
        lines: [
          '问：看板的「完工」口径是什么？—— 一张菲票配满全部工序才算完工，在制=已开工未配满，改口径只动一处统计，各页数字一致。',
          '问：「菲票数量」和订单数量有什么区别？—— 菲票数量=该款该色实际生成的菲票件数；订单数量挂在「订单」页签里按单统计，两者不相混。',
          '问：员工说工资不对？—— 登记明细按菲票可逐扎核查；已结算不可撤，未结算可按权限撤销重登。'
        ]
      }
    ]
  },
  {
    key: 'cutter',
    tab: '裁床',
    icon: '✂️',
    sections: [
      {
        title: '核心动线',
        lines: [
          '1. 接单开床：生产管理 → 新建床次（可挂订单，来源单号自动带出）→ 编菲生成扎号 → 打印菲票（打印管理里先绑定蓝牙打印机）。',
          '2. 日常操作：床次卡片 ⋮ 菜单可锁定/解锁、置裁完/取消、重打菲票；裁完不拦截登记，只作进度标记。',
          '3. 领面料：轻量仓库开领料单并挂批次，床次×批次血缘自动建立，出问题可全程追溯。',
          '4. 看任务：生产看板「已裁数量/已裁合计」列随日期范围刷新，混色床各色行都会计入。'
        ]
      },
      {
        title: '常见问题',
        lines: [
          '问：菲票打错了/少了扎？—— 床次详情可加扎重打；已打印的菲票建议作废重编，避免车间扫到重复扎。',
          '问：一床多色怎么算？—— 菲票按色×码生成，登记和已裁都按各色菲票分别统计。',
          '问：床次被锁了还能登记吗？—— 锁定只是防止误操作改动，扫菲登记不受影响；解锁需 production.lock 权限。'
        ]
      }
    ]
  },
  {
    key: 'sewer',
    tab: '车位',
    icon: '🧵',
    sections: [
      {
        title: '核心动线',
        lines: [
          '1. 扫菲三步：扫菲页扫码 → 选工序 → 输件数，登记即计件。',
          '2. 扫不上码？用「手输码」入口：输床号+扎号，系统列出候选菲票，选中后照常登记。',
          '3. 看自己：首页两块数字（今日件数/今日预估工资）随时看当日成果；「我的登记」页可撤销本人当日登记。',
          '4. 满扎提醒：扎件数登满（96/100 扎口）会亮徽标，满扎请交下一工序或裁床收扎。'
        ]
      },
      {
        title: '常见问题',
        lines: [
          '问：登错了怎么办？—— 本人当日可在「我的登记」撤销重登；隔日找管理员当月内代撤，已结算的不能再撤。',
          '问：帮工友代登？—— 管理员用「协助扫菲」，登记会记录真实操作人，工资仍归工友。',
          '问：为什么这件没工价？—— 该工序未上价（0 价工序会被拦截提示），找老板在款式或订单工序里补价后重登。'
        ]
      }
    ]
  }
];

Page({
  data: {
    roles: ARTICLES.map((a) => ({ key: a.key, tab: a.tab, icon: a.icon })),
    current: 'boss' as HelpArticle['key'],
    article: ARTICLES[0]
  },

  onLoad(options: Record<string, string>) {
    // 带参直达（?role=boss|cutter|sewer）；非法/缺省按岗位猜默认角色
    const role = String(options.role || '') as HelpArticle['key'];
    if (ARTICLES.some((a) => a.key === role)) {
      this.applyRole(role);
      return;
    }
    const app = getApp<IAppOption>();
    this.applyRole(app.globalData.isAdmin ? 'boss' : 'sewer');
  },

  onShow() {
    requireLogin();
  },

  applyRole(key: HelpArticle['key']) {
    const article = ARTICLES.find((a) => a.key === key) || ARTICLES[0];
    this.setData({ current: article.key, article });
  },

  onRoleTap(e: WechatMiniprogram.TouchEvent) {
    const key = String(e.currentTarget.dataset.key || '') as HelpArticle['key'];
    if (key) this.applyRole(key);
  }
});
