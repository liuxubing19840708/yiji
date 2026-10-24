# -*- coding: utf-8 -*-
"""stats.py 口径唯一化防漂移套件（SA2，配套 docs/端到端测试设计方案.md §3.2）。

运行（连跑 2 次全绿为交付门）：
  .venv/Scripts/python.exe -X utf8 manage.py test test_stats_refactor -v 2

golden 快照说明（§九 风险表「stats 收编口径漂移」防线）：
  快照采集于 2026-10-20 重构前（临时脚本 tmp_golden_capture.py，跑完即删，
  数值硬编码于下方 GOLDEN）。同一「口径基准厂」数据集下，重构前后对照结果：

  · 逐位相等（重构前后一致）：工作台 beds、dashboard plans/daily/processes/
    rank/group_rank、按扎查看、扫码解析、款式统计、yesterday/last_month 汇总；
  · 两处【预期内】差异（收编即修复的既有口径 bug，非漂移；STAT-010 要求
    四消费方口径相等，与「保留缺陷行为」互斥，取与工作台一致的多数派口径）：
    1) dashboard summary done/working：前 120/125 → 后 170/75
       （原 pc_map 缺失回落循环把「有订单工序床次」的 pc 一并覆盖成款式工序数，
       混入 0 工序床次时全厂 pc 被改写 → GLD-B 满工序扎被误判工作中）；
    2) 床次列表 registered_qty：前 GLD-B 150 / GLD-A 585 → 后 50 / 195
       （原 _production_record_stats 过滤走 company__productions__id__in 跨表
       JOIN，厂内每张床把登记行放大 N 倍，N=厂内床次数=3）。
    其余数值（plans/daily/rank 等）不受这两处影响，逐位相等。
  本套件 golden 断言取【统一后】数值，上述差异在对应用例注释中前后对照留痕。

覆盖：STAT-001~004 golden 逐一相等；STAT-010~013 四消费方口径相等（含 pc=0
床次行为一致、混色床 cut_total 列内自洽）；STAT-020~022 range 六档边界
（custom 反转/非法回落、负数/未来日期安全）；STAT-030~031 plans 行 order_no
（多单取最早/无单空串，skipUnless 订单域就绪，TODO：INT 合并后回归）。
幂等：django.test.TestCase 事务回滚，不污染库；订单域未就绪时相关用例自动跳过。
"""
from datetime import timedelta
from unittest import skipUnless

from django.contrib.auth.hashers import make_password
from django.db.models import Sum
from django.test import Client, TestCase
from django.utils import timezone

from apps.accounts.models import Company, Post as PostM, User, UserCompany
from apps.accounts.services import create_company_with_owner
from apps.catalog.models import Process, Style, StyleProcess, Ticket
from apps.production.models import Production, ProductionProcess
from apps.records.models import Record

CO_NAME = '口径基准厂(可删)'
OWNER_PHONE = '13900139001'
U1_PHONE = '13900139002'
U2_PHONE = '13900139003'
STYLE_NO = 'GLD-S1'


def _orders_ready():
    """订单域（SA1 并行交付）是否就绪：apps.orders 可导入且 Production.order 已落。"""
    try:
        from apps.orders.models import Order  # noqa: F401  延迟 import
        from apps.production.models import Production as P
        P._meta.get_field('order')
        return True
    except Exception:
        return False


ORDERS_READY = _orders_ready()

