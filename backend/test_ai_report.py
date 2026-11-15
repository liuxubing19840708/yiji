# -*- coding: utf-8 -*-
"""
AI 报表问答（test_ai_report）端到端后端验证 —— 2026-10-25 新功能。

覆盖：
  意图 AI-001~003：未登录 401 / 未配 key 回落快捷问题 / 白名单外回落
  校验 AI-010~011：空问题 400 / 超 200 字 400
  报表 AI-020~024：今日产量（件数/人数/产值）/ 本月产量（Top5）/ 订单进度（复用
                   orders 聚合口径 50%）/ 交期预警（黄+红进、远期不进）/ 个人薪酬（仅本人）
  留痕 AI-030：AiQueryLog 落库 ok=True
  频次 AI-040：超 100 次/日 429
  隔离 AI-050：他厂数据不进本厂报表

跑法：manage.py shell -c "exec(compile(open('test_ai_report.py',encoding='utf-8').read(),'test_ai_report.py','exec'))"
幂等：自建「AI测试厂(可删)」复用；每轮 preclean 硬清本厂业务行与问答留痕。
GLM 不真调：parse_intent 在测试内替换为桩函数（网络零依赖）。
"""
import json
from datetime import timedelta

from django.contrib.auth.hashers import make_password
from django.test import Client
from django.utils import timezone

from apps.accounts.models import Company, Post as PostM, User, UserCompany
from apps.accounts.services import create_company_with_owner
from apps.ai_report.models import AiQueryLog
from apps.catalog.models import Customer, Process, Style, Ticket
from apps.orders.models import Order, OrderItem
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


def ensure_member(u, company, post):
    uc = UserCompany.all_objects.filter(user=u, company=company).first()
    if uc is None:
        return UserCompany.objects.create(user=u, company=company, post=post,
                                          status=UserCompany.Status.ACTIVE)
    uc.is_deleted = False
    uc.status = UserCompany.Status.ACTIVE
    uc.post = post
    uc.save(update_fields=['is_deleted', 'status', 'post', 'updated_at'])
    return uc


OWNER, WORKER_A, WORKER_B = '13988711101', '13988711102', '13988711103'
CO_A = 'AI测试厂(可删)'


def preclean(company):
    """幂等自清扫：硬清本厂业务行与问答留痕（测试厂专属，真实厂零接触）。"""
    AiQueryLog.objects.filter(company=company).delete()
    Record.objects.filter(company=company).delete()
    Order.all_objects.filter(company=company).delete()
    OrderItem.all_objects.filter(order__company=company).delete()
    Ticket_m = Record.ticket.field.related_model
    Ticket_m.objects.filter(company=company, bed_no__startswith='TAI').delete()
    Production.objects.filter(company=company, bed_no__startswith='TAI').delete()
    Style.objects.filter(company=company, style_no__startswith='TAI').delete()
    Customer.objects.filter(company=company, customer_no__startswith='TAI').delete()
    Process.objects.filter(company=company, name__startswith='TAI').delete()


# ================= 建厂（复用） + preclean =================
company = Company.objects.filter(name=CO_A).first()
if company is None:
    owner = ensure_user(OWNER, 'AI厂长')
    company, _ = create_company_with_owner(owner, CO_A)
else:
    owner = ensure_user(OWNER, 'AI厂长')
CID = company.id
preclean(company)

post_plain = PostM.objects.filter(company=company, name='AI普工', is_deleted=False).first()
if post_plain is None:
    post_plain = PostM.objects.create(company=company, name='AI普工', permissions=[])
worker_a = ensure_user(WORKER_A, '张三')
worker_b = ensure_user(WORKER_B, '李四')
ensure_member(worker_a, company, post_plain)
ensure_member(worker_b, company, post_plain)

tok_a = login(WORKER_A)

# ================= 测试数据：今日/本月登记 + 订单 =================
style = Style.objects.create(company=company, style_no='TAI-S1', name='AI测试款',
                             customer=Customer.objects.create(
                                 company=company, customer_no='TAI-C1', name='AI客户'))
now = timezone.now()
order_hot = Order.objects.create(company=company, order_no='TAI-O1', style=style,
                                 customer=style.customer,
                                 delivery_date=(timezone.localdate() + timedelta(days=3)))
order_over = Order.objects.create(company=company, order_no='TAI-O2', style=style,
                                  delivery_date=(timezone.localdate() - timedelta(days=2)))
