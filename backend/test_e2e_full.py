# -*- coding: utf-8 -*-
"""
端到端全量数据测试（2026-10-22 用户需求）：删除测试厂全部数据后重构，
5 员工 / 10 订单（覆盖 1 床与多床）/ 工序覆盖整件与多工序，
验证订单进度 / 看板各状态统计 / 工序占比 / 员工排行 / 工资 / 工作台 / 工序流转推导。

数据集（全部落在专用测试厂「端到端测试厂(可删)」，绝不触碰真实厂）：
  工序：E2E裁剪0.5(E2E-P1) 车缝1.0 锁眼0.3 整烫0.2（sort 1-4）
  款式：E2E-S1 整件(仅裁剪0.5, 允许跳序) / E2E-S2 多工序(车缝→锁眼→整烫, 禁跳序)
  订单/床次/扎：
    O1 S1 黑M100+白L100 → B1(2扎)        w1 登记 200（P1 100+100）
    O2 S1 黑M200       → B2(2×100)      w2 登记 150（P1 100+50）
    O3 S1 白L50        → B3(1扎)        不登记，床次【已锁定】
    O4 S1 黑M80        → B4(1扎)        w3 登记 80，床次【已裁完】
    O5 S1 黑M60/黑L60/白M60/白L60 → B5/B6/B7(60+60+60+60)  w4: B5 60 + B7白M 30
    O6 S2 黑M100       → B8(1扎)        w1: P2 100 + P3 50
    O7 S2 白L80        → B9(1扎)        w2: P2/P3/P4 各80，床次【已裁完】
    O8 S2 黑M120       → B10(2×60)      w3: t1 P2 60
    O9 S2 黑M50+白M50  → B11/B12        w4: P2 50 / w5: P2 25
    O10 S2 白L60+黑L60 → B13/B14        不登记
预期（口径见 apps/production/stats.py done_working_qty + DashboardView）：
  订单进度 cut/registered/progress 见 EXPECT_PROGRESS（进度封顶 100，多登记不放大）；
  看板 today：done 760 / working 285（完成口径=已完扎的登记件数，未完扎登记计工作中，
              done+working=registered）/ registered 1045 / cut 1290 /
              user 5 / ticket 12 / active_beds 11 / cut_total 1290 / order_total 1290
  工序占比：裁剪520 车缝315 锁眼130 整烫80；排行：w2 390 > w1 350 > w3=w4 140 > w5 25
  工资(today)：w1 350/215.00 w2 390/195.00 w3 140/100.00 w4 140/95.00 w5 25/25.00（均未结算）
  工作台 w1 my_today_amount 215.00
  流转推导：O6票→锁眼余50 / O10-B13票→车缝余60 / O1票→None(已满) / O3票→裁剪余50
  床次列表：14床 ticket_count合计18 done_ticket_count合计8 registered_qty合计1045

跑法：manage.py shell -c "exec(compile(open('test_e2e_full.py',encoding='utf-8').read(),'test_e2e_full.py','exec'))"
幂等：专用厂，每轮全清业务数据后重建；只删本公司，真实数据零接触。
"""
import json
from decimal import Decimal

from django.contrib.auth.hashers import make_password
from django.test import Client
from django.utils import timezone

from apps.accounts.models import Company, Post as PostM, User, UserCompany
from apps.accounts.services import create_company_with_owner
from apps.catalog.models import Customer, Process, Style, StyleProcess, Ticket
from apps.orders.models import Order, OrderItem
from apps.production.models import Production, ProductionProcess
from apps.records.models import Record
from apps.salary.models import SalaryAdjust, SettleApply, Settlement

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


CO = '端到端测试厂(可删)'
OWNER = '13988720901'
WORKERS = [('13988720902', 'E2E员工一'), ('13988720903', 'E2E员工二'),
           ('13988720904', 'E2E员工三'), ('13988720905', 'E2E员工四'),
           ('13988720906', 'E2E员工五')]

