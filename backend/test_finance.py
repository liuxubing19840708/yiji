# -*- coding: utf-8 -*-
"""
应收域（test_finance）端到端后端验证 —— SA-P2-2 交付（2026-10-24 P2 finance 域）。

覆盖 P2 应收域全部端点与口径（docs/服装ERP全模块设计方案.md §4.3 + P1设计决策表 D4）：
  端点  F-AR-01~06（手工建应收/单号顺延/金额校验/客户订单归属校验）
  列表  F-LIST-01~05（status/customer_id/keyword/date 过滤/作废不显）
  核销  F-RCPT-01~06（部分→partial→settled 流转/超额 400 硬拦/响应双块/audit）
  撤销  F-RVK-01~05（最近一条反向回退/重算 status/无可撤 400/撤后可再收/audit）
  作废  F-VOID-01~04（有核销先撤/作废下架/作废后核销 404/单号不释放）
  估值  F-VAL-01~10（移动加权递推/领用退库回冲/date_to 截断/material 过滤/
                     缺价行剔除/草稿入库剔除/无物料归集/跨厂隔离）
  权限  F-PERM-01~06（401/403/2 段式/effective_perms 下发/legacy 零回归）
  隔离  F-ISO-01~04（跨厂列表不可见/跨厂核销 404/估值隔离）

跑法：manage.py shell -c "exec(compile(open('test_finance.py',encoding='utf-8').read(),'test_finance.py','exec'))"
幂等：自建「财务测试厂(可删)/财务B厂(可删)」并跨次复用（Company.creator PROTECT
不可删）；每轮 preclean 硬清本厂业务行，不依赖 DB 现状；
绝不触碰真实厂（company_id=86 等）。
"""
import json
from datetime import datetime
from decimal import Decimal

from django.contrib.auth.hashers import make_password
from django.test import Client
from django.utils import timezone

from apps.accounts.models import Company, Post as PostM, User, UserCompany
from apps.accounts.services import create_company_with_owner
from apps.core.permissions import effective_perms
from apps.finance.models import Receivable, ReceiptLine
from apps.ops.models import AuditLog
from apps.orders.models import Order, OrderItem
from apps.production.models import Production
from apps.catalog.models import Customer, Style
from apps.tech.models import Material
from apps.warehouse.models import (IssueItem, Lot, MaterialIssue,
                                   ProductionLotLink, StockIn, StockInItem)

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
    '13988712101', '13988712102', '13988712103', '13988712104', '13988712105',
    '13988712106')
CO_A, CO_B = '财务测试厂(可删)', '财务B厂(可删)'


def preclean(company):
    """幂等自清扫：硬清本厂业务行（PROTECT 逆序：流水→应收→入库行→入库单→
    领料行→领料单→血缘→批次→床次→订单→客户→物料）。"""
    ReceiptLine.all_objects.filter(company=company).delete()
    Receivable.all_objects.filter(company=company).delete()
    StockInItem.all_objects.filter(stock_in__company=company).delete()
    StockIn.all_objects.filter(company=company).delete()
    IssueItem.all_objects.filter(issue__company=company).delete()
    MaterialIssue.all_objects.filter(company=company).delete()
    ProductionLotLink.all_objects.filter(production__company=company)._raw_delete(
        ProductionLotLink.all_objects.db)
    Production.all_objects.filter(company=company).delete()
    Lot.all_objects.filter(company=company).delete()
    Material.all_objects.filter(company=company).delete()
    OrderItem.all_objects.filter(order__company=company).delete()
    Order.all_objects.filter(company=company).delete()
    Customer.all_objects.filter(company=company).delete()
    Style.all_objects.filter(company=company).delete()
    AuditLog.objects.filter(company=company).delete()


# ================= 建厂（复用） + preclean =================
company = Company.objects.filter(name=CO_A).first()
if company is None:
    owner = ensure_user(OWNER, '财务厂长')
    company, admin_post = create_company_with_owner(owner, CO_A)
else:
    owner = ensure_user(OWNER, '财务厂长')
    admin_post = PostM.objects.get(company=company, is_admin=True, is_deleted=False)
company_b = Company.objects.filter(name=CO_B).first()
if company_b is None:
    owner_b = ensure_user(H_OWNER_B, '财务B厂长')
    company_b, _ = create_company_with_owner(owner_b, CO_B)
else:
    owner_b = ensure_user(H_OWNER_B, '财务B厂长')
CID, CID_B = company.id, company_b.id

preclean(company)
preclean(company_b)

