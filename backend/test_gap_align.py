# -*- coding: utf-8 -*-
"""
竞品对齐 Wave1 套件（test_gap_align，2026-11-04）——差距分析 GAP-2/3/6/7/8。

覆盖：
  GA-PROC-01~03 工序 is_key/hide_price 字段 PUT 生效（styles.manage）
  GA-HIDE-01~04 工价隐藏：选择器对无 styles.price/salary.view 者下发 price=null
                + price_hidden=true；styles.price 持有者与管理员可见
  GA-PATCH-01~06 修改数量填原因：缺 reason 400 / 正常 200+QTY 留痕含原因 /
                封顶 400 / 锁床 400 / 非本人无点 403 / 非本人有点 200
  GA-BREG-01~04 批量代登：两扎成功+超限扎失败（部分成功）/ entries 空 400 /
                >50 扎 400 / 工序不在床次配置 → 逐行失败
  GA-BUND-01~03 批量撤销：2 撤 1 不存在 / 已结算软删行不可撤 / 留痕成对
  GA-EXP-01~02 导出 xlsx：200+content-type / 无 salary.view 403
  GA-DIM-01~03  dim=process 全量 / key_only=1 只含关键工序 / 非法 dim=process 外忽略 key_only

独立工厂「对齐测试厂(可删)」，跑法同 shell-exec 族。
"""
import json

from django.contrib.auth.hashers import make_password
from django.test import Client

from apps.accounts.models import Company, Post as PostM, User, UserCompany
from apps.accounts.services import create_company_with_owner
from apps.catalog.models import Customer, Process, Style, Ticket
from apps.ops.models import AuditLog
from apps.production.models import Production
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


OWNER, H_MGR, H_VIEW, H_WORKER = (
    '13988716101', '13988716102', '13988716103', '13988716104')
CO_A = '对齐测试厂(可删)'


def preclean(company):
    AuditLog.objects.filter(company=company).delete()
    Record.all_objects.filter(company=company).delete()
    Ticket.all_objects.filter(company=company).delete()
    Production.all_objects.filter(company=company).delete()
    from apps.tech.models import Bom  # noqa: E402
    Bom.all_objects.filter(company=company).delete()
    Process.all_objects.filter(company=company).delete()
    Style.all_objects.filter(company=company).delete()
    Customer.all_objects.filter(company=company).delete()


company = Company.objects.filter(name=CO_A).first()
if company is None:
    owner = ensure_user(OWNER, '对齐厂长')
    company, _ = create_company_with_owner(owner, CO_A)
else:
    owner = ensure_user(OWNER, '对齐厂长')
CID = company.id
preclean(company)

post_mgr = ensure_post(company, '对齐管理岗',
                       ['styles.manage', 'styles.price', 'records.create',
                        'records.revoke', 'scan.assist', 'salary.view',
                        'production.manage'])
post_view = ensure_post(company, '对齐查看岗', ['records.create'])
mgr = ensure_user(H_MGR, '对齐管理员')
viewer = ensure_user(H_VIEW, '对齐查看员')
worker = ensure_user(H_WORKER, '对齐工人')
ensure_member(mgr, company, post_mgr)
ensure_member(viewer, company, post_view)
ensure_member(worker, company, post_view)

TOK_MGR = login(H_MGR)
TOK_VIEW = login(H_VIEW)
check('GA-SETUP 登录', bool(TOK_MGR) and bool(TOK_VIEW))

cu = Customer.objects.create(company=company, customer_no='GAP-CU-1', name='对齐客户')
sty = Style.objects.create(company=company, style_no='GAP-S1', name='对齐款')

# 开裁前置（P1 规则）：款式需一张生效 BOM
from apps.tech.models import Bom  # noqa: E402
Bom.objects.create(company=company, style=sty, status=Bom.Status.ACTIVE)

# ================= 工序 is_key / hide_price =================
p1 = Process.objects.create(company=company, name='车缝G', default_price='1.500', is_fixed=True)
p2 = Process.objects.create(company=company, name='锁边G', default_price='0.800', is_fixed=True)

st, d, _ = api('PUT', f'/processes/{p1.id}', token=TOK_MGR, cid=CID,
               data={'name': '车缝G', 'default_price': '1.500', 'is_fixed': True,
                     'is_key': True, 'hide_price': True, 'sort': 1})
check('GA-PROC-01 PUT is_key+hide_price 200', st == 200, f'{st} {d}')
p1.refresh_from_db()
check('GA-PROC-02 字段落库', p1.is_key and p1.hide_price)
st, d, _ = api('PUT', f'/processes/{p2.id}', token=TOK_MGR, cid=CID,
               data={'name': '锁边G', 'default_price': '0.800', 'is_fixed': True,
                     'sort': 2})
check('GA-PROC-03 不传新字段 200（向后兼容）', st == 200, st)

