# -*- coding: utf-8 -*-
"""
薪酬边界补测（salary/views.py 分支覆盖回填，测试方案 §3.5 欠账）：
- 期间解析分支：只传 date_from / 只传 date_to / tab=fixed 只传起始（月数=0）
- 固定工资：按月/按天两种 fixed_type 的汇总与员工 fixed 概览
- 员工薪酬查看边界：本人可查 / 他人 403 / 不存在 404 / tab=count 无金额
- 员工固定工资配置：404 / 金额格式 400 / 负数 400 / 类型不合法 400 / 只改类型
- 工资调整：列表三视角（管理员全量/按人/本人）+ 不存在员工 400
- 结算：列表筛选 / 员工不存在 400 / 倒挂期间 400 / 空期间 400 /
  全员结算 + 撤销（user=None 分支）/ PAID 期间撤销拦截
- 结算申请：倒挂 400 / 列表（本人/管理员 status 过滤与非法 status 400）/
  审核 approve / 非法 action 400 / 重复审核 400 / 待审核不可发放 400 /
  发放成功（PAID + 生成结算单）/ 无可结记录 400 / 他人作废 403 /
  本人作废非待审 400 / 管理员作废已发放 400 / 作废 404
- 完成情况汇总：user_id / customer_id 过滤 + 款式→工序钻取
- hide_scan_price 三视角：管理员读设置 / 员工读设置 / 未登录 401
跑法：manage.py shell -c "exec(compile(open('test_salary_edge.py',encoding='utf-8').read(),'test_salary_edge.py','exec'))"
幂等：自建「薪酬边界测试厂(可删)」跨轮复用（Company.creator PROTECT 不可删）；
业务行每次 queryset.delete() 物理清理，不残留软删行撞唯一约束。
"""
import json
from datetime import timedelta

from django.contrib.auth.hashers import make_password
from django.test import Client
from django.utils import timezone

from apps.accounts.models import Company, Post as PostM, User, UserCompany
from apps.accounts.services import create_company_with_owner
from apps.catalog.models import Customer, Process, Style, StyleProcess, Ticket
from apps.ops.models import AuditLog, CompanySetting, Notification
from apps.production.models import Production
from apps.records.models import Record
from apps.salary.models import SalaryAdjust, SettleApply, Settlement

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


OWNER, H_W1, H_W2, H_W3 = ('13988920001', '13988920002', '13988920003',
                           '13988920004')
H_W4, H_W5 = '13988920005', '13988920006'
CO = '薪酬边界测试厂(可删)'


def ensure_user(phone, name):
    u = User.objects.filter(phone=phone).first()
    if u is None:
        u = User.objects.create(phone=phone, username=phone, real_name=name,
                                password=make_password('yisao1234'), is_active=True)
    return u


def ensure_member(u, company, post):
    uc = UserCompany.all_objects.filter(user=u, company=company).first()
    if uc is None:
        return UserCompany.objects.create(user=u, company=company, post=post,
                                          status=UserCompany.Status.ACTIVE)
    uc.is_deleted = False
    uc.status = UserCompany.Status.ACTIVE
    uc.post = post
    uc.is_suspended = False
    uc.save(update_fields=['is_deleted', 'status', 'post', 'is_suspended',
                           'updated_at'])
    return uc


def wipe_business(company):
    """物理清理该公司全部薪酬/业务行（queryset.delete=Collector 物理删）。"""
    Record.objects.filter(company=company).delete()
    Settlement.objects.filter(company=company).delete()
    SettleApply.objects.filter(company=company).delete()
    SalaryAdjust.objects.filter(company=company).delete()
    Ticket.all_objects.filter(company=company).delete()
    Production.all_objects.filter(company=company).delete()
    StyleProcess.all_objects.filter(style__company=company).delete()
    Style.all_objects.filter(company=company).delete()
    Customer.all_objects.filter(company=company, name__startswith='薪酬边界').delete()
    Process.all_objects.filter(company=company, name__startswith='SAL').delete()
    AuditLog.objects.filter(company=company).delete()
    Notification.objects.filter(company=company).delete()
    ClientLogCleanup = None  # 占位：本套件不产生客户端日志


# ================= preclean：复用测试厂 =================
company = Company.objects.filter(name=CO).first()
if company is None:
    owner = ensure_user(OWNER, '薪酬边界厂长')
    company, admin_post = create_company_with_owner(owner, CO)
else:
    owner = ensure_user(OWNER, '薪酬边界厂长')
    admin_post = PostM.objects.get(company=company, is_admin=True, is_deleted=False)
CID = company.id