post_plain = ensure_post(company, '财务普工', [])
post_view = ensure_post(company, '财务查看岗', ['finance.view'])
post_perm = ensure_post(company, '财务管理岗', ['finance.view', 'finance.manage'])
post_legacy = ensure_post(company, '财务旧岗', ['production'])
plain = ensure_user(H_PLAIN, '财务无权员')
view_u = ensure_user(H_VIEW, '财务查看员')
perm_u = ensure_user(H_PERM, '财务管理员')
legacy_u = ensure_user(H_LEGACY, '财务旧岗员')
ensure_member(plain, company, post_plain, group_no=0)
ensure_member(view_u, company, post_view, group_no=0)
ensure_member(perm_u, company, post_perm, group_no=0)
ensure_member(legacy_u, company, post_legacy, group_no=0)

T_owner = login(OWNER)
T_plain = login(H_PLAIN)
T_view = login(H_VIEW)
T_perm = login(H_PERM)
T_legacy = login(H_LEGACY)
T_owner_b = login(H_OWNER_B)
today = timezone.localdate()
ar_prefix = f'AR{today:%Y%m%d}'

# ================= 权限 =================
st, d = api('GET', '/finance/receivables')
check('F-PERM-01 无 token 401', st == 401, f'{st}')
st, d = api('GET', '/finance/receivables', T_plain, CID)
check('F-PERM-02 无权 GET 403', st == 403, f'{st} {d}')
st, d = api('POST', '/finance/receivables', T_plain, CID,
            {'customer': 1, 'amount': 100})
check('F-PERM-02b 无权 POST 403', st == 403, f'{st}')
st, d = api('GET', '/finance/receivables', T_view, CID)
check('F-PERM-03 view 岗 GET 200', st == 200, f'{st} {d}')
st, d = api('POST', '/finance/receivables', T_view, CID,
            {'customer': 1, 'amount': 100})
check('F-PERM-03b view 岗 POST 403', st == 403, f'{st}')
st, d = api('GET', '/finance/stock-valuation', T_view, CID)
check('F-PERM-03c view 岗报表 403（报表归 manage）', st == 403, f'{st}')
st, d = api('GET', '/finance/receivables', T_legacy, CID)
check('F-PERM-06 legacy 岗 finance 403（零回归：production 不覆盖 finance）',
      st == 403, f'{st}')
ep = effective_perms(post_perm)
check('F-PERM-04 财务管理岗 effective_perms 含双点',
      'finance.view' in ep and 'finance.manage' in ep, str(ep))
ep_l = effective_perms(post_legacy)
check('F-PERM-05 legacy 岗不含 finance 点', 'finance.view' not in ep_l
      and 'finance.manage' not in ep_l, str(ep_l))

# ================= 数据工厂 =================
cust = Customer.objects.create(company=company, customer_no='TFIN-C1', name='华东服饰')
cust2 = Customer.objects.create(company=company, customer_no='TFIN-C2', name='华南制衣')
style = Style.objects.create(company=company, style_no='TFIN-S1', name='财务测试款')
order1 = Order.objects.create(company=company, order_no='TFIN-001', style=style,
                              customer=cust)
OrderItem.objects.create(order=order1, color='红', size='M', qty=100,
                         price=Decimal('10'))

# ================= 建应收 =================
st, d = api('POST', '/finance/receivables', T_perm, CID,
            {'customer': cust.id, 'order': order1.id, 'amount': 1000,
             'remark': '首单应收'})
check('F-AR-01 建应收 201（自动号/未收/outstanding）',
      st == 201 and d['receivable_no'].startswith(ar_prefix)
      and d['status'] == 'unpaid' and d['received_amount'] == '0.00'
      and d['outstanding'] == '1000.00' and d['customer_name'] == '华东服饰'
      and d['order_no'] == 'TFIN-001', f'{st} {d}')
ar1_no, ar1 = d['receivable_no'], d['id']
st, d = api('POST', '/finance/receivables', T_perm, CID,
            {'customer': cust2.id, 'amount': 500})
check('F-AR-02 单号当日顺延 +1',
      st == 201 and d['receivable_no'].endswith(f'{int(ar1_no[-3:]) + 1:03d}'),
      f'{st} {d}')
ar2 = d['id']
st, d = api('POST', '/finance/receivables', T_perm, CID,
            {'customer': cust.id, 'amount': 0})
check('F-AR-03 金额 0 → 400', st == 400, f'{st} {d}')
st, d = api('POST', '/finance/receivables', T_perm, CID,
            {'customer': 99999999, 'amount': 100})
check('F-AR-04 客户不存在 → 400', st == 400, f'{st} {d}')
st, d = api('POST', '/finance/receivables', T_owner_b, CID_B,
            {'customer': cust.id, 'amount': 100})
