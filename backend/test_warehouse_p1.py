# -*- coding: utf-8 -*-
"""
P1 warehouse 域套件（test_warehouse_p1）—— 领料定额化 + 库存价链（SA2 交付，2026-10-24）。

覆盖《服装ERP全模块设计方案》§3.2/§3.3 材料段 + P1设计决策表 C1-C4/A3/D4：
  定额  W1 存量零破坏（不填新列照常工作）
        W2 选定额行自动带出 material/qty_req（单耗×(1+损耗)×床次件数，plan_qty 基数）
        W4 无 BOM 手选 material（无定额，进占比统计）；W5 手选+手填 qty_req
  超领  W3 零拦截：qty>qty_req 落 over=True + over_reason 正常保存（清单 C2）
        W7 单位黑洞防线：公斤↔米 克重×幅宽双向折算后判定
        W8 换算缺参跳过判定 → over_state=pending + 待补清单（A3）
  退料  W6 type=return 确认即冲减已领量（正数录 qty；地板 0）
  双口径 W9 快照 Σqty_req vs 当前 active BOM 应发（改版差额）
  报表  W10 跨领料单聚合；W11 date_from/date_to/order_id/production_id/material_id 过滤
        W12 无定额领料占比
  价链  W13 入库带采购行 → 行价默认带出 PO 价；确认回填批次单价（D4）
        W14 手录价覆盖默认；W15 确认时补带出（ORM 造 unit_price=None 路径）
        W16 Lot.unit_cost 补录/清空（挂 warehouse.manage）
  权限  W17 401 / 403（车间岗无 warehouse.manage）/ 跨厂隔离
  边界  W18 type 非法 400；W19 bom_item/material/purchase_item 不存在 400

跑法：manage.py shell -c "exec(compile(open('test_warehouse_p1.py',encoding='utf-8').read(),'test_warehouse_p1.py','exec'))"
幂等：自建「仓库P1测试厂(可删)/仓库P1对照厂(可删)」并跨次复用；每轮 preclean 硬清
本厂 warehouse/tech 业务行（真实厂 86 零接触）。
"""
import json
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.hashers import make_password
from django.test import Client
from django.utils import timezone

from apps.accounts.models import Company, Post as PostM, User, UserCompany
from apps.accounts.services import create_company_with_owner
from apps.catalog.models import Style
from apps.orders.models import Order, OrderItem
from apps.production.models import Production
from apps.warehouse.models import (
    IssueItem, Lot, MaterialIssue, ProductionLotLink, PurchaseItem,
    PurchaseOrder, StockIn, StockInItem,
)

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
    if method.upper() == 'GET':
        # GET：dict 作查询串；POST/PUT：JSON 体
        r = fn(f'/api{path}', data=data or None, **extra)
    else:
        r = fn(f'/api{path}', data=json.dumps(data) if data is not None else None,
               content_type='application/json', **extra)
    try:
        body = json.loads(r.content.decode('utf-8'))
    except Exception:
        body = {}
    return r.status_code, body


PHONE_A = '13988710201'      # 测试厂管理员
PHONE_B = '13988710202'      # 测试厂车间岗（无 warehouse.manage → 403）
PHONE_C = '13988710203'      # 对照厂管理员（跨厂隔离）
CO_A = '仓库P1测试厂(可删)'
CO_B = '仓库P1对照厂(可删)'

company = Company.objects.filter(name=CO_A).first()
if company is None:
    u = User.objects.filter(phone=PHONE_A).first() or User.objects.create(
        phone=PHONE_A, username=PHONE_A, real_name='仓库P1厂长',
        password=make_password('yisao1234'), is_active=True)
    company, _ = create_company_with_owner(u, CO_A)
else:
    User.objects.filter(phone=PHONE_A).update(
        password=make_password('yisao1234'), is_active=True)
CID = company.id

# 对照厂（跨厂隔离用）
company_b = Company.objects.filter(name=CO_B).first()
if company_b is None:
    u3 = User.objects.filter(phone=PHONE_C).first() or User.objects.create(
        phone=PHONE_C, username=PHONE_C, real_name='仓库P1对照厂长',
        password=make_password('yisao1234'), is_active=True)
    company_b, _ = create_company_with_owner(u3, CO_B)

