# -*- coding: utf-8 -*-
"""
成本域（test_cost）端到端后端验证 —— SA3 交付（2026-10-24 P1 cost 域）。

覆盖 P1 cost 域全部端点与口径（docs/服装ERP全模块设计方案.md §3.3）：
  取数  C-VAL-01~10（材料/人工/whole票面均摊/退料冲减/缺价含0价/换算与缺参/
                     无单床次 no_order_cnt/revenue/单件成本/毛利率）
  端点  C-API-01~16（列表过滤/重算/批量/制费/对账单/401/403/软删404/无items全0行）
  stale C-STL-01~08（领料确认/登记/撤销/订单改价/制费 PUT 埋点 + 幂等翻两次只写一次）
  对账  C-REC-01~04（Σ订单人工 vs 发放总额逐项差异/口径说明/空期间）
  权限  C-PERM-01~05（401/403/权限点注册/effective_perms 下发/legacy 零回归）
  隔离  C-ISO-01~03（跨厂 404/各厂成本独立/列表不见）
  并发  C-CNC-01~02（select_for_update+get_or_create 重复重算幂等/批量单单失败隔离）
  损益  C-PNL-01~07（/cost/orders/pnl 应收聚合/两口径毛利/keyword 过滤/无应收兜0）

跑法：manage.py shell -c "exec(compile(open('test_cost.py',encoding='utf-8').read(),'test_cost.py','exec'))"
幂等：自建「成本测试厂(可删)/成本B厂(可删)」并跨次复用（Company.creator PROTECT
不可删）；每轮 preclean 硬清本厂业务行，不依赖 DB 现状；
绝不触碰真实厂（company_id=86 等）。
"""
import json
from decimal import Decimal

from django.contrib.auth.hashers import make_password
from django.test import Client
from django.utils import timezone

from apps.accounts.models import Company, Post as PostM, User, UserCompany
from apps.accounts.services import create_company_with_owner
from apps.catalog.models import Process, Style, Ticket
from apps.cost.models import OrderCost
from apps.cost.services import (mark_orders_stale, mark_ticket_stale,
                                recompute, wage_reconciliation)
from apps.ops.models import AuditLog
from apps.orders.models import Order, OrderItem
from apps.production.models import Production, ProductionProcess
from apps.records.models import Record
from apps.salary.models import Settlement
from apps.tech.models import Material
from apps.warehouse.models import IssueItem, Lot, MaterialIssue

PASS, FAIL = [], []


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    print(('PASS' if cond else FAIL), name, ('' if cond else f'| {detail}'))


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


def ensure_post(company, name, perms):
    p = PostM.objects.filter(company=company, name=name, is_deleted=False).first()
    if p is None:
        p = PostM.objects.create(company=company, name=name, permissions=perms)
    else:
        PostM.objects.filter(pk=p.pk).update(permissions=perms)
        p.refresh_from_db()
    return p


def ensure_member(u, company, post, **uc_kw):
    uc = UserCompany.all_objects.filter(user=u, company=company).first()
    if uc is None:
        uc = UserCompany.objects.create(user=u, company=company, post=post,
                                        status=UserCompany.Status.ACTIVE)
    fields = {k: v for k, v in uc_kw.items()}
    uc.is_deleted = False
    uc.status = UserCompany.Status.ACTIVE
    uc.post = post
    for k, v in fields.items():
        setattr(uc, k, v)
    uc.save()
    return uc


OWNER, H_PLAIN, H_VIEW, H_PERM, H_LEGACY, H_OWNER_B = (
    '13988711301', '13988711302', '13988711303', '13988711304', '13988711305',
    '13988711306')
H_W1, H_WH, H_WH2, H_COM, H_COM2, H_FIX = (
    '13988711311', '13988711312', '13988711313', '13988711314', '13988711315',
    '13988711316')
CO_A, CO_B = '成本测试厂(可删)', '成本B厂(可删)'


def preclean(company):
    """幂等自清扫：硬清本厂业务行（PROTECT 逆序：领料行→单据→登记→菲票→床次→订单）。"""
    OrderCost.all_objects.filter(company=company).delete()
    IssueItem.all_objects.filter(issue__company=company).delete()
    MaterialIssue.all_objects.filter(company=company).delete()
    Record.all_objects.filter(company=company).delete()
    Settlement.all_objects.filter(company=company).delete()
    Ticket.all_objects.filter(company=company).delete()
    ProductionProcess.all_objects.filter(production__company=company).delete()
    Production.all_objects.filter(company=company).delete()
    OrderItem.all_objects.filter(order__company=company).delete()
    Order.all_objects.filter(company=company).delete()
    Lot.all_objects.filter(company=company).delete()
    Material.all_objects.filter(company=company).delete()
    Style.objects.filter(company=company).delete()
    Process.all_objects.filter(company=company, name__startswith='TCOS').delete()
    AuditLog.objects.filter(company=company).delete()


# ================= 建厂（复用） + preclean =================
company = Company.objects.filter(name=CO_A).first()
if company is None:
    owner = ensure_user(OWNER, '成本厂长')
    company, admin_post = create_company_with_owner(owner, CO_A)
