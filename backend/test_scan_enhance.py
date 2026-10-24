# -*- coding: utf-8 -*-
"""
SA3 扫菲增强后端验证（测试方案 §3.3 SCAN-001~031，≥25 断言）：
- SCAN-001~003 工序选择器：registered_qty（该扎该工序全员累计）/ cap（扎数）
- SCAN-010~015 GET /tickets/lookup：手输码候选票（命中唯一 / 多候选 / 空参 400 /
  无权限 403 / 跨公司 404 / 0 命中 404 / 派生菲票状态）
- SCAN-020~023 workbench my_today_amount：本人今日 Σqty×price（两位小数字符串、
  无登记 0.00、hide_scan_price 开关不影响本人、跨日清零）
- SCAN-030~031 满扎口径：补登到恰好满扎仍可（增量补登）、满扎后选择器仍返回
  软提示徽标、超限登记 400（既有硬限）；cap=0（无限扎）→ null
跑法：manage.py shell -c "exec(compile(open('test_scan_enhance.py',encoding='utf-8').read(),'test_scan_enhance.py','exec'))"
幂等：自建「扫菲增强测试厂(可删)」并跨次复用（Company.creator PROTECT 不可删）；
业务行每次 queryset.delete() 物理清理（BaseModel 仅 instance.delete 软删，
queryset.delete 走 Collector 物理删，不残留软删行撞唯一约束）。
"""
import json
from datetime import timedelta

from django.contrib.auth.hashers import make_password
from django.test import Client
from django.utils import timezone

from apps.accounts.models import Company, Post as PostM, User, UserCompany
from apps.accounts.services import create_company_with_owner
from apps.catalog.models import Process, Style, StyleProcess, Ticket
from apps.ops.models import AuditLog, CompanySetting
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


OWNER, H_W1, H_W2, H_PLAIN, H_AMT, H_OWNER_B = (
    '13988910001', '13988910002', '13988910003', '13988910004',
    '13988910005', '13988910006')
CO_A, CO_B = '扫菲增强测试厂(可删)', '扫菲增强测试厂B(可删)'


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


def wipe_business(company):
    """物理清理该公司全部测试业务行（queryset.delete=Collector 物理删）。"""
    Record.objects.filter(company=company).delete()
    Ticket.all_objects.filter(company=company).delete()
    Production.all_objects.filter(company=company).delete()
    StyleProcess.all_objects.filter(style__company=company).delete()
    Style.all_objects.filter(company=company).delete()
    Process.all_objects.filter(company=company, name__startswith='SCAN').delete()
    AuditLog.objects.filter(company=company).delete()


# ================= preclean：复用测试厂 =================
company = Company.objects.filter(name=CO_A).first()
if company is None:
    owner = ensure_user(OWNER, '扫菲增强厂长')
    company, admin_post = create_company_with_owner(owner, CO_A)
else:
    owner = User.objects.get(phone=OWNER)
    admin_post = PostM.objects.get(company=company, is_admin=True, is_deleted=False)
CID = company.id

company_b = Company.objects.filter(name=CO_B).first()
if company_b is None:
    owner_b = ensure_user(H_OWNER_B, 'B厂厂长')
    company_b, _ = create_company_with_owner(owner_b, CO_B)
else:
    owner_b = ensure_user(H_OWNER_B, 'B厂厂长')
CID_B = company_b.id

wipe_business(company)

# 岗位与成员（每轮强制复位，保证断言前提稳定）
post_scan = PostM.objects.filter(company=company, name='SCAN登记岗',
                                 is_deleted=False).first()
if post_scan is None:
    post_scan = PostM.objects.create(company=company, name='SCAN登记岗',
                                     permissions=['records.create'])
else:
    PostM.objects.filter(pk=post_scan.pk).update(permissions=['records.create'])
    post_scan.refresh_from_db()
post_plain = PostM.objects.filter(company=company, name='SCAN普工',
                                  is_deleted=False).first() \
    or PostM.objects.create(company=company, name='SCAN普工', permissions=[])

