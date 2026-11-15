# -*- coding: utf-8 -*-
"""跨域数据一致性 E2E（对拍套件）：构造一条贯穿全业务域的拟真数据链，
在每个域边界用「另一域的派生数字」互相印证，挖掘口径不一致/漏记/错记问题。

数据链（全部数字手工推演，写死断言）：
  档案: 3工序(裁2.5/车3.0/锁1.0→款式价自动汇总6.5) + 客户
  BOM:  面料M1 1.2米/件(损耗6%→毛耗1.272) 辅料M2 1个/件(0.4%→1.004)
  报价: qty200 材料费4800 labor_base=6.5(款式工序快照) 小批量+30%
  订单: 黑M100@30 黑L60@30 白M40@28 → total 200 revenue 5920
  床次: 挂单建床200件 bundle_limit30 → 8扎(30,30,30,10/30,30/30,10) Σ=200
  库存: 期初 M1 500米@20 M2 300个@2；采购 88元/kg×0.3kg/m=26.4元/m 入库1000米@26.4
  领料: M1 264.4(定额254.4 超领10) M2 200.8(定额吻合) → 库存余 235.6/99.2
  登记: W1 扎1-3全工序(270件/585元) W2 扎4-6全工序+扎7只裁车(270件/620元)
        合计540件/1205元；扎8 不登记
  成本: 材料5689.6(264.4×20+200.8×2) 人工1205 收入5920 制费100 → total 6994.6
  出货: 黑M50 黑L30 白M40 → 应收 50×30+30×30+40×28=3520 核销1000+2520
  结算: W1 今日结算 585 → unsettled 0；W2 调整-10 只留痕不冲未结（仍620）
  看板: registered 540 done 480 working 60 cut 200 ticket 7 工序 190/190/160
        rank 270/270；dim 五维件数合计均=540 金额合计=1205
  审计: 制费修改/出货审核/应收核销/结算工资/撤销登记 均落链

运行：PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe manage.py shell -c \
  "exec(compile(open('test_e2e_consistency.py', encoding='utf-8').read(), 'test_e2e_consistency.py', 'exec'))"
"""
import json
import re
from datetime import timedelta

from django.test import Client
from django.utils import timezone

from apps.accounts.models import User, UserCompany, Post as PostM, SMSCode
from apps.catalog.models import Style, StyleProcess, Process, Ticket, Customer, TicketPrintLog
from apps.production.models import Production, ProductionProcess
from apps.records.models import Record
from apps.salary.models import SettleApply, Settlement, SalaryAdjust
from apps.ops.models import CompanySetting, AuditLog, Notification
from apps.orders.models import Order
from apps.tech.models import Material, Bom, BomItem
from apps.warehouse.models import Lot, PurchaseOrder, StockIn, MaterialIssue, ProductionLotLink
from apps.cost.models import OrderCost
from apps.quotation.models import Quotation, QuoteMaterial
from apps.shipment.models import Shipment, ShipmentItem, PackingBox
from apps.finance.models import Receivable, ReceiptLine

TODAY = timezone.localdate().isoformat()
# 统一隔离入口注入 True：空白测试库无需预清理，结束后由 Django 销毁测试库。
# 独立 shell 跑法仍保留原有的专用测试厂自清理能力。
ISOLATED_TEST_RUN = globals().get('ISOLATED_TEST_RUN', False)

c = Client()
PASS, FAIL = [], []
MARK = '对拍'
COMPANY_NAME = '一致性对拍厂'
OWNER_PH = '13911110000'
W1_PH, W2_PH = '13911112222', '13911113333'


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    print(('PASS' if cond else 'FAIL'), name, ('' if cond else f'| {detail}'))


def eq(a, b, tol=0.005):
    try:
        return abs(float(a) - float(b)) <= tol
    except (TypeError, ValueError):
        return False


def req(method, path, token=None, cid=None, data=None, **kw):
    extra = {}
    if token:
        extra['HTTP_AUTHORIZATION'] = f'Bearer {token}'
    if cid:
        extra['HTTP_X_COMPANY_ID'] = str(cid)
    resp = getattr(c, method)(f'/api{path}', data=data, content_type='application/json', **extra, **kw)
    try:
        body = json.loads(resp.content.decode('utf-8'))
    except Exception:
        body = {}
    return resp.status_code, body


