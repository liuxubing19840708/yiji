# -*- coding: utf-8 -*-
"""
订单变更留痕（test_order_audit）——差距分析 #3（2026-11-03）。

覆盖 GET /orders/{id}/audit-logs：
  OA-01 无 token 401 / OA-02 无点 403 / OA-03 正常拉取含改价+状态留痕
  OA-04 倒序（最近在前） / OA-05 订单锚定（其他订单留痕不串入）
  OA-06 【订单号】定界（同前缀不同单不误伤） / OA-07 跨厂 404 / OA-08 分页

独立工厂「订单留痕测试厂(可删)」，跑法同 shell-exec 族。
"""
import json
from decimal import Decimal

from django.contrib.auth.hashers import make_password
from django.test import Client

from apps.accounts.models import Company, Post as PostM, User, UserCompany
from apps.accounts.services import create_company_with_owner
from apps.catalog.models import Customer, Style
from apps.ops.models import AuditLog
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


def login(phone, password='yisao1234'):
    _, d = api('POST', '/auth/login', data={'phone': phone, 'password': password})
    return d.get('access', '')


def ensure_user(phone, name):
    u = User.objects.filter(phone=phone).first()
    if u is None:
        u = User.objects.create(phone=phone, username=phone, real_name=name,
                                password=make_password('yisao1234'), is_active=True)
    return u


def ensure_post(company, name, perms):
    p = PostM.objects.filter(company=company, name=name, is_deleted=False).first()
    if p is None:
        p = PostM.objects.create(company=company, name=name, permissions=perms)
    else:
        PostM.objects.filter(pk=p.pk).update(permissions=perms)
        p.refresh_from_db()
    return p


def ensure_member(u, company, post):
    uc = UserCompany.all_objects.filter(user=u, company=company).first()
    if uc is None:
        uc = UserCompany.objects.create(user=u, company=company, post=post,
                                        status=UserCompany.Status.ACTIVE)
    uc.is_deleted = False
    uc.status = UserCompany.Status.ACTIVE
    uc.post = post
    uc.save()
    return uc


OWNER, H_PERM, H_VIEW, H_OWNER_B = (
    '13988714101', '13988714102', '13988714103', '13988714104')
CO_A, CO_B = '订单留痕测试厂(可删)', '订单留痕B厂(可删)'


def preclean(company):
    AuditLog.objects.filter(company=company).delete()
    OrderItem.all_objects.filter(order__company=company).delete()
    Order.all_objects.filter(company=company).delete()
    Customer.all_objects.filter(company=company).delete()
    Style.all_objects.filter(company=company).delete()


company = Company.objects.filter(name=CO_A).first()
if company is None:
    owner = ensure_user(OWNER, '留痕厂长')
    company, _ = create_company_with_owner(owner, CO_A)
else:
    owner = ensure_user(OWNER, '留痕厂长')
company_b = Company.objects.filter(name=CO_B).first()
if company_b is None:
    owner_b = ensure_user(H_OWNER_B, '留痕B厂长')
    company_b, _ = create_company_with_owner(owner_b, CO_B)
else:
    owner_b = ensure_user(H_OWNER_B, '留痕B厂长')
CID, CID_B = company.id, company_b.id

preclean(company)
preclean(company_b)

post_view = ensure_post(company, '留痕查看岗', ['orders.view'])
post_perm = ensure_post(company, '留痕管理岗', ['orders.view', 'orders.manage'])
perm_u = ensure_user(H_PERM, '留痕管理员')
view_u = ensure_user(H_VIEW, '留痕查看员')
ensure_member(perm_u, company, post_perm)
ensure_member(view_u, company, post_view)

TOK_PERM = login(H_PERM)
TOK_VIEW = login(H_VIEW)
TOK_OWNER_B = login(H_OWNER_B)
check('OA-SETUP 登录', bool(TOK_PERM) and bool(TOK_VIEW))

admin = User.objects.get(phone=OWNER)
cu = Customer.objects.create(company=company, customer_no='OAL-CU-001', name='留痕客户')
st1 = Style.objects.create(company=company, style_no='OAL-S1', name='留痕款1')
st2 = Style.objects.create(company=company, style_no='OAL-S2', name='留痕款2')