w1 = ensure_user(H_W1, '扫描工一')
w2 = ensure_user(H_W2, '扫描工二')
plain = ensure_user(H_PLAIN, '扫描无权员')
amt_user = ensure_user(H_AMT, '扫描金额员')
ensure_member(w1, company, post_scan)
ensure_member(w2, company, post_scan)
ensure_member(plain, company, post_plain)
ensure_member(amt_user, company, post_plain)  # 工作台读取无权限门槛，普通成员即可
ensure_member(owner_b, company_b,
              PostM.objects.get(company=company_b, is_admin=True, is_deleted=False))

T = {
    'owner': login(OWNER), 'w1': login(H_W1), 'w2': login(H_W2),
    'plain': login(H_PLAIN), 'amt': login(H_AMT), 'owner_b': login(H_OWNER_B),
}
check('P0 测试厂成员登录', all(T.values()), str({k: bool(v) for k, v in T.items()}))

# ================= 最小业务链：款式→床次→菲票 =================
style = Style.objects.create(company=company, style_no='SCAN-A1', name='扫菲增强测试款')
proc = Process.objects.create(company=company, name='SCAN车缝', is_fixed=True,
                              default_price=2.5, sort=99)
StyleProcess.objects.create(style=style, process=proc, price=2.5, sort=1)
prod = Production.objects.create(company=company, style=style, bed_no='SCANB1',
                                 total_qty=150)
prod2 = Production.objects.create(company=company, style=style, bed_no='SCANB9',
                                  total_qty=10, is_cut_done=True)
ticket1 = Ticket.objects.create(company=company, production=prod, bed_no='SCANB1',
                                bundle_no='1', color='黑', size='M', qty=100)
ticket2 = Ticket.objects.create(company=company, production=prod, bed_no='SCANB1',
                                bundle_no='2', color='白', size='L', qty=50)
ticket0 = Ticket.objects.create(company=company, production=prod2, bed_no='SCANB9',
                                bundle_no='9', color='灰', size='S', qty=0)
# 金额员专用票：(票, 工序, 员工) 唯一约束下，昨日/今日两条记录需落在不同票上
prod3 = Production.objects.create(company=company, style=style, bed_no='SCANB2',
                                  total_qty=50)
ticket3 = Ticket.objects.create(company=company, production=prod3, bed_no='SCANB2',
                                bundle_no='1', color='蓝', size='XL', qty=50)

# ================= SCAN-001~003 工序选择器 registered_qty/cap =================
# 两人分别登记同一扎同一工序（全员累计口径的数据前提）
st, d = api('POST', '/records', T['w1'], CID,
            {'ticket_id': ticket1.id, 'process_id': proc.id, 'qty': 30})
check('SCAN-002pre w1 登记 30 件(201)', st == 201, f'{st} {json.dumps(d, ensure_ascii=False)[:120]}')
st, d = api('POST', '/records', T['w2'], CID,
            {'ticket_id': ticket1.id, 'process_id': proc.id, 'qty': 20})
check('SCAN-002pre w2 登记 20 件(201)', st == 201, f'{st}')

st, d = api('GET', f'/records/process-selector?ticket_id={ticket1.id}', T['plain'], CID)
results = (d or {}).get('results', []) if isinstance(d, dict) else []
mine = next((r for r in results if r.get('id') == proc.id), None)
check('SCAN-001a 选择器 200 且多工序可选', st == 200 and len(results) >= 2,
      f'{st} n={len(results)}')
check('SCAN-001b 每工序含 registered_qty/cap/price 字段',
      bool(results) and all(
          {'registered_qty', 'cap', 'price'} <= set(r) for r in results),
      json.dumps(results[:1], ensure_ascii=False)[:150])
check('SCAN-002a registered_qty=全员累计 50（30+20）',
      mine is not None and mine['registered_qty'] == 50, str(mine))
st2, d2 = api('GET', f'/records/process-selector?ticket_id={ticket1.id}&user_id={w1.id}',
              T['plain'], CID)