else:
    owner = ensure_user(OWNER, '成本厂长')
    admin_post = PostM.objects.get(company=company, is_admin=True, is_deleted=False)
company_b = Company.objects.filter(name=CO_B).first()
if company_b is None:
    owner_b = ensure_user(H_OWNER_B, '成本B厂长')
    company_b, _ = create_company_with_owner(owner_b, CO_B)
else:
    owner_b = ensure_user(H_OWNER_B, '成本B厂长')
CID, CID_B = company.id, company_b.id

preclean(company)
preclean(company_b)

post_plain = ensure_post(company, '成本普工', [])
post_view = ensure_post(company, '成本查看岗', ['cost.view'])
post_perm = ensure_post(company, '成本管理岗', ['cost.view', 'cost.manage'])
post_legacy = ensure_post(company, '成本旧岗', ['production'])
plain = ensure_user(H_PLAIN, '成本无权员')
view_u = ensure_user(H_VIEW, '成本查看员')
perm_u = ensure_user(H_PERM, '成本管理员')
legacy_u = ensure_user(H_LEGACY, '成本旧岗员')
ensure_member(plain, company, post_plain, group_no=0)
ensure_member(view_u, company, post_view, group_no=0)
ensure_member(perm_u, company, post_perm, group_no=0)
ensure_member(legacy_u, company, post_legacy, group_no=0)

T_owner = login(OWNER)
T_plain = login(H_PLAIN)
T_view = login(H_VIEW)
T_perm = login(H_PERM)
T_owner_b = login(H_OWNER_B)
today = timezone.localdate()

# ================= 数据工厂 =================
style = Style.objects.create(company=company, style_no='TCOS-S1', name='成本测试款')
p_sew = Process.objects.create(company=company, name='TCOS车缝', default_price=1,
                               is_fixed=True, sort=1)
p_over = Process.objects.create(company=company, name='TCOS锁边', default_price=0.5,
                                is_fixed=True, sort=2)

order1 = Order.objects.create(company=company, order_no='TCOS-001', style=style)
OrderItem.objects.create(order=order1, color='红', size='M', qty=100, price=Decimal('10.5'))
OrderItem.objects.create(order=order1, color='红', size='L', qty=50, price=None)  # 未定价
order2 = Order.objects.create(company=company, order_no='TCOS-002', style=style)  # 无 items
order3 = Order.objects.create(company=company, order_no='TCOS-003', style=style)
OrderItem.objects.create(order=order3, color='蓝', size='M', qty=10, price=Decimal('5'))

bed1 = Production.objects.create(company=company, style=style, bed_no='TCOS-B1',
                                 total_qty=100, order=order1)
bed2 = Production.objects.create(company=company, style=style, bed_no='TCOS-B2',
                                 total_qty=50, order=order1)
bed3 = Production.objects.create(company=company, style=style, bed_no='TCOS-B3',
                                 total_qty=40)  # 无单床次
ProductionProcess.objects.create(production=bed1, process=p_sew, price=Decimal('1'))
ProductionProcess.objects.create(production=bed1, process=p_over, price=Decimal('0.5'))
ProductionProcess.objects.create(production=bed2, process=p_sew, price=Decimal('2'))

w1 = ensure_user(H_W1, '计件工')
wh = ensure_user(H_WH, '整件工')
wh2 = ensure_user(H_WH2, '零率整件工')
com = ensure_user(H_COM, '提成工')
com2 = ensure_user(H_COM2, '提成工2')
fix = ensure_user(H_FIX, '固定工')
# 注意 UserCompany.group_no 默认=1：显式归零，提成组单独用 99 号组
ensure_member(w1, company, post_plain, group_no=99)         # 提成组基数成员
ensure_member(wh, company, post_plain, group_no=0,
              salary_type=UserCompany.SalaryType.WHOLE, piece_rate=Decimal('3'))
ensure_member(wh2, company, post_plain, group_no=0,
              salary_type=UserCompany.SalaryType.WHOLE, piece_rate=Decimal('0'))
ensure_member(com, company, post_plain, group_no=99,
              salary_type=UserCompany.SalaryType.COMMISSION,
              commission_rate=Decimal('10'))
ensure_member(com2, company, post_plain, group_no=99,
              salary_type=UserCompany.SalaryType.COMMISSION,
              commission_rate=Decimal('10'))
ensure_member(fix, company, post_plain, group_no=0,
              salary_type=UserCompany.SalaryType.FIXED,
              fixed_salary=Decimal('100'),
              fixed_type=UserCompany.FixedType.DAY)

t1 = Ticket.objects.create(company=company, production=bed1, bed_no='TCOS-B1',
                           bundle_no='1', color='红', size='M', qty=100, qr_code='x1')
t2 = Ticket.objects.create(company=company, production=bed2, bed_no='TCOS-B2',
                           bundle_no='1', color='红', size='L', qty=50, qr_code='x2')
t3 = Ticket.objects.create(company=company, production=bed3, bed_no='TCOS-B3',
                           bundle_no='1', color='红', size='M', qty=40, qr_code='x3')
t5 = Ticket.objects.create(company=company, production=bed1, bed_no='TCOS-B1',
                           bundle_no='5', color='红', size='XL', qty=10, qr_code='x5')
