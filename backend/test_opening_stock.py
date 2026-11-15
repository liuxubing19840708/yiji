# -*- coding: utf-8 -*-
"""
上线落地补码套件（test_opening_stock）—— SA-P2-5 交付，2026-10-24。

覆盖《扫菲项目上线落地方案》§11 对标差异 D1/D4 + D19 拍板价链规范化：
  D1 期初库存导入  O-IMP 系列端点 /warehouse/opening-stock/import：
          成功导入（Lot get_or_create/主单位折算/批次价落定/source_in 回填/库位备注落
          Lot.remark/StockIn source_type='opening' status='done'）；无缸号缺省批次「期初」；
          缺换算参数行；物料不存在；坏行整批拒（all-or-nothing）；重导拦截「期初已导入过」；
          401/403/legacy 'production' 岗 200/跨厂隔离
  D19 价链规范化  P-PU 系列采购价 price_unit（kg→m 折算/缺参 400/缺省主单位零换算）；
          S-PU 系列入库手录单价规范化；R-PU 暂收确认链路规范化传递（pi.price 已规范化直传
          + price_unit 随行留痕）；O-IMP-07/08 期初导入 kg 行
  D4 开裁缺 BOM 拦截  D4 系列端点 POST /productions：
          有款式无 active BOM 400「该款式无生效BOM…」/ bom_bypass+reason 放行+audit 留痕 /
          bypass 缺 reason 400 / 无款式（存量单）不拦 / 有 BOM 放行 / legacy 裁床岗零回归 /
          非挂单带款式同样拦截
跑法：manage.py shell -c "exec(compile(open('test_opening_stock.py',encoding='utf-8').read(),'test_opening_stock.py','exec'))"
幂等：自建「上线补丁测试厂(可删)/上线补丁对照厂(可删)」并跨次复用；每轮 preclean 硬清
本厂全部业务行（真实厂 86 零接触）。
"""
import os
import tempfile
import json
from decimal import Decimal

from django.contrib.auth.hashers import make_password
from django.test import Client
from django.utils import timezone

from apps.accounts.models import Company, Post as PostM, User, UserCompany
from apps.accounts.services import create_company_with_owner
from apps.catalog.models import Style
from apps.ops.models import AuditLog
from apps.orders.models import Order
from apps.production.models import Production
from apps.tech.models import Bom, Material
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


def api_upload(path, token, cid, file_path, field='file'):
    """multipart 文件上传（Excel 导入用）。"""
    c = Client()
    with open(file_path, 'rb') as f:
        r = c.post(f'/api{path}', {field: f},
                   HTTP_AUTHORIZATION=f'Bearer {token}',
                   HTTP_X_COMPANY_ID=str(cid))
    try:
        body = json.loads(r.content.decode('utf-8'))
    except Exception:
        body = {}
    return r.status_code, body


PHONE_A = '13988710501'      # 测试厂管理员
PHONE_B = '13988710502'      # 测试厂车间岗（无 warehouse/production 权限 → 403）
PHONE_C = '13988710503'      # 对照厂管理员（跨厂隔离）
PHONE_D = '13988710504'      # 测试厂 legacy 'production' 岗（旧 coarse key）
CO_A = '上线补丁测试厂(可删)'
CO_B = '上线补丁对照厂(可删)'

company = Company.objects.filter(name=CO_A).first()
if company is None:
    u = User.objects.filter(phone=PHONE_A).first() or User.objects.create(
        phone=PHONE_A, username=PHONE_A, real_name='上线补丁厂长',
        password=make_password('yisao1234'), is_active=True)
    company, _ = create_company_with_owner(u, CO_A)
else:
    User.objects.filter(phone=PHONE_A).update(
        password=make_password('yisao1234'), is_active=True)
CID = company.id

company_b = Company.objects.filter(name=CO_B).first()
if company_b is None:
    u3 = User.objects.filter(phone=PHONE_C).first() or User.objects.create(
        phone=PHONE_C, username=PHONE_C, real_name='上线补丁对照厂长',
        password=make_password('yisao1234'), is_active=True)
    company_b, _ = create_company_with_owner(u3, CO_B)

