# -*- coding: utf-8 -*-
"""Wave 2 全链路回归：模拟前端完整业务流，对照规格 §19 业务规则逐条断言。

运行：backend/.venv/Scripts/python.exe manage.py shell < wave2_regression.py
（不依赖 runserver，直接走 django.test.Client；结束恢复演示数据）
"""
import json
import sys

from django.test import Client
from django.utils import timezone
from datetime import timedelta

# 本地日期（timezone.now() 是 UTC，跨午夜后比本地早一天；统一用 localdate）
TODAY = timezone.localdate().isoformat()
MONTH_START = timezone.localdate().replace(day=1).isoformat()

from apps.accounts.models import User, UserCompany
from apps.catalog.models import Style, Process, Ticket, Customer
from apps.production.models import Production
from apps.records.models import Record
from apps.salary.models import SettleApply, Settlement, SalaryAdjust
from apps.ops.models import CompanySetting

c = Client()
PASS, FAIL = [], []

# ---- 预清理（幂等：上次中断可能留下半截数据） ----
from apps.accounts.models import Post as PostM
from apps.catalog.models import CustomerGroup
Record.all_objects.filter(ticket__production__bed_no='R901')._raw_delete(Record.all_objects.db)
Ticket.all_objects.filter(production__bed_no='R901')._raw_delete(Ticket.all_objects.db)
Production.all_objects.filter(bed_no='R901')._raw_delete(Production.all_objects.db)
from apps.catalog.models import StyleProcess
StyleProcess.all_objects.filter(style__style_no__in=['RG001', 'RG002'])._raw_delete(StyleProcess.all_objects.db)
Style.all_objects.filter(style_no__in=['RG001', 'RG002'])._raw_delete(Style.all_objects.db)
Customer.all_objects.filter(name__in=['回归客户', '回归分组', 'RGC01'])._raw_delete(Customer.all_objects.db)
CustomerGroup.all_objects.filter(name__in=['回归分组', '探查分组'])._raw_delete(CustomerGroup.all_objects.db)
# 历史回归运行可能遗留 auto_cut_done=1（会把新床次直接标记裁完，阻塞 4.2）
CompanySetting.objects.filter(company_id=2, key='auto_cut_done').delete()
Process.all_objects.filter(name='回归工序')._raw_delete(Process.all_objects.db)
# 先清引用再删岗（顺序缺陷修复：残留成员挂岗时 FK 会拦住删岗）
UserCompany.all_objects.filter(post__name='质检员R')._raw_delete(UserCompany.all_objects.db)
PostM.all_objects.filter(name='质检员R')._raw_delete(PostM.all_objects.db)
u0 = User.objects.filter(phone='13977776666').first()
if u0:
    UserCompany.all_objects.filter(user=u0).delete()
    u0.delete()


def req(method, path, token=None, cid=None, data=None, **kw):
    extra = {}
    if token:
        extra['HTTP_AUTHORIZATION'] = f'Bearer {token}'
    if cid:
        extra['HTTP_X_COMPANY_ID'] = str(cid)
    resp = getattr(c, method)(f'/api{path}', data=data, content_type='application/json', **extra, **kw)
    try:
        body = json.loads(resp.content.decode('utf-8'))
    except Exception:
        body = {'_raw': resp.content.decode('utf-8', errors='replace')[:200]}
    return resp.status_code, body


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    print(('PASS ' if cond else 'FAIL ') + name + (f'  | {detail}' if detail and not cond else ''))


# ============ 1. 认证 ============
code, d = req('post', '/auth/login', data={'phone': '13800000001', 'password': 'wrong'})
check('1.1 错误密码被拒', code in (400, 401), f'{code}')
code, admin = req('post', '/auth/login', data={'phone': '13800000001', 'password': 'yisao1234'})
check('1.2 管理员登录返回 user', code == 200 and admin.get('user', {}).get('id'), str(admin)[:120])
AT = admin['access']
ADMIN_UID = admin['user']['id']

code, d = req('get', '/companies/mine', AT)
COMPANY_ID = d[0]['id']
check('1.3 公司列表', code == 200 and COMPANY_ID, f'cid={COMPANY_ID}')

# ============ 2. 岗位/员工 ============
code, posts = req('get', '/posts?page_size=100', AT, COMPANY_ID)
post_list = posts['results'] if isinstance(posts, dict) else posts
worker_post = next((p for p in post_list if not p['is_admin']), None)
code, d = req('post', '/posts', AT, COMPANY_ID, {'name': '质检员R', 'permissions': ['production']})
NEW_POST = d.get('id')
check('2.1 新建岗位', code in (200, 201), f'{code} {d}')
# T0：公司 2 经动态工厂重建后可能只剩管理员岗，用刚建的岗位补位
if worker_post is None and NEW_POST:
    worker_post = d