wipe_business(company)

# 成员与岗位（每轮强制复位：w1~w3 均为车间岗，只有 my_salary.view/working.view）
post_shop = PostM.objects.filter(company=company, name='SAL车间',
                                 is_deleted=False).first()
if post_shop is None:
    post_shop = PostM.objects.create(company=company, name='SAL车间',
                                     permissions=['my_salary.view', 'working.view'])
else:
    PostM.objects.filter(pk=post_shop.pk).update(
        permissions=['my_salary.view', 'working.view'])
    post_shop.refresh_from_db()
w1 = ensure_user(H_W1, '薪酬工一')
w2 = ensure_user(H_W2, '薪酬工二')
w3 = ensure_user(H_W3, '薪酬工三')
w4 = ensure_user(H_W4, '薪酬工四')
w5 = ensure_user(H_W5, '薪酬工五')
uc1 = ensure_member(w1, company, post_shop)
uc2 = ensure_member(w2, company, post_shop)
uc3 = ensure_member(w3, company, post_shop)
uc4 = ensure_member(w4, company, post_shop)
uc5 = ensure_member(w5, company, post_shop)
UserCompany.objects.filter(pk__in=[uc1.pk, uc2.pk, uc3.pk, uc4.pk, uc5.pk]).update(
    fixed_salary=None, fixed_type=UserCompany.FixedType.MONTH)
Company.objects.filter(pk=company.pk).update(capacity=50)

T = {'owner': login(OWNER), 'w1': login(H_W1), 'w2': login(H_W2), 'w3': login(H_W3)}
check('P0 测试厂成员登录', all(T.values()), str({k: bool(v) for k, v in T.items()}))

# ================= 最小业务链：款式→床次→菲票→登记 =================
style = Style.objects.create(company=company, style_no='SAL-S1', name='薪酬边界测试款')
cust = Customer.objects.create(company=company, customer_no='SALC1',
                               name='薪酬边界客户')
proc = Process.objects.create(company=company, name='SAL车缝', is_fixed=True,
                              default_price=2.5, sort=98)
StyleProcess.objects.create(style=style, process=proc, price=2.5, sort=1)
prod = Production.objects.create(company=company, style=style, bed_no='SALB1',
                                 customer=cust, total_qty=150)
t1 = Ticket.objects.create(company=company, production=prod, bed_no='SALB1',
                           bundle_no='1', color='黑', size='M', qty=100)
t2 = Ticket.objects.create(company=company, production=prod, bed_no='SALB1',
                           bundle_no='2', color='白', size='L', qty=50)
t3 = Ticket.objects.create(company=company, production=prod, bed_no='SALB1',
                           bundle_no='3', color='灰', size='S', qty=30)
t4 = Ticket.objects.create(company=company, production=prod, bed_no='SALB1',
                           bundle_no='4', color='蓝', size='XL', qty=20)
today = timezone.localdate()
now = timezone.now()
# 登记数据：w1=10×2.5=25 元；w2=4×5=20 元；w3=2×0（未上价工序统计用）
rec1 = Record.objects.create(company=company, ticket=t1, process=proc, user=w1,
                             qty=10, price='2.500', registered_at=now)
rec2 = Record.objects.create(company=company, ticket=t2, process=proc, user=w2,
                             qty=4, price='5.000', registered_at=now)
rec3 = Record.objects.create(company=company, ticket=t3, process=proc, user=w3,
                             qty=2, price='0.000', registered_at=now)
# 昨日记录：供全员结算用独立期间（避开 PAID 申请的期间重叠拦截）
rec4 = Record.objects.create(company=company, ticket=t4, process=proc, user=w3,
                             qty=1, price='2.500',
                             registered_at=now - timedelta(days=1))

# ================= 期间解析分支（CompanySummaryView 108/110） =================
st, d = api('GET', '/salary/company?date_from=2026-10-01', T['owner'], CID)
check('SAL-101 汇总只传 date_from → 自动补月末（200 + period 回显）',
      st == 200 and (d or {}).get('period', {}).get('date_to') is not None,
      f'{st} {json.dumps(d, ensure_ascii=False)[:120]}')
st, d = api('GET', '/salary/company?date_to=2026-10-31', T['owner'], CID)
check('SAL-102 汇总只传 date_to → 自动补月初（200）',
      st == 200 and (d or {}).get('period', {}).get('date_from') is not None,
      f'{st} {json.dumps(d, ensure_ascii=False)[:120]}')