order_far = Order.objects.create(company=company, order_no='TAI-O3', style=style,
                                 delivery_date=(timezone.localdate() + timedelta(days=30)))
OrderItem.objects.create(order=order_hot, color='黑', size='M', qty=100, price=0)
OrderItem.objects.create(order=order_over, color='黑', size='M', qty=50, price=0)
# 测试用床次+菲票+工序（Record 必联 ticket/process，唯一约束=票×工序×人）
production = Production.objects.create(company=company, style=style, bed_no='TAI-B1',
                                       total_qty=100, customer=style.customer,
                                       order=order_hot)
ticket = Ticket.objects.create(company=company, production=production, bed_no='TAI-B1',
                               bundle_no='1', color='黑', size='M', qty=100,
                               qr_code='TAI|01|1|黑|M|100')
process_a = Process.objects.create(company=company, name='TAI车缝', default_price=1.5)
process_b = Process.objects.create(company=company, name='TAI锁边', default_price=1.0)
process_c = Process.objects.create(company=company, name='TAI后整', default_price=3.0)
# 张三今日 2 笔：40×1.5 + 10×2.0 = 80 元；李四今日 1 笔：20×1.0 = 20 元
recs = [
    Record(company=company, ticket=ticket, process=process_a, user=worker_a,
           qty=40, price=1.5, registered_at=now),
    Record(company=company, ticket=ticket, process=process_b, user=worker_a,
           qty=10, price=2.0, registered_at=now),
    Record(company=company, ticket=ticket, process=process_a, user=worker_b,
           qty=20, price=1.0, registered_at=now),
]
# 上月一笔（35 天前=9 月，不进今日也不进本月）：验证跨月口径切分
last_month = now - timedelta(days=35)
recs.append(Record(company=company, ticket=ticket, process=process_c, user=worker_a,
                   qty=5, price=3.0, registered_at=last_month))
Record.objects.bulk_create(recs)
# 昨日一笔（时间槽位用例）：李四 7×1.0 = 7 元
Record.objects.create(company=company, ticket=ticket, process=process_b, user=worker_b,
                      qty=7, price=1.0,
                      registered_at=now - timedelta(days=1))

print('=== 意图与校验 ===')

# AI-001 未登录
code, _ = api('POST', '/ai/report/query', data={'question': '今日产量'})
check('AI-001 未登录 401', code == 401, f'code={code}')

# AI-002 未配 key（显式清掉 settings，防 .env 真值泄漏进测试）→ 快捷问题回落
from django.conf import settings as dj_settings  # noqa: E402
from apps.ai_report import services  # noqa: E402
_orig_parse = services.parse_intent
_orig_free = services.free_answer
_orig_key = dj_settings.GLM_API_KEY
_orig_free_mode = dj_settings.AI_FREE_MODE
services.parse_intent = lambda *a, **k: None
dj_settings.GLM_API_KEY = ''
code, d = api('POST', '/ai/report/query', tok_a, CID, {'question': '随便聊聊'})
check('AI-002 无key回落快捷问题', code == 200 and d.get('fallback') is True
      and len(d.get('quick_questions', [])) == 5, f'code={code} keys={list(d)}')

# AI-003 白名单外 + 自由问答失败（返回 None）→ 快捷问题回落
services.parse_intent = lambda *a, **k: {'report_key': ''}
services.free_answer = lambda *a, **k: None
dj_settings.GLM_API_KEY = _orig_key
dj_settings.AI_FREE_MODE = True
code, d = api('POST', '/ai/report/query', tok_a, CID, {'question': '讲个笑话'})
check('AI-003 白名单外且自由失败回落', code == 200 and d.get('fallback') is True, f'code={code}')

# AI-004 自由问答成功（测试阶段 AI_FREE_MODE）→ {free, answer}
services.free_answer = lambda *a, **k: '自由回答内容（测试桩）'
code, d = api('POST', '/ai/report/query', tok_a, CID, {'question': '讲个笑话'})
check('AI-004 自由问答模式', code == 200 and d.get('fallback') is False
      and d.get('free') is True and d.get('answer') == '自由回答内容（测试桩）',
      f'code={code} d={d}')

# AI-005 自由问答不生效（AI_FREE_MODE=0）→ 回落
dj_settings.AI_FREE_MODE = False
code, d = api('POST', '/ai/report/query', tok_a, CID, {'question': '讲个笑话'})
check('AI-005 关闭自由模式回落', code == 200 and d.get('fallback') is True, f'code={code}')
dj_settings.AI_FREE_MODE = True