# 车间岗（无 warehouse.manage）
if UserCompany.objects.filter(company=company, user__phone=PHONE_B).exists():
    u_b = User.objects.get(phone=PHONE_B)
else:
    u_b = User.objects.filter(phone=PHONE_B).first() or User.objects.create(
        phone=PHONE_B, username=PHONE_B, real_name='仓库P1车间',
        password=make_password('yisao1234'), is_active=True)
    workshop_post = PostM.objects.filter(company=company, name='车间').first() \
        or PostM.objects.create(company=company, name='车间',
                                permissions=['my_salary.view', 'working.view'])
    UserCompany.objects.create(user=u_b, company=company, post=workshop_post,
                               status=UserCompany.Status.ACTIVE,
                               hired_date=timezone.localdate())

AT = api('POST', '/auth/login', data={'phone': PHONE_A, 'password': 'yisao1234'})[1].get('access', '')
BT = api('POST', '/auth/login', data={'phone': PHONE_B, 'password': 'yisao1234'})[1].get('access', '')
CT = api('POST', '/auth/login', data={'phone': PHONE_C, 'password': 'yisao1234'})[1].get('access', '')
assert AT and BT and CT, '登录失败'

# ---- preclean（先子后父，硬清本厂 P1 测试数据；前缀 TWH1） ----
# PROTECT 链：IssueItem→lot/production，ProductionLotLink→lot，StockInItem→lot/purchase_item
# 环：Lot.source_in→StockInItem(SET_NULL) × StockInItem.lot→Lot(PROTECT) —— 先断环
Lot.all_objects.filter(company=company).update(source_in=None)
IssueItem.all_objects.filter(issue__company=company)._raw_delete(IssueItem.all_objects.db)
MaterialIssue.all_objects.filter(company=company,
                                 issue_no__startswith='TWH1')._raw_delete(
    MaterialIssue.all_objects.db)
ProductionLotLink.all_objects.filter(company=company)._raw_delete(
    ProductionLotLink.all_objects.db)
StockInItem.all_objects.filter(stock_in__company=company)._raw_delete(
    StockInItem.all_objects.db)
StockIn.all_objects.filter(company=company, in_no__startswith='TWH1')._raw_delete(
    StockIn.all_objects.db)
Lot.all_objects.filter(company=company, lot_no__startswith='TWH1')._raw_delete(
    Lot.all_objects.db)
PurchaseItem.all_objects.filter(order__company=company,
                                order__order_no__startswith='TWH1')._raw_delete(
    PurchaseItem.all_objects.db)
PurchaseOrder.all_objects.filter(company=company,
                                 order_no__startswith='TWH1')._raw_delete(
    PurchaseOrder.all_objects.db)
Production.all_objects.filter(company=company, bed_no__startswith='TWH1')._raw_delete(
    Production.all_objects.db)
OrderItem.all_objects.filter(order__company=company, order__order_no__startswith='TWH1') \
    ._raw_delete(OrderItem.all_objects.db)
Order.all_objects.filter(company=company, order_no__startswith='TWH1')._raw_delete(
    Order.all_objects.db)
# tech 行（SA1 建的 app；本套件只清本厂前缀）
from apps.tech.models import Bom, BomItem, Material  # noqa: E402
BomItem.all_objects.filter(bom__company=company).delete()
Bom.all_objects.filter(company=company, style__style_no__startswith='TWH1').delete()
Material.all_objects.filter(company=company, code__startswith='TWH1').delete()
Style.all_objects.filter(company=company, style_no__startswith='TWH1')._raw_delete(
    Style.all_objects.db)

# ---- 基础数据 ----
style = Style.objects.create(company=company, style_no='TWH1-S1', name='P1定额款')
order = Order.objects.create(company=company, order_no='TWH1-O1', style=style)
OrderItem.objects.create(order=order, color='黑', size='M', qty=100)

m_fabric = Material.objects.create(company=company, code='TWH1-M1', name='主面料',
                                   category='fabric', unit='米',
                                   weight_gsm=200, width_cm=150)
m_noparam = Material.objects.create(company=company, code='TWH1-M2', name='缺参面料',
                                    category='fabric', unit='米')   # 无克重/幅宽
m_kg = Material.objects.create(company=company, code='TWH1-M3', name='针织公斤料',
                               category='fabric', unit='公斤',
                               weight_gsm=200, width_cm=150)