# ================= 建厂（专用，可整厂清） =================
company = Company.objects.filter(name=CO).first()
if company is None:
    owner = ensure_user(OWNER, 'E2E厂长')
    company, _ = create_company_with_owner(owner, CO)
else:
    admin_post = PostM.objects.get(company=company, is_admin=True, is_deleted=False)
    uc = (UserCompany.objects.select_related('user')
          .filter(company=company, post=admin_post, is_deleted=False)
          .order_by('id').first())
    owner = uc.user if uc else ensure_user(OWNER, 'E2E厂长')
CID = company.id

# ================= 全清本公司业务数据（先子后父；专用厂允许整清） =================
Settlement.objects.filter(company=company).delete()
SettleApply.objects.filter(company=company).delete()
SalaryAdjust.objects.filter(company=company).delete()
Record.objects.filter(company=company).delete()
Ticket.all_objects.filter(company=company).delete()
ProductionProcess.objects.filter(production__company=company).delete()
Production.all_objects.filter(company=company).delete()
OrderItem.all_objects.filter(order__company=company).delete()
Order.all_objects.filter(company=company).delete()
StyleProcess.objects.filter(style__company=company).delete()
Style.all_objects.filter(company=company).delete()
Process.all_objects.filter(company=company).delete()
Customer.all_objects.filter(company=company).delete()
UserCompany.all_objects.filter(company=company, post__is_admin=False).delete()
PostM.all_objects.filter(company=company, is_admin=False).delete()

today = timezone.localdate()
DF = today.isoformat()

# ================= 造数据 =================
procs = {}
for i, (name, price) in enumerate([('E2E裁剪', '0.500'), ('E2E车缝', '1.000'),
                                   ('E2E锁眼', '0.300'), ('E2E整烫', '0.200')], 1):
    procs[i] = Process.objects.create(company=company, name=name, sort=i * 10,
                                      is_fixed=True, default_price=price)

s1 = Style.objects.create(company=company, style_no='E2E-S1', name='整件测试款')
s2 = Style.objects.create(company=company, style_no='E2E-S2', name='多工序测试款')
StyleProcess.objects.create(style=s1, process=procs[1], price='0.500',
                            sort=1, allow_skip=True)
for sortp, pid in enumerate([2, 3, 4], 1):
    StyleProcess.objects.create(style=s2, process=procs[pid],
                                price=procs[pid].default_price,
                                sort=sortp, allow_skip=False)

worker_ids = {}
wphone = {}
for i, (phone, name) in enumerate(WORKERS, 1):
    u = ensure_user(phone, name)
    worker_ids[i] = u.id
    wphone[i] = phone
    ensure_member = UserCompany.objects.create(
        user=u, company=company,
        post=PostM.objects.create(company=company, name=f'E2E普工{i}', permissions=[]),
        status=UserCompany.Status.ACTIVE)

# 订单：{单号: (款式, [(色,码,量)...])}
ORDER_DEFS = {
    'E2E-O1': (s1, [('黑', 'M', 100), ('白', 'L', 100)]),
    'E2E-O2': (s1, [('黑', 'M', 200)]),
    'E2E-O3': (s1, [('白', 'L', 50)]),
    'E2E-O4': (s1, [('黑', 'M', 80)]),
    'E2E-O5': (s1, [('黑', 'M', 60), ('黑', 'L', 60), ('白', 'M', 60), ('白', 'L', 60)]),
    'E2E-O6': (s2, [('黑', 'M', 100)]),
    'E2E-O7': (s2, [('白', 'L', 80)]),
    'E2E-O8': (s2, [('黑', 'M', 120)]),
    'E2E-O9': (s2, [('黑', 'M', 50), ('白', 'M', 50)]),
    'E2E-O10': (s2, [('白', 'L', 60), ('黑', 'L', 60)]),
}
orders = {}
for no, (style, items) in ORDER_DEFS.items():
    o = Order.objects.create(company=company, order_no=no, style=style,
                             status=Order.Status.PRODUCING)
    for color, size, qty in items:
        OrderItem.objects.create(order=o, color=color, size=size, qty=qty)
    orders[no] = o

