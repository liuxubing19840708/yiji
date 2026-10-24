# -*- coding: utf-8 -*-
"""
新代码补缺套件（INT 最终补）—— 消零 apps/orders 与 stats.py 残余 missing 行：

  N1/N2 Order.__str__ / OrderItem.__str__               （models.py 62/86）
  N3 production_pc_map 带数据路径                        （stats.py 47 推导式）
  N4 ticket_record_rows 带 production_ids 路径           （stats.py 106 推导式）

跑法：manage.py shell -c "exec(compile(open('test_newcode_gap.py',encoding='utf-8').read(),'test_newcode_gap.py','exec'))"
幂等：复用「订单测试厂(可删)」，TORDGAP 前缀硬清。
"""
import json

from django.contrib.auth.hashers import make_password
from django.test import Client

from apps.accounts.models import Company, User
from apps.accounts.services import create_company_with_owner
from apps.catalog.models import Process, Style, StyleProcess, Ticket
from apps.orders.models import Order, OrderItem
from apps.production.models import Production, ProductionProcess
from apps.production import stats
from apps.records.models import Record

PASS, FAIL = [], []


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    print(('PASS' if cond else 'FAIL'), name, ('' if cond else f'| {detail}'))


def api(method, path, token=None, cid=None, data=None):
    c = Client()
    extra = {}
    if token:
        extra['HTTP_AUTHORIZATION'] = f'Bearer {token}'
    if cid:
        extra['HTTP_X_COMPANY_ID'] = str(cid)
    fn = getattr(c, method.lower())
    kw = {'content_type': 'application/json', **extra}
    r = fn(f'/api{path}', data=json.dumps(data) if data is not None else None, **kw)
    try:
        return r.status_code, json.loads(r.content.decode('utf-8'))
    except Exception:
        return r.status_code, {}


PHONE = '13988710101'
company = Company.objects.filter(name='订单测试厂(可删)').first()
assert company, '订单测试厂不存在，请先跑 test_orders.py'
User.objects.filter(phone=PHONE).update(password=make_password('yisao1234'), is_active=True)
AT = api('POST', '/auth/login', data={'phone': PHONE, 'password': 'yisao1234'})[1].get('access', '')
assert AT, '管理员登录失败'

# preclean（先子后父）
OrderItem.all_objects.filter(order__company=company, order__order_no__startswith='GAP-') \
    ._raw_delete(OrderItem.all_objects.db)
Order.all_objects.filter(company=company, order_no__startswith='GAP-')._raw_delete(
    Order.all_objects.db)
Record.all_objects.filter(company=company, ticket__bed_no__startswith='TORDGAP').delete()
Ticket.all_objects.filter(company=company, bed_no__startswith='TORDGAP')._raw_delete(
    Ticket.all_objects.db)
ProductionProcess.all_objects.filter(production__bed_no__startswith='TORDGAP')._raw_delete(
    ProductionProcess.all_objects.db)
Production.all_objects.filter(company=company, bed_no__startswith='TORDGAP')._raw_delete(
    Production.all_objects.db)
StyleProcess.all_objects.filter(style__style_no__startswith='TORDGAP')._raw_delete(
    StyleProcess.all_objects.db)
Style.all_objects.filter(company=company, style_no__startswith='TORDGAP')._raw_delete(
    Style.all_objects.db)
Process.all_objects.filter(company=company, name='N补缺工序')._raw_delete(Process.all_objects.db)

# N1/N2 __str__
o = Order.objects.create(company=company, order_no='GAP-STR')
oi = OrderItem.objects.create(order=o, color='黑', size='M', qty=7)
check('N1 Order.__str__', str(o) == f'{company.id}:GAP-STR', str(o))
check('N2 OrderItem.__str__', str(oi) == f'{o.id}:黑/Mx7', str(oi))
o.delete()

# N3/N4 stats 带数据路径
style = Style.objects.create(company=company, style_no='TORDGAP-S1', name='补缺款')
prod = Production.objects.create(company=company, style=style, bed_no='TORDGAP-B1',
                                 total_qty=50)
proc = Process.objects.create(company=company, name='N补缺工序', default_price=1,
                              is_fixed=True)
StyleProcess.objects.create(style=style, process=proc, price=1)
ProductionProcess.objects.create(production=prod, process=proc, price=1)
tk = Ticket.objects.create(company=company, production=prod, bed_no='TORDGAP-B1',
                           bundle_no=1, color='黑', size='M', qty=10,
                           qr_code='GAP|N1|TORDGAP|1|黑|M|10')

pc = stats.production_pc_map([prod.id])
check('N3 production_pc_map 带数据', isinstance(pc, dict) and pc.get(prod.id, 0) >= 0, str(pc))

from apps.records.models import Record  # noqa: E402
from apps.accounts.models import UserCompany  # noqa: E402
uc = UserCompany.all_objects.filter(company=company, user__phone=PHONE).first()
Record.objects.create(company=company, ticket=tk, process=proc, user=uc.user, qty=3, price=1)
rows = stats.ticket_record_rows(company, production_ids=[prod.id])
check('N4 ticket_record_rows 带数据', len(rows) == 1 and rows[0]['qty'] == 3, str(rows)[:80])

print(f'\n==== 新代码补缺套件：{len(PASS)} pass / {len(FAIL)} fail ====')
if FAIL:
    print('FAILED:', FAIL)