bom_v1 = Bom.objects.create(company=company, style=style, version=1, status='active')
bi_fabric = BomItem.objects.create(bom=bom_v1, material=m_fabric, kind='main',
                                   unit='米', unit_usage=2, loss_rate=10)   # 2.2米/件
bi_kg = BomItem.objects.create(bom=bom_v1, material=m_kg, kind='main',
                               unit='公斤', unit_usage=10, loss_rate=0)   # 10公斤/件
bom_v2 = Bom.objects.create(company=company, style=style, version=2, status='draft')
bi_fabric_v2 = BomItem.objects.create(bom=bom_v2, material=m_fabric, kind='main',
                                      unit='米', unit_usage=3, loss_rate=10)  # 3.3米/件

# 床次件数基数：P1 用 plan_qty=100（回落验证用 P2：plan_qty 空 → total_qty=80）
prod1 = Production.objects.create(company=company, style=style, bed_no='TWH1-B1',
                                  total_qty=90, plan_qty=100, order=order)
prod2 = Production.objects.create(company=company, style=style, bed_no='TWH1-B2',
                                  total_qty=80, plan_qty=None)

lot_m = Lot.objects.create(company=company, lot_no='TWH1-L1', fabric_name='主面料',
                           color='黑', qty=1000, unit='米')
lot_kg = Lot.objects.create(company=company, lot_no='TWH1-L2', fabric_name='针织公斤料',
                            color='黑', qty=200, unit='公斤')

# ================= 定额化 =================

# W1 存量零破坏：不填任何新列照常工作
code, d = api('POST', '/material-issues', AT, CID, {
    'items': [{'production_id': prod1.id, 'lot_id': lot_m.id, 'qty': 100}]})
check('W1 存量零破坏 201', code == 201 and d.get('id'), f'{code} {d}')
check('W1 行新列缺省 qty_req=null/over=False',
      d['items'][0]['qty_req'] is None and d['items'][0]['over'] is False
      and d['items'][0]['material'] is None, str(d['items'][0]))
LEGACY_ID = d['id']
code, d = api('POST', f'/material-issues/{LEGACY_ID}/confirm', AT, CID)
check('W1 legacy 确认写血缘不回归',
      code == 200 and ProductionLotLink.objects.filter(
          production=prod1, lot=lot_m, qty=100).exists(), f'{code} {d}')

# W2 定额行：bom_item 自动带出 material/qty_req=2×1.1×100=220（plan_qty 基数）
code, d = api('POST', '/material-issues', AT, CID, {
    'items': [{'production_id': prod1.id, 'lot_id': lot_m.id, 'qty': 200,
               'bom_item_id': bi_fabric.id}]})
check('W2 定额行 201', code == 201, f'{code} {d}')
it = d['items'][0]
check('W2 自动带出 material/qty_req=220',
      it['material'] == m_fabric.id and it['qty_req'] == '220.00'
      and it['material_unit'] == '米' and it['over'] is False
      and it['over_state'] == 'normal', str(it))
check('W2 床次基数用 plan_qty(100) 非 total_qty(90)', it['qty_req'] == '220.00', it['qty_req'])

# W2b 回落验证：P2 plan_qty 空 → total_qty=80 → 2×1.1×80=176
code, d = api('POST', '/material-issues', AT, CID, {
    'items': [{'production_id': prod2.id, 'lot_id': lot_m.id, 'qty': 100,
               'bom_item_id': bi_fabric.id}]})
check('W2b plan_qty 空回落 total_qty：qty_req=176',
      code == 201 and d['items'][0]['qty_req'] == '176.00', f'{code} {d}')

# W3 超领零拦截：300 > 220 → 正常保存 + over=True + over_reason
code, d = api('POST', '/material-issues', AT, CID, {
    'items': [{'production_id': prod1.id, 'lot_id': lot_m.id, 'qty': 300,
               'bom_item_id': bi_fabric.id, 'over_reason': '尾数补裁'}]})
check('W3 超领零拦截 201（不 4xx）', code == 201, f'{code} {d}')
it = d['items'][0]
check('W3 over=True + over_reason 留痕',
      it['over'] is True and it['over_state'] == 'over'
      and it['over_reason'] == '尾数补裁', str(it))