check('F-AR-05 跨厂客户 → 400', st == 400, f'{st} {d}')
st, d = api('POST', '/finance/receivables', T_perm, CID,
            {'customer': cust.id, 'order': 99999999, 'amount': 100})
check('F-AR-05b 订单不存在 → 400', st == 400, f'{st} {d}')
check('F-AR-06 模型 __str__', str(Receivable.objects.get(pk=ar1)) == f'{CID}:{ar1_no}',
      str(Receivable.objects.get(pk=ar1)))

# ================= 核销流转 =================
st, d = api('POST', f'/finance/receivables/{ar1}/receipts', T_perm, CID,
            {'amount': 400, 'method': 'cash', 'handler': '王会计', 'remark': '定金'})
check('F-RCPT-01 核销 400 元 → partial（响应双块）',
      st == 201 and 'receivable' in d and 'receipt' in d
      and d['receivable']['received_amount'] == '400.00'
      and d['receivable']['status'] == 'partial'
      and d['receivable']['outstanding'] == '600.00'
      and d['receipt']['amount'] == '400.00'
      and d['receipt']['method_display'] == '现款'
      and d['receipt']['handler'] == '王会计', f'{st} {d}')
rcpt1 = d['receipt']['id']
st, d = api('POST', f'/finance/receivables/{ar1}/receipts', T_perm, CID,
            {'amount': 600, 'method': 'acceptance'})
check('F-RCPT-02 再核销 600 → settled',
      st == 201 and d['receivable']['received_amount'] == '1000.00'
      and d['receivable']['status'] == 'settled'
      and d['receipt']['method_display'] == '承兑', f'{st} {d}')
rcpt2 = d['receipt']['id']
st, d = api('POST', f'/finance/receivables/{ar1}/receipts', T_perm, CID,
            {'amount': 1})
check('F-RCPT-03 已结清再收 → 400 超额硬拦', st == 400, f'{st} {d}')
st, d = api('POST', f'/finance/receivables/{ar2}/receipts', T_perm, CID,
            {'amount': 600})
check('F-RCPT-03b 超出应收额 → 400（500 上限收 600）', st == 400
      and '超额收款' in str(d), f'{st} {d}')
st, d = api('POST', f'/finance/receivables/{ar2}/receipts', T_perm, CID,
            {'amount': 0})
check('F-RCPT-04 金额 0 → 400', st == 400, f'{st} {d}')
st, d = api('POST', f'/finance/receivables/{ar2}/receipts', T_perm, CID,
            {'amount': 100, 'method': 'bitcoin'})
check('F-RCPT-05 方式非法 → 400', st == 400, f'{st} {d}')
check('F-RCPT-06 核销 audit 留痕',
      AuditLog.objects.filter(company=company, message__contains='应收核销')
      .filter(message__contains=ar1_no).count() >= 2,
      str(AuditLog.objects.filter(company=company).values_list('message')[:3]))

# ================= 契约缺口补测（INT 2026-10-24：详情端点 A + 收款流水端点 B） =================
st, d = api('GET', f'/finance/receivables/{ar1}', T_perm, CID)
check('F-GAP-A01 详情 200（字段与列表行一致/已结清 outstanding=0）',
      st == 200 and d.get('id') == ar1 and d.get('receivable_no') == ar1_no
      and d.get('outstanding') == '0.00' and d.get('status') == 'settled', f'{st} {d}')
st, d = api('GET', f'/finance/receivables/{ar1}', T_view, CID)
check('F-GAP-A02 详情挂 finance.view 200', st == 200, f'{st} {d}')
st, d = api('GET', f'/finance/receivables/{ar1}')
check('F-GAP-A03 无 token → 401', st == 401, f'{st} {d}')
st, d = api('GET', f'/finance/receivables/{ar1}', T_owner_b, CID_B)
check('F-GAP-A04 跨厂详情 → 404（公司隔离）', st == 404, f'{st} {d}')
st, d = api('GET', '/finance/receivables/99999999', T_perm, CID)
check('F-GAP-A05 不存在 → 404', st == 404, f'{st} {d}')
st, d = api('GET', f'/finance/receivables/{ar1}/receipt-lines', T_perm, CID)
check('F-GAP-B01 流水 2 条倒序（最近一笔在前）',
      st == 200 and len(d) == 2 and d[0]['id'] == rcpt2 and d[1]['id'] == rcpt1
      and {x['amount'] for x in d} == {'400.00', '600.00'}, f'{st} {d}')
