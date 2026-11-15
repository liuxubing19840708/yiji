# -*- coding: utf-8 -*-
"""
账号运维补缺套件（2026-10-23）：覆盖 apps/ops、apps/accounts、apps/catalog、apps/warehouse
在 coverage_missing.txt 中的未覆盖分支（目标 ≥90 分支）：

  ops        SettingView GET/PUT（含未知键/非 dict 体/无权限）、AuditLogListView 全筛选
             （type/date/date_from/date_to/keyword/scope=mine/无 logs.view 403）、
             Notification 列表筛选（is_read/code）+ 单条已读/全部已读/404、
             SettingSerializer 校验双分支、CompanySetting.set_values/get_bool。
  accounts   send_sms_code（频控/日限）、verify_sms_code（错码/消费/复用）、
             normalize_fixed_salary、get_wx_access_token（缓存/网络错/无 token/成功）、
             WechatLogin（缺 code/未配置/网络错/code 无效/成功）、Me GET/PATCH/PUT、
             改密（错旧密码/成功/短密码）、注销（无密码/错密码/最后管理员/成功匿名化+审计）、
             companies mine/create/join/switch、wxacode（未配置/网络错/JSON 错/格式异常/PNG）、
             员工列表全 tab+keyword+岗位筛选、invite（错手机号/带岗/重复/在职/软删复活）、
             approve（403/成功/非待审/容量满）、reject、resign（最后管理员/未结算/成功）、
             rehire（成功/非离职/容量满）、set-post/set-group/pin、set_plan（未知套餐/降档拒/成功+审计）、
             assigned-processes（非法工序/去重/免重复审计）、岗位 CRUD+copy+删除三拦、
             序列化器分支（权限点白名单/组编号/指定工序/邀请手机号与岗位/改密旧密码）。
  catalog    款式创建全错误分支（重名/配置非列表/项非 dict/缺工序/重复工序/工序不存在/单价非法）、
             images 校验（非数组/>9 张/空/首张同步）、列表 keyword/customer 筛选+统计行、
             详情/改名冲突/仅 process_items 部分更新/工艺字段部分更新/带票禁删/删除、
             copy 顺延、sync-process（需确认/确认）、工序列表筛选+重名+删除三拦、
             process-selector（管理员/员工指定+脏值兜底）、工序模板全校验+改名冲突+软删重建、
             客户分组/客户 CRUD+重名+自动编号、菲票列表全筛选、编菲全错误分支+拆扎+扎号冲突+
             默认裁完+审计、打印（403/空表/错 id/缺票/去重成功）、菲票删除三态。
  warehouse  批次 CRUD+重名+血缘/领料禁删+日期分支、血缘 PUT（404/缺批/重复）、
             正反向追溯+菲票/床次终端、采购单（自动单号日期/明细/状态筛选/确认终态四拦）、
             入库单（自动单号/批次回填锚点/明细必填/状态四拦）、领料单（自动单号/校验/确认血缘累加）。

数据集全部落在专用测试厂「账号运维厂(可删)」，前缀 OPAF / 1997766xxxxx；
每轮开头硬清本厂+本前缀（先子后父），绝不触碰真实厂（company id=86）。

跑法：manage.py shell -c "exec(compile(open('test_ops_accounts.py',encoding='utf-8').read(),'test_ops_accounts.py','exec'))"
幂等：专用厂整清重建；临时厂/批量用户/验证码随 preclean 清扫，可重复执行。
"""
import json
import time
import urllib.error
from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.test import Client
from django.test.utils import override_settings
from django.utils import timezone

from apps.accounts.models import Company, Post as PostM, SMSCode, User, UserCompany
from apps.accounts.services import (_wx_token_cache, create_company_with_owner,
                                    get_wx_access_token, normalize_fixed_salary,
                                    send_sms_code, verify_sms_code)
from apps.catalog.models import (Customer, CustomerGroup, Process, ProcessTemplate,
                                 Style, StyleProcess, Ticket)
from apps.catalog.serializers import ProcessTemplateSerializer
from apps.catalog.views import (_validate_template_processes, CustomerListCreateView,
                                save_style_processes)
from apps.ops.models import AuditLog, CompanySetting, Notification
from apps.ops.services import audit, notify
from apps.production.models import Production
from apps.records.models import Record
from apps.warehouse.models import (IssueItem, Lot, MaterialIssue, ProductionLotLink,
                                   PurchaseOrder, StockIn)
from rest_framework.exceptions import ValidationError

PASS, FAIL = [], []


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    print(('PASS' if cond else 'FAIL'), name, ('' if cond else f'| 预期外: {detail}'))


def api(method, path, token=None, cid=None, data=None):
    c = Client()
    extra = {}
    if token:
        extra['HTTP_AUTHORIZATION'] = f'Bearer {token}'
    if cid:
        extra['HTTP_X_COMPANY_ID'] = str(cid)
    fn = getattr(c, method.lower())
    r = fn(f'/api{path}',
           data=json.dumps(data) if data is not None else None,
           content_type='application/json', **extra)
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


# ================= 常量 =================
CO = '账号运维厂(可删)'
CO_TEMP = 'OPAF临时厂'
OWNER = '19977660001'
W1, W2 = '19977660002', '19977660003'
P_U1, P_U2, P_U3, P_R = '19977660155', '19977660156', '19977660157', '19977660158'
P_JOIN, P_INV, P_DEACT, P_SMSLOG, P_SMSVERIFY, P_SMSREG = (
    '19977660144', '19977660166', '19977660133', '19977660111', '19977660122', '19977660177')
P_SMSLOG2 = '19977660112'
P_NOREAL = '19977660188'
TEST_PHONES = [OWNER, W1, W2, P_U1, P_U2, P_U3, P_R, P_JOIN, P_INV, P_DEACT,
               P_SMSLOG, P_SMSLOG2, P_SMSVERIFY, P_SMSREG, P_NOREAL]
today = timezone.localdate()
DF = today.isoformat()