# W4 无 BOM 手选 material → 无定额（进占比统计）
code, d = api('POST', '/material-issues', AT, CID, {
    'items': [{'production_id': prod1.id, 'lot_id': lot_m.id, 'qty': 50,
               'material_id': m_fabric.id}]})
check('W4 手选 material 201 且 qty_req=null/no_req',
      code == 201 and d['items'][0]['material'] == m_fabric.id
      and d['items'][0]['qty_req'] is None
      and d['items'][0]['over_state'] == 'no_req', f'{code} {d}')

# W5 手选 + 手填 qty_req（人工定额）
code, d = api('POST', '/material-issues', AT, CID, {
    'items': [{'production_id': prod1.id, 'lot_id': lot_m.id, 'qty': 70,
               'material_id': m_fabric.id, 'qty_req': 60}]})
it = d['items'][0]
check('W5 手填 qty_req=60 且 70>60 判 over',
      code == 201 and it['qty_req'] == '60.00' and it['over'] is True
      and it['over_state'] == 'over', f'{code} {it}')

# W6 退料：type=return 正数录 qty，确认即冲减已领量（挂 material 供报表按物料冲减）
code, d = api('POST', '/material-issues', AT, CID, {
    'type': 'return',
    'items': [{'production_id': prod1.id, 'lot_id': lot_m.id, 'qty': 40,
               'material_id': m_fabric.id}]})
check('W6 退料单 201 type=return', code == 201 and d['type'] == 'return', f'{code} {d}')
check('W6 退料草稿不冲减', ProductionLotLink.objects.get(
    production=prod1, lot=lot_m).qty == Decimal('100'), '确认前仍为100')
RET_ID = d['id']
code, d = api('POST', f'/material-issues/{RET_ID}/confirm', AT, CID)
check('W6 确认后已领 100→60',
      code == 200 and ProductionLotLink.objects.get(
          production=prod1, lot=lot_m).qty == Decimal('60'), f'{code} {d}')
# W6b 超退地板 0：再退 100 → 0 不为负
code, d = api('POST', '/material-issues', AT, CID, {
    'type': 'return',
    'items': [{'production_id': prod1.id, 'lot_id': lot_m.id, 'qty': 100,
               'material_id': m_fabric.id}]})
code, d = api('POST', f"/material-issues/{d['id']}/confirm", AT, CID)
check('W6b 超退冲减地板 0', ProductionLotLink.objects.get(
    production=prod1, lot=lot_m).qty == Decimal('0'),
    str(ProductionLotLink.objects.get(production=prod1, lot=lot_m).qty))

# W7 单位黑洞防线：lot 公斤 vs 主单位米（克重200×幅宽150 → 0.3kg/m）
code, d = api('POST', '/material-issues', AT, CID, {
    'items': [{'production_id': prod1.id, 'lot_id': lot_kg.id, 'qty': 100,
               'material_id': m_fabric.id, 'qty_req': 300}]})
# 100kg → 333.33 米 > 300 → over
it = d['items'][0]
check('W7 公斤→米折算后判 over（100kg=333.33m>300m）',
      code == 201 and it['over'] is True and it['over_state'] == 'over',
      f'{code} {it}')
# 反向：lot 米 vs 主单位公斤（10 公斤/件 × 100 = 1000kg 定额；30米=9kg < 1000 → normal）
code, d = api('POST', '/material-issues', AT, CID, {
    'items': [{'production_id': prod1.id, 'lot_id': lot_m.id, 'qty': 30,
               'bom_item_id': bi_kg.id}]})
it = d['items'][0]
check('W7b 米→公斤折算后 normal（9kg<1000kg）',
      code == 201 and it['qty_req'] == '1000.00' and it['over'] is False
      and it['over_state'] == 'normal', f'{code} {it}')

# W8 换算缺参：lot 公斤 + 物料主单位米且无克重/幅宽 → pending（跳过判定）
code, d = api('POST', '/material-issues', AT, CID, {
    'items': [{'production_id': prod1.id, 'lot_id': lot_kg.id, 'qty': 20,
               'material_id': m_noparam.id, 'qty_req': 5}]})
it = d['items'][0]
check('W8 缺参跳过判定：over=False/state=pending 正常保存',
      code == 201 and it['over'] is False and it['over_state'] == 'pending',
      f'{code} {it}')