def purge_company(cid):
    """整厂硬删（叶子→根；PROTECT 链 IssueItem→BomItem→Bom/Material→Style 须按序）。
    安全阀：只允许删本套件自建的对拍测试厂（公司名含标记），真实厂绝不触碰。"""
    from apps.ops.models import Company as _CoGuard
    _name = (_CoGuard.all_objects.filter(pk=cid).values_list('name', flat=True).first() or '')
    assert COMPANY_NAME in _name and '汉川旭东' not in _name, f'拒绝删除非对拍厂: {_name!r}'
    Record.all_objects.filter(company_id=cid).update(settlement=None)
    from apps.shipment.models import ShipmentPrintLog  # noqa: E402
    from apps.core.models import ClientLog  # noqa: E402
    from apps.warehouse.models import StockInItem, PurchaseItem  # noqa: E402
    from apps.orders.models import OrderItem  # noqa: E402

    def _del(qs, label):
        try:
            qs.delete()
        except Exception as e:
            print(f'  [purge] {label}: {type(e).__name__} {str(e)[:80]}')

    # 第一段：有 company 字段的叶子（MaterialIssue.delete 级联删 IssueItem——
    # IssueItem.bom_item PROTECT，必须先于 BomItem）
    for m in (Record, TicketPrintLog, Ticket, ProductionLotLink, MaterialIssue,
              Settlement, SettleApply, SalaryAdjust, ReceiptLine, Receivable,
              ShipmentPrintLog, Shipment, Quotation, OrderCost):
        _del(m.all_objects.filter(company_id=cid), m.__name__)
    # 第二段：无 company 字段的子表按父关系过滤（BomItem.material/bom 均 PROTECT）
    _del(ProductionProcess.all_objects.filter(production__company_id=cid), 'ProductionProcess')
    _del(OrderItem.all_objects.filter(order__company_id=cid), 'OrderItem')
    _del(StockInItem.all_objects.filter(stock_in__company_id=cid), 'StockInItem')
    _del(PurchaseItem.all_objects.filter(order__company_id=cid), 'PurchaseItem')
    _del(StyleProcess.all_objects.filter(style__company_id=cid), 'StyleProcess')
    _del(PackingBox.all_objects.filter(shipment__company_id=cid), 'PackingBox')
    _del(ShipmentItem.all_objects.filter(shipment__company_id=cid), 'ShipmentItem')
    _del(QuoteMaterial.all_objects.filter(quotation__company_id=cid), 'QuoteMaterial')
    _del(BomItem.all_objects.filter(bom__company_id=cid), 'BomItem')
    # 第三段：根表（Lot→Bom→Material→Style；Bom.style/Material PROTECT）
    for m in (Production, Order, StockIn, PurchaseOrder, Lot,
              Bom, Material, Style, Customer, Process, Notification, AuditLog,
              ClientLog, CompanySetting):
        _del(m.all_objects.filter(company_id=cid), m.__name__)
    UserCompany.all_objects.filter(company_id=cid).delete()
    PostM.all_objects.filter(company_id=cid).delete()
    try:
        from apps.ops.models import Company as _Co
        _Co.all_objects.filter(pk=cid)._raw_delete(_Co.all_objects.db)
    except Exception as e:
        print(f'  [purge] Company: {type(e).__name__} {str(e)[:80]}')


# ---- 预清理：上次中断残留的对拍厂 + 员工手机号 ----
from apps.ops.models import Company  # noqa: E402

if not ISOLATED_TEST_RUN:
    for _cid in Company.all_objects.filter(name__contains=COMPANY_NAME).values_list('id', flat=True):
        purge_company(_cid)
    for _ph in (OWNER_PH, W1_PH, W2_PH):
        Record.all_objects.filter(user__phone=_ph)._raw_delete(Record.all_objects.db)
        SMSCode.objects.filter(phone=_ph).delete()
        _u = User.objects.filter(phone=_ph).first()
        if _u:
            UserCompany.all_objects.filter(user=_u)._raw_delete(UserCompany.all_objects.db)
            _u.delete()

# ============================================================
# 1. 自建管理员 + 登录 + 建对拍厂（空厂起步，不依赖演示账号）
# ============================================================
from django.contrib.auth.hashers import make_password  # noqa: E402

owner = User.objects.create(
    username=OWNER_PH, phone=OWNER_PH, real_name='对拍管理员',
    password=make_password('yisao1234'), is_active=True)
code, d = req('post', '/auth/login', data={'phone': OWNER_PH, 'password': 'yisao1234'})
AT = d.get('access', '')
check('1.1 管理员登录', code == 200 and AT, f'{code}')

code, nc = req('post', '/companies/create', AT, data={'name': COMPANY_NAME, 'city': '汕头'})
CID = nc.get('company', {}).get('id')
check('1.2 建对拍厂', code == 201 and CID, f'{code} {str(nc)[:80]}')
code, d = req('post', f'/companies/{CID}/switch', AT, CID, {})
check('1.3 切换到对拍厂', code == 200, f'{code}')

# ============================================================
# 2. 档案：工序/款式/客户（款式价自动汇总对拍）
# ============================================================
PA = req('post', '/processes', AT, CID, {'name': f'{MARK}裁剪', 'price': '2.500'})[1]['id']
PB = req('post', '/processes', AT, CID, {'name': f'{MARK}车缝', 'price': '3.000'})[1]['id']
PC = req('post', '/processes', AT, CID, {'name': f'{MARK}锁边', 'price': '1.000'})[1]['id']
code, d = req('post', '/customers', AT, CID,
              {'customer_no': 'DP-C01', 'name': f'{MARK}客户', 'phone': '13900000001'})
CUS = d.get('id')
check('2.1 建客户', code == 201 and CUS, f'{code}')

code, st = req('post', '/styles', AT, CID, {
    'style_no': 'DP001', 'name': f'{MARK}款', 'customer_id': CUS,
    'process_items': [
        {'process_id': PA, 'price': 2.5, 'sort': 1},
        {'process_id': PB, 'price': 3.0, 'sort': 2},
        {'process_id': PC, 'price': 1.0, 'sort': 3, 'allow_skip': False}]})
SID = st.get('id')
code2, sd = req('get', f'/styles/{SID}', AT, CID)
check('2.2 款式价=工序价自动汇总6.5', code == 201 and SID and eq(sd.get('price'), 6.5),
      f'{code} price={sd.get("price")}')
# 款式工序价（登记计价口径来源）
SP = {}
for p in sd.get('processes') or sd.get('process_items') or []:
    pr = p.get('process')
    pid = pr.get('id') if isinstance(pr, dict) else (p.get('process_id') or pr)
    if pid is not None:
        SP[pid] = float(p.get('price') or 0)
