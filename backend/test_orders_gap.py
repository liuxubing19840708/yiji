# -*- coding: utf-8 -*-
"""
订单域补缺套件（INT 集成阶段补）—— 覆盖 test_orders 未触达的分支（coverage missing=0 门禁）：

  G1 items 元素非 dict → 400「格式不正确」            （_validated_item_rows 60 行）
  G2 items 缺颜色 → 400                                （68 行）
  G3 items 缺尺码 → 400                                （70 行）
  G4 items qty 非数字 → 400「数量必须大于0」           （65-66 行转换失败路径）
  G5 PUT 不带 items：明细保持原样（partial 更新分支）   （217->222 false 弧）
  G6 PUT 同单号不改：exclude_pk 去重分支               （85 行）
  G7 PUT 改单号：软删清场 + 撞号校验分支               （210-212 行）
  G8 交期充裕（>7 天）绿灯分支                          （133 行 else）

跑法：manage.py shell -c "exec(compile(open('test_orders_gap.py',encoding='utf-8').read(),'test_orders_gap.py','exec'))"
幂等：复用「订单测试厂(可删)」，前缀 TORDGAP 硬清自清扫，不依赖 DB 现状。
"""
import json

from django.contrib.auth.hashers import make_password
from django.test import Client

from apps.accounts.models import Company, Post as PostM, User
from apps.accounts.services import create_company_with_owner
from apps.catalog.models import Style
from apps.orders.models import Order, OrderItem

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
        body = json.loads(r.content.decode('utf-8'))
    except Exception:
        body = {}
    return r.status_code, body


PHONE = '13988710101'
CO_A = '订单测试厂(可删)'

company = Company.objects.filter(name=CO_A).first()
if company is None:
    u = User.objects.filter(phone=PHONE).first() or User.objects.create(
        phone=PHONE, username=PHONE, real_name='订单厂长',
        password=make_password('yisao1234'), is_active=True)
    company, _ = create_company_with_owner(u, CO_A)
else:
    User.objects.filter(phone=PHONE).update(
        password=make_password('yisao1234'), is_active=True)
CID = company.id
OrderItem.all_objects.filter(order__company=company, order__order_no__startswith='GAP-') \
    ._raw_delete(OrderItem.all_objects.db)
Order.all_objects.filter(company=company, order_no__startswith='GAP-')._raw_delete(
    Order.all_objects.db)

AT = api('POST', '/auth/login', data={'phone': PHONE, 'password': 'yisao1234'})[1].get('access', '')
assert AT, '管理员登录失败'

# P4 契约：订单必须关联款式（G1~G3 裸 items 用例除外——items 校验先于 style 校验）
gap_style = Style.objects.filter(company=company, style_no='TORDGAP-S1').first() \
    or Style.objects.create(company=company, style_no='TORDGAP-S1', name='补缺测试款')

# ---- G1~G4 items 校验边界（实测 style 校验先于 items，故带合法 style 触达 items 分支） ----
code, d = api('POST', '/orders', AT, CID, {'style': gap_style.id, 'items': ['不是字典']})
check('G1 items 非dict 400 格式不正确', code == 400 and '格式不正确' in str(d), f'{code} {d}')

code, d = api('POST', '/orders', AT, CID, {'style': gap_style.id, 'items': [{'size': 'M', 'qty': 1}]})
check('G2 items 缺颜色 400', code == 400 and '缺少颜色' in str(d), f'{code} {d}')

code, d = api('POST', '/orders', AT, CID, {'style': gap_style.id, 'items': [{'color': '红', 'qty': 1}]})
check('G3 items 缺尺码 400', code == 400 and '缺少尺码' in str(d), f'{code} {d}')

code, d = api('POST', '/orders', AT, CID,
              {'style': gap_style.id, 'items': [{'color': '红', 'size': 'M', 'qty': 'abc'}]})
check('G4 items qty 非数字 400 数量必须大于0', code == 400 and '数量必须大于0' in str(d), f'{code} {d}')

# ---- G5~G7 PUT 边界 ----
code, d = api('POST', '/orders', AT, CID,
              {'order_no': 'GAP-S1', 'remark': 'v1', 'style': gap_style.id,
               'items': [{'color': '黑', 'size': 'M', 'qty': 10},
                         {'color': '黑', 'size': 'L', 'qty': 5}]})
check('G-前置 建单 GAP-S1', code in (200, 201) and d.get('id'), f'{code} {d}')
OID = d['id']

# G6 先行：同单号不改（走 exclude_pk 去重分支）
code, d = api('PUT', f'/orders/{OID}', AT, CID,
              {'order_no': 'GAP-S1', 'remark': 'v2', 'style': gap_style.id})
check('G6 PUT 同单号不改 200(exclude_pk 分支)', code == 200, f'{code} {d}')

# G5：不带 items → 明细保持
code, d = api('GET', f'/orders/{OID}', AT, CID)
items_before = len(d.get('items', []))
code, d = api('PUT', f'/orders/{OID}', AT, CID, {'remark': 'v3', 'style': gap_style.id})
code2, d2 = api('GET', f'/orders/{OID}', AT, CID)
check('G5 PUT 不带 items 明细保持', code == 200 and len(d2.get('items', [])) == items_before == 2,
      f'{code}/{code2} before={items_before} after={len(d2.get("items", []))}')

# G7：改单号（软删清场 + 撞号校验走 exclude_pk）
code, d = api('PUT', f'/orders/{OID}', AT, CID, {'order_no': 'GAP-S2', 'style': gap_style.id})
check('G7 PUT 改单号 200', code == 200 and d.get('order_no') == 'GAP-S2', f'{code} {str(d)[:80]}')

# ---- G8 交期充裕绿灯 ----
from datetime import timedelta  # noqa: E402
from django.utils import timezone  # noqa: E402
code, d = api('POST', '/orders', AT, CID,
              {'order_no': 'GAP-S3', 'style': gap_style.id,
               'delivery_date': str(timezone.localdate() + timedelta(days=30)),
               'items': [{'color': '白', 'size': 'M', 'qty': 8}]})
check('G-前置 建单 GAP-S3', code in (200, 201), f'{code} {d}')
code, d = api('GET', '/orders?keyword=GAP-S3', AT, CID)
row = next((r for r in d.get('results', d if isinstance(d, list) else [])), {})
check('G8 交期充裕绿灯(>7天)', row.get('delivery_light') == 'green' and row.get('days_left') == 30,
      f'{row.get("delivery_light")}/{row.get("days_left")}')

print(f'\n==== 订单域补缺套件：{len(PASS)} pass / {len(FAIL)} fail ====')
if FAIL:
    print('FAILED:', FAIL)
