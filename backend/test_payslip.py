# -*- coding: utf-8 -*-
"""
薪资条套件（test_payslip，2026-11-04 竞品对齐 GAP-4 Wave5）。

覆盖：
  PS-BAS-01~03 数据：逐员工行 + 工序明细 + 快照金额优先（wage_detail）
  PS-EXP-01~02 导出：view 点 200 xlsx / 无 view 点 403
  PS-PERM-01 无 salary.view 403
  PS-EDGE-01~03 已撤销结算单可查 / 不存在 404 / 明细与登记记录对账一致

独立工厂「薪资条测试厂(可删)」，跑法同 shell-exec 族。
"""
import json

from django.contrib.auth.hashers import make_password
from django.test import Client

from apps.accounts.models import Company, Post as PostM, User, UserCompany
from apps.accounts.services import create_company_with_owner
from apps.catalog.models import Customer, Process, Style, Ticket
from apps.production.models import Production
from apps.records.models import Record
from apps.salary.models import Settlement
from apps.tech.models import Bom

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


OWNER, H_M, H_V, W1, W2 = (
    '13988716401', '13988716402', '13988716403', '13988716404', '13988716405')
CO_P = '薪资条测试厂(可删)'

company = Company.objects.filter(name=CO_P).first()
if company is None:
    owner = ensure_user(OWNER, '薪资条厂长')
    company, _ = create_company_with_owner(owner, CO_P)
else:
    owner = ensure_user(OWNER, '薪资条厂长')
CID = company.id

# 清数据
Record.all_objects.filter(company=company).delete()
Settlement.all_objects.filter(company=company).delete()
Ticket.all_objects.filter(company=company).delete()
Production.all_objects.filter(company=company).delete()
Bom.all_objects.filter(company=company).delete()
Process.all_objects.filter(company=company).delete()
Style.all_objects.filter(company=company).delete()
Customer.all_objects.filter(company=company).delete()

post_view = ensure_post(company, '薪资条查看岗',
                        ['salary.view', 'salary.settle', 'production.manage'])
post_none = ensure_post(company, '薪资条无点岗', ['records.create'])
mgr = ensure_user(H_M, '薪资条管理员')
viewer = ensure_user(H_V, '薪资条查看员')
w1 = ensure_user(W1, '王裁剪')
w2 = ensure_user(W2, '李车缝')
ensure_member(mgr, company, post_view)
ensure_member(viewer, company, post_view)
ensure_member(w1, company, post_none)
ensure_member(w2, company, post_none)

TOK_M = login(H_M)
TOK_N = login(H_N if False else H_V)  # 查看员
TOK_NONE = login(W1)
check('PS-SETUP 登录', bool(TOK_M) and bool(TOK_N))

cu = Customer.objects.create(company=company, customer_no='PS-CU-1', name='薪资条客户')
sty = Style.objects.create(company=company, style_no='PS-S1', name='薪资条款')
Bom.objects.create(company=company, style=sty, status=Bom.Status.ACTIVE)
p_cut = Process.objects.create(company=company, name='裁剪P', default_price='2.000', is_fixed=True)
p_sew = Process.objects.create(company=company, name='车缝P', default_price='1.000', is_fixed=True)

st, d, _ = api('POST', '/productions', token=TOK_M, cid=CID,
               data={'style': sty.id, 'bed_no': 'P1', 'total_qty': 100})
assert st == 201, (st, d)
prod = Production.objects.get(company=company, bed_no='P1')
t1 = Ticket.objects.create(company=company, production=prod, bed_no='P1',
                           bundle_no='1', color='白', size='M', qty=100, qr_code='px')

# 直接造登记记录（不走接口，聚焦薪资条本身）
r1 = Record.objects.create(company=company, ticket=t1, process=p_cut, user=w1,
                           operator=w1, qty=40, price=p_cut.default_price)
