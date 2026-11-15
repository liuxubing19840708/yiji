# -*- coding: utf-8 -*-
"""
P2 warehouse 采购补全域套件（test_warehouse_p2）—— SA-P2-1 交付，2026-10-24。

覆盖《服装ERP全模块设计方案》§4.1 + P2 拍板口径：
  供应商  D1 CRUD/唯一/keyword/软删复用；D1g 被采购单或暂收单引用拒删（PROTECT 语义）
  采购列  D2 PurchaseItem demand_source/arrived_qty/qualified_qty/material 新列（存量零破坏）
  暂收链  D3 暂收→IQC 拆量→确认→StockIn(type=purchase) 价链带价全链
          （unit_price=PurchaseItem.price 自动带出 / Lot get_or_create + unit_cost 落定 /
            source_in 回填 / pi.arrived_qty·qualified_qty 累计 / 拒收量留痕不进库存）
          D4 无单直收（无 purchase：unit_price=NULL，material 手选）；D4b 全拒收不生成入库单
          D5 draft 删除；D6 边界（items 空/FK 不存在/跨厂 FK/缺面料名）
  需求汇总 D7-D17 三减项逐项验证（口径见 DemandSummaryView docstring）：
          需求=BOM gross_usage×未投产；在途=Σ max(0,qty-arrived) 逐行截断+不挂物料行不计；
          库存=ΣStockInItem-Σ已确认领料（退料加回）；公斤↔米折算；缺参 pending 不入合计；
          未投产=订单件数-Σ计划件数（部分投产/排完跳过）；customer_id/style_id 过滤；
          cancelled 订单剔除；负建议量照实返回
  权限    D18 401；D19 车间岗 403；D20 legacy 'production' 岗：暂收/汇总 200（旧 coarse
          覆盖 warehouse.manage）但供应商 403（warehouse.supplier legacy 零授权）；D21 跨厂隔离
跑法：manage.py shell -c "exec(compile(open('test_warehouse_p2.py',encoding='utf-8').read(),'test_warehouse_p2.py','exec'))"
幂等：自建「仓库P2测试厂(可删)/仓库P2对照厂(可删)」并跨次复用；每轮 preclean 硬清
本厂全部业务行（真实厂 86 零接触；暂收/入库单号为自动号不带 TWH2 前缀，故按公司全清）。
"""
import json
from decimal import Decimal

from django.contrib.auth.hashers import make_password
from django.db.models import Sum
from django.test import Client
from django.utils import timezone