# 车间岗（无 warehouse/production 权限）
if UserCompany.objects.filter(company=company, user__phone=PHONE_B).exists():
    u_b = User.objects.get(phone=PHONE_B)
else:
    u_b = User.objects.filter(phone=PHONE_B).first() or User.objects.create(
        phone=PHONE_B, username=PHONE_B, real_name='上线补丁车间',
        password=make_password('yisao1234'), is_active=True)
    workshop_post = PostM.objects.filter(company=company, name='补丁车间').first() \
        or PostM.objects.create(company=company, name='补丁车间',
                                permissions=['my_salary.view', 'working.view'])
    UserCompany.objects.create(user=u_b, company=company, post=workshop_post,
                               status=UserCompany.Status.ACTIVE,
                               hired_date=timezone.localdate())

# legacy 'production' 岗（旧 coarse key：覆盖 warehouse.manage + production.manage）
if UserCompany.objects.filter(company=company, user__phone=PHONE_D).exists():
    u_d = User.objects.get(phone=PHONE_D)
else:
    u_d = User.objects.filter(phone=PHONE_D).first() or User.objects.create(
        phone=PHONE_D, username=PHONE_D, real_name='上线补丁legacy裁床',
        password=make_password('yisao1234'), is_active=True)
    legacy_post = PostM.objects.filter(company=company, name='补丁legacy裁床').first() \
        or PostM.objects.create(company=company, name='补丁legacy裁床',
                                permissions=['production'])
    UserCompany.objects.create(user=u_d, company=company, post=legacy_post,
                               status=UserCompany.Status.ACTIVE,
                               hired_date=timezone.localdate())

AT = api('POST', '/auth/login', data={'phone': PHONE_A, 'password': 'yisao1234'})[1].get('access', '')
BT = api('POST', '/auth/login', data={'phone': PHONE_B, 'password': 'yisao1234'})[1].get('access', '')
CT = api('POST', '/auth/login', data={'phone': PHONE_C, 'password': 'yisao1234'})[1].get('access', '')
DT = api('POST', '/auth/login', data={'phone': PHONE_D, 'password': 'yisao1234'})[1].get('access', '')
assert AT and BT and CT and DT, '登录失败'

# ---- preclean（先子后父硬清两厂业务行；真实厂 86 零接触） ----
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
    Order.all_objects.filter(company=co)._raw_delete(Order.all_objects.db)
    AuditLog.all_objects.filter(company=co)._raw_delete(AuditLog.all_objects.db)
    Supplier.all_objects.filter(company=co)._raw_delete(Supplier.all_objects.db)
    BomItem_all = None
    from apps.tech.models import BomItem  # noqa: E402
    BomItem.all_objects.filter(bom__company=co)._raw_delete(BomItem.all_objects.db)
    Bom.all_objects.filter(company=co)._raw_delete(Bom.all_objects.db)
    # Material 被仓库行 PROTECT 引用，上面已清完
    Material.all_objects.filter(company=co)._raw_delete(Material.all_objects.db)
    Style.all_objects.filter(company=co)._raw_delete(Style.all_objects.db)

# ============ 测试数据 ============
# M1 梭织米主单位（无换算参数）；M2 针织公斤主单位（克重200 幅宽150 → per_m_kg=0.3 公斤/米）；
# M3 梭织米主单位但带换算参数（公斤↔米互折测试）；M4 米主单位无参数（缺换算参数行）；
# M5 公斤主单位无参数（单价换算缺参行）
m1 = Material.objects.create(company=company, code='OP-M1', name='期初纯棉帆布',
                             category='fabric', unit='米')
m2 = Material.objects.create(company=company, code='OP-M2', name='期初针织汗布',
                             category='fabric', unit='公斤',
                             weight_gsm=Decimal('200'), width_cm=Decimal('150'))
m3 = Material.objects.create(company=company, code='OP-M3', name='期初梭织涤棉',
                             category='fabric', unit='米',
                             weight_gsm=Decimal('200'), width_cm=Decimal('150'))
m4 = Material.objects.create(company=company, code='OP-M4', name='期初无参米料',
                             category='fabric', unit='米')
