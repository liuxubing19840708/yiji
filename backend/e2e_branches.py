# -*- coding: utf-8 -*-
"""
守卫/参数分支补充套件（e2e_edge 的续篇，分支覆盖 60%→75% 目标的「必补+值得补+顺手补」三档）：
- B1 注销密码二次校验 + 微信登录（mock code2session）
- B2 成员生命周期守卫：重复邀请/非待审核审批拒绝/非在职离职/非离职复职/最后管理员
- B3 加入公司 join/switch 守卫
- B4 记录撤销守卫：本人当日/当月限制/已结算不可撤/非管理员撤他人
- B5 结算守卫：期间倒置/已撤销再撤/不存在/已发放不可撤
- B6 删除守卫：菲票带登记/工序被款式用/工序有登记/系统岗位/在职成员岗位/裁完禁登记/指定工序
- B7 容量规则⑨第三入口：复职撞上限
- B9 列表筛选参数逐个扫描 + 设置项校验 + 上传校验
跑法：manage.py shell -c "exec(compile(open('e2e_branches.py',encoding='utf-8').read(),'e2e_branches.py','exec'))"
幂等：自带 preclean/cleanup；不动演示库任何既有成员的状态（守卫全部在 raise 路径上）。
"""
import json
import io
from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, override_settings
from django.utils import timezone

from apps.accounts.models import Post as PostM, SMSCode, User, UserCompany
from apps.catalog.models import Process, Style, StyleProcess, Ticket
from apps.ops.models import CompanySetting, Notification
from apps.production.models import Production
from apps.records.models import Record
from apps.salary.models import SettleApply, Settlement

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


def sms_token(phone, code):
    """桩验证码直接入库 → login-sms 换 token（员工账号无密码）。"""
    SMSCode.objects.create(phone=phone, code=code,
                           expires_at=timezone.now() + timedelta(minutes=10))
    _, d = api('POST', '/auth/login-sms', data={'phone': phone, 'code': code})
    return d.get('access', '')


def norm(d):
    """列表响应解包（分页/数组二态）。"""
    return d.get('results') if isinstance(d, dict) and isinstance(d.get('results'), list) \
        else (d if isinstance(d, list) else [])


# ---- 常量 ----
EMP_PHONE, FRESH_PHONE, PEND_PHONE, CAP_PHONE = (
    '13877772525', '13877772222', '13877772626', '13877772323')
BR_PHONES = [EMP_PHONE, FRESH_PHONE, PEND_PHONE, CAP_PHONE]
SNO = 'BRANCHA'
AT = login()
# ---- 动态测试厂（原种子库 company_id=2 已随演示库清理；仅改 setup，断言原样）----
from apps.accounts.models import Company as _CompanyBr  # noqa: E402
from apps.accounts.services import create_company_with_owner as _mkco  # noqa: E402
from django.contrib.auth.hashers import make_password as _mkpwd  # noqa: E402
CO_NAME = '分支守卫测试厂(可删)'
company_br = _CompanyBr.objects.filter(name=CO_NAME).first()
if company_br is None:
    _owner = User.objects.get(phone='13800000001')
    company_br, _admin_post = _mkco(_owner, CO_NAME)
CID = company_br.id
CO_NO = company_br.company_no  # B3 加入公司用动态厂号（原硬编码 100001 属种子库）
# B2-0 前提：李翠花在职关系（原种子库自带，动态厂补建，幂等）
_lq_user = User.objects.filter(phone='13800000003').first()
if _lq_user is None:
    _lq_user = User.objects.create(phone='13800000003', username='13800000003',
                                   real_name='李翠花',
                                   password=_mkpwd('yisao1234'), is_active=True)
_lq_sys_post = PostM.objects.filter(company=company_br, is_admin=False,
                                    is_deleted=False).first() \
    or PostM.objects.create(company=company_br, name='BRANCH基础岗', is_admin=False)
_lq_uc = UserCompany.all_objects.filter(user=_lq_user, company=company_br).first()
if _lq_uc is None:
    UserCompany.objects.create(user=_lq_user, company=company_br,
                               post=_lq_sys_post, status=UserCompany.Status.ACTIVE)