# AI-006 本地短路：常见问法不调 GLM（parse_intent 桩留调用痕迹验证未触发）
_parse_calls = []
services.parse_intent = lambda *a, **k: (_parse_calls.append(1),
                                         {'report_key': ''})[1]
code, d = api('POST', '/ai/report/query', tok_a, CID, {'question': '今日产量多少'})
check('AI-006 本地短路零GLM', code == 200 and d.get('fallback') is False
      and (d.get('report') or {}).get('title') == '今日产量'
      and not _parse_calls, f'code={code} calls={len(_parse_calls)}')

# AI-007/008 时间槽位 + history 透传：本地解不了的问法走 GLM，date_range/history 透传
_cap = {}
def _cap_parse(question, history=None):
    _cap['question'] = question
    _cap['history'] = history
    return {'report_key': 'today_output', 'date_range': 'yesterday'}
services.parse_intent = _cap_parse
_hist = [{'role': 'user', 'content': '今日产量多少'},
         {'role': 'ai', 'content': '今日产量：70 件'},
         {'role': 'user', 'content': 'x' * 300}]
code, d = api('POST', '/ai/report/query', tok_a, CID,
              {'question': '前一天的数据', 'history': _hist})
r = d.get('report') or {}
check('AI-007 时间槽位昨日产量', code == 200 and d.get('fallback') is False
      and r.get('title') == '昨日产量'
      and any('7 件' in str(row) for row in r.get('rows', []))
      and any('7.00' in str(row) for row in r.get('rows', [])),
      f'code={code} title={r.get("title")} rows={r.get("rows")}')
check('AI-008 history清洗透传', _cap.get('history') is not None
      and len(_cap['history']) == 3
      and all(len(t['content']) <= 200 for t in _cap['history'])
      and _cap['history'][-1]['content'] == 'x' * 200
      and _cap['history'][1]['role'] == 'ai',
      f'history={_cap.get("history")}')

# AI-009 本地工资规则收紧：「本月总共工资」是全厂问法 → 不劫持，走 GLM(空)→自由问答
_parse_calls2 = []
services.parse_intent = lambda *a, **k: (_parse_calls2.append(1),
                                         {'report_key': ''})[1]
services.free_answer = lambda *a, **k: '全厂本月工资回答（测试桩）'
code, d = api('POST', '/ai/report/query', tok_a, CID, {'question': '本月总共工资'})
check('AI-009 全厂工资不劫持', code == 200 and d.get('fallback') is False
      and d.get('free') is True and '全厂' in (d.get('answer') or '')
      and len(_parse_calls2) == 1,
      f'code={code} d={d} calls={len(_parse_calls2)}')

# AI-010 「我的工资」仍走 my_salary（本人问法保留本地短路）
services.parse_intent = lambda *a, **k: {'report_key': ''}
code, d = api('POST', '/ai/report/query', tok_a, CID, {'question': '我的工资'})
check('AI-010 本人工资仍短路', code == 200 and d.get('fallback') is False
      and (d.get('report') or {}).get('title') == '我的薪酬摘要',
      f'code={code} d={d}')

# AI-011 岗位/角色问题 → 白名单外 → 自由问答（不进薪酬卡片）
code, d = api('POST', '/ai/report/query', tok_a, CID, {'question': '我是什么岗位'})
check('AI-011 岗位问题走自由问答', code == 200 and d.get('fallback') is False
      and d.get('free') is True,
      f'code={code} d={d}')

# AI-010/011 参数校验
code, _ = api('POST', '/ai/report/query', tok_a, CID, {'question': '  '})
check('AI-010 空问题 400', code == 400, f'code={code}')
code, _ = api('POST', '/ai/report/query', tok_a, CID, {'question': '问' * 201})
check('AI-011 超200字 400', code == 400, f'code={code}')

print('=== 五张报表 ===')

# AI-020 今日产量：3 笔 / 70 件 / 2 人 / 100.00 元
services.parse_intent = lambda *a, **k: {'report_key': 'today_output'}
code, d = api('POST', '/ai/report/query', tok_a, CID, {'question': '今日产量多少'})
r = d.get('report') or {}
check('AI-020 今日产量汇总', code == 200 and d.get('fallback') is False
      and r.get('title') == '今日产量'
      and any('70 件' in str(row) for row in r.get('rows', []))
      and any('100.00' in str(row) for row in r.get('rows', []))
      and any('2 人' in str(row) for row in r.get('rows', [])),
      f'code={code} rows={r.get("rows")}')

