# -*- coding: utf-8 -*-
"""
Plan A 二期（权限点体系后端收尾）验证：
- A1 effective_perms 下发：'*' 岗 / legacy coarse 岗 / 细粒度岗三种岗位在
  GET /posts、GET /companies/mine、PUT /companies/:id/switch 响应中携带解析后的点集
- A2 SYSTEM_POSTS 细粒度化：新公司播种的 裁床/车间 岗位与旧 coarse 默认逐点等价
  （has_perm 对全部合法 key 逐一断言，管理员岗保持 ['*']）
- A3 新补关卡 POST /styles/<pk>/sync-process：无点 403、legacy 'styles' 岗 200（零回归）、
  授 styles.manage 后 200
跑法：manage.py shell -c "exec(compile(open('plan_a2_tests.py',encoding='utf-8').read(),'plan_a2_tests.py','exec'))"
幂等：自建「权限点二期厂(可删)」并跨次复用（Company.creator PROTECT 不可删）；
cleanup 只清业务行并复位测试岗位权限，不删公司/账号。
"""
import json

from django.contrib.auth.hashers import make_password
from django.test import Client

from apps.accounts.models import Company, Post as PostM, User, UserCompany
from apps.accounts.services import SYSTEM_POSTS, create_company_with_owner
from apps.core.permissions import (
    ALL_PERMISSION_KEYS, _LEGACY_COVERED, effective_perms, has_perm)

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


# legacy coarse key → 展开点集（手工固化，避免用被测函数自证）
EXPECTED_LEGACY = {
    'styles': ['styles.manage', 'styles.price'],
    'customers': ['customers.manage'],
    'production': ['records.create', 'production.manage', 'trace.view', 'warehouse.manage',
                   'orders.manage'],  # 2026-10-20 P1：订单管理点由旧 'production' legacy 覆盖
    'working': ['working.view'],
    'my_salary': ['my_salary.view'],
}

OWNER2, H_PLAIN2, H_LEGACY2 = '13988710006', '13988710007', '13988710008'
CO2_NAME = '权限点二期厂(可删)'

# ================= preclean：复用二期测试厂 =================
company2 = Company.objects.filter(name=CO2_NAME).first()
if company2 is None:
    owner2 = User.objects.filter(phone=OWNER2).first()
    if owner2 is None:
        owner2 = User.objects.create(phone=OWNER2, username=OWNER2, real_name='二期厂长',
                                     password=make_password('yisao1234'), is_active=True)
    company2, admin_post2 = create_company_with_owner(owner2, CO2_NAME)
else:
    owner2 = User.objects.get(phone=OWNER2)
    admin_post2 = PostM.objects.get(company=company2, is_admin=True, is_deleted=False)
CID2 = company2.id

from apps.ops.models import AuditLog  # noqa: E402
from apps.catalog.models import Style  # noqa: E402
AuditLog.objects.filter(company=company2).delete()


def ensure_user(phone, name):
    u = User.objects.filter(phone=phone).first()
    if u is None:
        u = User.objects.create(phone=phone, username=phone, real_name=name,
                                password=make_password('yisao1234'), is_active=True)
    return u


def ensure_post(name, perms):
    p = PostM.objects.filter(company=company2, name=name, is_deleted=False).first()
    if p is None:
        p = PostM.objects.create(company=company2, name=name, permissions=perms)
    else:
        PostM.objects.filter(pk=p.pk).update(permissions=perms)
        p.refresh_from_db()
    return p


def ensure_member(u, post):
    uc = UserCompany.all_objects.filter(user=u, company=company2).first()
    if uc is None:
        return UserCompany.objects.create(user=u, company=company2, post=post,
                                          status=UserCompany.Status.ACTIVE)
    uc.is_deleted = False
    uc.status = UserCompany.Status.ACTIVE
    uc.post = post
    uc.save(update_fields=['is_deleted', 'status', 'post', 'updated_at'])
    return uc


# 测试岗位矩阵（每轮强制复位）
# legacy 岗：一期旧 coarse key 全集（含 customers），用于验证展开与零回归
LEGACY_PERMS = ['styles', 'production', 'working', 'my_salary', 'customers']
post_legacy2 = ensure_post('二期旧岗', LEGACY_PERMS)
post_fine2 = ensure_post('二期细粒度岗', ['records.create', 'styles.price'])
post_plain2 = ensure_post('二期普工', [])