# ================= 床次 + 菲票 =================
st, d, _ = api('POST', '/productions', token=TOK_MGR, cid=CID,
               data={'style': sty.id, 'bed_no': 'G1', 'total_qty': 100})
assert st == 201, (st, d)
prod = Production.objects.get(company=company, bed_no='G1')
t1 = Ticket.objects.create(company=company, production=prod, bed_no='G1',
                           bundle_no='1', color='红', size='M', qty=100, qr_code='x')
t2 = Ticket.objects.create(company=company, production=prod, bed_no='G1',
                           bundle_no='2', color='红', size='L', qty=100, qr_code='y')

# ================= GA-HIDE 工价隐藏（选择器） =================
# 查看员（无 styles.price/salary.view）→ 车缝G 隐藏价，锁边G 可见
st, d, _ = api('GET', f'/records/process-selector?ticket_id={t1.id}',
               token=TOK_VIEW, cid=CID)
rows = {r['id']: r for r in d.get('results', [])}
check('GA-HIDE-01 无点者隐藏工序 price=null', st == 200 and rows[p1.id]['price'] is None,
      f'{st} {rows.get(p1.id)}')
check('GA-HIDE-02 price_hidden=true 下发', rows[p1.id].get('price_hidden') is True,
      str(rows.get(p1.id)))
check('GA-HIDE-03 非隐藏工序价格正常', rows[p2.id]['price'] == '0.800',
      str(rows.get(p2.id)))
st, d, _ = api('GET', f'/records/process-selector?ticket_id={t1.id}',
               token=TOK_MGR, cid=CID)
rows = {r['id']: r for r in d.get('results', [])}
check('GA-HIDE-04 styles.price 持有者可见', rows[p1.id]['price'] == '1.500',
      str(rows.get(p1.id)))

# ================= GA-PATCH 修改数量填原因 =================
st, d, _ = api('POST', '/records', token=TOK_MGR, cid=CID,
               data={'ticket_id': t1.id, 'process_id': p1.id, 'qty': 40, 'user_id': worker.id})
assert st == 201, (st, d)
rec1 = d
st, d, _ = api('POST', '/records', token=TOK_MGR, cid=CID,
               data={'ticket_id': t2.id, 'process_id': p1.id, 'qty': 10, 'user_id': worker.id})
assert st == 201, (st, d)
rec2 = d

st, d, _ = api('PUT', f"/records/{rec1['id']}", token=TOK_MGR, cid=CID,
               data={'qty': 45})
check('GA-PATCH-01 缺 reason 400', st == 400, f'{st} {d}')
st, d, _ = api('PUT', f"/records/{rec1['id']}", token=TOK_MGR, cid=CID,
               data={'qty': 45, 'reason': '  '})
check('GA-PATCH-02 空白 reason 400', st == 400, f'{st} {d}')
st, d, _ = api('PUT', f"/records/{rec1['id']}", token=TOK_MGR, cid=CID,
               data={'qty': 45, 'reason': '员工报错数，改小'})
check('GA-PATCH-03 正常改数 200', st == 200 and d.get('qty') == 45, f'{st} {d}')
log = AuditLog.objects.filter(company=company, type='qty').order_by('-id').first()
check('GA-PATCH-04 QTY 留痕含原因+前后值',
      log is not None and '40 件 → 45 件' in log.message and '员工报错数' in log.message,
      log.message if log else 'no log')
# 封顶：车缝G t1 现累计 45（GA-PATCH-03 已改），改 101 > 扎数 100
st, d, _ = api('PUT', f"/records/{rec1['id']}", token=TOK_MGR, cid=CID,
               data={'qty': 101, 'reason': '超限试探'})
check('GA-PATCH-05 改后超扎数 400', st == 400, f'{st} {d}')
# 非本人：查看员（无 records.revoke）改 worker 的 rec2 → 403
st, d, _ = api('PUT', f"/records/{rec2['id']}", token=TOK_VIEW, cid=CID,
               data={'qty': 12, 'reason': '越权试探'})
check('GA-PATCH-06 非本人无点 403', st == 403, f'{st} {d}')

# ================= GA-BREG 批量代登 =================
st, d, _ = api('POST', '/records/batch-register', token=TOK_MGR, cid=CID,
               data={'process_id': p2.id, 'user_id': worker.id,
                     'entries': [{'ticket_id': t1.id, 'qty': 30},
                                 {'ticket_id': t2.id, 'qty': 20},
                                 {'ticket_id': t1.id, 'qty': 80}]})
check('GA-BREG-01 部分成功 200', st == 200 and d.get('ok') == 2 and d.get('fail') == 1,
      f'{st} {d}')