t6 = Ticket.objects.create(company=company, production=bed1, bed_no='TCOS-B1',
                           bundle_no='6', color='红', size='XL', qty=100, qr_code='x6')
t4 = Ticket.objects.create(company=company, production=bed3, bed_no='TCOS-B3',
                           bundle_no='4', color='红', size='L', qty=50, qr_code='x4')


def mk_record(ticket, process, user, qty, price):
    return Record.objects.create(company=company, ticket=ticket, process=process,
                                 user=user, qty=qty, price=price)


mk_record(t1, p_sew, w1, 100, Decimal('1'))     # bed1 工序1 满
mk_record(t1, p_over, w1, 100, Decimal('0.5'))  # bed1 工序2 满 → t1 完成
mk_record(t2, p_sew, wh, 50, Decimal('2'))      # whole 工登记（不按 qty×price 计成本）
mk_record(t2, p_sew, wh2, 5, Decimal('2'))      # 零率整件工 → rate=0 分支
mk_record(t3, p_sew, w1, 40, Decimal('1'))      # 无单床次登记
mk_record(t3, p_sew, fix, 1, Decimal('1'))      # 固定工出勤（工资对账 fixed 天数）
mk_record(t5, p_sew, wh, 3, Decimal('1'))       # t5 未完成（pn=1 < pc=2）→ 均摊跳过分支

# 批次：L1 有价 / L2 无价 / L3 0价 / L4 公斤有价 / L5 未知单位 / L6 米价折公斤
lot1 = Lot.objects.create(company=company, lot_no='TCOS-L1', fabric_name='面料',
                          qty=100, unit='米', unit_cost=Decimal('10'))
lot2 = Lot.objects.create(company=company, lot_no='TCOS-L2', fabric_name='面料',
                          qty=50, unit='米', unit_cost=None)
lot3 = Lot.objects.create(company=company, lot_no='TCOS-L3', fabric_name='面料',
                          qty=50, unit='米', unit_cost=Decimal('0'))
lot5 = Lot.objects.create(company=company, lot_no='TCOS-L5', fabric_name='面料',
                          qty=50, unit='码', unit_cost=Decimal('5'))
lot6 = Lot.objects.create(company=company, lot_no='TCOS-L6', fabric_name='面料',
                          qty=50, unit='米', unit_cost=Decimal('12'))
lot4 = Lot.objects.create(company=company, lot_no='TCOS-L4', fabric_name='面料',
                          qty=50, unit='米', unit_cost=Decimal('10'))
mat1 = Material.objects.create(company=company, code='TCOS-M1', name='主面料',
                               category=Material.Category.FABRIC, unit='米')
mat2 = Material.objects.create(company=company, code='TCOS-M2', name='针织面料',
                               category=Material.Category.FABRIC, unit='公斤',
                               weight_gsm=Decimal('200'), width_cm=Decimal('150'))
# 换算率：每米公斤 = 200×150/100000 = 0.3 → 30米 = 9公斤

issue1 = MaterialIssue.objects.create(company=company, issue_no='TCOS-LL1',
                                      issue_at=timezone.now(),
                                      status=MaterialIssue.Status.CONFIRMED,
                                      operator=owner)
IssueItem.objects.create(issue=issue1, production=bed1, lot=lot1, qty=10)              # 100
IssueItem.objects.create(issue=issue1, production=bed1, lot=lot4, qty=5,
                         material=mat1)                                                # 50（同单位）
IssueItem.objects.create(issue=issue1, production=bed1, lot=lot6, qty=30,
                         material=mat2)                                                # 30米→9公斤×12=108
IssueItem.objects.create(issue=issue1, production=bed1, lot=lot2, qty=8)               # 缺价
IssueItem.objects.create(issue=issue1, production=bed1, lot=lot3, qty=4)               # 0价
IssueItem.objects.create(issue=issue1, production=bed1, lot=lot5, qty=10,
                         material=mat1)                                                # 码↔米未知对
issue2 = MaterialIssue.objects.create(company=company, issue_no='TCOS-LL2',
                                      type=MaterialIssue.Type.RETURN,
                                      issue_at=timezone.now(),
                                      status=MaterialIssue.Status.CONFIRMED,
                                      operator=owner)
IssueItem.objects.create(issue=issue2, production=bed1, lot=lot1, qty=2)               # 退料冲减 -20

settle = Settlement.objects.create(company=company, user=None,
                                   period_start=today, period_end=today,
                                   employee_count=1, amount=Decimal('500'),
                                   status=Settlement.Status.SETTLED, created_by=owner)
Settlement.objects.create(company=company, user=None,
                          period_start=today, period_end=today,
                          employee_count=1, amount=Decimal('999'),
                          status=Settlement.Status.REVOKED, created_by=owner)

# ================= C-VAL 取数口径 =================
print('\n---- C-VAL 取数口径 ----')
oc1 = recompute(company, order1, user=owner)
check('C-VAL-01 材料成本=领料-退料(100+50+108-20)',
      Decimal(oc1.material_cost) == Decimal('238.00'), str(oc1.material_cost))
check('C-VAL-02 缺价行数=3（无价/0价/未知单位对）',
      oc1.missing_price_cnt == 3, str(oc1.missing_price_cnt))