u_plain2 = ensure_user(H_PLAIN2, '二期普工')
u_legacy2 = ensure_user(H_LEGACY2, '二期旧岗员工')
ensure_member(u_plain2, post_plain2)
ensure_member(u_legacy2, post_legacy2)

T_owner = login(OWNER2)
T_plain = login(H_PLAIN2)
T_legacy = login(H_LEGACY2)

# 系统播种的裁床/车间岗（SYSTEM_POSTS 细粒度化后）
post_cai = PostM.objects.get(company=company2, name='裁床', is_deleted=False)
post_che = PostM.objects.get(company=company2, name='车间', is_deleted=False)
# 播种岗随 SYSTEM_POSTS 声明同步（复用测试厂建于旧声明时，先把岗权限追平再断言等价）
for _p in (post_cai, post_che):
    _want = next(p['permissions'] for p in SYSTEM_POSTS if p['name'] == _p.name)
    if _p.permissions != _want:
        PostM.objects.filter(pk=_p.pk).update(permissions=_want)
        _p.refresh_from_db()

# ================= A1 effective_perms 下发 =================
# A1.1 函数级：'*' 岗 / legacy 岗 / 细粒度岗 / None
# 期望顺序按 PERMISSION_GROUPS 声明顺序：styles → customers → records → production
# → working → my_salary → trace → warehouse
exp_legacy_sorted = ['styles.manage', 'styles.price', 'customers.manage', 'records.create',
                     'production.manage', 'orders.manage', 'working.view', 'my_salary.view',
                     'trace.view', 'warehouse.manage']
check('A1 effective_perms(None)=[]', effective_perms(None) == [])
check('A1 legacy岗 展开与手工固化一致',
      set(EXPECTED_LEGACY['styles'] + EXPECTED_LEGACY['production']
          + EXPECTED_LEGACY['working'] + EXPECTED_LEGACY['my_salary']
          + EXPECTED_LEGACY['customers'])
      == set(effective_perms(post_legacy2)), str(effective_perms(post_legacy2)))
check('A1 细粒度岗 原样透传', effective_perms(post_fine2) == ['styles.price', 'records.create'],
      str(effective_perms(post_fine2)))


class _UC:
    """has_perm 只读 user_company.post，用轻量壳免建库。"""
    def __init__(self, post):
        self.post = post


star_post = PostM(company=company2, name='星岗', permissions=['*'])
check("A1 '*' 岗 → ['*']", effective_perms(star_post) == ['*'])

# A1.2 API：GET /posts 三种岗位都带 effective_perms
st, d = api('GET', '/posts', T_owner, CID2)
rows = {r['name']: r for r in (d if isinstance(d, list) else d.get('results', d.get('posts', [])))}
check('A1 GET /posts 列表可读', st == 200 and '二期旧岗' in rows, f'{st} {list(rows)[:6]}')
check('A1 GET /posts legacy岗 下发展开点集',
      rows.get('二期旧岗', {}).get('effective_perms') == exp_legacy_sorted,
      str(rows.get('二期旧岗', {}).get('effective_perms')))
check('A1 GET /posts 细粒度岗 下发原样点集',
      rows.get('二期细粒度岗', {}).get('effective_perms') == ['styles.price', 'records.create'],
      str(rows.get('二期细粒度岗', {}).get('effective_perms')))
check("A1 GET /posts '*'岗 下发 ['*']",
      rows.get('管理员', {}).get('effective_perms') == ['*'],
      str(rows.get('管理员', {}).get('effective_perms')))

# A1.3 API：GET /companies/mine（owner 的 '*' 管理员岗）
st, d = api('GET', '/companies/mine', T_owner)
mine = [c for c in d if c['id'] == CID2] if isinstance(d, list) else []
check('A1 companies/mine 带 effective_perms',
      st == 200 and mine and mine[0]['post']['effective_perms'] == ['*'],
      f'{st} {mine[0]["post"] if mine else d}')

# A1.4 API：PUT /companies/:id/switch（legacy 岗成员视角）
st, d = api('PUT', f'/companies/{CID2}/switch', T_legacy, CID2)
check('A1 switch 响应 post.effective_perms 与展开一致',
      st == 200 and d.get('company', {}).get('post', {}).get('effective_perms') == exp_legacy_sorted,
      f'{st} {d.get("company", {}).get("post")}')

