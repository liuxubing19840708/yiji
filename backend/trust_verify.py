# -*- coding: utf-8 -*-
"""
切片 22（工人端信任补齐）后端验证：
- T1 报工 0 价拦截：0 价工序 POST /records → 400「未配置工价」；上价后 → 201（痛点 #2）
- T2 scope=mine：员工只能看到 target_user/operator 是本人的审计记录；无 scope → 403（痛点 #3）
- T3 管理员全量视图不受影响（无 scope → 200）
跑法：manage.py shell -c "exec(compile(open('trust_verify.py',encoding='utf-8').read(),'trust_verify.py','exec'))"
幂等：自带 preclean/cleanup。
"""
import json

from django.contrib.auth.hashers import make_password
from django.test import Client

from apps.accounts.models import Post as PostM, User, UserCompany
from apps.catalog.models import Ticket
from apps.catalog.models import Process
from apps.ops.models import AuditLog
from apps.records.models import Record

PASS, FAIL = [], []


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    print(('PASS' if cond else 'FAIL'), name, ('' if cond else f'| {detail}'))


def api(method, path, token=None, cid=None, data=None):
    c = Client()
    cid = CID if cid is None else cid  # 缺省用动态测试厂（setup 段先建好）
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


EMP_PHONE = '13977720001'  # 13877770001 已被并行追溯流用作 Company.creator，换号避让
PROC_NAME = 'VTEST-未上价工序'
CO_NAME = '透明化验证厂(可删)'

# ================= preclean =================
# 原套件依赖演示种子库 company_id=2，该库已清空 → 改为动态建厂（仅改 setup，
# 断言保持原样；Company.creator PROTECT 不可删，测试厂跨次复用）。
from apps.accounts.models import Company  # noqa: E402
from apps.accounts.services import create_company_with_owner  # noqa: E402
from apps.catalog.models import Style  # noqa: E402
from apps.production.models import Production  # noqa: E402
from apps.production.models import ProductionProcess  # noqa: E402
from apps.records.models import Record  # noqa: E402

emp0 = User.objects.filter(phone=EMP_PHONE).first()
if emp0:
    AuditLog.objects.filter(target_user=emp0).delete()
    Record.all_objects.filter(user=emp0).delete()
    try:
        emp0.delete()
    except ProtectedError:
        pass  # PROTECT（并行流残留引用）时跳过，由后续 create 报错暴露
Process.all_objects.filter(name=PROC_NAME).delete()
Ticket.all_objects.filter(bundle_no='99990', color='透明').delete()
Production.all_objects.filter(bed_no='VTEST-B1').delete()
Style.all_objects.filter(style_no='VTEST-S1').delete()

admin = User.objects.get(phone='13800000001')
company = Company.objects.filter(name=CO_NAME).first()
if company is None:
    company, _ = create_company_with_owner(admin, CO_NAME)
CID = company.id

emp = User.objects.create(
    phone=EMP_PHONE, username=EMP_PHONE, real_name='透明化验证员工',
    password=make_password('yisao1234'), is_active=True)
post = PostM.objects.filter(company=company, is_admin=False, is_deleted=False).first() \
    or PostM.objects.create(company=company, name='VTEST普工', is_admin=False)
UserCompany.objects.create(user=emp, company=company, post=post,
                           status=UserCompany.Status.ACTIVE)

proc = Process.objects.create(company=company, name=PROC_NAME, default_price=0,
                              is_fixed=False)
# 2026-10-18 新规：登记工序须 ∈ 床次工序集、(票,工序) 累计 ≤ 扎数 →
# 自建床次/菲票并把测试工序配置进床次（price=0 保持 T1 0价拦截/默认价回退语义）
_style = Style.objects.create(company=company, style_no='VTEST-S1', name='透明化验证款')
_prod = Production.objects.create(company=company, style=_style, bed_no='VTEST-B1',
                                  total_qty=50)
TK = Ticket.objects.create(company=company, production=_prod, bed_no='VTEST-B1',
                           bundle_no='99990', color='透明', size='XL', qty=50,
                           qr_code='VTEST|99990')
ProductionProcess.all_objects.filter(production_id=TK.production_id,
                                     process_id=proc.id).delete()
ProductionProcess.objects.create(production_id=TK.production_id,
                                 process_id=proc.id, price=0)

AT = login('13800000001')
ET = login(EMP_PHONE)
check('P0 员工登录', bool(ET))

# ================= T1 报工 0 价拦截 =================
code, d = api('POST', '/records', AT, data={'ticket_id': TK.id, 'process_id': proc.id, 'qty': 5})
msg = json.dumps(d, ensure_ascii=False)
check('T1a 0价工序报工被拒(400)', code == 400, f'{code} {msg}')
check('T1b 文案带工序名+指引', '未配置工价' in msg and PROC_NAME in msg, msg[:120])

Process.objects.filter(pk=proc.id).update(default_price=8.5)
code, d = api('POST', '/records', AT, data={'ticket_id': TK.id, 'process_id': proc.id, 'qty': 5})
check('T1c 上价后立即可登记(201)', code == 201, f'{code} {json.dumps(d, ensure_ascii=False)[:120]}')
rid = d.get('id')

# 协助登记（管理员代员工）：生成 target_user=emp 的审计记录
code, d = api('POST', '/records', AT, data={
    'ticket_id': TK.id, 'process_id': proc.id, 'user_id': emp.id, 'qty': 3})
check('T1d 协助登记同样要求已上价(201)', code == 201, f'{code}')
aid = d.get('id')

# ================= T2 scope=mine =================
code, d = api('GET', '/audit-logs?scope=mine&page_size=50', ET)
check('T2a 员工scope=mine可读(200)', code == 200, f'{code}')
rows = d.get('results', [])
check('T2b 协助登记出现在员工变更流',
      any(r.get('type') == 'assist' and r.get('target_user') == emp.id for r in rows),
      f'rows={len(rows)}')
check('T2c 只含与本人相关的记录',
      all(r.get('target_user') == emp.id or r.get('operator') == emp.id for r in rows),
      f'rows={len(rows)}')
code, d = api('GET', '/audit-logs', ET)
check('T2d 员工无scope被拒(403)', code == 403, f'{code}')
code, d = api('GET', '/audit-logs', AT)
check('T3 管理员全量视图不受影响(200)', code == 200, f'{code}')

# ================= cleanup =================
for pk in (rid, aid):
    if pk:
        Record.objects.filter(pk=pk).delete()
AuditLog.objects.filter(target_user=emp).delete()
proc.delete()
# 手机号 unique 无条件约束：员工必须物理删，软删行会挡住下一轮 create
User.objects.filter(phone=EMP_PHONE).delete()
print(f'== trust_verify: {len(PASS)} passed, {len(FAIL)} failed ==')