m5 = Material.objects.create(company=company, code='OP-M5', name='期初无参公斤料',
                             category='fabric', unit='公斤')

# ============ D1 期初库存导入 ============
print('---- D1 期初库存 Excel 导入 ----')

s, d = api('POST', '/opening-stock/import', token=AT, cid=CID, data={'rows': [
    {'物料编码': 'OP-M1', '缸号': 'G-001', '数量': '100', '单位': '',
     '批次单价': '12.5', '单价单位': '', '库位': 'A区-1', '备注': '期初盘点'},
    {'物料编码': 'OP-M3', '缸号': 'G-002', '数量': '30', '单位': '公斤',
     '批次单价': '88', '单价单位': '公斤'},
]})
check('O-IMP-01 成功导入 created=2 in_no RK 前缀', s == 200 and d.get('created') == 2
      and str(d.get('in_no', '')).startswith('RK') and d.get('stock_in_id'), f'{s} {d}')
si = StockIn.objects.filter(company=company, pk=d.get('stock_in_id')).first() if d.get('stock_in_id') else None
check('O-IMP-01b StockIn source_type=opening status=done remark 期初库存导入',
      si is not None and si.source_type == 'opening' and si.status == 'done'
      and si.remark == '期初库存导入',
      f'{si.source_type if si else None}/{si.status if si else None}')
lot1 = Lot.objects.filter(company=company, lot_no='G-001').first()
lot2 = Lot.objects.filter(company=company, lot_no='G-002').first()
check('O-IMP-01c Lot G-001 主单位原值+批次价+库位备注落 remark',
      lot1 is not None and lot1.qty == Decimal('100') and lot1.unit == '米'
      and lot1.unit_cost == Decimal('12.500')
      and '库位：A区-1' in lot1.remark and '期初盘点' in lot1.remark,
      f'{lot1.qty if lot1 else None}/{lot1.unit_cost if lot1 else None}/{lot1.remark if lot1 else ""}')
# M3：30 公斤 → 30/(200×150/100000)=30/0.3=100 米；88 元/公斤 × 0.3 = 26.4 元/米
item2 = StockInItem.objects.filter(stock_in=si, lot_no='G-002').first() if si else None
check('O-IMP-01d Lot G-002 公斤→米数量折算 100 米 + 单价 88 元/公斤→26.4 元/米规范化',
      lot2 is not None and lot2.qty == Decimal('100.00') and lot2.unit == '米'
      and lot2.unit_cost == Decimal('26.400')
      and item2 is not None and item2.qty == Decimal('100.00')
      and item2.unit_price == Decimal('26.400') and item2.price_unit == 'kg'
      and item2.material_id == m3.id,
      f'lot={lot2.qty if lot2 else None}/{lot2.unit_cost if lot2 else None} '
      f'item={item2.unit_price if item2 else None}/{item2.price_unit if item2 else None}')
check('O-IMP-01e 新建批次回填 source_in',
      lot1 is not None and lot1.source_in is not None
      and lot1.source_in.stock_in_id == si.id, '')

# xlsx 真文件（模板列头校验）+ 无缸号缺省批次
tmpdir = tempfile.mkdtemp(prefix='opening_imp_')
from openpyxl import Workbook  # noqa: E402
xlsx_path = os.path.join(tmpdir, 'opening.xlsx')
wb = Workbook()
ws = wb.active
ws.append(['物料编码', '缸号', '数量', '单位', '批次单价', '单价单位', '库位', '备注'])
ws.append(['OP-M1', '', '55', '', '10', '', '', ''])
ws.append(['OP-M2', 'G-003', '20', '米', '66', '米', '', ''])  # 米→公斤：20×0.3=6 公斤
wb.save(xlsx_path)
s, d = api_upload('/opening-stock/import', AT, CID, xlsx_path)
check('O-IMP-02 xlsx 导入成功；无缸号缺省批次「期初」',
      s == 200 and d.get('created') == 2 and d.get('errors') == [], f'{s} {d}')