# 床次：{床号: (订单, 款式, [(扎号,色,码,量)...], 锁定, 裁完)}
BED_DEFS = {
    'E2E-B1': ('E2E-O1', s1, [(1, '黑', 'M', 100), (2, '白', 'L', 100)], False, False),
    'E2E-B2': ('E2E-O2', s1, [(1, '黑', 'M', 100), (2, '黑', 'M', 100)], False, False),
    'E2E-B3': ('E2E-O3', s1, [(1, '白', 'L', 50)], True, False),
    'E2E-B4': ('E2E-O4', s1, [(1, '黑', 'M', 80)], False, True),
    'E2E-B5': ('E2E-O5', s1, [(1, '黑', 'M', 60)], False, False),
    'E2E-B6': ('E2E-O5', s1, [(1, '黑', 'L', 60)], False, False),
    'E2E-B7': ('E2E-O5', s1, [(1, '白', 'M', 60), (2, '白', 'L', 60)], False, False),
    'E2E-B8': ('E2E-O6', s2, [(1, '黑', 'M', 100)], False, False),
    'E2E-B9': ('E2E-O7', s2, [(1, '白', 'L', 80)], False, True),
    'E2E-B10': ('E2E-O8', s2, [(1, '黑', 'M', 60), (2, '黑', 'M', 60)], False, False),
    'E2E-B11': ('E2E-O9', s2, [(1, '黑', 'M', 50)], False, False),
    'E2E-B12': ('E2E-O9', s2, [(1, '白', 'M', 50)], False, False),
    'E2E-B13': ('E2E-O10', s2, [(1, '白', 'L', 60)], False, False),
    'E2E-B14': ('E2E-O10', s2, [(1, '黑', 'L', 60)], False, False),
}
beds, tickets = {}, {}
for bed_no, (ono, style, bundles, locked, cut_done) in BED_DEFS.items():
    total = sum(q for _, _, _, q in bundles)
    p = Production.objects.create(company=company, style=style, bed_no=bed_no,
                                  total_qty=total, plan_qty=total,
                                  order=orders[ono],
                                  source_order_no=ono,
                                  is_locked=locked, is_cut_done=cut_done)
    beds[bed_no] = p
    for bno, color, size, qty in bundles:
        t = Ticket.objects.create(company=company, production=p, bed_no=bed_no,
                                  bundle_no=str(bno), color=color, size=size,
                                  qty=qty, qr_code=f'E2E|{bed_no}|{bno}|{color}|{size}|{qty}')
        tickets[(bed_no, bno)] = t


def reg(bed, bno, pid, w, qty):
    Record.objects.create(company=company, ticket=tickets[(bed, bno)],
                          process=procs[pid], user=User.objects.get(pk=worker_ids[w]),
                          qty=qty, price=procs[pid].default_price)


# 登记数据（见头注数据集）
reg('E2E-B1', 1, 1, 1, 100); reg('E2E-B1', 2, 1, 1, 100)
reg('E2E-B2', 1, 1, 2, 100); reg('E2E-B2', 2, 1, 2, 50)
reg('E2E-B4', 1, 1, 3, 80)
reg('E2E-B5', 1, 1, 4, 60); reg('E2E-B7', 1, 1, 4, 30)
reg('E2E-B8', 1, 2, 1, 100); reg('E2E-B8', 1, 3, 1, 50)
reg('E2E-B9', 1, 2, 2, 80); reg('E2E-B9', 1, 3, 2, 80); reg('E2E-B9', 1, 4, 2, 80)
reg('E2E-B10', 1, 2, 3, 60)
reg('E2E-B11', 1, 2, 4, 50); reg('E2E-B12', 1, 2, 5, 25)