def mk_order(style, items):
    st, d = api('POST', '/orders', token=TOK_PERM, cid=CID, data={
        'customer': cu.id, 'style': style.id, 'delivery_date': '2026-12-31', 'items': items})
    assert st == 201, (st, d)
    return d

# 两张订单：order1 走改价留痕；order2 只有建档+状态留痕
o1 = mk_order(st1, [{'color': '红', 'size': 'M', 'qty': 100}])
o2 = mk_order(st2, [{'color': '蓝', 'size': 'L', 'qty': 50}])

# 触发留痕：o1 改价（敏感操作带 before/after）
st, d = api('PUT', f"/orders/{o1['id']}", token=TOK_PERM, cid=CID, data={
    'customer': cu.id, 'style': st1.id, 'delivery_date': '2026-12-31',
    'items': [{'color': '红', 'size': 'M', 'qty': 100, 'price': '25.50'}]})
check('OA-SETUP o1 改价 200', st == 200, f'{st} {d}')
st, _ = api('PUT', f"/orders/{o1['id']}/status", token=TOK_PERM, cid=CID,
            data={'status': 'done'})
check('OA-SETUP o1 状态 done', st == 200)

BASE = f"/orders/{o1['id']}/audit-logs"

# ================= 权限 / 隔离 =================
st, _ = api('GET', BASE)
check('OA-01 无 token 401', st == 401, st)

st, d = api('GET', BASE, token=TOK_VIEW, cid=CID)
check('OA-02 orders.view 200', st == 200, f'{st} {d}')

st, d = api('GET', BASE, token=TOK_OWNER_B, cid=CID_B)
check('OA-07 跨厂 404', st == 404, f'{st} {d}')

# ================= 主流程 =================
st, d = api('GET', BASE, token=TOK_PERM, cid=CID)
rows = d.get('results', [])
msgs = ' || '.join(r['message'] for r in rows)
check('OA-03 含改价留痕', any(r['type'] == 'price' for r in rows), msgs)
check('OA-03b 含状态变更留痕', any('订单状态变更' in r['message'] for r in rows), msgs)
check('OA-04 倒序', len(rows) >= 2 and rows[0]['id'] > rows[-1]['id'],
      str([r['id'] for r in rows]))
price_row = next((r for r in rows if r['type'] == 'price'), None)
check('OA-03c 改价带 before/after',
      price_row is not None and price_row.get('after') is not None
      and '25.50' in json.dumps(price_row['after'], ensure_ascii=False),
      str(price_row))
check('OA-03d 操作人姓名下发',
      price_row is not None and (price_row.get('operator_name') or '') != '',
      str(price_row))

# o2 的留痕不串入 o1（同厂隔离）；同前缀单号不误伤（【】定界）
st, d2 = api('GET', f"/orders/{o2['id']}/audit-logs", token=TOK_PERM, cid=CID)
rows2 = d2.get('results', [])
check('OA-05 订单锚定不串单', all(o1['order_no'] not in r['message'] for r in rows2),
      str([r['message'] for r in rows2]))
check('OA-06 o2 只含自己的留痕',
      all(o2['order_no'] in r['message'] for r in rows2) and len(rows2) >= 1,
      str([r['message'] for r in rows2]))

# 分页 page_size=1 → next 有值
st, d3 = api('GET', BASE + '?page=1&page_size=1', token=TOK_PERM, cid=CID)
check('OA-08 分页 next', st == 200 and d3.get('next'), f'{st} {str(d3)[:120]}')

# ================= cleanup =================
print('\n---- cleanup ----')
try:
    preclean(company)
    preclean(company_b)
    for p in (post_view, post_perm):
        PostM.objects.filter(pk=p.pk).update(permissions=[])
    print('cleanup: done（业务行已清，测试厂/账号复用保留）')
except Exception as e:  # noqa: BLE001
    print('cleanup WARN:', e)

print(f'\n==== 订单留痕套件：{len(PASS)} pass / {len(FAIL)} fail ====')
if FAIL:
    print('FAILED:', FAIL)
    raise SystemExit(1)
