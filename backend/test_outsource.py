# -*- coding: utf-8 -*-
"""
外发模块套件（test_outsource，2026-11-04 竞品对齐 GAP-5 Wave4）。

覆盖：
  OS-CRE-01~04 建单：正常 201（单号 OS 前缀自动） / 外发数量 0 400 /
               单价×数量=加工费 / 主档厂名快照
  OS-PERM-01~03 权限：仅 view 点 POST 403 / view 点 GET 200 / 无 view 点 GET 403
  OS-RECV-01~05 收回：正常 200 partial / 累计足额 → returned / 超余量 400 /
               settled 再收回 400 / 收回明细落库
  OS-SETTLE-01~03 结算：returned → settled 200 / 非 returned 400 / settled 禁删 403
  OS-DEL-01~02 删除：无收回 204 / 有收回 400
  OS-AUD-01 留痕：建单/收回/结算 other 类型 AuditLog
  OS-EXP-01~02 导出 xlsx：200+content-type / 无 view 点 403
  OS-NO-01 单号连续递增

独立工厂「外发测试厂(可删)」，跑法同 shell-exec 族。
"""
import json

from django.contrib.auth.hashers import make_password
from django.test import Client

from apps.accounts.models import Company, Post as PostM, User, UserCompany
from apps.accounts.services import create_company_with_owner
from apps.catalog.models import Customer, Style
from apps.ops.models import AuditLog
from apps.subcontract.models import OutsourceOrder, OutsourceReceive
from apps.warehouse.models import Supplier

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
    return r.status_code, body, r


def login(phone, password='yisao1234'):
    _, d, _ = api('POST', '/auth/login', data={'phone': phone, 'password': password})
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


OWNER, H_M, H_V, H_N = '13988716301', '13988716302', '13988716303', '13988716304'
CO_O = '外发测试厂(可删)'


def preclean(company):
    AuditLog.objects.filter(company=company).delete()
    OutsourceReceive.all_objects.filter(company=company).delete()
    OutsourceOrder.all_objects.filter(company=company).delete()
    Supplier.all_objects.filter(company=company).delete()
    Style.all_objects.filter(company=company).delete()
    Customer.all_objects.filter(company=company).delete()


company = Company.objects.filter(name=CO_O).first()
if company is None:
    owner = ensure_user(OWNER, '外发厂长')
    company, _ = create_company_with_owner(owner, CO_O)
else:
    owner = ensure_user(OWNER, '外发厂长')
CID = company.id
preclean(company)

post_full = ensure_post(company, '外发全权岗', ['outsource.view', 'outsource.manage'])
post_view = ensure_post(company, '外发查看岗', ['outsource.view'])
post_none = ensure_post(company, '外发无点岗', ['records.create'])
mgr = ensure_user(H_M, '外发管理员')
viewer = ensure_user(H_V, '外发查看员')
none_user = ensure_user(H_N, '外发无点员')
ensure_member(mgr, company, post_full)
ensure_member(viewer, company, post_view)
ensure_member(none_user, company, post_none)

TOK_M = login(H_M)
TOK_V = login(H_V)
TOK_N = login(H_N)
check('OS-SETUP 登录', bool(TOK_M) and bool(TOK_V) and bool(TOK_N))

sup = Supplier.objects.create(company=company, code='OS-SUP-1', name='宏达外协厂')
sty = Style.objects.create(company=company, style_no='OS-S1', name='外发款')

# ================= OS-CRE 建单 =================
st, d, _ = api('POST', '/outsource/orders', token=TOK_M, cid=CID,
               data={'supplier': sup.id, 'style': sty.id, 'process_desc': '水洗+整烫',
                     'send_qty': 200, 'unit_price': '0.500',
                     'due_date': '2026-11-10', 'remark': '走量大货'})
check('OS-CRE-01 建单 201', st == 201, f'{st} {d}')
o1 = d.get('id')
check('OS-CRE-02 单号 OS 前缀', str(d.get('no', '')).startswith('OS'), str(d.get('no')))
check('OS-CRE-03 加工费=数量×单价', d.get('amount') == '100.00', str(d.get('amount')))
check('OS-CRE-04 厂名快照', d.get('factory_name') == '宏达外协厂', str(d.get('factory_name')))

st, d, _ = api('POST', '/outsource/orders', token=TOK_M, cid=CID,
               data={'factory_name': '个体王师傅', 'send_qty': 0, 'unit_price': '1.000'})
check('OS-CRE-05 外发数量 0 → 400', st == 400, f'{st} {d}')

# 手填厂（无主档）
st, d, _ = api('POST', '/outsource/orders', token=TOK_M, cid=CID,
               data={'factory_name': '个体王师傅', 'send_qty': 50, 'unit_price': '1.000'})
check('OS-CRE-06 手填厂 201', st == 201, f'{st} {d}')
o2 = d.get('id')
check('OS-CRE-07 状态默认 sent', d.get('status') == 'sent', str(d.get('status')))

