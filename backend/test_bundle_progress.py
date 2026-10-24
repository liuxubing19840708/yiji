# -*- coding: utf-8 -*-
"""扎级完成矩阵端点（2026-10-22 需求）冒烟：GET /records/bundle-progress。

- done 口径：distinct 已登记工序数 ≥ 配置工序数（stats.py 唯一口径，pc=0 不算完工）；
- users：该扎有登记员工去重 + 累计件数降序；
- 床次批量（多 production_ids）+ 上限截断 + 缺参 400。
"""
import json

from django.contrib.auth.hashers import make_password
from django.test import Client, TestCase

from apps.accounts.models import Post as PostM, User, UserCompany
from apps.accounts.services import create_company_with_owner
from apps.catalog.models import Process, Style, StyleProcess, Ticket
from apps.production.models import Production, ProductionProcess
from apps.records.models import Record

OWNER_PHONE = '15900001001'
U1_PHONE = '15900001002'
U2_PHONE = '15900001003'
CO_NAME = 'GLD扎矩阵厂'


class BundleProgressTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        owner = User.objects.filter(phone=OWNER_PHONE).first() or \
            User.objects.create(phone=OWNER_PHONE, username=OWNER_PHONE,
                                real_name='矩阵厂长',
                                password=make_password('yisao1234'), is_active=True)
        cls.u1 = User.objects.filter(phone=U1_PHONE).first() or \
            User.objects.create(phone=U1_PHONE, username=U1_PHONE, real_name='车工一',
                                password=make_password('yisao1234'), is_active=True)
        cls.u2 = User.objects.filter(phone=U2_PHONE).first() or \
            User.objects.create(phone=U2_PHONE, username=U2_PHONE, real_name='车工二',
                                password=make_password('yisao1234'), is_active=True)
        company, cls.admin_post = create_company_with_owner(owner, CO_NAME)
        cls.company = company
        cls.cid = company.id
        plain_post = PostM.objects.create(company=company, name='GLD-普工', is_admin=False)
        for u in (cls.u1, cls.u2):
            UserCompany.objects.create(user=u, company=company, post=plain_post,
                                       status=UserCompany.Status.ACTIVE)

        procs = [Process.objects.create(company=company, name=f'GLD-工序{i}', default_price=i)
                 for i in (1, 2)]
        cls.style = Style.objects.create(company=company, style_no='GLD-STYLE-ZJ',
                                         name='扎矩阵款')
        for i, p in enumerate(procs):
            StyleProcess.objects.create(style=cls.style, process=p, price=i + 1, sort=i)

        cls.prod = Production.objects.create(company=company, style=cls.style,
                                             bed_no='GLD-ZJ', total_qty=200)
        for p in procs:
            ProductionProcess.objects.create(production=cls.prod, process=p, price=1)
        # 无订单工序的床次：pc 回落款式工序 2，同样可判完工
        cls.prod_b = Production.objects.create(company=company, style=cls.style,
                                               bed_no='GLD-ZB', total_qty=100)

        t1 = Ticket.objects.create(company=company, production=cls.prod, bed_no='GLD-ZJ',
                                   bundle_no='1', color='黑', size='M', qty=100,
                                   qr_code='GLD-ZJ1')
        t2 = Ticket.objects.create(company=company, production=cls.prod, bed_no='GLD-ZJ',
                                   bundle_no='2', color='黑', size='L', qty=100,
                                   qr_code='GLD-ZJ2')
        t3 = Ticket.objects.create(company=company, production=cls.prod_b, bed_no='GLD-ZB',
                                   bundle_no='1', color='白', size='M', qty=100,
                                   qr_code='GLD-ZB1')
        # 扎1 配满 2 工序（完工，参与者 u1×60 + u2×40，users 按件数降序 u1 在前）
        # 扎2 登 1 工序（在制）；扎3 无登记（未登记，pc=2）
        Record.objects.create(company=company, ticket=t1, process=procs[0], user=cls.u1,
                              qty=60, price='1.000')
        Record.objects.create(company=company, ticket=t1, process=procs[1], user=cls.u2,
                              qty=40, price='1.000')
        Record.objects.create(company=company, ticket=t2, process=procs[0], user=cls.u1,
                              qty=30, price='1.000')

        cls.tokens = {phone: cls._login(phone) for phone in (OWNER_PHONE, U1_PHONE)}

    @classmethod
    def _login(cls, phone):
        c = Client()
        r = c.post('/api/auth/login',
                   data=json.dumps({'phone': phone, 'password': 'yisao1234'}),
                   content_type='application/json')
        return json.loads(r.content.decode('utf-8'))['access']

    def api(self, path, token):
        c = Client()
        r = c.get(f'/api{path}', HTTP_AUTHORIZATION=f'Bearer {token}',
                  HTTP_X_COMPANY_ID=str(self.cid))
        return json.loads(r.content.decode('utf-8'))

    def test_done_state_users(self):
        """完工扎 users 降序 + done 判定 + 在制/未登记状态。"""
        d = self.api(f'/records/bundle-progress?production_ids={self.prod.id}',
                     self.tokens[OWNER_PHONE])
        self.assertEqual(d['count'], 2)
        rows = {r['bundle_no']: r for r in d['results']}
        done = rows['1']
        self.assertTrue(done['done'])
        self.assertEqual(done['state'], 'done')
        self.assertEqual(done['registered_process_count'], 2)
        self.assertEqual(done['process_count'], 2)
        self.assertEqual(done['registered_qty'], 100)
        self.assertEqual([u['name'] for u in done['users']], ['车工一', '车工二'])
        self.assertEqual([u['qty'] for u in done['users']], [60, 40])
        partial = rows['2']
        self.assertFalse(partial['done'])
        self.assertEqual(partial['state'], 'partial')
        self.assertEqual([u['name'] for u in partial['users']], ['车工一'])

    def test_pc_fallback_to_style(self):
        """无订单工序床次 pc 回落款式工序数，配满即完工（与 stats 口径一致）。"""
        d = self.api(f'/records/bundle-progress?production_ids={self.prod_b.id}',
                     self.tokens[OWNER_PHONE])
        self.assertEqual(d['count'], 1)
        row = d['results'][0]
        self.assertEqual(row['process_count'], 2)
        self.assertEqual(row['state'], 'none')

    def test_multi_and_missing(self):
        """多床次批量；缺参 400；跨厂床次不可见。"""
        d = self.api(f'/records/bundle-progress?production_ids={self.prod.id},{self.prod_b.id}',
                     self.tokens[OWNER_PHONE])
        self.assertEqual(d['count'], 3)
        r = Client().get(f'/api/records/bundle-progress',
                         HTTP_AUTHORIZATION=f'Bearer {self.tokens[OWNER_PHONE]}',
                         HTTP_X_COMPANY_ID=str(self.cid))
        self.assertEqual(r.status_code, 400)
        # 另一公司：注册一个独立厂查不到本厂菲票
        other = User.objects.create(phone='15900001099', username='15900001099',
                                    real_name='别厂厂长',
                                    password=make_password('yisao1234'), is_active=True)
        create_company_with_owner(other, 'GLD扎矩阵他厂')
        c = Client()
        r2 = c.post('/api/auth/login',
                    data=json.dumps({'phone': '15900001099', 'password': 'yisao1234'}),
                    content_type='application/json')
        token2 = json.loads(r2.content.decode('utf-8'))['access']
        c2 = Client()
        r3 = c2.get(f'/api/records/bundle-progress?production_ids={self.prod.id}',
                    HTTP_AUTHORIZATION=f'Bearer {token2}',
                    HTTP_X_COMPANY_ID=str(
                        UserCompany.objects.filter(user=other).first().company_id))
        d3 = json.loads(r3.content.decode('utf-8'))
        self.assertEqual(d3['count'], 0)