st, d = api('GET', f'/finance/receivables/{ar1}/receipt-lines', T_view, CID)
check('F-GAP-B02 流水挂 finance.view 200', st == 200, f'{st} {d}')
st, d = api('GET', f'/finance/receivables/{ar1}/receipt-lines', T_owner_b, CID_B)
check('F-GAP-B03 跨厂流水 → 404', st == 404, f'{st} {d}')
st, d = api('GET', f'/finance/receivables/{ar2}/receipt-lines', T_perm, CID)
check('F-GAP-B04 无核销 → 空数组', st == 200 and d == [], f'{st} {d}')

# ================= 撤销 =================
st, d = api('POST', f'/finance/receivables/{ar1}/receipts/revoke', T_perm, CID)
check('F-RVK-01 撤销最近一条（600）→ 回 partial/400',
      st == 200 and d['revoked_receipt_id'] == rcpt2
      and d['receivable']['received_amount'] == '400.00'
      and d['receivable']['status'] == 'partial', f'{st} {d}')
check('F-RVK-01b 流水行软删保留',
      ReceiptLine.all_objects.get(pk=rcpt2).is_deleted is True
      and ReceiptLine.objects.filter(receivable_id=ar1).count() == 1)
st, d = api('POST', f'/finance/receivables/{ar1}/receipts/revoke', T_perm, CID)
check('F-RVK-02 再撤 → 回 unpaid/0',
      st == 200 and d['receivable']['received_amount'] == '0.00'
      and d['receivable']['status'] == 'unpaid', f'{st} {d}')
st, d = api('POST', f'/finance/receivables/{ar1}/receipts/revoke', T_perm, CID)
check('F-RVK-03 无可撤 → 400', st == 400, f'{st} {d}')
st, d = api('POST', f'/finance/receivables/{ar1}/receipts', T_perm, CID,
            {'amount': 1000})
check('F-RVK-04 撤后可再收 → settled',
      st == 201 and d['receivable']['status'] == 'settled', f'{st} {d}')
check('F-RVK-05 撤销 audit 留痕',
      AuditLog.objects.filter(company=company, message__contains='核销撤销')
      .filter(message__contains=ar1_no).count() == 2,
      str(AuditLog.objects.filter(company=company, message__contains='撤销')
          .values_list('message')[:3]))

# ================= 作废 =================
st, d = api('POST', f'/finance/receivables/{ar2}/receipts', T_perm, CID,
            {'amount': 200})
check('F-VOID-00 ar2 预核销 200', st == 201, f'{st} {d}')
st, d = api('POST', f'/finance/receivables/{ar2}/void', T_perm, CID)
check('F-VOID-01 有核销作废 → 400', st == 400
      and '先' in str(d), f'{st} {d}')
st, d = api('POST', f'/finance/receivables/{ar2}/receipts/revoke', T_perm, CID)
check('F-VOID-01b 撤销后可作废（前置解除）', st == 200, f'{st} {d}')
st, d = api('POST', f'/finance/receivables/{ar2}/void', T_perm, CID)
check('F-VOID-02 作废 200 voided',
      st == 200 and d.get('voided') is True and d['receivable_no'].startswith(ar_prefix),
      f'{st} {d}')
check('F-VOID-02b 作废 audit 留痕',
      AuditLog.objects.filter(company=company, message__contains='应收作废').count() == 1)
st, d = api('GET', '/finance/receivables', T_view, CID)
nos = [r['receivable_no'] for r in d['results']]
check('F-VOID-03 作废行列表不显',
      st == 200 and ar2 not in [r['id'] for r in d['results']], f'{st} {nos}')
st, d = api('POST', f'/finance/receivables/{ar2}/receipts', T_perm, CID,
            {'amount': 100})
check('F-VOID-03b 作废后核销 404', st == 404, f'{st} {d}')
st, d = api('POST', '/finance/receivables', T_perm, CID,
            {'customer': cust.id, 'amount': 300})
check('F-VOID-04 单号顺延不释放（all_objects 扫描，001/002 后作废仍顺延至 003）',
      st == 201 and d['receivable_no'].endswith(
          f'{int(ar1_no[-3:]) + 2:03d}'), f'{st} {d}')
ar4 = d['id']
st, d = api('POST', '/finance/receivables', T_perm, CID,
            {'customer': cust2.id, 'amount': 100})
ar5 = d['id']

# ================= 列表过滤 =================
st, d = api('GET', '/finance/receivables?status=settled', T_view, CID)
check('F-LIST-01 status=settled 只出已结清',
      st == 200 and {r['id'] for r in d['results']} == {ar1}, f'{st} {d}')
