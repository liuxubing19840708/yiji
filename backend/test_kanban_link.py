# -*- coding: utf-8 -*-
"""看板下钻 link 白名单 + 「订单」类目 + 「菲票数量」改名回归套件（SA2，
配套 docs/端到端测试设计方案.md §3.4 与实施方案 §2.9/§四契约 kanban 行）。

运行（连跑 2 次全绿为交付门）：
  .venv/Scripts/python.exe -X utf8 manage.py test test_kanban_link -v 2

覆盖：
  - link 白名单三值生效（production|ticket|order）→ 列下发带 link；
  - 非法值/旧模板缺失 link → 回落空串，渲染不炸；
  - formula 列不受 link 影响（白名单只过滤 link，不动求值）；
  - 字段注册表「订单」类目（order_no/delivery_date/order_progress）解析与渲染；
  - 「订单数量」→「菲票数量」改名回归：内置默认列字典与注册表显示名更新、
    field_key=order_qty 公式语义不动、用户自建列（存量 title/formula）不受影响。
幂等：django.test.TestCase 事务回滚，不污染库。
"""
import json

from django.test import Client, TestCase

from apps.accounts.models import Post as PostM, User, UserCompany
from apps.accounts.services import create_company_with_owner
from apps.catalog.models import Process, Style, Ticket
from apps.kanban_template.fields import KANBAN_FIELDS, field_label, fields_payload
from apps.kanban_template.models import KanbanColumn, KanbanTemplate
from apps.kanban_template.services import (
    LINK_TARGETS,
    builtin_default_columns,
    render_with_fallback,
)
from apps.production.models import Production, ProductionProcess
from apps.records.models import Record

CO_NAME = '看板下钻厂(可删)'
OWNER_PHONE = '13900139011'
STYLE_NO = 'LINK-S1'


def _orders_ready():
    """订单域（SA1 并行交付）是否就绪（order_no 渲染断言用）。"""
    try:
        from apps.orders.models import Order  # noqa: F401
        from apps.production.models import Production as P
        P._meta.get_field('order')
        return True
    except Exception:
        return False


ORDERS_READY = _orders_ready()


def _pwd():
    from django.contrib.auth.hashers import make_password
    return make_password('yisao1234')


def _now():
    from django.utils import timezone
    return timezone.now()