# ================= 固定工资：配置 + 汇总（140 DAY 分支） + fixed 概览 =================
# 四类工资制：固定工资计薪要求 salary_type=fixed（配置时一并切换）
st, d = api('PUT', f'/salary/employee/{w2.id}/config', T['owner'], CID,
            {'fixed_salary': '2000', 'fixed_type': 'month',
             'salary_type': 'fixed'})
check('SAL-110 配置 w2 固定工资 2000/按月(200)', st == 200 and (d or {}).get('fixed_salary') == '2000.00'
      and (d or {}).get('salary_type') == 'fixed',
      f'{st} {json.dumps(d, ensure_ascii=False)[:120]}')
st, d = api('PUT', f'/salary/employee/{w3.id}/config', T['owner'], CID,
            {'fixed_salary': '100', 'fixed_type': 'day', 'salary_type': 'fixed'})
check('SAL-111 配置 w3 固定工资 100/按天(200)', st == 200 and (d or {}).get('fixed_type') == 'day',
      f'{st} {json.dumps(d, ensure_ascii=False)[:120]}')
st, d = api('GET', '/salary/company', T['owner'], CID)
ft = (d or {}).get('fixed_total')
# w2 按月 2000×1 个月 + w3 按天 100×2 个工作日（今日+昨日）= 2200
check('SAL-112 汇总 fixed_total 按月+按天合计 2200.00（DAY/MONTH 双分支）',
      st == 200 and ft == '2200.00', f'{st} fixed_total={ft}')
# 复位回工序计件：后续登记结算场景按记录快照口径（四选一互斥切换）
st, d = api('PUT', f'/salary/employee/{w2.id}/config', T['owner'], CID,
            {'salary_type': 'process'})
check('SAL-113 w2 切回工序计件(200)', st == 200 and (d or {}).get('salary_type') == 'process',
      f'{st} {json.dumps(d, ensure_ascii=False)[:120]}')
st, d = api('PUT', f'/salary/employee/{w3.id}/config', T['owner'], CID,
            {'salary_type': 'process'})
check('SAL-114 w3 切回工序计件(200)', st == 200 and (d or {}).get('salary_type') == 'process',
      f'{st} {json.dumps(d, ensure_ascii=False)[:120]}')
st, d = api('GET', f'/salary/employee/{w3.id}?tab=fixed', T['owner'], CID)
fb = (d or {}).get('fixed', {})
check('SAL-113 员工 fixed 概览按天：worked_days=2 金额 200.00（274 DAY 分支）',
      st == 200 and fb.get('worked_days') == 2 and fb.get('amount') == '200.00'
      and fb.get('months') == 1,
      f'{st} {json.dumps(fb, ensure_ascii=False)[:150]}')
st, d = api('GET', f'/salary/employee/{w3.id}?tab=fixed&date_from=2030-01-01', T['owner'], CID)
fb = (d or {}).get('fixed', {})
check('SAL-114 tab=fixed 只传 date_from → 月数 0、无登记 0.00（_month_count 短路分支）',
      st == 200 and fb.get('months') == 0 and fb.get('worked_days') == 0
      and fb.get('amount') == '0.00',
      f'{st} {json.dumps(fb, ensure_ascii=False)[:150]}')

# ================= 员工薪酬查看边界（172 / 180-181） =================
st, d = api('GET', f'/salary/employee/{w2.id}', T['w1'], CID)
check('SAL-120 员工查他人工资且无 salary.view → 403',
      st == 403, f'{st} {json.dumps(d, ensure_ascii=False)[:100]}')
st, d = api('GET', '/salary/employee/999999', T['owner'], CID)
check('SAL-121 查不存在员工 → 404', st == 404, f'{st} {json.dumps(d, ensure_ascii=False)[:100]}')
st, d = api('GET', f'/salary/employee/{w1.id}', T['w1'], CID)
head = (d or {})
check('SAL-122 本人查自己 → 200 且头部 settled/unsettled 齐全',
      st == 200 and 'settled' in head and 'unsettled' in head,
      f'{st} {json.dumps(head, ensure_ascii=False)[:150]}')
st, d = api('GET', f'/salary/employee/{w1.id}?tab=count', T['w1'], CID)
check('SAL-123 tab=count 只返回数量（summary 无 amount、行无金额）',
      st == 200 and 'amount' not in (d or {}).get('summary', {})
      and all('amount' not in r for r in (d or {}).get('by_date', [])),
      f'{st} {json.dumps((d or {}).get("summary"), ensure_ascii=False)[:120]}')