mine2 = next((r for r in (d2.get('results', []) if isinstance(d2, dict) else [])
              if r.get('id') == proc.id), None)
check('SCAN-002b 指定 user_id 视角仍是全员累计 50（非仅本人 30）',
      st2 == 200 and mine2 is not None and mine2['registered_qty'] == 50, str(mine2))
other = next((r for r in results if r.get('id') != proc.id), None)
check('SCAN-003 未登记工序 registered_qty=0 且 cap=扎数 100',
      other is not None and other['registered_qty'] == 0 and other['cap'] == 100,
      str(other))

# ================= SCAN-010~015 手输码兜底 /tickets/lookup =================
st, d = api('GET', f'/tickets/lookup?bed_no=SCANB1&bundle_no=1', T['owner'], CID)
rows = (d or {}).get('results', []) if isinstance(d, dict) else []
check('SCAN-010 床次+扎号精确命中唯一', st == 200 and (d or {}).get('count') == 1
      and len(rows) == 1, f'{st} {json.dumps(d, ensure_ascii=False)[:150]}')
r0 = rows[0] if rows else {}
check('SCAN-010b 候选字段齐全（id/床次/扎号/款号/色码/尺码/数量/状态）',
      r0.get('id') == ticket1.id and r0.get('bed_no') == 'SCANB1'
      and r0.get('bundle_no') == '1' and r0.get('style_no') == 'SCAN-A1'
      and r0.get('color') == '黑' and r0.get('size') == 'M'
      and r0.get('qty') == 100 and r0.get('status') == 'normal'
      and bool(r0.get('status_display')),
      json.dumps(r0, ensure_ascii=False)[:200])
st, d = api('GET', '/tickets/lookup?bed_no=SCANB1', T['owner'], CID)
check('SCAN-011 仅床次查询返回多候选列表（同床 2 扎）',
      st == 200 and (d or {}).get('count') == 2
      and {r['bundle_no'] for r in d.get('results', [])} == {'1', '2'},
      f'{st} {json.dumps(d, ensure_ascii=False)[:150]}')
st, d = api('GET', '/tickets/lookup?bundle_no=2', T['owner'], CID)
check('SCAN-011b 仅扎号查询返回候选列表（跨床口径）',
      st == 200 and (d or {}).get('count') == 1
      and d.get('results', [{}])[0].get('bed_no') == 'SCANB1',
      f'{st} {json.dumps(d, ensure_ascii=False)[:150]}')
st, d = api('GET', '/tickets/lookup', T['owner'], CID)
check('SCAN-012 空参 400', st == 400, f'{st} {json.dumps(d, ensure_ascii=False)[:100]}')
st, d = api('GET', '/tickets/lookup?bed_no=&bundle_no=', T['owner'], CID)
check('SCAN-012b 全空白参数 400', st == 400, f'{st}')
st, d = api('GET', '/tickets/lookup?bed_no=SCANB1&bundle_no=1', T['plain'], CID)
check('SCAN-013 无 records.create 权限 403', st == 403, f'{st} {d}')
st, d = api('GET', '/tickets/lookup?bed_no=SCANB1&bundle_no=1', T['owner_b'], CID_B)
check('SCAN-013b 跨公司查询他厂菲票 404', st == 404, f'{st} {d}')
st, d = api('GET', '/tickets/lookup?bed_no=SCANB1&bundle_no=999', T['owner'], CID)
check('SCAN-014 0 命中 404', st == 404, f'{st} {d}')

# 派生菲票状态：锁定床次 / 已裁完床次
Production.objects.filter(pk=prod.pk).update(is_locked=True)
st, d = api('GET', '/tickets/lookup?bed_no=SCANB1&bundle_no=1', T['owner'], CID)
st_locked = (d.get('results') or [{}])[0].get('status') if isinstance(d, dict) else None
check('SCAN-015a 锁定床次菲票 status=locked', st == 200 and st_locked == 'locked',
      f'{st} {st_locked}')