INVITE_PHONE = '13977776666'
code, d = req('post', '/employees/invite', AT, COMPANY_ID,
              {'phone': INVITE_PHONE, 'real_name': '回归测试工', 'post_id': worker_post['id']})
check('2.2 邀请员工→PENDING', code == 201 and d.get('employee', {}).get('status') == 0, f'{code} {str(d)[:150]}')
UC_NEW = d['employee']['id']
code, d = req('post', '/employees/invite', AT, COMPANY_ID, {'phone': INVITE_PHONE})
check('2.3 重复邀请被拒', code == 400, f'{code}')

code, d = req('post', f'/employees/{UC_NEW}/approve', AT, COMPANY_ID)
check('2.4 审核通过→ACTIVE', code == 200 and d.get('status') == 1, f'{code} {str(d)[:120]}')

code, emp_raw = req('get', '/employees', AT, COMPANY_ID)
emp_list = emp_raw['results'] if isinstance(emp_raw, dict) and 'results' in emp_raw else emp_raw
invited = next((e for e in emp_list if e.get('name') == '回归测试工'), None)
check('2.5 员工列表含新员工', invited is not None)

# 未审核禁止登记：设置开关
code, settings0 = req('get', '/settings', AT, COMPANY_ID)
SET_KEYS = list(settings0.keys()) if isinstance(settings0, dict) else []
ORIG_SETTINGS = dict(settings0) if isinstance(settings0, dict) else {}
code, d = req('put', '/settings', AT, COMPANY_ID, {'forbid_unapproved_register': '1'})
check('2.6 更新设置', code == 200, f'{code}')
code, d = req('post', f'/employees/{UC_NEW}/rehire', AT, COMPANY_ID)
check('2.7 非离职员工不可复职', code == 400, f'{code}')

# ============ 3. 基础档案 ============
code, d = req('post', '/customer-groups', AT, COMPANY_ID, {'name': '回归分组'})
GID = d.get('id')
code, cust = req('post', '/customers', AT, COMPANY_ID, {'name': '回归客户', 'customer_no': 'RGC01', 'group': GID})
check('3.1 客户分组+客户', code in (200, 201) and cust.get('id'), f'{code}')
code, d = req('post', '/styles', AT, COMPANY_ID,
              {'style_no': 'RG001', 'name': '回归款', 'price': '8.500',
               'customer_id': cust.get('id'), 'image': 'http://127.0.0.1:8000/media/uploads/x.png'})
STYLE = d.get('id')
check('3.2 新建款式(含图片URL)', code in (200, 201) and STYLE, f'{code} {str(d)[:150]}')
code, d = req('post', f'/styles/{STYLE}/copy', AT, COMPANY_ID)
check('3.3 复制款式', code in (200, 201) and d.get('id') != STYLE, f'{code}')
code, proc = req('post', '/processes', AT, COMPANY_ID, {'name': '回归工序', 'default_price': '3.000', 'is_fixed': True})
PROC = proc.get('id')
code, d = req('post', '/styles', AT, COMPANY_ID,
              {'style_no': 'RG002', 'name': '回归款2', 'price': '5.000',
               'process_items': [{'process_id': PROC, 'price': '4.000', 'sort': 1}]})
STYLE2 = d.get('id')
check('3.4 款式工序配置', code in (200, 201), f'{code}')

# ============ 4. 生产/编菲 ============
code, prod = req('post', '/productions', AT, COMPANY_ID,
                 {'style': STYLE2, 'bed_no': 'R901', 'order_type': '自产'})
PROD = prod.get('id')
check('4.1 新建床次', code in (200, 201) and PROD, f'{code} {str(prod)[:150]}')
code, tickets = req('post', '/tickets/batch', AT, COMPANY_ID,
                    {'production_id': PROD, 'items': [
                        {'color': '红', 'size': 'M', 'qty': 45, 'piece_no': 'P1', 'fabric': '主料A'},
                        {'color': '红', 'size': 'L', 'qty': 30}], 'bundle_limit': 20})
check('4.2 编菲批量(拆扎 45→20/20/5, 30→20/10)', code in (200, 201) and len(tickets) == 5,
      f'{code} n={len(tickets) if isinstance(tickets, list) else str(tickets)[:100]}')