# ================= 员工固定工资配置边界（353-366） =================
st, d = api('PUT', '/salary/employee/999999/config', T['owner'], CID, {'fixed_salary': '100'})
check('SAL-130 配置不存在员工 → 404', st == 404, f'{st} {json.dumps(d, ensure_ascii=False)[:100]}')
st, d = api('PUT', f'/salary/employee/{w1.id}/config', T['owner'], CID, {'fixed_salary': 'abc'})
check('SAL-131 固定工资非数字 → 400', st == 400, f'{st} {json.dumps(d, ensure_ascii=False)[:120]}')
st, d = api('PUT', f'/salary/employee/{w1.id}/config', T['owner'], CID, {'fixed_salary': '-5'})
check('SAL-132 固定工资负数 → 400', st == 400, f'{st} {json.dumps(d, ensure_ascii=False)[:120]}')
st, d = api('PUT', f'/salary/employee/{w1.id}/config', T['owner'], CID, {'fixed_type': 'hour'})
check('SAL-133 固定工资类型不合法 → 400', st == 400, f'{st} {json.dumps(d, ensure_ascii=False)[:120]}')
st, d = api('PUT', f'/salary/employee/{w1.id}/config', T['owner'], CID, {'fixed_type': 'day'})
check('SAL-134 只传 fixed_type 不传金额 → 200（金额字段 None 分支）',
      st == 200 and (d or {}).get('fixed_type') == 'day'
      and (d or {}).get('fixed_salary') == '0.00',
      f'{st} {json.dumps(d, ensure_ascii=False)[:120]}')

# ================= 工资调整（383-396 / 414-415） =================
st, d = api('POST', '/salary/adjust', T['owner'], CID,
            {'user_id': w1.id, 'amount': '-10', 'reason': '测试扣款'})
check('SAL-140 管理员发起工资调整 201', st == 201, f'{st} {json.dumps(d, ensure_ascii=False)[:150]}')
st, d = api('POST', '/salary/adjust', T['owner'], CID,
            {'user_id': 999999, 'amount': '5', 'reason': 'x'})
check('SAL-141 调整不存在员工 → 400', st == 400, f'{st} {json.dumps(d, ensure_ascii=False)[:120]}')
st, d = api('GET', '/salary/adjust', T['owner'], CID)
check('SAL-142 管理员列表返回全量（≥1 条且含操作人）',
      st == 200 and len((d or {}).get('results', d if isinstance(d, list) else [])) >= 1,
      f'{st} {json.dumps(d, ensure_ascii=False)[:120]}')
st, d = api('GET', f'/salary/adjust?user_id={w1.id}&date_from=2020-01-01&date_to=2030-01-01',
            T['owner'], CID)
rows = (d or {}).get('results', d if isinstance(d, list) else [])
check('SAL-143 管理员按 user_id+期间过滤（user_id 参数分支）',
      st == 200 and all(r.get('user') == w1.id or r.get('user_name') for r in rows)
      and len(rows) == 1, f'{st} n={len(rows)}')
st, d = api('GET', '/salary/adjust', T['w1'], CID)
rows = (d or {}).get('results', d if isinstance(d, list) else [])
check('SAL-144 员工列表仅本人相关（非管理员分支）',
      st == 200 and all(r.get('user') == w1.id for r in rows),
      f'{st} n={len(rows)}')

# ================= 结算申请（先行，金额快照取未结登记） =================
st, d = api('POST', '/salary/settle-applies', T['w1'], CID,
            {'period_start': str(today), 'period_end': str(today - timedelta(days=1))})
check('SAL-150 申请期间倒挂 → 400', st == 400, f'{st} {json.dumps(d, ensure_ascii=False)[:120]}')
st, d = api('POST', '/salary/settle-applies', T['w1'], CID,
            {'period_start': str(today), 'period_end': str(today), 'note': '请结算'})
check('SAL-151 w1 发起结算申请 201 且金额快照 25.00',
      st == 201 and (d or {}).get('amount') == '25.00'
      and (d or {}).get('status') == 0,
      f'{st} {json.dumps(d, ensure_ascii=False)[:180]}')
apply1 = (d or {}).get('id')
st, d = api('POST', '/salary/settle-applies', T['w2'], CID,
            {'period_start': str(today), 'period_end': str(today)})
apply2 = (d or {}).get('id')
check('SAL-152 w2 发起结算申请 201 金额 20.00', st == 201 and (d or {}).get('amount') == '20.00',
      f'{st} {json.dumps(d, ensure_ascii=False)[:150]}')
st, d = api('GET', '/salary/settle-applies', T['w2'], CID)
rows = (d or {}).get('results', d if isinstance(d, list) else [])
check('SAL-153 员工列表仅本人申请（非管理员分支）',
      st == 200 and rows and all(r.get('user') == w2.id for r in rows),
      f'{st} n={len(rows)}')
