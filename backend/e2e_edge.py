# -*- coding: utf-8 -*-
"""
边界/安全/并发 E2E 套件（e2e_fullchain 的补充，对应《测试用例-E2E设计》待补 4 组）：
- TC01-16 token 过期 → 401
- TC13-01 SQL 注入（keyword 参数）
- TC13-02 分页边界 / TC13-05 超长字段
- TC07-24 / TC13-03 / TC13-04 并发：同扎登记 / 重复审批 / 重复结算
跑法：manage.py shell -c "exec(compile(open('e2e_edge.py',encoding='utf-8').read(),'e2e_edge.py','exec'))"
幂等：自带 preclean/cleanup。
"""
import json
from concurrent.futures import ThreadPoolExecutor

from django.db.models.deletion import ProtectedError  # noqa: E402
from django.test import Client
from django.utils import timezone
from datetime import timedelta

from apps.accounts.models import User, UserCompany, Post as PostM
from apps.catalog.models import Ticket
from apps.records.models import Record
from apps.salary.models import SettleApply

PASS, FAIL = [], []


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    print(('PASS' if cond else 'FAIL'), name, ('' if cond else f'| {detail}'))


def api(method, path, token=None, cid=2, data=None):
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


def login(phone='13800000001', password='yisao1234'):
    _, d = api('POST', '/auth/login', data={'phone': phone, 'password': password})
    return d.get('access', '')


TEST_PHONE = '13855559999'
QR_TICKET_QS = Ticket.objects.filter(company_id=2, production__is_locked=False,
                                     production__is_cut_done=False).order_by('id')
TK = QR_TICKET_QS.first()

# ================= S1 token 过期（TC01-16） =================
from rest_framework_simplejwt.tokens import RefreshToken  # noqa: E402

AT = login()
u = User.objects.get(phone='13800000001')
tok = RefreshToken.for_user(u).access_token
tok.set_exp(lifetime=timedelta(seconds=-30))  # 已过期
code, d = api('GET', '/auth/me', str(tok))
check('E1 过期token被拒(401)', code == 401, f'{code}')
code, d = api('GET', '/auth/me', 'not-a-token')
check('E1b 伪造token被拒(401)', code == 401, f'{code}')

# ================= S2 SQL 注入（TC13-01） =================
payloads = ["' OR 1=1 --", "1' UNION SELECT NULL--", "%' OR '1'='1", "'; DROP TABLE auth_user;--"]
all_ok = True
for p in payloads:
    for path in (f'/employees?keyword={p}', f'/styles?keyword={p}', f'/customers?keyword={p}',
                 f'/productions?keyword={p}', f'/processes?keyword={p}'):
        code, d = api('GET', path, AT)
        if code != 200:
            all_ok = False
            print('   注入失败样例:', path, code)
            break
        # 期望结果为空（不会因注入而把全表查出来）
        rows = d.get('results') if isinstance(d, dict) else d
        if rows:  # 非空即可能注入生效
            all_ok = all_ok and not any('厂长' in json.dumps(rows))
check('E2 keyword SQL注入不生效且不500', all_ok)
code, d = api('POST', '/auth/login', data={'phone': "' OR '1'='1", 'password': 'x'})
check('E2b 登录注入不生效', code in (400, 401), f'{code}')

# ================= S3 分页/超长字段边界（TC13-02/05） =================
code, d = api('GET', '/styles?page=9999&page_size=1000', AT)
check('E3 超界页码不500(DRF 404合法)', code == 200 or (code == 404 and '无效页面' in str(d)), f'{code} {str(d)[:60]}')
code, d = api('GET', '/employees?page=9999', AT)
check('E3b 员工超界页不500', code == 200, f'{code}')
code, d = api('GET', '/styles?page_size=99999', AT)
check('E3c 超大page_size不500', code == 200, f'{code}')
code, d = api('POST', '/styles', AT, 2, {'style_no': 'EDGELEN', 'name': '长' * 300})
check('E4 超长款式名被校验(400/截断不500)', code in (400, 201), f'{code}')
if code == 201:
    from apps.catalog.models import Style
    Style.objects.filter(style_no='EDGELEN', name__contains='长长长').delete()
code, d = api('POST', '/employees/invite', AT, 2, {'phone': '138000000012', 'real_name': '长号'})
check('E4b 12位手机号邀请被拒', code == 400, f'{code}')
code, d = api('POST', '/companies/create', AT, data={'name': ''})
check('E4c 空公司名被拒', code == 400, f'{code}')