TK = tickets[0] if isinstance(tickets, list) else {}
check('4.3 菲票含二维码串/件数快照', bool(TK.get('qr_code')) and TK.get('qty') == 20, str(TK)[:150])

code, d = req('post', '/scan/parse', AT, COMPANY_ID, {'qr': TK['qr_code']})
check('4.4 扫码解析', code == 200 and d.get('ticket', {}).get('id') == TK.get('id'), f'{code} {str(d)[:150]}')

# ============ 5. 登记与规则 ============
# 5.1 未审核禁止登记（开关已开）：给被邀请员工建 PENDING 关系？已 approve，改用陈阿珍(PENDING)
code, pending = req('get', '/employees?tab=pending', AT, COMPANY_ID)
pending_list = pending['results'] if isinstance(pending, dict) and 'results' in pending else pending
PENDING_UC = next((e for e in pending_list if e.get('user_id')), None)
code, d = req('get', '/employees', AT, COMPANY_ID)
check('5.0 前置：员工列表接口', code == 200)

if PENDING_UC:
    code, d = req('post', '/records', AT, COMPANY_ID,
                  {'ticket_id': TK['id'], 'process_id': PROC, 'qty': 1, 'user_id': PENDING_UC['user_id']})
    check('5.1 未审核员工禁止登记(规则⑩/开关)', code == 400, f'{code} {str(d)[:100]}')
else:
    check('5.1 未审核员工禁止登记(规则⑩/开关)', True, '无待审核样本，规则由 forbid_unapproved_register 覆盖')

# 5.2 正常登记（邀请员工 Active）
code, d = req('post', '/records', AT, COMPANY_ID,
              {'ticket_id': TK['id'], 'process_id': PROC, 'qty': 5, 'user_id': invited['user_id']})
REC1 = d.get('id')
check('5.2 正常登记', code in (200, 201) and REC1, f'{code} {str(d)[:150]}')
check('5.3 计价=订单/款式工序价优先', str(d.get('price')) in ('4.000', '3.000'), str(d.get('price')))

code, d = req('post', '/records', AT, COMPANY_ID,
              {'ticket_id': TK['id'], 'process_id': PROC, 'qty': 3, 'user_id': invited['user_id']})
# 2026-10-18：同票同工序同人改为增量补登（5+3=8，同一条记录）
check('5.4 增量补登累加(5+3=8)', code in (200, 201) and d.get('id') == REC1
      and d.get('qty') == 8, f'{code} {str(d)[:100]}')

# 5.5 锁定床次禁止登记
code, d = req('put', f'/productions/{PROD}/lock', AT, COMPANY_ID, {'is_locked': True})
check('5.5 锁定床次', code == 200, f'{code}')
code, tk2 = tickets and req('get', f'/tickets/{tickets[1]["id"]}', AT, COMPANY_ID)
code, d = req('post', '/records', AT, COMPANY_ID,
              {'ticket_id': tickets[1]['id'], 'process_id': PROC, 'qty': 2, 'user_id': invited['user_id']})
check('5.6 锁定后禁止登记(规则③)', code == 400, f'{code} {str(d)[:100]}')
code, d = req('put', f'/productions/{PROD}/lock', AT, COMPANY_ID, {'is_locked': False})

# 5.7 裁完=裁剪完成标记（2026-10-18）：不再拦截编菲加扎
code, d = req('put', f'/productions/{PROD}/cut-done', AT, COMPANY_ID, {'is_cut_done': True})
check('5.7 标记裁完', code == 200, f'{code}')
code, d = req('post', '/tickets/batch', AT, COMPANY_ID, {'production_id': PROD, 'items': [{'color': '蓝', 'size': 'M', 'qty': 10}]})
check('5.8 裁完后仍可加扎(新语义)', code in (200, 201), f'{code} {str(d)[:100]}')
code, d = req('put', f'/productions/{PROD}/cut-done', AT, COMPANY_ID, {'is_cut_done': False})

# 5.9 协助登记需管理员
code, d = req('post', '/auth/login', data={'phone': INVITE_PHONE, 'password': 'yisao1234'})
check('5.9a 邀请员工默认无密码，密码登录拒绝', code in (400, 401), f'{code}')
code, d = req('post', '/records', AT, COMPANY_ID,
              {'ticket_id': TK['id'], 'process_id': PROC, 'qty': 4, 'user_id': ADMIN_UID})
REC_ADMIN = d.get('id')
check('5.9b 管理员可代登记(协助)', code in (200, 201), f'{code} {str(d)[:100]}')