check('2.3 款式工序价 2.5/3.0/1.0',
      eq(SP.get(PA), 2.5) and eq(SP.get(PB), 3.0) and eq(SP.get(PC), 1.0),
      f'SP={SP} raw={str(sd.get("process_items"))[:200]}')

# ============================================================
# 3. BOM：物料+定额（损耗默认 梭织6%/辅料0.4%）
# ============================================================
code, m1 = req('post', '/tech/materials', AT, CID, {
    'code': 'DP-M1', 'name': f'{MARK}面料', 'category': 'fabric', 'unit': '米',
    'weight_gsm': 200, 'width_cm': 150})
M1 = m1.get('id')
code, m2 = req('post', '/tech/materials', AT, CID, {
    'code': 'DP-M2', 'name': f'{MARK}辅料', 'category': 'accessory', 'unit': '个'})
M2 = m2.get('id')
check('3.1 建物料', code == 201 and M1 and m2.get('id'), f'{code}')

code, bom = req('post', '/tech/boms', AT, CID, {
    'style': SID,
    'items': [
        {'material': M1, 'kind': 'main', 'unit_usage': 1.2},
        {'material': M2, 'kind': 'accessory', 'unit_usage': 1}]})
BID = bom.get('id')
bitems = bom.get('items', [])
check('3.2 建BOM 默认损耗 6%/0.4%',
      code == 201 and BID and len(bitems) == 2
      and eq(bitems[0]['loss_rate'], 6) and eq(bitems[1]['loss_rate'], 0.4), f'{code} {str(bom)[:120]}')
code, d = req('post', f'/tech/boms/{BID}/activate', AT, CID)
check('3.3 BOM激活', code == 200 and d.get('status') == 'active', f'{code} {str(d)[:60]}')
BMI1 = bitems[0]['id'] if bitems else None
BMI2 = bitems[1]['id'] if bitems else None
# 毛耗对拍：gross_usage = 1.2×1.06 / 1×1.004
gi = {it['id']: it for it in bitems}
check('3.4 毛耗 1.272/1.004',
      eq(gi.get(BMI1, {}).get('gross_usage', gi.get(BMI1, {}).get('unit_usage')), 1.272, 0.001)
      or eq(float(gi.get(BMI1, {}).get('unit_usage', 0)) * 1.06, 1.272, 0.001),
      str(gi.get(BMI1))[:80])

# ============================================================
# 4. 报价：labor_base 快照=款式工序合计；材料=单耗×200×20
# ============================================================
code, q = req('post', '/quotation/quotations', AT, CID, {
    'style': SID, 'qty': 200,
    'materials': [{'bom_item': BMI1, 'quote_unit_usage': 1.2, 'price': 20}]})
check('4.1 报价材料费=1.2×200×20=4800', code == 201 and eq(q.get('material_total'), 4800),
      f'{code} {str(q)[:120]}')
lb = q.get('labor', {})
check('4.2 报价 labor_base=款式工序合计6.5', eq(lb.get('labor_base'), 6.5), str(lb)[:100])
check('4.3 小批量加成 qty<300 自动+30%', eq(lb.get('small_lot_addon'), 30)
      and eq(lb.get('labor_total'), 6.5 * 2.3 * 1.3, 0.02), str(lb)[:120])
check('4.4 报价总额=材料+人工', eq(q.get('total_price'), 4800 + float(lb.get('labor_total', 0)), 0.02),
      f'total={q.get("total_price")}')

# ============================================================
# 5. 订单：3色码带价 → revenue 5920
# ============================================================
code, o = req('post', '/orders', AT, CID, {
    'style': SID, 'customer': CUS,
    'process_items': [{'process_id': PA, 'price': 2.5}, {'process_id': PB, 'price': 3.0},
                      {'process_id': PC, 'price': 1.0}],
    'items': [{'color': '黑', 'size': 'M', 'qty': 100, 'price': 30},
              {'color': '黑', 'size': 'L', 'qty': 60, 'price': 30},
              {'color': '白', 'size': 'M', 'qty': 40, 'price': 28}]})
OID = o.get('id')
ONO = o.get('order_no', '')
code2, od = req('get', f'/orders/{OID}', AT, CID)
check('5.1 建订单 total=200 单号SO格式', code == 201 and eq(od.get('total_qty'), 200)
      and re.fullmatch(r'SO\d{8}\d{3}', ONO or ''), f'{code} {str(o)[:100]}')
code, d = req('get', f'/orders/{OID}', AT, CID)
check('5.2 订单回读三项价格', all(eq(it.get('price'), p) for it, p in
      zip(d.get('items', []), (30, 30, 28))), str(d.get('items'))[:100])

# ============================================================
# 6. 挂单建床+编菲：200件 30/扎 → 8扎 Σ=200
# ============================================================
code, prod = req('post', '/productions', AT, CID,
                 {'bed_no': 'DPC1', 'order_id': OID, 'total_qty': 200})
PID = prod.get('id')
check('6.1 挂单建床(有active BOM放行)', code == 201 and PID, f'{code} {str(prod)[:80]}')
code, d = req('get', f'/productions/{PID}', AT, CID)
check('6.2 床次继承订单工序3道', code == 200
      and len(d.get('order_processes', d.get('process_items', []))) == 3,
      f'{code} {str(d)[:160]}')
code, tks = req('post', '/tickets/batch', AT, CID, {
    'production_id': PID, 'bundle_limit': 30,
    'items': [{'color': '黑', 'size': 'M', 'qty': 100},
              {'color': '黑', 'size': 'L', 'qty': 60},
              {'color': '白', 'size': 'M', 'qty': 40}]})