st, d = api('GET', f'/finance/receivables?customer_id={cust2.id}', T_view, CID)
check('F-LIST-02 customer_id 过滤',
      st == 200 and all(r['customer'] == cust2.id for r in d['results'])
      and len(d['results']) == 1, f'{st} {d}')
st, d = api('GET', '/finance/receivables?keyword=华南', T_view, CID)
check('F-LIST-03 keyword=客户名', st == 200 and len(d['results']) == 1
      and d['results'][0]['customer_name'] == '华南制衣', f'{st} {d}')
st, d = api('GET', '/finance/receivables?keyword=TFIN-001', T_view, CID)
check('F-LIST-03b keyword=订单号', st == 200 and len(d['results']) == 1
      and d['results'][0]['order_no'] == 'TFIN-001', f'{st} {d}')
st, d = api('GET', f'/finance/receivables?date_from={today:%Y-%m-%d}&date_to={today:%Y-%m-%d}',
            T_view, CID)
check('F-LIST-04 当日 date 过滤全量命中', st == 200 and d['count'] == 3, f'{st} {d}')
st, d = api('GET', f'/finance/receivables?date_from={today:%Y-%m-%d}&date_to={today:%Y-%m-%d}&status=unpaid',
            T_view, CID)
check('F-LIST-04b date+status 组合（ar4/ar5 unpaid）',
      st == 200 and {r['id'] for r in d['results']} == {ar4, ar5}, f'{st} {d}')

# ================= stock-valuation（移动加权 D4 第二口径） =================
D = timezone.get_current_timezone()


def dt(*a):
    return timezone.make_aware(datetime(*a))


m1 = Material.objects.create(company=company, code='TFIN-M1', name='主面料',
                             category=Material.Category.FABRIC, unit='米')
m2 = Material.objects.create(company=company, code='TFIN-M2', name='辅料',
                             category=Material.Category.ACCESSORY, unit='个')
lot1 = Lot.objects.create(company=company, lot_no='TFIN-L1', fabric_name='主面料',
                          qty=200, unit='米')
lot3 = Lot.objects.create(company=company, lot_no='TFIN-L3', fabric_name='无名批次',
                          qty=50, unit='米')

si1 = StockIn.objects.create(company=company, in_no='TFIN-SI1', in_at=dt(2026, 10, 1, 10, 0),
                             status=StockIn.Status.DONE)
StockInItem.objects.create(stock_in=si1, lot=lot1, lot_no=lot1.lot_no,
                           fabric_name='主面料', qty=100, unit='米',
                           unit_price=Decimal('5'), material=m1)
si2 = StockIn.objects.create(company=company, in_no='TFIN-SI2', in_at=dt(2026, 10, 2, 10, 0),
                             status=StockIn.Status.DONE)
StockInItem.objects.create(stock_in=si2, lot=lot1, lot_no=lot1.lot_no,
                           fabric_name='主面料', qty=100, unit='米',
                           unit_price=Decimal('7'), material=m1)
# 缺价行 + 草稿单：不进额不进量
si3 = StockIn.objects.create(company=company, in_no='TFIN-SI3', in_at=dt(2026, 10, 3, 10, 0),
                             status=StockIn.Status.DONE)
StockInItem.objects.create(stock_in=si3, lot=lot1, lot_no=lot1.lot_no,
                           fabric_name='主面料', qty=40, unit='米',
                           unit_price=None, material=m1)
si_draft = StockIn.objects.create(company=company, in_no='TFIN-SI4',
                                  in_at=dt(2026, 10, 3, 11, 0),
                                  status=StockIn.Status.DRAFT)
StockInItem.objects.create(stock_in=si_draft, lot=lot1, lot_no=lot1.lot_no,
                           fabric_name='主面料', qty=999, unit='米',
                           unit_price=Decimal('1'), material=m1)
# 无物料批次（material 归集 None 组）
si4 = StockIn.objects.create(company=company, in_no='TFIN-SI5', in_at=dt(2026, 10, 4, 10, 0),
                             status=StockIn.Status.DONE)
StockInItem.objects.create(stock_in=si4, lot=lot3, lot_no=lot3.lot_no,
                           fabric_name='无名批次', qty=50, unit='米',
                           unit_price=Decimal('2'))

bed1 = Production.objects.create(company=company, style=style, bed_no='TFIN-B1',
                                 total_qty=100, order=order1)


def mk_issue(no, mtype, lot, qty, when):
    mi = MaterialIssue.objects.create(company=company, issue_no=no, type=mtype,
                                      issue_at=when,
                                      status=MaterialIssue.Status.CONFIRMED)
    IssueItem.objects.create(issue=mi, production=bed1, lot=lot, qty=qty,
                             material=m1 if mtype != 'x' else None)
    return mi