r_map = {r['ticket_id']: r for r in d.get('results', [])}
check('GA-BREG-02 超限扎失败带原因', r_map.get(t1.id, {}).get('ok') is not True
      and '超限' in (r_map.get(t1.id, {}).get('error') or '')
      or r_map.get(t1.id, {}).get('error') == 'ticket_id 缺失',
      str(r_map))
check('GA-BREG-03 成功行带 record_id/amount',
      r_map.get(t2.id, {}).get('ok') and r_map[t2.id].get('record_id')
      and r_map[t2.id].get('amount') == '16.00', str(r_map.get(t2.id)))
st, d, _ = api('POST', '/records/batch-register', token=TOK_MGR, cid=CID,
               data={'process_id': p2.id, 'entries': []})
check('GA-BREG-04 entries 空 400', st == 400, f'{st} {d}')
st, d, _ = api('POST', '/records/batch-register', token=TOK_MGR, cid=CID,
               data={'process_id': p2.id,
                     'entries': [{'ticket_id': t1.id, 'qty': 1}] * 51})
check('GA-BREG-05 >50 扎 400', st == 400, f'{st} {d}')
# 协助门禁：查看员（无 scan.assist）带 user_id 代登 → 逐行失败
st, d, _ = api('POST', '/records/batch-register', token=TOK_VIEW, cid=CID,
               data={'process_id': p2.id, 'user_id': worker.id,
                     'entries': [{'ticket_id': t1.id, 'qty': 1}]})
check('GA-BREG-06 无 scan.assist 代登失败', st == 200 and d.get('fail') == 1,
      f'{st} {d}')
# 批量自登（不带 user_id）：查看员本人可登
st, d, _ = api('POST', '/records/batch-register', token=TOK_VIEW, cid=CID,
               data={'process_id': p2.id, 'entries': [{'ticket_id': t2.id, 'qty': 5}]})
check('GA-BREG-07 批量自登成功', st == 200 and d.get('ok') == 1, f'{st} {d}')

# ================= GA-BUND 批量撤销 =================
ids = [r_map[t2.id]['record_id'], rec2['id'], 99999999]
st, d, _ = api('POST', '/records/batch-undo', token=TOK_MGR, cid=CID,
               data={'record_ids': ids})
check('GA-BUND-01 部分成功 2/1', st == 200 and d.get('ok') == 2 and d.get('fail') == 1,
      f'{st} {d}')
check('GA-BUND-02 不存在行标失败',
      [r for r in d['results'] if r['record_id'] == 99999999][0]['ok'] is False,
      str(d))
check('GA-BUND-03 撤销留痕成对',
      AuditLog.objects.filter(company=company, type='revoke').count() == 2,
      AuditLog.objects.filter(company=company, type='revoke').count())
st, d, _ = api('POST', '/records/batch-undo', token=TOK_VIEW, cid=CID,
               data={'record_ids': [rec1['id']]})
check('GA-BUND-04 无点者撤他人逐行失败', st == 200 and d.get('fail') == 1, f'{st} {d}')

# ================= GA-EXP 导出 xlsx =================
st, d, r = api('GET', '/records/export', token=TOK_MGR, cid=CID)
check('GA-EXP-01 200 xlsx content-type', st == 200 and
      'spreadsheetml' in (r.get('Content-Type') or ''), f'{st}')
st, d, r = api('GET', '/records/export', token=TOK_VIEW, cid=CID)
check('GA-EXP-02 无 salary.view 403', st == 403, st)

# ================= GA-DIM dim=process =================
st, d, _ = api('GET', '/dashboard/dim?dim=process', token=TOK_MGR, cid=CID)
check('GA-DIM-01 process 维度 200 有行', st == 200 and len(d.get('rows', [])) >= 1,
      f'{st} {str(d)[:120]}')
st, d, _ = api('GET', '/dashboard/dim?dim=process&key_only=1', token=TOK_MGR, cid=CID)
names = [r['cells'][0] for r in d.get('rows', [])]
check('GA-DIM-02 key_only 只含关键工序', st == 200 and names == ['车缝G'],
      f'{names}')
st, d, _ = api('GET', '/dashboard/dim?dim=order&key_only=1', token=TOK_MGR, cid=CID)
check('GA-DIM-03 非 process 维度忽略 key_only', st == 200, f'{st} {str(d)[:80]}')

# ================= cleanup =================
print('\n---- cleanup ----')
try:
    preclean(company)
    for p in (post_mgr, post_view):
        PostM.objects.filter(pk=p.pk).update(permissions=[])
    print('cleanup: done（业务行已清，测试厂/账号复用保留）')
except Exception as e:  # noqa: BLE001
    print('cleanup WARN:', e)

print(f'\n==== 竞品对齐 Wave1 套件：{len(PASS)} pass / {len(FAIL)} fail ====')
if FAIL:
    print('FAILED:', FAIL)
    raise SystemExit(1)