tk_list = tks if isinstance(tks, list) else tks.get('results', [])
check('6.3 编菲8扎 Σqty=200', code == 201 and len(tk_list) == 8
      and sum(t['qty'] for t in tk_list) == 200, f'{code} n={len(tk_list)}')
check('6.4 扎号床内连续1..8 且全带qr',
      [t['bundle_no'] for t in tk_list] == [str(i) for i in range(1, 9)]
      and all(t.get('qr_code') for t in tk_list), str([t.get('bundle_no') for t in tk_list]))
# 拆扎数：黑M 30/30/30/10 黑L 30/30 白M 30/10
sizes = [(t['color'], t['size'], t['qty']) for t in tk_list]
check('6.5 拆扎口径 30/30/30/10+30/30+30/10',
      sizes == [('黑', 'M', 30), ('黑', 'M', 30), ('黑', 'M', 30), ('黑', 'M', 10),
                ('黑', 'L', 30), ('黑', 'L', 30), ('白', 'M', 30), ('白', 'M', 10)], str(sizes))
code, d = req('get', f'/orders/{OID}/progress', AT, CID)
check('6.6 订单进度 cut_qty=200 registered=0',
      eq(d.get('cut_qty'), 200) and eq(d.get('registered_qty'), 0) and eq(d.get('total_qty'), 200),
      str(d)[:120])
TK = tk_list  # t1..t8
R1, R8 = TK[0]['id'], TK[7]['id']

# ============================================================
# 7. 库存：期初导入 + 采购入库（88元/kg→26.4元/m 折算对拍）
# ============================================================
code, d = req('post', '/opening-stock/import', AT, CID, {'rows': [
    {'物料编码': 'DP-M1', '缸号': 'DPLOT1', '数量': 500, '批次单价': 20},
    {'物料编码': 'DP-M2', '缸号': 'DPLOT2', '数量': 300, '批次单价': 2}]})
check('7.1 期初导入 created=2', code == 200 and d.get('created') == 2, f'{code} {str(d)[:80]}')

code, po = req('post', '/purchase-orders', AT, CID, {'items': [
    {'fabric_name': f'{MARK}面料', 'qty': 300, 'unit': 'kg', 'price': 88,
     'price_unit': 'kg', 'material_id': M1}]})
POI = (po.get('items') or [{}])[0]
check('7.2 采购88元/kg折算26.4元/m', code == 201 and eq(POI.get('price'), 26.4),
      f'{code} price={POI.get("price")}')
code, si = req('post', '/stock-ins', AT, CID, {
    'source_type': 'purchase', 'purchase': po.get('id'),
    'items': [{'fabric_name': f'{MARK}面料', 'lot_no': 'DPLOT3', 'qty': 1000,
               'unit': '米', 'unit_price': 26.4, 'purchase_item': POI.get('id'),
               'material_id': M1}]})
SIID = si.get('id')
check('7.3 采购入库1000米', code == 201 and SIID, f'{code} {str(si)[:80]}')
code, d = req('post', f'/stock-ins/{SIID}/confirm', AT, CID)
check('7.4 入库确认', code == 200, f'{code}')

lots = {l['lot_no']: l for l in Lot.all_objects.filter(company_id=CID).values(
    'id', 'lot_no', 'qty', 'unit_cost')}
L1, L2, L3 = lots.get('DPLOT1'), lots.get('DPLOT2'), lots.get('DPLOT3')
check('7.5 批次快照 500@20 300@2 1000@26.4',
      L1 and L2 and L3 and eq(L1['qty'], 500) and eq(L1['unit_cost'], 20)
      and eq(L2['unit_cost'], 2) and eq(L3['qty'], 1000) and eq(L3['unit_cost'], 26.4),
      str(lots)[:160])

# ============================================================
# 8. 领料：定额=BOM毛耗×床次件数；超领放行留痕；确认冲库存
# ============================================================
code, iss1 = req('post', '/material-issues', AT, CID, {'items': [
    {'production_id': PID, 'lot_id': L1['id'], 'qty': 264.4, 'bom_item_id': BMI1}]})
i1 = (iss1.get('items') or [{}])[0]
check('8.1 领料定额=1.2×1.06×200=254.4', code == 201 and eq(i1.get('qty_req'), 254.4, 0.01),
      f'{code} req={i1.get("qty_req")}')
check('8.2 超领10米 over=True 不拦截',
      i1.get('over') is True and i1.get('over_state') == 'over', str(i1)[:300])
code, iss2 = req('post', '/material-issues', AT, CID, {'items': [
    {'production_id': PID, 'lot_id': L2['id'], 'qty': 200.8, 'bom_item_id': BMI2}]})
i2 = (iss2.get('items') or [{}])[0]
check('8.3 辅料定额吻合 1×1.004×200=200.8 over=False',
      code == 201 and eq(i2.get('qty_req'), 200.8, 0.01) and i2.get('over') is False,
      f'{code} {str(i2)[:100]}')
code, d = req('post', f'/material-issues/{iss1["id"]}/confirm', AT, CID)
code2, d2 = req('post', f'/material-issues/{iss2["id"]}/confirm', AT, CID)
check('8.4 两单确认+写血缘', code == 200 and code2 == 200
      and ProductionLotLink.all_objects.filter(production_id=PID).count() == 2,
      f'{code}/{code2} links={ProductionLotLink.all_objects.filter(production_id=PID).count()}')