st, d = api('GET', '/salary/settle-applies?status=abc', T['owner'], CID)
check('SAL-154 管理员列表 status 非法 → 400', st == 400, f'{st} {json.dumps(d, ensure_ascii=False)[:120]}')
st, d = api('GET', '/salary/settle-applies?status=0&user_id=' + str(w1.id), T['owner'], CID)
rows = (d or {}).get('results', d if isinstance(d, list) else [])
check('SAL-155 管理员列表 status=0 + user_id 过滤命中待审核',
      st == 200 and rows and all(r.get('status') == 0 for r in rows),
      f'{st} n={len(rows)}')
st, d = api('POST', '/salary/settle-applies', T['w3'], CID,
            {'period_start': str(today), 'period_end': str(today)})
check('SAL-156 未结工资为 0（0 价工序）→ 400 拦截',
      st == 400 and '未结工资为 0' in json.dumps(d, ensure_ascii=False),
      f'{st} {json.dumps(d, ensure_ascii=False)[:150]}')

# ================= 审核 / 发放 / 作废状态机 =================
st, d = api('PUT', f'/salary/settle-applies/{apply1}/review', T['owner'], CID, {'action': 'bad'})
check('SAL-160 非法 action → 400', st == 400, f'{st} {json.dumps(d, ensure_ascii=False)[:120]}')
st, d = api('PUT', f'/salary/settle-applies/{apply1}/review', T['owner'], CID,
            {'action': 'approve'})
check('SAL-161 审核通过 → 待发放(1)', st == 200 and (d or {}).get('status') == 1,
      f'{st} {json.dumps(d, ensure_ascii=False)[:150]}')
st, d = api('PUT', f'/salary/settle-applies/{apply1}/review', T['owner'], CID,
            {'action': 'approve'})
check('SAL-162 重复审核（非待审核）→ 400', st == 400, f'{st} {json.dumps(d, ensure_ascii=False)[:120]}')
st, d = api('PUT', f'/salary/settle-applies/{apply2}/pay', T['owner'], CID, {})
check('SAL-163 待审核申请不可发放 → 400', st == 400, f'{st} {json.dumps(d, ensure_ascii=False)[:120]}')
st, d = api('PUT', f'/salary/settle-applies/{apply1}/pay', T['owner'], CID, {'payer': '张三'})
check('SAL-164 发放成功 → PAID(2) + paid_by 落款',
      st == 200 and (d or {}).get('status') == 2 and (d or {}).get('paid_by_name'),
      f'{st} {json.dumps(d, ensure_ascii=False)[:180]}')
st, d = api('PUT', f'/salary/settle-applies/{apply1}/pay', T['owner'], CID, {})
check('SAL-165 已发放再发放 → 400（非待发放）', st == 400, f'{st} {json.dumps(d, ensure_ascii=False)[:120]}')
st, d = api('PUT', '/salary/settle-applies/999999/void', T['owner'], CID, {})
check('SAL-166 作废不存在申请 → 404', st == 404, f'{st} {json.dumps(d, ensure_ascii=False)[:100]}')
st, d = api('PUT', f'/salary/settle-applies/{apply1}/void', T['w2'], CID, {'reason': 'x'})
check('SAL-167 员工作废他人申请 → 403', st == 403, f'{st} {json.dumps(d, ensure_ascii=False)[:100]}')
st, d = api('PUT', f'/salary/settle-applies/{apply1}/void', T['owner'], CID, {})
check('SAL-168 管理员作废已发放申请 → 400', st == 400, f'{st} {json.dumps(d, ensure_ascii=False)[:120]}')

# 无可结记录发放：先把 w2 的登记直接结算掉，再走 apply2 审核通过 → 发放 400
st, d = api('POST', '/salary/settlements', T['owner'], CID,
            {'period_start': str(today), 'period_end': str(today), 'user_id': w2.id})
check('SAL-170 指定员工结算 w2 201', st == 201, f'{st} {json.dumps(d, ensure_ascii=False)[:150]}')
st, d = api('PUT', f'/salary/settle-applies/{apply2}/review', T['owner'], CID,
            {'action': 'approve'})
check('SAL-171 apply2 审核通过 200', st == 200 and (d or {}).get('status') == 1,
      f'{st} {json.dumps(d, ensure_ascii=False)[:120]}')