lot_d = Lot.objects.filter(company=company, lot_no='期初').first()
check('O-IMP-02b 缺省批次 Lot 期初 数量55 主单位米',
      lot_d is not None and lot_d.qty == Decimal('55') and lot_d.unit == '米'
      and lot_d.fabric_name == '期初纯棉帆布', '')
lot3 = Lot.objects.filter(company=company, lot_no='G-003').first()
check('O-IMP-02c 米→公斤数量折算 20×0.3=6 公斤 + 单价 66 元/米→220 元/公斤规范化',
      lot3 is not None and lot3.qty == Decimal('6.00') and lot3.unit == '公斤'
      and lot3.unit_cost == Decimal('220.000'),
      f'{lot3.qty if lot3 else None}/{lot3.unit_cost if lot3 else None}')
item3 = StockInItem.objects.filter(stock_in__company=company, lot_no='G-003').first()
check('O-IMP-02d 单价单位中文列「米」归一为 m 留痕',
      item3 is not None and item3.price_unit == 'm',
      f'{item3.price_unit if item3 else None}')

# 缺换算参数行（米料报公斤数量，无克重/幅宽）
s, d = api('POST', '/opening-stock/import', token=AT, cid=CID, data={'rows': [
    {'物料编码': 'OP-M4', '缸号': 'G-X', '数量': '10', '单位': '公斤'},
]})
check('O-IMP-03 缺换算参数行 → errors「缺换算参数」整批不入库',
      s == 200 and d.get('created') == 0
      and len(d.get('errors', [])) == 1 and d['errors'][0]['reason'] == '缺换算参数'
      and not StockInItem.objects.filter(stock_in__company=company, lot_no='G-X').exists(),
      f'{s} {d}')

# 物料不存在 + 坏行整批拒
s, d = api('POST', '/opening-stock/import', token=AT, cid=CID, data={'rows': [
    {'物料编码': 'OP-M1', '缸号': 'G-Y', '数量': '10'},
    {'物料编码': 'OP-NOPE', '缸号': 'G-Z', '数量': '10'},
]})
check('O-IMP-04 物料不存在 → 整批拒 errors 带行号',
      s == 200 and d.get('created') == 0 and len(d.get('errors', [])) == 1
      and d['errors'][0]['reason'] == '物料不存在' and d['errors'][0]['row'] == 3, f'{s} {d}')
s, d = api('POST', '/opening-stock/import', token=AT, cid=CID, data={'rows': [
    {'物料编码': 'OP-M1', '缸号': 'G-W', '数量': '10'},
    {'物料编码': 'OP-M1', '缸号': 'G-V', '数量': '0'},
]})
check('O-IMP-05 坏行（数量0）→ all-or-nothing 整批拒',
      s == 200 and d.get('created') == 0 and '大于0' in d['errors'][0]['reason']
      and not StockInItem.objects.filter(stock_in__company=company, lot_no='G-W').exists()
      and not Lot.objects.filter(company=company, lot_no='G-W').exists(), f'{s} {d}')

# 重导拦截（同物料+同缸号已有期初行）
s, d = api('POST', '/opening-stock/import', token=AT, cid=CID, data={'rows': [
    {'物料编码': 'OP-M1', '缸号': 'G-001', '数量': '100'},
]})
check('O-IMP-06 重导拦截「期初已导入过」整批拒',
      s == 200 and d.get('created') == 0 and len(d.get('errors', [])) == 1
      and d['errors'][0]['reason'] == '期初已导入过', f'{s} {d}')
# 批内重复（同物料同缸号两行）
s, d = api('POST', '/opening-stock/import', token=AT, cid=CID, data={'rows': [
    {'物料编码': 'OP-M4', '缸号': 'G-DUP', '数量': '1'},
    {'物料编码': 'OP-M4', '缸号': 'G-DUP', '数量': '2'},
]})
check('O-IMP-06b 批内同物料同缸号重复 → 整批拒',
      s == 200 and d.get('created') == 0 and '重复' in d['errors'][0]['reason'], f'{s} {d}')