# 单号连续
no1 = OutsourceOrder.objects.get(pk=o1).no
no2 = OutsourceOrder.objects.get(pk=o2).no
check('OS-NO-01 单号递增', int(no2[-3:]) == int(no1[-3:]) + 1, f'{no1} -> {no2}')

# ================= OS-PERM 权限 =================
st, d, _ = api('POST', '/outsource/orders', token=TOK_V, cid=CID,
               data={'factory_name': 'x', 'send_qty': 1})
check('OS-PERM-01 仅 view 点 POST 403', st == 403, f'{st}')
st, d, _ = api('GET', '/outsource/orders', token=TOK_V, cid=CID)
check('OS-PERM-02 view 点 GET 200', st == 200 and d.get('count', 0) >= 2, f'{st}')
st, d, _ = api('GET', '/outsource/orders', token=TOK_N, cid=CID)
check('OS-PERM-03 无 view 点 GET 403', st == 403, f'{st}')

# ================= OS-RECV 收回 =================
st, d, _ = api('PUT', f'/outsource/orders/{o1}', token=TOK_M, cid=CID,
               data={'action': 'receive', 'qty': 80})
check('OS-RECV-01 收回 80 → partial', st == 200 and d.get('status') == 'partial'
      and d.get('received_qty') == 80, f'{st} {d}')

st, d, _ = api('PUT', f'/outsource/orders/{o1}', token=TOK_M, cid=CID,
               data={'action': 'receive', 'qty': 200})
check('OS-RECV-02 超余量 400', st == 400, f'{st} {d}')

st, d, _ = api('PUT', f'/outsource/orders/{o1}', token=TOK_M, cid=CID,
               data={'action': 'receive', 'qty': 120, 'remark': '尾批含 5 件待修'})
check('OS-RECV-03 收足 → returned', st == 200 and d.get('status') == 'returned'
      and d.get('received_qty') == 200, f'{st} {d}')
check('OS-RECV-04 收回日期落库', bool(d.get('return_date')), str(d.get('return_date')))
check('OS-RECV-05 收回明细 2 条',
      OutsourceReceive.objects.filter(order_id=o1).count() == 2)

st, d, _ = api('PUT', f'/outsource/orders/{o1}', token=TOK_M, cid=CID,
               data={'action': 'receive', 'qty': 1})
check('OS-RECV-06 returned 再收回 400', st == 400, f'{st} {d}')

# ================= OS-SETTLE 结算 =================
st, d, _ = api('PUT', f'/outsource/orders/{o1}', token=TOK_M, cid=CID,
               data={'action': 'settle'})
check('OS-SET-01 returned → settled 200', st == 200 and d.get('status') == 'settled',
      f'{st} {d}')

st, d, _ = api('PUT', f'/outsource/orders/{o2}', token=TOK_M, cid=CID,
               data={'action': 'settle'})
check('OS-SET-02 sent 直结算 400', st == 400, f'{st} {d}')

st, d, _ = api('DELETE', f'/outsource/orders/{o1}', token=TOK_M, cid=CID)
check('OS-SET-03 settled 禁删 403', st == 403, f'{st}')

# ================= OS-DEL 删除 =================
st, d, _ = api('DELETE', f'/outsource/orders/{o2}', token=TOK_M, cid=CID)
check('OS-DEL-01 无收回软删 204', st == 204, f'{st}')

st, d, _ = api('POST', '/outsource/orders', token=TOK_M, cid=CID,
               data={'factory_name': '临时厂', 'send_qty': 10, 'unit_price': '1.000'})
o3 = d.get('id')
api('PUT', f'/outsource/orders/{o3}', token=TOK_M, cid=CID,
    data={'action': 'receive', 'qty': 5})
st, d, _ = api('DELETE', f'/outsource/orders/{o3}', token=TOK_M, cid=CID)
check('OS-DEL-02 有收回 400', st == 400, f'{st} {d}')

# ================= OS-AUD 留痕 =================
msgs = list(AuditLog.objects.filter(company=company, type='other')
            .values_list('message', flat=True))
check('OS-AUD-01 建单/收回/结算留痕',
      any('新建外发单' in m for m in msgs) and any('收回' in m for m in msgs)
      and any('已结算' in m for m in msgs), f'{len(msgs)} 条')

# ================= OS-EXP 导出 =================
st, d, r = api('GET', '/outsource/export', token=TOK_V, cid=CID)
ct = r.get('Content-Type') or r.get('content-type') or ''
check('OS-EXP-01 view 点导出 200 xlsx', st == 200 and 'spreadsheetml' in ct, f'{st} {ct}')
st, d, r = api('GET', '/outsource/export', token=TOK_N, cid=CID)
check('OS-EXP-02 无 view 点 403', st == 403, f'{st}')

print()
print(f'==== 外发套件：PASS {len(PASS)} / FAIL {len(FAIL)} ====')
if FAIL:
    print('FAILURES:', FAIL)
