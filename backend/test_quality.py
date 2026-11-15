# -*- coding: utf-8 -*-
"""
质检模块套件（test_quality，2026-11-04 竞品对齐 GAP-1 Wave3）。

覆盖：
  QS-CRE-01~05 登记返修：正常 201 / 缺原因 400 / 超扎数 400 / 跨公司菲票 400 /
               同票同工序在途重复 400
  QS-PERM-01~03 权限：manage 无 view 点者 POST 403 / view 点者 GET 200 /
               无 view 点者 GET 403
  QS-RECV-01~04 收货：正常 200 status=received / 超返修数 400 / 非 rework 400 /
               producer 跨公司 400
  QS-FIN-01~03 完成：正常 200 status=done / 非 received 400 / done 后再完成 400
  QS-DEL-01~03 删除：rework 软删 204 / done 禁删 403 / 软删行不占在途唯一
  QS-AUD-01~02 留痕：收货+完成+删除 other 类型 AuditLog 落条
  QS-EXP-01~02 导出 xlsx：200+content-type / 无 view 点 403

独立工厂「质检测试厂(可删)」，跑法同 shell-exec 族。
"""
import json

from django.contrib.auth.hashers import make_password
from django.test import Client

from apps.accounts.models import Company, Post as PostM, User, UserCompany
from apps.accounts.services import create_company_with_owner
from apps.catalog.models import Customer, Process, Style, Ticket
from apps.ops.models import AuditLog
from apps.production.models import Production
from apps.quality.models import QualityRecord
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


OWNER, H_QM, H_QV, H_NONE = (
    '13988716201', '13988716202', '13988716203', '13988716204')
CO_Q = '质检测试厂(可删)'


def preclean(company):
    AuditLog.objects.filter(company=company).delete()
    QualityRecord.all_objects.filter(company=company).delete()
    Ticket.all_objects.filter(company=company).delete()
    Production.all_objects.filter(company=company).delete()
    Bom.all_objects.filter(company=company).delete()
    Process.all_objects.filter(company=company).delete()
    Style.all_objects.filter(company=company).delete()
    Customer.all_objects.filter(company=company).delete()


company = Company.objects.filter(name=CO_Q).first()
if company is None:
    owner = ensure_user(OWNER, '质检厂长')
    company, _ = create_company_with_owner(owner, CO_Q)
else:
    owner = ensure_user(OWNER, '质检厂长')
CID = company.id
preclean(company)

post_full = ensure_post(company, '质检全权岗',
                        ['quality.view', 'quality.manage', 'production.manage'])
post_view = ensure_post(company, '质检查看岗', ['quality.view'])
post_none = ensure_post(company, '质检无点岗', ['records.create'])
qm = ensure_user(H_QM, '质检管理员')
qv = ensure_user(H_QV, '质检查看员')
qn = ensure_user(H_NONE, '无点员工')
ensure_member(qm, company, post_full)
ensure_member(qv, company, post_view)
ensure_member(qn, company, post_none)

TOK_QM = login(H_QM)
TOK_QV = login(H_QV)
TOK_NONE = login(H_NONE)
check('QS-SETUP 登录', bool(TOK_QM) and bool(TOK_QV) and bool(TOK_NONE))

cu = Customer.objects.create(company=company, customer_no='QS-CU-1', name='质检客户')
sty = Style.objects.create(company=company, style_no='QS-S1', name='质检款')
Bom.objects.create(company=company, style=sty, status=Bom.Status.ACTIVE)
proc = Process.objects.create(company=company, name='车缝Q', default_price='1.000', is_fixed=True)

st, d, _ = api('POST', '/productions', token=TOK_QM, cid=CID,
               data={'style': sty.id, 'bed_no': 'Q1', 'total_qty': 100})
assert st == 201, (st, d)
prod = Production.objects.get(company=company, bed_no='Q1')
t1 = Ticket.objects.create(company=company, production=prod, bed_no='Q1',
                           bundle_no='1', color='蓝', size='M', qty=100, qr_code='qx')