Production.objects.filter(pk=prod.pk).update(is_locked=False)
st, d = api('GET', '/tickets/lookup?bed_no=SCANB9&bundle_no=9', T['owner'], CID)
st_cut = (d.get('results') or [{}])[0].get('status') if isinstance(d, dict) else None
check('SCAN-015b 裁完床次菲票 status=cut_done', st == 200 and st_cut == 'cut_done',
      f'{st} {st_cut}')

# ================= SCAN-020~023 workbench my_today_amount =================
st, d = api('GET', '/records/workbench', T['amt'], CID)
check('SCAN-021 无登记时 my_today_amount=「0.00」',
      st == 200 and (d or {}).get('my_today_amount') == '0.00',
      f"{st} {json.dumps(d, ensure_ascii=False)[:120]}")
# 昨日记录：不计入今日金额（跨日清零口径）；落在 ticket2，避开 (票,工序,员工) 唯一约束
Record.objects.create(company=company, ticket=ticket2, process=proc, user=amt_user,
                      qty=10, price='2.500',
                      registered_at=timezone.now() - timedelta(days=1))
st, d = api('GET', '/records/workbench', T['amt'], CID)
check('SCAN-023a 昨日登记不计入今日金额（跨日清零）',
      st == 200 and (d or {}).get('my_today_amount') == '0.00',
      f"{st} {(d or {}).get('my_today_amount')}")
# 今日登记：Σqty×price = 3 × 2.5 = 7.5（ticket3，与昨日票分开避唯一约束）
Record.objects.create(company=company, ticket=ticket3, process=proc, user=amt_user,
                      qty=3, price='2.500', registered_at=timezone.now())
st, d = api('GET', '/records/workbench', T['amt'], CID)
amount = (d or {}).get('my_today_amount') if isinstance(d, dict) else None
check('SCAN-020 my_today_amount=今日 Σqty×price=「7.50」',
      st == 200 and amount == '7.50', f'{st} {amount}')
check('SCAN-020b 金额为两位小数字符串（与工资字段口径一致）',
      isinstance(amount, str) and len(amount.split('.')[-1]) == 2, repr(amount))
CompanySetting.objects.filter(company=company, key='hide_scan_price').update(value='1')
st, d = api('GET', '/records/workbench', T['amt'], CID)
check('SCAN-022 hide_scan_price=1 时本人仍返回 my_today_amount（开关只管看别人）',
      st == 200 and (d or {}).get('my_today_amount') == '7.50',
      f"{st} {(d or {}).get('my_today_amount')}")
CompanySetting.objects.filter(company=company, key='hide_scan_price').update(value='0')

# ================= SCAN-030~031 满扎口径 / cap=null =================
# 现状 50/100：w1 补登 50（同人增量合并 30+50=80），恰好满扎 → 允许
st, d = api('POST', '/records', T['w1'], CID,
            {'ticket_id': ticket1.id, 'process_id': proc.id, 'qty': 50})
check('SCAN-030a 补登到恰好满扎（累计==扎数）仍可登记 201',
      st == 201, f'{st} {json.dumps(d, ensure_ascii=False)[:120]}')
st, d = api('GET', f'/records/process-selector?ticket_id={ticket1.id}', T['w1'], CID)
mine = next((r for r in (d.get('results', []) if isinstance(d, dict) else [])
             if r.get('id') == proc.id), None)
check('SCAN-030b 满扎后选择器仍返回该工序（软提示 registered_qty==cap==100，不隐藏）',
      st == 200 and mine is not None and mine['registered_qty'] == 100
      and mine['cap'] == 100, str(mine))
st, d = api('POST', '/records', T['w2'], CID,
            {'ticket_id': ticket1.id, 'process_id': proc.id, 'qty': 1})
check('SCAN-030c 满扎后超限登记 400（既有硬限「超限拒绝」口径不变）',
      st == 400, f'{st} {json.dumps(d, ensure_ascii=False)[:120]}')
