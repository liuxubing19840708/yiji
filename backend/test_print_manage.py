# -*- coding: utf-8 -*-
"""菲票打印上报链路修复套件（H1/H2/M4/M1/M2/L4，2026-10-24 用户需求）：

  - H1 打印计数并发丢失更新：print_count 改单条 UPDATE(F) 原子自增；
  - H2 打印上报幂等：print_key 逐票去重，重复上报记 duplicated 不重复计数；
  - M4 打印明细表：TicketPrintLog（is_reprint 取计数前 is_printed 快照、operator 记录）；
  - M1 容忍缺失票：缺失/已删票进 skipped 保持 200，不再 400；
  - M2 二维码注入防护：bed_no/color/size 含 '|' 或 GBK 编出 0x7C 字节 → 400；
  - L4 并发编菲撞唯一约束：IntegrityError 后顺延扎号重试一次，不再 500。

跑法（django.test.TestCase 事务回滚，不污染库；动态建厂，真实厂零接触）：
  .venv/Scripts/python.exe -X utf8 manage.py test test_print_manage -v 1

断言风格沿用 check()/api() 脚本套件（PASS/FAIL 汇总），每个 test 收尾校验 FAIL 为空。
"""
import json
from unittest.mock import patch

from django.contrib.auth.hashers import make_password
from django.test import Client, TestCase

from apps.accounts.models import Post as PostM
from apps.accounts.models import User, UserCompany
from apps.accounts.services import create_company_with_owner
from apps.catalog.models import Style, Ticket, TicketPrintLog
from apps.catalog import views as catalog_views
from apps.core.utils import build_ticket_qr
from apps.production.models import Production

CO_NAME = '打印管理厂(可删)'
OWNER_PHONE = '13900139021'
WORKER_PHONE = '13900139022'

PASS, FAIL = [], []


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    print(('PASS' if cond else 'FAIL'), name, ('' if cond else f'| 预期外: {detail}'))


def api(method, path, token=None, cid=None, data=None):
    c = Client()
    extra = {}
    if token:
        extra['HTTP_AUTHORIZATION'] = f'Bearer {token}'
    if cid:
        extra['HTTP_X_COMPANY_ID'] = str(cid)
    fn = getattr(c, method.lower())
    r = fn(f'/api{path}',
           data=json.dumps(data) if data is not None else None,
           content_type='application/json', **extra)
    try:
        body = json.loads(r.content.decode('utf-8'))
    except Exception:
        body = {}
    return r.status_code, body


def gbk_probe_char():
    """GBK 编码后含 0x7C 字节的汉字（扫码枪按 | 字节切段会错位的真实风险源）。"""
    for cp in range(0x4E00, 0xA000):
        ch = chr(cp)
        try:
            if b'\x7c' in ch.encode('gbk'):
                return ch
        except UnicodeEncodeError:
            continue
    return None