# D19 期初导入单价换算缺参行（M5 公斤主单位无克重/幅宽，单价单位=米 → 需折算缺参）
s, d = api('POST', '/opening-stock/import', token=AT, cid=CID, data={'rows': [
    {'物料编码': 'OP-M5', '缸号': 'G-P', '数量': '10', '批次单价': '88', '单价单位': '米'},
]})
check('O-IMP-07 单价换算缺克重/幅宽 → 该行报错',
      s == 200 and d.get('created') == 0
      and '缺克重/幅宽' in d['errors'][0]['reason'], f'{s} {d}')
# 单价单位=主单位 → 零换算不报错
s, d = api('POST', '/opening-stock/import', token=AT, cid=CID, data={'rows': [
    {'物料编码': 'OP-M5', '缸号': 'G-P2', '数量': '10', '批次单价': '88', '单价单位': '公斤'},
]})
check('O-IMP-07b 单价单位=主单位零换算（88 元/公斤原样落库）',
      s == 200 and d.get('created') == 1, f'{s} {d}')

# 权限
s, _d = api('POST', '/opening-stock/import', data={'rows': []})
check('O-IMP-08 无 token 401', s == 401, f's={s}')
s, _d = api('POST', '/opening-stock/import', token=BT, cid=CID, data={'rows': [
    {'物料编码': 'OP-M1', '数量': '1'}]})
check('O-IMP-09 车间岗 403（import 挂 warehouse.manage）', s == 403, f's={s}')
s, d = api('POST', '/opening-stock/import', token=DT, cid=CID, data={'rows': [
    {'物料编码': 'OP-M1', '缸号': 'G-LEG', '数量': '3'}]})
check('O-IMP-10 legacy production 岗 200（旧 coarse 覆盖 warehouse.manage）',
      s == 200 and d.get('created') == 1, f'{s} {d}')

# 跨厂隔离：对照厂导本厂编码 → 物料不存在；对照厂数据独立
m_b = Material.objects.create(company=company_b, code='OP-B1', name='对照厂物料',
                              category='fabric', unit='米')
s, d = api('POST', '/opening-stock/import', token=CT, cid=company_b.id, data={'rows': [
    {'物料编码': 'OP-M1', '缸号': 'G-B', '数量': '10'},
]})
check('O-IMP-11 跨厂物料编码不可见（物料不存在）',
      s == 200 and d.get('created') == 0 and d['errors'][0]['reason'] == '物料不存在', f'{s} {d}')
s, d = api('POST', '/opening-stock/import', token=CT, cid=company_b.id, data={'rows': [
    {'物料编码': 'OP-B1', '缸号': 'G-B', '数量': '10'}]})
check('O-IMP-11b 对照厂自己的物料导入成功（公司隔离互不污染）',
      s == 200 and d.get('created') == 1, f'{s} {d}')

# ============ D19 价链规范化（采购/入库/暂收链路） ============
print('---- D19 单价单位可选（价链规范化） ----')

s, d = api('POST', '/purchase-orders', token=AT, cid=CID, data={
    'items': [{'fabric_name': '涤棉', 'qty': '100', 'unit': '米',
               'price': '88', 'price_unit': 'kg', 'material_id': m3.id}]})
pi_id = d['items'][0]['id'] if s == 201 and d.get('items') else None
check('P-PU-01 采购价 88 元/公斤 → 存储 26.4 元/米规范化 + price_unit 留痕',
      s == 201 and pi_id and Decimal(str(d['items'][0]['price'])) == Decimal('26.400')
      and d['items'][0]['price_unit'] == 'kg', f'{s} {d}')
s, d = api('POST', '/purchase-orders', token=AT, cid=CID, data={
    'items': [{'fabric_name': '无参料', 'qty': '10', 'unit': '米',
               'price': '88', 'price_unit': 'kg', 'material_id': m4.id}]})
check('P-PU-02 采购价换算缺克重/幅宽 → 400',
      s == 400 and '缺克重/幅宽' in json.dumps(d, ensure_ascii=False), f'{s} {d}')
s, d = api('POST', '/purchase-orders', token=AT, cid=CID, data={
    'items': [{'fabric_name': '主单位价', 'qty': '10', 'unit': '米',
               'price': '12.5', 'material_id': m3.id}]})