from apps.accounts.models import Company, Post as PostM, User, UserCompany
from apps.accounts.services import create_company_with_owner
from apps.catalog.models import Customer, Style
from apps.orders.models import Order, OrderItem
from apps.production.models import Production
from apps.warehouse.models import (
    IssueItem, Lot, MaterialIssue, ProductionLotLink, PurchaseItem,
    PurchaseOrder, Receipt, ReceiptItem, StockIn, StockInItem, Supplier,
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
        r = fn(f'/api{path}', data=data or None, **extra)
    else:
        r = fn(f'/api{path}', data=json.dumps(data) if data is not None else None,
               content_type='application/json', **extra)
    try:
        body = json.loads(r.content.decode('utf-8'))
    except Exception:
        body = {}
    return r.status_code, body


PHONE_A = '13988710301'      # 测试厂管理员
PHONE_B = '13988710302'      # 测试厂车间岗（无 warehouse.* → 403）
PHONE_C = '13988710303'      # 对照厂管理员（跨厂隔离）
PHONE_D = '13988710304'      # 测试厂 legacy 'production' 岗（旧 coarse key）
CO_A = '仓库P2测试厂(可删)'
CO_B = '仓库P2对照厂(可删)'

company = Company.objects.filter(name=CO_A).first()
if company is None:
    u = User.objects.filter(phone=PHONE_A).first() or User.objects.create(
        phone=PHONE_A, username=PHONE_A, real_name='仓库P2厂长',
        password=make_password('yisao1234'), is_active=True)
    company, _ = create_company_with_owner(u, CO_A)
else:
    User.objects.filter(phone=PHONE_A).update(
        password=make_password('yisao1234'), is_active=True)
CID = company.id

company_b = Company.objects.filter(name=CO_B).first()
if company_b is None:
    u3 = User.objects.filter(phone=PHONE_C).first() or User.objects.create(
        phone=PHONE_C, username=PHONE_C, real_name='仓库P2对照厂长',
        password=make_password('yisao1234'), is_active=True)
    company_b, _ = create_company_with_owner(u3, CO_B)

# 车间岗（无 warehouse 权限）
if UserCompany.objects.filter(company=company, user__phone=PHONE_B).exists():
    u_b = User.objects.get(phone=PHONE_B)
else:
    u_b = User.objects.filter(phone=PHONE_B).first() or User.objects.create(
        phone=PHONE_B, username=PHONE_B, real_name='仓库P2车间',
        password=make_password('yisao1234'), is_active=True)
    workshop_post = PostM.objects.filter(company=company, name='车间').first() \
        or PostM.objects.create(company=company, name='车间',
                                permissions=['my_salary.view', 'working.view'])
    UserCompany.objects.create(user=u_b, company=company, post=workshop_post,
                               status=UserCompany.Status.ACTIVE,
                               hired_date=timezone.localdate())

# legacy 'production' 岗（旧 coarse key：覆盖 warehouse.manage，不覆盖 warehouse.supplier）
if UserCompany.objects.filter(company=company, user__phone=PHONE_D).exists():
    u_d = User.objects.get(phone=PHONE_D)
else:
    u_d = User.objects.filter(phone=PHONE_D).first() or User.objects.create(
        phone=PHONE_D, username=PHONE_D, real_name='仓库P2legacy仓管',
        password=make_password('yisao1234'), is_active=True)
    legacy_post = PostM.objects.filter(company=company, name='P2legacy仓管').first() \
        or PostM.objects.create(company=company, name='P2legacy仓管',
                                permissions=['production'])
    UserCompany.objects.create(user=u_d, company=company, post=legacy_post,
                               status=UserCompany.Status.ACTIVE,
                               hired_date=timezone.localdate())

AT = api('POST', '/auth/login', data={'phone': PHONE_A, 'password': 'yisao1234'})[1].get('access', '')
BT = api('POST', '/auth/login', data={'phone': PHONE_B, 'password': 'yisao1234'})[1].get('access', '')
CT = api('POST', '/auth/login', data={'phone': PHONE_C, 'password': 'yisao1234'})[1].get('access', '')
DT = api('POST', '/auth/login', data={'phone': PHONE_D, 'password': 'yisao1234'})[1].get('access', '')
assert AT and BT and CT and DT, '登录失败'

# ---- preclean（先子后父，硬清测试厂/对照厂业务行；两厂均为专属测试厂） ----
# PROTECT 链：ReceiptItem→pi/material，StockInItem→lot/pi/material，IssueItem→lot/production，
# Lot.source_in→StockInItem(SET_NULL) × StockInItem.lot→Lot(PROTECT) —— 先断环
for co in (company, company_b):
    ReceiptItem.all_objects.filter(receipt__company=co)._raw_delete(ReceiptItem.all_objects.db)
    Receipt.all_objects.filter(company=co)._raw_delete(Receipt.all_objects.db)
    IssueItem.all_objects.filter(issue__company=co)._raw_delete(IssueItem.all_objects.db)
    MaterialIssue.all_objects.filter(company=co)._raw_delete(MaterialIssue.all_objects.db)
    ProductionLotLink.all_objects.filter(company=co)._raw_delete(ProductionLotLink.all_objects.db)
    Lot.all_objects.filter(company=co).update(source_in=None)   # 先断环再清入库行
    StockInItem.all_objects.filter(stock_in__company=co)._raw_delete(StockInItem.all_objects.db)
    StockIn.all_objects.filter(company=co)._raw_delete(StockIn.all_objects.db)
    Lot.all_objects.filter(company=co)._raw_delete(Lot.all_objects.db)
    PurchaseItem.all_objects.filter(order__company=co)._raw_delete(PurchaseItem.all_objects.db)
    PurchaseOrder.all_objects.filter(company=co)._raw_delete(PurchaseOrder.all_objects.db)
    Production.all_objects.filter(company=co)._raw_delete(Production.all_objects.db)
    OrderItem.all_objects.filter(order__company=co)._raw_delete(OrderItem.all_objects.db)
    Order.all_objects.filter(company=co)._raw_delete(Order.all_objects.db)
    Customer.all_objects.filter(company=co)._raw_delete(Customer.all_objects.db)
    Supplier.all_objects.filter(company=co)._raw_delete(Supplier.all_objects.db)
# tech 行（先 BomItem→Bom→Material，被仓库行 PROTECT 引用已先清）
BomItem_all = None
from apps.tech.models import Bom, BomItem, Material  # noqa: E402
BomItem.all_objects.filter(bom__company=company).delete()
Bom.all_objects.filter(company=company).delete()
Material.all_objects.filter(company=company).delete()
Style.all_objects.filter(company=company)._raw_delete(Style.all_objects.db)

# ---- 基础数据 ----
style = Style.objects.create(company=company, style_no='TWH2-S1', name='P2客户款')
style2 = Style.objects.create(company=company, style_no='TWH2-S2', name='P2汇总款')
m1 = Material.objects.create(company=company, code='TWH2-M1', name='主面料',
                             category='fabric', unit='米', weight_gsm=200, width_cm=150)
m_kg = Material.objects.create(company=company, code='TWH2-M2', name='公斤料',
                               category='fabric', unit='公斤', weight_gsm=200, width_cm=150)
m_np = Material.objects.create(company=company, code='TWH2-M3', name='缺参面料',
                               category='fabric', unit='米')   # 无克重/幅宽
m2 = Material.objects.create(company=company, code='TWH2-M4', name='汇总面料',
                             category='fabric', unit='米', weight_gsm=200, width_cm=150)

bom1 = Bom.objects.create(company=company, style=style, version=1, status='active')
BomItem.objects.create(bom=bom1, material=m1, kind='main', unit='米',
                       unit_usage=2, loss_rate=10)              # gross 2.2 米/件
bom2 = Bom.objects.create(company=company, style=style2, version=1, status='active')
bi2 = BomItem.objects.create(bom=bom2, material=m2, kind='main', unit='米',
                             unit_usage=2, loss_rate=10)        # gross 2.2 米/件

order1 = Order.objects.create(company=company, order_no='TWH2-O1', style=style)
OrderItem.objects.create(order=order1, color='黑', size='M', qty=100)
order2 = Order.objects.create(company=company, order_no='TWH2-O3', style=style2)
OrderItem.objects.create(order=order2, color='黑', size='M', qty=100)
# 辅助订单（领料宿主，排满不进需求）：qty10+计划10 → not_produced=0
order_aux = Order.objects.create(company=company, order_no='TWH2-O4', style=style2)
OrderItem.objects.create(order=order_aux, color='黑', size='M', qty=10)
prod_aux = Production.objects.create(company=company, style=style2, bed_no='TWH2-B1',
                                     total_qty=10, plan_qty=10, order=order_aux)

customer = Customer.objects.create(company=company, name='TWH2-C1')

# 采购单 + 行（价链源）
po1 = PurchaseOrder.objects.create(company=company, order_no='TWH2-CG1',
                                   ordered_at=timezone.localdate())
pi1 = PurchaseItem.objects.create(order=po1, fabric_name='主面料', color='黑',
                                  qty=500, unit='米', price=Decimal('12.500'),
                                  material=m1)

# 对照厂数据（跨厂 FK 校验用）
po_b = PurchaseOrder.objects.create(company=company_b, order_no='TWH2B-CG1',
                                    ordered_at=timezone.localdate())
pi_b = PurchaseItem.objects.create(order=po_b, fabric_name='对照厂面料', qty=10, unit='米')
sup_b = Supplier.objects.create(company=company_b, code='TWH2B-SUP', name='对照厂供应商')


# ================= 供应商主档 =================

# D1 CRUD
code, d = api('POST', '/suppliers', AT, CID, {
    'code': 'TWH2-SUP1', 'name': '一手布商', 'contact_person': '王老板',
    'phone': '13800000001', 'settle_method': '月结', 'rating': 'A'})
check('D1 供应商创建 201', code == 201 and d.get('id'), f'{code} {d}')
SUP1 = d.get('id')
check('D1 字段回显', d.get('settle_method') == '月结' and d.get('rating') == 'A', str(d))
code, d = api('POST', '/suppliers', AT, CID, {'code': 'TWH2-SUP1', 'name': '重复编码'})
check('D1b 编码重复 400', code == 400, f'{code} {d}')
code, d = api('GET', '/suppliers', AT, CID, {'keyword': '一手'})
check('D1c keyword 命中', code == 200 and len(d.get('results', [])) == 1, f'{code} {d}')
code, d = api('GET', '/suppliers', AT, CID, {'keyword': '不存在的'})
check('D1c2 keyword 不命中空', code == 200 and not d.get('results'), f'{code} {d}')
code, d = api('PUT', f'/suppliers/{SUP1}', AT, CID,
              {'name': '一手布商(改)', 'rating': 'S'})
check('D1d 更新 200', code == 200 and d.get('rating') == 'S', f'{code} {d}')
code, d = api('POST', '/suppliers', AT, CID, {'code': 'TWH2-SUP0', 'name': '占位户'})
code, d = api('PUT', f'/suppliers/{SUP1}', AT, CID, {'code': 'TWH2-SUP0'})
check('D1e 改码唯一冲突 400（撞已有编码）', code == 400, f'{code} {d}')
code, d = api('PUT', f'/suppliers/{SUP1}', AT, CID, {'code': 'TWH2-SUP1'})
check('D1f 改码为自身 200', code == 200 and d.get('code') == 'TWH2-SUP1', f'{code} {d}')
# 未引用供应商可软删 + 编码可复用
code, d = api('POST', '/suppliers', AT, CID, {'code': 'TWH2-SUP2', 'name': '临时户'})
SUP2 = d.get('id')
code, d = api('DELETE', f'/suppliers/{SUP2}', AT, CID)
check('D1f2 未引用删除 204', code == 204, str(code))
code, d = api('POST', '/suppliers', AT, CID, {'code': 'TWH2-SUP2', 'name': '复用编码'})
check('D1f3 软删后编码可复用', code == 201, f'{code} {d}')
code, d = api('GET', '/suppliers/99999999', AT, CID)
check('D1g 详情 404', code == 404, str(code))

# D2 PurchaseItem 新列（demand_source/arrived/qualified/material）
code, d = api('POST', '/purchase-orders', AT, CID, {
    'items': [{'fabric_name': '主面料', 'qty': 30, 'unit': '米', 'price': 10,
               'material_id': m1.id, 'demand_source': 'summary_import'}]})
check('D2 采购行新列 201', code == 201 and d.get('id'), f'{code} {d}')
it = (d.get('items') or [{}])[0]
check('D2 行回显 material/demand_source/arrived 0',
      it.get('material') == m1.id and it.get('demand_source') == 'summary_import'
      and it.get('arrived_qty') == '0.00' and it.get('qualified_qty') == '0.00', str(it))
code, d = api('POST', '/purchase-orders', AT, CID, {
    'items': [{'fabric_name': 'x', 'qty': 1, 'material_id': 99999999}]})
check('D2b 采购行物料不存在 400', code == 400, f'{code} {d}')


# ================= 暂收 → IQC → 确认 → StockIn 价链全链 =================

# D3 暂收创建（PO 行带出面料/单位/物料，单号自动 ZS）
code, d = api('POST', '/receipts', AT, CID, {
    'supplier': SUP1, 'purchase': po1.id,
    'items': [{'purchase_item': pi1.id, 'lot_no': 'TWH2-L1', 'arrived_qty': 100}]})
check('D3 暂收创建 201 单号自动 ZS', code == 201 and str(d.get('receipt_no', '')).startswith('ZS'),
      f'{code} {d}')
RECIPE = d.get('id')
it = (d.get('items') or [{}])[0]
check('D3 PO 行带出面料/单位/物料/单价源',
      it.get('fabric_name') == '主面料' and it.get('unit') == '米'
      and it.get('material') == m1.id and it.get('arrived_qty') == '100.00'
      and it.get('qualified_qty') == '0.00' and it.get('purchase_item') == pi1.id, str(it))
# IQC 行级拆量（PUT items 整表重建）
code, d = api('PUT', f'/receipts/{RECIPE}', AT, CID, {
    'items': [{'purchase_item': pi1.id, 'lot_no': 'TWH2-L1', 'arrived_qty': 100,
               'qualified_qty': 90, 'rejected_qty': 10, 'remark': '色差拒收'}]})
it = (d.get('items') or [{}])[0]
check('D3b IQC 拆量 90/10 留痕', code == 200 and it.get('qualified_qty') == '90.00'
      and it.get('rejected_qty') == '10.00' and it.get('remark') == '色差拒收', str(it))
code, d = api('GET', '/receipts', AT, CID, {'status': 'draft', 'keyword': '主面料'})
check('D3c 列表 status+keyword 过滤', code == 200
      and any(r.get('id') == RECIPE for r in d.get('results', [])), f'{code}')
# 确认
si_before = StockIn.objects.filter(company=company).count()
code, d = api('POST', f'/receipts/{RECIPE}/confirm', AT, CID)
check('D3d 确认 200 status=confirmed', code == 200 and d.get('status') == 'confirmed'
      and d.get('confirmed_at'), f'{code} {d}')
check('D3e2 确认响应回带 in_no/in_id（契约缺口 E，INT 2026-10-24 补）',
      str(d.get('in_no') or '').startswith('RK') and isinstance(d.get('in_id'), int),
      f'{d.get("in_no")} {d.get("in_id")}')
check('D3e 合格量自动生成 StockIn(type=purchase,done)',
      StockIn.objects.filter(company=company, purchase=po1,
                             source_type='purchase', status='done').count() == 1
      and StockIn.objects.filter(company=company).count() == si_before + 1, '')
si = StockIn.objects.filter(company=company, purchase=po1).order_by('-id').first()
sii = StockInItem.objects.filter(stock_in=si).first()
check('D3f StockInItem 带采购行+价链带价+物料',
      sii.qty == Decimal('90.00') and sii.purchase_item_id == pi1.id
      and sii.unit_price == Decimal('12.500') and sii.material_id == m1.id
      and sii.lot_no == 'TWH2-L1', str(sii.qty))
lot1 = Lot.objects.get(company=company, lot_no='TWH2-L1')
check('D3g 批次诞生+单价落定+来源回填',
      lot1.unit_cost == Decimal('12.500') and lot1.source_in_id == sii.id
      and lot1.qty == Decimal('90.00') and lot1.unit == '米', str(lot1.unit_cost))
pi1.refresh_from_db()
check('D3h 采购行 arrived=100/qualified=90 累计',
      pi1.arrived_qty == Decimal('100.00') and pi1.qualified_qty == Decimal('90.00'),
      f'{pi1.arrived_qty}/{pi1.qualified_qty}')
check('D3i 拒收量留痕不进库存（该批次入库总量=90）',
      StockInItem.objects.filter(lot_no='TWH2-L1', stock_in__company=company)
      .aggregate(s=Sum('qty')) is not None
      and StockInItem.objects.filter(lot_no='TWH2-L1', stock_in__company=company)
      .count() == 1 and sii.qty == Decimal('90.00'), '')
# confirmed 后不可改/删/重复确认
code, d = api('PUT', f'/receipts/{RECIPE}', AT, CID, {'remark': 'x'})
check('D3j 确认后 PUT 400', code == 400, f'{code} {d}')
code, d = api('DELETE', f'/receipts/{RECIPE}', AT, CID)
check('D3k 确认后 DELETE 400', code == 400, str(code))
code, d = api('POST', f'/receipts/{RECIPE}/confirm', AT, CID)
check('D3l 重复确认 400', code == 400, f'{code} {d}')

# D4 无单直收（无 purchase：unit_price=NULL，material 手选）
si_before = StockIn.objects.filter(company=company).count()
code, d = api('POST', '/receipts', AT, CID, {
    'supplier_name': '散户布行',
    'items': [{'material_id': m1.id, 'fabric_name': '散户面料', 'color': '蓝',
               'lot_no': 'TWH2-L2', 'arrived_qty': 50, 'qualified_qty': 50}]})
check('D4 无单直收 201', code == 201 and d.get('purchase') is None, f'{code} {d}')
DIRECT = d.get('id')
code, d = api('POST', f'/receipts/{DIRECT}/confirm', AT, CID)
check('D4b 直收确认 200', code == 200 and d.get('status') == 'confirmed', f'{code} {d}')
check('D4c 直收生成 StockIn 无采购单/无价',
      StockIn.objects.filter(company=company).count() == si_before + 1)
si2 = StockIn.objects.filter(company=company).order_by('-id').first()
sii2 = StockInItem.objects.filter(stock_in=si2).first()
check('D4d 直收行 unit_price=NULL + material 留痕',
      sii2.purchase_item_id is None and sii2.unit_price is None
      and sii2.material_id == m1.id, '')
lot2 = Lot.objects.get(company=company, lot_no='TWH2-L2')
check('D4e 直收批次无价 unit_cost=None', lot2.unit_cost is None, str(lot2.unit_cost))

# D4b 全拒收：不生成入库单
si_before = StockIn.objects.filter(company=company).count()
code, d = api('POST', '/receipts', AT, CID, {
    'items': [{'material_id': m1.id, 'fabric_name': '全拒面料', 'lot_no': 'TWH2-L9',
               'arrived_qty': 20, 'qualified_qty': 0, 'rejected_qty': 20}]})
REJ = d.get('id')
code, d = api('POST', f'/receipts/{REJ}/confirm', AT, CID)
check('D4f 全拒收确认 200 且不生成入库单', code == 200
      and StockIn.objects.filter(company=company).count() == si_before
      and not Lot.all_objects.filter(company=company, lot_no='TWH2-L9').exists(),
      f'{code} {d}')
check('D4g 全拒收响应不回带 in_no/in_id', 'in_no' not in d and 'in_id' not in d,
      f'{d.get("in_no")} {d.get("in_id")}')

# D5 draft 删除
code, d = api('POST', '/receipts', AT, CID, {
    'items': [{'material_id': m1.id, 'fabric_name': '待删', 'lot_no': 'TWH2-L8',
               'arrived_qty': 1}]})
TMP_R = d.get('id')
code, d = api('DELETE', f'/receipts/{TMP_R}', AT, CID)
check('D5 草稿删除 204', code == 204, str(code))
code, d = api('GET', f'/receipts/{TMP_R}', AT, CID)
check('D5b 删除后 404', code == 404, str(code))

# D6 边界
code, d = api('POST', '/receipts', AT, CID, {'items': []})
check('D6 items 空 400', code == 400, f'{code} {d}')
code, d = api('POST', '/receipts', AT, CID)
check('D6b items 缺失 400', code == 400, str(code))
code, d = api('POST', '/receipts', AT, CID, {'items': [{'lot_no': 'TWH2-LX', 'arrived_qty': 1}]})
check('D6c 无采购行缺面料名 400', code == 400, f'{code} {d}')
code, d = api('POST', '/receipts', AT, CID, {
    'items': [{'purchase_item': 99999999, 'lot_no': 'TWH2-LX', 'arrived_qty': 1,
               'fabric_name': 'x'}]})
check('D6d 采购行不存在 400', code == 400, f'{code} {d}')
code, d = api('POST', '/receipts', AT, CID, {
    'items': [{'purchase_item': pi_b.id, 'lot_no': 'TWH2-LX', 'arrived_qty': 1,
               'fabric_name': 'x'}]})
check('D6e 采购行跨厂 400', code == 400, f'{code} {d}')
code, d = api('POST', '/receipts', AT, CID, {
    'items': [{'material_id': 99999999, 'fabric_name': 'x', 'lot_no': 'TWH2-LX',
               'arrived_qty': 1}]})
check('D6f 物料不存在 400', code == 400, f'{code} {d}')
code, d = api('POST', '/receipts', AT, CID, {
    'supplier': sup_b.id,
    'items': [{'fabric_name': 'x', 'lot_no': 'TWH2-LX', 'arrived_qty': 1}]})
check('D6g 供应商跨厂 400', code == 400, f'{code} {d}')
code, d = api('POST', '/receipts', AT, CID, {
    'purchase': po_b.id,
    'items': [{'fabric_name': 'x', 'lot_no': 'TWH2-LX', 'arrived_qty': 1}]})
check('D6h 采购单跨厂 400', code == 400, f'{code} {d}')
# PUT 不带 items（仅改头字段）→ 行不变；带 supplier=None/purchase 清空分支
code, d = api('GET', f'/receipts/{DIRECT}', AT, CID)
n_items_before = len(d.get('items') or [])
code, d = api('PUT', f'/receipts/{DIRECT}', AT, CID)
check('D6i PUT 无 items 400（IQC 需整表提交）', code == 400, f'{code} {d}')
# 换一张草稿单测头字段分支
code, d = api('POST', '/receipts', AT, CID, {
    'supplier': SUP1, 'purchase': po1.id,
    'items': [{'material_id': m1.id, 'fabric_name': '头字段', 'lot_no': 'TWH2-L7',
               'arrived_qty': 1}]})
HDR = d.get('id')
code, d = api('PUT', f'/receipts/{HDR}', AT, CID, {'supplier': None, 'purchase': None,
                                                   'supplier_name': '手填名', 'remark': '改头'})
check('D6j PUT 清空 supplier/purchase + 改名', code == 200
      and d.get('supplier') is None and d.get('purchase') is None
      and d.get('supplier_name') == '手填名', f'{code} {d}')
check('D6k PUT 无 items 行保留', len(d.get('items') or []) == 1, str(len(d.get('items') or [])))
# supplier PUT 改码成功分支
code, d = api('PUT', f'/suppliers/{SUP1}', AT, CID, {'code': 'TWH2-SUP1B'})
check('D6l 供应商改码成功', code == 200 and d.get('code') == 'TWH2-SUP1B', f'{code} {d}')
code, d = api('PUT', f'/suppliers/{SUP1}', AT, CID, {'code': 'TWH2-SUP1'})
check('D6m 改回', code == 200, f'{code}')

# D1h 被引用供应商拒删（采购单/暂收单引用）
code, d = api('POST', '/purchase-orders', AT, CID, {
    'supplier': SUP1, 'items': [{'fabric_name': '引用料', 'qty': 5}]})
code, d = api('DELETE', f'/suppliers/{SUP1}', AT, CID)
check('D1h 被引用供应商删除 400（PROTECT 语义）', code == 400, f'{code} {d}')
# 暂收单引用同样拒删
code, d = api('POST', '/suppliers', AT, CID, {'code': 'TWH2-SUP3', 'name': '暂收引用户'})
SUP3 = d.get('id')
code, d = api('POST', '/receipts', AT, CID, {
    'supplier': SUP3,
    'items': [{'fabric_name': 'x', 'lot_no': 'TWH2-L6', 'arrived_qty': 1}]})
code, d = api('DELETE', f'/suppliers/{SUP3}', AT, CID)
check('D1i 暂收引用供应商删除 400', code == 400, f'{code} {d}')


# ================= 需求汇总（三减项逐项） =================

def ds_row(rows, mid):
    return next((r for r in rows if r['material_id'] == mid), None)


# D7 基线：需求=BOM gross(2.2)×未投产(100)=220，库存/在途 0
code, d = api('GET', '/demand-summary', AT, CID, {'style_id': style2.id})
r2 = ds_row(d.get('rows', []), m2.id)
check('D7 需求基线 220/0/0/220', code == 200 and r2 is not None
      and r2['qty_demand'] == '220.00' and r2['qty_stock'] == '0.00'
      and r2['qty_in_transit'] == '0.00' and r2['qty_suggested'] == '220.00', str(r2))
check('D7 辅助订单排满被跳过（order_planned_full>=1）',
      (d['summary'].get('order_planned_full') or 0) >= 1, str(d['summary']))

# D8 在途减项：PO 行 qty−arrived（max 逐行截断 + 不挂物料行不计）
po2 = PurchaseOrder.objects.create(company=company, order_no='TWH2-CG3',
                                   ordered_at=timezone.localdate())
pi2 = PurchaseItem.objects.create(order=po2, fabric_name='汇总面料', qty=50, unit='米',
                                  material=m2)
pi_over = PurchaseItem.objects.create(order=po2, fabric_name='超收行', qty=10, unit='米',
                                      material=m2, arrived_qty=Decimal('20.00'))
pi_nomat = PurchaseItem.objects.create(order=po2, fabric_name='不挂料行', qty=999, unit='米')
code, d = api('GET', '/demand-summary', AT, CID, {'style_id': style2.id})
r2 = ds_row(d.get('rows', []), m2.id)
check('D8 在途=50（超收行截断为0、不挂料行不计）',
      r2['qty_in_transit'] == '50.00' and r2['qty_suggested'] == '170.00', str(r2))

# D9 库存减项：入库行（material 锚点，含草稿入库单=流水口径）
code, d = api('POST', '/stock-ins', AT, CID, {
    'items': [{'fabric_name': '汇总面料', 'lot_no': 'TWH2-L3', 'qty': 40, 'unit': '米',
               'material_id': m2.id}]})
check('D9 入库行 material 回显', code == 201
      and (d.get('items') or [{}])[0].get('material') == m2.id, f'{code} {d}')
code, d = api('GET', '/demand-summary', AT, CID, {'style_id': style2.id})
r2 = ds_row(d.get('rows', []), m2.id)
check('D9 库存 40 → 建议 130', r2['qty_stock'] == '40.00'
      and r2['qty_suggested'] == '130.00', str(r2))

# D10 领料冲减（已确认）：30 米 → 库存 10，建议 100
lot4 = Lot.objects.create(company=company, lot_no='TWH2-L4', fabric_name='汇总面料',
                          qty=100, unit='米')
code, d = api('POST', '/material-issues', AT, CID, {
    'items': [{'production_id': prod_aux.id, 'lot_id': lot4.id, 'qty': 30,
               'material_id': m2.id}]})
ISSUE1 = d.get('id')
code, d = api('POST', f'/material-issues/{ISSUE1}/confirm', AT, CID)
check('D10 领料确认 200', code == 200, f'{code} {d}')
code, d = api('GET', '/demand-summary', AT, CID, {'style_id': style2.id})
r2 = ds_row(d.get('rows', []), m2.id)
check('D10 已确认领料冲减 → 库存 10/建议 160（需求仍 220，部分投产在 D12）',
      r2['qty_stock'] == '10.00' and r2['qty_suggested'] == '160.00', str(r2))

# D11 退料加回：退 10 → 库存 20，建议 110
code, d = api('POST', '/material-issues', AT, CID, {
    'type': 'return',
    'items': [{'production_id': prod_aux.id, 'lot_id': lot4.id, 'qty': 10,
               'material_id': m2.id}]})
RET1 = d.get('id')
api('POST', f'/material-issues/{RET1}/confirm', AT, CID)
code, d = api('GET', '/demand-summary', AT, CID, {'style_id': style2.id})
r2 = ds_row(d.get('rows', []), m2.id)
check('D11 退料加回 → 库存 20/建议 150', r2['qty_stock'] == '20.00'
      and r2['qty_suggested'] == '150.00', str(r2))

# D12 部分投产：计划 40 → 未投产 60 → 需求 132，建议 132-20-50=62
prod_main = Production.objects.create(company=company, style=style2, bed_no='TWH2-B2',
                                      total_qty=45, plan_qty=40, order=order2)
code, d = api('GET', '/demand-summary', AT, CID, {'style_id': style2.id})
r2 = ds_row(d.get('rows', []), m2.id)
check('D12 未投产=100-40 → 需求 132/建议 62', r2['qty_demand'] == '132.00'
      and r2['qty_suggested'] == '62.00', str(r2))

# D13 排完：计划 100 → 未投产 0 → 该款物料全部消失
Production.objects.filter(pk=prod_main.pk).update(plan_qty=100)
code, d = api('GET', '/demand-summary', AT, CID, {'style_id': style2.id})
check('D13 排完订单跳过 → 行空', code == 200 and d.get('rows') == [], str(d.get('rows')))
Production.objects.filter(pk=prod_main.pk).update(plan_qty=40)   # 还原供后续用例

# D14 折算：BOM 公斤料（主单位公斤），库存批次单位米 → 米×0.3=公斤折算
bi_kg2 = BomItem.objects.create(bom=bom2, material=m_kg, kind='main', unit='公斤',
                                unit_usage=1, loss_rate=0)       # 1×60=60 公斤
code, d = api('POST', '/stock-ins', AT, CID, {
    'items': [{'fabric_name': '公斤料', 'lot_no': 'TWH2-L5', 'qty': 30, 'unit': '米',
               'material_id': m_kg.id}]})   # 30米 = 9公斤（gsm200×w150）
code, d = api('GET', '/demand-summary', AT, CID, {'style_id': style2.id})
rkg = ds_row(d.get('rows', []), m_kg.id)
check('D14 米→公斤折算库存 9 → 建议 51', rkg is not None and rkg['unit'] == '公斤'
      and rkg['qty_demand'] == '60.00' and rkg['qty_stock'] == '9.00'
      and rkg['qty_suggested'] == '51.00', str(rkg))

# D15 缺参：主单位米物料 + 公斤批次 → 折算 None 不入合计计 pending
bi_np = BomItem.objects.create(bom=bom2, material=m_np, kind='main', unit='米',
                               unit_usage=1, loss_rate=0)       # 1×60=60 米
code, d = api('POST', '/stock-ins', AT, CID, {
    'items': [{'fabric_name': '缺参面料', 'lot_no': 'TWH2-L6B', 'qty': 5, 'unit': '公斤',
               'material_id': m_np.id}]})
code, d = api('GET', '/demand-summary', AT, CID, {'style_id': style2.id})
rnp = ds_row(d.get('rows', []), m_np.id)
check('D15 缺参行不入合计 + conversion_pending_cnt>0',
      rnp is not None and rnp['qty_stock'] == '0.00' and rnp['qty_suggested'] == '60.00'
      and (d['summary'].get('conversion_pending_cnt') or 0) >= 1, str(rnp))

# D16 customer_id 过滤（负建议量照实返回：110-140-430=-460）
order_c = Order.objects.create(company=company, order_no='TWH2-O2', style=style,
                               customer=customer)
OrderItem.objects.create(order=order_c, color='黑', size='M', qty=50)
code, d = api('GET', '/demand-summary', AT, CID, {'customer_id': customer.id})
r1 = ds_row(d.get('rows', []), m1.id)
check('D16 customer 过滤：需求 110（m1 库存 90+50、在途 500-100+30）',
      code == 200 and r1 is not None and r1['qty_demand'] == '110.00'
      and r1['qty_stock'] == '140.00' and r1['qty_in_transit'] == '430.00'
      and r1['qty_suggested'] == '-460.00', str(r1))
check('D16b 负建议量 need_cnt=0', (d['summary'].get('need_cnt') or 0) == 0, str(d['summary']))

# D17 cancelled 订单剔除
order_x = Order.objects.create(company=company, order_no='TWH2-O5', style=style2,
                               status=Order.Status.CANCELLED)
OrderItem.objects.create(order=order_x, color='黑', size='M', qty=999)
code, d = api('GET', '/demand-summary', AT, CID, {'style_id': style2.id})
r2 = ds_row(d.get('rows', []), m2.id)
check('D17 cancelled 订单不计需求（仍 132）', r2 is not None
      and r2['qty_demand'] == '132.00', str(r2))


# ================= 权限与隔离 =================

# D18 401
code, _ = api('GET', '/suppliers')
check('D18 供应商无 token 401', code == 401, str(code))
code, _ = api('GET', '/demand-summary')
check('D18b 汇总无 token 401', code == 401, str(code))
code, _ = api('POST', '/receipts', None, CID, {'items': []})
check('D18c 暂收无 token 401', code == 401, str(code))

# D19 车间岗：查询不拦（项目惯例，同 Lot 列表），写操作 403
code, d = api('GET', '/suppliers', BT, CID)
check('D19 供应商列表查询 200（读不拦惯例）', code == 200, str(code))
code, d = api('POST', '/suppliers', BT, CID, {'code': 'X', 'name': 'x'})
check('D19b 供应商创建 403', code == 403, str(code))
code, d = api('PUT', f'/suppliers/{SUP1}', BT, CID, {'name': 'x'})
check('D19c 供应商修改 403', code == 403, str(code))
code, d = api('DELETE', f'/suppliers/{SUP1}', BT, CID)
check('D19d 供应商删除 403', code == 403, str(code))
code, d = api('POST', '/receipts', BT, CID, {'items': []})
check('D19c 暂收创建 403', code == 403, str(code))
code, d = api('GET', '/demand-summary', BT, CID)
check('D19d 需求汇总 403', code == 403, str(code))

# D20 legacy 'production' 岗：warehouse.manage 覆盖（暂收/汇总），supplier 零授权
code, d = api('GET', '/receipts', DT, CID)
check('D20 legacy 岗暂收列表 200（旧 coarse 覆盖）', code == 200, f'{code} {d}')
code, d = api('GET', '/demand-summary', DT, CID)
check('D20b legacy 岗汇总 200', code == 200, str(code))
code, d = api('POST', '/suppliers', DT, CID, {'code': 'TWH2-SUP9', 'name': 'x'})
check('D20c legacy 岗供应商 403（legacy 零授权）', code == 403, f'{code} {d}')

# D21 跨厂隔离
code, d = api('GET', '/receipts', CT, company_b.id)
check('D21 对照厂暂收列表 0 行', code == 200 and not d.get('results'), f'{code}')
code, d = api('GET', f'/receipts/{RECIPE}', CT, company_b.id)
check('D21b 对照厂读测试厂暂收 404', code == 404, str(code))
code, d = api('GET', f'/suppliers/{SUP1}', CT, company_b.id)
check('D21c 对照厂读测试厂供应商 404', code == 404, str(code))
code, d = api('POST', f'/receipts/{RECIPE}/confirm', CT, company_b.id)
check('D21d 对照厂确认测试厂暂收 404', code == 404, str(code))
code, d = api('GET', '/demand-summary', CT, company_b.id)
check('D21e 对照厂汇总空行', code == 200 and d.get('rows') == [], f'{code}')


# ================= 补分支（coverage missing=0 门禁） =================

# D22 汇总无过滤参数全量 + 无款式订单计数分支
order_ns = Order.objects.create(company=company, order_no='TWH2-O6')
OrderItem.objects.create(order=order_ns, color='黑', size='M', qty=10)
code, d = api('GET', '/demand-summary', AT, CID)
check('D22 无参全量 200 + 无款式订单计数',
      code == 200 and (d['summary'].get('order_no_style') or 0) >= 1, str(d['summary']))

# D23 暂收列表 keyword（receipt_no/供应商名）分支
code, d = api('GET', '/receipts', AT, CID, {'keyword': '手填名'})
check('D23 暂收 keyword 供应商名命中', code == 200
      and any(r.get('id') == HDR for r in d.get('results', [])), f'{code}')
code, d = api('GET', '/receipts', AT, CID, {'status': 'confirmed'})
check('D23b confirmed 过滤', code == 200
      and all(r['status'] == 'confirmed' for r in d.get('results', [])), f'{code}')

# D24 无 BOM 款式订单（order_no_bom 计数分支）
style_nb = Style.objects.create(company=company, style_no='TWH2-S3', name='无BOM款')
order_nb = Order.objects.create(company=company, order_no='TWH2-O7', style=style_nb)
OrderItem.objects.create(order=order_nb, color='黑', size='M', qty=10)
code, d = api('GET', '/demand-summary', AT, CID, {'style_id': style_nb.id})
check('D24 无 active BOM → 行空 + order_no_bom=1', code == 200 and d.get('rows') == []
      and d['summary'].get('order_no_bom') == 1, str(d['summary']))

# D25 汇总行含物料档案字段（前端展示口径）
code, d = api('GET', '/demand-summary', AT, CID, {'style_id': style2.id})
r2 = ds_row(d.get('rows', []), m2.id)
check('D25 行结构 material_code/name/unit',
      r2['material_code'] == 'TWH2-M4' and r2['material_name'] == '汇总面料'
      and r2['unit'] == '米', str(r2))

# D26 手工入库不带 material（存量零破坏：material NULL 不入汇总）
code, d = api('POST', '/stock-ins', AT, CID, {
    'items': [{'fabric_name': '老面料', 'lot_no': 'TWH2-L7B', 'qty': 10, 'unit': '米'}]})
check('D26 手工入库无 material 兼容', code == 201
      and (d.get('items') or [{}])[0].get('material') is None, f'{code} {d}')

# D27 供应商列表无 keyword 分支 + 分页结构
code, d = api('GET', '/suppliers', AT, CID)
check('D27 供应商列表无过滤 200', code == 200 and 'results' in d, f'{code}')

print(f'\n==== P2 warehouse 套件：{len(PASS)} pass / {len(FAIL)} fail ====')
if FAIL:
    print('FAILED:', FAIL)

# ================= D28+ 新代码分支补缺（coverage missing=0 门禁） =================

# D28 供应商编码为空 400（perform_create 卫语句）
code, d = api('POST', '/suppliers', AT, CID, {'code': '', 'name': '空码'})
check('D28 供应商空码 400', code == 400, f'{code} {d}')

# D29 暂收手填单号（跳过自动取号分支）
code, d = api('POST', '/receipts', AT, CID, {
    'receipt_no': 'TWH2-ZSM1',
    'items': [{'material_id': m1.id, 'fabric_name': '手单号', 'lot_no': 'TWH2-LM1',
               'arrived_qty': 3}]})
check('D29 手填暂收单号 201', code == 201 and d.get('receipt_no') == 'TWH2-ZSM1',
      f'{code} {d}')

# D30 暂收 PUT 头字段：挂供应商/采购单（含 404 校验分支）
code, d = api('PUT', f'/receipts/{HDR}', AT, CID, {'supplier': SUP1, 'purchase': po1.id})
check('D30 PUT 挂供应商/采购单 200', code == 200 and d.get('supplier') == SUP1
      and d.get('purchase') == po1.id, f'{code} {d}')
code, d = api('PUT', f'/receipts/{HDR}', AT, CID, {'supplier': 99999999})
check('D30b PUT 供应商不存在 400', code == 400, f'{code} {d}')
code, d = api('PUT', f'/receipts/{HDR}', AT, CID, {'purchase': 99999999})
check('D30c PUT 采购单不存在 400', code == 400, f'{code} {d}')

# D31 同批次二次到料：confirm 走既有批次分支（created=False，不重置批次/来源）
code, d = api('POST', '/receipts', AT, CID, {
    'purchase': po1.id,
    'items': [{'purchase_item': pi1.id, 'lot_no': 'TWH2-L1', 'arrived_qty': 10,
               'qualified_qty': 10}]})
R2 = d.get('id')
code, d = api('POST', f'/receipts/{R2}/confirm', AT, CID)
pi1.refresh_from_db()
lot1 = Lot.objects.get(company=company, lot_no='TWH2-L1')
check('D31 既有批次二次入库 200', code == 200, f'{code} {d}')
check('D31b 批次不重建（qty 仍首建 90、source_in 仍首行）+ 采购行累计到 110/100',
      lot1.qty == Decimal('90.00') and lot1.source_in_id == sii.id
      and pi1.arrived_qty == Decimal('110.00')
      and pi1.qualified_qty == Decimal('100.00'),
      f'{lot1.qty}/{pi1.arrived_qty}/{pi1.qualified_qty}')

# D32 未挂单床次（order=None）不进投产合计（planned 分支）
Production.objects.create(company=company, style=style2, bed_no='TWH2-B3',
                          total_qty=5, plan_qty=5, order=None)
code, d = api('GET', '/demand-summary', AT, CID, {'style_id': style2.id})
r2 = ds_row(d.get('rows', []), m2.id)
check('D32 未挂单床次不计投产（需求仍 132）', r2 is not None
      and r2['qty_demand'] == '132.00', str(r2))

# D33 领料行折算缺参（公斤 lot × 米缺参物料）→ 汇总 pending 计数再 +1
lot_kg = Lot.objects.create(company=company, lot_no='TWH2-LKG', fabric_name='缺参面料',
                            qty=10, unit='公斤')
code, d = api('POST', '/material-issues', AT, CID, {
    'items': [{'production_id': prod_aux.id, 'lot_id': lot_kg.id, 'qty': 2,
               'material_id': m_np.id}]})
ISS2 = d.get('id')
code, d = api('POST', f'/material-issues/{ISS2}/confirm', AT, CID)
check('D33 缺参领料确认 200', code == 200, f'{code} {d}')
code, d = api('GET', '/demand-summary', AT, CID, {'style_id': style2.id})
check('D33b 领料侧缺参计 conversion_pending（>=2）',
      (d['summary'].get('conversion_pending_cnt') or 0) >= 2, str(d['summary']))

# D34 新模型 __str__ 留痕（debug 输出分支）
sup_obj = Supplier.objects.get(company=company, pk=SUP1)
rcp_obj = Receipt.objects.get(company=company, pk=RECIPE)
itm_obj = ReceiptItem.objects.filter(receipt=rcp_obj).first()
check('D34 __str__ 输出含业务键', 'TWH2-SUP1' in str(sup_obj)
      and 'ZS' in str(rcp_obj) and 'TWH2-L1' in str(itm_obj),
      f'{sup_obj}|{rcp_obj}|{itm_obj}')

# ============ W-P25 安全库存补货点口径全链（测试设计 2026-11-02 新补） ============
# 口径（DemandSummaryView docstring）：qty_reorder=max(0, 需求-库存-在途+水位)；
# 设水位物料零需求也入池；safety_alert_cnt=库存+在途<水位 物料数；
# 一键生成采购单：demand_source=summary_import+material 锚点，行即入在途
m_safe = Material.objects.create(company=company, code='TWH2-M5', name='水位面料',
                                 category='fabric', unit='米', weight_gsm=200,
                                 width_cm=150, safety_stock=Decimal('50.00'))
code, d = api('GET', '/demand-summary', AT, CID)
r_safe = ds_row(d.get('rows', []), m_safe.id)
check('W-P25-01 设水位零需求物料入池（demand=0/safety=50）',
      r_safe is not None and r_safe['qty_demand'] == '0.00'
      and r_safe['safety_stock'] == '50.00', str(r_safe))
check('W-P25-02 补货点口径 qty_reorder=max(0,0-0-0+50)=50 且计数入列',
      r_safe is not None and r_safe['qty_reorder'] == '50.00'
      and d['summary']['reorder_cnt'] >= 1
      and d['summary']['safety_alert_cnt'] >= 1, str(d.get('summary')))
# 在途联动：挂物料的 PO 行 max(0,qty-arrived) 进在途 → reorder 相应收敛
po_s = PurchaseOrder.objects.create(company=company, order_no='TWH2-CG9',
                                    ordered_at=timezone.localdate())
PurchaseItem.objects.create(order=po_s, fabric_name='水位面料', qty=30, unit='米',
                            material=m_safe)
code, d = api('GET', '/demand-summary', AT, CID)
r_safe = ds_row(d.get('rows', []), m_safe.id)
check('W-P25-03 在途 30 进补货点口径（reorder=50-30=20，alert 仍 30<50）',
      r_safe is not None and r_safe['qty_in_transit'] == '30.00'
      and r_safe['qty_reorder'] == '20.00', str(r_safe))
# 一键生成采购单：summary_import 行带物料锚点，随即计入在途
code, d = api('POST', '/purchase-orders', AT, CID, {
    'items': [{'fabric_name': '水位面料', 'qty': '20.00', 'unit': '米',
               'material_id': m_safe.id, 'demand_source': 'summary_import'}]})
PO_S2 = d.get('id')
check('W-P25-04 一键生成采购单 201（草稿 CG 单）',
      code == 201 and PO_S2 and d.get('status') == 'draft', f'{code} {str(d)[:120]}')
code, d = api('GET', f'/purchase-orders/{PO_S2}', AT, CID)
si = (d.get('items') or [{}])[0]
check('W-P25-05 行带 material 锚点+summary_import 来源',
      si.get('material') == m_safe.id and si.get('demand_source') == 'summary_import',
      str(si))
code, d = api('GET', '/demand-summary', AT, CID)
r_safe = ds_row(d.get('rows', []), m_safe.id)
check('W-P25-06 生成后在途 50 → reorder 归 0（在途闭环）',
      r_safe is not None and r_safe['qty_in_transit'] == '50.00'
      and r_safe['qty_reorder'] == '0.00', str(r_safe))
# X-SAFETY 负水位防线（本轮缺陷修复回归）：POST safety_stock=-1 → 400
code, d = api('POST', '/tech/materials', AT, CID, {
    'code': 'TWH2-MNEG', 'name': '负水位物料', 'category': 'fabric',
    'unit': '米', 'safety_stock': '-1'})
check('W-P25-07 负水位入参 400（非负校验）', code == 400, f'{code} {d}')

print(f'\n==== P2 warehouse 套件（含补分支）：{len(PASS)} pass / {len(FAIL)} fail ====')
if FAIL:
    print('FAILED:', FAIL)