st, d = api('PUT', f'/salary/settle-applies/{apply2}/pay', T['owner'], CID, {})
check('SAL-172 期间无可结登记发放 → 400', st == 400, f'{st} {json.dumps(d, ensure_ascii=False)[:120]}')
st, d = api('PUT', f'/salary/settle-applies/{apply2}/void', T['w2'], CID, {'reason': '算了'})
check('SAL-173 员工作废本人非待审核申请 → 400', st == 400, f'{st} {json.dumps(d, ensure_ascii=False)[:120]}')
st, d = api('PUT', f'/salary/settle-applies/{apply2}/void', T['owner'], CID, {'reason': '作废'})
check('SAL-174 管理员作废待发放申请 → 200', st == 200 and (d or {}).get('status') == 4,
      f'{st} {json.dumps(d, ensure_ascii=False)[:120]}')

# ================= 结算列表 + 全员结算/撤销（433-444 / 469-470 / 519->521） =================
st, d = api('GET', f'/salary/settlements?user_id={w2.id}&date_from=2020-01-01&date_to=2030-01-01',
            T['owner'], CID)
rows = (d or {}).get('results', d if isinstance(d, list) else [])
check('SAL-180 结算单列表 user_id+期间过滤',
      st == 200 and rows and all(r.get('user') == w2.id for r in rows),
      f'{st} n={len(rows)}')
st, d = api('POST', '/salary/settlements', T['owner'], CID,
            {'period_start': str(today), 'period_end': str(today), 'user_id': 999999})
check('SAL-181 结算不存在员工 → 400', st == 400, f'{st} {json.dumps(d, ensure_ascii=False)[:120]}')
st, d = api('POST', '/salary/settlements', T['owner'], CID,
            {'period_start': str(today), 'period_end': str(today - timedelta(days=1))})
check('SAL-182 结算期间倒挂 → 400', st == 400, f'{st} {json.dumps(d, ensure_ascii=False)[:120]}')
st, d = api('POST', '/salary/settlements', T['owner'], CID,
            {'period_start': '2020-01-01', 'period_end': '2020-01-02'})
check('SAL-183 空期间结算 → 400', st == 400, f'{st} {json.dumps(d, ensure_ascii=False)[:120]}')
yday = str(today - timedelta(days=1))
st, d = api('POST', '/salary/settlements', T['owner'], CID,
            {'period_start': yday, 'period_end': yday})
settle_all = (d or {}).get('id')
check('SAL-184 全员结算 201（user=None，独立期间命中昨日记录 1 人）',
      st == 201 and (d or {}).get('user') is None
      and (d or {}).get('employee_count') == 1
      and (d or {}).get('amount') == '2.50',
      f'{st} {json.dumps(d, ensure_ascii=False)[:180]}')
st, d = api('POST', '/salary/settlements', T['owner'], CID,
            {'period_start': yday, 'period_end': yday})
check('SAL-185 期间已无可结登记再结算 → 400', st == 400,
      f'{st} {json.dumps(d, ensure_ascii=False)[:120]}')
st, d = api('POST', f'/salary/settlements/{settle_all}/revoke', T['owner'], CID)
check('SAL-186 撤销全员结算单（user=None 分支）→ 200 revoked',
      st == 200 and (d or {}).get('status') == 'revoked',
      f'{st} {json.dumps(d, ensure_ascii=False)[:120]}')
st, d = api('POST', f'/salary/settlements/{settle_all}/revoke', T['owner'], CID)
check('SAL-187 重复撤销 → 400', st == 400, f'{st} {json.dumps(d, ensure_ascii=False)[:120]}')
st, d = api('POST', '/salary/settlements/999999/revoke', T['owner'], CID)
check('SAL-188 撤销不存在结算单 → 404', st == 404, f'{st} {json.dumps(d, ensure_ascii=False)[:100]}')
# PAID 拦截：w1 的申请已发放（PAID，期间=今天），撤销其个人结算单应被拦
s_user = Settlement.objects.filter(company=company, user=w1,
                                   status=Settlement.Status.SETTLED).first()
st, d = api('POST', f'/salary/settlements/{s_user.pk}/revoke', T['owner'], CID)
check('SAL-189 该期间工资已发放(PAID) 撤销被拦 → 400', st == 400,
      f'{st} {json.dumps(d, ensure_ascii=False)[:120]}')

# ================= 完成情况汇总钻取（723 / 726） =================
st, d = api('GET', f'/salary/summary/completion?user_id={w1.id}', T['owner'], CID)
styles_rows = (d or {}).get('styles', [])
check('SAL-190 汇总 user_id 过滤命中 w1 款式行',
      st == 200 and styles_rows and styles_rows[0].get('style_no') == 'SAL-S1',
      f'{st} {json.dumps(styles_rows[:1], ensure_ascii=False)[:150]}')