class TicketPrintManageTests(TestCase):
    """打印上报链路：动态建厂 + API 全链路断言。"""

    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create(
            phone=OWNER_PHONE, username=OWNER_PHONE, real_name='打印厂长',
            password=make_password('yisao1234'), is_active=True)
        cls.company, _ = create_company_with_owner(cls.owner, CO_NAME)
        # 无 print.manage / production.manage 的普工岗 → 打印上报 403
        plain_post = PostM.objects.create(company=cls.company, name='打印普工岗',
                                          permissions=[])
        cls.worker = User.objects.create(
            phone=WORKER_PHONE, username=WORKER_PHONE, real_name='打印员工',
            password=make_password('yisao1234'), is_active=True)
        UserCompany.objects.create(user=cls.worker, company=cls.company,
                                   post=plain_post, status=UserCompany.Status.ACTIVE)
        cls.style = Style.objects.create(company=cls.company, style_no='PM-S1',
                                         name='打印测试款')
        cls.prod = Production.objects.create(company=cls.company, style=cls.style,
                                             bed_no='PM-B1', total_qty=0, plan_qty=50)
        cls.token = cls._login(OWNER_PHONE)
        cls.wtoken = cls._login(WORKER_PHONE)

    @classmethod
    def _login(cls, phone):
        st, d = api('POST', '/auth/login', data={'phone': phone, 'password': 'yisao1234'})
        assert st == 200, f'登录失败: {st} {d}'
        return d.get('access', '')

    def mk_ticket(self, bno, qty=10):
        return Ticket.objects.create(
            company=self.company, production=self.prod, bed_no=self.prod.bed_no,
            bundle_no=str(bno), color='红', size='M', qty=qty,
            qr_code=build_ticket_qr(self.company.company_no, self.prod.bed_no,
                                    self.style.style_no, str(bno), '红', 'M', qty))

    def report(self, ids, print_key=None, token=None):
        data = {'ticket_ids': ids}
        if print_key is not None:
            data['print_key'] = print_key
        return api('POST', '/tickets/print',
                   token or self.token, self.company.id, data)

    # ---- a) H1：print_count 原子自增 + is_printed/print_at 置位 ----
    def test_h1_print_count_atomic(self):
        t1, t2 = self.mk_ticket(1), self.mk_ticket(2)
        Ticket.objects.filter(pk=t1.pk).update(print_count=2)  # 预置非零计数，验证 +1 而非覆盖
        st, d = self.report([t1.id, t2.id])
        check('PM-H1a 上报 200 且 reported=2', st == 200 and d.get('reported') == 2, f'{st} {d}')
        check('PM-H1a skipped/duplicated 为空',
              d.get('skipped') == [] and d.get('duplicated') == [], d)
        t1.refresh_from_db()
        t2.refresh_from_db()
        check('PM-H1a print_count 在原值上 +1', (t1.print_count, t2.print_count) == (3, 1),
              f'{t1.print_count}/{t2.print_count}')
        check('PM-H1a is_printed 置位且 print_at 落值',
              t1.is_printed and t2.is_printed and t1.print_at is not None,
              f'{t1.is_printed}/{t1.print_at}')

    # ---- b) H2：同 print_key 重复上报 → duplicated、计数不增 ----
    def test_h2_print_key_idempotent(self):
        t = self.mk_ticket(3)
        st1, d1 = self.report([t.id], print_key='PM-KEY-1')
        st2, d2 = self.report([t.id], print_key='PM-KEY-1')
        check('PM-H2 首报 reported=1、duplicated 空',
              st1 == 200 and d1.get('reported') == 1 and d1.get('duplicated') == [], f'{st1} {d1}')
        check('PM-H2 重报 200 且 duplicated 命中',
              st2 == 200 and d2.get('reported') == 0 and d2.get('duplicated') == [t.id],
              f'{st2} {d2}')
        t.refresh_from_db()
        check('PM-H2 重报后计数不增', t.print_count == 1, t.print_count)
        check('PM-H2 重报不新增打印记录',
              TicketPrintLog.objects.filter(print_key='PM-KEY-1', ticket=t).count() == 1, '')
        # 不同 key 再报：正常计数（幂等只针对同 key）
        st3, d3 = self.report([t.id], print_key='PM-KEY-2')
        t.refresh_from_db()
        check('PM-H2 换 key 正常计数', st3 == 200 and d3.get('reported') == 1
              and t.print_count == 2, f'{st3} {d3} {t.print_count}')
        self.assertFalse(FAIL, FAIL)

    # ---- c) M1：缺失/已删票 → skipped 且 200 ----
    def test_m1_missing_and_deleted_skipped(self):
        t = self.mk_ticket(4)
        ghost_id = 990000001
        st, d = self.report([t.id, ghost_id])
        check('PM-M1 混合上报 200', st == 200, f'{st} {d}')
        check('PM-M1 缺失票进 skipped、存在票正常计数',
              d.get('skipped') == [ghost_id] and d.get('reported') == 1, d)
        t.refresh_from_db()
        check('PM-M1 缺失票不影响存在票计数', t.print_count == 1, t.print_count)
        # 软删除票 → skipped
        t2 = self.mk_ticket(5)
        t2.delete()
        st2, d2 = self.report([t2.id])
        check('PM-M1 已删票 skipped 且 200（reported=0）',
              st2 == 200 and d2.get('reported') == 0 and d2.get('skipped') == [t2.id],
              f'{st2} {d2}')
        self.assertFalse(FAIL, FAIL)

    # ---- d) M4：TicketPrintLog 行生成（is_reprint / operator / print_key） ----
    def test_m4_print_log_rows(self):
        t = self.mk_ticket(6)
        self.report([t.id], print_key='PM-KEY-A')  # 首打
        logs = list(t.print_logs.order_by('id'))
        check('PM-M4 首打生成一条打印记录', len(logs) == 1, len(logs))
        check('PM-M4 首打 is_reprint=False', logs and logs[0].is_reprint is False,
              logs and logs[0].is_reprint)
        check('PM-M4 operator 记录上报人', logs and logs[0].operator_id == self.owner.id,
              logs and logs[0].operator_id)
        check('PM-M4 print_key 落库', logs and logs[0].print_key == 'PM-KEY-A',
              logs and logs[0].print_key)
        self.report([t.id], print_key='PM-KEY-B')  # 补打（is_printed 已 True）
        logs2 = list(t.print_logs.order_by('id'))
        check('PM-M4 补打 is_reprint=True',
              len(logs2) == 2 and logs2[1].is_reprint is True,
              [(l.id, l.is_reprint) for l in logs2])
        self.assertFalse(FAIL, FAIL)

    # ---- e) M2：编菲字段含 '|' 或 GBK 0x7C → 400 ----
    def test_m2_batch_qr_field_guard(self):
        before = Ticket.objects.filter(company=self.company, production=self.prod).count()
        probe = gbk_probe_char()
        cases = [
            ({'color': '红|蓝', 'size': 'M'}, '不能包含'),
            ({'color': '红', 'size': 'M|L'}, '不能包含'),
        ]
        if probe:
            cases.append(({'color': f'{probe}红', 'size': 'M'}, '扫码'))
        for i, (item, expect) in enumerate(cases):
            st, d = api('POST', '/tickets/batch', self.token, self.company.id,
                        {'production_id': self.prod.id,
                         'items': [dict(item, qty=10)]})
            check(f'PM-M2 case{i} 编菲 400 且报「{expect}」',
                  st == 400 and expect in json.dumps(d, ensure_ascii=False), f'{st} {d}')
        after = Ticket.objects.filter(company=self.company, production=self.prod).count()
        check('PM-M2 被拒批次未产生菲票', before == after, f'{before}->{after}')
        self.assertFalse(FAIL, FAIL)

    # ---- f) 权限：无 print.manage/production.manage 上报 403 ----
    def test_f_print_perm_denied(self):
        t = self.mk_ticket(7)
        st, d = self.report([t.id], token=self.wtoken)
        check('PM-F 无权限上报 403', st == 403, f'{st} {d}')
        t.refresh_from_db()
        check('PM-F 被拒后计数未动', t.print_count == 0 and not t.is_printed,
              f'{t.print_count}/{t.is_printed}')
        self.assertFalse(FAIL, FAIL)

    # ---- 附) L4：并发编菲撞唯一约束 → 顺延重试一次而非 500 ----
    def test_l4_bundle_no_race_retry(self):
        occupied = self.mk_ticket(11)  # 直接占号，模拟并发对手已入库
        real_alloc = catalog_views.TicketBatchView._alloc_bundle_no
        calls = {'n': 0}

        def fake_alloc(view, bed_no, candidate):
            # 第一次（准备阶段）绕过冲突检查返回已占用扎号 → bulk_create 撞唯一约束；
            # 重试阶段恢复真实顺延逻辑
            calls['n'] += 1
            if calls['n'] == 1:
                return str(occupied.bundle_no)
            return real_alloc(view, bed_no, candidate)

        with patch.object(catalog_views.TicketBatchView, '_alloc_bundle_no', fake_alloc):
            st, d = api('POST', '/tickets/batch', self.token, self.company.id,
                        {'production_id': self.prod.id, 'start_bundle_no': 12,
                         'items': [{'color': '红', 'size': 'M', 'qty': 10}]})
        check('PM-L4 撞号后重试 201', st == 201, f'{st} {d}')
        check('PM-L4 顺延到未占用扎号',
              st == 201 and d and d[0]['bundle_no'] not in ('11',), d)
        check('PM-L4 确曾触发 IntegrityError 重试', calls['n'] >= 2, calls['n'])
        self.assertFalse(FAIL, FAIL)