# 无限扎口径：qty=0 的票 → cap=null
st, d = api('GET', f'/records/process-selector?ticket_id={ticket0.id}', T['w1'], CID)
mine0 = next((r for r in (d.get('results', []) if isinstance(d, dict) else [])
              if r.get('id') == proc.id), None)
check('SCAN-031a qty=0（无限扎）票 cap=null 且 registered_qty=0',
      st == 200 and mine0 is not None and mine0['cap'] is None
      and mine0['registered_qty'] == 0, str(mine0))
st, d = api('GET', '/records/process-selector', T['w1'], CID)
check('SCAN-031b 无票上下文选择器 cap=null',
      st == 200 and isinstance(d, dict)
      and all(r.get('cap') is None for r in d.get('results', [])),
      f'{st} n={len(d.get("results", [])) if isinstance(d, dict) else 0}')

# ================= SCAN-040~042 工序流转兜底推导（2026-10-22） =================
# 款式 SCAN-A1 的 StyleProcess 均为 allow_skip=True（缺省）→ 款式序不可用，
# 回退工序档案 sort 在允许集内推导：第一道「登记数<扎数」的工序即当前工序
st, d = api('GET', f'/records/process-selector?ticket_id={ticket2.id}', T['w1'], CID)
cur = (d or {}).get('current_process') if isinstance(d, dict) else None
check('SCAN-040a 无款式序时按档案顺序兜底推导 current_process=proc 余40',
      st == 200 and cur and cur.get('id') == proc.id and cur.get('remaining') == 40,
      f'{st} {cur}')
# 允许集内两道工序时按 Process.sort 升序取第一道未满的（proc2=50 < proc=99）
proc2 = Process.objects.create(company=company, name='SCAN钉扣', is_fixed=True,
                               default_price=1.0, sort=50)
StyleProcess.objects.create(style=style, process=proc2, price=1.0, sort=2)
st, d = api('GET', f'/records/process-selector?ticket_id={ticket2.id}', T['w1'], CID)
cur = (d or {}).get('current_process') if isinstance(d, dict) else None
check('SCAN-040b 两道工序按档案 sort 取第一道未满（proc2 sort50 优先）',
      st == 200 and cur and cur.get('id') == proc2.id and cur.get('remaining') == 50,
      f'{st} {cur}')
st, d = api('POST', '/records', T['w1'], CID,
            {'ticket_id': ticket2.id, 'process_id': proc2.id, 'qty': 50})
check('SCAN-040c proc2 补满 201', st == 201, f'{st} {json.dumps(d, ensure_ascii=False)[:120]}')
st, d = api('GET', f'/records/process-selector?ticket_id={ticket2.id}', T['w1'], CID)
cur = (d or {}).get('current_process') if isinstance(d, dict) else None
check('SCAN-041 proc2 满扎后流转到 proc（余40）',
      st == 200 and cur and cur.get('id') == proc.id and cur.get('remaining') == 40,
      f'{st} {cur}')
st, d = api('POST', '/records', T['w2'], CID,
            {'ticket_id': ticket2.id, 'process_id': proc.id, 'qty': 40})
check('SCAN-041b proc 补满 201', st == 201, f'{st}')
st, d = api('GET', f'/records/process-selector?ticket_id={ticket2.id}', T['w1'], CID)
cur = (d or {}).get('current_process') if isinstance(d, dict) else None
check('SCAN-042 全部工序满扎 current_process=None',
      st == 200 and cur is None, f'{st} {cur}')

# ================= cleanup：物理清业务行 + 复位开关 =================
try:
    wipe_business(company)
    CompanySetting.objects.filter(company=company, key='hide_scan_price').update(
        value=CompanySetting.DEFAULTS['hide_scan_price'])
    print('cleanup: done（业务行已物理清理，测试厂/账号复用保留）')
except Exception as e:  # noqa: BLE001
    print('cleanup WARN:', e)

print(f'\n==== 扫菲增强套件：{len(PASS)} pass / {len(FAIL)} fail ====')
if FAIL:
    print('FAILED:', FAIL)