else:
    _lq_uc.is_deleted = False
    _lq_uc.status = UserCompany.Status.ACTIVE
    _lq_uc.post = _lq_sys_post
    _lq_uc.save(update_fields=['is_deleted', 'status', 'post', 'updated_at'])
today = timezone.localdate()
month_start = today.replace(day=1).isoformat()
today_s = today.isoformat()
prev_month = (today - timedelta(days=35)).isoformat()

# ---- preclean（历史残留自清扫）----
Settlement.all_objects.filter(company_id=CID, user__phone__in=BR_PHONES).delete()
SettleApply.all_objects.filter(company_id=CID, user__phone__in=BR_PHONES).delete()
Record.all_objects.filter(company_id=CID, user__phone__in=BR_PHONES).delete()
Record.all_objects.filter(company_id=CID, ticket__production__style__style_no=SNO).delete()
Ticket.all_objects.filter(company_id=CID, production__style__style_no=SNO).delete()
Production.all_objects.filter(style__style_no=SNO).delete()
StyleProcess.objects.filter(style__style_no=SNO).delete()
from apps.tech.models import Bom as _BomPC  # noqa: E402
_BomPC.all_objects.filter(style__style_no=SNO).delete()  # BOM.style PROTECT 挡 Style 删除
Style.all_objects.filter(style_no=SNO).delete()
Process.all_objects.filter(name__startswith='分支').delete()
for p in BR_PHONES:
    u = User.objects.filter(phone=p).first()
    if u:
        UserCompany.all_objects.filter(user=u).delete()
        SMSCode.objects.filter(phone=p).delete()
        Notification.objects.filter(company_id=CID, message__contains=p).delete()
        u.delete()
PostM.all_objects.filter(company_id=CID, name__startswith='BRANCH').delete()
User.objects.filter(openid__startswith='ovk_branch').delete()
# 历史中断残留的容量实验公司（B7 每次新建，崩溃时可能来不及清）
from apps.accounts.models import Company as _CompanyM0  # noqa: E402
for cid_old in _CompanyM0.all_objects.filter(name='分支容量公司').values_list('id', flat=True):
    UserCompany.all_objects.filter(company_id=cid_old).delete()
    Notification.objects.filter(company_id=cid_old).delete()
    CompanySetting.all_objects.filter(company_id=cid_old).delete()
    PostM.all_objects.filter(company_id=cid_old).delete()
    _CompanyM0.all_objects.filter(pk=cid_old).delete()

# 设置开关快照（B4 用，退出恢复）
_, st_map = api('GET', '/settings', AT)

# ================= B1 注销二次校验 + 微信登录 =================
code, d = api('POST', '/auth/deactivate', AT, CID, {'password': 'wrong-pass-123'})
check('B1a 注销密码错误被拒(400,不真注销)', code == 400 and '密码校验失败' in str(d), f'{code} {str(d)[:60]}')

code, d = api('POST', '/auth/wechat-login', data={})
check('B1b 微信登录缺code(400)', code == 400 and '缺少 code' in str(d), f'{code} {str(d)[:60]}')
code, d = api('POST', '/auth/wechat-login', data={'code': 'js_branch'})
check('B1c 未配置appid(400)', code == 400 and '未配置' in str(d), f'{code} {str(d)[:60]}')


def wx_mock(payload):
    m = MagicMock()
    m.return_value.__enter__.return_value.read.return_value = json.dumps(payload).encode('utf-8')
    return m


with override_settings(WECHAT_APPID='wx-branch-test', WECHAT_SECRET='sec-branch-test'), \
        patch('urllib.request.urlopen', wx_mock({'openid': 'ovk_branch_w1', 'session_key': 'k'})):
    code, d = api('POST', '/auth/wechat-login', data={'code': 'js_branch'})
    check('B1d 微信登录新用户建号发token', code == 200 and d.get('access') and
          User.objects.filter(openid='ovk_branch_w1').count() == 1, f'{code} {str(d)[:80]}')
    code, d = api('POST', '/auth/wechat-login', data={'code': 'js_branch2'})
    check('B1e 同openid复登不重复建号', code == 200 and
          User.objects.filter(openid='ovk_branch_w1').count() == 1, f'{code} {str(d)[:80]}')
