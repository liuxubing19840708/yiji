# -*- coding: utf-8 -*-
"""端到端冒烟测试（演示种子数据上运行）：manage.py shell < apps/core/smoke.py"""
import io
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from django.test import Client  # noqa: E402


def show(label, resp, n=240):
    body = resp.content.decode('utf-8', errors='replace').replace('\n', ' ')
    print(f'{label}: {resp.status_code} {body[:n]}')


comp_c = Client()
comp = None
tok = comp_c.post('/api/auth/login', {'phone': '13800000001', 'password': 'yisao1234'},
                  content_type='application/json')
assert tok.status_code == 200, tok.content
comp = tok.json()['user']['company_links'] if 'company_links' in tok.json().get('user', {}) else None
# 直接从 companies/mine 拿公司（跳过真实厂 86：demo 账号可能被拉入真实厂，
# 冒烟有写操作，只能在演示/测试厂上跑）
mine = comp_c.get('/api/companies/mine',
                  HTTP_AUTHORIZATION=f"Bearer {tok.json()['access']}")
cid = next((c['id'] for c in (mine.json() or []) if c.get('id') != 86), None)
assert cid, '无可用非 86 公司，冒烟终止'
H = {'HTTP_AUTHORIZATION': f"Bearer {tok.json()['access']}", 'HTTP_X_COMPANY_ID': str(cid)}
print('company', cid)

# 员工登录
c = Client()
rt = c.post('/api/auth/login', {'phone': '13800000003', 'password': 'yisao1234'},
            content_type='application/json')
assert rt.status_code == 200, rt.content
me = c.get('/api/auth/me', HTTP_AUTHORIZATION=f"Bearer {rt.json()['access']}")
H2 = {'HTTP_AUTHORIZATION': f"Bearer {rt.json()['access']}", 'HTTP_X_COMPANY_ID': str(cid)}
uid = me.json().get('id')
print('me', me.status_code, 'uid', uid)

# 扫码解析
from apps.catalog.models import Ticket  # noqa: E402
t1 = Ticket.objects.filter(company_id=cid).first()
pr = c.post('/api/scan/parse', {'qr': t1.qr_code}, content_type='application/json', **H2)
show('scan/parse', pr, 200)

# 登记（票上第一道款式工序）
sp = t1.production.style.processes.select_related('process').first()
r = c.post('/api/records', {'ticket_id': t1.id, 'process_id': sp.process_id, 'qty': 25},
           content_type='application/json', **H2)
show('create record', r)
rid = r.json().get('id')

# 工作台
w = c.get('/api/records/workbench?date_from=2026-10-15&date_to=2026-10-15', **H2)
show('workbench', w, 200)

# 撤销
if rid:
    d = c.delete(f'/api/records/{rid}', **H2)
    print('revoke:', d.status_code)

# 我的工资
m = c.get(f'/api/salary/employee/{uid}?date_from=2026-10-01&date_to=2026-10-31', **H2)
show('my salary', m)

# 管理端：公司汇总 / 完成汇总 / 结算创建
s = comp_c.get('/api/salary/company?date_from=2026-10-01&date_to=2026-10-31', **H)
show('salary/company', s)
p = comp_c.get('/api/salary/summary/completion?date_from=2026-10-01&date_to=2026-10-31', **H)
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

# 日志 keyword 参数（Agent D 报告的契约缺口验证）
al = comp_c.get('/api/audit-logs?keyword=登记', **H)
print('audit-logs keyword:', al.status_code)

print('SMOKE DONE')