check('C-VAL-03 人工成本=process150+whole票面均摊1.5',
      Decimal(oc1.labor_cost) == Decimal('151.50'), str(oc1.labor_cost))
check('C-VAL-04 产出件数=完成扎数×扎量(100+50)',
      oc1.output_qty == 150, str(oc1.output_qty))
check('C-VAL-05 理论收入=Σqty×price（未定价行不计）',
      Decimal(oc1.revenue) == Decimal('1050.00'), str(oc1.revenue))
check('C-VAL-06 未归属床次=1（TCOS-B3）', oc1.no_order_cnt == 1, str(oc1.no_order_cnt))
check('C-VAL-07 总成本/单件成本（无制费）',
      Decimal(oc1.total_cost) == Decimal('389.50')
      and Decimal(oc1.unit_cost) == Decimal('2.597'),
      f'{oc1.total_cost}/{oc1.unit_cost}')
check('C-VAL-08 毛利/毛利率',
      Decimal(oc1.gross_profit) == Decimal('660.50')
      and Decimal(oc1.gross_margin) == Decimal('62.90'),
      f'{oc1.gross_profit}/{oc1.gross_margin}')
check('C-VAL-09 状态=computed/非stale/计算人',
      oc1.status == 'computed' and not oc1.is_stale and oc1.computed_by_id == owner.id,
      f'{oc1.status}/{oc1.is_stale}')
oc2 = recompute(company, order2, user=owner)
check('C-VAL-10 无items订单全0行（unit_cost/毛利率 null）',
      Decimal(oc2.total_cost) == 0 and oc2.unit_cost is None
      and oc2.gross_margin is None and oc2.status == 'computed'
      and oc2.no_order_cnt == 1,
      f'{oc2.total_cost}/{oc2.unit_cost}/{oc2.gross_margin}')

# ================= C-API 端点 =================
print('\n---- C-API 端点 ----')
st, d = api('GET', '/cost/orders', cid=CID)
check('C-API-01 无token 401', st == 401, f's={st}')
st, d = api('GET', '/cost/orders', T_plain, CID)
check('C-API-02 无 cost.view 403', st == 403, f's={st}')
st, d = api('GET', '/cost/orders', T_view, CID)
rows = d.get('results', d if isinstance(d, list) else [])
check('C-API-03 cost.view 可读列表（软删订单不显）',
      st == 200 and {r['order_no'] for r in rows} == {'TCOS-001', 'TCOS-002'},
      f'{st} {[r.get("order_no") for r in rows]}')
row1 = next((r for r in rows if r['order_no'] == 'TCOS-001'), {})
check('C-API-04 行字段齐（订单/款式/计算人）',
      row1.get('order') == order1.id and row1.get('style_no') == 'TCOS-S1'
      and row1.get('order_status') == 'producing'
      and row1.get('computed_by_name') == '成本厂长',
      str(row1)[:200])
st, d = api('GET', '/cost/orders?keyword=TCOS-001', T_view, CID)
rows = d.get('results', [])
check('C-API-05 keyword 过滤', len(rows) == 1 and rows[0]['order_no'] == 'TCOS-001',
      str(len(rows)))
st, d = api('GET', f'/cost/orders?date_from={today}', T_view, CID)
check('C-API-06 date_from 命中', d.get('count', len(d.get('results', []))) == 2,
      str(d.get('count')))
st, d = api('GET', f'/cost/orders?date_to={today}', T_view, CID)
check('C-API-07 date_to 命中', d.get('count', 0) == 2, str(d.get('count')))
st, d = api('GET', f'/cost/orders?computed_at_from={today}', T_view, CID)
check('C-API-08 computed_at_from 命中（增量查看键）',
      d.get('count', 0) == 2, str(d.get('count')))
st, d = api('GET', '/cost/orders?computed_at_from=2099-01-01', T_view, CID)
check('C-API-09 computed_at_from 未来=空', d.get('count', 0) == 0, str(d.get('count')))

st, d = api('POST', f'/cost/orders/{order1.id}/recompute', T_plain, CID, {})
check('C-API-10 无 cost.manage 重算 403', st == 403, f's={st}')
st, d = api('POST', f'/cost/orders/{order1.id}/recompute', T_perm, CID, {})
check('C-API-11 cost.manage 重算 200', st == 200 and d.get('order_no') == 'TCOS-001',
      f'{st}')
st, d = api('POST', '/cost/orders/999999/recompute', T_perm, CID, {})
check('C-API-12 不存在订单 404', st == 404, f's={st}')

st, d = api('POST', '/cost/orders/recompute-batch', T_perm, CID,
            {'order_ids': [order1.id, order2.id, 999999]})
check('C-API-13 批量重算（成功+失败清单）',
      st == 200 and d.get('success') == [order1.id, order2.id]
      and d.get('failed') == [{'order_id': 999999, 'error': '订单不存在'}]
      and d.get('count') == 2, str(d))
st, d = api('POST', '/cost/orders/recompute-batch', T_perm, CID, {})
check('C-API-14 批量空body 400', st == 400, f'{st}')
st, d = api('POST', '/cost/orders/recompute-batch', T_perm, CID, {'order_ids': []})
check('C-API-15 批量空数组 400', st == 400, f'{st}')
st, d = api('POST', '/cost/orders/recompute-batch', T_perm, CID,
            {'order_ids': ['x']})