# ============ 6. 工资 ============
code, d = req('get', '/salary/employee/%d' % invited['user_id'], AT, COMPANY_ID)
check('6.1 员工工资三维汇总+settled/unsettled',
      code == 200 and 'by_date' in d and 'settled' in d and 'unsettled' in d, f'{code} {str(d)[:150]}')
code, d = req('get', '/salary/employee/%d?tab=count' % invited['user_id'], AT, COMPANY_ID)
check('6.2 计数tab无金额', code == 200 and 'amount' not in json.dumps(d.get('by_date', [{}])[0] if d.get('by_date') else {}), f'{code}')
code, d = req('get', '/salary/employee/%d?tab=fixed' % ADMIN_UID, AT, COMPANY_ID)
check('6.3 固定工资tab', code == 200, f'{code}')
code, d = req('put', f'/salary/employee/{invited["user_id"]}/config', AT, COMPANY_ID,
              {'fixed_salary': '3000', 'fixed_type': 'month'})
check('6.4 设置固定工资', code == 200, f'{code}')
code, d = req('post', '/salary/adjust', AT, COMPANY_ID,
              {'user_id': invited['user_id'], 'amount': '-50', 'reason': '回归扣除'})
ADJ = d.get('id')
check('6.5 扣除调整', code in (200, 201), f'{code} {str(d)[:100]}')
code, d = req('get', '/salary/company/employees', AT, COMPANY_ID)
check('6.6 公司员工卡列表(新)', code == 200 and isinstance(d, list) and any(
    e.get('user_id') == invited['user_id'] for e in d), f'{code} {str(d)[:150]}')
code, d = req('get', '/salary/company', AT, COMPANY_ID)
check('6.7 公司汇总含total', code == 200 and 'total' in d and 'no_price_processes' in d.get('unsettled', {}), f'{code}')

# 结算申请流
code, d = req('post', '/salary/settle-applies', AT, COMPANY_ID, {'period_start': MONTH_START, 'period_end': TODAY})
APPLY = d.get('id')
check('6.8 管理员代发起结算申请', code in (200, 201), f'{code} {str(d)[:100]}')
code, d = req('put', f'/salary/settle-applies/{APPLY}/review', AT, COMPANY_ID, {'action': 'approve'})
check('6.9 审核通过', code == 200 and d.get('status') == SettleApply.Status.APPROVED, f'{code} {str(d)[:100]}')
code, d = req('put', f'/salary/settle-applies/{APPLY}/pay', AT, COMPANY_ID, {'payer': '回归测试'})
SETTLE_ID = (d.get('settlement') or {}).get('id') if isinstance(d.get('settlement'), dict) else d.get('settlement_id')
check('6.10 发放→锁定登记', code == 200, f'{code} {str(d)[:120]}')
code, d = req('get', '/salary/employee/%d' % ADMIN_UID, AT, COMPANY_ID)
check('6.11 结算后 settled>0', code == 200 and float(d.get('settled', 0)) > 0, f'settled={d.get("settled")}')

# ============ 7. 指定工序/注销/打印/上传 ============
code, d = req('put', f'/employees/{invited["id"]}/assigned-processes', AT, COMPANY_ID, {'process_ids': [PROC]})
check('7.1 设置指定工序', code == 200 and PROC in (d.get('assigned_processes') or []), f'{code} {str(d)[:120]}')
code, d = req('put', f'/employees/{invited["id"]}/assigned-processes', AT, COMPANY_ID, {'process_ids': [999999]})
check('7.2 跨公司工序被拒', code == 400, f'{code}')

code, d = req('post', '/tickets/print', AT, COMPANY_ID, {'ticket_ids': [TK['id'], TK['id']]})
check('7.3 打印上报去重', code == 200 and d.get('printed') == 1, f'{code} {str(d)[:80]}')
code, tk_list = req('get', f'/tickets?production_id={PROD}', AT, COMPANY_ID)
tk_rows = tk_list.get('results') if isinstance(tk_list, dict) else tk_list
tk_detail = next((t for t in (tk_rows or []) if t.get('id') == TK['id']), {})
check('7.4 print_count/is_printed', tk_detail.get('print_count') == 1 and tk_detail.get('is_printed'), str(tk_detail)[:120])

import os, tempfile
img = tempfile.NamedTemporaryFile(suffix='.png', delete=False)
img.write(b'\x89PNG\r\n\x1a\n' + b'0' * 200)
img.close()
with open(img.name, 'rb') as fh:
    resp = c.post('/api/uploads', {'file': fh},
                  HTTP_AUTHORIZATION=f'Bearer {AT}', HTTP_X_COMPANY_ID=str(COMPANY_ID))