def main():
    PASS.clear()
    FAIL.clear()

    # ================= preclean：硬清本厂 + 本前缀（先子后父），临时厂整体删 =================
    for c in Company.all_objects.filter(name=CO_TEMP):
        c.delete()  # 级联清临时厂（上一轮 /companies/create 残留）

    company = Company.all_objects.filter(name=CO).first()
    if company is None:
        owner0 = ensure_user(OWNER, 'OPAF厂长')
        company, _ = create_company_with_owner(owner0, CO)
    CID = company.id

    # 先子后父整清业务数据
    AuditLog.objects.filter(company=company).delete()
    Notification.objects.filter(company=company).delete()
    CompanySetting.objects.filter(company=company).delete()
    Record.objects.filter(company=company).delete()
    Ticket.all_objects.filter(company=company).delete()
    StyleProcess.all_objects.filter(style__company=company).delete()
    IssueItem.objects.filter(issue__company=company).delete()
    MaterialIssue.all_objects.filter(company=company).delete()
    ProductionLotLink.all_objects.filter(company=company).delete()
    StockIn.all_objects.filter(company=company).delete()
    PurchaseOrder.all_objects.filter(company=company).delete()
    Lot.all_objects.filter(company=company).delete()
    Production.all_objects.filter(company=company).delete()
    Style.all_objects.filter(company=company).delete()
    ProcessTemplate.all_objects.filter(company=company).delete()
    Process.all_objects.filter(company=company).delete()
    Customer.all_objects.filter(company=company).delete()
    CustomerGroup.all_objects.filter(company=company).delete()
    SMSCode.objects.filter(phone__in=TEST_PHONES).delete()
    # 成员：保留管理员岗链接（厂长），其余（含 post=None 的待审链接）硬删
    admin_post_ids = list(PostM.all_objects.filter(
        company=company, is_admin=True).values_list('id', flat=True))
    UserCompany.all_objects.filter(company=company).exclude(
        post_id__in=admin_post_ids).delete()
    PostM.all_objects.filter(company=company, is_admin=False).delete()
    # 批量压容量用户（上一轮 set_plan 残留）
    User.objects.filter(username__startswith='opaf_bulk_').delete()
    # 短信注册用户（保证 login-sms 自动注册分支每轮可触发）
    User.objects.filter(username=f'u_{P_SMSREG}').delete()
    # 微信自动注册用户（保证 wechat-login 新 openid 建户分支每轮可触发）
    User.objects.filter(openid__startswith='opaf_new_openid').delete()
    CompanySetting.ensure_defaults(company)

    owner = ensure_user(OWNER, 'OPAF厂长')
    w1u = ensure_user(W1, 'OPAF员工一')
    w2u = ensure_user(W2, 'OPAF员工二')

    worker_post = PostM.objects.create(company=company, name='OPAF普工', permissions=[])
    UserCompany.objects.create(user=w1u, company=company, post=worker_post,
                               status=UserCompany.Status.ACTIVE,
                               hired_date=today, group_no=1)
    UserCompany.objects.create(user=w2u, company=company, post=worker_post,
                               status=UserCompany.Status.ACTIVE,
                               hired_date=today, group_no=1)
    # 基础工序（preclean 已整清，重建供款式/选择器用例）
    p_cut = Process.objects.create(company=company, name='裁剪', sort=1)
    p_make = Process.objects.create(company=company, name='车缝', sort=2)
    p_fixed = Process.objects.create(company=company, name='整件', is_fixed=True, sort=3)

    TA = login(OWNER)
    TW = login(W1)

    # ================================================================
    print('\n===== A. ops 系统设置 =====')
    st, d = api('GET', '/settings', TA, CID)
    check('A1 GET settings 200 且含全部默认键',
          st == 200 and 'auto_cut_done' in d and d.get('auto_cut_done') == '0', f'{st} {d}')
    st, d = api('GET', '/settings', TW, CID)
    check('A2 员工可读设置（展示开关）', st == 200 and 'hide_scan_price' in d, f'{st}')

    st, d = api('PUT', '/settings', TA, CID,
                {'hide_scan_price': '1', 'filter_zero_tickets': '0'})
    check('A3 PUT settings 200 且值落库', st == 200
          and d.get('hide_scan_price') == '1' and d.get('filter_zero_tickets') == '0',
          f'{st} {d}')
    check('A4 修改设置落操作日志',
          AuditLog.objects.filter(company=company, message__contains='修改系统设置').exists())
    check('A5 get_bool 开=1 / 关=0',
          CompanySetting.get_bool(company, 'hide_scan_price') is True
          and CompanySetting.get_bool(company, 'auto_cut_done') is False)
    try:
        CompanySetting.set_values(company, {'no_such_key': '1'})
        check('A5b set_values 未知键抛 ValueError', False, '未抛')
    except ValueError:
        check('A5b set_values 未知键抛 ValueError', True)
    api('PUT', '/settings', TA, CID, {'filter_zero_tickets': '1'})  # 复位（update_or_create 更新支路）

    st, d = api('PUT', '/settings', TA, CID, {'no_such_key': '1'})
    check('A6 未知设置项 → 400', st == 400, f'{st} {d}')
    st, d = api('PUT', '/settings', TA, CID, ['not', 'a', 'dict'])
    check('A7 请求体非对象 → 400', st == 400, f'{st} {d}')
    st, d = api('PUT', '/settings', TW, CID, {'hide_scan_price': '1'})
    check('A8 无 settings.manage → 403', st == 403, f'{st} {d}')

    print('\n===== B. ops 操作日志 =====')
    audit(company, AuditLog.Type.PRICE, operator=owner, target_user=w1u,
          style_no='OPAF-S1', message='OPAF改价日志（员工一）')
    audit(company, AuditLog.Type.REVOKE, operator=owner,
          bed_no='OPAF-B1', bundle_no='3', message='OPAF撤销日志')

    st, d = api('GET', '/audit-logs', TA, CID)
    check('B1 日志全量列表 200', st == 200 and d.get('count', 0) >= 2, f'{st} {d.get("count")}')
    st, d = api('GET', '/audit-logs?type=price', TA, CID)
    check('B2 type 筛选', st == 200 and all(r['type'] == 'price' for r in d.get('results', []))
          and len(d.get('results', [])) >= 1, f'{st}')
    st, d = api('GET', f'/audit-logs?date={DF}', TA, CID)
    check('B3 date 单日筛选', st == 200 and d.get('count', 0) >= 2, f'{st} {d.get("count")}')
    st, d = api('GET', f'/audit-logs?date_from={DF}&date_to={DF}', TA, CID)
    check('B4 date_from/date_to 区间筛选', st == 200 and d.get('count', 0) >= 2, f'{st}')
    st, d = api('GET', '/audit-logs?keyword=OPAF-S1', TA, CID)
    check('B5 keyword 命中款号', st == 200 and d.get('count', 0) == 1, f'{st} {d.get("count")}')
    st, d = api('GET', '/audit-logs?scope=mine', TW, CID)
    check('B6 scope=mine 员工视角仅本人相关', st == 200 and d.get('count', 0) == 1
          and d['results'][0].get('target_user_name') == 'OPAF员工一', f'{st} {d.get("count")}')
    st, d = api('GET', '/audit-logs', TW, CID)
    check('B7 无 logs.view 全量 → 403', st == 403, f'{st} {d}')

    print('\n===== C. ops 站内通知 =====')
    notify(company, [w1u.id], 'invited', '入职邀请', 'OPAF通知一')
    notify(company, [w1u.id], 'resigned', '离职通知', 'OPAF通知二')
    notify(company, [owner.id], 'join_request', '新成员申请', 'OPAF通知三')

    st, d = api('GET', '/notifications', TW, CID)
    check('C1 通知列表 200（本人）', st == 200 and d.get('count', 0) == 2, f'{st} {d.get("count")}')
    st, d = api('GET', '/notifications?is_read=0', TW, CID)
    check('C2 is_read=0 筛选', st == 200 and d.get('count', 0) == 2, f'{st}')
    st, d = api('GET', '/notifications?is_read=1', TW, CID)
    check('C3 is_read=1 空集', st == 200 and d.get('count', 0) == 0, f'{st}')
    st, d = api('GET', '/notifications?code=invited', TW, CID)
    check('C4 code 筛选', st == 200 and d.get('count', 0) == 1, f'{st}')
    nid = d['results'][0]['id']
    st, d = api('POST', f'/notifications/{nid}/read', TW, CID)
    check('C5 单条已读', st == 200 and d.get('message') == '已标记为已读', f'{st} {d}')
    st, d = api('GET', '/notifications?is_read=1', TW, CID)
    check('C6 已读生效', st == 200 and d.get('count', 0) == 1, f'{st}')
    st, d = api('POST', '/notifications/999999999/read', TW, CID)
    check('C7 已读不存在 → 404', st == 404, f'{st}')
    st, d = api('POST', '/notifications/read-all', TW, CID)
    check('C8 全部已读', st == 200 and d.get('message') == '已全部标记为已读', f'{st} {d}')
    st, d = api('GET', '/notifications?is_read=0', TW, CID)
    check('C9 read-all 后未读清零', st == 200 and d.get('count', 0) == 0, f'{st}')

    # ================================================================
    print('\n===== D. accounts 短信服务 =====')
    send_sms_code(P_SMSLOG)                      # 成功支路
    check('D1 发送验证码落库', SMSCode.objects.filter(phone=P_SMSLOG).count() == 1)
    try:
        send_sms_code(P_SMSLOG)
        check('D2 60s 重发拦截', False, '未抛 ValidationError')
    except ValidationError:
        check('D2 60s 重发拦截', True)
    now = timezone.now()
    SMSCode.objects.bulk_create([SMSCode(phone=P_SMSLOG, code='000000',
                                         expires_at=now + timedelta(minutes=5))
                                 for _ in range(19)])
    # 回拨 61s 绕过频控，让日限分支（而非 60s 频控分支）命中
    SMSCode.objects.filter(phone=P_SMSLOG).update(created_at=now - timedelta(seconds=61))
    try:
        send_sms_code(P_SMSLOG)
        check('D3 日限 20 条拦截', False, '未抛 ValidationError')
    except ValidationError:
        check('D3 日限 20 条拦截', True)

    SMSCode.objects.create(phone=P_SMSVERIFY, code='654321',
                           expires_at=now + timedelta(minutes=5))
    try:
        verify_sms_code(P_SMSVERIFY, '000000')
        check('D4 错码拒绝', False, '未抛 ValidationError')
    except ValidationError:
        check('D4 错码拒绝', True)
    verify_sms_code(P_SMSVERIFY, '654321')       # 消费支路
    check('D5 正确验证码消费（used_at 落库）',
          SMSCode.objects.get(phone=P_SMSVERIFY, code='654321').used_at is not None)
    try:
        verify_sms_code(P_SMSVERIFY, '654321')
        check('D6 已消费验证码复用拒绝', False, '未抛 ValidationError')
    except ValidationError:
        check('D6 已消费验证码复用拒绝', True)

    check('D7 normalize_fixed_salary(None)=None', normalize_fixed_salary(None) is None)
    check('D8 normalize_fixed_salary 四舍五入 q2',
          str(normalize_fixed_salary('12.345')) == '12.35', str(normalize_fixed_salary('12.345')))

    print('\n===== D+. 微信凭证服务 get_wx_access_token =====')
    old_cache = dict(_wx_token_cache)
    try:
        _wx_token_cache.update(token='opaf_cached', expires_at=time.time() + 999)
        check('D9 缓存命中直接返回', get_wx_access_token() == 'opaf_cached')
        _wx_token_cache.update(token='', expires_at=0.0)
        with mock.patch('urllib.request.urlopen', side_effect=urllib.error.URLError('boom')):
            try:
                get_wx_access_token()
                check('D10 网络错误包装 ValidationError', False, '未抛')
            except ValidationError as e:
                check('D10 网络错误包装 ValidationError', '网络错误' in str(e.detail), str(e))
        with mock.patch('urllib.request.urlopen',
                        mock.mock_open(read_data=b'{"errmsg":"invalid appid"}')):
            try:
                get_wx_access_token()
                check('D11 响应缺 access_token 报错', False, '未抛')
            except ValidationError as e:
                check('D11 响应缺 access_token 报错', 'invalid appid' in str(e.detail), str(e))
        with mock.patch('urllib.request.urlopen',
                        mock.mock_open(read_data=b'{"access_token":"tok123","expires_in":7200}')):
            check('D12 成功取 token', get_wx_access_token() == 'tok123')
            check('D13 token 进缓存', _wx_token_cache['token'] == 'tok123')
    finally:
        _wx_token_cache.clear()
        _wx_token_cache.update(old_cache)

    # ================================================================
    print('\n===== E. accounts 认证端点 =====')
    st, d = api('POST', '/auth/sms-code', data={'phone': 'abc'})
    check('E1 非法手机号 → 400', st == 400, f'{st} {d}')
    st, d = api('POST', '/auth/sms-code', data={'phone': P_SMSLOG2})
    check('E2 合法手机号发送 → 200', st == 200, f'{st} {d}')
    st, d = api('POST', '/auth/sms-code', data={'phone': P_SMSLOG2})
    check('E3 60s 内重发 → 400', st == 400, f'{st}')
    st, d = api('POST', '/auth/sms-code', data={'phone': P_SMSLOG})
    check('E4 超日限 → 400', st == 400, f'{st}')

    SMSCode.objects.create(phone=P_SMSREG, code='246810',
                           expires_at=now + timedelta(minutes=5))
    st, d = api('POST', '/auth/login-sms', data={'phone': P_SMSREG, 'code': '000000'})
    check('E5 短信登录错码 → 400', st == 400, f'{st} {d}')
    st, d = api('POST', '/auth/login-sms', data={'phone': P_SMSREG, 'code': '246810'})
    check('E6 短信登录成功并自动注册', st == 200 and d.get('user', {}).get('phone') == P_SMSREG
          and d.get('access'), f'{st} {str(d)[:120]}')
    check('E7 自动注册用户落库', User.objects.filter(username=f'u_{P_SMSREG}').exists())

    st, d = api('POST', '/auth/wechat-login', data={})
    check('E8 缺 code → 400', st == 400, f'{st} {d}')
    st, d = api('POST', '/auth/wechat-login', data={'code': 'opaf'})
    check('E9 未配置微信参数 → 400', st == 400, f'{st} {d}')
    wx_user = User.objects.filter(openid='opaf_wx_openid').first()
    if wx_user is None:
        wx_user = User.objects.create(username='opaf_wx', openid='opaf_wx_openid',
                                      real_name='OPAF微信用户')
    with override_settings(WECHAT_APPID='opaf_appid', WECHAT_SECRET='opaf_secret'):
        with mock.patch('urllib.request.urlopen', side_effect=urllib.error.URLError('net')):
            st, d = api('POST', '/auth/wechat-login', data={'code': 'opaf'})
            check('E10 微信登录网络错 → 400', st == 400, f'{st} {d}')
        with mock.patch('urllib.request.urlopen',
                        mock.mock_open(read_data=b'{"errmsg":"invalid code"}')):
            st, d = api('POST', '/auth/wechat-login', data={'code': 'opaf'})
            check('E11 code 无效 → 400', st == 400 and 'invalid code' in json.dumps(d, ensure_ascii=False),
                  f'{st} {d}')
        with mock.patch('urllib.request.urlopen',
                        mock.mock_open(read_data=b'{"openid":"opaf_wx_openid"}')):
            st, d = api('POST', '/auth/wechat-login', data={'code': 'opaf'})
            check('E12 微信登录成功（存量 openid）', st == 200 and d.get('access')
                  and d.get('user', {}).get('id') == wx_user.id, f'{st} {str(d)[:120]}')
        with mock.patch('urllib.request.urlopen',
                        mock.mock_open(read_data=b'{"openid":"opaf_new_openid_2"}')):
            st, d = api('POST', '/auth/wechat-login', data={'code': 'opaf'})
            check('E12b 新 openid 自动注册微信用户', st == 200 and d.get('access')
                  and User.objects.filter(openid='opaf_new_openid_2').exists(), f'{st} {str(d)[:120]}')

    st, d = api('GET', '/auth/me', TA)
    check('E13 GET /auth/me', st == 200 and d.get('id') == owner.id, f'{st}')
    st, d = api('PATCH', '/auth/me', TA, data={'real_name': 'OPAF厂长改'})
    check('E14 PATCH 资料', st == 200 and d.get('real_name') == 'OPAF厂长改', f'{st} {d}')
    st, d = api('PUT', '/auth/me', TA, data={'real_name': 'OPAF厂长', 'gender': 1})
    check('E15 PUT 资料（wx.request 兼容）', st == 200 and d.get('real_name') == 'OPAF厂长'
          and d.get('gender') == 1, f'{st} {d}')

    st, d = api('PUT', '/auth/password', TA, data={'old_password': 'wrong', 'new_password': 'opaf456789'})
    check('E16 改密旧密码错 → 400', st == 400, f'{st} {d}')
    st, d = api('PUT', '/auth/password', TA, data={'old_password': 'yisao1234', 'new_password': '123'})
    check('E17 新密码过短 → 400', st == 400, f'{st} {d}')
    st, d = api('PUT', '/auth/password', TA, data={'old_password': 'yisao1234', 'new_password': 'opaf456789'})
    check('E18 改密成功', st == 200, f'{st} {d}')
    check('E19 新密码可登录', len(login(OWNER, 'opaf456789')) > 20)
    st, d = api('PUT', '/auth/password', TA, data={'old_password': 'opaf456789', 'new_password': 'yisao1234'})
    check('E20 改回原密码', st == 200, f'{st}')

    print('\n===== E+. 注销账号 =====')
    du = ensure_user(P_DEACT, 'OPAF待注销')
    UserCompany.objects.create(user=du, company=company, post=worker_post,
                               status=UserCompany.Status.ACTIVE)
    TD = login(P_DEACT)
    st, d = api('POST', '/auth/deactivate', TD, data={})
    check('E21 注销缺密码 → 400', st == 400, f'{st} {d}')
    st, d = api('POST', '/auth/deactivate', TD, data={'password': 'wrong'})
    check('E22 注销密码错 → 400', st == 400, f'{st} {d}')
    st, d = api('POST', '/auth/deactivate', TA, CID, data={'password': 'yisao1234'})
    check('E23 最后管理员禁注销 → 400', st == 400, f'{st} {d}')
    st, d = api('POST', '/auth/deactivate', TD, data={'password': 'yisao1234'})
    check('E24 注销成功', st == 200, f'{st} {d}')
    du.refresh_from_db()
    check('E25 匿名化（is_active=False / 手机号 del_ 前缀）',
          du.is_active is False and (du.phone or '').startswith('del_')
          and du.real_name == '已注销用户', f'{du.phone} {du.real_name}')
    check('E26 注销落审计日志',
          AuditLog.objects.filter(company=company, message__contains='注销账号').exists())
    UserCompany.all_objects.filter(user=du).delete()  # 清掉注销用户链接，员工计数回到 3

    # ================================================================
    print('\n===== F. companies mine/create/join/switch/wxacode =====')
    st, d = api('GET', '/companies/mine', TA)
    check('F1 mine 含本公司且 member_count≥3', st == 200 and any(
        c.get('id') == CID and c.get('member_count', 0) >= 3 for c in d) , f'{st} {str(d)[:200]}')
    mine_row = next((c for c in d if c.get('id') == CID), {})
    check('F2 mine 项含 status/post/plan_display',
          mine_row.get('status') == UserCompany.Status.ACTIVE
          and (mine_row.get('post') or {}).get('is_admin') is True
          and bool(mine_row.get('plan_display')), str(mine_row)[:200])

    st, d = api('POST', '/companies/create', TA, data={'name': '  '})
    check('F3 空公司名 → 400', st == 400, f'{st} {d}')
    st, d = api('POST', '/companies/create', TA, data={'name': CO_TEMP, 'city': '测试市'})
    check('F4 创建公司 → 201 且带 company_no', st == 201
          and (d.get('company') or {}).get('company_no'), f'{st} {str(d)[:200]}')

    ju = ensure_user(P_JOIN, 'OPAF申请人')
    TJ = login(P_JOIN)
    st, d = api('POST', '/companies/join', TJ, data={'company_no': 'NOPE99'})
    check('F5 公司编号不存在 → 400', st == 400, f'{st} {d}')
    st, d = api('POST', '/companies/join', TJ, data={'company_no': company.company_no})
    check('F6 申请加入 → 201', st == 201, f'{st} {str(d)[:160]}')
    check('F7 申请落管理员通知',
          Notification.objects.filter(company=company, code='join_request').exists())
    st, d = api('POST', '/companies/join', TJ, data={'company_no': company.company_no})
    check('F8 重复申请 → 400', st == 400, f'{st} {d}')
    UserCompany.all_objects.filter(user=ju, company=company).delete()  # 清申请链接，pending 计数回到 3

    st, d = api('POST', f'/companies/{CID}/switch', TA)
    check('F9 切换公司 200', st == 200 and d.get('message') == '已切换', f'{st} {d}')
    st, d = api('POST', '/companies/999999999/switch', TA)
    check('F10 非在职成员切换 → 404', st == 404, f'{st} {d}')

    st, d = api('GET', '/companies/wxacode', TA, CID)
    check('F11 未配置微信参数 wxacode → 400', st == 400, f'{st} {d}')

    class FakeResp:
        def __init__(self, body, headers):
            self._body, self.headers = body, headers
        def read(self):
            return self._body
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    old_cache = dict(_wx_token_cache)
    try:
        _wx_token_cache.update(token='opaf_tok', expires_at=time.time() + 999)
        with override_settings(WECHAT_APPID='opaf_appid', WECHAT_SECRET='opaf_secret'):
            with mock.patch('urllib.request.urlopen', side_effect=urllib.error.URLError('net')):
                st, d = api('GET', '/companies/wxacode', TA, CID)
                check('F12 wxacode 网络错 → 400', st == 400, f'{st} {d}')
            with mock.patch('urllib.request.urlopen', return_value=FakeResp(
                    b'not-json', {'Content-Type': 'application/json'})):
                st, d = api('GET', '/companies/wxacode', TA, CID)
                check('F13 wxacode 响应格式异常 → 400', st == 400, f'{st} {d}')
            with mock.patch('urllib.request.urlopen', return_value=FakeResp(
                    b'{"errcode":40001,"errmsg":"invalid"}', {'Content-Type': 'application/json'})):
                st, d = api('GET', '/companies/wxacode', TA, CID)
                check('F14 wxacode 业务错 → 400', st == 400, f'{st} {d}')
            with mock.patch('urllib.request.urlopen', return_value=FakeResp(
                    b'PNGDATA', {'Content-Type': 'image/png'})):
                st, d = api('GET', '/companies/wxacode', TA, CID)
                check('F15 wxacode 成功返回 PNG', st == 200, f'{st}')
    finally:
        _wx_token_cache.clear()
        _wx_token_cache.update(old_cache)

    # ================================================================
    print('\n===== G. 员工列表与管理 =====')
    pu1, pu2, pu3, ru = (ensure_user(P_U1, 'OPAF待审一'), ensure_user(P_U2, 'OPAF待审二'),
                         ensure_user(P_U3, 'OPAF待审三'), ensure_user(P_R, 'OPAF离职一'))
    L1 = UserCompany.objects.create(user=pu1, company=company, status=UserCompany.Status.PENDING)
    L2 = UserCompany.objects.create(user=pu2, company=company, status=UserCompany.Status.PENDING)
    L3 = UserCompany.objects.create(user=pu3, company=company, status=UserCompany.Status.PENDING)
    LR = UserCompany.objects.create(user=ru, company=company, post=worker_post,
                                    status=UserCompany.Status.RESIGNED)

    st, d = api('GET', '/employees', TA, CID)
    check('G1 在职列表（默认 tab=all）', st == 200 and len(d) == 3, f'{st} {len(d)}')
    st, d = api('GET', '/employees?tab=pending', TA, CID)
    check('G2 tab=pending', st == 200 and len(d) == 3, f'{st} {len(d)}')
    st, d = api('GET', '/employees?tab=resigned', TA, CID)
    check('G3 tab=resigned', st == 200 and len(d) == 1, f'{st}')
    st, d = api('GET', f'/employees?keyword={W1}', TA, CID)
    check('G4 keyword 按手机号筛', st == 200 and len(d) == 1, f'{st}')
    st, d = api('GET', f'/employees?tab=pending&post=0', TA, CID)
    check('G5 post=0 未设岗筛选', st == 200 and len(d) == 3, f'{st} {len(d)}')
    st, d = api('GET', f'/employees?post={worker_post.id}', TA, CID)
    check('G6 post=<id> 岗位筛选', st == 200 and len(d) == 2, f'{st} {len(d)}')
    w2_link = UserCompany.objects.get(user=w2u, company=company)
    w2_link.fixed_salary = Decimal('123.456')
    w2_link.save(update_fields=['fixed_salary'])
    st, d = api('GET', f'/employees?keyword={W2}', TA, CID)
    check('G7 fixed_salary q2 展示', st == 200 and str(d[0].get('fixed_salary')) == '123.46',
          f'{d[0].get("fixed_salary")}')

    st, d = api('POST', f'/employees/{L1.id}/approve', TW, CID)
    check('G8 无 employees.manage 审批 → 403', st == 403, f'{st} {d}')
    st, d = api('POST', f'/employees/{L1.id}/approve', TA, CID)
    check('G9 审批通过', st == 200 and d.get('status') == UserCompany.Status.ACTIVE
          and d.get('hired_date') == DF, f'{st} {d}')
    check('G10 审批通知落库',
          Notification.objects.filter(company=company, code='join_approved', user=pu1).exists())
    st, d = api('POST', f'/employees/{L1.id}/approve', TA, CID)
    check('G11 非待审状态再审批 → 400', st == 400, f'{st} {d}')
    cap = company.capacity
    Company.objects.filter(pk=CID).update(capacity=UserCompany.objects.filter(
        company=company, status=UserCompany.Status.ACTIVE, is_deleted=False).count())
    company.refresh_from_db()
    st, d = api('POST', f'/employees/{L2.id}/approve', TA, CID)
    check('G12 容量满拒绝审批 → 400', st == 400, f'{st} {d}')
    Company.objects.filter(pk=CID).update(capacity=cap)
    company.refresh_from_db()

    st, d = api('POST', f'/employees/{L3.id}/reject', TA, CID)
    check('G13 拒绝申请', st == 200 and d.get('status') == UserCompany.Status.RESIGNED, f'{st} {d}')
    st, d = api('POST', f'/employees/{L3.id}/reject', TA, CID)
    check('G14 非待审再拒绝 → 400', st == 400, f'{st} {d}')

    st, d = api('POST', f'/employees/{LR.id}/rehire', TA, CID)
    check('G15 离职复职', st == 200 and d.get('status') == UserCompany.Status.ACTIVE, f'{st} {d}')
    st, d = api('POST', f'/employees/{LR.id}/rehire', TA, CID)
    check('G16 非离职再复职 → 400', st == 400, f'{st} {d}')
    Company.objects.filter(pk=CID).update(capacity=UserCompany.objects.filter(
        company=company, status=UserCompany.Status.ACTIVE, is_deleted=False).count())
    company.refresh_from_db()
    st, d = api('POST', f'/employees/{LR.id}/rehire', TA, CID)  # 已在职 → 400（状态支路）
    Company.objects.filter(pk=CID).update(capacity=UserCompany.objects.filter(
        company=company, status=UserCompany.Status.ACTIVE, is_deleted=False).count() - 1)
    LR.status = UserCompany.Status.RESIGNED
    LR.save(update_fields=['status'])
    st, d = api('POST', f'/employees/{LR.id}/rehire', TA, CID)
    check('G17 容量满拒绝复职 → 400', st == 400, f'{st} {d}')
    Company.objects.filter(pk=CID).update(capacity=cap)
    company.refresh_from_db()

    print('\n===== G+. 离职 / 岗位 / 组 / 置顶 =====')
    owner_link = UserCompany.objects.get(user=owner, company=company)
    st, d = api('POST', f'/employees/{owner_link.id}/resign', TA, CID)
    check('G18 最后管理员禁离职 → 400', st == 400, f'{st} {d}')
    # w2 造一条未结算登记 → 拒离职
    w2_link.refresh_from_db()
    rec_proc = Process.objects.create(company=company, name='OPAF记录工序', default_price='0.5', sort=99)
    w2_link.fixed_salary = None
    w2_link.save(update_fields=['fixed_salary'])
    st, d = api('POST', f'/employees/{w2_link.id}/resign', TA, CID)
    check('G19 无登记可正常离职前提（w2 未结算待造）',
          st == 200 and d.get('status') == UserCompany.Status.RESIGNED, f'{st} {d}')
    # 隔离库中无未结登记时必须能离职；随后复职，补造未结登记验证拦截。
    UserCompany.objects.filter(pk=w2_link.pk).update(status=UserCompany.Status.ACTIVE)
    w2_link.refresh_from_db()
    # P1 床次在 H 段创建；此处先建最小菲票供登记约束
    prod0 = Production.objects.create(company=company, style=Style.objects.create(
        company=company, style_no='OPAF-S0', name='离职前置款'), bed_no='OPAF-B0',
        total_qty=5, plan_qty=5)
    t0 = Ticket.objects.create(company=company, production=prod0, bed_no='OPAF-B0',
                               bundle_no='1', color='黑', size='M', qty=5, qr_code='OPAF|B0|1')
    Record.objects.create(company=company, ticket=t0, process=rec_proc, user=w2u, qty=5,
                          price=rec_proc.default_price)
    st, d = api('POST', f'/employees/{w2_link.id}/resign', TA, CID)
    check('G20 存在未结算登记禁离职 → 400', st == 400, f'{st} {d}')
    st, d = api('POST', f'/employees/{L1.id}/resign', TA, CID)
    check('G21 无未结算离职成功', st == 200 and d.get('status') == UserCompany.Status.RESIGNED,
          f'{st} {d}')
    check('G22 离职通知落库',
          Notification.objects.filter(company=company, code='resigned', user=pu1).exists())
    st, d = api('POST', f'/employees/{L1.id}/resign', TA, CID)
    check('G23 非在职再离职 → 400', st == 400, f'{st} {d}')
    st, d = api('POST', f'/employees/{L1.id}/rehire', TA, CID)
    check('G24 L1 复职回在职（后续用例用）', st == 200, f'{st}')

    new_post = PostM.objects.create(company=company, name='OPAF裁床岗', permissions=[])
    st, d = api('POST', f'/employees/{L1.id}/set-post', TA, CID, data={'post_id': 999999999})
    check('G25 岗位不存在 → 400', st == 400, f'{st} {d}')
    st, d = api('POST', f'/employees/{L2.id}/set-post', TA, CID, data={'post_id': new_post.id})
    check('G26 非在职成员设岗 → 400', st == 400, f'{st} {d}')
    st, d = api('POST', f'/employees/{L1.id}/set-post', TA, CID, data={'post_id': new_post.id})
    check('G27 设置岗位成功', st == 200 and d.get('post_name') == 'OPAF裁床岗', f'{st} {d}')

    st, d = api('POST', f'/employees/{L1.id}/set-group', TA, CID, data={'group_no': 0})
    check('G28 组编号 0 → 400', st == 400, f'{st} {d}')
    st, d = api('POST', f'/employees/{L1.id}/set-group', TA, CID, data={'group_no': 3})
    check('G29 设置组编号成功', st == 200 and d.get('group_no') == 3, f'{st} {d}')
    st, d = api('POST', f'/employees/{L2.id}/set-group', TA, CID, data={'group_no': 1})
    check('G29b 非在职成员设组 → 400', st == 400, f'{st} {d}')
    st, d = api('POST', '/employees/999999999/approve', TA, CID)
    check('G29c 成员不存在 → 404', st == 404, f'{st} {d}')

    st, d = api('POST', f'/employees/{L1.id}/pin', TA, CID, data={'is_pinned': True})
    check('G30 置顶', st == 200 and d.get('is_pinned') is True, f'{st} {d}')
    st, d = api('POST', f'/employees/{L1.id}/pin', TA, CID, data={'is_pinned': False})
    check('G31 取消置顶', st == 200 and d.get('is_pinned') is False, f'{st} {d}')

    print('\n===== G++. 套餐 / 指定工序 =====')
    st, d = api('PUT', '/companies/plan', TA, CID, data={'plan': 'gold'})
    check('G32 未知套餐 → 400', st == 400, f'{st} {d}')
    bulk_users = []
    for i in range(1, 22):
        bu, _ = User.objects.get_or_create(username=f'opaf_bulk_{i:02d}',
                                           defaults={'real_name': f'OPAF批量{i}'})
        bulk_users.append(bu)
    UserCompany.objects.bulk_create([UserCompany(user=bu, company=company, post=worker_post,
                                                 status=UserCompany.Status.ACTIVE)
                                     for bu in bulk_users])
    st, d = api('PUT', '/companies/plan', TA, CID, data={'plan': 'trial'})
    check('G33 在职超容量拒绝降档 → 400', st == 400, f'{st} {d}')
    UserCompany.objects.filter(user__in=bulk_users).delete()
    Company.objects.filter(pk=CID).update(plan='trial', capacity=20)
    company.refresh_from_db()
    st, d = api('PUT', '/companies/plan', TA, CID, data={'plan': 'basic'})
    check('G34 变更套餐成功（容量随写）', st == 200 and d.get('plan') == 'basic'
          and d.get('capacity') == 50, f'{st} {d}')
    check('G35 变更套餐落审计',
          AuditLog.objects.filter(company=company, message__contains='变更版本套餐').exists())

    st, d = api('PUT', f'/employees/{L1.id}/assigned-processes', TA, CID,
                data={'process_ids': [999999999]})
    check('G36 指定工序含非本厂工序 → 400', st == 400, f'{st} {d}')
    p_sel = Process.objects.create(company=company, name='OPAF指定工序', sort=98)
    st, d = api('PUT', f'/employees/{L1.id}/assigned-processes', TA, CID,
                data={'process_ids': [p_sel.id, p_sel.id]})
    check('G37 指定工序去重保存', st == 200 and d.get('assigned_processes') == [p_sel.id],
          f'{st} {d}')
    n1 = AuditLog.objects.filter(company=company, type=AuditLog.Type.PROCESS).count()
    st, d = api('PUT', f'/employees/{L1.id}/assigned-processes', TA, CID,
                data={'process_ids': [p_sel.id]})
    n2 = AuditLog.objects.filter(company=company, type=AuditLog.Type.PROCESS).count()
    check('G38 未变化不重复审计', st == 200 and n1 == n2, f'{st} {n1}/{n2}')

    print('\n===== G3. 邀请员工 =====')
    st, d = api('POST', '/employees/invite', TA, CID, data={'phone': 'abc'})
    check('G39 邀请手机号非法 → 400', st == 400, f'{st} {d}')
    inv_post = PostM.objects.create(company=company, name='OPAF邀请岗', permissions=[])
    st, d = api('POST', '/employees/invite', TA, CID,
                data={'phone': P_INV, 'real_name': 'OPAF受邀', 'post_id': 999999999})
    check('G40 邀请岗位不存在 → 400', st == 400, f'{st} {d}')
    st, d = api('POST', '/employees/invite', TA, CID,
                data={'phone': P_INV, 'real_name': 'OPAF受邀', 'post_id': inv_post.id})
    check('G41 邀请成功 → 201 待审核', st == 201
          and (d.get('employee') or {}).get('status') == UserCompany.Status.PENDING, f'{st} {d}')
    check('G42 邀请通知落库',
          Notification.objects.filter(company=company, code='invited').exists())
    st, d = api('POST', '/employees/invite', TA, CID, data={'phone': P_INV})
    check('G43 重复邀请（待审）→ 400', st == 400, f'{st} {d}')
    inv_link = UserCompany.objects.get(user__phone=P_INV, company=company)
    st, d = api('POST', f'/employees/{inv_link.id}/approve', TA, CID)
    check('G44 受邀者审批通过', st == 200, f'{st}')
    st, d = api('POST', '/employees/invite', TA, CID, data={'phone': P_INV})
    check('G45 在职成员邀请 → 400', st == 400, f'{st} {d}')
    inv_link.is_deleted = True
    inv_link.save(update_fields=['is_deleted'])
    st, d = api('POST', '/employees/invite', TA, CID,
                data={'phone': P_INV, 'real_name': 'OPAF受邀', 'post_id': inv_post.id})
    check('G46 软删关系复活为待审 → 201', st == 201, f'{st} {d}')
    inv_link.refresh_from_db()
    check('G47 复活后 PENDING 且带岗位',
          inv_link.status == UserCompany.Status.PENDING
          and inv_link.post_id == inv_post.id, f'{inv_link.status} {inv_link.post_id}')
    # 容量满拒绝邀请（口径同 approve）
    cap = company.capacity
    Company.objects.filter(pk=CID).update(capacity=UserCompany.objects.filter(
        company=company, status=UserCompany.Status.ACTIVE, is_deleted=False).count())
    company.refresh_from_db()
    st, d = api('POST', '/employees/invite', TA, CID, data={'phone': '19977660189'})
    check('G47b 容量满拒绝邀请 → 400', st == 400, f'{st} {d}')
    Company.objects.filter(pk=CID).update(capacity=cap)
    company.refresh_from_db()
    # 已存在用户 real_name 为空时回填（invite 462-463 分支）
    User.objects.create(phone=P_NOREAL, username=f'u_{P_NOREAL}', real_name='')
    st, d = api('POST', '/employees/invite', TA, CID,
                data={'phone': P_NOREAL, 'real_name': 'OPAF补名'})
    check('G47c 邀请回填空姓名', st == 201
          and User.objects.get(phone=P_NOREAL).real_name == 'OPAF补名', f'{st} {d}')
    UserCompany.all_objects.filter(user__phone=P_NOREAL).delete()
    User.objects.filter(phone=P_NOREAL).delete()  # 复位，保证每轮走同一分支

    print('\n===== G4. 岗位 CRUD / copy =====')
    st, d = api('GET', '/posts', TA, CID)
    posts_rows = d.get('results', d if isinstance(d, list) else [])
    check('G48 岗位列表含 effective_perms', st == 200 and any(
        p.get('name') == '管理员' and p.get('effective_perms') == ['*'] for p in posts_rows),
        f'{st} {str(d)[:160]}')
    admin_post_id = next(p['id'] for p in posts_rows if p.get('is_admin'))
    st, d = api('GET', f'/posts/{admin_post_id}', TA, CID)
    check('G49 岗位详情', st == 200 and d.get('id') == admin_post_id, f'{st}')
    st, d = api('POST', '/posts', TW, CID, data={'name': 'OPAF越权岗'})
    check('G50 无 employees.manage 建岗 → 403', st == 403, f'{st} {d}')
    st, d = api('POST', '/posts', TA, CID, data={'name': 'OPAF质检岗', 'permissions': ['nope']})
    check('G51 未知权限点 → 400', st == 400, f'{st} {d}')
    st, d = api('POST', '/posts', TA, CID,
                data={'name': 'OPAF质检岗', 'permissions': ['working.view', 'my_salary.view']})
    check('G52 建岗成功', st == 201 and d.get('name') == 'OPAF质检岗', f'{st} {d}')
    qc_id = d.get('id')
    st, d = api('PUT', f'/posts/{qc_id}', TA, CID, data={'name': 'OPAF质检岗2'})
    check('G53 改岗名成功', st == 200 and d.get('name') == 'OPAF质检岗2', f'{st} {d}')
    st, d = api('POST', f'/posts/{qc_id}/copy', TA, CID)
    check('G54 复制岗位 → 名称加副本', st == 201 and d.get('name') == 'OPAF质检岗2副本', f'{st} {d}')
    st, d = api('POST', f'/posts/{qc_id}/copy', TA, CID)
    check('G55 重名副本自动编号 2', st == 201 and d.get('name') == 'OPAF质检岗2副本2', f'{st} {d}')
    st, d = api('DELETE', f'/posts/{admin_post_id}', TA, CID)
    check('G56 系统岗位禁删 → 400', st == 400, f'{st} {d}')
    st, d = api('DELETE', f'/posts/{worker_post.id}', TA, CID)
    check('G57 有在职成员岗位禁删 → 400', st == 400, f'{st} {d}')
    st, d = api('DELETE', f'/posts/{qc_id}', TA, CID)
    check('G58 空岗位删除 → 204', st == 204, f'{st} {d}')

    # ================================================================
    print('\n===== H. catalog 款式 =====')
    st, d = api('POST', '/styles', TW, CID, data={'style_no': 'OPAF-SX', 'name': 'x'})
    check('H1 无 styles.manage 建款 → 403', st == 403, f'{st} {d}')
    st, d = api('POST', '/styles', TA, CID, data={
        'style_no': 'OPAF-S1', 'name': '款式一',
        'images': ['/uploads/opa1.jpg', '/uploads/opa2.jpg'],
        'process_items': [{'process_id': p_cut.id, 'price': '1.500', 'sort': 1, 'allow_skip': False},
                          {'process_id': p_make.id, 'price': '2.000', 'sort': 2}]})
    check('H2 建款成功（总单价=工序价和）', st == 201 and d.get('price') == '3.500'
          and d.get('image') == '/uploads/opa1.jpg', f'{st} {str(d)[:200]}')
    s1 = Style.objects.get(company=company, style_no='OPAF-S1')
    check('H3 工序配置落库 2 条', StyleProcess.objects.filter(style=s1).count() == 2)

    st, d = api('POST', '/styles', TA, CID, data={'style_no': 'OPAF-S1', 'name': '重复款'})
    check('H4 款号重复 → 400', st == 400, f'{st} {d}')
    st, d = api('POST', '/styles', TA, CID, data={'style_no': 'OPAF-SE1', 'name': 'e', 'process_items': 'x'})
    check('H5 process_items 非列表 → 400', st == 400, f'{st} {d}')
    st, d = api('POST', '/styles', TA, CID, data={'style_no': 'OPAF-SE2', 'name': 'e', 'process_items': [1]})
    check('H6 配置项非对象 → 400', st == 400, f'{st} {d}')
    st, d = api('POST', '/styles', TA, CID, data={'style_no': 'OPAF-SE3', 'name': 'e', 'process_items': [{}]})
    check('H7 缺工序 id → 400', st == 400, f'{st} {d}')
    st, d = api('POST', '/styles', TA, CID, data={
        'style_no': 'OPAF-SE4', 'name': 'e',
        'process_items': [{'process_id': p_cut.id}, {'process_id': p_cut.id}]})
    check('H8 重复工序 → 400', st == 400, f'{st} {d}')
    st, d = api('POST', '/styles', TA, CID, data={
        'style_no': 'OPAF-SE5', 'name': 'e', 'process_items': [{'process_id': 999999999}]})
    check('H9 工序不存在 → 400', st == 400, f'{st} {d}')
    st, d = api('POST', '/styles', TA, CID, data={
        'style_no': 'OPAF-SE6', 'name': 'e', 'process_items': [{'process_id': p_cut.id, 'price': 'abc'}]})
    check('H10 单价非法 → 400', st == 400, f'{st} {d}')
    st, d = api('POST', '/styles', TA, CID, data={
        'style_no': 'OPAF-SE7', 'name': 'e', 'images': 'not-list'})
    check('H11 images 非数组 → 400', st == 400, f'{st} {d}')
    st, d = api('POST', '/styles', TA, CID, data={
        'style_no': 'OPAF-SE8', 'name': 'e', 'images': [f'{i}.jpg' for i in range(10)]})
    check('H12 images 超 9 张 → 400', st == 400, f'{st} {d}')
    st, d = api('POST', '/styles', TA, CID, data={
        'style_no': 'OPAF-SE9', 'name': '空图款', 'images': []})
    check('H13 images 空 → [] 且 image 空', st == 201 and d.get('images') == []
          and d.get('image') == '', f'{st} {d}')
    st, d = api('POST', '/styles', TA, CID, data={
        'style_no': 'OPAF-SE10', 'name': '空串图款', 'images': ''})
    check('H13b images 空串 → []', st == 201 and d.get('images') == [], f'{st} {d}')

    cg = CustomerGroup.objects.create(company=company, name='OPAF组一')
    cust1 = Customer.objects.create(company=company, customer_no='KH0001', name='客户甲', group=cg)
    st, d = api('GET', '/styles?keyword=OPAF-S1', TA, CID)
    check('H14 款式列表 keyword 筛选+统计行', st == 200 and d.get('count', 0) == 1
          and 'ticket_count' in d['results'][0] and 'unfinished_ticket_count' in d['results'][0],
          f'{st} {str(d)[:200]}')
    st, d = api('GET', f'/styles?customer_id={cust1.id}', TA, CID)
    check('H15 customer_id 筛选（空集）', st == 200 and d.get('count', 0) == 0, f'{st}')

    st, d = api('POST', '/styles', TA, CID, data={'style_no': 'OPAF-S2', 'name': '款式二'})
    s2 = Style.objects.get(company=company, style_no='OPAF-S2')
    st, d = api('GET', f'/styles/{s1.id}', TA, CID)
    check('H16 款式详情带统计', st == 200 and d.get('style_no') == 'OPAF-S1'
          and 'cut_total' in d, f'{st}')
    st, d = api('PUT', f'/styles/{s2.id}', TA, CID, data={'style_no': 'OPAF-S1', 'name': 'x'})
    check('H17 改名撞款号 → 400', st == 400, f'{st} {d}')
    st, d = api('PUT', f'/styles/{s1.id}', TA, CID,
                data={'process_items': [{'process_id': p_make.id, 'price': '2.500', 'sort': 1}]})
    check('H18 仅 process_items 部分更新（改价）', st == 200 and d.get('price') == '2.500', f'{st} {d}')
    st, d = api('PUT', f'/styles/{s1.id}', TA, CID, data={'craft_note': 'OPAF工艺说明'})
    check('H19 工艺字段部分更新', st == 200 and d.get('craft_note') == 'OPAF工艺说明', f'{st} {d}')
    st, d = api('POST', f'/styles/{s1.id}/copy', TA, CID)
    check('H20 复制款式 -1', st == 201 and d.get('style_no') == 'OPAF-S1-1', f'{st} {d}')
    st, d = api('POST', f'/styles/{s1.id}/copy', TA, CID)
    check('H21 复制款式顺延 -2', st == 201 and d.get('style_no') == 'OPAF-S1-2', f'{st} {d}')
    st, d = api('POST', '/styles/999999999/copy', TA, CID)
    check('H21b 复制不存在款式 → 404', st == 404, f'{st} {d}')
    st, d = api('POST', '/styles/999999999/sync-process', TA, CID, data={})
    check('H21c 同步不存在款式 → 404', st == 404, f'{st} {d}')

    # save_style_processes 防御分支直调（API 层被序列化器先拦，函数级补覆盖）
    s_tmp = Style.objects.create(company=company, style_no='OPAF-SD', name='直调款')
    for _i, _bad in enumerate([{'process_items': 'x'}, {'process_items': [1]},
                               {'process_items': [{}]},
                               {'process_items': [{'process_id': None}]},
                               {'process_items': [{'process_id': p_cut.id, 'price': object()}]}]):
        try:
            save_style_processes(company, _bad, s_tmp)
            check(f'H21d save_style_processes 非法入参 #{_i + 1} 拒绝', False, '未抛')
        except ValidationError:
            check(f'H21d save_style_processes 非法入参 #{_i + 1} 拒绝', True)
    save_style_processes(
        company, {'process_items': [{'process_id': p_cut.id, 'sort': 'x'}]}, s_tmp)
    check('H21e save_style_processes sort 非法兜底 0',
          StyleProcess.objects.get(style=s_tmp, process=p_cut).sort == 0)
    st, d = api('DELETE', f'/styles/{s_tmp.id}', TA, CID)
    check('H21f 删无票款式 → 204', st == 204, f'{st} {d}')

    print('\n===== H+. catalog 工序 =====')
    st, d = api('GET', '/processes?keyword=裁', TA, CID)
    check('H22 工序 keyword 筛选', st == 200 and d.get('count', 0) >= 1, f'{st}')
    st, d = api('GET', '/processes?is_fixed=1', TA, CID)
    check('H23 is_fixed 筛选', st == 200 and d.get('count', 0) >= 1
          and all(p['is_fixed'] for p in d.get('results', [])), f'{st}')
    st, d = api('POST', '/processes', TA, CID, data={'name': '裁剪'})
    check('H24 工序重名 → 400', st == 400, f'{st} {d}')
    st, d = api('POST', '/processes', TA, CID, data={'name': 'OPAF新工序', 'default_price': '1.000'})
    check('H25 建工序成功', st == 201, f'{st} {d}')
    p_new = Process.objects.get(company=company, name='OPAF新工序')
    st, d = api('PUT', f'/processes/{p_new.id}', TA, CID, data={'name': '裁剪'})
    check('H26 改名撞工序 → 400', st == 400, f'{st} {d}')
    st, d = api('PUT', f'/processes/{p_new.id}', TA, CID,
                data={'name': 'OPAF新工序2', 'default_price': '2.000'})
    check('H27 改工序成功', st == 200 and d.get('name') == 'OPAF新工序2', f'{st} {d}')

    st, d = api('GET', '/process-selector', TA, CID)
    check('H28 选择器（管理员=固定∪全部）', st == 200 and any(p['id'] == p_fixed.id for p in d), f'{st}')
    w1_link = UserCompany.objects.get(user=w1u, company=company)
    w1_link.assigned_processes = ['x', str(p_sel.id), p_sel.id]
    w1_link.save(update_fields=['assigned_processes'])
    st, d = api('GET', '/process-selector', TW, CID)
    check('H29 员工选择器含指定工序且脏值兜底', st == 200
          and any(p['id'] == p_sel.id for p in d)
          and all(p['id'] != p_new.id for p in d), f'{st} {[p["id"] for p in d][:6]}')

    print('\n===== H3. 工序模板 =====')
    st, d = api('POST', '/process-templates', TA, CID, data={
        'name': 'OPAF模板A', 'items': [{'process_id': p_cut.id, 'price': '1.000'}]})
    check('H30 建模板成功', st == 201, f'{st} {d}')
    st, d = api('POST', '/process-templates', TA, CID, data={'name': 'OPAF模板A'})
    check('H31 模板重名 → 400', st == 400, f'{st} {d}')
    st, d = api('POST', '/process-templates', TA, CID, data={'name': 'OPAF模板B', 'items': 'x'})
    check('H32 模板 items 非列表 → 400', st == 400, f'{st} {d}')
    st, d = api('POST', '/process-templates', TA, CID, data={'name': 'OPAF模板B', 'items': [1]})
    check('H33 模板项非对象 → 400', st == 400, f'{st} {d}')
    st, d = api('POST', '/process-templates', TA, CID, data={'name': 'OPAF模板B', 'items': [{}]})
    check('H34 模板缺工序 → 400', st == 400, f'{st} {d}')
    st, d = api('POST', '/process-templates', TA, CID, data={
        'name': 'OPAF模板B', 'items': [{'process_id': p_cut.id, 'price': '-1'}]})
    check('H35 模板负工价 → 400', st == 400, f'{st} {d}')
    st, d = api('POST', '/process-templates', TA, CID, data={
        'name': 'OPAF模板B', 'items': [{'process_id': p_cut.id, 'price': 'abc'}]})
    check('H35b 模板工价非数字 → 400', st == 400, f'{st} {d}')
    try:
        _validate_template_processes(company, [{}])
        check('H35c 模板工序校验缺 id 拒绝', False, '未抛')
    except ValidationError:
        check('H35c 模板工序校验缺 id 拒绝', True)
    try:
        _validate_template_processes(company, ['x'])
        check('H35d 模板工序校验脏元素拒绝', False, '未抛')
    except ValidationError:
        check('H35d 模板工序校验脏元素拒绝', True)
    check('H35e validate_items(None) 视为未传',
          ProcessTemplateSerializer().validate_items(None) == [])
    st, d = api('POST', '/process-templates', TA, CID, data={
        'name': 'OPAF模板B', 'items': [{'process_id': 999999999}]})
    check('H36 模板挂他厂工序 → 400', st == 400, f'{st} {d}')
    st, d = api('POST', '/process-templates', TA, CID, data={
        'name': 'OPAF模板B', 'items': [{'process_id': p_cut.id, 'price': '1.000'}]})
    check('H36b 建第二模板成功', st == 201, f'{st} {d}')
    tpl = ProcessTemplate.objects.get(company=company, name='OPAF模板A')
    st, d = api('PUT', f'/process-templates/{tpl.id}', TA, CID, data={'name': 'OPAF模板B'})
    check('H37 模板改名撞 → 400', st == 400, f'{st} {d}')
    st, d = api('PUT', f'/process-templates/{tpl.id}', TA, CID,
                data={'name': 'OPAF模板A2', 'items': [{'process_id': p_make.id, 'price': '2.000'}]})
    check('H38 模板改名+改配置', st == 200 and d.get('name') == 'OPAF模板A2', f'{st} {d}')
    st, d = api('GET', '/process-templates?keyword=A2', TA, CID)
    check('H39 模板 keyword 筛选', st == 200 and d.get('count', 0) == 1, f'{st}')
    st, d = api('DELETE', f'/process-templates/{tpl.id}', TA, CID)
    check('H40 删模板 → 200/204（软删）', st == 204, f'{st} {d}')
    st, d = api('POST', '/process-templates', TA, CID, data={'name': 'OPAF模板A2'})
    check('H41 软删同名重建（硬清软删行）', st == 201, f'{st} {d}')

    print('\n===== H4. 客户分组 / 客户 =====')
    st, d = api('GET', '/customer-groups', TA, CID)
    cg_rows = d.get('results', d if isinstance(d, list) else [])
    check('H42 分组列表含客户数', st == 200 and any(
        g.get('name') == 'OPAF组一' and g.get('customer_count') == 1 for g in cg_rows),
        f'{st} {str(d)[:160]}')
    st, d = api('POST', '/customer-groups', TA, CID, data={'name': 'OPAF组一'})
    check('H43 分组重名 → 400', st == 400, f'{st} {d}')
    st, d = api('POST', '/customer-groups', TA, CID, data={'name': 'OPAF组二'})
    check('H44 建分组成功', st == 201, f'{st}')
    g2 = CustomerGroup.objects.get(company=company, name='OPAF组二')
    st, d = api('PUT', f'/customer-groups/{g2.id}', TA, CID, data={'name': 'OPAF组一'})
    check('H45 分组改名撞 → 400', st == 400, f'{st} {d}')
    st, d = api('PUT', f'/customer-groups/{g2.id}', TA, CID, data={'name': 'OPAF组二改'})
    check('H46 分组改名成功', st == 200, f'{st} {d}')
    st, d = api('DELETE', f'/customer-groups/{g2.id}', TA, CID)
    check('H47 删分组 → 204', st == 204, f'{st} {d}')

    st, d = api('POST', '/customers', TA, CID, data={'name': '客户乙'})
    check('H48 客户编号留空自动 KH 号', st == 201 and str(d.get('customer_no', '')).startswith('KH'),
          f'{st} {d}')
    st, d = api('POST', '/customers', TA, CID, data={'customer_no': 'KH0001', 'name': '撞号'})
    check('H49 客户编号重复 → 400', st == 400, f'{st} {d}')
    st, d = api('GET', '/customers?keyword=客户甲', TA, CID)
    check('H50 客户 keyword 筛选', st == 200 and d.get('count', 0) == 1, f'{st}')
    st, d = api('GET', f'/customers?group={cg.id}', TA, CID)
    check('H51 客户 group 筛选', st == 200 and d.get('count', 0) == 1, f'{st}')
    cust2 = Customer.objects.get(company=company, name='客户乙')
    st, d = api('PUT', f'/customers/{cust2.id}', TA, CID, data={'customer_no': 'KH0001', 'name': '客户乙'})
    check('H52 客户改号撞 → 400', st == 400, f'{st} {d}')
    st, d = api('PUT', f'/customers/{cust2.id}', TA, CID,
                data={'customer_no': 'KH0002', 'name': '客户乙', 'phone': '13800000000'})
    check('H53 客户改号成功', st == 200 and d.get('customer_no') == 'KH0002', f'{st} {d}')
    st, d = api('DELETE', f'/customers/{cust2.id}', TA, CID)
    check('H54 删客户 → 204', st == 204, f'{st} {d}')

    # ================================================================
    print('\n===== I. 编菲 / 菲票 =====')
    prod1 = Production.objects.create(company=company, style=s2, bed_no='OPAF-B1',
                                      total_qty=0, plan_qty=0, is_cut_done=False)
    st, d = api('POST', '/tickets/batch', TW, CID, data={'production_id': prod1.id, 'items': []})
    check('I1 无 production.manage 编菲 → 403', st == 403, f'{st} {d}')
    st, d = api('POST', '/tickets/batch', TA, CID, data={'production_id': prod1.id, 'items': []})
    check('I2 空 items → 400', st == 400, f'{st} {d}')
    st, d = api('POST', '/tickets/batch', TA, CID, data={'production_id': prod1.id, 'items': 'x'})
    check('I3 items 非列表 → 400', st == 400, f'{st} {d}')
    st, d = api('POST', '/tickets/batch', TA, CID,
                data={'production_id': prod1.id, 'items': [{'qty': 5}], 'bundle_limit': 0})
    check('I4 bundle_limit 非正 → 400', st == 400, f'{st} {d}')
    st, d = api('POST', '/tickets/batch', TA, CID,
                data={'production_id': prod1.id, 'items': [{'qty': 5}], 'bundle_limit': 'x'})
    check('I5 bundle_limit 非数字 → 400', st == 400, f'{st} {d}')
    st, d = api('POST', '/tickets/batch', TA, CID,
                data={'production_id': prod1.id, 'items': [{'qty': 5}], 'start_bundle_no': 'x1'})
    check('I6 起始扎号非数字 → 400', st == 400, f'{st} {d}')
    st, d = api('POST', '/tickets/batch', TA, CID,
                data={'production_id': prod1.id, 'items': ['notdict']})
    check('I7 编菲项非对象 → 400', st == 400, f'{st} {d}')
    st, d = api('POST', '/tickets/batch', TA, CID,
                data={'production_id': prod1.id, 'items': [{'qty': 0}]})
    check('I8 数量 0 → 400', st == 400, f'{st} {d}')
    st, d = api('POST', '/tickets/batch', TA, CID,
                data={'production_id': prod1.id, 'items': [{'qty': 'abc'}]})
    check('I9 数量非数字 → 400', st == 400, f'{st} {d}')
    st, d = api('POST', '/tickets/batch', TA, CID,
                data={'production_id': 999999999, 'items': [{'qty': 5}]})
    check('I10 床次不存在 → 404', st == 404, f'{st} {d}')

    st, d = api('POST', '/tickets/batch', TA, CID,
                data={'production_id': prod1.id,
                      'items': [{'color': '黑', 'size': 'M', 'qty': 35}], 'bundle_limit': 10})
    check('I11 编菲拆扎 35/10 → 4 扎', st == 201 and len(d) == 4
          and [t['qty'] for t in d] == [10, 10, 10, 5], f'{st} {[t.get("qty") for t in d]}')
    prod1.refresh_from_db()
    check('I12 床次总量回写 35', prod1.total_qty == 35, f'{prod1.total_qty}')
    st, d = api('POST', '/tickets/batch', TA, CID,
                data={'production_id': prod1.id,
                      'items': [{'color': '白', 'size': 'L', 'qty': 3}], 'start_bundle_no': '1'})
    check('I13 起始扎号冲突自动顺延', st == 201 and d[0]['bundle_no'] == '5', f'{st} {d}')

    api('PUT', '/settings', TA, CID, {'auto_cut_done': '1'})
    prod2 = Production.objects.create(company=company, style=s1, bed_no='OPAF-B2',
                                      total_qty=0, plan_qty=0, is_cut_done=False)
    st, d = api('POST', '/tickets/batch', TA, CID,
                data={'production_id': prod2.id, 'items': [{'color': '黑', 'size': 'M', 'qty': 5}]})
    prod2.refresh_from_db()
    check('I14 默认裁完：编菲后自动置裁完', st == 201 and prod2.is_cut_done is True,
          f'{st} {prod2.is_cut_done}')
    check('I15 默认裁完落审计',
          AuditLog.objects.filter(company=company, message__contains='默认裁完').exists())
    api('PUT', '/settings', TA, CID, {'auto_cut_done': '0'})

    t_first = Ticket.objects.get(production=prod1, bundle_no='1')
    t_second = Ticket.objects.get(production=prod1, bundle_no='5')
    t_third = Ticket.objects.get(production=prod1, bundle_no='2')
    # t_first 的登记记录提前落库（供「已有登记禁删菲票」与追溯时间线用例）
    Record.objects.create(company=company, ticket=t_first, process=rec_proc, user=w1u,
                          qty=5, price=rec_proc.default_price)
    st, d = api('GET', f'/tickets?production_id={prod1.id}', TA, CID)
    check('I16 菲票列表 production_id 筛选', st == 200 and d.get('count', 0) == 5, f'{st} {d.get("count")}')
    st, d = api('GET', '/tickets?bed_no=OPAF-B1', TA, CID)
    check('I17 bed_no 筛选', st == 200 and d.get('count', 0) == 5, f'{st}')
    st, d = api('GET', f'/tickets?bundle_no=1&production_id={prod1.id}', TA, CID)
    check('I18 bundle_no 筛选', st == 200 and d.get('count', 0) == 1, f'{st}')
    st, d = api('GET', '/tickets?is_printed=1', TA, CID)
    check('I19 is_printed=1 空集', st == 200 and d.get('count', 0) == 0, f'{st}')
    st, d = api('GET', '/tickets?ordering=print_at', TA, CID)
    check('I20 ordering=print_at（打印历史排序）', st == 200 and d.get('count', 0) >= 5, f'{st}')

    print('\n===== I+. 菲票打印 / 删除 =====')
    st, d = api('POST', '/tickets/print', TW, CID, data={'ticket_ids': [t_third.id]})
    check('I21 无打印权限 → 403', st == 403, f'{st} {d}')
    st, d = api('POST', '/tickets/print', TA, CID, data={})
    check('I22 空 ticket_ids → 400', st == 400, f'{st} {d}')
    st, d = api('POST', '/tickets/print', TA, CID, data={'ticket_ids': ['x']})
    check('I23 非整数 id → 400', st == 400, f'{st} {d}')
    st, d = api('POST', '/tickets/print', TA, CID, data={'ticket_ids': [t_third.id, 999999999]})
    check('I24 缺票 → 200 且进 skipped（M1 容忍缺失）',
          st == 200 and d.get('skipped') == [999999999], f'{st} {d}')
    st, d = api('POST', '/tickets/print', TA, CID,
                data={'ticket_ids': [t_third.id, t_third.id]})
    check('I25 打印去重成功 reported=1', st == 200 and d.get('reported') == 1, f'{st} {d}')
    t_third.refresh_from_db()
    check('I26 打印计数/标记落库（I24 混合上报已计一次 + I25 一次 = 2）',
          t_third.print_count == 2 and t_third.is_printed is True
          and t_third.print_at is not None, f'print_count={t_third.print_count}')
    check('I27 打印落审计',
          AuditLog.objects.filter(company=company, message__contains='打印菲票').exists())
    st, d = api('GET', '/tickets?is_printed=1', TA, CID)
    check('I28 打印后 is_printed=1 命中', st == 200 and d.get('count', 0) == 1, f'{st}')

    st, d = api('DELETE', f'/tickets/{t_first.id}', TA, CID)
    check('I29 已有登记禁删菲票 → 400', st == 400, f'{st} {d}')
    st, d = api('DELETE', f'/tickets/{t_second.id}', TA, CID)
    check('I30 无登记删菲票 → 204', st == 204, f'{st} {d}')
    st, d = api('DELETE', '/tickets/999999999', TA, CID)
    check('I31 删不存在菲票 → 404', st == 404, f'{st} {d}')

    print('\n===== I3. 款式删除 / 同步工序（依赖已造票与登记） =====')
    st, d = api('DELETE', f'/styles/{s2.id}', TA, CID)
    check('I32 有菲票款式禁删 → 400', st == 400, f'{st} {d}')
    st, d = api('DELETE', '/styles/999999999', TA, CID)
    check('I33 删不存在款式 → 404', st == 404, f'{st}')
    s_copy = Style.objects.get(company=company, style_no='OPAF-S1-1')
    st, d = api('POST', f'/styles/{s_copy.id}/sync-process', TA, CID, data={})
    check('I34 无登记款式同步直接 synced', st == 200 and d.get('synced') is True
          and d.get('ticket_count') == 0, f'{st} {d}')
    st, d = api('POST', f'/styles/{s2.id}/sync-process', TA, CID, data={})
    check('I35 有登记款式需二次确认', st == 200 and d.get('need_confirm') is True
          and d.get('ticket_count') == 1, f'{st} {d}')
    st, d = api('POST', f'/styles/{s2.id}/sync-process', TA, CID, data={'confirm': True})
    check('I36 确认后同步成功', st == 200 and d.get('synced') is True
          and d.get('ticket_count') == 1, f'{st} {d}')
    check('I37 同步工序落审计',
          AuditLog.objects.filter(company=company, message__contains='同步款式').exists())

    p_record = Process.objects.create(company=company, name='OPAF被登记工序', sort=97)
    Record.objects.create(company=company, ticket=t_first, process=p_record, user=w1u,
                          qty=1, price=rec_proc.default_price)
    st, d = api('DELETE', f'/processes/{p_record.id}', TA, CID)
    check('I38 有登记工序禁删 → 400', st == 400, f'{st} {d}')
    st, d = api('DELETE', f'/processes/{p_make.id}', TA, CID)
    check('I39 被款式使用工序禁删 → 400', st == 400, f'{st} {d}')
    p_del = Process.objects.create(company=company, name='OPAF可删工序', sort=96)
    st, d = api('DELETE', f'/processes/{p_del.id}', TA, CID)
    check('I40 删空闲工序 → 204', st == 204, f'{st} {d}')

    # ================================================================
    print('\n===== J. warehouse 批次 / 血缘 =====')
    st, d = api('GET', '/lots', TW, CID)
    check('J1 批次列表员工可读（GET 无门槛）', st == 200, f'{st} {d}')
    st, d = api('POST', '/lots', TW, CID, data={'lot_no': 'OPAF-LX'})
    check('J1b 无 warehouse.manage 建批次 → 403', st == 403, f'{st} {d}')
    st, d = api('POST', '/lots', TA, CID,
                data={'lot_no': 'OPAF-L1', 'fabric_name': '棉布', 'color': '黑', 'qty': '100'})
    check('J2 建批次成功', st == 201 and d.get('lot_no') == 'OPAF-L1', f'{st} {d}')
    lot1 = Lot.objects.get(company=company, lot_no='OPAF-L1')
    st, d = api('POST', '/lots', TA, CID, data={'lot_no': 'OPAF-L1'})
    check('J3 批次重名 → 400', st == 400, f'{st} {d}')
    st, d = api('POST', '/lots', TA, CID, data={'lot_no': 'OPAF-L2', 'qty': '50'})
    lot2 = Lot.objects.get(company=company, lot_no='OPAF-L2')
    st, d = api('POST', '/lots', TA, CID, data={'lot_no': 'OPAF-LC'})
    lot3 = Lot.objects.get(company=company, lot_no='OPAF-LC')
    st, d = api('GET', '/lots?keyword=OPAF-L1', TA, CID)
    check('J4 批次 keyword 筛选', st == 200 and d.get('count', 0) == 1, f'{st}')
    st, d = api('GET', f'/lots/{lot1.id}', TA, CID)
    check('J5 批次详情', st == 200 and d.get('lot_no') == 'OPAF-L1', f'{st}')
    st, d = api('PUT', f'/lots/{lot2.id}', TA, CID, data={'lot_no': 'OPAF-L1'})
    check('J6 批次改名撞 → 400', st == 400, f'{st} {d}')
    st, d = api('PUT', f'/lots/{lot1.id}', TA, CID,
                data={'fabric_name': '亚麻', 'qty': '55', 'batch_date': DF, 'remark': '改'})
    check('J7 批次更新（含 batch_date 分支）', st == 200 and d.get('fabric_name') == '亚麻'
          and d.get('qty') == '55.00' and d.get('batch_date') == DF, f'{st} {d}')
    st, d = api('GET', '/lots/999999999', TA, CID)
    check('J7b 批次详情不存在 → 404', st == 404, f'{st} {d}')
    st, d = api('PUT', f'/lots/{lot2.id}', TA, CID, data={'lot_no': 'OPAF-L2R'})
    check('J7c 批次改名成功', st == 200 and d.get('lot_no') == 'OPAF-L2R', f'{st} {d}')

    st, d = api('PUT', f'/productions/{prod1.id}/lot-links', TA, CID,
                data={'items': [{'lot_id': 999999999, 'qty': '1'}]})
    check('J8 血缘挂不存在批次 → 400', st == 400, f'{st} {d}')
    st, d = api('PUT', f'/productions/{prod1.id}/lot-links', TA, CID,
                data={'items': [{'lot_id': lot1.id, 'qty': '12'}, {'lot_id': lot1.id, 'qty': '5'}]})
    check('J9 血缘重复批次 → 400', st == 400, f'{st} {d}')
    st, d = api('PUT', '/productions/999999999/lot-links', TA, CID, data={'items': []})
    check('J10 血缘床次不存在 → 404', st == 404, f'{st} {d}')
    st, d = api('GET', '/productions/999999999/lot-links', TA, CID)
    check('J10b 血缘读取床次不存在 → 404', st == 404, f'{st} {d}')
    st, d = api('PUT', f'/productions/{prod1.id}/lot-links', TA, CID,
                data={'items': [{'lot_id': lot1.id, 'qty': '12'}]})
    check('J11 血缘整表重建', st == 200 and len(d) == 1 and d[0]['lot_no'] == 'OPAF-L1', f'{st} {d}')
    st, d = api('GET', f'/productions/{prod1.id}/lot-links', TA, CID)
    check('J12 血缘读取', st == 200 and len(d) == 1, f'{st}')

    print('\n===== J+. 追溯 =====')
    st, d = api('GET', '/trace/lot?keyword=OPAF-L1', TA, CID)
    check('J13 批次追溯搜索（含受影响面，含软删票原始计数）', st == 200
          and len(d.get('results', [])) == 1
          and d['results'][0]['production_count'] == 1
          and d['results'][0]['ticket_count'] == 5
          and d['results'][0]['qty_total'] == 38, f'{st} {str(d)[:220]}')
    st, d = api('GET', f'/trace/lot/{lot1.id}', TA, CID)
    check('J14 正向追溯批次→床次', st == 200 and len(d.get('productions', [])) == 1
          and d['productions'][0]['bed_no'] == 'OPAF-B1'
          and d['impact']['ticket_count'] == 4
          and d['impact']['qty_total'] == 35, f'{st} {str(d)[:220]}')
    st, d = api('GET', '/trace/lot/999999999', TA, CID)
    check('J15 追溯批次不存在 → 404', st == 404, f'{st}')
    st, d = api('GET', '/trace/reverse?style_no=OPAF-S2', TA, CID)
    check('J16 反向追溯按款号', st == 200 and len(d.get('matches', [])) == 1
          and d['matches'][0]['bed_no'] == 'OPAF-B1', f'{st}')
    st, d = api('GET', '/trace/reverse?color=白', TA, CID)
    check('J17 反向追溯按颜色（票色反查床次）', st == 200 and len(d.get('matches', [])) == 1
          and d['matches'][0]['bed_no'] == 'OPAF-B1', f'{st} {str(d)[:200]}')
    st, d = api('GET', f'/trace/reverse?customer_id={cust1.id}&date_from={DF}&date_to={DF}', TA, CID)
    check('J18 反向追溯按客户+日期', st == 200 and d.get('matches', []) == [], f'{st}')
    st, d = api('GET', f'/trace/ticket/{t_first.id}', TA, CID)
    check('J19 菲票终端追溯（登记时间线）', st == 200
          and len(d.get('records', [])) == 2
          and d['records'][0]['process'] == 'OPAF被登记工序'
          and d['ticket']['bundle_no'] == '1', f'{st} {str(d)[:220]}')
    st, d = api('GET', '/trace/ticket/999999999', TA, CID)
    check('J20 菲票追溯不存在 → 404', st == 404, f'{st}')
    st, d = api('GET', f'/trace/production/{prod1.id}', TA, CID)
    check('J21 床次追溯（血缘+票汇总，活跃票口径）', st == 200 and d.get('ticket_count') == 4
          and d.get('qty_total') == 35 and len(d.get('lots', [])) == 1, f'{st} {str(d)[:200]}')
    st, d = api('GET', '/trace/production/999999999', TA, CID)
    check('J21b 床次追溯不存在 → 404', st == 404, f'{st} {d}')
    st, d = api('GET', f'/trace/lot?keyword=OPAF-L1', TW, CID)
    check('J22 无 trace.view 追溯 → 403', st == 403, f'{st} {d}')

    print('\n===== J3. 采购单 =====')
    st, d = api('POST', '/purchase-orders', TA, CID, data={'supplier_name': 'OPAF供应商'})
    check('J23 采购单自动单号+默认今日', st == 201 and str(d.get('order_no', '')).startswith('CG')
          and d.get('ordered_at') == DF, f'{st} {d}')
    po1 = PurchaseOrder.objects.get(company=company, supplier_name='OPAF供应商')
    st, d = api('POST', '/purchase-orders', TA, CID,
                data={'supplier_name': 'OPAF供应商2', 'ordered_at': '2026-01-02',
                      'items': [{'fabric_name': '棉布', 'color': '黑', 'qty': '10', 'price': '3.5'}]})
    check('J24 采购单带明细创建', st == 201 and len(d.get('items', [])) == 1
          and d.get('ordered_at') == '2026-01-02', f'{st} {d}')
    po2 = PurchaseOrder.objects.get(company=company, supplier_name='OPAF供应商2')
    st, d = api('GET', '/purchase-orders?status=draft', TA, CID)
    check('J25 采购单 status 筛选', st == 200 and d.get('count', 0) == 2, f'{st} {d.get("count")}')
    st, d = api('GET', f'/purchase-orders/{po1.id}', TA, CID)
    check('J25b 采购单详情', st == 200 and d.get('id') == po1.id, f'{st}')
    st, d = api('GET', '/purchase-orders/999999999', TA, CID)
    check('J25c 采购单详情不存在 → 404', st == 404, f'{st} {d}')
    st, d = api('DELETE', '/purchase-orders/999999999', TA, CID)
    check('J25d 删不存在采购单 → 404', st == 404, f'{st} {d}')
    st, d = api('PUT', f'/purchase-orders/{po1.id}', TA, CID,
                data={'supplier_name': 'OPAF供应商改', 'ordered_at': '2026-01-03'})
    check('J26 采购单草稿可改', st == 200 and d.get('supplier_name') == 'OPAF供应商改'
          and d.get('ordered_at') == '2026-01-03', f'{st} {d}')
    st, d = api('POST', f'/purchase-orders/{po1.id}/confirm', TA, CID)
    check('J27 采购单确认 → done', st == 200 and d.get('status') == 'done', f'{st} {d}')
    st, d = api('POST', f'/purchase-orders/{po1.id}/confirm', TA, CID)
    check('J28 重复确认 → 400', st == 400, f'{st} {d}')
    st, d = api('PUT', f'/purchase-orders/{po1.id}', TA, CID, data={'remark': 'x'})
    check('J29 完成采购单禁改 → 400', st == 400, f'{st} {d}')
    st, d = api('DELETE', f'/purchase-orders/{po1.id}', TA, CID)
    check('J30 完成采购单禁删 → 400', st == 400, f'{st} {d}')
    st, d = api('POST', '/purchase-orders/999999999/confirm', TA, CID)
    check('J31 确认不存在采购单 → 404', st == 404, f'{st}')
    st, d = api('DELETE', f'/purchase-orders/{po2.id}', TA, CID)
    check('J32 删草稿采购单 → 204', st == 204, f'{st} {d}')

    print('\n===== J4. 入库单 =====')
    st, d = api('POST', '/stock-ins', TA, CID, data={'supplier': 'x'})
    check('J33 入库无明细 → 400', st == 400, f'{st} {d}')
    st, d = api('POST', '/stock-ins', TA, CID,
                data={'items': [{'fabric_name': '棉布', 'color': '黑', 'lot_no': 'OPAF-L9',
                                 'qty': '50', 'unit': '米'}]})
    check('J34 入库自动单号 RK', st == 201 and str(d.get('in_no', '')).startswith('RK'), f'{st} {d}')
    si1 = StockIn.objects.get(company=company, in_no=d['in_no'])
    lot9 = Lot.objects.filter(company=company, lot_no='OPAF-L9').first()
    check('J35 入库行自动建批次并回填来源锚点', lot9 is not None and lot9.source_in_id is not None,
          f'{lot9 and lot9.source_in_id}')
    st, d = api('GET', '/stock-ins?status=draft', TA, CID)
    check('J36 入库 status 筛选', st == 200 and d.get('count', 0) >= 1, f'{st}')
    st, d = api('GET', f'/stock-ins/{si1.id}', TA, CID)
    check('J36b 入库详情', st == 200 and d.get('id') == si1.id, f'{st}')
    st, d = api('GET', '/stock-ins/999999999', TA, CID)
    check('J36c 入库详情不存在 → 404', st == 404, f'{st} {d}')
    st, d = api('DELETE', '/stock-ins/999999999', TA, CID)
    check('J36d 删不存在入库单 → 404', st == 404, f'{st} {d}')
    st, d = api('PUT', f'/stock-ins/{si1.id}', TA, CID,
                data={'remark': 'OPAF备注', 'in_at': '2026-01-02T08:00:00'})
    check('J37 入库草稿可改（remark/in_at 分支）', st == 200 and d.get('remark') == 'OPAF备注', f'{st} {d}')
    st, d = api('POST', f'/stock-ins/{si1.id}/confirm', TA, CID)
    check('J38 入库确认 → done', st == 200 and d.get('status') == 'done', f'{st}')
    st, d = api('POST', f'/stock-ins/{si1.id}/confirm', TA, CID)
    check('J39 重复入库确认 → 400', st == 400, f'{st} {d}')
    st, d = api('PUT', f'/stock-ins/{si1.id}', TA, CID, data={'remark': 'x'})
    check('J40 完成入库禁改 → 400', st == 400, f'{st} {d}')
    st, d = api('DELETE', f'/stock-ins/{si1.id}', TA, CID)
    check('J41 完成入库禁删 → 400', st == 400, f'{st} {d}')
    st, d = api('POST', '/stock-ins/999999999/confirm', TA, CID)
    check('J42 确认不存在入库单 → 404', st == 404, f'{st}')
    st, d = api('POST', '/stock-ins', TA, CID,
                data={'items': [{'fabric_name': '麻布', 'lot_no': 'OPAF-L8', 'qty': '5'}]})
    si2 = StockIn.objects.get(company=company, in_no=d['in_no'])
    st, d = api('DELETE', f'/stock-ins/{si2.id}', TA, CID)
    check('J43 删草稿入库 → 204', st == 204, f'{st} {d}')

    print('\n===== J5. 领料单 =====')
    st, d = api('POST', '/material-issues', TA, CID, data={})
    check('J44 领料无明细 → 400', st == 400, f'{st} {d}')
    st, d = api('POST', '/material-issues', TA, CID,
                data={'items': [{'lot_id': 999999999, 'production_id': prod1.id, 'qty': '5'}]})
    check('J45 领料批次不存在 → 400', st == 400, f'{st} {d}')
    st, d = api('POST', '/material-issues', TA, CID,
                data={'items': [{'lot_id': lot1.id, 'production_id': 999999999, 'qty': '5'}]})
    check('J46 领料床次不存在 → 400', st == 400, f'{st} {d}')
    st, d = api('POST', '/material-issues', TA, CID,
                data={'items': [{'lot_id': lot1.id, 'production_id': prod1.id, 'qty': '5'}]})
    check('J47 领料自动单号 LL', st == 201 and str(d.get('issue_no', '')).startswith('LL'), f'{st} {d}')
    mi1 = MaterialIssue.objects.get(company=company, issue_no=d['issue_no'])
    st, d = api('GET', '/material-issues?status=draft', TA, CID)
    check('J48 领料 status 筛选', st == 200 and d.get('count', 0) >= 1, f'{st}')
    st, d = api('GET', f'/material-issues/{mi1.id}', TA, CID)
    check('J49 领料详情含明细', st == 200 and len(d.get('items', [])) == 1, f'{st} {d}')
    st, d = api('GET', '/material-issues/999999999', TA, CID)
    check('J50 领料详情不存在 → 404', st == 404, f'{st}')
    st, d = api('PUT', f'/material-issues/{mi1.id}', TA, CID,
                data={'remark': 'OPAF领料备注', 'issue_at': '2026-01-02T09:00:00'})
    check('J51 领料草稿可改', st == 200 and d.get('remark') == 'OPAF领料备注', f'{st} {d}')
    link_before = ProductionLotLink.objects.get(production=prod1, lot=lot1)
    st, d = api('POST', f'/material-issues/{mi1.id}/confirm', TA, CID)
    check('J52 领料确认 → confirmed', st == 200 and d.get('status') == 'confirmed', f'{st} {d}')
    link_before.refresh_from_db()
    check('J53 确认钩子血缘用量累加 12+5=17', str(link_before.qty) == '17.00', f'{link_before.qty}')
    st, d = api('POST', f'/material-issues/{mi1.id}/confirm', TA, CID)
    check('J54 重复领料确认 → 400', st == 400, f'{st} {d}')
    st, d = api('PUT', f'/material-issues/{mi1.id}', TA, CID, data={'remark': 'x'})
    check('J55 已确认领料禁改 → 400', st == 400, f'{st} {d}')
    st, d = api('DELETE', f'/material-issues/{mi1.id}', TA, CID)
    check('J56 已确认领料禁删 → 400', st == 400, f'{st} {d}')
    st, d = api('POST', '/material-issues/999999999/confirm', TA, CID)
    check('J57 确认不存在领料 → 404', st == 404, f'{st}')
    st, d = api('POST', '/material-issues', TA, CID,
                data={'items': [{'lot_id': lot2.id, 'production_id': prod1.id, 'qty': '2'}]})
    mi2 = MaterialIssue.objects.get(company=company, issue_no=d['issue_no'])
    st, d = api('DELETE', f'/material-issues/{mi2.id}', TA, CID)
    check('J58 删草稿领料 → 204', st == 204, f'{st} {d}')

    print('\n===== J6. 批次删除拦截 / 收尾 =====')
    st, d = api('DELETE', f'/lots/{lot1.id}', TA, CID)
    check('J59 有血缘批次禁删 → 400', st == 400, f'{st} {d}')
    IssueItem.objects.create(issue=mi1, production=prod1, lot=lot2, qty='2')
    st, d = api('DELETE', f'/lots/{lot2.id}', TA, CID)
    check('J60 有领料批次禁删 → 400', st == 400, f'{st} {d}')
    st, d = api('DELETE', f'/lots/{lot3.id}', TA, CID)
    check('J61 删空闲批次 → 204', st == 204, f'{st} {d}')

    # ================= 汇总 =================
    print(f'\n===== 账号运维套件：{len(PASS)} pass / {len(FAIL)} fail =====')
    for f in FAIL:
        print('FAIL:', f)


# SQLite 并发兜底：database is locked 时 2 秒重跑（套件幂等，可整体重入）
for _attempt in range(3):
    try:
        main()
        break
    except Exception as e:
        if 'database is locked' in str(e).lower() and _attempt < 2:
            print(f'[retry] database is locked, 重跑第 {_attempt + 2} 轮…')
            time.sleep(2)
            continue
        raise