check('P-PU-03 price_unit 缺省=主单位零换算（12.5 原样落库）',
      s == 201 and Decimal(str(d['items'][0]['price'])) == Decimal('12.500')
      and d['items'][0]['price_unit'] == '', f'{s} {d}')
s, d = api('POST', '/purchase-orders', token=AT, cid=CID, data={
    'items': [{'fabric_name': '无物料带单位', 'qty': '10', 'unit': '米',
               'price': '88', 'price_unit': 'kg'}]})
check('P-PU-04 price_unit 带而无物料 → 400',
      s == 400 and '物料' in json.dumps(d, ensure_ascii=False), f'{s} {d}')

s, d = api('POST', '/stock-ins', token=AT, cid=CID, data={
    'items': [{'fabric_name': '涤棉', 'lot_no': 'S-PU-1', 'qty': '10', 'unit': '米',
               'unit_price': '88', 'price_unit': 'kg', 'material_id': m3.id}]})
check('S-PU-01 入库手录 88 元/公斤 → unit_price 26.4 元/米规范化',
      s == 201 and Decimal(str(d['items'][0]['unit_price'])) == Decimal('26.400')
      and d['items'][0]['price_unit'] == 'kg', f'{s} {d}')
si_id = d.get('id')
s, d = api('POST', f'/stock-ins/{si_id}/confirm', token=AT, cid=CID)
lot_pu = Lot.objects.filter(company=company, lot_no='S-PU-1').first()
check('S-PU-02 确认后 Lot.unit_cost=26.4（成本侧语义零改动：恒主单位规范化）',
      s == 200 and lot_pu is not None and lot_pu.unit_cost == Decimal('26.400'), f'{s} {d}')
s, d = api('POST', '/stock-ins', token=AT, cid=CID, data={
    'items': [{'fabric_name': '无物料带单位', 'lot_no': 'S-PU-2', 'qty': '10',
               'unit_price': '88', 'price_unit': 'kg'}]})
check('S-PU-03 price_unit 带而无物料 → 400',
      s == 400 and '物料' in json.dumps(d, ensure_ascii=False), f'{s} {d}')

# 暂收确认链路：PO（kg 报价已规范化 26.4）→ 暂收 → 确认 → 入库行直传规范化价
po2 = PurchaseOrder.objects.filter(company=company, items__id=pi_id).first()
s, d = api('POST', '/receipts', token=AT, cid=CID, data={
    'purchase': po2.id,
    'items': [{'purchase_item': pi_id, 'lot_no': 'R-PU-1',
               'arrived_qty': '50', 'qualified_qty': '50'}]})
rc_id = d.get('id')
check('R-PU-01 暂收创建（挂 PO 行带物料）', s == 201 and rc_id, f'{s} {d}')
s, d = api('POST', f'/receipts/{rc_id}/confirm', token=AT, cid=CID)
rc_item = StockInItem.objects.filter(stock_in__company=company, lot_no='R-PU-1').first()
check('R-PU-02 确认自动入库 unit_price=26.4 规范化直传 + price_unit 随行留痕',
      s == 200 and rc_item is not None
      and rc_item.unit_price == Decimal('26.400') and rc_item.price_unit == 'kg',
      f'{s} {rc_item.unit_price if rc_item else None}/{rc_item.price_unit if rc_item else None}')

# ============ D4 开裁缺 BOM 拦截 ============
print('---- D4 开裁缺 BOM 拦截 ----')

style_nb = Style.objects.create(company=company, style_no='OP-S-NB', name='无BOM款')
style_wb = Style.objects.create(company=company, style_no='OP-S-WB', name='有BOM款')
Bom.objects.create(company=company, style=style_wb, version=1, status=Bom.Status.ACTIVE)
order_nb = Order.objects.create(company=company, order_no='OP-O-NB',
                                status=Order.Status.PRODUCING, style=style_nb)
order_wb = Order.objects.create(company=company, order_no='OP-O-WB',
                                status=Order.Status.PRODUCING, style=style_wb)
order_ns = Order.objects.create(company=company, order_no='OP-O-NS',
                                status=Order.Status.PRODUCING, style=None)