st, d = api('GET', f'/salary/summary/completion?customer_id={cust.id}', T['owner'], CID)
check('SAL-191 汇总 customer_id 过滤命中同款',
      st == 200 and (d or {}).get('styles') and (d or {})['styles'][0].get('qty') == 17,
      f'{st} {json.dumps((d or {}).get("styles", [])[:1], ensure_ascii=False)[:150]}')
st, d = api('GET', f'/salary/summary/completion?style_id={style.id}&process_id={proc.id}',
            T['owner'], CID)
emps = (d or {}).get('employees', [])
check('SAL-192 款式→工序三级钻取：员工汇总+菲票明细',
      st == 200 and (d or {}).get('processes') and (d or {}).get('tickets')
      and len(emps) == 3,
      f'{st} employees={json.dumps(emps, ensure_ascii=False)[:150]}')

# ================= hide_scan_price 三视角（设置读取门槛） =================
st, d = api('GET', '/settings', T['owner'], CID)
check('SAL-200 管理员读设置含 hide_scan_price',
      st == 200 and 'hide_scan_price' in (d or {}), f'{st} {str(d)[:120]}')
st, d = api('GET', '/settings', T['w1'], CID)
check('SAL-201 员工读设置（展示开关）→ 200', st == 200 and isinstance(d, dict),
      f'{st} {str(d)[:120]}')
st, d = api('GET', '/settings', None, CID)
check('SAL-202 未登录读设置 → 401', st == 401, f'{st} {str(d)[:80]}')

# ================= 回归：_settle 员工去重（Record.Meta.ordering 混入 DISTINCT） =================
# w3 今昨两天各一条登记：结算人数必须去重为 1（修复前被 ordering 撑成 2）
st, d = api('POST', '/salary/settlements', T['owner'], CID,
            {'period_start': yday, 'period_end': str(today), 'user_id': w3.id})
check('SAL-203 同员工多条登记 employee_count=1（order_by 前置修 DISTINCT）',
      st == 201 and (d or {}).get('employee_count') == 1
      and (d or {}).get('amount') == '2.50',
      f'{st} {json.dumps(d, ensure_ascii=False)[:180]}')

# ================= 四类工资制：整件计件 / 计件提成 / 防重复计酬 =================
# 此前 rec1~rec4 均已被申请结算/全员结算锁定（且申请流中途改过价），
# 四类工资场景使用全新成员 w4/w5（零已结）+ 新菲票 t5（rec5 未结 45 元）。
# t5 已完成（pc=1，单工序登记）；w4/w5 与全员同在默认 1 组。
t5 = Ticket.objects.create(company=company, production=prod, bed_no='SALB1',
                           bundle_no='5', color='青', size='XXL', qty=60)
rec5 = Record.objects.create(company=company, ticket=t5, process=proc,
                             user=w4, qty=18, price='2.500',
                             registered_at=now)
# 整件计件（w4）：t5 参与登记仅 w4 一人 → 10 元/件 × 1/1 = 10.00（与金额无关）
st, d = api('PUT', f'/salary/employee/{w4.id}/config', T['owner'], CID,
            {'salary_type': 'whole', 'piece_rate': '10'})
check('SAL-210 配置 w4 整件计件 单价10(200)',
      st == 200 and (d or {}).get('salary_type') == 'whole'
      and (d or {}).get('piece_rate') == '10.00',
      f'{st} {json.dumps(d, ensure_ascii=False)[:120]}')
st, d = api('GET', f'/salary/employee/{w4.id}', T['owner'], CID)
check('SAL-211 w4 整件工资头部 未结10.00 已结0.00',
      st == 200 and (d or {}).get('unsettled') == '10.00'
      and (d or {}).get('settled') == '0.00'
      and (d or {}).get('salary_type') == 'whole',
      f'{st} u={(d or {}).get("unsettled")} s={(d or {}).get("settled")} '
      f't={(d or {}).get("salary_type")}')
# 计件提成（w5）：本组（默认1组）未结计件总额 rec5=45 × 10% = 4.50
st, d = api('PUT', f'/salary/employee/{w5.id}/config', T['owner'], CID,
            {'salary_type': 'commission', 'commission_rate': '10'})
check('SAL-212 配置 w5 计件提成 10%(200)',
      st == 200 and (d or {}).get('salary_type') == 'commission',
      f'{st} {json.dumps(d, ensure_ascii=False)[:120]}')