check('C-API-16 批量非法元素 400', st == 400, f'{st}')
st, d = api('POST', '/cost/orders/recompute-batch', T_perm, CID,
            {'date_from': str(today)})
check('C-API-17 批量日期范围（当日活跃单全算）',
      st == 200 and sorted(d.get('success', []))
      == sorted([order1.id, order2.id, order3.id]),
      str(d))

# ================= C-REC 工资总额对账单 =================
print('\n---- C-REC 工资总额对账单 ----')
st, d = api('GET', '/cost/wage-reconciliation', T_plain, CID)
check('C-REC-00 无 cost.view 403', st == 403, f's={st}')
st, d = api('GET', '/cost/wage-reconciliation', T_view, CID)
rows = {r['key']: r for r in d.get('rows', [])}
check('C-REC-01 发放总额=已结算快照500（revoked 999 不计）',
      Decimal(d.get('paid_total')) == Decimal('500.00'), str(d.get('paid_total')))
check('C-REC-02 Σ订单人工=151.50 且 diff 列示',
      Decimal(rows.get('order_labor', {}).get('amount', '0')) == Decimal('151.50')
      and Decimal(rows['order_labor']['diff']) == Decimal('-348.50')
      and 'cost_order_cost.labor_cost' in rows['order_labor']['source'],
      str(rows.get('order_labor')))
check('C-REC-03 process 口径=190（未结算）',
      Decimal(rows.get('process_wage', {}).get('amount', '0')) == Decimal('190.00'),
      str(rows.get('process_wage')))
check('C-REC-04 whole 口径=1.50（票面均摊 3/2人）',
      Decimal(rows.get('whole_wage', {}).get('amount', '0')) == Decimal('1.50'),
      str(rows.get('whole_wage')))
check('C-REC-05 commission 口径=38（同组两提成工共享基数缓存）',
      Decimal(rows.get('commission_wage', {}).get('amount', '0')) == Decimal('38.00'),
      str(rows.get('commission_wage')))
check('C-REC-06 fixed 口径=100（期间费用）',
      Decimal(rows.get('fixed_wage', {}).get('amount', '0')) == Decimal('100.00'),
      str(rows.get('fixed_wage')))
check('C-REC-07 工资口径合计=329.50 且 notes 含口径说明',
      Decimal(d.get('wage_total')) == Decimal('329.50') and len(d.get('notes', [])) >= 3,
      str(d.get('wage_total')))
st, d = api('GET', '/cost/wage-reconciliation?date_from=2000-01-01&date_to=2000-01-02',
            T_view, CID)
check('C-REC-08 空期间 paid=0 diff=amount 自身',
      Decimal(d.get('paid_total')) == 0
      and Decimal(d['rows'][0]['diff']) == Decimal(d['rows'][0]['amount']),
      str(d.get('paid_total')))
st, d = api('GET', '/cost/wage-reconciliation?date_from=2099-01-01&date_to=2000-01-01',
            T_view, CID)
check('C-REC-09 起止倒置 400', st == 400, f'{st}')

# ================= 制费（overhead） =================
print('\n---- C-OVH 制费 ----')
st, d = api('PUT', f'/cost/orders/{order1.id}/overhead', T_plain, CID,
            {'overhead_cost': 100})
check('C-OVH-01 无 cost.manage 403', st == 403, f's={st}')
st, d = api('PUT', f'/cost/orders/{order1.id}/overhead', T_perm, CID,
            {'overhead_cost': -1})
check('C-OVH-02 负数 400', st == 400, f'{st}')
st, d = api('PUT', f'/cost/orders/{order1.id}/overhead', T_perm, CID,
            {'overhead_cost': 'abc'})
check('C-OVH-03 非法数字 400', st == 400, f'{st}')
st, d = api('PUT', f'/cost/orders/{order1.id}/overhead', T_perm, CID,
            {'overhead_cost': 100})
check('C-OVH-04 首录制费 200（行已存在→更新）',
      st == 200 and Decimal(d.get('overhead_cost')) == Decimal('100.00'), f'{st} {d}')
oc1.refresh_from_db()
check('C-OVH-05 制费 PUT 翻 stale（埋点④）', oc1.is_stale is True, str(oc1.is_stale))
check('C-OVH-06 制费改动 audit 留痕',
      AuditLog.objects.filter(company=company, type=AuditLog.Type.OTHER,
                              message__contains='订单制费修改').exists())
recompute(company, order1, user=owner)
oc1.refresh_from_db()
check('C-OVH-07 制费后重算：总成本 489.50/单件 3.263/毛利 560.50',
      Decimal(oc1.total_cost) == Decimal('489.50')
      and Decimal(oc1.unit_cost) == Decimal('3.263')
      and Decimal(oc1.gross_profit) == Decimal('560.50')
      and Decimal(oc1.gross_margin) == Decimal('53.38')
      and oc1.is_stale is False,
      f'{oc1.total_cost}/{oc1.unit_cost}/{oc1.gross_margin}/{oc1.is_stale}')