s, d = api('POST', '/productions', token=AT, cid=CID,
           data={'bed_no': 'D4-B1', 'order_id': order_nb.id})
check('D4-01 挂单款式无 active BOM → 400 拦截',
      s == 400 and '无生效BOM' in json.dumps(d, ensure_ascii=False)
      and not Production.objects.filter(company=company, bed_no='D4-B1').exists(),
      f'{s} {d}')

s, d = api('POST', '/productions', token=AT, cid=CID,
           data={'bed_no': 'D4-B2', 'order_id': order_nb.id,
                 'bom_bypass': True, 'bypass_reason': '交期紧，客户书面确认旧料开裁'})
check('D4-02 bom_bypass+reason → 放行 201',
      s == 201 and Production.objects.filter(company=company, bed_no='D4-B2').exists(),
      f'{s} {d}')
au = AuditLog.objects.filter(company=company, message__contains='无生效BOM开裁放行').first()
check('D4-02b ops audit 留痕（床次+款式+原因）',
      au is not None and 'D4-B2' in au.message
      and '交期紧，客户书面确认旧料开裁' in au.message and au.style_no == 'OP-S-NB',
      f'{au.message if au else None}')

s, d = api('POST', '/productions', token=AT, cid=CID,
           data={'bed_no': 'D4-B3', 'order_id': order_nb.id, 'bom_bypass': True})
check('D4-03 bypass 缺 reason → 400',
      s == 400 and '放行原因' in json.dumps(d, ensure_ascii=False), f'{s} {d}')

s, d = api('POST', '/productions', token=AT, cid=CID,
           data={'bed_no': 'D4-B4', 'order_id': order_ns.id})
check('D4-04 订单无款式（存量/特殊单）→ BOM 校验不触发（原「请选择款式」先行）',
      s == 400 and 'BOM' not in json.dumps(d, ensure_ascii=False)
      and '款式' in json.dumps(d, ensure_ascii=False), f'{s} {d}')

s, d = api('POST', '/productions', token=AT, cid=CID,
           data={'bed_no': 'D4-B5', 'order_id': order_wb.id})
check('D4-05 有 active BOM → 正常放行 201',
      s == 201 and Production.objects.filter(company=company, bed_no='D4-B5').exists(),
      f'{s} {d}')

s, d = api('POST', '/productions', token=DT, cid=CID,
           data={'bed_no': 'D4-B6', 'style': style_wb.id})
check('D4-06 legacy 裁床岗（production coarse）建床零回归 201',
      s == 201 and Production.objects.filter(company=company, bed_no='D4-B6').exists(),
      f'{s} {d}')
s, d = api('POST', '/productions', token=DT, cid=CID,
           data={'bed_no': 'D4-B7', 'style': style_nb.id})
check('D4-06b legacy 裁床岗同样受 BOM 拦截（校验不豁免旧岗）',
      s == 400 and '无生效BOM' in json.dumps(d, ensure_ascii=False), f'{s} {d}')

s, d = api('POST', '/productions', token=AT, cid=CID,
           data={'bed_no': 'D4-B8', 'style': style_nb.id})
check('D4-07 非挂单带款式（无BOM）→ 同样 400 拦截',
      s == 400 and '无生效BOM' in json.dumps(d, ensure_ascii=False), f'{s} {d}')
s, d = api('POST', '/productions', token=AT, cid=CID,
           data={'bed_no': 'D4-B9', 'style': style_wb.id,
                 'bom_bypass': True, 'bypass_reason': '非挂单例外通道'})
check('D4-07b 非挂单 bypass+reason → 放行（请求款式同受例外通道）',
      s == 201, f'{s} {d}')

# 权限不变：车间岗（无 production 权限）建床仍 403
s, d = api('POST', '/productions', token=BT, cid=CID,
           data={'bed_no': 'D4-BX', 'style': style_wb.id})
check('D4-08 权限不变：车间岗 403', s == 403, f's={s}')

# ============ 汇总 ============
print()
print(f'==== test_opening_stock: {len(PASS)} pass / {len(FAIL)} fail ====')
if FAIL:
    print('FAILED:', FAIL)
    raise SystemExit(1)