r2 = Record.objects.create(company=company, ticket=t1, process=p_sew, user=w1,
                           operator=w1, qty=30, price=p_sew.default_price)
r3 = Record.objects.create(company=company, ticket=t1, process=p_sew, user=w2,
                           operator=w2, qty=50, price=p_sew.default_price)
# w1 计件合计 40*2+30*1=110；w2 = 50*1=50

settle = Settlement.objects.create(
    company=company, period_start='2026-11-01', period_end='2026-11-30',
    employee_count=2, amount='160.00',
    wage_detail={str(w1.id): '110.00', str(w2.id): '50.00'},
    created_by=mgr)
Record.objects.filter(company=company).update(settlement=settle)

# ================= PS-BAS 数据 =================
st, d, _ = api('GET', f'/salary/settlements/{settle.id}/payslip', token=TOK_M, cid=CID)
check('PS-BAS-01 薪资条 200', st == 200, f'{st}')
rows = {r['user_id']: r for r in d.get('rows', [])}
check('PS-BAS-02 逐员工两行', len(d.get('rows', [])) == 2, str(len(d.get('rows', []))))
w1row = rows.get(w1.id, {})
check('PS-BAS-03 w1 工序明细 2 条', len(w1row.get('items', [])) == 2, str(w1row))
check('PS-BAS-04 快照金额优先', w1row.get('snapshot_amount') == '110.00'
      and w1row.get('amount') == '110.00', str(w1row.get('amount')))
check('PS-BAS-05 明细与登记对账一致',
      sum(float(i['amount']) for i in w1row.get('items', [])) == 110.0, str(w1row))
check('PS-BAS-06 合计 160', d.get('total') == '160.00', str(d.get('total')))
check('PS-BAS-07 结算单元数据',
      d.get('settlement', {}).get('period_start') == '2026-11-01', str(d.get('settlement')))

# 无快照回退明细合计
settle2 = Settlement.objects.create(
    company=company, period_start='2026-10-01', period_end='2026-10-31',
    employee_count=1, amount='0', wage_detail=None, created_by=mgr)
st, d, _ = api('GET', f'/salary/settlements/{settle2.id}/payslip', token=TOK_M, cid=CID)
check('PS-EDGE-01 无登记空薪资条', st == 200 and d.get('rows') == [] and d.get('total') == '0.00',
      f'{st} {d}')

# 已撤销可查
settle.status = Settlement.Status.REVOKED
settle.save(update_fields=['status'])
st, d, _ = api('GET', f'/salary/settlements/{settle.id}/payslip', token=TOK_M, cid=CID)
check('PS-EDGE-02 已撤销可查', st == 200, f'{st}')
settle.status = Settlement.Status.SETTLED
settle.save(update_fields=['status'])

# 404
st, d, _ = api('GET', '/salary/settlements/999999/payslip', token=TOK_M, cid=CID)
check('PS-EDGE-03 不存在 404', st == 404, f'{st}')

# ================= PS-PERM =================
st, d, _ = api('GET', f'/salary/settlements/{settle.id}/payslip', token=TOK_NONE, cid=CID)
check('PS-PERM-01 无 salary.view 403', st == 403, f'{st}')

# ================= PS-EXP 导出 =================
st, d, r = api('GET', f'/salary/settlements/{settle.id}/payslip/export', token=TOK_M, cid=CID)
ct = r.get('Content-Type') or r.get('content-type') or ''
check('PS-EXP-01 导出 200 xlsx', st == 200 and 'spreadsheetml' in ct, f'{st} {ct}')
st, d, r = api('GET', f'/salary/settlements/{settle.id}/payslip/export', token=TOK_NONE, cid=CID)
check('PS-EXP-02 无 view 点 403', st == 403, f'{st}')

print()
print(f'==== 薪资条套件：PASS {len(PASS)} / FAIL {len(FAIL)} ====')
if FAIL:
    print('FAILURES:', FAIL)