TA = login(owner.phone)
TW1 = login(wphone[1])

# ================= 用例 1：订单进度（10 单） =================
EXPECT_PROGRESS = {
    'E2E-O1': (200, 200, 100.0), 'E2E-O2': (200, 150, 75.0),
    'E2E-O3': (50, 0, 0.0), 'E2E-O4': (80, 80, 100.0),
    'E2E-O5': (240, 90, 37.5), 'E2E-O6': (100, 150, 100.0),
    'E2E-O7': (80, 240, 100.0), 'E2E-O8': (120, 60, 50.0),
    'E2E-O9': (100, 75, 75.0), 'E2E-O10': (120, 0, 0.0),
}
print('\n===== 用例1：订单进度（裁货/登记/进度） =====')
for no, (cut, regd, prog) in EXPECT_PROGRESS.items():
    st, d = api('GET', f'/orders/{orders[no].id}/progress', TA, CID)
    a = (int(d.get('cut_qty', -1)), int(d.get('registered_qty', -1)),
         float(d.get('progress', -1)))
    check(f'订单 {no} 进度=裁{cut}/登{regd}/{prog}%',
          st == 200 and a == (cut, regd, prog), f'实际 {a} (http {st})')

# ================= 用例 2：看板汇总（today） =================
print('\n===== 用例2：看板汇总卡（today） =====')
st, d = api('GET', '/dashboard?range=today', TA, CID)
sm = d.get('summary', {})
EXPECT_SUMMARY = {'done_qty': 760, 'working_qty': 285, 'registered_qty': 1045,
                  'cut_qty': 1290, 'user_count': 5, 'ticket_count': 12,
                  'active_beds': 11, 'cut_total': 1290, 'order_total': 1290}
for k, v in EXPECT_SUMMARY.items():
    check(f'汇总.{k}={v}', int(sm.get(k, -1)) == v, f'实际 {sm.get(k)}')
check('汇总.done+working=registered', sm.get('done_qty', 0) + sm.get('working_qty', 0)
      == sm.get('registered_qty', -1), f"{sm.get('done_qty')}+{sm.get('working_qty')}")

print('\n===== 用例2b：工序占比（today） =====')
EXPECT_PROC = {'E2E裁剪': 520, 'E2E车缝': 315, 'E2E锁眼': 130, 'E2E整烫': 80}
got_proc = {r['name']: int(r['qty']) for r in d.get('processes', [])}
check('工序占比四项', got_proc == EXPECT_PROC, f'实际 {got_proc}')
sum_pct = sum(float(r.get('percent', 0)) for r in d.get('processes', []))
check('工序占比合计≈100%', abs(sum_pct - 100.0) < 0.5, f'实际 {sum_pct}')

print('\n===== 用例2c：员工排行（today） =====')
EXPECT_RANK = {2: 390, 1: 350, 3: 140, 4: 140, 5: 25}
got_rank = {r.get('user_id'): int(r.get('qty', 0)) for r in d.get('rank', [])}
check('排行五人件数', got_rank == {worker_ids[k]: v for k, v in EXPECT_RANK.items()},
      f'实际 {got_rank}')

# ================= 用例 3：床次列表聚合 =================
print('\n===== 用例3：床次列表聚合 =====')
st, d = api('GET', '/productions?page_size=50', TA, CID)
rows = d.get('results', d.get('items', []))
check('床次总数 14', d.get('count', len(rows)) == 14, f'实际 {d.get("count")}')
check('扎数合计 18', sum(r.get('ticket_count', 0) for r in rows) == 18,
      f'实际 {sum(r.get("ticket_count", 0) for r in rows)}')
check('完工扎合计 8', sum(r.get('done_ticket_count', 0) for r in rows) == 8,
      f'实际 {sum(r.get("done_ticket_count", 0) for r in rows)}')