# ================= C-STL stale 埋点 =================
print('\n---- C-STL stale 埋点 ----')
# 埋点① 领料确认
draft = MaterialIssue.objects.create(company=company, issue_no='TCOS-LL9',
                                     issue_at=timezone.now(), operator=owner)
IssueItem.objects.create(issue=draft, production=bed1, lot=lot1, qty=1)
st, _ = api('POST', f'/material-issues/{draft.id}/confirm', T_owner, CID)
oc1.refresh_from_db()
check('C-STL-01 领料确认翻 stale（含跨订单床次）',
      st == 200 and oc1.is_stale is True, f'{st} {oc1.is_stale}')
n = mark_orders_stale(company, [order1.id])
check('C-STL-02 stale 幂等：已 stale 再埋零写入', n == 0, str(n))
# 埋点② 登记 / 撤销
recompute(company, order1, user=owner)
st, d = api('POST', '/records', T_owner, CID,
            {'ticket_id': t6.id, 'process_id': p_sew.id, 'qty': 10})
check('C-STL-03 登记 201 且翻 stale',
      st == 201 and OrderCost.objects.get(order=order1).is_stale is True,
      f'{st}')
rec_id = d.get('id')
recompute(company, order1, user=owner)
st, _ = api('DELETE', f'/records/{rec_id}', T_owner, CID)
check('C-STL-04 撤销翻 stale',
      st == 204 and OrderCost.objects.get(order=order1).is_stale is True,
      f'{st}')
# 未挂单床次登记 → 零写入分支
recompute(company, order1, user=owner)
st, d = api('POST', '/records', T_owner, CID,
            {'ticket_id': t4.id, 'process_id': p_sew.id, 'qty': 1})
check('C-STL-05 无单床次登记：stale 保持 False（无订单可翻）',
      st == 201 and OrderCost.objects.get(order=order1).is_stale is False, f'{st}')
n = mark_ticket_stale(company, t3)
check('C-STL-06 无单床次 ticket 埋点返回 0', n == 0, str(n))
# 埋点③ OrderItem.price 修改
recompute(company, order1, user=owner)
st, d = api('PUT', f'/orders/{order1.id}', T_owner, CID,
            {'style': style.id,
             'items': [{'color': '红', 'size': 'M', 'qty': 100, 'price': 12},
                       {'color': '红', 'size': 'L', 'qty': 50}]})
oc1.refresh_from_db()
check('C-STL-07 订单改价翻 stale + PRICE 留痕',
      st == 200 and oc1.is_stale is True
      and AuditLog.objects.filter(company=company, type=AuditLog.Type.PRICE,
                                  message__contains='色码单价修改').exists(),
      f'{st} {oc1.is_stale}')
recompute(company, order1, user=owner)
st, d = api('PUT', f'/orders/{order1.id}', T_owner, CID,
            {'style': style.id,
             'items': [{'color': '红', 'size': 'M', 'qty': 100, 'price': 12},
                       {'color': '红', 'size': 'L', 'qty': 50}]})
oc1.refresh_from_db()
check('C-STL-08 价格未变不翻 stale', st == 200 and oc1.is_stale is False,
      f'{st} {oc1.is_stale}')
st, d = api('PUT', f'/orders/{order1.id}', T_owner, CID,
            {'style': style.id,
             'items': [{'color': '红', 'size': 'M', 'qty': 100, 'price': -1}]})
check('C-STL-09 负单价 400', st == 400, f'{st}')
st, d = api('PUT', f'/orders/{order1.id}', T_owner, CID,
            {'style': style.id,
             'items': [{'color': '红', 'size': 'M', 'qty': 100, 'price': 'abc'}]})
check('C-STL-10 非法单价 400', st == 400, f'{st}')
# 新建订单带单价 → revenue 可用（POST 侧 price 解析分支）
st, d = api('POST', '/orders', T_owner, CID,
            {'style': style.id, 'order_no': 'TCOS-004',
             'items': [{'color': '白', 'size': 'M', 'qty': 10, 'price': 7}]})
check('C-STL-11 建单带单价 200', st == 201, f'{st} {d}')
order4_id = d.get('id')

# ================= 软删边界 =================
print('\n---- C-DEL 软删边界 ----')
recompute(company, order3, user=owner)
order3.is_deleted = True
order3.save(update_fields=['is_deleted', 'updated_at'])
st2, d2 = api('POST', f'/cost/orders/{order3.id}/recompute', T_perm, CID, {})
st3, d3 = api('PUT', f'/cost/orders/{order3.id}/overhead', T_perm, CID,
              {'overhead_cost': 5})
check('C-DEL-01 软删订单：列表不显+重算404+制费404',
      order3.id not in [
          r['order'] for r in api('GET', '/cost/orders', T_view, CID)[1].get('results', [])]
      and st2 == 404 and st3 == 404, f'{st2}/{st3}')
st, d = api('POST', '/cost/orders/recompute-batch', T_perm, CID,
            {'order_ids': [order3.id]})
check('C-DEL-02 批量遇软删单进 failed',
      d.get('success') == [] and d['failed'][0]['error'] == '订单不存在', str(d))