# AI-021 本月产量：张三 Top1（55 件 / 95.00 元），上月 35 天前同月边界的兼容
services.parse_intent = lambda *a, **k: {'report_key': 'month_output'}
code, d = api('POST', '/ai/report/query', tok_a, CID, {'question': '本月产量'})
r = d.get('report') or {}
top_row = next((row for row in r.get('rows', []) if '张三' in str(row[0])), None)
check('AI-021 本月Top榜含张三', code == 200 and top_row is not None,
      f'code={code} rows={r.get("rows")}')

# AI-022 订单进度：TAI-O1 0%（100/0）、生产中单出现、已完成/远期不混入
services.parse_intent = lambda *a, **k: {'report_key': 'order_progress'}
code, d = api('POST', '/ai/report/query', tok_a, CID, {'question': '订单进度'})
r = d.get('report') or {}
nos = [row[0] for row in r.get('rows', [])]
check('AI-022 订单进度生产中单', code == 200 and 'TAI-O1' in nos and 'TAI-O2' in nos
      and 'TAI-O3' in nos, f'code={code} nos={nos}')

# AI-023 交期预警：剩3天(TAI-O1) + 已过期(TAI-O2) 进；30天后(TAI-O3) 不进
services.parse_intent = lambda *a, **k: {'report_key': 'deadline_alert'}
code, d = api('POST', '/ai/report/query', tok_a, CID, {'question': '交期预警'})
r = d.get('report') or {}
alert_rows = {row[0]: row for row in r.get('rows', [])}
check('AI-023 交期预警黄红进远期不进',
      code == 200 and 'TAI-O1' in alert_rows and 'TAI-O2' in alert_rows
      and 'TAI-O3' not in alert_rows
      and any('剩' in alert_rows['TAI-O1'][4] for _ in [0])
      and '已过期' in alert_rows['TAI-O2'][4],
      f'code={code} rows={r.get("rows")}')

# AI-024 个人薪酬：仅本人（张三）——本月 80.00（上月 15 元不计入）/ 今日 80.00
services.parse_intent = lambda *a, **k: {'report_key': 'my_salary'}
code, d = api('POST', '/ai/report/query', tok_a, CID, {'question': '我这个月工资'})
r = d.get('report') or {}
check('AI-024 本人薪酬摘要', code == 200 and r.get('title') == '我的薪酬摘要'
      and any('80.00' in str(row) for row in r.get('rows', []))
      and '80.00' in (r.get('summary') or '')
      and '95.00' not in (r.get('summary') or ''),
      f'code={code} summary={r.get("summary")} rows={r.get("rows")}')

print('=== 留痕 / 频次 / 隔离 ===')

# AI-030 留痕：ok=True（最后一次成功提问）
last = AiQueryLog.objects.filter(company=company, user=worker_a).order_by('-id').first()
check('AI-030 问答留痕落库', last is not None and last.ok is True
      and (last.intent or {}).get('report_key') == 'my_salary'
      and (last.intent or {}).get('date_range') == 'this_month',
      f'last={last} ok={getattr(last, "ok", None)}')

# AI-040 频次：当日已 100 次 → 429（先清掉本用例前产生的留痕再灌满）
AiQueryLog.objects.filter(company=company, user=worker_a).delete()
AiQueryLog.objects.bulk_create([
    AiQueryLog(company=company, user=worker_a, question='x', intent=None, ok=False)
    for _ in range(100)])
services.parse_intent = lambda *a, **k: {'report_key': 'today_output'}
code, _ = api('POST', '/ai/report/query', tok_a, CID, {'question': '今日产量'})
check('AI-040 超100次/日 429', code == 429, f'code={code}')
AiQueryLog.objects.filter(company=company, user=worker_a).delete()

# AI-050 他厂隔离：B 厂（真实厂之外另建）查不到 A 厂数据
services.parse_intent = lambda *a, **k: {'report_key': 'today_output'}
code_b, d_b = api('POST', '/ai/report/query', tok_a, CID + 999999, {'question': '今日产量'})
check('AI-050 无效公司上下文 4xx', 400 <= code_b < 500, f'code={code_b}')