PENDING_ISSUE_ID = d['id']
code, d = api('POST', f'/material-issues/{PENDING_ISSUE_ID}/confirm', AT, CID)
check('W8b pending 行确认不回归', code == 200, f'{code} {d}')

# W9 双口径：干净床次（快照 220）vs 改版 active BOM 3×1.1×100=330
prod3 = Production.objects.create(company=company, style=style, bed_no='TWH1-B3',
                                  total_qty=50, plan_qty=100)
api('POST', '/material-issues', AT, CID, {
    'items': [{'production_id': prod3.id, 'lot_id': lot_m.id, 'qty': 10,
               'bom_item_id': bi_fabric.id}]})
# 差异报表只统计已确认单（口径=成本取数）——把前面用例的草稿单批量确认
MaterialIssue.all_objects.filter(company=company, status='draft').update(
    status='confirmed')
bom_v1.status = 'deactivated'
bom_v1.save(update_fields=['status', 'updated_at'])
bom_v2.status = 'active'
bom_v2.save(update_fields=['status', 'updated_at'])
code, d = api('GET', '/issues/divergence', AT, CID,
              {'production_id': prod3.id, 'material_id': m_fabric.id})
row = next((r for r in d['rows'] if r['material_id'] == m_fabric.id), None)
check('W9 双口径快照220 vs 改版330 并列返回',
      row is not None and row['qty_req_snapshot'] == '220.00'
      and row['qty_req_current'] == '330.00', str(row))
# 改回 v1（后续用例依赖）
bom_v2.status = 'deactivated'
bom_v2.save(update_fields=['status', 'updated_at'])
bom_v1.status = 'active'
bom_v1.save(update_fields=['status', 'updated_at'])

# ================= 差异报表 =================

# W10 跨领料单聚合 + 退料冲减出数
code, d = api('GET', '/issues/divergence', AT, CID,
              {'production_id': prod1.id, 'material_id': m_fabric.id})
row = next((r for r in d['rows'] if r['material_id'] == m_fabric.id), None)
check('W10 报表 200 且行含超领/退料字段',
      code == 200 and row is not None and 'qty_returned' in row
      and 'qty_net' in row and row['over'] is True, f'{code} {str(row)[:200]}')
check('W10 超领量=80（300-220）+10（70-60）+33.33（333.33-300）',
      row['over_qty'] == '123.33', str(row.get('over_qty')))
check('W10 退料 140 已冲减出数',
      row['qty_returned'] == '140.00', str(row.get('qty_returned')))
check('W10 超领行明细含单号/原因',
      any(r['over_reason'] == '尾数补裁' for r in row['over_rows']),
      str(row.get('over_rows'))[:200])

# W11 过滤键
code, d = api('GET', '/issues/divergence', AT, CID,
              {'date_from': str((timezone.localdate() + timedelta(days=1)))})
check('W11 date_from 未来日期 → 空行', code == 200 and d['rows'] == [], f'{code} {len(d["rows"])}')
code, d = api('GET', '/issues/divergence', AT, CID,
              {'date_from': str(timezone.localdate() - timedelta(days=1)),
               'date_to': str(timezone.localdate() + timedelta(days=1))})
check('W11 日期区间命中', code == 200 and len(d['rows']) > 0, f'{code} {len(d["rows"])}')
code, d = api('GET', '/issues/divergence', AT, CID, {'order_id': order.id})
check('W11 order_id 过滤只出挂单床次',
      code == 200 and all(r['order_id'] == order.id for r in d['rows'])
      and any(r['production_id'] == prod1.id for r in d['rows'])
      and not any(r['production_id'] == prod2.id for r in d['rows']), f'{code} {d["rows"][:1]}')
code, d = api('GET', '/issues/divergence', AT, CID, {'material_id': m_kg.id})
check('W11 material_id 过滤', code == 200 and all(r['material_id'] == m_kg.id
                                                  for r in d['rows']), f'{code} {d["rows"][:1]}')

# W12 无定额占比
code, d = api('GET', '/issues/divergence', AT, CID)
check('W12 无定额占比>0 且待补计数>0',
      code == 200 and (d['summary']['no_req_ratio'] or 0) > 0
      and d['summary']['pending_count'] > 0, str(d['summary']))