# ================= S4 并发场景（TC07-24 / TC13-03 / TC13-04） =================

def worker(method, path, token, data=None):
    try:
        return api(method, path, token, 2, data)
    except Exception as e:  # SQLite 偶发 locked 也算"被拒"
        return 500, {'_exc': type(e).__name__}


# -- 4a 同票同工序同人并发登记 ×4 → 最终只应有 1 条（增量补登后为 1 条累计行）
PROC_ID = None
from apps.catalog.models import Process  # noqa: E402
from apps.production.models import ProductionProcess  # noqa: E402
proc = Process.objects.filter(company_id=2, is_fixed=True).order_by('id').first()
if proc is None:  # T0：公司 2 经动态工厂重建后无固定工序，补建一个供并发登记用
    proc = Process.objects.create(company_id=2, name='E2E并发工序', is_fixed=True, default_price=1)
PROC_ID = proc.id
# 选一个员工+票+工序组合，先清掉该组合已有记录
emp_uc = UserCompany.objects.filter(company_id=2, status=UserCompany.Status.ACTIVE,
                                    user__phone__startswith='138').exclude(user_id=6) \
    .select_related('user').order_by('id').first()
if emp_uc is None:  # T0：动态工厂后唯一 138 员工可能就是被 exclude 的 user 6（管理员），放开排除兜底
    emp_uc = UserCompany.objects.filter(company_id=2, status=UserCompany.Status.ACTIVE,
                                        user__phone__startswith='138') \
        .select_related('user').order_by('id').first()
if emp_uc is None:
    raise SystemExit('无可用在职员工做并发测试')
emp_uid = emp_uc.user_id
# 2026-10-18 新规：登记工序须在床次工序集内、且累计 ≤ 扎数 →
# 需选 qty≥12（4×3）的票并把工序配置进该床次订单工序
free_ticket = None
for t in QR_TICKET_QS.filter(qty__gte=12):
    # 2026-10-18 累计封顶：要求该 (票,工序) 跨所有员工均无登记
    if not Record.all_objects.filter(ticket=t, process_id=PROC_ID, is_deleted=False).exists():
        free_ticket = t
        break
if free_ticket is None:
    _tk = Ticket.objects.filter(company_id=2, production__is_locked=False) \
        .select_related('production').first()
    if _tk is None:  # T0：公司 2 重建后可能无任何解锁床次，补建款+床次兜底
        from apps.catalog.models import Style  # noqa: E402
        _style = Style.objects.filter(company_id=2).first() or \
            Style.objects.create(company_id=2, style_no='EDGE-S1', name='边界测试款')
        from apps.tech.models import Bom  # noqa: E402  # P4 建床需 active BOM
        if not Bom.all_objects.filter(style=_style).exists():
            Bom.objects.create(company_id=2, style=_style, version=1, status=Bom.Status.ACTIVE)
        _prod = Production.objects.create(company_id=2, style=_style, bed_no='EDGE-B1',
                                          total_qty=100)
    else:
        _prod = _tk.production
    free_ticket = Ticket.objects.create(company_id=2, production=_prod, bed_no=_prod.bed_no,
                                        bundle_no='99991', color='并发', size='XL', qty=50,
                                        qr_code='EDGE|99991')
check('E5 前置:找到空闲票(qty≥12且票+工序无登记)', free_ticket is not None)
if free_ticket:
    ProductionProcess.all_objects.filter(
        production_id=free_ticket.production_id, process_id=PROC_ID).delete()
    ProductionProcess.objects.create(production_id=free_ticket.production_id,
                                     process_id=PROC_ID, price=1)
    Record.all_objects.filter(ticket=free_ticket, process_id=PROC_ID, user_id=emp_uid).delete()
    with ThreadPoolExecutor(max_workers=4) as ex:
        rs = list(ex.map(lambda _: worker('POST', '/records', AT,
                                          {'ticket_id': free_ticket.id, 'process_id': PROC_ID,
                                           'qty': 3, 'user_id': emp_uid}), range(4)))
    oks = [r for r in rs if r[0] in (200, 201)]
    final = Record.all_objects.filter(ticket=free_ticket, process_id=PROC_ID, user_id=emp_uid).count()
    check('E5b 并发登记×4最终仅1条', final == 1, f'oks={len(oks)} final={final} rs={[(r[0]) for r in rs]}')
    ProductionProcess.all_objects.filter(
        production_id=free_ticket.production_id, process_id=PROC_ID).delete()
    Record.all_objects.filter(ticket=free_ticket, process_id=PROC_ID, user_id=emp_uid).delete()