with override_settings(WECHAT_APPID='wx-branch-test', WECHAT_SECRET='sec-branch-test'), \
        patch('urllib.request.urlopen', wx_mock({'errmsg': 'invalid code'})):
    code, d = api('POST', '/auth/wechat-login', data={'code': 'js_branch'})
    check('B1f code2session无openid(400)', code == 400 and '微信登录失败' in str(d), f'{code} {str(d)[:60]}')

# ================= B2 成员生命周期守卫（全在 raise 路径，不改状态） =================
lq = UserCompany.objects.filter(company_id=CID, user__phone='13800000003',
                                status=UserCompany.Status.ACTIVE).first()
check('B2-0 李翠花在职关系存在(前置)', lq is not None)
code, d = api('POST', '/employees/invite', AT, CID, {'phone': '13800000003', 'real_name': '李翠花'})
check('B2a 邀请在职成员被拒(已是成员)', code == 400, f'{code} {str(d)[:60]}')
code, d = api('POST', f'/employees/{lq.id}/approve', AT, CID, {})
check('B2b 审批在职成员被拒(非待审核)', code == 400, f'{code} {str(d)[:60]}')
code, d = api('POST', f'/employees/{lq.id}/reject', AT, CID, {})
check('B2c 拒绝在职成员被拒(非待审核)', code == 400, f'{code} {str(d)[:60]}')
code, d = api('POST', f'/employees/{lq.id}/rehire', AT, CID, {})
check('B2d 复职在职成员被拒(非离职)', code == 400, f'{code} {str(d)[:60]}')

code, d = api('POST', '/employees/invite', AT, CID, {'phone': PEND_PHONE, 'real_name': '待审员工'})
pend_link = UserCompany.objects.filter(company_id=CID, user__phone=PEND_PHONE).first()
check('B2e 邀请新员工挂起', code in (200, 201) and pend_link and pend_link.status == 0,
      f'{code} {str(d)[:60]}')
code, d = api('POST', f'/employees/{pend_link.id}/resign', AT, CID, {'force': 1})
check('B2f 离职待审核成员被拒(仅在职可离职)', code == 400, f'{code} {str(d)[:60]}')

admin_link = UserCompany.objects.filter(company_id=CID, user__phone='13800000001',
                                        status=UserCompany.Status.ACTIVE).first()
code, d = api('POST', f'/employees/{admin_link.id}/resign', AT, CID, {'force': 1})
check('B2g 最后一名管理员离职被拒', code == 400 and '管理员' in str(d), f'{code} {str(d)[:60]}')

# ================= B3 join / switch 守卫 =================
FT = sms_token(FRESH_PHONE, '135711')
check('B3-0 新用户短信登录(无公司关系)', bool(FT))
code, d = api('POST', '/companies/join', FT, 0, {'company_no': CO_NO})
check('B3a 新用户申请加入公司(201)', code == 201, f'{code} {str(d)[:60]}')
code, d = api('POST', '/companies/join', FT, 0, {'company_no': CO_NO})
check('B3b 重复申请加入被拒', code == 400, f'{code} {str(d)[:60]}')
code, d = api('PUT', '/companies/2/switch', FT, 0)
check('B3c 切换未加入公司被拒(404)', code == 404, f'{code} {str(d)[:60]}')

# ================= B4 记录撤销守卫 =================
# 链路：款式→工序×2→床次→菲票；EMP 在职登记
code, st = api('POST', '/styles', AT, CID, {'style_no': SNO, 'name': '分支测试款'})
check('B4-0a 建测试款式', code == 201, f'{code}')
SID = st['id']
# P4 开裁缺 BOM 拦截（2026-10-24）：建床需款式有 active BOM，测试款补齐
from apps.tech.models import Bom  # noqa: E402
if not Bom.all_objects.filter(style_id=SID).exists():
    Bom.objects.create(company_id=CID, style_id=SID, version=1, status=Bom.Status.ACTIVE)

code, p1 = api('POST', '/processes', AT, CID, {'name': '分支工序A', 'default_price': '0.5'})
code, p2 = api('POST', '/processes', AT, CID, {'name': '分支工序B', 'default_price': '0.6'})
code, p3 = api('POST', '/processes', AT, CID, {'name': '分支工序C', 'default_price': '0.7'})
P1, P2, P3 = p1['id'], p2['id'], p3['id']
code, _ = api('PUT', f'/styles/{SID}', AT, CID,
              {'process_items': [{'process_id': P1, 'price': '0.5', 'sort': 1},
                                 {'process_id': P2, 'price': '0.6', 'sort': 2},
                                 {'process_id': P3, 'price': '0.7', 'sort': 3}]})