services.parse_intent = _orig_parse
services.free_answer = _orig_free
dj_settings.GLM_API_KEY = _orig_key
dj_settings.AI_FREE_MODE = _orig_free_mode

# ================= 方案 B 按需选块：select_and_build 数据单测（零 GLM） =================
print('=== 方案 B 选块数据单测 ===')
from apps.ai_report import data_pack

owner_uc = UserCompany.objects.filter(user=owner, company=company).first()
owner_is_admin = bool(owner_uc and owner_uc.post and owner_uc.post.is_admin)
check('AI-060 厂长岗 is_admin=True', owner_is_admin,
      f'uc={owner_uc} post={getattr(owner_uc, "post", None)}')

# 产量块：今日 70 件 / 100 元（40*1.5+10*2+20*1）——data 按块名嵌套
pb = data_pack.select_and_build('output', company, worker_a, False, '今日产量', 'today')
t_out = (pb or {}).get('data', {}).get('output', {}).get('today', {})
check('AI-061 产量块今日聚合', t_out.get('qty') == 70 and t_out.get('amount') == '100.00',
      f'today={t_out}')
check('AI-062 产量块区间标签', (pb or {}).get('data', {}).get('output', {})
      .get('asked_range', {}).get('label'),
      f'asked_range={(pb or {}).get("data", {}).get("output", {}).get("asked_range")}')

# 订单块：3 张测试单 + 交期预警含 TAI-O2（已过期）
ob = data_pack.select_and_build('orders', company, worker_a, False, '订单进度', 'today')
obd = (ob or {}).get('data', {}).get('orders', {})
nos = [r['order_no'] for r in obd.get('orders_recent', [])]
dl = [r['order_no'] for r in obd.get('deadline_orders', [])]
check('AI-063 订单块含3张单', set(nos) >= {'TAI-O1', 'TAI-O2', 'TAI-O3'}, f'nos={nos}')
check('AI-064 交期预警含过期单', 'TAI-O2' in dl, f'deadline={dl}')

# my_info 块：提问人本人岗位+薪酬（worker_a 今日 80 元）
mb = data_pack.select_and_build('my_info', company, worker_a, False, '我是谁', 'today')
mi = (mb or {}).get('data', {}).get('my_info', {})
check('AI-065 my_info本人薪酬', mi.get('my_salary', {}).get('today_amount') == '80.00',
      f'data={mi}')

# 敏感块门禁：普工查 finance → denied；厂长查 finance → 正常返回
fb_w = data_pack.select_and_build('finance', company, worker_a, False, '应收多少', 'today')
check('AI-066 普工敏感块denied',
      (fb_w or {}).get('data', {}).get('finance', {}).get('denied'),
      f'data={fb_w}')
fb_a = data_pack.select_and_build('finance', company, owner, owner_is_admin,
                                  '应收多少', 'today')
check('AI-067 厂长敏感块放行',
      'denied' not in (fb_a or {}).get('data', {}).get('finance', {}),
      f'data={fb_a}')

# 实体精查：问句点名 11 位数字单号 → order_detail 命中（正则 \d{8}\d{3}）
Order.objects.filter(company=company, order_no='20261025001').delete()
order_num = Order.objects.create(company=company, order_no='20261025001', style=style,
                                 customer=style.customer,
                                 delivery_date=(timezone.localdate() + timedelta(days=3)))
production2 = Production.objects.create(company=company, style=style, bed_no='TAI-B2',
                                        total_qty=50, customer=style.customer,
                                        order=order_num)
ticket2 = Ticket.objects.create(company=company, production=production2,
                                bed_no='TAI-B2', bundle_no='1', color='黑', size='M',
                                qty=50, qr_code='TAI|02|1|黑|M|50')
eb = data_pack.select_and_build('orders', company, worker_a, False,
                                f'订单{order_num.order_no}做到哪了', 'today')
ents = (eb or {}).get('matched_entities', {})
check('AI-068 实体精查order_detail+菲票联动',
      ents.get('order_detail', {}).get('order_no') == '20261025001'
      and ents.get('order_ticket_count') == 1,
      f'entities={ents}')

# 实体精查：点名员工（厂长查张三）→ employee_detail
from apps.accounts.models import User as UserModel
real_a = UserModel.objects.get(phone=WORKER_A).real_name
ed = data_pack.select_and_build('employees', company, owner, owner_is_admin,
                                f'{real_a}这个月干了多少', 'today')