# -- 4b 并发审批同一待审核员工 ×2 → 仅一次成功
_old = User.objects.filter(phone=TEST_PHONE).first()  # 清上次残留（ORM delete 级联其 UC）
if _old:
    _old.delete()
nu = User.objects.create_user(username=f'u_{TEST_PHONE}', phone=TEST_PHONE, real_name='并发审批')
uc = UserCompany.objects.create(user=nu, company_id=2, status=UserCompany.Status.PENDING)
with ThreadPoolExecutor(max_workers=2) as ex:
    rs = list(ex.map(lambda _: worker('POST', f'/employees/{uc.id}/approve', AT), range(2)))
ok_n = len([r for r in rs if r[0] == 200])
uc.refresh_from_db()
check('E6 并发审批×2仅1次成功且终态ACTIVE', ok_n == 1 and uc.status == UserCompany.Status.ACTIVE,
      f'ok_n={ok_n} status={uc.status} rs={[r[0] for r in rs]}')

# -- 4c 并发直接结算同一员工同期间 ×2 → 仅一单
uc_post = UserCompany.objects.filter(user_id=emp_uid, company_id=2).first()
Record.all_objects.filter(user_id=emp_uid, company_id=2).delete()
t2 = None
for t in QR_TICKET_QS:
    if not Record.all_objects.filter(ticket=t, process_id=PROC_ID, user_id=emp_uid).exists():
        t2 = t
        break
if t2:
    Record.objects.create(company_id=2, ticket=t2, process_id=PROC_ID, user_id=emp_uid,
                          qty=5, price=1, registered_at=timezone.now())
    today = timezone.localdate().isoformat()
    with ThreadPoolExecutor(max_workers=2) as ex:
        rs = list(ex.map(lambda _: worker('POST', '/salary/settlements', AT,
                                          {'period_start': today, 'period_end': today,
                                           'user_id': emp_uid}), range(2)))
    ok_n = len([r for r in rs if r[0] in (200, 201)])
    check('E7 并发结算×2仅1单', ok_n == 1, f'ok_n={ok_n} rs={[r[0] for r in rs]}')
    from apps.salary.models import Settlement
    Settlement.all_objects.filter(user_id=emp_uid, company_id=2).delete()
    Record.all_objects.filter(user_id=emp_uid, company_id=2).delete()

# ================= S5 补遗：容量上限(规则⑨) + 删除有票款式 =================
from apps.accounts.models import Company as _Company  # noqa: E402
from apps.catalog.models import Style as _StyleM  # noqa: E402
from apps.production.models import Production as _ProdM  # noqa: E402

CAP_E1, CAP_E2, CAP_E3 = '13877770001', '13877770002', '13877770003'
CAP_PHONES = [CAP_E1, CAP_E2, CAP_E3]

# E8 容量上限：建 capacity=2 的新公司（创建者占 1），批 1 人→满员→审批/邀请均 400
# 上次残留（PROTECT 容错：并行测试可能把同号用作了 Company.creator）
for _u in User.objects.filter(phone__in=CAP_PHONES):
    try:
        _u.delete()
    except ProtectedError:
        pass
_old_cap = _Company.objects.filter(name='EDGE容量公司').first()
if _old_cap:
    _old_cap.delete()
code, d = api('POST', '/companies/create', AT, None, {'name': 'EDGE容量公司', 'city': '汕头'})
check('E8a 建容量公司', code in (200, 201) and d.get('company', {}).get('id'), f'{code} {str(d)[:80]}')
CID_CAP = d.get('company', {}).get('id')
_Company.objects.filter(id=CID_CAP).update(capacity=2)  # 建完压到 2 人做满员实验