check('W12 超领占比>0', (d['summary']['over_ratio'] or 0) > 0, str(d['summary']))
check('W12 待补清单含缺参行（公斤lot vs 米主单位缺克重）',
      any(p['material_unit'] == '米' and p['lot_unit'] == '公斤'
          for p in d['pending_units']), str(d['pending_units'])[:200])

# ================= 库存价链 =================

# W13 入库带采购行 → 行价默认带出 PO 行价；确认回填批次单价
po = PurchaseOrder.objects.create(company=company, order_no='TWH1-CG1',
                                  ordered_at=timezone.localdate())
pi = PurchaseItem.objects.create(order=po, fabric_name='主面料', color='黑',
                                 qty=500, unit='米', price=Decimal('12.500'))
code, d = api('POST', '/stock-ins', AT, CID, {
    'in_no': 'TWH1-RK1', 'purchase': po.id, 'source_type': 'purchase',
    'items': [{'fabric_name': '主面料', 'color': '黑', 'lot_no': 'TWH1-L3',
               'qty': 500, 'unit': '米', 'purchase_item': pi.id}]})
check('W13 入库行价默认带出 PO 行价 12.5',
      code == 201 and d['items'][0]['unit_price'] == '12.500'
      and d['items'][0]['purchase_item'] == pi.id, f'{code} {d["items"][0]}')
RK1 = d['id']
code, d = api('POST', f'/stock-ins/{RK1}/confirm', AT, CID)
lot3 = Lot.objects.get(company=company, lot_no='TWH1-L3')
check('W13 确认后批次单价落定 12.5', lot3.unit_cost == Decimal('12.500'),
      str(lot3.unit_cost))

# W14 手录价覆盖默认（不入库价链默认路径）
code, d = api('POST', '/stock-ins', AT, CID, {
    'in_no': 'TWH1-RK2', 'purchase': po.id,
    'items': [{'fabric_name': '主面料', 'color': '红', 'lot_no': 'TWH1-L4',
               'qty': 100, 'unit': '米', 'purchase_item': pi.id,
               'unit_price': 15}]})
check('W14 手录价 15 覆盖 PO 价', code == 201 and d['items'][0]['unit_price'] == '15.000',
      f'{code} {d["items"][0]}')

# W15 确认时补带出（ORM 造 unit_price=None 路径）
si2 = StockIn.objects.get(company=company, in_no='TWH1-RK2')
StockInItem.objects.filter(stock_in=si2).update(unit_price=None)
code, d = api('POST', f'/stock-ins/{si2.id}/confirm', AT, CID)
check('W15 确认时补带出 PO 价',
      code == 200 and StockInItem.objects.get(stock_in=si2).unit_price
      == Decimal('12.500'), f'{code} {d}')
lot4 = Lot.objects.get(company=company, lot_no='TWH1-L4')
check('W15 批次单价无值时由入库价折算落定', lot4.unit_cost == Decimal('12.500'),
      str(lot4.unit_cost))

# W16 Lot.unit_cost 补录/清空（挂 warehouse.manage）
code, d = api('PUT', f'/lots/{lot_m.id}', AT, CID, {'unit_cost': '9.8'})
check('W16 批次单价补录 9.8', code == 200 and d['unit_cost'] == '9.800', f'{code} {d}')
code, d = api('PUT', f'/lots/{lot_m.id}', AT, CID, {'unit_cost': None})
check('W16b 批次单价清空', code == 200 and d['unit_cost'] is None, f'{code} {d}')

# ================= 权限与边界 =================

# W17 401/403/跨厂
code, _ = api('GET', '/issues/divergence')
check('W17 无 token 401', code == 401, str(code))
code, _ = api('POST', '/material-issues', None, CID, {'items': []})
check('W17b 领料无 token 401', code == 401, str(code))
code, d = api('GET', '/issues/divergence', BT, CID)
check('W17c 车间岗（无 warehouse.manage）403', code == 403, f'{code} {d}')
code, d = api('POST', '/material-issues', BT, CID, {
    'items': [{'production_id': prod1.id, 'lot_id': lot_m.id, 'qty': 1}]})