# ================= C-ISO 公司隔离 =================
print('\n---- C-ISO 公司隔离 ----')
style_b = Style.objects.create(company=company_b, style_no='TCOS-B-S1', name='B厂款')
p_b = Process.objects.create(company=company_b, name='TCOS-B车缝', default_price=1,
                             is_fixed=True, sort=1)
order_b = Order.objects.create(company=company_b, order_no='TCOS-B-001',
                               style=style_b)
OrderItem.objects.create(order=order_b, color='黑', size='M', qty=10,
                         price=Decimal('5'))
bed_b = Production.objects.create(company=company_b, style=style_b, bed_no='TCOS-B-B1',
                                  total_qty=10, order=order_b)
ProductionProcess.objects.create(production=bed_b, process=p_b, price=Decimal('2'))
tb = Ticket.objects.create(company=company_b, production=bed_b, bed_no='TCOS-B-B1',
                           bundle_no='1', color='黑', size='M', qty=10, qr_code='xb')
Record.objects.create(company=company_b, ticket=tb, process=p_b, user=owner_b,
                      qty=10, price=Decimal('2'))
bed_b2 = Production.objects.create(company=company_b, style=style_b,
                                   bed_no='TCOS-B-B2', total_qty=5)
tb2 = Ticket.objects.create(company=company_b, production=bed_b2, bed_no='TCOS-B-B2',
                            bundle_no='1', color='黑', size='M', qty=5, qr_code='xb2')
Record.objects.create(company=company_b, ticket=tb2, process=p_b, user=owner_b,
                      qty=5, price=Decimal('1'))   # 无单床次登记（B厂 no_order_cnt）
ocb = recompute(company_b, order_b, user=owner_b)
check('C-ISO-01 B厂成本独立（labor=20，不含A厂数据）',
      Decimal(ocb.labor_cost) == Decimal('20.00') and ocb.no_order_cnt == 1,
      f'{ocb.labor_cost}/{ocb.no_order_cnt}')
st, d = api('POST', f'/cost/orders/{order_b.id}/recompute', T_perm, CID, {})
check('C-ISO-02 跨厂订单 404', st == 404, f's={st}')
rows_b = api('GET', '/cost/orders', T_owner_b, CID_B)[1].get('results', [])
check('C-ISO-03 列表厂隔离', {r['order_no'] for r in rows_b} == {'TCOS-B-001'},
      str([r['order_no'] for r in rows_b]))

# ================= C-PERM 权限点 =================
print('\n---- C-PERM 权限点 ----')
from apps.core.permissions import PERMISSION_GROUPS, effective_perms, has_perm  # noqa: E402

cost_group = next((g for g in PERMISSION_GROUPS if g['module'] == 'cost'), None)
check('C-PERM-01 cost 组注册（cost.view/cost.manage，legacy=False）',
      cost_group is not None
      and {p['key'] for p in cost_group['points']} == {'cost.view', 'cost.manage'}
      and all(p['legacy'] is False for p in cost_group['points']),
      str(cost_group))
check('C-PERM-02 cost.manage 岗 has_perm 双点',
      has_perm(perm_u.usercompany_set.first() if hasattr(perm_u, 'usercompany_set')
               else UserCompany.objects.get(user=perm_u, company=company), 'cost.manage')
      and has_perm(UserCompany.objects.get(user=perm_u, company=company), 'cost.view'))
check('C-PERM-03 legacy production 岗零授权',
      not has_perm(UserCompany.objects.get(user=legacy_u, company=company), 'cost.view')
      and 'cost.view' not in effective_perms(post_legacy))
check('C-PERM-04 effective_perms 下发双点',
      set(effective_perms(post_perm)) >= {'cost.view', 'cost.manage'},
      str(effective_perms(post_perm)))
st, d = api('PUT', f'/cost/orders/{order1.id}/overhead', T_view, CID,
            {'overhead_cost': 1})
check('C-PERM-05 仅 cost.view 无 manage → 制费 403', st == 403, f's={st}')

# ================= C-CNC 并发/批量 =================
print('\n---- C-CNC 并发/批量 ----')
oc_dup = recompute(company, order1, user=owner)
check('C-CNC-01 重复重算幂等（select_for_update+get_or_create 既有行）',
      oc_dup.pk == oc1.pk and oc_dup.is_stale is False
      and OrderCost.objects.filter(company=company, order=order1).count() == 1,
      str(oc_dup.pk))
import apps.cost.services as costsvc  # noqa: E402

_orig = costsvc._compute_fields


def _boom(c, o):
    raise RuntimeError('boom')


costsvc._compute_fields = _boom
st, d = api('POST', '/cost/orders/recompute-batch', T_perm, CID,
            {'order_ids': [order1.id, order2.id]})
costsvc._compute_fields = _orig
check('C-CNC-02 批量单单失败隔离（异常进 failed 不拖垮整批）',
      st == 200 and d.get('success') == []
      and all('boom' in f['error'] for f in d.get('failed', []))
      and len(d.get('failed', [])) == 2, str(d))
st, d = api('POST', '/cost/orders/recompute-batch', T_perm, CID,
            {'order_ids': [order1.id, order2.id]})