mk_issue('TFIN-MI1', MaterialIssue.Type.ISSUE, lot1, 50, dt(2026, 10, 5, 10, 0))
mk_issue('TFIN-MI2', MaterialIssue.Type.RETURN, lot1, 20, dt(2026, 10, 6, 10, 0))
# 未确认领料不进估值
mi_draft = MaterialIssue.objects.create(company=company, issue_no='TFIN-MI3',
                                        issue_at=dt(2026, 10, 7, 10, 0))
IssueItem.objects.create(issue=mi_draft, production=bed1, lot=lot1, qty=999,
                         material=m1)

st, d = api('GET', '/finance/stock-valuation', T_perm, CID)
rows = {r['material_id']: r for r in d['rows']}
r1, r_none = rows.get(m1.id), rows.get(None)
check('F-VAL-01 加权递推：两笔 5/7 → wavg 6.000（Σ额/Σ量）',
      st == 200 and r1 and r1['in_qty'] == '200.00' and r1['in_amt'] == '1200.00'
      and r1['wavg_price'] == '6.000' and r1['balance_qty'] == '170.00'
      and r1['balance_amt'] == '1020.00', f'{st} {d}')
check('F-VAL-02 领用 50 按加权出账 + 退料 20 冲减净领用 → 净领 30/结存 170/1020',
      r1['issued_qty'] == '30.00' and r1['balance_qty'] == '170.00'
      and r1['balance_amt'] == '1020.00' and r1['wavg_price'] == '6.000',
      str(r1))
check('F-VAL-03 缺价行剔除计数（草稿单不计）',
      r1['missing_price_rows'] == 1 and r1['in_qty'] == '200.00', str(r1))
check('F-VAL-04 无物料归集 → material=None 独立组',
      r_none is not None and r_none['material_name'] == ''
      and r_none['balance_qty'] == '50.00'
      and r_none['wavg_price'] == '2.000', str(r_none))
check('F-VAL-05 未确认领料不计入（999 不在结存）',
      r1['balance_qty'] == '170.00', str(r1))
check('F-VAL-06 批次明细在 lots 内',
      len(r1['lots']) == 1 and r1['lots'][0]['lot_no'] == 'TFIN-L1'
      and r1['lots'][0]['balance_qty'] == '170.00', str(r1['lots']))
check('F-VAL-07 note 双口径说明',
      '双口径' in d.get('note', '') and '批次价' in d.get('note', ''), d.get('note'))

st, d = api('GET', '/finance/stock-valuation?date_to=2026-10-01', T_perm, CID)
rows = {r['material_id']: r for r in d['rows']}
check('F-VAL-08 date_to 截断到首笔入库 → wavg 5.000/结存 100',
      rows.get(m1.id, {}).get('wavg_price') == '5.000'
      and rows.get(m1.id, {}).get('balance_qty') == '100.00'
      and rows.get(m1.id, {}).get('balance_amt') == '500.00', f'{st} {d}')

st, d = api('GET', f'/finance/stock-valuation?material_id={m1.id}', T_perm, CID)
check('F-VAL-09 material_id 过滤只出该物料组',
      st == 200 and len(d['rows']) == 1 and d['rows'][0]['material_id'] == m1.id,
      f'{st} {d}')
st, d = api('GET', '/finance/stock-valuation?material_id=99999999', T_perm, CID)
check('F-VAL-09b 不存在物料 → 空组', st == 200 and d['rows'] == [], f'{st} {d}')
st, d = api('GET', '/finance/stock-valuation?material_id=abc', T_perm, CID)
check('F-VAL-09c material_id 非整数 → 400', st == 400, f'{st} {d}')

# B 厂批次隔离
lot_b = Lot.objects.create(company=company_b, lot_no='TFIN-LB1', fabric_name='B厂面料',
                           qty=10, unit='米')
si_b = StockIn.objects.create(company=company_b, in_no='TFIN-SIB1',
                              in_at=dt(2026, 10, 2, 10, 0), status=StockIn.Status.DONE)
StockInItem.objects.create(stock_in=si_b, lot=lot_b, lot_no=lot_b.lot_no,
                           fabric_name='B厂面料', qty=10, unit='米',
                           unit_price=Decimal('99'))
st, d = api('GET', '/finance/stock-valuation', T_perm, CID)
tot = sum(Decimal(r['balance_amt']) for r in d['rows'])
check('F-VAL-10 A 厂估值不含 B 厂批次（1020+100=1120）',
      st == 200 and tot == Decimal('1120.00'), f'{st} {tot}')