check('床次登记合计 1045', sum(r.get('registered_qty', 0) for r in rows) == 1045,
      f'实际 {sum(r.get("registered_qty", 0) for r in rows)}')
check('进行中床次 11（14-锁1-裁完2）',
      sum(1 for r in rows if not r.get('is_locked') and not r.get('is_cut_done')) == 11)
b1 = next((r for r in rows if r.get('bed_no') == 'E2E-B1'), {})
check('B1 完工扎=2/登记=裁货(200)', b1.get('done_ticket_count') == 2
      and b1.get('registered_qty') == 200 and b1.get('cut_total') == 200, f'实际 {b1}')
b3 = next((r for r in rows if r.get('bed_no') == 'E2E-B3'), {})
check('B3 锁定/登记0', b3.get('is_locked') is True
      and b3.get('registered_qty') == 0, f'实际 {b3}')

# ================= 用例 4：员工工资（today） =================
print('\n===== 用例4：员工工资（today） =====')
EXPECT_SAL = {1: (350, '215.00'), 2: (390, '195.00'), 3: (140, '100.00'),
              4: (140, '95.00'), 5: (25, '25.00')}
for w, (qty, amount) in EXPECT_SAL.items():
    st, d = api('GET', f'/salary/employee/{worker_ids[w]}?date_from={DF}&date_to={DF}',
                TA, CID)
    s = d.get('summary', {})
    check(f'w{w} 工资 {qty}件/{amount}元',
          int(s.get('qty', -1)) == qty and str(s.get('amount', '')) == amount
          and Decimal(str(d.get('settled', -1))) == 0
          and str(d.get('unsettled', '')) == amount,
          f"实际 qty={s.get('qty')} amount={s.get('amount')} "
          f"settled={d.get('settled')} unsettled={d.get('unsettled')}")

# ================= 用例 5：工作台（w1 本人今日） =================
print('\n===== 用例5：工作台 w1 =====')
st, d = api('GET', '/records/workbench', TW1, CID)
check('w1 my_today_amount=215.00', d.get('my_today_amount') == '215.00',
      f"实际 {d.get('my_today_amount')}")

# ================= 用例 6：工序流转推导 =================
print('\n===== 用例6：工序流转推导（process-selector） =====')
EXPECT_CUR = [
    (('E2E-B8', 1), procs[3].id, 'E2E锁眼', 50),
    (('E2E-B13', 1), procs[2].id, 'E2E车缝', 60),
    (('E2E-B1', 1), None, '', None),
    (('E2E-B3', 1), procs[1].id, 'E2E裁剪', 50),
]
for (bed, bno), pid, pname, rem in EXPECT_CUR:
    st, d = api('GET', f'/records/process-selector?ticket_id={tickets[(bed, bno)].id}',
                TA, CID)
    cur = d.get('current_process')
    if pid is None:
        check(f'{bed}扎{bno} 推导=None（已满/无序）', cur is None, f'实际 {cur}')
    else:
        ok = (cur and cur.get('id') == pid and cur.get('name') == pname
              and int(cur.get('remaining', -1)) == rem)
        check(f'{bed}扎{bno} 推导={pname} 余{rem}', bool(ok), f'实际 {cur}')

# ================= 用例 7：扫菲登记约束（跳序拦截 / 满扎拒绝） =================
print('\n===== 用例7：登记约束抽查 =====')
st, d = api('POST', '/records', TA, CID,
            {'ticket_id': tickets[('E2E-B8', 1)].id, 'process_id': procs[4].id,
             'qty': 10})
check('S2 禁跳序：锁眼(50/100)未满禁登整烫 → 拒绝',
      st in (400, 409), f'http {st} {d}')
st, d = api('POST', '/records', TA, CID,
            {'ticket_id': tickets[('E2E-B1', 1)].id, 'process_id': procs[1].id,
             'qty': 1})
check('S1 满扎：B1扎1 裁剪已满100/100 → 拒绝',
      st in (400, 409), f'http {st} {d}')
