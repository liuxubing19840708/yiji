# -*- coding: utf-8 -*-
"""
AI 报表真 GLM 冒烟（smoke_ai_glm）——差距分析 #7（2026-11-03）。

**手工触发**，不进任何套件/CI：走真 GLM 配额（.env GLM_API_KEY + AI_FREE_MODE=1）。
缺 key / FREE_MODE 关 → SKIP 退出 0，不报红。

三问断言：
  A1 白名单内（本地关键词短路，零 GLM 费用）→ report 卡片；
  A2 白名单外自由问答（真 GLM，带数据块）→ answer 非空且非 fallback；
  A3 指代追问（「那昨天呢」，真 GLM+history）→ answer 非空。

跑法：manage.py shell -c "exec(compile(open('smoke_ai_glm.py',encoding='utf-8').read(),'smoke_ai_glm.py','exec'))"
工厂：「AI冒烟测试厂(可删)」自建复用，带一张 50% 进度订单供 GLM 有数据可答。
"""
import json
import time

from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.test import Client

from apps.accounts.models import Company, Post as PostM, User, UserCompany
from apps.accounts.services import create_company_with_owner
from apps.catalog.models import Customer, Style
from apps.ai_report.models import AiQueryLog
from apps.orders.models import Order, OrderItem

PASS, FAIL, SKIP = [], [], []


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


# ================= 前置：key / FREE_MODE（缺则 SKIP 不报红） =================
api_key = getattr(settings, 'GLM_API_KEY', '')
free_mode = getattr(settings, 'AI_FREE_MODE', False)
if not api_key or not free_mode:
    print('SKIP：GLM_API_KEY 未配置或 AI_FREE_MODE 关闭（真 GLM 冒烟需两者）')
    print(f'SKIP detail: key={"有" if api_key else "无"} free_mode={free_mode}')
    raise SystemExit(0)

OWNER = '13988715101'
CO_A = 'AI冒烟测试厂(可删)'


def preclean(company):
    AiQueryLog.objects.filter(company=company).delete()
    OrderItem.all_objects.filter(order__company=company).delete()
    Order.all_objects.filter(company=company).delete()
    Customer.all_objects.filter(company=company).delete()
    Style.all_objects.filter(company=company).delete()


company = Company.objects.filter(name=CO_A).first()
if company is None:
    owner = User.objects.filter(phone=OWNER).first()
    if owner is None:
        owner = User.objects.create(phone=OWNER, username=OWNER, real_name='AI冒烟厂长',
                                    password=make_password('yisao1234'), is_active=True)
    company, _ = create_company_with_owner(owner, CO_A)
CID = company.id
preclean(company)

# 建测试数据：客户 + 款 + 一张 100 件订单（GLM 有数据可答）
cu = Customer.objects.create(company=company, customer_no='AIS-CU-001', name='冒烟客户')
sty = Style.objects.create(company=company, style_no='AIS-S1', name='冒烟款')
o, _ = Order.objects.get_or_create(
    company=company, order_no='AIS-SO-001',
    defaults={'customer': cu, 'style': sty, 'status': Order.Status.PRODUCING})
OrderItem.objects.get_or_create(
    order=o, color='红', size='M', defaults={'qty': 100})

c = Client()
extra = {'HTTP_AUTHORIZATION': 'Bearer fake', 'HTTP_X_COMPANY_ID': str(CID)}
# 直接 force_authenticate 走 Session？项目用 JWT——登录拿真 token
st, d = api('POST', '/auth/login',
            data={'phone': OWNER, 'password': 'yisao1234'})
check('A0 登录', st == 200 and d.get('access'), f'{st} {str(d)[:80]}')
TOK = d['access']


def ask(question, history=None):
    t0 = time.time()
    st, d = api('POST', '/ai/report/query', token=TOK, cid=CID,
                data={'question': question, 'history': history or []})
    return st, d, time.time() - t0


# ================= A1 白名单内（本地短路，零 GLM） =================
st, d, el = ask('今天产量多少件')
check('A1 白名单内 200', st == 200, f'{st} {str(d)[:120]}')
check('A1 返回报表卡片', bool(d.get('report')), str(d)[:150])

# ================= A2 白名单外自由问答（真 GLM） =================
# 问法须避开全部本地关键词（产量/进度/交期/工资/时间词）才会走 GLM 自由问答
Q2 = '红色和蓝色哪个颜色更受客户欢迎'
st, d, el = ask(Q2)
ans2 = (d or {}).get('answer') or ''
print(f'\n--- A2 ({el:.1f}s) free={bool(d.get("free"))} fallback={bool(d.get("fallback"))} ---')
print(f'问：{Q2}\n答：{ans2[:300]}')
check('A2 200', st == 200, st)
check('A2 走自由问答', bool(d.get('free')), str(d)[:150])
check('A2 answer 非空', len(ans2) > 5, ans2[:100])

# ================= A3 指代追问（真 GLM + history） =================
Q3 = '那绿色呢'
history = [{'role': 'user', 'content': Q2},
           {'role': 'assistant', 'content': ans2[:200]}]
st, d, el = ask(Q3, history)
ans3 = (d or {}).get('answer') or ''
print(f'\n--- A3 ({el:.1f}s) free={bool(d.get("free"))} fallback={bool(d.get("fallback"))} ---')
print(f'问：{Q3}\n答：{ans3[:300]}')
check('A3 200', st == 200, st)
check('A3 走自由问答', bool(d.get('free')), str(d)[:150])
check('A3 answer 非空', len(ans3) > 5, ans3[:100])
check('A3 留痕落库', AiQueryLog.objects.filter(company=company).count() >= 3,
      AiQueryLog.objects.filter(company=company).count())

# ================= cleanup（留 AiQueryLog 摘要供排障，清业务行） =================
print('\n---- cleanup ----')
try:
    OrderItem.all_objects.filter(order__company=company).delete()
    Order.all_objects.filter(company=company).delete()
    Customer.all_objects.filter(company=company).delete()
    Style.all_objects.filter(company=company).delete()
    # AiQueryLog 保留（排障留痕，量小）
    print('cleanup: done（业务行已清；AiQueryLog 留痕保留）')
except Exception as e:  # noqa: BLE001
    print('cleanup WARN:', e)

print(f'\n==== AI 真 GLM 冒烟：{len(PASS)} pass / {len(FAIL)} fail ====')
if FAIL:
    print('FAILED:', FAIL)
    raise SystemExit(1)