# ---- golden 快照（统一后口径；差异处注释保留重构前数值）----
GOLDEN = {
    # STAT-001 工作台（beds 按 -id：C、B、A）
    'workbench_beds': [
        {'bed_no': 'GLD-C', 'total_qty': 0, 'registered_qty': 0, 'done_ticket_count': 0},
        {'bed_no': 'GLD-B', 'total_qty': 50, 'registered_qty': 50, 'done_ticket_count': 1},
        {'bed_no': 'GLD-A', 'total_qty': 300, 'registered_qty': 195, 'done_ticket_count': 1},
    ],
    'workbench_my_today_amount': '0.00',   # 厂长本人无登记（SA3 字段一并锁定）
    'workbench_u1_my_today_len': 4,        # 车工一今日 4 条登记
    # STAT-002 dashboard（range=month）。done/working 重构前为 120/125（见文件头注 1）
    'summary': {'done_qty': 170, 'working_qty': 75, 'cut_qty': 350,
                'registered_qty': 245, 'user_count': 2, 'ticket_count': 4,
                'active_beds': 3, 'cut_total': 350, 'order_total': 370},
    'daily_qty': [0, 0, 0, 0, 0, 0, 245],          # 近 7 天，仅今日有量
    'processes': [('GLD-工序1', 135, 55.1), ('GLD-工序2', 70, 28.6),
                  ('GLD-工序3', 40, 16.3)],
    'rank': [('车工一', 125, 3), ('车工二', 120, 3)],
    'group_rank': [(1, 245, 2, 6)],                # (组号, qty, 人数, 人均扎数和)
    # STAT-003 床次列表（-id：C、B、A）。registered_qty 重构前为 0/150/585（头注 2）
    'production_list': [
        {'bed_no': 'GLD-C', 'total_qty': 0, 'registered_qty': 0,
         'done_ticket_count': 0, 'ticket_count': 1, 'cut_total': 0},
        {'bed_no': 'GLD-B', 'total_qty': 50, 'registered_qty': 50,
         'done_ticket_count': 1, 'ticket_count': 1, 'cut_total': 50},
        {'bed_no': 'GLD-A', 'total_qty': 300, 'registered_qty': 195,
         'done_ticket_count': 1, 'ticket_count': 3, 'cut_total': 300},
    ],
    'bundles_a': {'process_count': 3, 'count': 3,
                  'rows': [('1', 'done', 120, 3), ('2', 'partial', 60, 2),
                           ('3', 'partial', 15, 1)]},  # (扎号,状态,登记件数,已登工序数)
    'scan_a1': {'process_count': 3, 'registered_process_count': 3, 'registered_qty': 120},
    # STAT-004 款式统计行
    'styles': {'cut_total': 370, 'ticket_count': 5, 'process_count': 3,
               'done_ticket_count': 1, 'unfinished_ticket_count': 4},
    # STAT-002 plans（行序按颜色字典序：灰/白/黑；cut_total 列内自洽=650）
    'plans': [
        ('灰', 20, 'GLD-C', 0, 650),
        ('白', 150, 'GLD-A', 350, 650),
        ('黑', 200, 'GLD-A', 300, 650),
    ],
    # STAT-020 range 边界：这些档的 summary 应与 month 完全一致（数据全在今日）
    'range_eq_month': ('today', 'week', 'custom_normal', 'custom_invalid',
                       'bogus', 'negative'),
    'zero_ranges': ('yesterday', 'last_month', 'custom_reversed', 'future'),
}