st, d = api('POST', '/records', TA, CID,
            {'ticket_id': tickets[('E2E-B3', 1)].id, 'process_id': procs[1].id,
             'qty': 10})
check('锁定床次 B3 禁登 → 拒绝', st in (400, 403, 409), f'http {st} {d}')
# 抽查产生的拒绝请求均不应落库：总记录数应仍为造数的 15 条
cnt = Record.objects.filter(company=company).count()
check('抽查拒绝均未落库（记录总数仍 15）', cnt == 15, f'实际 {cnt}')

# ================= 用例 6：看板维度查看（2026-10-23 需求：订单/裁床/部门/组/人） =================
WIDE = 'range=custom&start_date=2000-01-01&end_date=2100-01-01'
st, d = api('GET', f'/dashboard/dim?dim=bad&{WIDE}', TA, CID)
check('DIM-01 非法 dim → 400', st == 400, f'{st}')
st, do = api('GET', f'/dashboard/dim?dim=order&{WIDE}', TA, CID)
check('DIM-02 订单维度 200 且首列为订单号',
      st == 200 and (do['columns'] or [{}])[0].get('title') == '订单号',
      f'{st} cols={[c.get("title") for c in do.get("columns", [])]}')
check('DIM-03 订单维度行均带下钻标记且件数为正',
      do['rows'] and all(r['nav'] == 'order' for r in do['rows'])
      and all(int(r['cells'][4]) > 0 for r in do['rows']),
      f'rows={len(do.get("rows", []))}')
qty_order = sum(int(r['cells'][4]) for r in do['rows'])
st, db = api('GET', f'/dashboard/dim?dim=bed&{WIDE}', TA, CID)
check('DIM-04 裁床维度 200 且行数=有登记床次数(10)',
      st == 200 and len(db['rows']) == 10, f'{st} rows={len(db.get("rows", []))}')
check('DIM-05 裁床维度行带下钻标记且末列为状态',
      db['rows'] and all(r['nav'] == 'bed' for r in db['rows'])
      and db['columns'][-1]['title'] == '状态', f'{st}')
check('DIM-06 订单/裁床两维度登记件数合计一致（有登记床次均挂单）',
      qty_order == sum(int(r['cells'][4]) for r in db['rows']),
      f'order={qty_order} bed={sum(int(r["cells"][4]) for r in db["rows"])}')
st, du = api('GET', f'/dashboard/dim?dim=user&{WIDE}', TA, CID)
check('DIM-07 人维度行数=有登记员工数(5) 且单元格数=列数',
      st == 200 and len(du['rows']) == 5
      and all(len(r['cells']) == len(du['columns']) for r in du['rows']),
      f'{st} rows={len(du.get("rows", []))}')
qty_user = sum(int(r['cells'][4]) for r in du['rows'])
st, dg = api('GET', f'/dashboard/dim?dim=group&{WIDE}', TA, CID)
check('DIM-08 组维度登记合计=人维度合计（同源聚合）',
      st == 200 and sum(int(r['cells'][3]) for r in dg['rows']) == qty_user,
      f'group={sum(int(r["cells"][3]) for r in dg.get("rows", []))} user={qty_user}')
st, dp = api('GET', f'/dashboard/dim?dim=post&{WIDE}', TA, CID)
check('DIM-09 部门维度 200 且登记合计=人维度合计',
      st == 200 and sum(int(r['cells'][3]) for r in dp['rows']) == qty_user,
      f'{st} post={sum(int(r["cells"][3]) for r in dp.get("rows", []))}')
st, _ = api('GET', f'/dashboard/dim?dim=user&{WIDE}', TW1, CID)
check('DIM-10 员工（非管理员）也可查看维度表', st == 200, f'{st}')

# ================= 汇总 =================
print(f'\n===== E2E 汇总: PASS {len(PASS)} / FAIL {len(FAIL)} =====')
for f in FAIL:
    print('FAIL:', f)