code, prod = api('POST', '/productions', AT, CID, {'style': SID, 'bed_no': 'BR-B1', 'order_type': '自产'})
check('B4-0b 建床次', code == 201, f'{code} {str(prod)[:60]}')
PROD_ID = prod['id']
code, tks = api('POST', '/tickets/batch', AT, CID,
                {'production_id': PROD_ID, 'items': [{'color': '红', 'size': 'M', 'qty': 20}],
                 'bundle_limit': 20})
check('B4-0c 生成菲票', code in (200, 201), f'{code} {str(tks)[:60]}')
T1 = Ticket.objects.filter(production_id=PROD_ID).order_by('id').first().id

code, d = api('POST', '/employees/invite', AT, CID, {'phone': EMP_PHONE, 'real_name': '分支员工'})
code, _ = api('POST', f"/employees/{UserCompany.objects.get(company_id=CID, user__phone=EMP_PHONE).id}/approve",
              AT, CID, {})
emp_link = UserCompany.objects.get(company_id=CID, user__phone=EMP_PHONE)
# 已知 bug 绕行：records 撤销视图直接取 post.is_admin，无岗位员工会 500 → 先绑临时岗位
# （原绑 '*'：Plan A 权限点化后 '*' 恒过 records.revoke，B4a 的 403 前提被破坏；
#   员工语义只需登记权，改为最小点集，属 setup 修正，断言不动）
code, bp = api('POST', '/posts', AT, CID,
               {'name': 'BRANCH临时岗', 'permissions': ['records.create']})
branch_post = PostM.objects.get(pk=bp['id'])
emp_link.post = branch_post
emp_link.save(update_fields=['post'])
EMPTOK = sms_token(EMP_PHONE, '246813')
check('B4-0d 测试员工已在职+登录', emp_link.status == 1 and bool(EMPTOK))

code, recA = api('POST', '/records', AT, CID, {'ticket_id': T1, 'process_id': P1, 'qty': 1})
check('B4-0e 管理员登记落库', code in (200, 201), f'{code} {str(recA)[:60]}')
code, rec1 = api('POST', '/records', EMPTOK, CID, {'ticket_id': T1, 'process_id': P2, 'qty': 2})
check('B4-0f 员工登记落库', code in (200, 201), f'{code} {str(rec1)[:60]}')

code, d = api('DELETE', f"/records/{recA['id']}", EMPTOK, CID)
check('B4a 员工撤他人登记被拒(403)', code == 403, f'{code} {str(d)[:60]}')
code, d = api('DELETE', f"/records/{rec1['id']}", EMPTOK, CID)
check('B4b 员工撤本人当日登记(成功)', code in (200, 204), f'{code} {str(d)[:60]}')

code, rec2 = api('POST', '/records', EMPTOK, CID, {'ticket_id': T1, 'process_id': P2, 'qty': 2})
Record.objects.filter(pk=rec2['id']).update(registered_at=timezone.localtime() - timedelta(days=1))
code, d = api('DELETE', f"/records/{rec2['id']}", EMPTOK, CID)
check('B4c 撤非当日本人登记被拒', code == 400 and '当日' in str(d), f'{code} {str(d)[:60]}')

# 注：员工撤任何非当日登记先撞「仅本人当日」守卫，当月限制只有管理员自撤可达
api('PUT', '/settings', AT, CID, {'revoke_current_month_only': '1'})
Record.objects.filter(pk=recA['id']).update(registered_at=timezone.localtime() - timedelta(days=35))
code, d = api('DELETE', f"/records/{recA['id']}", AT, CID)
check('B4d 当月限制开启时管理员撤跨月登记被拒', code == 400 and '当月' in str(d), f'{code} {str(d)[:60]}')
api('PUT', '/settings', AT, CID, {'revoke_current_month_only': str(st_map.get('revoke_current_month_only', '0'))})
Record.objects.filter(pk=rec2['id']).update(registered_at=timezone.localtime())
Record.objects.filter(pk=recA['id']).update(registered_at=timezone.localtime())