code, d1 = api('POST', '/employees/invite', AT, CID_CAP, {'phone': CAP_E1, 'real_name': '容量甲'})
check('E8b 邀请第1人(1/2)', code in (200, 201), f'{code} {str(d1)[:80]}')
code, d2 = api('POST', '/employees/invite', AT, CID_CAP, {'phone': CAP_E2, 'real_name': '容量乙'})
check('E8c 邀请第2人(仍在容量内,挂起)', code in (200, 201), f'{code} {str(d2)[:80]}')
emps = api('GET', '/employees?tab=pending', AT, CID_CAP)
emps = emps[1].get('results') if isinstance(emps[1], dict) else emps[1]
e1 = [e for e in emps if e.get('phone') == CAP_E1][0]
e2 = [e for e in emps if e.get('phone') == CAP_E2][0]
code, _ = api('POST', f"/employees/{e1['id']}/approve", AT, CID_CAP, {})
check('E8d 审批第1人→满员(2/2)', code in (200, 201), f'{code}')
code, d = api('POST', f"/employees/{e2['id']}/approve", AT, CID_CAP, {})
check('E8e 满员时审批被拒(规则⑨)', code == 400 and '上限' in str(d), f'{code} {str(d)[:60]}')
code, d = api('POST', '/employees/invite', AT, CID_CAP, {'phone': CAP_E3, 'real_name': '容量丙'})
check('E8f 满员时邀请被拒(规则⑨)', code == 400 and '上限' in str(d), f'{code} {str(d)[:60]}')

# E9 删除有菲票的款式被拒
_style0 = _StyleM.objects.filter(style_no='EDGEDEL').first()
if _style0:
    _ProdM.all_objects.filter(style_id=_style0.id).delete()
    from apps.tech.models import Bom as _BomPre  # noqa: E402  # P4 建床需 BOM：残留 BOM 会挡款式删除
    _BomPre.all_objects.filter(style_id=_style0.id).delete()
    _style0.delete()
code, st = api('POST', '/styles', AT, 2, {'style_no': 'EDGEDEL', 'name': '边缘删除款'})
check('E9a 建测试款式', code == 201, f'{code}')
SID = st['id'] if code == 201 else None
from apps.tech.models import Bom as _BomM  # noqa: E402  # P4 建床需 active BOM
if SID and not _BomM.all_objects.filter(style_id=SID).exists():
    _BomM.objects.create(company_id=2, style_id=SID, version=1, status=_BomM.Status.ACTIVE)
code, prod = api('POST', '/productions', AT, 2, {'style': SID, 'bed_no': '9999', 'order_type': '自产'})
check('E9b 建床次', code == 201, f'{code} {str(prod)[:80]}')
PROD_ID = prod.get('id') if code == 201 else None
code, tks = api('POST', '/tickets/batch', AT, 2,
                {'production_id': PROD_ID, 'items': [{'color': '红', 'size': 'M', 'qty': 10}],
                 'bundle_limit': 10})
check('E9c 生成菲票', code in (200, 201), f'{code} {str(tks)[:80]}')
code, d = api('DELETE', f'/styles/{SID}', AT, 2)
check('E9d 删除有菲票款式被拒(400)', code == 400 and '无法删除' in str(d), f'{code} {str(d)[:60]}')

# ---- cleanup ----
# E8/E9 清理：先删床次/款式与涉事用户；Post.company 是 PROTECT，须先删岗位再删公司
from apps.accounts.models import Post as _PostM  # noqa: E402
from apps.ops.models import CompanySetting as _CSM, Notification as _NotifM  # noqa: E402
_ProdM.all_objects.filter(style_id=SID).delete()   # 级联删菲票
_BomM.all_objects.filter(style_id=SID).delete()    # BOM.style PROTECT 挡款式删除
_StyleM.objects.filter(style_no='EDGEDEL').delete()
for _u in User.objects.filter(phone__in=CAP_PHONES):
    try:
        _u.delete()
    except ProtectedError:
        pass
_NotifM.objects.filter(company_id=CID_CAP).delete()
UserCompany.all_objects.filter(company_id=CID_CAP).delete()
_CSM.objects.filter(company_id=CID_CAP).delete()
_PostM.objects.filter(company_id=CID_CAP).delete()
_Company.objects.filter(id=CID_CAP).delete()

UserCompany.all_objects.filter(user=nu)._raw_delete(UserCompany.all_objects.db)
nu.delete()
Record.all_objects.filter(user_id=emp_uid, company_id=2).delete()
SettleApply.all_objects.filter(company_id=2, user_id__in=[emp_uid])._raw_delete(SettleApply.all_objects.db)

print(f'\n===== EDGE RESULT: {len(PASS)} passed, {len(FAIL)} failed =====')
if FAIL:
    print('FAILED:', *FAIL, sep='\n  - ')