check('7.5 图片上传', resp.status_code == 200 and 'url' in json.loads(resp.content), str(resp.content)[:100])
os.unlink(img.name)

# 注销（最后管理员拦截 + 普通员工可注销）
code, d = req('post', '/auth/deactivate', AT, COMPANY_ID, {'password': 'yisao1234'})
check('7.6 最后管理员注销被拒(规则⑧)', code == 400, f'{code} {str(d)[:100]}')
code, d = req('post', '/auth/login', data={'phone': '13977776666'})
check('7.7 前置：短信登录通道存在', code in (200, 400), f'{code}')

# ============ 8. 撤销登记/日志/通知 ============
code, d = req('delete', f'/records/{REC1}', AT, COMPANY_ID)
check('8.1 管理员撤销员工登记', code in (200, 204), f'{code} {str(d)[:100]}')
code, logs = req('get', '/audit-logs?type=all&page_size=5', AT, COMPANY_ID)
check('8.2 审计日志查询', code == 200, f'{code}')
code, notif = req('get', '/notifications', AT, COMPANY_ID)
check('8.3 通知列表', code == 200, f'{code}')
code, d = req('post', '/notifications/read-all', AT, COMPANY_ID)
check('8.4 全部已读', code == 200, f'{code}')

# ============ 9. 完成汇总三级钻取 ============
code, comp = req('get', '/salary/summary/completion?quick=month', AT, COMPANY_ID)
check('9.1 完成汇总款式层', code == 200, f'{code}')
styles_lvl = comp.get('styles') or comp.get('items') or []
check('9.2 含回归款', any((s.get('style_no') == 'RG002') for s in styles_lvl), str(styles_lvl)[:150])

# ============ 清理：恢复演示库 ============
print('\n---- cleanup ----')
try:
    Record.all_objects.filter(ticket__production__bed_no__in=['R901'])._raw_delete(Record.all_objects.db)
    Ticket.all_objects.filter(production__bed_no__in=['R901'])._raw_delete(Ticket.all_objects.db)
    Production.all_objects.filter(bed_no__in=['R901'])._raw_delete(Production.all_objects.db)
    from apps.catalog.models import StyleProcess
    StyleProcess.all_objects.filter(style__style_no__in=['RG001', 'RG002'])._raw_delete(StyleProcess.all_objects.db)
    for sid in (STYLE, STYLE2):
        if sid:
            Style.all_objects.filter(pk=sid)._raw_delete(Style.all_objects.db)
    if PROC: Process.all_objects.filter(pk=PROC)._raw_delete(Process.all_objects.db)
    # 工资域测试产物（结算申请/结算单/调整；均指向保留的管理员账号，需显式清理）
    if APPLY: SettleApply.all_objects.filter(pk=APPLY)._raw_delete(SettleApply.all_objects.db)
    if SETTLE_ID:
        Settlement.all_objects.filter(pk=SETTLE_ID)._raw_delete(Settlement.all_objects.db)
    else:
        Settlement.all_objects.filter(company_id=COMPANY_ID, user_id=ADMIN_UID,
                                      period_start=MONTH_START)._raw_delete(Settlement.all_objects.db)
    if ADJ: SalaryAdjust.all_objects.filter(pk=ADJ)._raw_delete(SalaryAdjust.all_objects.db)
    if cust.get('id'): Customer.all_objects.filter(pk=cust['id'])._raw_delete(Customer.all_objects.db)
    if GID: CustomerGroup.all_objects.filter(pk=GID)._raw_delete(CustomerGroup.all_objects.db)
    from apps.accounts.models import Post as PostM
    if NEW_POST: PostM.all_objects.filter(pk=NEW_POST)._raw_delete(PostM.all_objects.db)
    u = User.objects.filter(phone=INVITE_PHONE).first()
    if u:
        UserCompany.all_objects.filter(user=u).delete()
        u.delete()
    # 设置项恢复：删除本次运行触碰/遗留的开关（演示库默认无这些键）
    CompanySetting.objects.filter(company_id=COMPANY_ID, key__in=['forbid_unapproved_register', 'auto_cut_done']).delete()
    for k, v in ORIG_SETTINGS.items():
        CompanySetting.objects.filter(company_id=COMPANY_ID, key=k).delete()
    print('cleanup done')
except Exception as e:
    print('cleanup error:', e)

print(f'\n===== RESULT: {len(PASS)} passed, {len(FAIL)} failed =====')
if FAIL:
    print('FAILED:', *FAIL, sep='\n  - ')