class KanbanLinkTests(TestCase):
    """link 白名单 / 订单类目 / 改名回归。"""

    @classmethod
    def setUpTestData(cls):
        owner = User.objects.filter(phone=OWNER_PHONE).first() or \
            User.objects.create(phone=OWNER_PHONE, username=OWNER_PHONE,
                                real_name='下钻厂长', password=_pwd(), is_active=True)
        cls.company, _post = create_company_with_owner(owner, CO_NAME)
        cls.cid = cls.company.id
        cls.token = cls._login(OWNER_PHONE)

        # 最小渲染数据：1 款 1 床 1 扎 + 1 道订单工序 + 1 条登记
        cls.style = Style.objects.create(company=cls.company, style_no=STYLE_NO,
                                         name='下钻测试款')
        cls.prod = Production.objects.create(company=cls.company, style=cls.style,
                                             bed_no='LINK-A', total_qty=100)
        cls.proc = Process.objects.create(company=cls.company, name='LINK-缝制',
                                          is_fixed=False)
        ProductionProcess.objects.create(production=cls.prod, process=cls.proc, price=1)
        ticket = Ticket.objects.create(company=cls.company, production=cls.prod,
                                       bed_no='LINK-A', bundle_no='1', color='黑',
                                       size='M', qty=100, qr_code='LINK-A1')
        Record.objects.create(company=cls.company, ticket=ticket, process=cls.proc,
                              user=owner, qty=100, price='1.000',
                              registered_at=_now())

        cls.tpl_seq = [0]

    @classmethod
    def _login(cls, phone):
        c = Client()
        r = c.post('/api/auth/login',
                   data=json.dumps({'phone': phone, 'password': 'yisao1234'}),
                   content_type='application/json')
        return json.loads(r.content.decode('utf-8'))['access']

    # ---- 助手 ----
    def api(self, method, path, data=None):
        c = Client()
        r = getattr(c, method)(f'/api{path}', data=data,
                               content_type='application/json',
                               HTTP_AUTHORIZATION=f'Bearer {self.token}',
                               HTTP_X_COMPANY_ID=str(self.cid))
        return r.status_code, json.loads(r.content.decode('utf-8') or '{}')

    def _col(self, seq, bind='field', key='', expr='', extra=None, title='列'):
        return {'seq': seq, 'title': title, 'bind_type': bind, 'field_key': key,
                'expr': expr, 'static_text': '', 'extra': extra or {}}

    def _create_template(self, columns, name_suffix=''):
        """经 HTTP 建模板（走保存期校验链路），返回模板 id。"""
        cls_seq = self.tpl_seq[0]
        self.tpl_seq[0] += 1
        name = f'LINK测试模板{cls_seq}{name_suffix}'
        st, d = self.api('post', '/kanban/templates',
                         {'name': name, 'is_default': False, 'columns': columns})
        self.assertEqual(st, 201, d)
        t = KanbanTemplate.objects.get(company=self.company, name=name)
        self.addCleanup(lambda: (KanbanColumn.all_objects.filter(template=t).delete(),
                                 KanbanTemplate.all_objects.filter(pk=t.pk).delete()))
        return t

    def _render(self, template):
        """渲染指定模板，返回 {列key→文本} 映射（单行单块前提）。"""
        st, d = self.api('get', f'/kanban/render?range=month&template_id={template.id}')
        self.assertEqual(st, 200)
        blocks = [b for g in d['groups'] for b in g['blocks']]
        self.assertTrue(blocks)
        return d, blocks[0]['rows'][0]['cells']

    # ==================================================================
    # L1 link 白名单
    # ==================================================================
    def test_link_whitelist_three_values(self):
        """L1 白名单三值生效：extra.link=production/ticket/order 原样下发。"""
        for target in LINK_TARGETS:
            t = self._create_template([
                self._col(0, 'field', 'style_no', extra={'link': target},
                          title='款号'),
            ])
            st, d = self.api('get', f'/kanban/render?range=month&template_id={t.id}')
            self.assertEqual(st, 200)
            self.assertEqual(d['columns'][0]['link'], target, target)

    def test_link_invalid_ignored_and_legacy_fallback(self):
        """L2 非法 link 忽略不炸；旧模板无 link 字段回落空串。"""
        for raw, desc in (('javascript:alert(1)', '注入串'), ('ORDER', '大小写'),
                          ('bed', '近义非法值'), ('', '空串'), (None, 'None')):
            t = self._create_template([
                self._col(0, 'field', 'style_no', extra={'link': raw}, title='款号'),
            ], name_suffix=desc)
            st, d = self.api('get', f'/kanban/render?range=month&template_id={t.id}')
            self.assertEqual(st, 200, desc)
            self.assertEqual(d['columns'][0]['link'], '', f'{desc} 应忽略为空串')
        # 旧模板（extra 里根本没有 link 键）→ 空串回落
        t = self._create_template([self._col(0, 'field', 'style_no', title='款号')])
        st, d = self.api('get', f'/kanban/render?range=month&template_id={t.id}')
        self.assertEqual((st, d['columns'][0]['link']), (200, ''))

    def test_link_formula_and_static_unaffected(self):
        """L3 formula/static 列不受 link 影响：白名单只过滤 link，不动求值。"""
        t = self._create_template([
            self._col(0, 'formula', expr='cut_qty - order_qty',
                      extra={'link': 'ticket'}, title='差额'),
            self._col(1, 'static', extra={'link': 'production'}, title='固定'),
        ])
        d, cells = self._render(t)
        self.assertEqual(cells[0]['text'], '0')   # 公式照常求值（cut 100 - 票 100）
        self.assertEqual(d['columns'][0]['link'], 'ticket')
        self.assertEqual(cells[1]['text'], '')
        self.assertEqual(d['columns'][1]['link'], 'production')

    def test_link_builtin_default_has_no_link(self):
        """L4 内置默认模板列不含 link → 下发空串（渲染端不弹下钻）。"""
        from apps.kanban_template.services import _columns_payload
        payload = _columns_payload(builtin_default_columns())
        # 2026-10-21 UX 批次：内置默认列增至 13（加「订单号」「订单数量」两列）
        self.assertEqual(len(payload), 13)
        self.assertTrue(all(c['link'] == '' for c in payload))

    # ==================================================================
    # O1 订单类目
    # ==================================================================
    def test_order_category_registry(self):
        """O1 注册表加「订单」类目：三字段在册、类目/显示名/描述齐全。"""
        for key, label in (('order_no', '订单号'), ('delivery_date', '交期'),
                           ('order_progress', '订单完成率')):
            self.assertIn(key, KANBAN_FIELDS, key)
            self.assertEqual(KANBAN_FIELDS[key].category, '订单', key)
            self.assertEqual(KANBAN_FIELDS[key].label, label, key)
            self.assertEqual(field_label(key), label, key)
        payload = fields_payload()
        cats = [p['category'] for p in payload]
        self.assertIn('订单', cats)
        # 类目排序：款式 → 生产/床次 → 菲票聚合 → 订单 → 时间 → 占位
        seen = []
        for c in cats:
            if not seen or seen[-1] != c:
                seen.append(c)
        self.assertEqual(seen, ['款式', '生产/床次', '菲票聚合', '订单', '时间',
                                '占位（数据源未建）'])
        # 类目内 key 正确（2026-10-21 起「订单数量」order_total 同属订单类目）
        order_keys = {p['key'] for p in payload if p['category'] == '订单'}
        self.assertEqual(order_keys,
                         {'order_no', 'delivery_date', 'order_progress', 'order_total'})

    def test_order_fields_render_safe_without_order(self):
        """O2 订单字段渲染安全：无挂单/订单域数据时 order_no/交期空串、完成率 0。"""
        t = self._create_template([
            self._col(0, 'field', 'order_no', title='订单号'),
            self._col(1, 'field', 'delivery_date', title='交期'),
            self._col(2, 'field', 'order_progress', title='订单完成率'),
            self._col(3, 'formula', expr='order_progress + 1', title='完成率加一'),
        ])
        st, d = self.api('get', f'/kanban/render?range=month&template_id={t.id}')
        self.assertEqual(st, 200)
        blocks = [b for g in d['groups'] for b in g['blocks']]
        texts = [c['text'] for c in blocks[0]['rows'][0]['cells']]
        self.assertEqual(texts[:3], ['', '', '0'])
        self.assertIn(texts[3], ('1', '—'))  # 公式引用 0 值不炸（求值成功或占位）

    def test_order_no_renders_plan_value(self):
        """O3 order_no 渲染取 build_plan_rows 下发值（挂单后=最早订单号）。"""
        if not ORDERS_READY:
            self.skipTest('订单域（apps.orders，SA1）未合并，跳过；TODO：INT 合并后回归')
        from apps.orders.models import Order
        order = Order.objects.create(company=self.company, order_no='SO-8001')
        Production.objects.filter(pk=self.prod.pk).update(order=order)
        t = self._create_template([self._col(0, 'field', 'order_no', title='订单号')])
        d, cells = self._render(t)
        self.assertEqual(cells[0]['text'], 'SO-8001')

    # ==================================================================
    # R1「订单数量」→「菲票数量」改名回归
    # ==================================================================
    def test_rename_default_columns_and_registry(self):
        """R1 默认列字典「菲票数量」列（2026-10-21 起第 6 列）field_key 仍为 order_qty；
        注册表显示名同步；列数 13 与其它列不变。"""
        cols = builtin_default_columns()
        self.assertEqual(len(cols), 13)
        c5 = cols[5]
        self.assertEqual(c5['title'], '菲票数量')
        self.assertEqual(c5['field_key'], 'order_qty')
        self.assertEqual(c5['bind_type'], 'field')
        self.assertEqual(KANBAN_FIELDS['order_qty'].label, '菲票数量')
        # 其它列标题原样（改名只动一列；3/4 位为后加的订单号/订单数量）
        self.assertEqual([c['title'] for c in cols][:3], ['款号', '下单日期', '颜色'])
        self.assertEqual([c['title'] for c in cols][3:5], ['订单号', '订单数量'])
        self.assertEqual([c['title'] for c in cols][6:], ['床次', '已裁数量', '已裁合计',
                                                          '排单情况', '下线数量', '出货',
                                                          '尾部成品'])

    def test_rename_formula_semantics_unchanged(self):
        """R2 公式语义不动：order_qty 参与公式求值结果与改名前一致。"""
        t = self._create_template([
            self._col(0, 'formula', expr='order_qty + 1', title='票数加一'),
            self._col(1, 'formula', expr='round(order_qty / cut_total * 100, 1)',
                      title='占比'),
        ])
        d, cells = self._render(t)
        self.assertEqual(cells[0]['text'], '101')    # order_qty=100（+1）
        self.assertEqual(cells[1]['text'], '100')    # 100/100*100

    def test_rename_user_built_column_unaffected(self):
        """R3 用户自建列不受影响：存量 title「订单数量」原样渲染不炸不洗。"""
        t = self._create_template([
            self._col(0, 'field', 'order_qty', title='订单数量'),   # 存量自建标题
            self._col(1, 'formula', expr='order_qty * 2', title='订单数量x2'),
        ])
        d, cells = self._render(t)
        self.assertEqual([c['text'] for c in cells], ['100', '200'])
        # 下发列标题保持用户自建的「订单数量」（不强制清洗存量标题）
        titles = {c['seq']: c['title'] for c in d['columns']}
        self.assertEqual(titles[0], '订单数量')
        self.assertEqual(titles[1], '订单数量x2')

    def test_rename_builtin_render_end_to_end(self):
        """R4 内置默认模板端到端渲染：改名后 13 列正常渲染不炸。"""
        payload = render_with_fallback(self.company, 'month', '', '',
                                       template=None)
        self.assertEqual(payload['template']['id'], None)
        self.assertEqual(len(payload['columns']), 13)
        self.assertEqual(payload['columns'][5]['title'], '菲票数量')
        blocks = [b for g in payload['groups'] for b in g['blocks']]
        self.assertTrue(blocks)
        row = blocks[0]['rows'][0]
        self.assertEqual(len(row['cells']), 13)