t2 = Ticket.objects.create(company=company, production=prod, bed_no='Q1',
                           bundle_no='2', color='蓝', size='L', qty=100, qr_code='qy')

# ================= QS-CRE 登记返修 =================
st, d, _ = api('POST', '/quality/records', token=TOK_QM, cid=CID,
               data={'ticket': t1.id, 'process': proc.id, 'defect_qty': 5,
                     'reason': '线迹跳针'})
check('QS-CRE-01 正常登记 201', st == 201, f'{st} {d}')
qr1 = d.get('id')
check('QS-CRE-02 默认 rework+质检人快照',
      d.get('status') == 'rework' and d.get('inspector_name') == '质检管理员', str(d))

st, d, _ = api('POST', '/quality/records', token=TOK_QM, cid=CID,
               data={'ticket': t1.id, 'process': proc.id, 'defect_qty': 3, 'reason': '  '})
check('QS-CRE-03 缺原因 400', st == 400, f'{st} {d}')

st, d, _ = api('POST', '/quality/records', token=TOK_QM, cid=CID,
               data={'ticket': t1.id, 'process': proc.id, 'defect_qty': 999,
                     'reason': '超量'})
check('QS-CRE-04 超扎数 400', st == 400, f'{st} {d}')

# 跨公司菲票：测试必须自建他厂完整对象，库里碰巧没有他厂数据也不能跳过。
other_co = Company.objects.create(
    name='质检跨厂隔离公司', company_no=Company.next_company_no(), creator=qm)
other_style = Style.objects.create(
    company=other_co, style_no='QS-FOREIGN-S1', name='他厂质检款')
other_prod = Production.objects.create(
    company=other_co, style=other_style, bed_no='QS-FOREIGN-BED', total_qty=10)
t_foreign = Ticket.objects.create(
    company=other_co, production=other_prod, bed_no=other_prod.bed_no,
    bundle_no='1', color='红', size='M', qty=10, qr_code='QS-FOREIGN-QR')
st, d, _ = api('POST', '/quality/records', token=TOK_QM, cid=CID,
               data={'ticket': t_foreign.id, 'defect_qty': 1, 'reason': '跨公司'})
check('QS-CRE-05 跨公司菲票 400', st == 400
      and not QualityRecord.all_objects.filter(ticket=t_foreign).exists(), f'{st} {d}')

# 同票同工序在途重复
st, d, _ = api('POST', '/quality/records', token=TOK_QM, cid=CID,
               data={'ticket': t1.id, 'process': proc.id, 'defect_qty': 2, 'reason': '重复'})
check('QS-CRE-06 在途重复 400', st == 400, f'{st} {d}')

# ================= QS-PERM 权限 =================
st, d, _ = api('POST', '/quality/records', token=TOK_QV, cid=CID,
               data={'ticket': t2.id, 'defect_qty': 1, 'reason': '无 manage 点'})
check('QS-PERM-01 仅 view 点 POST 403', st == 403, f'{st} {d}')
st, d, _ = api('GET', '/quality/records', token=TOK_QV, cid=CID)
check('QS-PERM-02 view 点 GET 200', st == 200 and d.get('count', 0) >= 1, f'{st}')
st, d, _ = api('GET', '/quality/records', token=TOK_NONE, cid=CID)
check('QS-PERM-03 无 view 点 GET 403', st == 403, f'{st}')

# 整扎返修（不挂工序）+ 在途唯一不拦不同票
st, d, _ = api('POST', '/quality/records', token=TOK_QM, cid=CID,
               data={'ticket': t2.id, 'defect_qty': 10, 'reason': '色差整扎'})
check('QS-CRE-07 不挂工序可登记', st == 201, f'{st} {d}')
qr2 = d.get('id')