# 库存余额口径：Lot.qty 为入库量快照不冲减，结存由估值报表流水递推
code, val = req('get', '/finance/stock-valuation', AT, CID)
vrows = {r.get('material_id'): r for r in val.get('rows', [])}
check('8.5 估值结存 M1=1235.6 M2=99.2',
      eq(vrows.get(M1, {}).get('balance_qty'), 1235.6, 0.02)
      and eq(vrows.get(M2, {}).get('balance_qty'), 99.2, 0.02),
      str(vrows)[:160])
code, d = req('get', '/issues/divergence', AT, CID, {'production_id': PID})
rows = d.get('rows', [])
m1row = next((r for r in rows if r.get('material_id') == M1), {})
m2row = next((r for r in rows if r.get('material_id') == M2), {})
check('8.6 差异报表 M1超领10/M2吻合',
      eq(m1row.get('over_qty'), 10, 0.01) and m1row.get('over') is True
      and eq(m1row.get('qty_net'), 264.4, 0.01) and m2row.get('over') is False,
      str(rows)[:160])

# ============================================================
# 9. 扫菲登记：W1 扎1-3全工序 W2 扎4-7(扎7缺锁边) → 540件/1205元
# ============================================================
for ph in (W1_PH, W2_PH):
    req('post', '/employees/invite', AT, CID, {'phone': ph, 'real_name': f'{MARK}工{ph[-2:]}'})
pend = req('get', '/employees?tab=pending', AT, CID)[1]
pend = pend.get('results') if isinstance(pend, dict) else pend
uc_by_phone = {}
for e in (pend or []):
    ph = (e.get('user') or {}).get('phone') or e.get('phone')
    uc_by_phone[ph] = e['id']
for ph in (W1_PH, W2_PH):
    req('post', f'/employees/{uc_by_phone[ph]}/approve', AT, CID)
code, post = req('post', '/posts', AT, CID, {'name': f'{MARK}工岗', 'permissions': ['production']})
for ph in (W1_PH, W2_PH):
    req('post', f'/employees/{uc_by_phone[ph]}/set-post', AT, CID, {'post_id': post.get('id')})


def emp_token(ph):
    req('post', '/auth/sms-code', data={'phone': ph})
    row = SMSCode.objects.filter(phone=ph).order_by('-id').first()
    _, d = req('post', '/auth/login-sms', data={'phone': ph, 'code': row.code if row else '0'})
    return d.get('access', ''), d.get('user', {}).get('id')


ET1, W1 = emp_token(W1_PH)
ET2, W2 = emp_token(W2_PH)
check('9.1 双员工就绪', bool(ET1 and W1 and ET2 and W2), f'{W1}/{W2}')

code, d = req('post', '/scan/parse', AT, CID, {'qr': TK[0]['qr_code']})
check('9.2 扫码解析出床次/款号', code == 200 and d.get('ticket', {}).get('bed_no') == 'DPC1'
      and d.get('style', {}).get('style_no') == 'DP001', f'{code} {str(d)[:80]}')


def reg(tk, proc, qty, uid, token=None):
    code, r = req('post', '/records', token or AT, CID,
                  {'ticket_id': tk, 'process_id': proc, 'qty': qty, 'user_id': uid})
    print(f'  [reg] tk={tk} proc={proc} qty={qty} user={uid} -> {code} {str(r)[:90]}')
    return code, r


rec_ids = {}
W1_AMT, W2_AMT = 0.0, 0.0
# 扎1: 车缝 12→18 增量补登同一行（对拍 id 不变）
code, r = reg(TK[0]['id'], PB, 12, W1)
first_id = r.get('id')
code, r = reg(TK[0]['id'], PB, 18, W1)
check('9.3 同票同工序同人增量补登同一行', code == 201 and r.get('id') == first_id
      and eq(r.get('qty'), 30), f'{code} {str(r)[:80]}')
rec_ids[(0, PB)] = first_id
code, r = reg(TK[0]['id'], PB, 5, W1)
check('9.4 满扎30后再登→400', code == 400, f'{code} {str(r)[:60]}')
rec_ids[(0, PB)] = first_id
W1_AMT += 30 * 3.0
# 扎1 剩余工序 + 扎2/3 全工序（W1）
for i, tk in enumerate(TK[:3]):
    for proc, pr in ((PA, 2.5), (PB, 3.0), (PC, 1.0)):
        if (i, proc) in rec_ids:
            continue
        code, r = reg(tk['id'], proc, 30, W1)
        if code == 201:
            rec_ids[(i, proc)] = r['id']
            W1_AMT += 30 * pr
        else:
            check(f'9.x 登记扎{i+1}工序{proc}', False, f'{code} {str(r)[:60]}')
# 扎4/5/6 全工序 W2；扎7 裁+车 W2
for i, tk in enumerate(TK[3:7], start=3):
    procs = ((PA, 2.5), (PB, 3.0), (PC, 1.0)) if i < 6 else ((PA, 2.5), (PB, 3.0))
    for proc, pr in procs:
        code, r = reg(tk['id'], proc, tk['qty'], W2)
        if code == 201:
            rec_ids[(i, proc)] = r['id']
            W2_AMT += tk['qty'] * pr
        else:
            check(f'9.x 登记扎{i+1}工序{proc}', False, f'{code} {str(r)[:60]}')
check('9.5 登记金额推演 585/620', eq(W1_AMT, 585) and eq(W2_AMT, 620), f'{W1_AMT}/{W2_AMT}')
# 台账回显：库内每扎每工序实登合计（对拍 9.5 推演）
from django.db.models import Sum as _DSum  # noqa: E402
_pname = {p.id: p.name for p in Process.all_objects.filter(company_id=CID)}
_ledger = {}
for _r in (Record.all_objects.filter(company_id=CID)
           .values('ticket_id', 'process_id').annotate(s=_DSum('qty'))):
    _tk = next(t for t in TK if t['id'] == _r['ticket_id'])
    _ledger[(_tk['bundle_no'], _pname.get(_r['process_id']))] = int(_r['s'])