check('AI-069 厂长查员工实体', (ed or {}).get('matched_entities', {})
      .get('employee_detail', {}).get('name') == real_a,
      f'entities={(ed or {}).get("matched_entities")}')

# 未知块 → None（模型选错 key 时安全回落）
check('AI-070 未知块None', data_pack.select_and_build(
    'no_such_block', company, worker_a, False, 'x', 'today') is None)

# 注册表完整性：13 块、admin 块全标注
reg = {k: a for k, _d, a in data_pack.BLOCK_REGISTRY}
check('AI-071 注册表13块', len(data_pack.BLOCK_REGISTRY) == 13,
      f'n={len(data_pack.BLOCK_REGISTRY)}')
check('AI-072 敏感块标注正确',
      all(reg[k] for k in ('employees', 'stock', 'cost', 'finance', 'purchase',
                           'shipment', 'settlement', 'quotation'))
      and not any(reg[k] for k in ('output', 'my_info', 'orders', 'beds', 'tickets')),
      f'reg={reg}')

# ================= 高/中缺陷修复回归：时间槽位/双块/代词兜底/缓存 =================
print('=== 缺陷修复回归 ===')
from apps.ai_report.services import resolve_range as _rr

today = timezone.localdate()
monday = today - timedelta(days=today.weekday())
s, e, lb = _rr('last_week')
check('AI-073 上周槽位', (s, e, lb) == (monday - timedelta(days=7),
                                       monday - timedelta(days=1), '上周'),
      f'{s}~{e} {lb}')
s2, e2, lb2 = _rr('before_yesterday')
check('AI-074 前天槽位', (s2, e2, lb2) == (today - timedelta(days=2),
                                           today - timedelta(days=2), '前天'),
      f'{s2} {lb2}')

mkb = data_pack.select_and_build(['orders', 'tickets'], company, worker_a, False,
                                 '订单和菲票', 'today')
check('AI-075 双块选择', set((mkb or {}).get('blocks', [])) == {'orders', 'tickets'}
      and set((mkb or {}).get('data', {}).keys()) == {'orders', 'tickets'},
      f'blocks={(mkb or {}).get("blocks")}')
mkb2 = data_pack.select_and_build(['orders', 'nope', 'tickets', 'cost'], company,
                                  worker_a, False, 'x', 'today')
check('AI-076 未知块过滤+封顶2', (mkb2 or {}).get('blocks') == ['orders', 'tickets'],
      f'blocks={(mkb2 or {}).get("blocks")}')

eb2 = data_pack.select_and_build('orders', company, worker_a, False,
                                 '那它做到哪了', 'today',
                                 extra_text='订单20261025001做到哪了')
check('AI-077 代词追问实体兜底',
      (eb2 or {}).get('matched_entities', {}).get('order_detail', {}).get('order_no')
      == '20261025001',
      f'entities={(eb2 or {}).get("matched_entities")}')

# 缓存 + meta 回填（桩 _glm_chat 数调用次数；一跳=选块JSON，二跳=答案文本）
_k0, _f0 = dj_settings.GLM_API_KEY, dj_settings.AI_FREE_MODE
dj_settings.GLM_API_KEY, dj_settings.AI_FREE_MODE = 'fake', True
_orig_glm = services._glm_chat
_calls = {'n': 0}


def _fake_glm(messages, **kw):
    _calls['n'] += 1
    return ('{"blocks":["tickets"],"date_range":"today"}'
            if '数据路由器' in messages[0]['content'] else '缓存测试答案')


services._glm_chat = _fake_glm
services._FREE_CACHE.clear()
m1, m2 = {}, {}
a1 = services.free_answer('菲票一共多少张', company, worker_a, False, None, 'today', meta=m1)
a2 = services.free_answer('菲票一共多少张', company, worker_a, False, None, 'today', meta=m2)
check('AI-078 同问60s缓存命中', a1 == '缓存测试答案' and a2 == a1
      and _calls['n'] == 2 and m1.get('blocks') == ['tickets']
      and m2.get('cached') is True,
      f'n={_calls["n"]} m1={m1} m2={m2}')
services._FREE_CACHE.clear()
services._glm_chat = _orig_glm
dj_settings.GLM_API_KEY, dj_settings.AI_FREE_MODE = _k0, _f0

print(f'\n=== AI报表问答测试完成：PASS {len(PASS)} / FAIL {len(FAIL)} ===')
if FAIL:
    print('失败项:', FAIL)
    raise SystemExit(1)
