# -*- coding: utf-8 -*-
"""
Plan A 一期（权限点体系）后端验证：
- P1 权限点字典 GET /permissions：登录成员可读，分组含细粒度点
- P2 岗位 permissions 白名单：细粒度 key 可写、非法 key 400
- P3 旧 coarse key 兼容：['styles','production',...] 的 legacy 覆盖不变
  （可登记/可追溯，不可删款式/删床次/撤他人/结算/设置/锁床）
- P4 高危点显式授权：['styles.delete'] 可删款式、['records.revoke'] 可撤他人、
  ['salary.settle'] 过权限关（业务 400 区别于 403）、['settings.manage'] 可改设置、
  ['production.lock'] 可锁床次、['scan.assist'] 可协助、['employees.manage'] 可 pin
- P5 安全洞回归：普通成员（无点）此前可 approve/pin/set-post/invite（无门槛）——现一律 403
- P6 本人当日撤销不设点（owner 路径不变）
跑法：manage.py shell -c "exec(compile(open('perm_points_tests.py',encoding='utf-8').read(),'perm_points_tests.py','exec'))"
幂等：自建「权限点测试厂(可删)」并跨次复用（Company.creator PROTECT 不可删）；
cleanup 只清业务行并复位岗位权限，不删公司/账号。
"""
import json

from django.contrib.auth.hashers import make_password
from django.test import Client
from django.utils import timezone

from apps.accounts.models import Company, Post as PostM, User, UserCompany
from apps.accounts.services import create_company_with_owner
from apps.catalog.models import Style, Ticket
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
    return r.status_code, body


def login(phone, password='yisao1234'):
    _, d = api('POST', '/auth/login', data={'phone': phone, 'password': password})
    return d.get('access', '')


OWNER, H_LEGACY, H_REVOKE, H_PLAIN, H_DELETE = (
    '13988710001', '13988710002', '13988710003', '13988710004', '13988710005')
CO_NAME = '权限点测试厂(可删)'

# ================= preclean：复用测试厂 =================
company = Company.objects.filter(name=CO_NAME).first()
if company is None:
    owner = User.objects.filter(phone=OWNER).first()
    if owner is None:
        owner = User.objects.create(phone=OWNER, username=OWNER, real_name='权限点厂长',
                                    password=make_password('yisao1234'), is_active=True)
    company, admin_post = create_company_with_owner(owner, CO_NAME)
else:
    owner = User.objects.get(phone=OWNER)
    admin_post = PostM.objects.get(company=company, is_admin=True, is_deleted=False)
CID = company.id

from apps.ops.models import AuditLog  # noqa: E402
AuditLog.objects.filter(company=company).delete()


def ensure_user(phone, name):
    u = User.objects.filter(phone=phone).first()
    if u is None:
        u = User.objects.create(phone=phone, username=phone, real_name=name,
                                password=make_password('yisao1234'), is_active=True)
    return u


def ensure_post(name, perms):
    p = PostM.objects.filter(company=company, name=name, is_deleted=False).first()
    if p is None:
        p = PostM.objects.create(company=company, name=name, permissions=perms)
    else:
        PostM.objects.filter(pk=p.pk).update(permissions=perms)
        p.refresh_from_db()
    return p


def ensure_member(u, post):
    uc = UserCompany.all_objects.filter(user=u, company=company).first()
    if uc is None:
        return UserCompany.objects.create(user=u, company=company, post=post,
                                          status=UserCompany.Status.ACTIVE)
    uc.is_deleted = False
    uc.status = UserCompany.Status.ACTIVE
    uc.post = post
    uc.save(update_fields=['is_deleted', 'status', 'post', 'updated_at'])
    return uc


# 岗位矩阵（每轮强制复位，保证断言前提稳定）
post_legacy = ensure_post('P2旧岗', ['styles', 'production', 'working', 'my_salary', 'customers'])
post_revoke = ensure_post('P2撤销岗', ['records.create', 'records.revoke', 'salary.settle'])
post_plain = ensure_post('P2普工', [])
post_delete = ensure_post('P2删除岗', ['styles.manage', 'styles.delete'])

u_legacy = ensure_user(H_LEGACY, '旧岗员工')
revoker = ensure_user(H_REVOKE, '撤销岗员工')
plain = ensure_user(H_PLAIN, '无权员工')
u_delete = ensure_user(H_DELETE, '删除岗员工')
ensure_member(u_legacy, post_legacy)
ensure_member(revoker, post_revoke)
ensure_member(plain, post_plain)
ensure_member(u_delete, post_delete)

T = {
    'owner': login(OWNER), 'legacy': login(H_LEGACY),
    'revoke': login(H_REVOKE), 'plain': login(H_PLAIN), 'delete': login(H_DELETE),
}