code, s1 = api('POST', '/salary/settlements', AT, CID,
               {'period_start': month_start, 'period_end': today_s, 'user_id': emp_link.user_id})
check('B4-0e 发起结算锁定登记(201)', code == 201 and s1.get('id'), f'{code} {str(s1)[:80]}')
code, d = api('DELETE', f"/records/{rec2['id']}", EMPTOK, CID)
check('B4e 撤已结算登记被拒', code == 400 and '已结算' in str(d), f'{code} {str(d)[:60]}')

# ================= B5 结算守卫 =================
code, d = api('POST', '/salary/settlements', AT, CID,
              {'period_start': today_s, 'period_end': month_start, 'user_id': emp_link.user_id})
check('B5a 结算期间倒置被拒(400)', code == 400, f'{code} {str(d)[:60]}')
code, d = api('POST', f"/salary/settlements/{s1['id']}/revoke", AT, CID)
check('B5b 撤销结算单解锁登记(200)', code in (200, 201), f'{code} {str(d)[:60]}')
code, d = api('POST', f"/salary/settlements/{s1['id']}/revoke", AT, CID)
check('B5c 重复撤销已撤销结算单被拒', code == 400 and '已撤销' in str(d), f'{code} {str(d)[:60]}')
code, d = api('POST', '/salary/settlements/99999999/revoke', AT, CID)
check('B5d 撤销不存在结算单(404)', code == 404, f'{code} {str(d)[:60]}')

# 已发放不可撤：员工申请→审核→发放（发放自动生成含 rec2 的结算单）→ 再结算再撤 → 400
code, ap1 = api('POST', '/salary/settle-applies', EMPTOK, CID,
                {'period_start': month_start, 'period_end': today_s})
check('B5-0a 员工发起结算申请(201)', code == 201 and ap1.get('id'), f'{code} {str(ap1)[:80]}')
code, _ = api('PUT', f"/salary/settle-applies/{ap1['id']}/review", AT, CID, {'action': 'approve'})
code, _ = api('PUT', f"/salary/settle-applies/{ap1['id']}/pay", AT, CID, {})
st1 = SettleApply.objects.get(pk=ap1['id']).status
check('B5-0b 申请已审核发放(status=2)', st1 == 2, f'status={st1}')
# rec2 已结算仍占据 (T1,P2,员工) 组合（「同票同工序同人仅一条」），换 P1 建新登记
code, rec3 = api('POST', '/records', EMPTOK, CID, {'ticket_id': T1, 'process_id': P1, 'qty': 3})
code, s2 = api('POST', '/salary/settlements', AT, CID,
               {'period_start': month_start, 'period_end': today_s, 'user_id': emp_link.user_id})
check('B5-0c 再结算(201)', code == 201 and s2.get('id'), f'{code} {str(s2)[:80]}')
code, d = api('POST', f"/salary/settlements/{s2['id']}/revoke", AT, CID)
check('B5e 该期间工资已发放不可撤', code == 400 and '已发放' in str(d), f'{code} {str(d)[:60]}')

# ================= B6 删除守卫 / 裁完 / 指定工序 =================
code, d = api('DELETE', f'/tickets/{T1}', AT, CID)
check('B6a 删除有登记菲票被拒', code == 400 and '登记' in str(d), f'{code} {str(d)[:60]}')
code, d = api('DELETE', f'/processes/{P3}', AT, CID)
check('B6b 删除被款式使用工序被拒', code == 400, f'{code} {str(d)[:60]}')
code, _ = api('PUT', f'/styles/{SID}', AT, CID,
              {'process_items': [{'process_id': P2, 'price': '0.6', 'sort': 1}]})
code, d = api('DELETE', f'/processes/{P3}', AT, CID)
check('B6c 解绑后删除无登记工序(200)', code in (200, 204), f'{code} {str(d)[:60]}')
code, d = api('DELETE', f'/processes/{P2}', AT, CID)
check('B6d 删除有登记工序被拒', code == 400, f'{code} {str(d)[:60]}')