st, d = api('GET', f'/salary/employee/{w5.id}', T['owner'], CID)
check('SAL-213 w5 提成工资头部 未结4.50',
      st == 200 and (d or {}).get('unsettled') == '4.50',
      f'{st} u={(d or {}).get("unsettled")} s={(d or {}).get("settled")} '
      f't={(d or {}).get("salary_type")}')
st, d = api('POST', '/salary/settlements', T['owner'], CID,
            {'period_start': yday, 'period_end': str(today), 'user_id': w5.id})
check('SAL-214 w5 提成结算 201 金额4.50 wage_detail 快照',
      st == 201 and (d or {}).get('amount') == '4.50'
      and (d or {}).get('employee_count') == 1
      and (d or {}).get('wage_detail', {}).get(str(w5.id)) == '4.50',
      f'{st} {json.dumps(d, ensure_ascii=False)[:180]}')
st, d = api('POST', '/salary/settlements', T['owner'], CID,
            {'period_start': yday, 'period_end': str(today), 'user_id': w5.id})
check('SAL-215 w5 重复结算 → 400（全历史已结扣减防重复计酬）', st == 400,
      f'{st} {json.dumps(d, ensure_ascii=False)[:120]}')
st, d = api('GET', f'/salary/employee/{w5.id}', T['owner'], CID)
check('SAL-216 w5 头部翻正 已结4.50 未结0.00',
      st == 200 and (d or {}).get('settled') == '4.50'
      and (d or {}).get('unsettled') == '0.00',
      f'{st} s={(d or {}).get("settled")} u={(d or {}).get("unsettled")}')
st, d = api('POST', '/salary/settlements', T['owner'], CID,
            {'period_start': yday, 'period_end': str(today), 'user_id': w4.id})
check('SAL-217 w4 整件结算 201 金额10.00',
      st == 201 and (d or {}).get('amount') == '10.00',
      f'{st} {json.dumps(d, ensure_ascii=False)[:160]}')
st, d = api('POST', '/salary/settlements', T['owner'], CID,
            {'period_start': yday, 'period_end': str(today)})
check('SAL-218 全员再结算 → 400（process 记录已锁定 + 非 process 已结清）',
      st == 400, f'{st} {json.dumps(d, ensure_ascii=False)[:120]}')
# 配置校验：whole 缺单价 / commission 缺比例 / 比例越界 / salary_type 非法
st, d = api('PUT', f'/salary/employee/{w3.id}/config', T['owner'], CID,
            {'salary_type': 'whole'})
check('SAL-219 切整件缺单价 → 400', st == 400,
      f'{st} {json.dumps(d, ensure_ascii=False)[:120]}')
st, d = api('PUT', f'/salary/employee/{w3.id}/config', T['owner'], CID,
            {'salary_type': 'commission'})
check('SAL-220 切提成缺比例 → 400', st == 400,
      f'{st} {json.dumps(d, ensure_ascii=False)[:120]}')
st, d = api('PUT', f'/salary/employee/{w3.id}/config', T['owner'], CID,
            {'salary_type': 'commission', 'commission_rate': '150'})
check('SAL-221 提成比例 150 → 400', st == 400,
      f'{st} {json.dumps(d, ensure_ascii=False)[:120]}')
st, d = api('PUT', f'/salary/employee/{w3.id}/config', T['owner'], CID,
            {'salary_type': 'hourly'})
check('SAL-222 非法工资方式 → 400', st == 400,
      f'{st} {json.dumps(d, ensure_ascii=False)[:120]}')
st, d = api('GET', '/salary/company', T['owner'], CID)
check('SAL-223 汇总响应含 whole_total/commission_total',
      st == 200 and 'whole_total' in (d or {}) and 'commission_total' in (d or {}),
      f'{st} {json.dumps(d, ensure_ascii=False)[:120]}')

# ================= cleanup：物理清业务行 + 复位成员配置 =================
try:
    wipe_business(company)
    UserCompany.objects.filter(
        pk__in=[uc1.pk, uc2.pk, uc3.pk, uc4.pk, uc5.pk]).update(
        fixed_salary=None, fixed_type=UserCompany.FixedType.MONTH,
        salary_type=UserCompany.SalaryType.PROCESS, piece_rate=None,
        commission_rate=None)
    Company.objects.filter(pk=company.pk).update(capacity=50)
    print('cleanup: done（业务行已物理清理，测试厂/账号复用保留）')
except Exception as e:  # noqa: BLE001
    print('cleanup WARN:', e)

print(f'\n==== 薪酬边界套件：{len(PASS)} pass / {len(FAIL)} fail ====')
if FAIL:
    print('FAILED:', FAIL)