st, d = api('GET', '/finance/stock-valuation', T_owner_b, CID_B)
tot_b = sum(Decimal(r['balance_amt']) for r in d['rows'])
check('F-ISO-03 B 厂估值只含本厂（10×99=990）',
      st == 200 and tot_b == Decimal('990.00'), f'{st} {d}')

# ================= 跨厂隔离（应收） =================
cust_b = Customer.objects.create(company=company_b, customer_no='TFIN-CB1',
                                 name='B厂客户')
st, d = api('POST', '/finance/receivables', T_owner_b, CID_B,
            {'customer': cust_b.id, 'amount': 800})
check('F-ISO-00 B 厂建应收（无客户也可建）', st == 201, f'{st} {d}')
ar_b = d['id']
st, d = api('GET', '/finance/receivables', T_perm, CID)
check('F-ISO-01 A 厂列表不见 B 厂应收',
      st == 200 and ar_b not in [r['id'] for r in d['results']], f'{st} {d}')
st, d = api('POST', f'/finance/receivables/{ar_b}/receipts', T_perm, CID,
            {'amount': 100})
check('F-ISO-02 A 厂核销 B 厂应收 → 404', st == 404, f'{st} {d}')
st, d = api('POST', f'/finance/receivables/{ar_b}/void', T_owner, CID)
check('F-ISO-04 A 厂作废 B 厂应收 → 404', st == 404, f'{st} {d}')
st, d = api('POST', f'/finance/receivables/{ar_b}/receipts', T_owner_b, CID_B,
            {'amount': 800})
check('F-ISO-05 B 厂自己核销正常', st == 201
      and d['receivable']['status'] == 'settled', f'{st} {d}')

# ================= 覆盖补缺（服务级守卫/重试/估值边角，门禁 missing=0） =================
from unittest.mock import patch

from rest_framework.exceptions import ValidationError as VE

from apps.finance import services as fin_svc

# 服务级直调校验（视图层 serializer 先拦，服务内防线须直调覆盖）
try:
    fin_svc.create_receivable(company, customer=cust, amount=0)
    ok = False
except VE:
    ok = True
check('F-COV-01 service 建应收 amount<=0 400', ok)
try:
    fin_svc.create_receivable(company, customer=cust_b, amount=100)
    ok = False
except VE:
    ok = True
check('F-COV-02 service 跨厂客户 400', ok)
order_b = Order.objects.create(company=company_b, order_no='TFIN-B01')
try:
    fin_svc.create_receivable(company, customer=cust, amount=100, order=order_b)
    ok = False
except VE:
    ok = True
check('F-COV-03 service 跨厂订单 400', ok)
rec4 = Receivable.objects.get(pk=ar4)
try:
    fin_svc.add_receipt(company, rec4, amount=0)
    ok = False
except VE:
    ok = True
check('F-COV-04 service 核销 amount<=0 400', ok)
rec5, line_x = fin_svc.add_receipt(company, rec4, amount=10, method='weird',
                                   handler='x')
check('F-COV-05 非法方式直调回落 other',
      line_x.method == ReceiptLine.Method.OTHER
      and str(line_x).startswith(f'{ar4}:'), str(line_x))
fin_svc.revoke_last_receipt(company, rec4)      # 还原 ar4 unpaid（补缺不留痕）

# 并发撞号重试：patch 单号发生器首返回已占用号 → IntegrityError → 候选号 +1 重试
stray_no = f'{ar_prefix}901'
Receivable.all_objects.create(company=company, receivable_no=stray_no, amount=1)
Receivable.all_objects.create(company=company, receivable_no=f'{ar_prefix}AB',
                              amount=1)          # 非数字后缀：gen 扫描跳过分支
with patch('apps.finance.services.gen_receivable_no',
           side_effect=[stray_no, f'{ar_prefix}902']):
    rec_r = fin_svc.create_receivable(company, customer=cust, amount=50)
check('F-COV-06 撞号重试（IntegrityError→候选号+1）',
      rec_r.receivable_no == f'{ar_prefix}902', rec_r.receivable_no)
from django.db import IntegrityError as _IE
try:
    with patch('apps.finance.services.gen_receivable_no',
               side_effect=[stray_no, stray_no, stray_no]):
        fin_svc.create_receivable(company, customer=cust, amount=50)
    ok = False
except _IE:
    ok = True
check('F-COV-14 三连撞号耗尽重试 → IntegrityError 上抛（不静默）', ok)
check('F-COV-15 单号扫描跳过非数字后缀（直调 gen）',
      fin_svc.gen_receivable_no(company) == f'{ar_prefix}903',
      fin_svc.gen_receivable_no(company))