check('C-CNC-03 恢复后批量重算全绿',
      st == 200 and sorted(d.get('success', [])) == [order1.id, order2.id], str(d))
st, d = api('POST', '/cost/orders/recompute-batch', T_perm, CID,
            {'date_to': str(today)})
check('C-CNC-04 批量仅 date_to 端（date_from 缺省分支）',
      st == 200 and sorted(d.get('success', []))
      == sorted([order1.id, order2.id, order4_id]), str(d))
# 制费 PUT 于无成本行订单 → get_or_create 草稿行承接（created 分支）
order5 = Order.objects.create(company=company, order_no='TCOS-005', style=style)
st, d = api('PUT', f'/cost/orders/{order5.id}/overhead', T_perm, CID,
            {'overhead_cost': 5})
row4 = OrderCost.all_objects.filter(company=company, order_id=order5.id).first()
check('C-CNC-05 制费首录建草稿行（draft 承接手录值）',
      st == 200 and row4 is not None and row4.status == 'draft'
      and Decimal(row4.overhead_cost) == Decimal('5.00')
      and Decimal(d.get('overhead_cost')) == Decimal('5.00'),
      f'{st} {row4 and (row4.status, row4.overhead_cost, row4.is_stale)}')
check('C-CNC-06 模型 __str__', str(row4).startswith(f'{CID}:{order5.id}:'),
      str(row4))
recompute(company, order1, user=owner)   # 收尾：恢复 O1=改价后口径（revenue=1200）

# ================= C-PNL 损益视图（2026-11-02 评审 Top10 #2） =================
print('\n---- C-PNL 订单损益 ----')
from apps.finance.models import Receivable  # noqa: E402

oc1 = OrderCost.objects.get(company=company, order=order1)
# 应收两行（出货自动+手工各一行口径）：金额 400 已收 150、金额 600 已收 600
ar1 = Receivable.objects.create(company=company, order=order1,
                                receivable_no=f'AR-PNL1-{order1.id}', amount=Decimal('400'),
                                received_amount=Decimal('150'))
ar2 = Receivable.objects.create(company=company, order=order1,
                                receivable_no=f'AR-PNL2-{order1.id}', amount=Decimal('600'),
                                received_amount=Decimal('600'))
st, d = api('GET', '/cost/orders/pnl', T_perm, CID, None)
row = next((r for r in d.get('results', []) if r.get('order') == order1.id), None) \
    if st == 200 else None
check('C-PNL-01 端点 200 且含 O1 行', st == 200 and row is not None, f's={st}')
check('C-PNL-02 应收聚合两行合计（400+600=1000，不挂单不计）',
      row and Decimal(row['ar_amount']) == Decimal('1000.00')
      and Decimal(row['ar_received']) == Decimal('750.00'),
      str(row and (row.get('ar_amount'), row.get('ar_received'))))
check('C-PNL-03 未回款 ar_gap=250、实收毛利=已收-总成本',
      row and Decimal(row['ar_gap']) == Decimal('250.00')
      and Decimal(row['real_profit']) == Decimal('750.00') - Decimal(row['total_cost']),
      str(row and (row.get('ar_gap'), row.get('real_profit'), row.get('total_cost'))))
check('C-PNL-04 理论口径随行（revenue/gross_profit=revenue-total_cost）',
      row and Decimal(row['revenue']) == Decimal(row['gross_profit'])
      + Decimal(row['total_cost']),
      str(row and (row.get('revenue'), row.get('gross_profit'))))
st, d = api('GET', '/cost/orders/pnl?keyword=TCOS-005', T_perm, CID, None)
check('C-PNL-05 keyword 过滤生效',
      st == 200 and all(r['order_no'] == 'TCOS-005' for r in d.get('results', [])),
      str(st))
st, d = api('GET', '/cost/orders/pnl', T_view, CID, None)
check('C-PNL-06 cost.view 可读（只读页同点）', st == 200, f's={st}')
# 无应收行订单兜底 '0'（None → '0'，前端字符串口径）
row5 = next((r for r in (d.get('results') or []) if r.get('order') == order5.id), None) \
    if st == 200 else None
if not row5:
    st, d = api('GET', '/cost/orders/pnl?keyword=TCOS-005', T_perm, CID, None)
    row5 = next((r for r in (d.get('results') or []) if r.get('order') == order5.id), None)
check('C-PNL-07 无应收行兜 0 字符串',
      row5 and row5['ar_amount'] == '0' and row5['ar_received'] == '0'
      and row5['ar_gap'] == '0',
      str(row5 and (row5['ar_amount'], row5['ar_received'], row5['ar_gap'])))
ar1.delete()
ar2.delete()

# ================= cleanup =================
print('\n---- cleanup ----')
try:
    preclean(company)
    preclean(company_b)
    for p in (post_plain, post_view, post_perm, post_legacy):
        PostM.objects.filter(pk=p.pk).update(permissions=[])
    print('cleanup: done（业务行已清，测试厂/账号复用保留）')
except Exception as e:  # noqa: BLE001
    print('cleanup WARN:', e)

print(f'\n==== 成本域套件：{len(PASS)} pass / {len(FAIL)} fail ====')
if FAIL:
    print('FAILED:', FAIL)
    raise SystemExit(1)