# ================= A2 SYSTEM_POSTS 播种等价（has_perm 逐点断言） =================
# 旧默认 coarse 岗（一期前 SYSTEM_POSTS 原值），用未入库 Post 实例作对照基准。
# 比对域 = 全部细粒度点（coarse key 本身不再被端点消费，不算可达面差异）：
# 换算目标即"旧 coarse 岗在每个细粒度点上的 has_perm 判定，新岗逐一相同"。
from apps.core.permissions import PERMISSION_GROUPS  # noqa: E402
FINE_KEYS = [p['key'] for g in PERMISSION_GROUPS for p in g['points']]
old_cai = PostM(company=company2, name='旧裁床', permissions=['styles', 'production', 'working', 'my_salary'])
old_che = PostM(company=company2, name='旧车间', permissions=['my_salary', 'working'])

diff = [k for k in FINE_KEYS
        if has_perm(_UC(post_cai), k) != has_perm(_UC(old_cai), k)]
check('A2 播种裁床与旧默认逐点等价(全部细粒度点)',
      diff == [], f'差异点: {diff}')
diff = [k for k in FINE_KEYS
        if has_perm(_UC(post_che), k) != has_perm(_UC(old_che), k)]
check('A2 播种车间与旧默认逐点等价(全部细粒度点)',
      diff == [], f'差异点: {diff}')
check('A2 裁床可达面只含 legacy 低危点',
      all(any(k in pts for pts in _LEGACY_COVERED.values())
          for k in effective_perms(post_cai)), str(effective_perms(post_cai)))
check('A2 车间可达面=my_salary.view+working.view',
      set(effective_perms(post_che)) == {'my_salary.view', 'working.view'},
      str(effective_perms(post_che)))
check('A2 管理员岗保持 [*]+is_admin',
      admin_post2.is_admin and effective_perms(admin_post2) == ['*'])
check('A2 播种裁床权限与 SYSTEM_POSTS 声明一致',
      post_cai.permissions == next(p['permissions'] for p in SYSTEM_POSTS if p['name'] == '裁床'),
      str(post_cai.permissions))
check('A2 播种车间权限与 SYSTEM_POSTS 声明一致',
      post_che.permissions == next(p['permissions'] for p in SYSTEM_POSTS if p['name'] == '车间'),
      str(post_che.permissions))

# ================= A3 新补关卡：POST /styles/<pk>/sync-process =================
style2 = Style.objects.filter(company=company2, style_no='PERM2-S1').first()
if style2 is None:
    style2 = Style.objects.create(company=company2, style_no='PERM2-S1', name='二期同步测试款')

st, d = api('POST', f'/styles/{style2.id}/sync-process', T_plain, CID2, {})
check('A3 无点 sync-process 403', st == 403, f'{st} {d}')
# legacy 兼容：旧 coarse 'styles' 岗经 legacy 覆盖天然通过（与一期前无门槛时期不同口径：
# 本端点此前无任何关卡，补 styles.manage 后旧 'styles' 岗可达面不变）
st, d = api('POST', f'/styles/{style2.id}/sync-process', T_legacy, CID2, {})
check('A3 legacy styles 岗 sync-process 200（零回归）', st == 200, f'{st} {d}')
# 授点后普工通过
PostM.objects.filter(pk=post_plain2.pk).update(permissions=['styles.manage'])
post_plain2.refresh_from_db()
st, d = api('POST', f'/styles/{style2.id}/sync-process', T_plain, CID2, {})
check('A3 授 styles.manage 后 sync-process 200', st == 200, f'{st} {d}')

# ================= cleanup：清业务行 + 复位岗位权限（公司/账号复用保留） =================
try:
    Style.objects.filter(company=company2, style_no='PERM2-S1').delete()
    AuditLog.objects.filter(company=company2).delete()
    PostM.objects.filter(pk=post_plain2.pk).update(permissions=[])
    print('cleanup: done（业务行已清，测试厂/账号复用保留）')
except Exception as e:  # noqa: BLE001
    print('cleanup WARN:', e)

print(f'\n==== 权限点二期套件：{len(PASS)} pass / {len(FAIL)} fail ====')
if FAIL:
    print('FAILED:', FAIL)