code, d = api('DELETE', f"/posts/{branch_post.id}", AT, CID)
check('B6e 删除仍有在职成员岗位被拒', code == 400, f'{code} {str(d)[:60]}')
sys_post = PostM.objects.filter(company_id=CID, is_system=True).first()
code, d = api('DELETE', f'/posts/{sys_post.id}', AT, CID)
check('B6f 删除系统内置岗位被拒', code == 400, f'{code} {str(d)[:60]}')

code, d = api('PUT', f"/employees/{emp_link.id}/assigned-processes", AT, CID, {'process_ids': [P2]})
check('B6g 指定工序变更(200)', code == 200, f'{code} {str(d)[:60]}')

# 新菲票；2026-10-18 裁完=裁剪完成标记，裁完后登记/加扎均不再拦截
api('POST', '/tickets/batch', AT, CID,
    {'production_id': PROD_ID, 'items': [{'color': '蓝', 'size': 'M', 'qty': 10}], 'bundle_limit': 10})
T2 = Ticket.objects.filter(production_id=PROD_ID).order_by('-id').first().id
code, _ = api('PUT', f'/productions/{PROD_ID}/cut-done', AT, CID, {'is_cut_done': True})
code, d = api('POST', '/records', EMPTOK, CID, {'ticket_id': T2, 'process_id': P2, 'qty': 1})
check('B6h 裁完后登记放行(新语义)', code in (200, 201), f'{code} {str(d)[:60]}')
api('PUT', f'/productions/{PROD_ID}/cut-done', AT, CID, {'is_cut_done': False})

# ================= B7 容量规则⑨第三入口：复职撞上限 =================
code, cc = api('POST', '/companies/create', AT, 0, {'name': '分支容量公司'})
CID_CAP = cc.get('company', {}).get('id')
from apps.accounts.models import Company as _CompanyM  # noqa: E402
_CompanyM.objects.filter(id=CID_CAP).update(capacity=2)
code, d = api('POST', '/employees/invite', AT, CID_CAP, {'phone': CAP_PHONE, 'real_name': '容量复职'})
code, _ = api('POST', f"/employees/{UserCompany.objects.get(company_id=CID_CAP, user__phone=CAP_PHONE).id}/approve",
              AT, CID_CAP, {})
cap_link = UserCompany.objects.get(company_id=CID_CAP, user__phone=CAP_PHONE)
code, _ = api('POST', f'/employees/{cap_link.id}/resign', AT, CID_CAP, {'force': 1})
_CompanyM.objects.filter(id=CID_CAP).update(capacity=1)
code, d = api('POST', f'/employees/{cap_link.id}/rehire', AT, CID_CAP, {})
check('B7 满员复职被拒(已达上限)', code == 400 and '上限' in str(d), f'{code} {str(d)[:60]}')

# ================= B9 列表筛选参数逐个扫描 + 设置/上传校验 =================
ok, bad = 0, []


def f200(name, path):
    global ok
    code, d = api('GET', path, AT, CID)
    if code == 200:
        ok += 1
    else:
        bad.append(f'{name}:{code}')


f200('rec-user', f'/records?user_id={emp_link.user_id}')
f200('rec-ticket', f'/records?ticket_id={T1}')
f200('rec-process', f'/records?process_id={P2}')
f200('rec-dfrom', f'/records?date_from={month_start}')
f200('rec-dto', f'/records?date_to={today_s}')
f200('rec-unsettled', '/records?settlement=unsettled')
f200('prod-bed', '/productions?bed_no=BR-B1')
f200('prod-style', f'/productions?style_id={SID}')
f200('prod-otype', '/productions?order_type=自产')
f200('prod-locked', '/productions?is_locked=0')
f200('prod-cut', '/productions?is_cut_done=0')
f200('prod-kw', '/productions?keyword=BR')
f200('cut-none', f'/productions/{PROD_ID}/cut-detail?state=none')
f200('cut-part', f'/productions/{PROD_ID}/cut-detail?state=partial')
f200('cut-done', f'/productions/{PROD_ID}/cut-detail?state=done&keyword=1')
f200('proc-kw', '/processes?keyword=分支')
f200('proc-fixed0', '/processes?is_fixed=0')
f200('proc-fixed1', '/processes?is_fixed=1')
f200('style-cust', '/styles?customer_id=0')
f200('cust-kw', '/customers?keyword=演示')
f200('cust-group', '/customers?group=0')
f200('audit-date', f'/audit-logs?date={today_s}')
f200('audit-df', f'/audit-logs?date_from={month_start}')
f200('audit-dt', f'/audit-logs?date_to={today_s}')
f200('audit-kw', '/audit-logs?keyword=branch')
f200('notif-unread', '/notifications?is_read=0')
f200('notif-read', '/notifications?is_read=1')
f200('notif-code', '/notifications?code=join_request')
f200('sal-today', '/salary/company?quick=today')
f200('sal-yest', '/salary/company?quick=yesterday')
f200('sal-uid', f'/salary/company?user_id={emp_link.user_id}&date_from={month_start}&date_to={today_s}')
f200('apply-uid', f'/salary/settle-applies?user_id={emp_link.user_id}')
f200('apply-df', f'/salary/settle-applies?date_from={month_start}')
f200('apply-dt', f'/salary/settle-applies?date_to={today_s}')
f200('apply-st', '/salary/settle-applies?status=0')
check(f'B9a 筛选参数扫描 35/35 全 200', ok == 35, f'ok={ok} bad={bad}')