print('  [台账]', sorted(_ledger.items()))
check('9.5b 台账Σ=540', sum(_ledger.values()) == 540, str(sorted(_ledger.items())))
# 计价口径：登记价=款式工序价（非工序档案价也相同，此处数值一致）
sample = Record.all_objects.get(pk=rec_ids[(0, PA)])
check('9.6 登记计价取款式工序价2.5', eq(sample.price, 2.5), f'{sample.price}')
code, d = req('get', '/records/workbench', ET1, CID)
check('9.7 工作台今日金额W1=585.00', eq(d.get('my_today_amount'), 585.0),
      f'{d.get("my_today_amount")}')
code, d = req('get', f'/salary/employee/{W1}', AT, CID)
check('9.8 W1未结=585', eq(d.get('unsettled'), 585.0), f'{d.get("unsettled")}')
code, d = req('get', f'/salary/employee/{W2}', AT, CID)
check('9.9 W2未结=620', eq(d.get('unsettled'), 620.0), f'{d.get("unsettled")}')
code, d = req('get', f'/orders/{OID}/progress', AT, CID)
check('9.10 订单进度 registered=540',
      eq(d.get('registered_qty'), 540) and eq(d.get('cut_qty'), 200),
      str({k: d.get(k) for k in ('registered_qty', 'cut_qty', 'progress')})[:120])
itmap = {(it['color'], it['size']): it for it in d.get('items', [])}
check('9.11 分色码登记量 300/180/60',
      eq(itmap.get(('黑', 'M'), {}).get('registered_qty'), 300)
      and eq(itmap.get(('黑', 'L'), {}).get('registered_qty'), 180)
      and eq(itmap.get(('白', 'M'), {}).get('registered_qty'), 60), str(itmap)[:160])

# ============================================================
# 10. 成本：材料5689.6 人工1205 收入5920 制费100 → 6994.6 + stale 钩子
# ============================================================
code, d = req('post', f'/cost/orders/{OID}/recompute', AT, CID)
check('10.1 重算成功', code == 200, f'{code} {str(d)[:60]}')


def cost_fields():
    code, d = req('get', f'/cost/orders/{OID}', AT, CID)
    if code == 200 and isinstance(d, dict) and 'material_cost' in d:
        return d
    oc = OrderCost.all_objects.filter(company_id=CID, order_id=OID).first()
    if not oc:
        return {}
    return {'material_cost': oc.material_cost, 'labor_cost': oc.labor_cost,
            'overhead_cost': oc.overhead_cost, 'total_cost': oc.total_cost,
            'revenue': oc.revenue, 'is_stale': oc.is_stale, 'status': oc.status}


cf = cost_fields()
check('10.2 材料费=264.4×20+200.8×2=5689.6', eq(cf.get('material_cost'), 5689.6, 0.02),
      f'{cf.get("material_cost")}')
check('10.3 人工费=Σ登记=1205', eq(cf.get('labor_cost'), 1205, 0.02), f'{cf.get("labor_cost")}')
check('10.4 收入=订单明细Σ=5920', eq(cf.get('revenue'), 5920), f'{cf.get("revenue")}')
check('10.5 重算后不stale', cf.get('is_stale') in (False, 'False'), f'{cf.get("is_stale")}')
code, d = req('put', f'/cost/orders/{OID}/overhead', AT, CID, {'overhead_cost': 100})
check('10.6 制费100落账', code == 200 and eq(d.get('overhead_cost'), 100), f'{code} {str(d)[:60]}')
req('post', f'/cost/orders/{OID}/recompute', AT, CID)
cf = cost_fields()
check('10.7 总成本=5689.6+1205+100=6994.6', eq(cf.get('total_cost'), 6994.6, 0.02),
      f'{cf.get("total_cost")}')
code, d = req('get', '/cost/wage-reconciliation', AT, CID,
              {'date_from': TODAY, 'date_to': TODAY})
check('10.8 工资对账 wage_total=1205', eq(d.get('wage_total'), 1205, 0.02),
      f'{d.get("wage_total")} rows={str(d.get("rows"))[:100]}')
check('10.9 对账已发=0（结算前）', eq(d.get('paid_total'), 0), f'{d.get("paid_total")}')

# ============================================================
# 11. 出货→应收：只发已完成末道报工的色码，3600 核销1000+2600→settled 撤销→partial
# ============================================================
code, sh = req('post', '/shipments', AT, CID, {
    'order': OID, 'customer': CUS,
    'items': [{'color': '黑', 'size': 'M', 'qty': 90},
              {'color': '黑', 'size': 'L', 'qty': 30}]})
SHID = sh.get('id')
check('11.1 建出货 total=120', code == 201 and eq(sh.get('total_qty'), 120), f'{code} {str(sh)[:80]}')
code, d = req('post', f'/shipments/{SHID}/audit', AT, CID)
ar = d.get('receivable', {})
check('11.2 审核出应收 3600=Σqty×订单价',
      code == 200 and eq(ar.get('amount'), 3600) and ar.get('status') == 'unpaid',
      f'{code} {str(d)[:140]}')
check('11.3 部分出货 status=partial', d.get('shipment', {}).get('status') == 'partial',
      str(d.get('shipment'))[:60])