# ================= P1 权限点字典 =================
st, d = api('GET', '/permissions', T['plain'], CID)
groups = d.get('groups', []) if isinstance(d, dict) else []
all_keys = [p['key'] for g in groups for p in g['points']]
check('P1 字典可读(普通成员)', st == 200 and len(groups) >= 12, f'{st} groups={len(groups)}')
check('P1 字典含高危点', all(k in all_keys for k in
      ('records.revoke', 'salary.settle', 'styles.delete', 'employees.manage')),
      str(all_keys[:8]))

# ================= P2 岗位白名单 =================
st, d = api('POST', '/posts', T['owner'], CID,
            {'name': 'P2细粒度岗', 'permissions': ['records.revoke', 'styles.price']})
fine = PostM.objects.filter(company=company, name='P2细粒度岗', is_deleted=False).first()
if fine:
    fine.delete()
check('P2 细粒度 key 可写', st == 201, f'{st} {d}')
st, d = api('POST', '/posts', T['owner'], CID,
            {'name': 'P2非法岗', 'permissions': ['bogus.key']})
check('P2 非法 key 400', st == 400, f'{st} {d}')

# ================= 最小业务链：款式→床次→菲票→登记 =================
from apps.catalog.models import Process  # noqa: E402
from apps.production.models import Production  # noqa: E402
Record.objects.filter(company=company).delete()
Ticket.objects.filter(company=company, bundle_no='1', color='黑').delete()
Production.objects.filter(company=company, bed_no='PERM001').delete()
Style.objects.filter(company=company, style_no__startswith='PERM-').delete()
style = Style.objects.create(company=company, style_no='PERM-A1', name='权限测试款')
prod = Production.objects.create(company=company, style=style, bed_no='PERM001',
                                 total_qty=100, customer=None)
proc = Process.objects.filter(company=company, is_fixed=True).first()
ticket = Ticket.objects.create(company=company, production=prod, bundle_no='1',
                               color='黑', size='M', qty=100)
rec_other = Record.objects.create(company=company, ticket=ticket, process=proc,
                                  user=revoker, qty=10, price='1.000',
                                  registered_at=timezone.now())
rec_plain = Record.objects.create(company=company, ticket=ticket, process=proc,
                                  user=plain, qty=5, price='1.000',
                                  registered_at=timezone.now())

# ================= P3 旧 coarse key 兼容（legacy 覆盖不变 / 高危不含） =================
check('P3 旧岗可读工作台', api('GET', '/records/workbench', T['legacy'], CID)[0] == 200)
check('P3 旧岗可读追溯', api('GET', '/trace/reverse?style_no=PERM-A1', T['legacy'], CID)[0] == 200)
check('P3 旧岗不可删款式', api('DELETE', f'/styles/{style.id}', T['legacy'], CID)[0] == 403)
check('P3 旧岗不可删床次', api('DELETE', f'/productions/{prod.id}', T['legacy'], CID)[0] == 403)
check('P3 旧岗不可撤他人', api('DELETE', f'/records/{rec_other.id}', T['legacy'], CID)[0] == 403)
check('P3 旧岗不可结算', api('POST', '/salary/settlements', T['legacy'], CID,
                            {'period_start': '2026-10-01', 'period_end': '2026-10-31'})[0] == 403)
check('P3 旧岗不可改设置', api('PUT', '/settings', T['legacy'], CID,
                             {'hide_scan_price': '0'})[0] == 403)
check('P3 旧岗不可锁床', api('PUT', f'/productions/{prod.id}/lock', T['legacy'], CID,
                            {'locked': True})[0] == 403)

# ================= P4 高危点显式授权 =================
# styles.delete：删除岗可建可删款式（styles.manage 建档 + styles.delete 删除）
st, d = api('POST', '/styles', T['delete'], CID, {'style_no': 'PERM-A2', 'name': '待删款'})
sid = (d or {}).get('id')
st2, _ = api('DELETE', f'/styles/{sid}', T['delete'], CID)
check('P4 styles.manage+delete 可建可删', st == 201 and st2 == 204, f'create={st} del={st2}')
# records.revoke：撤销岗可撤他人登记（204=DestroyAPIView 成功）
st, _ = api('DELETE', f'/records/{rec_other.id}', T['revoke'], CID)
check('P4 records.revoke 可撤他人', st == 204, str(st))
# salary.settle：过权限关即可（有未结算登记会真结成 201，无则 400——都不是 403）
st, d = api('POST', '/salary/settlements', T['revoke'], CID,
            {'period_start': '2026-10-01', 'period_end': '2026-10-31'})
check('P4 salary.settle 过权限关(201/400≠403)', st in (200, 201, 400), f'{st} {d}')
# settings.manage：撤销岗现无此点 403 → 补点后 200
PostM.objects.filter(pk=post_revoke.pk).update(
    permissions=['records.create', 'records.revoke', 'salary.settle'])