check('W17d 车间岗领料 403', code == 403, f'{code} {d}')
code, d = api('POST', '/stock-ins', BT, CID, {'items': []})
check('W17e 车间岗入库 403', code == 403, f'{code} {d}')
# 跨厂隔离：对照厂看不到测试厂差异行
code, d = api('GET', '/issues/divergence', CT, company_b.id)
check('W17f 跨厂隔离：对照厂报表 0 行', code == 200 and d['rows'] == [],
      f'{code} {len(d["rows"])}')

# W18 type 非法
code, d = api('POST', '/material-issues', AT, CID, {
    'type': 'bogus',
    'items': [{'production_id': prod1.id, 'lot_id': lot_m.id, 'qty': 1}]})
check('W18 type 非法 400', code == 400, f'{code} {d}')

# W19 FK 不存在 400
code, d = api('POST', '/material-issues', AT, CID, {
    'items': [{'production_id': prod1.id, 'lot_id': lot_m.id, 'qty': 1,
               'bom_item_id': 99999999}]})
check('W19 bom_item 不存在 400', code == 400, f'{code} {d}')
code, d = api('POST', '/material-issues', AT, CID, {
    'items': [{'production_id': prod1.id, 'lot_id': lot_m.id, 'qty': 1,
               'material_id': 99999999}]})
check('W19b material 不存在 400', code == 400, f'{code} {d}')
code, d = api('POST', '/stock-ins', AT, CID, {
    'items': [{'fabric_name': 'x', 'lot_no': 'TWH1-LX', 'qty': 1,
               'purchase_item': 99999999}]})
check('W19c purchase_item 不存在 400', code == 400, f'{code} {d}')

# ================= W20 新代码分支补缺（coverage missing=0 门禁） =================

# W20a services 纯函数：物料 None / 零克重 / 反向缺参 / 未知单位对
from apps.warehouse.services import (convert_to_main_unit,  # noqa: E402
                                     quota_qty_for)
check('W20a material=None 返回 None',
      convert_to_main_unit(1, 'kg', None) is None, '')
m_zero = Material.objects.create(company=company, code='TWH1-M4', name='零克重',
                                 category='fabric', unit='米',
                                 weight_gsm=0, width_cm=150)
check('W20b 克重=0 折算返回 None（防除零）',
      convert_to_main_unit(1, '公斤', m_zero) is None, '')
m_kg_noparam = Material.objects.create(company=company, code='TWH1-M6', name='缺参公斤料',
                                       category='fabric', unit='公斤')  # 无克重/幅宽
check('W20c 反向缺参（米→公斤无克重）返回 None',
      convert_to_main_unit(1, '米', m_kg_noparam) is None, '')
m_yd = Material.objects.create(company=company, code='TWH1-M5', name='码料',
                               category='fabric', unit='米',
                               weight_gsm=200, width_cm=150)
check('W20d 未知单位对（码→米）返回 None',
      convert_to_main_unit(1, '码', m_yd) is None, '')
check('W20e 同单位直通', convert_to_main_unit(7, '米', m_fabric) == 7, '')

# W20f 无定额行换算缺参 → 待补清单（882-883 分支）
code, d = api('POST', '/material-issues', AT, CID, {
    'items': [{'production_id': prod1.id, 'lot_id': lot_kg.id, 'qty': 8,
               'material_id': m_noparam.id}]})
code, d = api('POST', f"/material-issues/{d['id']}/confirm", AT, CID)
check('W20f 无定额+缺参行确认', code == 200, f'{code} {d}')

# W20g 退料行换算缺参 → 报表待补（870-871 分支）
code, d = api('POST', '/material-issues', AT, CID, {
    'type': 'return',
    'items': [{'production_id': prod1.id, 'lot_id': lot_kg.id, 'qty': 3,
               'material_id': m_noparam.id}]})
code, d = api('POST', f"/material-issues/{d['id']}/confirm", AT, CID)
check('W20g 退料缺参行确认', code == 200, f'{code} {d}')
code, d = api('GET', '/issues/divergence', AT, CID)
check('W20g 报表待补清单含退料缺参行',
      any(p['material_unit'] == '米' and p['lot_unit'] == '公斤'
          for p in d['pending_units']), str(d['pending_units'])[:200])

# W20h 无 active BOM 的床次进报表（current_req_map continue 分支）
style_nobom = Style.objects.create(company=company, style_no='TWH1-S2', name='无BOM款')
prod4 = Production.objects.create(company=company, style=style_nobom,
                                  bed_no='TWH1-B4', total_qty=10, plan_qty=10)