# ================= QS-RECV 收货 =================
st, d, _ = api('PUT', f'/quality/records/{qr1}', token=TOK_QM, cid=CID,
               data={'action': 'receive', 'received_qty': 5, 'producer': qn.id})
check('QS-RECV-01 正常收货 200 received', st == 200 and d.get('status') == 'received',
      f'{st} {d}')
check('QS-RECV-02 收回数量+工人落库',
      d.get('received_qty') == 5 and d.get('producer_name') == '无点员工', str(d))

st, d, _ = api('POST', '/quality/records', token=TOK_QM, cid=CID,
               data={'ticket': t1.id, 'process': proc.id, 'defect_qty': 3, 'reason': '再修'})
qr3 = d.get('id')
st, d, _ = api('PUT', f'/quality/records/{qr3}', token=TOK_QM, cid=CID,
               data={'action': 'receive', 'received_qty': 99})
check('QS-RECV-03 收回超返修数 400', st == 400, f'{st} {d}')

st, d, _ = api('PUT', f'/quality/records/{qr1}', token=TOK_QM, cid=CID,
               data={'action': 'receive', 'received_qty': 1})
check('QS-RECV-04 非 rework 再收货 400', st == 400, f'{st} {d}')

# ================= QS-FIN 完成 =================
st, d, _ = api('PUT', f'/quality/records/{qr1}', token=TOK_QM, cid=CID,
               data={'action': 'finish', 'remark': '返修完成复检通过'})
check('QS-FIN-01 收货后完成 200 done', st == 200 and d.get('status') == 'done', f'{st} {d}')
check('QS-FIN-02 完成备注落库', d.get('remark') == '返修完成复检通过', str(d))

st, d, _ = api('PUT', f'/quality/records/{qr2}', token=TOK_QM, cid=CID,
               data={'action': 'finish'})
check('QS-FIN-03 rework 直完成 400', st == 400, f'{st} {d}')

st, d, _ = api('PUT', f'/quality/records/{qr1}', token=TOK_QM, cid=CID,
               data={'action': 'finish'})
check('QS-FIN-04 done 后再完成 400', st == 400, f'{st} {d}')

# ================= QS-DEL 删除 =================
st, d, _ = api('DELETE', f'/quality/records/{qr2}', token=TOK_QM, cid=CID)
check('QS-DEL-01 rework 软删 204', st == 204, f'{st}')
check('QS-DEL-02 done 禁删 403',
      api('DELETE', f'/quality/records/{qr1}', token=TOK_QM, cid=CID)[0] == 403)

# 软删行不占在途唯一：qr2 已删，t2 同参可再建
st, d, _ = api('POST', '/quality/records', token=TOK_QM, cid=CID,
               data={'ticket': t2.id, 'defect_qty': 4, 'reason': '删后重建'})
check('QS-DEL-03 软删后同键可重建', st == 201, f'{st} {d}')

# ================= QS-AUD 留痕 =================
audits = list(AuditLog.objects.filter(company=company, type='other')
              .order_by('-id').values_list('message', flat=True)[:5])
check('QS-AUD-01 收货/完成留痕', any('返修收货' in m for m in audits)
      and any('返修完成' in m for m in audits), str(audits))
check('QS-AUD-02 删除留痕', any('删除质检记录' in m for m in audits), str(audits))

# ================= QS-EXP 导出 =================
st, d, r = api('GET', '/quality/export', token=TOK_QV, cid=CID)
ct = r.get('Content-Type') or r.get('content-type') or ''
check('QS-EXP-01 view 点导出 200 xlsx',
      st == 200 and 'spreadsheetml' in ct, f'{st} {ct}')
st, d, r = api('GET', '/quality/export', token=TOK_NONE, cid=CID)
check('QS-EXP-02 无 view 点 403', st == 403, f'{st}')

print()
print(f'==== 质检套件：PASS {len(PASS)} / FAIL {len(FAIL)} ====')
if FAIL:
    print('FAILURES:', FAIL)