code, d = req('get', f'/shipments/{SHID}/undelivered', AT, CID)
ud = {(it['color'], it['size']): it.get('undelivered_qty') for it in d.get('items', [])}
check('11.4 未交矩阵 10/30/40 总80',
      eq(ud.get(('黑', 'M')), 10) and eq(ud.get(('黑', 'L')), 30)
      and eq(ud.get(('白', 'M')), 40) and eq(d.get('total_undelivered'), 80), str(ud))
ARID = ar.get('id')
code, d = req('post', f'/finance/receivables/{ARID}/receipts', AT, CID,
              {'amount': 1000, 'method': 'cash'})
check('11.5 核销1000→partial 余2600',
      code == 201 and d.get('receivable', {}).get('status') == 'partial'
      and eq(d.get('receivable', {}).get('outstanding'), 2600), f'{code} {str(d)[:100]}')
code, d = req('post', f'/finance/receivables/{ARID}/receipts', AT, CID,
              {'amount': 2600, 'method': 'other'})
check('11.6 核销2600→settled 余0',
      code == 201 and d.get('receivable', {}).get('status') == 'settled'
      and eq(d.get('receivable', {}).get('outstanding'), 0), f'{code} {str(d)[:100]}')
code, d = req('post', f'/finance/receivables/{ARID}/receipts/revoke', AT, CID)
check('11.7 撤销最近一笔→回partial',
      code == 200 and d.get('receivable', {}).get('status') == 'partial'
      and eq(d.get('receivable', {}).get('received_amount'), 1000), f'{code} {str(d)[:80]}')
req('post', f'/finance/receivables/{ARID}/receipts', AT, CID, {'amount': 2600, 'method': 'other'})

# ============================================================
# 12. 结算：W1 结585 → 锁定；W2 调整-10 只留痕，未结仍为620
# ============================================================
code, d = req('post', '/salary/settlements', AT, CID,
              {'period_start': TODAY, 'period_end': TODAY, 'user_id': W1})
STL = d.get('id')
check('12.1 结算W1 amount=585 快照', code == 201 and eq(d.get('amount'), 585)
      and str(W1) in str(d.get('wage_detail')), f'{code} {str(d)[:100]}')
code, d = req('get', f'/salary/employee/{W1}', AT, CID)
check('12.2 W1 settled=585 unsettled=0',
      eq(d.get('settled'), 585) and eq(d.get('unsettled'), 0), str(d)[:80])
code, d = req('delete', f'/records/{rec_ids[(0, PA)]}', AT, CID)
check('12.3 已结算登记禁撤(400)', code == 400, f'{code} {str(d)[:60]}')
code, d = req('post', '/salary/adjust', AT, CID,
              {'user_id': W2, 'amount': '-10', 'reason': f'{MARK}扣款'})
check('12.4 工资调整-10', code == 201, f'{code}')
code, d = req('get', '/salary/company/employees', AT, CID)
w2row = next((r for r in (d if isinstance(d, list) else d.get('results', []))
              if r.get('user_id') == W2), {})
check('12.5 员工卡W2未结=620（调整不冲未结）', eq(w2row.get('unsettled'), 620), str(w2row)[:400])
code, d = req('get', '/salary/adjust', AT, CID, {'user_id': W2})
adj_rows = d if isinstance(d, list) else d.get('results', [])
check('12.5b 调整台账留痕-10', any(eq(r.get('amount'), -10) for r in adj_rows), str(adj_rows)[:100])
code, d = req('get', '/cost/wage-reconciliation', AT, CID, {'date_from': TODAY, 'date_to': TODAY})
check('12.6 对账已发=585（结算后）', eq(d.get('paid_total'), 585), f'{d.get("paid_total")}')

# ============================================================
# 13. 看板：summary/工序/rank/dim 五维对拍
# ============================================================
code, db = req('get', '/dashboard', AT, CID,
               {'range': 'custom', 'start_date': TODAY, 'end_date': TODAY})
s = db.get('summary', {})
# done 口径（stats.done_working_qty）：distinct 工序数≥配置工序数的扎，其登记件数Σ
# → 扎1-6 全覆盖：90×6=540；扎7 只 2 道 → working 20
check('13.1 registered=540 done=480 working=60',
      eq(s.get('registered_qty'), 540) and eq(s.get('done_qty'), 480)
      and eq(s.get('working_qty'), 60), str(s)[:140])
check('13.2 cut_qty=200 ticket_count=7 user_count=2',
      eq(s.get('cut_qty'), 200) and s.get('ticket_count') == 7 and s.get('user_count') == 2,
      str(s)[:140])
pcs = {p['name']: p.get('qty') for p in db.get('processes', [])}
check('13.3 工序量 190/190/160',
      eq(pcs.get(f'{MARK}裁剪'), 190) and eq(pcs.get(f'{MARK}车缝'), 190)
      and eq(pcs.get(f'{MARK}锁边'), 160), str(pcs))
rank = {r.get('user_id'): r.get('qty') for r in db.get('rank', [])}
check('13.4 rank W1=W2=270（同量不同价）', eq(rank.get(W2), 270) and eq(rank.get(W1), 270), str(rank))
daily = db.get('daily', [])
todayrow = next((r for r in daily if str(r.get('date', '')).endswith(TODAY[5:])), None)
check('13.5 daily今日=540', todayrow and eq(todayrow.get('qty'), 540), str(daily)[-120:])


def col_idx(dd, *keys):
    for i, col in enumerate(dd.get('columns', [])):
        if col.get('key') in keys:
            return i
    return None