class StatsRefactorTests(TestCase):
    """golden 快照 + 四消费方口径相等 + range 边界 + plans order_no。"""

    @classmethod
    def setUpTestData(cls):
        owner = User.objects.filter(phone=OWNER_PHONE).first()
        if owner is None:
            owner = User.objects.create(phone=OWNER_PHONE, username=OWNER_PHONE,
                                        real_name='口径厂长',
                                        password=make_password('yisao1234'),
                                        is_active=True)
        cls.u1 = User.objects.filter(phone=U1_PHONE).first() or \
            User.objects.create(phone=U1_PHONE, username=U1_PHONE, real_name='车工一',
                                password=make_password('yisao1234'), is_active=True)
        cls.u2 = User.objects.filter(phone=U2_PHONE).first() or \
            User.objects.create(phone=U2_PHONE, username=U2_PHONE, real_name='车工二',
                                password=make_password('yisao1234'), is_active=True)
        # 测试库内公司名唯一（同名残留属异常，直接报错暴露）
        company, cls.admin_post = create_company_with_owner(owner, CO_NAME)
        cls.company = company
        cls.cid = company.id
        plain_post = PostM.objects.create(company=company, name='GLD-普工',
                                          is_admin=False)
        for u in (cls.u1, cls.u2):
            UserCompany.objects.create(user=u, company=company, post=plain_post,
                                       status=UserCompany.Status.ACTIVE)

        procs = [Process.objects.create(company=company, name=f'GLD-工序{i}',
                                        default_price=i) for i in (1, 2, 3)]
        cls.style = Style.objects.create(company=company, style_no=STYLE_NO,
                                         name='口径基准款')
        for i, p in enumerate(procs):
            StyleProcess.objects.create(style=cls.style, process=p, price=i + 1, sort=i)

        cls.prod_a = Production.objects.create(company=company, style=cls.style,
                                               bed_no='GLD-A', total_qty=300,
                                               plan_qty=300)
        cls.prod_b = Production.objects.create(company=company, style=cls.style,
                                               bed_no='GLD-B', total_qty=50)
        cls.prod_c = Production.objects.create(company=company, style=cls.style,
                                               bed_no='GLD-C', total_qty=0)
        for p in procs:
            ProductionProcess.objects.create(production=cls.prod_a, process=p, price=1)
        ProductionProcess.objects.create(production=cls.prod_b, process=procs[0], price=1)

        now = timezone.now()
        t_a1 = Ticket.objects.create(company=company, production=cls.prod_a,
                                     bed_no='GLD-A', bundle_no='1', color='黑',
                                     size='M', qty=100, qr_code='GLD-A1')
        t_a2 = Ticket.objects.create(company=company, production=cls.prod_a,
                                     bed_no='GLD-A', bundle_no='2', color='黑',
                                     size='L', qty=100, qr_code='GLD-A2')
        t_a3 = Ticket.objects.create(company=company, production=cls.prod_a,
                                     bed_no='GLD-A', bundle_no='3', color='白',
                                     size='M', qty=100, qr_code='GLD-A3')
        t_b1 = Ticket.objects.create(company=company, production=cls.prod_b,
                                     bed_no='GLD-B', bundle_no='1', color='白',
                                     size='L', qty=50, qr_code='GLD-B1')
        Ticket.objects.create(company=company, production=cls.prod_c,
                              bed_no='GLD-C', bundle_no='1', color='灰', size='M',
                              qty=20, qr_code='GLD-C1')
        # 登记：A1 配满 3 工序（完工）；A2 登 2（在制）；A3 登 1（在制）；
        #      B1 配满 1 工序（完工）；C1 无登记
        for t, p, u, q in [
            (t_a1, procs[0], cls.u1, 40), (t_a1, procs[1], cls.u1, 40),
            (t_a1, procs[2], cls.u2, 40), (t_a2, procs[0], cls.u1, 30),
            (t_a2, procs[1], cls.u2, 30), (t_a3, procs[0], cls.u1, 15),
            (t_b1, procs[0], cls.u2, 50),
        ]:
            Record.objects.create(company=company, ticket=t, process=p, user=u,
                                  qty=q, price='1.000', registered_at=now)

        cls.tokens = {phone: cls._login(phone) for phone in
                      (OWNER_PHONE, U1_PHONE, U2_PHONE)}
        cls.today = timezone.localdate()

    @classmethod
    def _login(cls, phone):
        c = Client()
        import json
        r = c.post('/api/auth/login',
                   data=json.dumps({'phone': phone, 'password': 'yisao1234'}),
                   content_type='application/json')
        return json.loads(r.content.decode('utf-8'))['access']

    # ---- 请求助手 ----
    def api(self, method, path, token, data=None):
        import json
        c = Client()
        r = getattr(c, method)(f'/api{path}', data=data,
                               content_type='application/json',
                               HTTP_AUTHORIZATION=f'Bearer {token}',
                               HTTP_X_COMPANY_ID=str(self.cid))
        return json.loads(r.content.decode('utf-8'))

    def owner(self):
        return self.tokens[OWNER_PHONE]

    # ==================================================================
    # STAT-001~004 golden：重构前后逐一相等（差异处见 GOLDEN 注释）
    # ==================================================================
    def test_stat001_golden_workbench(self):
        """STAT-001 工作台完成度 golden（重构前快照逐位相等）。"""
        d = self.api('get', '/records/workbench', self.owner())
        got = [{'bed_no': b['bed_no'], 'total_qty': b['total_qty'],
                'registered_qty': b['registered_qty'],
                'done_ticket_count': b['done_ticket_count']}
               for b in d['beds']]
        self.assertEqual(got, GOLDEN['workbench_beds'])
        self.assertEqual(d['my_today_amount'], GOLDEN['workbench_my_today_amount'])
        self.assertEqual(len(d['my_today']), 0)  # 厂长本人今日无登记
        d1 = self.api('get', '/records/workbench', self.tokens[U1_PHONE])
        self.assertEqual(len(d1['my_today']), GOLDEN['workbench_u1_my_today_len'])

    def test_stat002_golden_dashboard_month(self):
        """STAT-002 dashboard 汇总/计划表/柱图/工序/排行 golden。

        done/working 为统一后值（重构前 120/125，头注 1）；其余逐位相等。
        """
        d = self.api('get', '/dashboard?range=month', self.owner())
        self.assertEqual(d['range'], 'month')
        self.assertEqual(d['summary'], GOLDEN['summary'])
        # plans：行序（灰/白/黑）、数量、床次、已裁、列内自洽 cut_total
        plans = [(r['color'], r['order_qty'],
                  [b['bed_no'] for b in r['beds']],
                  [b['qty'] for b in r['beds']],
                  r['cut_qty'], r['cut_total']) for r in d['plans']]
        self.assertEqual(plans, [
            ('灰', 20, ['GLD-C'], [20], 0, 650),
            ('白', 150, ['GLD-A', 'GLD-B'], [100, 50], 350, 650),
            ('黑', 200, ['GLD-A'], [200], 300, 650),
        ])
        # daily：近 7 天仅今日有量 245（数量序列 golden；日期/星期随运行日漂移不断言）
        self.assertEqual([x['qty'] for x in d['daily']], GOLDEN['daily_qty'])
        self.assertEqual(len(d['daily']), 7)
        # processes Top：qty 与 percent golden
        self.assertEqual([(p['name'], p['qty'], p['percent']) for p in d['processes']],
                         GOLDEN['processes'])
        self.assertEqual([(r['name'], r['qty'], r['ticket_count']) for r in d['rank']],
                         GOLDEN['rank'])
        self.assertEqual([(r['group_no'], r['qty'], r['user_count'],
                           r['ticket_count']) for r in d['group_rank']],
                         GOLDEN['group_rank'])

    def test_stat003_golden_production_list_bundles_scan(self):
        """STAT-003 床次列表/按扎查看/扫码解析 golden。

        床次列表 registered_qty 为统一后值（重构前 B=150/A=585，join 放大 bug，
        头注 2）；按扎与扫码解析重构前后逐位相等。
        """
        d = self.api('get', '/productions?page_size=50', self.owner())
        got = [{'bed_no': it['bed_no'], 'total_qty': it['total_qty'],
                'registered_qty': it['registered_qty'],
                'done_ticket_count': it['done_ticket_count'],
                'ticket_count': it['ticket_count'],
                'cut_total': it['cut_total']} for it in d['results']]
        self.assertEqual(got, GOLDEN['production_list'])
        # 按扎查看：状态/登记件数/已登工序数逐扎相等
        b = self.api('get', f'/productions/{self.prod_a.id}/bundles', self.owner())
        self.assertEqual((b['process_count'], b['count']),
                         (GOLDEN['bundles_a']['process_count'],
                          GOLDEN['bundles_a']['count']))
        rows = [(x['bundle_no'], x['state'], x['registered_qty'],
                 x['registered_process_count']) for x in b['results']]
        self.assertEqual(rows, GOLDEN['bundles_a']['rows'])
        # 扫码解析：完工扎 registered_process_count=3 / registered_qty=120
        company_no = self.company.company_no
        s = self.api('post', '/scan/parse', self.owner(),
                     {'qr': f'{company_no}|GLD-A|{STYLE_NO}|1|黑|M|100'})
        self.assertEqual(s['production']['process_count'],
                         GOLDEN['scan_a1']['process_count'])
        self.assertEqual(s['production']['registered_process_count'],
                         GOLDEN['scan_a1']['registered_process_count'])
        self.assertEqual(s['production']['registered_qty'],
                         GOLDEN['scan_a1']['registered_qty'])

    def test_stat004_golden_styles(self):
        """STAT-004 款式统计行 golden（重构前后逐位相等）。"""
        d = self.api('get', '/styles?page_size=50', self.owner())
        target = next(x for x in d['results'] if x['style_no'] == STYLE_NO)
        for key, val in GOLDEN['styles'].items():
            self.assertEqual(target[key], val, key)

    # ==================================================================
    # STAT-010~013 同一数据集四消费方口径相等
    # ==================================================================
    def test_stat010_four_consumers_agree(self):
        """STAT-010 四消费方口径相等：工作台=床次列表=dashboard=款式统计。"""
        wb = self.api('get', '/records/workbench', self.owner())
        pl = self.api('get', '/productions?page_size=50', self.owner())
        dash = self.api('get', '/dashboard?range=today', self.owner())
        wb_map = {b['bed_no']: b for b in wb['beds']}
        pl_map = {it['bed_no']: it for it in pl['results']}
        for bed in ('GLD-A', 'GLD-B', 'GLD-C'):
            self.assertEqual(wb_map[bed]['registered_qty'],
                             pl_map[bed]['registered_qty'], bed)
            self.assertEqual(wb_map[bed]['done_ticket_count'],
                             pl_map[bed]['done_ticket_count'], bed)
        # dashboard 今日登记 = 工作台床次登记合计（数据全在今日）；完成+工作中=登记
        self.assertEqual(dash['summary']['registered_qty'],
                         sum(b['registered_qty'] for b in wb['beds']))
        self.assertEqual(dash['summary']['done_qty'] + dash['summary']['working_qty'],
                         dash['summary']['registered_qty'])
        # 完工扎判定一致：dashboard 完成件数 = Σ完工扎的登记件数（A1 120 + B1 50）
        self.assertEqual(dash['summary']['done_qty'], 170)
        # plans 菲票数量合计 = 全部菲票 qty 合计（口径同为 Ticket.qty 聚合）
        self.assertEqual(sum(r['order_qty'] for r in dash['plans']),
                         Ticket.objects.filter(company=self.company)
                         .aggregate(s=Sum('qty'))['s'])

    def test_stat011_pc_zero_bed_consistent(self):
        """STAT-011 pc=0 床次行为一致：无配置工序床次的登记一律算在制不算完工。

        （统一前工作台会把该床登记计为完工、dashboard 计为在制——口径分叉，
        统一后四处一致：不完工。）
        """
        # 新建 0 配置工序床次 D + 1 扎 + 1 条登记
        prod_d = Production.objects.create(company=self.company, style=self.style,
                                           bed_no='GLD-D', total_qty=10)
        t_d = Ticket.objects.create(company=self.company, production=prod_d,
                                    bed_no='GLD-D', bundle_no='1', color='黑',
                                    size='XL', qty=10, qr_code='GLD-D1')
        proc = Process.objects.filter(company=self.company).first()
        Record.objects.create(company=self.company, ticket=t_d, process=proc,
                              user=self.u1, qty=10, price='1.000',
                              registered_at=timezone.now())
        wb = self.api('get', '/records/workbench', self.owner())
        d_bed = next(b for b in wb['beds'] if b['bed_no'] == 'GLD-D')
        self.assertEqual((d_bed['registered_qty'], d_bed['done_ticket_count']),
                         (10, 0))  # 工作台：登记 10、完工 0
        pl = self.api('get', '/productions?page_size=50', self.owner())
        p_bed = next(it for it in pl['results'] if it['bed_no'] == 'GLD-D')
        self.assertEqual((p_bed['registered_qty'], p_bed['done_ticket_count']),
                         (10, 0))  # 床次列表：一致
        dash = self.api('get', '/dashboard?range=today', self.owner())
        self.assertEqual(dash['summary']['done_qty'], 170)   # 不新增完成
        self.assertEqual(dash['summary']['working_qty'], 85)  # 10 件计入工作中
        self.assertEqual(dash['summary']['registered_qty'], 255)

    def test_stat013_mixed_bed_cut_total_consistent(self):
        """STAT-013 混色床分摊口径列内自洽：同款各行 cut_qty 之和 = cut_total。"""
        dash = self.api('get', '/dashboard?range=month', self.owner())
        by_style = {}
        for r in dash['plans']:
            acc = by_style.setdefault(r['style_no'], [0, set()])
            acc[0] += r['cut_qty']
            acc[1].add(r['cut_total'])
        for style_no, (total_cuts, cut_totals) in by_style.items():
            self.assertEqual(len(cut_totals), 1, style_no)  # 同款各行同一 cut_total
            self.assertEqual(total_cuts, cut_totals.pop(), style_no)

    # ==================================================================
    # STAT-020~022 range 六档边界（custom 反转/非法回落、负数/未来日期安全）
    # ==================================================================
    def _summary(self, qs):
        return self.api('get', f'/dashboard?{qs}', self.owner())['summary']

    def test_stat020_range_boundaries(self):
        """STAT-020 六档边界：数据全在今日 → today/week/month 等值；
        yesterday/last_month/反转 custom/未来 custom 全 0；非法回落 month。"""
        month = self._summary('range=month')
        cs = self.today.strftime('%Y-%m-%d')
        self.assertEqual(self._summary('range=today'), month)
        self.assertEqual(self._summary('range=week'), month)
        self.assertEqual(self._summary(f'range=custom&start_date={cs}&end_date={cs}'),
                         month)
        # 非法 key / 非法日期 → 回落本月（负数日期同样安全回落，不 500）
        self.assertEqual(self._summary('range=nuke'), month)
        self.assertEqual(self._summary('range=custom&start_date=abc&end_date=def'),
                         month)
        self.assertEqual(
            self._summary('range=custom&start_date=-2020-01-01&end_date=-2020-01-02'),
            month)
        # custom 起止反转：换位生效（区间 2030 年 → 空档，全 0 存量字段保留）
        rev = self._summary('range=custom&start_date=2030-01-05&end_date=2030-01-01')
        self.assertEqual((rev['done_qty'], rev['working_qty'], rev['registered_qty'],
                          rev['cut_qty'], rev['user_count'], rev['ticket_count']),
                         (0, 0, 0, 0, 0, 0))
        self.assertEqual((rev['active_beds'], rev['cut_total'], rev['order_total']),
                         (3, 350, 370))
        # 昨日 / 上月 / 未来区间：登记与裁货均为 0，存量字段不变
        for key in ('range=yesterday', 'range=last_month',
                    'range=custom&start_date=2030-01-01&end_date=2030-01-31'):
            s = self._summary(key)
            self.assertEqual((s['done_qty'], s['working_qty'], s['registered_qty'],
                              s['cut_qty'], s['user_count'], s['ticket_count']),
                             (0, 0, 0, 0, 0, 0), key)
            self.assertEqual((s['active_beds'], s['cut_total'], s['order_total']),
                             (3, 350, 370), key)

    def test_stat021_custom_span_and_month_default(self):
        """STAT-021 custom 闭区间生效：跨含今日的区间等价 month；
        缺省 range 参数 = month。"""
        month = self._summary('range=month')
        early = (self.today - timedelta(days=10)).strftime('%Y-%m-%d')
        cs = self.today.strftime('%Y-%m-%d')
        self.assertEqual(
            self._summary(f'range=custom&start_date={early}&end_date={cs}'), month)
        self.assertEqual(self.api('get', '/dashboard', self.owner())['summary'],
                         month)

    # ==================================================================
    # STAT-030~031 plans 行 order_no（订单域 skipUnless 保护）
    # ==================================================================
    @skipUnless(ORDERS_READY, '订单域（apps.orders，SA1）未合并，跳过；'
                              'TODO：INT 合并后回归')
    def test_stat030_plans_order_no_earliest(self):
        """STAT-030 plans 行 order_no：多单取最早，无单床次不回填。"""
        from apps.orders.models import Order
        now = timezone.now()
        o_early = Order.objects.create(company=self.company, order_no='SO-9001')
        o_late = Order.objects.create(company=self.company, order_no='SO-9002')
        Order.objects.filter(pk=o_early.pk).update(created_at=now - timedelta(days=2))
        Order.objects.filter(pk=o_late.pk).update(created_at=now - timedelta(days=1))
        # A 挂晚单、C 挂早单（同款两单 → 取最早 SO-9001）；B 无单
        Production.objects.filter(pk=self.prod_a.pk).update(order=o_late)
        Production.objects.filter(pk=self.prod_c.pk).update(order=o_early)
        plans = self.api('get', '/dashboard?range=month', self.owner())['plans']
        self.assertEqual(len(plans), 3)
        for r in plans:
            self.assertEqual(r['order_no'], 'SO-9001', r['color'])
        # 同款内 order_no 全部一致（款号级字段）
        self.assertEqual({r['order_no'] for r in plans}, {'SO-9001'})

    @skipUnless(ORDERS_READY, '订单域（apps.orders，SA1）未合并，跳过；'
                              'TODO：INT 合并后回归')
    def test_stat031_plans_order_no_empty_without_order(self):
        """STAT-031 无挂单 → order_no 空串（存量行为不变）。"""
        plans = self.api('get', '/dashboard?range=month', self.owner())['plans']
        self.assertTrue(plans)
        for r in plans:
            self.assertEqual(r['order_no'], '')

    @skipUnless(ORDERS_READY, '订单域（apps.orders，SA1）未合并，跳过；'
                              'TODO：INT 合并后回归')
    def test_stat032_order_item_progress(self):
        """STAT-032 order_item_progress：分子=挂单床次登记按色码聚合，分母=OrderItem。"""
        from apps.orders.models import Order, OrderItem
        order = Order.objects.create(company=self.company, order_no='SO-9101')
        OrderItem.objects.create(order=order, color='黑', size='M', qty=100)
        OrderItem.objects.create(order=order, color='黑', size='L', qty=200)
        OrderItem.objects.create(order=order, color='白', size='M', qty=50)
        # A 床挂单：其登记 黑M=120（A1 三工序累加）、黑L=60、白M=15；B 床未挂单不计入
        Production.objects.filter(pk=self.prod_a.pk).update(order=order)
        from apps.production.stats import order_item_progress
        r = order_item_progress(order)
        self.assertEqual(r['total_qty'], 350)
        cells = {(x['color'], x['size']): (x['qty'], x['registered_qty'],
                                           x['progress']) for x in r['items']}
        self.assertEqual(cells[('黑', 'M')], (100, 120, 120.0))   # 登记超分母不硬禁（不封顶）
        self.assertEqual(cells[('黑', 'L')], (200, 60, 30.0))
        self.assertEqual(cells[('白', 'M')], (50, 15, 30.0))
        self.assertEqual(r['registered_qty'], 195)   # 挂单床次登记合计=195
        self.assertEqual(r['progress'], round(195 / 350 * 100, 1))  # 55.7
        # 无挂单登记的空白单：全 0
        empty = Order.objects.create(company=self.company, order_no='SO-9102')
        r2 = order_item_progress(empty)
        self.assertEqual((r2['total_qty'], r2['registered_qty'], r2['progress']),
                         (0, 0, 0))