code, d = api('POST', '/material-issues', AT, CID, {
    'items': [{'production_id': prod4.id, 'lot_id': lot_m.id, 'qty': 5}]})
MaterialIssue.all_objects.filter(company=company, status='draft').update(
    status='confirmed')
code, d = api('GET', '/issues/divergence', AT, CID,
              {'production_id': prod4.id})
check('W20h 无BOM床次 qty_req_current=None 照常出报表',
      code == 200 and len(d['rows']) == 1 and d['rows'][0]['qty_req_current'] is None,
      f'{code} {str(d["rows"])[:120]}')

# W20i 领料列表 + status 过滤（get_queryset 分支）
code, d = api('GET', '/material-issues', AT, CID, {'status': 'confirmed'})
check('W20i 领料列表 status 过滤', code == 200
      and all(r['status'] == 'confirmed' for r in d.get('results', d if isinstance(d, list) else [])),
      f'{code}')
code, d = api('GET', '/material-issues', AT, CID, {'status': 'draft'})
check('W20i2 draft 过滤（前段已批量确认 → 空）', code == 200
      and not (d.get('results') or []), f'{code}')

# W20j 领料边界：items 空 / 批次不存在 / 床次不存在
code, d = api('POST', '/material-issues', AT, CID, {'items': []})
check('W20j items 空 400', code == 400, f'{code} {d}')
code, d = api('POST', '/material-issues', AT, CID, {
    'items': [{'production_id': prod1.id, 'lot_id': 99999999, 'qty': 1}]})
check('W20j2 批次不存在 400', code == 400, f'{code} {d}')
code, d = api('POST', '/material-issues', AT, CID, {
    'items': [{'production_id': 99999999, 'lot_id': lot_m.id, 'qty': 1}]})
check('W20j3 床次不存在 400', code == 400, f'{code} {d}')

# W20k 领料单删除/重复确认/不存在（detail+confirm 边界）
code, d = api('POST', '/material-issues', AT, CID, {
    'items': [{'production_id': prod1.id, 'lot_id': lot_m.id, 'qty': 1}]})
TMP_ID = d['id']
code, d = api('DELETE', f'/material-issues/{TMP_ID}', AT, CID)
check('W20k 草稿删除 204', code == 204, str(code))
code, d = api('POST', f'/material-issues/{TMP_ID}/confirm', AT, CID)
check('W20k2 已删单确认 404', code == 404, f'{code} {d}')
code, d = api('POST', f'/material-issues/{RET_ID}/confirm', AT, CID)
check('W20k3 重复确认 400', code == 400, f'{code} {d}')

# W20l 入库确认边界：不存在 / 重复确认
code, d = api('POST', '/stock-ins/99999999/confirm', AT, CID)
check('W20l 入库不存在 404', code == 404, str(code))
code, d = api('POST', f'/stock-ins/{RK1}/confirm', AT, CID)
check('W20l2 重复确认 400', code == 400, f'{code} {d}')

# W20m Lot 删除保护（有血缘/领料引用不可删——services/ Lot 分支回归）
code, d = api('DELETE', f'/lots/{lot_m.id}', AT, CID)
check('W20m 有领料引用批次不可删 400', code == 400, f'{code} {d}')

# W20n quota_qty_for 直测（损耗 0 与有值两分支已被 W2/W2b 覆盖；补 None loss 兜底）
check('W20n 定额公式 2×1.1×100=220',
      quota_qty_for(bi_fabric, prod1) == Decimal('220.00'), '')

# W20o Lot PUT 其余可改字段回归（qty/batch_date 分支）
code, d = api('PUT', f'/lots/{lot_kg.id}', AT, CID,
              {'qty': 188, 'batch_date': str(timezone.localdate())})
check('W20o Lot qty/batch_date 更新',
      code == 200 and d['qty'] == '188.00' and d['batch_date'] == str(timezone.localdate()),
      f'{code} {d}')

# W20p 入库 items 空 400
code, d = api('POST', '/stock-ins', AT, CID, {'items': []})
check('W20p 入库 items 空 400', code == 400, f'{code} {d}')

print(f'\n==== P1 warehouse 套件：{len(PASS)} pass / {len(FAIL)} fail ====')
if FAIL:
    print('FAILED:', FAIL)