dimqtys, dimamts = {}, {}
for dim in ('order', 'bed', 'post', 'group', 'user'):
    code, d = req('get', '/dashboard/dim', AT, CID,
                  {'dim': dim, 'range': 'custom', 'start_date': TODAY, 'end_date': TODAY})
    rowsd = d.get('rows', [])
    qi = col_idx(d, 'qty', 'registered_qty', 'count', 'ticket_count')
    ai = col_idx(d, 'amount', 'total_amount')
    if qi is None or ai is None:
        check(f'13.x dim={dim} 列定义可解析', False, str(d.get('columns')))
        continue
    dimqtys[dim] = sum(float(r['cells'][qi]) for r in rowsd)
    dimamts[dim] = sum(float(r['cells'][ai]) for r in rowsd)
    if dim == 'order':
        check('13.6 dim=order 件数540 金额1205',
              eq(dimqtys['order'], 540) and eq(dimamts['order'], 1205),
              str(rowsd)[:160])
check('13.7 dim=user 金额合计1205', eq(dimamts.get('user'), 1205), str(dimamts))
check('13.8 五维件数合计一致=540',
      len(dimqtys) == 5 and all(eq(v, 540) for v in dimqtys.values()),
      str(dimqtys))
code, d = req('get', '/productions?page_size=50', AT, CID)
prow = next((r for r in (d.get('results') if isinstance(d, dict) else d) if r.get('id') == PID), {})
code2, wb = req('get', '/records/workbench', AT, CID)
wbrow = next((b for b in wb.get('beds', []) if b.get('id') == PID), {})
check('13.9 四消费方 registered_qty 一致=540',
      eq(prow.get('registered_qty'), 540) and eq(wbrow.get('registered_qty'), 540),
      f'list={prow.get("registered_qty")} wb={wbrow.get("registered_qty")}')
check('13.10 完扎数=6（扎8未登）', prow.get('done_ticket_count') == 6,
      f'{prow.get("done_ticket_count")}')
code, d = req('get', f'/productions/{PID}/bundles', AT, CID)
states = [r.get('state') for r in d.get('results', [])]
check('13.11 扎状态 6done+1partial+1none',
      states.count('done') == 6 and states.count('partial') == 1 and states.count('none') == 1,
      str(states))

# ============================================================
# 14. 审计链：关键动作全留痕
# ============================================================
code, d = req('get', '/audit-logs', AT, CID, {'date_from': TODAY, 'date_to': TODAY, 'page_size': 100})
msgs = json.dumps(d.get('results', []), ensure_ascii=False)
for key, name in (('订单制费修改', '14.1 制费修改留痕'), ('出货审核', '14.2 出货审核留痕'),
                  ('应收核销', '14.3 应收核销留痕'), ('结算工资', '14.4 结算留痕'),
                  ('工资调整', '14.5 工资调整留痕')):
    check(name, key in msgs, f'未找到[{key}]')
check('14.6 审计链≥5条', d.get('count', 0) >= 5, f'count={d.get("count")}')

# ============================================================
# 15. 收尾一致性：新增登记→成本stale→撤销→回算复原
# ============================================================
code, r = reg(TK[6]['id'], PC, 5, W2)   # 扎7 补锁边5件
NEWREC = r.get('id') if code == 201 else None
check('15.1 补登记成功', code == 201, f'{code}')
cf = cost_fields()
check('15.2 登记即翻stale', cf.get('is_stale') in (True, 'True'), f'{cf.get("is_stale")}')
code, d = req('delete', f'/records/{NEWREC}', AT, CID)
check('15.3 撤销未结登记OK', code == 204 and not d, f'{code} {str(d)[:60]}')
code, d = req('get', '/audit-logs', AT, CID,
              {'date_from': TODAY, 'date_to': TODAY, 'type': 'revoke'})
check('15.4 撤销登记落审计(type=revoke)', d.get('count', 0) >= 1, f'count={d.get("count")}')
req('post', f'/cost/orders/{OID}/recompute', AT, CID)
cf = cost_fields()
check('15.5 回算后与推演一致(6994.6/1205)且不stale',
      eq(cf.get('total_cost'), 6994.6, 0.02) and eq(cf.get('labor_cost'), 1205, 0.02)
      and cf.get('is_stale') in (False, 'False'), str(cf)[:140])
code, d = req('get', f'/salary/employee/{W2}', AT, CID)
check('15.6 W2未结回到620（补登又撤销）', eq(d.get('unsettled'), 620), f'{d.get("unsettled")}')

# ============================================================
# 清理：独立 shell 跑法硬删专用厂；隔离跑法由 Django 销毁空白测试库。
# ============================================================
if ISOLATED_TEST_RUN:
    left = Company.all_objects.filter(name__contains=COMPANY_NAME).count()
    check('99.1 隔离测试库保留单一对拍厂供最终核验', left == 1, f'left={left}')
else:
    purge_company(CID)
    for ph in (OWNER_PH, W1_PH, W2_PH):
        SMSCode.objects.filter(phone=ph).delete()
        u = User.objects.filter(phone=ph).first()
        if u:
            UserCompany.all_objects.filter(user=u)._raw_delete(UserCompany.all_objects.db)
            u.delete()
    left = Company.all_objects.filter(name__contains=COMPANY_NAME).count()
    check('99.1 清理后无残留厂', left == 0, f'left={left}')

print()
print(f'===== 一致性对拍汇总: PASS {len(PASS)} / FAIL {len(FAIL)} =====')
if FAIL:
    print('FAILED:')
    for f_ in FAIL:
        print(' -', f_)
    raise SystemExit(1)