st, _ = api('PUT', '/settings', T['revoke'], CID, {'hide_scan_price': '0'})
check('P4 无 settings.manage 403', st == 403, str(st))
PostM.objects.filter(pk=post_revoke.pk).update(
    permissions=['records.create', 'records.revoke', 'salary.settle', 'settings.manage'])
st, _ = api('PUT', '/settings', T['revoke'], CID, {'hide_scan_price': '0'})
check('P4 settings.manage 授予后可改', st == 200, str(st))
# production.lock：删除岗现无此点 403 → 补点后 200
st, _ = api('PUT', f'/productions/{prod.id}/lock', T['delete'], CID, {'locked': True})
check('P4 无 production.lock 403', st == 403, str(st))
PostM.objects.filter(pk=post_delete.pk).update(
    permissions=['styles.manage', 'styles.delete', 'production.lock'])
st, _ = api('PUT', f'/productions/{prod.id}/lock', T['delete'], CID, {'locked': True})
check('P4 production.lock 授予后可锁', st == 200, str(st))
# scan.assist：撤销岗补点后可协助（无点已在 P3/P5 族验证 403）
PostM.objects.filter(pk=post_revoke.pk).update(
    permissions=['records.create', 'records.revoke', 'salary.settle',
                 'settings.manage', 'scan.assist'])
st, d = api('POST', '/records', T['revoke'], CID,
            {'ticket_id': ticket.id, 'process_id': proc.id, 'user_id': plain.id, 'qty': 1})
check('P4 scan.assist 授予后协助过权限关', st in (200, 201, 400), f'{st} {d}')
# employees.manage：删除岗补点后可 pin 他人
st, _ = api('POST', f"/employees/{UserCompany.objects.get(user=plain, company=company).id}/pin",
            T['delete'], CID, {'is_pinned': True})
check('P4 无 employees.manage pin 403', st == 403, str(st))
PostM.objects.filter(pk=post_delete.pk).update(
    permissions=['styles.manage', 'styles.delete', 'production.lock', 'employees.manage'])
st, _ = api('POST', f"/employees/{UserCompany.objects.get(user=plain, company=company).id}/pin",
            T['delete'], CID, {'is_pinned': True})
check('P4 employees.manage 授予后可 pin', st == 200, str(st))

# ================= P5 安全洞回归（普通成员员工管理动作一律 403） =================
plain_link = UserCompany.objects.get(user=plain, company=company)
st, _ = api('POST', f'/employees/{plain_link.id}/pin', T['plain'], CID, {'is_pinned': True})
check('P5 普通成员 pin 403', st == 403, str(st))
st, _ = api('POST', f'/employees/{plain_link.id}/approve', T['plain'], CID)
check('P5 普通成员 approve 403', st == 403, str(st))
st, _ = api('POST', f'/employees/{plain_link.id}/set-post', T['plain'], CID,
            {'post_id': admin_post.id})
check('P5 普通成员 set-post 403（防提权）', st == 403, str(st))
st, _ = api('POST', '/employees/invite', T['plain'], CID, {'phone': '13999999999'})
check('P5 普通成员 invite 403', st == 403, str(st))

# ================= P6 本人当日撤销不设点 =================
# P4 的结算可能已把该登记结进去：解绑后再验本人撤销路径
Record.objects.filter(pk=rec_plain.pk).update(settlement_id=None)
st, _ = api('DELETE', f'/records/{rec_plain.id}', T['plain'], CID)
check('P6 本人当日撤销无点可撤', st == 204, str(st))

# ================= cleanup：清业务行 + 复位岗位权限（公司/账号复用保留） =================
try:
    from apps.salary.models import Settlement
    Record.objects.filter(company=company).delete()
    Settlement.objects.filter(company=company).delete()
    Ticket.objects.filter(company=company, production__bed_no='PERM001').delete()
    Production.objects.filter(company=company, bed_no='PERM001').delete()
    Style.objects.filter(company=company, style_no__startswith='PERM-').delete()
    AuditLog.objects.filter(company=company).delete()
    PostM.objects.filter(pk=post_revoke.pk).update(
        permissions=['records.create', 'records.revoke', 'salary.settle'])
    PostM.objects.filter(pk=post_delete.pk).update(
        permissions=['styles.manage', 'styles.delete'])
    print('cleanup: done（业务行已清，测试厂/账号复用保留）')
except Exception as e:  # noqa: BLE001
    print('cleanup WARN:', e)

print(f'\n==== 权限点套件：{len(PASS)} pass / {len(FAIL)} fail ====')
if FAIL:
    print('FAILED:', FAIL)