code, d = api('POST', '/notifications/99999999/read', AT, CID)
check('B9b 标记不存在通知已读(404)', code == 404, f'{code}')
code, d = api('PUT', '/settings', AT, CID, 'not-a-dict')
check('B9c 设置非对象体被拒(400)', code == 400, f'{code} {str(d)[:60]}')
code, d = api('PUT', '/settings', AT, CID, {'bogus_branch_key': '1'})
check('B9d 未知设置项被拒(400)', code == 400, f'{code} {str(d)[:60]}')

c = Client()
H = {'HTTP_AUTHORIZATION': f'Bearer {AT}', 'HTTP_X_COMPANY_ID': str(CID)}
r = c.post('/api/uploads', data={}, **H)
check('B9e 上传缺file字段(400)', r.status_code == 400, f'{r.status_code}')
r = c.post('/api/uploads', data={'file': SimpleUploadedFile('a.txt', b'x')}, **H)
check('B9f 上传非图片格式被拒(400)', r.status_code == 400, f'{r.status_code}')
r = c.post('/api/uploads', data={'file': SimpleUploadedFile('big.png', bytes(5 * 1024 * 1024 + 1))}, **H)
check('B9g 上传超5MB被拒(400)', r.status_code == 400, f'{r.status_code}')

# ================= cleanup =================
Settlement.all_objects.filter(company_id=CID, user__phone__in=BR_PHONES).delete()
SettleApply.all_objects.filter(company_id=CID, user__phone__in=BR_PHONES).delete()
Record.all_objects.filter(company_id=CID, user__phone__in=BR_PHONES).delete()
Record.all_objects.filter(company_id=CID, ticket__production__style__style_no=SNO).delete()
Ticket.all_objects.filter(company_id=CID, production__style__style_no=SNO).delete()
Production.all_objects.filter(style__style_no=SNO).delete()
StyleProcess.objects.filter(style__style_no=SNO).delete()
from apps.tech.models import Bom as _BomPC  # noqa: E402
_BomPC.all_objects.filter(style__style_no=SNO).delete()  # BOM.style PROTECT 挡 Style 删除
Style.all_objects.filter(style_no=SNO).delete()
Process.all_objects.filter(name__startswith='分支').delete()
UserCompany.all_objects.filter(company_id=CID_CAP).delete()
Notification.objects.filter(company_id=CID_CAP).delete()
CompanySetting.all_objects.filter(company_id=CID_CAP).delete()
PostM.all_objects.filter(company_id=CID_CAP).delete()
_CompanyM.objects.filter(id=CID_CAP).delete()
for p in BR_PHONES:
    u = User.objects.filter(phone=p).first()
    if u:
        UserCompany.all_objects.filter(user=u).delete()
        SMSCode.objects.filter(phone=p).delete()
        Notification.objects.filter(company_id=CID, message__contains=p).delete()
        u.delete()
PostM.all_objects.filter(company_id=CID, name__startswith='BRANCH').delete()
User.objects.filter(openid__startswith='ovk_branch').delete()

print(f'\n===== BRANCH RESULT: {len(PASS)} passed, {len(FAIL)} failed =====')
if FAIL:
    print('FAILED:', *FAIL, sep='\n  - ')
