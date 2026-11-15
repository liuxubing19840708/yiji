# -*- coding: utf-8 -*-
"""端到端冒烟测试（演示种子数据上运行）：manage.py shell < apps/core/smoke.py"""
import io
import os
import sys

# shell 直跑时修正 Windows 控制台编码；测试 runner 的 StringIO 没有 buffer。
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

# 隔离套件通过命名空间注入自建账号；独立 shell 仍可用环境变量或原演示账号。
OWNER_PHONE = globals().get(
    'SMOKE_OWNER_PHONE', os.environ.get('SMOKE_OWNER_PHONE', '13800000001'))
WORKER_PHONE = globals().get(
    'SMOKE_WORKER_PHONE', os.environ.get('SMOKE_WORKER_PHONE', '13800000003'))
TARGET_COMPANY_ID = globals().get(
    'SMOKE_COMPANY_ID', os.environ.get('SMOKE_COMPANY_ID'))

from django.test import Client  # noqa: E402


def show(label, resp, n=240, expected=(200,)):
    body = resp.content.decode('utf-8', errors='replace').replace('\n', ' ')
    print(f'{label}: {resp.status_code} {body[:n]}')
    assert resp.status_code in expected, f'{label}: HTTP {resp.status_code} {body[:n]}'


comp_c = Client()
comp = None
tok = comp_c.post('/api/auth/login', {'phone': OWNER_PHONE, 'password': 'yisao1234'},
                  content_type='application/json')
assert tok.status_code == 200, tok.content
comp = tok.json()['user']['company_links'] if 'company_links' in tok.json().get('user', {}) else None
# 直接从 companies/mine 拿公司（跳过真实厂 86：demo 账号可能被拉入真实厂，
# 冒烟有写操作，只能在演示/测试厂上跑）
mine = comp_c.get('/api/companies/mine',
                  HTTP_AUTHORIZATION=f"Bearer {tok.json()['access']}")
available_cids = [c.get('id') for c in (mine.json() or [])]
cid = (int(TARGET_COMPANY_ID) if TARGET_COMPANY_ID is not None
       else next((c['id'] for c in (mine.json() or []) if c.get('id') != 86), None))
assert cid and cid != 86 and cid in available_cids, '无可用非 86 公司，冒烟终止'
H = {'HTTP_AUTHORIZATION': f"Bearer {tok.json()['access']}", 'HTTP_X_COMPANY_ID': str(cid)}
print('company', cid)

# 员工登录
c = Client()
rt = c.post('/api/auth/login', {'phone': WORKER_PHONE, 'password': 'yisao1234'},
            content_type='application/json')
assert rt.status_code == 200, rt.content
me = c.get('/api/auth/me', HTTP_AUTHORIZATION=f"Bearer {rt.json()['access']}")
H2 = {'HTTP_AUTHORIZATION': f"Bearer {rt.json()['access']}", 'HTTP_X_COMPANY_ID': str(cid)}
uid = me.json().get('id')
print('me', me.status_code, 'uid', uid)

# 扫码解析
from apps.catalog.models import Ticket  # noqa: E402
t1 = Ticket.objects.filter(company_id=cid).first()
assert t1 is not None, '目标公司没有菲票，冒烟终止'
pr = c.post('/api/scan/parse', {'qr': t1.qr_code}, content_type='application/json', **H2)
show('scan/parse', pr, 200)

# 登记（票上第一道款式工序）
sp = t1.production.style.processes.select_related('process').first()
assert sp is not None, '目标菲票没有款式工序，冒烟终止'
r = c.post('/api/records', {'ticket_id': t1.id, 'process_id': sp.process_id, 'qty': 25},
           content_type='application/json', **H2)
show('create record', r, expected=(201,))
rid = r.json().get('id')

# 工作台（使用当前日期，避免冒烟随时间失效）
from django.utils import timezone  # noqa: E402
today = timezone.localdate()
month_start = today.replace(day=1)
w = c.get(f'/api/records/workbench?date_from={today}&date_to={today}', **H2)
show('workbench', w, 200)

# 撤销
assert rid, '登记未返回 id'
d = c.delete(f'/api/records/{rid}', **H2)
print('revoke:', d.status_code)
assert d.status_code == 204, f'revoke: HTTP {d.status_code} {d.content!r}'

# 我的工资
m = c.get(f'/api/salary/employee/{uid}?date_from={month_start}&date_to={today}', **H2)
show('my salary', m)

# 管理端：公司汇总 / 完成汇总 / 结算创建
s = comp_c.get(f'/api/salary/company?date_from={month_start}&date_to={today}', **H)
show('salary/company', s)
p = comp_c.get(f'/api/salary/summary/completion?date_from={month_start}&date_to={today}', **H)
show('completion', p)

# 生产：列表 / 订单工序 / 菲票
pl = comp_c.get('/api/productions?page=1&page_size=10', **H)
show('productions', pl, 160)
pid = pl.json()['results'][0]['id'] if pl.status_code == 200 and pl.json().get('results') else None
if pid:
    op = comp_c.get(f'/api/productions/{pid}/order-processes', **H)
    show('order-processes', op, 160)
    tb = comp_c.get(f'/api/productions/{pid}/bundles', **H)
    show('bundles', tb, 120)

# 员工 tab 参数（Agent A 报告的契约缺口验证）
e_all = comp_c.get('/api/employees?tab=all', **H)
e_pen = comp_c.get('/api/employees?tab=pending', **H)
print('employees tab=all:', e_all.status_code, 'tab=pending:', e_pen.status_code)
assert e_all.status_code == 200 and e_pen.status_code == 200, '员工 tab 查询失败'

# 日志 keyword 参数（Agent D 报告的契约缺口验证）
al = comp_c.get('/api/audit-logs?keyword=登记', **H)
print('audit-logs keyword:', al.status_code)
assert al.status_code == 200, f'audit-logs: HTTP {al.status_code}'

print('SMOKE DONE')