check('F-COV-07 ReceiptLine.__str__', str(line_x).startswith(f'{ar4}:'))

st, d = api('GET', '/finance/receivables?status=bogus', T_view, CID)
check('F-COV-08 status 非法 → 400', st == 400, f'{st} {d}')

# 估值边角：混合单位/净零批次/无批次行/0 价行/全领完结存 0
lot_mix = Lot.objects.create(company=company, lot_no='TFIN-LM', fabric_name='主面料',
                             qty=20, unit='码')
si5 = StockIn.objects.create(company=company, in_no='TFIN-SI6', in_at=dt(2026, 10, 24, 9, 0),
                             status=StockIn.Status.DONE)
StockInItem.objects.create(stock_in=si5, lot=lot_mix, lot_no=lot_mix.lot_no,
                           fabric_name='主面料', qty=20, unit='码',
                           unit_price=Decimal('3'), material=m1)
StockInItem.objects.create(stock_in=si5, lot=None, lot_no='NOLOT',
                           fabric_name='无批次行', qty=5, unit='米',
                           unit_price=Decimal('1'))          # 无批次行：跳过
si6 = StockIn.objects.create(company=company, in_no='TFIN-SI7', in_at=dt(2026, 10, 24, 9, 30),
                             status=StockIn.Status.DONE)
StockInItem.objects.create(stock_in=si6, lot=lot3, lot_no=lot3.lot_no,
                           fabric_name='无名批次', qty=8, unit='米',
                           unit_price=Decimal('0'))          # 0 价行：计缺价
StockInItem.objects.create(stock_in=si6, lot=None, lot_no='NOLOT2',
                           fabric_name='无批次缺价行', qty=5, unit='米',
                           unit_price=None)                  # 无批次且缺价：双跳过
lot_zero = Lot.objects.create(company=company, lot_no='TFIN-LZ', fabric_name='辅料',
                              qty=10, unit='个')
si7 = StockIn.objects.create(company=company, in_no='TFIN-SI8', in_at=dt(2026, 10, 24, 10, 0),
                             status=StockIn.Status.DONE)
StockInItem.objects.create(stock_in=si7, lot=lot_zero, lot_no=lot_zero.lot_no,
                           fabric_name='辅料', qty=10, unit='个',
                           unit_price=Decimal('2'), material=m2)
mk_issue('TFIN-MI4', MaterialIssue.Type.ISSUE, lot_zero, 10,
         dt(2026, 10, 24, 11, 0))                              # 全领完 → 结存 0
lot_net0 = Lot.objects.create(company=company, lot_no='TFIN-LN', fabric_name='净零',
                              qty=0, unit='米')
mk_issue('TFIN-MI5', MaterialIssue.Type.ISSUE, lot_net0, 10, dt(2026, 10, 24, 12, 0))
mk_issue('TFIN-MI6', MaterialIssue.Type.RETURN, lot_net0, 10, dt(2026, 10, 24, 13, 0))

st, d = api('GET', '/finance/stock-valuation', T_perm, CID)
rows = {r['material_id']: r for r in d['rows']}
r1, r_none, r2 = rows.get(m1.id), rows.get(None), rows.get(m2.id)
check('F-COV-09 混合单位组 unit 置空（Σ直加 wavg=5.684 仅供参考，以批次明细为准）',
      r1 is not None and r1['unit'] == '' and r1['balance_qty'] == '190.00'
      and r1['balance_amt'] == '1080.00' and r1['wavg_price'] == '5.684', str(r1))
check('F-COV-10 0 价行计缺价（None 组 missing=1）',
      r_none is not None and r_none['missing_price_rows'] == 1
      and r_none['balance_qty'] == '50.00', str(r_none))
check('F-COV-11 全领完结存 0 → wavg 0（else 分支）',
      r2 is not None and r2['balance_qty'] == '0.00'
      and r2['wavg_price'] == '0.000' and r2['in_qty'] == '10.00'
      and r2['issued_qty'] == '10.00', str(r2))
check('F-COV-12 净零批次（领=退）不进报表',
      all(lt['lot_no'] != 'TFIN-LN'
          for r in d['rows'] for lt in r['lots']), str(d['rows']))
check('F-COV-13 无批次行不进估值（NOLOT 不出现）',
      all(lt['lot_no'] != 'NOLOT'
          for r in d['rows'] for lt in r['lots']), str(d['rows']))

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

print(f'\n==== 应收域套件：{len(PASS)} pass / {len(FAIL)} fail ====')
if FAIL:
    print('FAILED:', FAIL)
    raise SystemExit(1)
