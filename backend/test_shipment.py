# -*- coding: utf-8 -*-
"""
出货域（test_shipment）端到端后端验证 —— SA-P2-4 交付（2026-10-24 P2 出货域）。

覆盖 P2 出货域全部端点与口径（docs/服装ERP全模块设计方案.md §4.2 + P1设计决策表 D1/D3 + §3.3 未交量真口径）：
  权限  S-PERM-01~08（401/403/2 段式/effective_perms 下发/legacy 零回归/print.manage 交叉）
  建单  S-ADD-01~08（自动号 SH 顺延/客户必传/跨厂 400/明细校验/显式单号查重）
  列表  S-LIST-01~03（status/order_id/customer_id/keyword 过滤）
  改删  S-EDIT-01~03（草稿全量改/已审核仅物流等/草稿删）
  审核  S-AUD-01~08（自动建应收金额/部分出货 partial/负库存 400/未交量超量 400/
                     缺价补价 400/confirmed 全部/重复审核 400/色码不在订单/audit 留痕）
  应收  S-AR-01~03（共用入口 create_receivable/shipment 真 FK 回填/手工应收不受影响）
  矩阵  S-UND-01（未交量真口径矩阵 qty-已出，作废回池）
  作废  S-VOID-01~04（warning 应收联动提示/不自动撤应收/作废回未交池/草稿作废/再作废 404）
  装箱  S-BOX-01~03（装箱行增删/boxes 汇总）
  打印  S-PRT-01~07（print_key 幂等/补打 is_reprint/历史/模板校验/print.manage 交叉/403）
  隔离  S-ISO-01~02（跨厂列表不可见/跨厂详情 404）

跑法：manage.py shell -c "exec(compile(open('test_shipment.py',encoding='utf-8').read(),'test_shipment.py','exec'))"
幂等：自建「出货测试厂(可删)/出货B厂(可删)」并跨次复用（Company.creator PROTECT
不可删）；每轮 preclean 硬清本厂业务行，不依赖 DB 现状；
绝不触碰真实厂（company_id=86 等）。
"""
import json
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.hashers import make_password
from django.test import Client
from django.utils import timezone

from apps.accounts.models import Company, Post as PostM, User, UserCompany
from apps.accounts.services import create_company_with_owner
from apps.catalog.models import Customer, Process, Style, StyleProcess, Ticket
from apps.core.permissions import effective_perms
from apps.finance.models import Receivable
from apps.ops.models import AuditLog
from apps.orders.models import Order, OrderItem
from apps.production.models import Production
from apps.records.models import Record
from apps.shipment.models import (PackingBox, Shipment, ShipmentItem,
                                  ShipmentPrintLog)

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


OWNER, H_PLAIN, H_VIEW, H_PERM, H_LEGACY, H_OWNER_B, H_PRINT, H_WORKER = (
    '13988713101', '13988713102', '13988713103', '13988713104', '13988713105',
    '13988713106', '13988713107', '13988713108')
CO_A, CO_B = '出货测试厂(可删)', '出货B厂(可删)'


def preclean(company):
    """幂等自清扫：硬清本厂业务行（PROTECT 逆序：打印→装箱→明细→出货单→
    流水→应收→登记→菲票→血缘→床次→批次→订单→客户→款式→工序→日志）。"""
    ShipmentPrintLog.all_objects.filter(company=company).delete()
    PackingBox.all_objects.filter(shipment__company=company).delete()
    ShipmentItem.all_objects.filter(shipment__company=company).delete()
    Shipment.all_objects.filter(company=company).delete()
    from apps.finance.models import ReceiptLine
    ReceiptLine.all_objects.filter(company=company).delete()
    Receivable.all_objects.filter(company=company).delete()
    Record.all_objects.filter(company=company).delete()
    Ticket.all_objects.filter(company=company).delete()
    from apps.warehouse.models import ProductionLotLink
    ProductionLotLink.all_objects.filter(production__company=company)._raw_delete(
        ProductionLotLink.all_objects.db)
    Production.all_objects.filter(company=company).delete()
    OrderItem.all_objects.filter(order__company=company).delete()
    Order.all_objects.filter(company=company).delete()
    Customer.all_objects.filter(company=company).delete()
    Style.all_objects.filter(company=company).delete()
    Process.all_objects.filter(company=company).delete()
    AuditLog.objects.filter(company=company).delete()


# ================= 建厂（复用） + preclean =================
company = Company.objects.filter(name=CO_A).first()
if company is None:
    owner = ensure_user(OWNER, '出货厂长')
    company, admin_post = create_company_with_owner(owner, CO_A)
else:
    owner = ensure_user(OWNER, '出货厂长')
    admin_post = PostM.objects.get(company=company, is_admin=True, is_deleted=False)
company_b = Company.objects.filter(name=CO_B).first()
if company_b is None:
    owner_b = ensure_user(H_OWNER_B, '出货B厂长')
    company_b, _ = create_company_with_owner(owner_b, CO_B)
else:
    owner_b = ensure_user(H_OWNER_B, '出货B厂长')
CID, CID_B = company.id, company_b.id

preclean(company)
preclean(company_b)

post_plain = ensure_post(company, '出货普工', [])
post_view = ensure_post(company, '出货查看岗', ['shipment.view'])
post_perm = ensure_post(company, '出货管理岗', ['shipment.view', 'shipment.manage'])
post_legacy = ensure_post(company, '出货旧岗', ['production'])
post_print = ensure_post(company, '打印岗', ['print.manage'])
plain = ensure_user(H_PLAIN, '出货无权员')
view_u = ensure_user(H_VIEW, '出货查看员')
perm_u = ensure_user(H_PERM, '出货管理员')
legacy_u = ensure_user(H_LEGACY, '出货旧岗员')
print_u = ensure_user(H_PRINT, '打印员')
worker = ensure_user(H_WORKER, '计件员工')
ensure_member(plain, company, post_plain, group_no=0)
ensure_member(view_u, company, post_view, group_no=0)
ensure_member(perm_u, company, post_perm, group_no=0)
ensure_member(legacy_u, company, post_legacy, group_no=0)
ensure_member(print_u, company, post_print, group_no=0)

T_owner = login(OWNER)
T_plain = login(H_PLAIN)
T_view = login(H_VIEW)
T_perm = login(H_PERM)
T_legacy = login(H_LEGACY)
T_owner_b = login(H_OWNER_B)
T_print = login(H_PRINT)
today = timezone.localdate()
sh_prefix = f'SH{today:%Y%m%d}'

# ================= 数据工厂 =================
cust = Customer.objects.create(company=company, customer_no='TSH-C1', name='北方服饰')
cust_b = Customer.objects.create(company=company_b, customer_no='TSHB-C1', name='南方制衣')
style = Style.objects.create(company=company, style_no='TSH-S1', name='出货测试款')
proc = Process.objects.create(company=company, name='车缝', is_fixed=True)
StyleProcess.objects.create(style=style, process=proc, price=1, sort=1,
                            allow_skip=False)

# 订单1：红/M 100 件 @10、蓝/L 50 件 @5（部分出货与两道量纲校验主战场）
order1 = Order.objects.create(company=company, order_no='TSH-001', style=style,
                              customer=cust, delivery_date=today + timedelta(days=7))
oi_rm = OrderItem.objects.create(order=order1, color='红', size='M', qty=100,
                                 price=Decimal('10'))
oi_bl = OrderItem.objects.create(order=order1, color='蓝', size='L', qty=50,
                                 price=Decimal('5'))
# 订单2：绿/S 30 件 无价（补价 400 主战场）
order2 = Order.objects.create(company=company, order_no='TSH-002', style=style,
                              customer=cust)
OrderItem.objects.create(order=order2, color='绿', size='S', qty=30, price=None)
# 订单3：白/XS 20 件 @8（confirmed 全部出货主战场）
order3 = Order.objects.create(company=company, order_no='TSH-003', style=style,
                              customer=cust)
OrderItem.objects.create(order=order3, color='白', size='XS', qty=20,
                         price=Decimal('8'))


def make_bed(order, color_size_qty):
    """挂单床次 + 菲票 + 扫菲登记（成品完工结存 = 登记量，数据源现实口径）。"""
    bed = Production.objects.create(company=company, style=style,
                                    bed_no=f'B-{order.order_no}',
                                    total_qty=sum(q for _, _, q in color_size_qty),
                                    order=order, customer=cust)
    for i, (color, size, qty) in enumerate(color_size_qty, start=1):
        tk = Ticket.objects.create(
            company=company, production=bed, bed_no=bed.bed_no,
            bundle_no=str(i), color=color, size=size, qty=qty,
            qr_code=f'{bed.bed_no}|{i}')
        Record.objects.create(company=company, ticket=tk, process=proc,
                              user=worker, qty=qty)
    return bed


make_bed(order1, [('红', 'M', 60), ('蓝', 'L', 30)])   # 完工：红/M 60、蓝/L 30
make_bed(order2, [('绿', 'S', 30)])
make_bed(order3, [('白', 'XS', 20)])


def create_shipment(payload, token=T_perm, cid=CID):
    return api('POST', '/shipments', token, cid, payload)


# ================= 权限 =================
st, d = api('GET', '/shipments')
check('S-PERM-01 无 token 401', st == 401, f'{st}')
st, d = api('GET', '/shipments', T_plain, CID)
check('S-PERM-02 无权 GET 403', st == 403, f'{st}')
st, d = api('POST', '/shipments', T_plain, CID,
            {'order': order1.id, 'customer': cust.id, 'items': []})
check('S-PERM-02b 无权 POST 403', st == 403, f'{st}')
st, d = api('GET', '/shipments', T_view, CID)
check('S-PERM-03 view 岗 GET 200', st == 200, f'{st} {d}')
st, d = create_shipment({'order': order1.id, 'customer': cust.id, 'items': []}, T_view)
check('S-PERM-03b view 岗 POST 403', st == 403, f'{st}')
ep = effective_perms(post_perm)
check('S-PERM-04 出货管理岗 effective_perms 含双点',
      'shipment.view' in ep and 'shipment.manage' in ep, str(ep))
ep_l = effective_perms(post_legacy)
check('S-PERM-05 legacy 岗不含 shipment 点', 'shipment.view' not in ep_l
      and 'shipment.manage' not in ep_l, str(ep_l))
st, d = api('GET', '/shipments', T_legacy, CID)
check('S-PERM-06 legacy 岗 shipment 403（零回归：production 不覆盖 shipment）',
      st == 403, f'{st}')
st, d = api('GET', '/shipments', T_owner_b, CID_B)
check('S-PERM-07 B 厂主自己公司 GET 200（隔离上下文正常）', st == 200, f'{st}')

# ================= 建单 =================
st, d = create_shipment({
    'order': order1.id, 'customer': cust.id,
    'delivery_date': str(today + timedelta(days=7)),
    'items': [{'color': '红', 'size': 'M', 'qty': 40},
              {'color': '蓝', 'size': 'L', 'qty': 20}],
    'remark': '首批出货'})
check('S-ADD-01 建单 201（自动号 SH+日期+顺延/total_qty 汇总/明细内联）',
      st == 201 and d['shipment_no'].startswith(sh_prefix)
      and d['status'] == 'draft' and d['total_qty'] == 60
      and len(d['items']) == 2 and d['customer_name'] == '北方服饰'
      and d['order_no'] == 'TSH-001', f'{st} {d}')
shp1_no, shp1 = d['shipment_no'], d['id']
st, d = create_shipment({'order': order1.id, 'customer': cust.id,
                         'items': [{'color': '红', 'size': 'M', 'qty': 1}]})
check('S-ADD-02 单号当日顺延 +1',
      st == 201 and d['shipment_no'].endswith(f'{int(shp1_no[-3:]) + 1:03d}'),
      f'{st} {d}')
shp_tmp = d['id']
st, d = create_shipment({'order': order1.id,
                         'items': [{'color': '红', 'size': 'M', 'qty': 1}]})
check('S-ADD-03 客户缺失 400（出货单必挂客户）', st == 400, f'{st} {d}')
st, d = api('POST', '/shipments', T_owner_b, CID_B,
            {'order': order1.id, 'customer': cust_b.id,
             'items': [{'color': '红', 'size': 'M', 'qty': 1}]})
check('S-ADD-04 跨厂订单 → 400', st == 400, f'{st} {d}')
st, d = create_shipment({'order': order1.id, 'customer': cust_b.id,
                         'items': [{'color': '红', 'size': 'M', 'qty': 1}]})
check('S-ADD-05 跨厂客户 → 400', st == 400, f'{st} {d}')
st, d = create_shipment({'order': order1.id, 'customer': cust.id, 'items': []})
check('S-ADD-06a 明细空 → 400', st == 400, f'{st}')
st, d = create_shipment({'order': order1.id, 'customer': cust.id,
                         'items': [{'color': '红', 'size': 'M', 'qty': 0}]})
check('S-ADD-06b qty=0 → 400', st == 400, f'{st}')
st, d = create_shipment({'order': order1.id, 'customer': cust.id,
                         'items': [{'color': '红', 'size': 'M', 'qty': 1},
                                   {'color': '红', 'size': 'M', 'qty': 2}]})
check('S-ADD-06c 同单同色码重复 → 400', st == 400, f'{st}')
st, d = create_shipment({'order': order1.id, 'customer': cust.id,
                         'shipment_no': shp1_no,
                         'items': [{'color': '红', 'size': 'M', 'qty': 1}]})
check('S-ADD-07 显式单号重复 → 400', st == 400, f'{st}')
# 显式单号成功建（SH 前缀非数字尾缀 → gen 扫描 isdigit False 分支）
st, d = create_shipment({'order': order1.id, 'customer': cust.id,
                         'shipment_no': f'{sh_prefix}A1',
                         'items': [{'color': '红', 'size': 'M', 'qty': 1}]})
check('S-ADD-07b 显式单号成功建 201（非数字尾缀入号段扫描）',
      st == 201 and d['shipment_no'] == f'{sh_prefix}A1', f'{st} {d}')
shp_manual = d['id']
api('DELETE', f'/shipments/{shp_manual}', T_perm, CID)
# 明细形状：非 dict 行 / 缺色 / 缺尺码 / qty 非数
st, d = create_shipment({'order': order1.id, 'customer': cust.id, 'items': ['x']})
check('S-ADD-06d 明细行非 dict → 400', st == 400, f'{st}')
st, d = create_shipment({'order': order1.id, 'customer': cust.id,
                         'items': [{'size': 'M', 'qty': 1}]})
check('S-ADD-06e 缺颜色 → 400', st == 400, f'{st}')
st, d = create_shipment({'order': order1.id, 'customer': cust.id,
                         'items': [{'color': '红', 'qty': 1}]})
check('S-ADD-06f 缺尺码 → 400', st == 400, f'{st}')
st, d = create_shipment({'order': order1.id, 'customer': cust.id,
                         'items': [{'color': '红', 'size': 'M', 'qty': 'abc'}]})
check('S-ADD-06g qty 非数字 → 400', st == 400, f'{st}')
# 自动号撞号重试：gen 首轮回吐已有单号 → IntegrityError → 重取号成功
from apps.shipment import services as shp_svc
_real_gen = shp_svc.gen_shipment_no
_flaky = {'n': 0}
def _flaky_gen(company, seq_offset=0):
    _flaky['n'] += 1
    if _flaky['n'] == 1:
        return shp1_no          # 撞已存在单号
    return _real_gen(company, seq_offset=seq_offset)
shp_svc.gen_shipment_no = _flaky_gen
st, d = create_shipment({'order': order1.id, 'customer': cust.id,
                         'items': [{'color': '红', 'size': 'M', 'qty': 2}]})
shp_svc.gen_shipment_no = _real_gen
check('S-ADD-09 自动号撞号事务内重试成功 201（不与已存号重复）',
      st == 201 and d['shipment_no'] != shp1_no, f'{st} {d}')
api('DELETE', f"/shipments/{d.get('id')}", T_perm, CID)
# 自动号连续撞号耗尽重试 → 400 生成冲突（候选号 5 轮全撞）
shp_svc.gen_shipment_no = lambda company, seq_offset=0: shp1_no
st, d = create_shipment({'order': order1.id, 'customer': cust.id,
                         'items': [{'color': '红', 'size': 'M', 'qty': 2}]})
shp_svc.gen_shipment_no = _real_gen
check('S-ADD-10 自动号 5 轮撞号耗尽 → 400 生成冲突', st == 400
      and '生成冲突' in json.dumps(d, ensure_ascii=False), f'{st} {d}')
# 显式单号保存时撞 UNIQUE（绕过预查）→ except 内二次查重 400
from apps.shipment import views as shp_views
_real_chk = shp_views._check_shipment_no_free
shp_views._check_shipment_no_free = lambda company, no: None  # 预查放行
st, d = create_shipment({'order': order1.id, 'customer': cust.id,
                         'shipment_no': shp1_no,
                         'items': [{'color': '红', 'size': 'M', 'qty': 1}]})
shp_views._check_shipment_no_free = _real_chk
check('S-ADD-11 保存时撞号（预查被绕过）→ except 二次查重 400', st == 400, f'{st} {d}')
st, d = api('DELETE', f'/shipments/{shp_tmp}', T_perm, CID)
check('S-ADD-08 草稿删单 204（顺延号占位单清理）', st == 204, f'{st}')

# ================= 列表过滤 =================
st, d = api('GET', '/shipments?status=draft', T_view, CID)
check('S-LIST-01 status=draft 过滤',
      st == 200 and all(r['status'] == 'draft' for r in d['results'])
      and any(r['id'] == shp1 for r in d['results']), f'{st}')
st, d = api('GET', '/shipments?status=bogus', T_view, CID)
check('S-LIST-01b 非法 status 400', st == 400, f'{st}')
st, d = api('GET', f'/shipments?order_id={order1.id}', T_view, CID)
check('S-LIST-02a order_id 过滤', st == 200
      and all(r['order'] == order1.id for r in d['results'])
      and any(r['id'] == shp1 for r in d['results']), f'{st}')
st, d = api('GET', f'/shipments?customer_id={cust.id}', T_view, CID)
check('S-LIST-02b customer_id 过滤', st == 200
      and all(r['customer'] == cust.id for r in d['results']), f'{st}')
st, d = api('GET', f'/shipments?keyword={shp1_no}', T_view, CID)
check('S-LIST-03a keyword=单号', st == 200
      and any(r['id'] == shp1 for r in d['results']), f'{st}')
st, d = api('GET', '/shipments?keyword=北方', T_view, CID)
check('S-LIST-03b keyword=客户名', st == 200
      and any(r['id'] == shp1 for r in d['results']), f'{st}')

# ================= 隔离 =================
st, d = api('GET', f'/shipments/{shp1}', T_owner_b, CID_B)
check('S-ISO-01 跨厂详情 404', st == 404, f'{st}')
st, d = api('GET', '/shipments', T_owner_b, CID_B)
check('S-ISO-02 跨厂列表不可见', st == 200
      and all(r['id'] != shp1 for r in d['results']), f'{st}')

# ================= 审核：三道校验 + 自动建应收 =================
st, d = api('POST', f'/shipments/{shp1}/audit', T_perm, CID)
check('S-AUD-01 审核 200：自动建应收 500 元（40×10+20×5）/status partial/响应双块',
      st == 200 and d['shipment']['status'] == 'partial'
      and d['shipment']['total_qty'] == 60
      and d['receivable']['amount'] == '500.00'
      and d['receivable']['status'] == 'unpaid'
      and d['receivable']['receivable_no'].startswith('AR')
      and d['receivable']['shipment_id'] == shp1, f'{st} {d}')
ar_no, ar_id = d['receivable']['receivable_no'], d['receivable']['id']
rec_row = Receivable.objects.get(pk=ar_id)
check('S-AR-01 应收 shipment 真 FK 回填（共用入口 create_receivable）',
      rec_row.shipment_id == shp1 and rec_row.customer_id == cust.id
      and rec_row.order_id == order1.id, f'{rec_row.shipment_id}')
shp_row = Shipment.objects.get(pk=shp1)
check('S-AUD-01b 审核人/时间落库 + partial 判定（订单仍有未交 90）',
      shp_row.status == Shipment.Status.PARTIAL
      and shp_row.audited_by_id is not None
      and shp_row.audited_at is not None, f'{shp_row.status}')

st, d = api('POST', f'/shipments/{shp1}/audit', T_perm, CID)
check('S-AUD-06 重复审核 400', st == 400, f'{st} {d}')

# 负库存拦截：完工 红/M=60、已出 40 → 本单 50（未交 90 OK）但完工不够 → 400
st, d = create_shipment({'order': order1.id, 'customer': cust.id,
                         'items': [{'color': '红', 'size': 'M', 'qty': 50}]})
shp_neg = d.get('id')
st, d = api('POST', f'/shipments/{shp_neg}/audit', T_perm, CID)
check('S-AUD-02 成品负库存 400（完工 60-已出 40-本单 50<0）',
      st == 400 and '成品库存不足' in json.dumps(d, ensure_ascii=False), f'{st} {d}')
# 未交量超量拦截：已出 40 + 本单 61 > 100 → 400（未交量真口径）
st, d = create_shipment({'order': order1.id, 'customer': cust.id,
                         'items': [{'color': '红', 'size': 'M', 'qty': 61}]})
shp_over = d.get('id')
st, d = api('POST', f'/shipments/{shp_over}/audit', T_perm, CID)
check('S-AUD-03 未交量超量 400（已出 40+61>订单 100）',
      st == 400 and '超出未交量' in json.dumps(d, ensure_ascii=False), f'{st} {d}')
# 缺价补价拦截：order2 绿/S 未定价 → 400 提示补价（衔接 P1 缺价口径）
st, d = create_shipment({'order': order2.id, 'customer': cust.id,
                         'items': [{'color': '绿', 'size': 'S', 'qty': 30}]})
shp_np = d.get('id')
st, d = api('POST', f'/shipments/{shp_np}/audit', T_perm, CID)
check('S-AUD-04 缺价 400 提示补价', st == 400
      and '补价' in json.dumps(d, ensure_ascii=False), f'{st} {d}')
# 色码不在订单
st, d = create_shipment({'order': order1.id, 'customer': cust.id,
                         'items': [{'color': '紫', 'size': 'XXL', 'qty': 1}]})
shp_bad = d.get('id')
st, d = api('POST', f'/shipments/{shp_bad}/audit', T_perm, CID)
check('S-AUD-07 色码不在订单 400', st == 400, f'{st} {d}')
# confirmed 全部：order3 全量出货
st, d = create_shipment({'order': order3.id, 'customer': cust.id,
                         'items': [{'color': '白', 'size': 'XS', 'qty': 20}]})
shp_full = d['id']
st, d = api('POST', f'/shipments/{shp_full}/audit', T_perm, CID)
check('S-AUD-05 全量出货 → confirmed（未交量清零）',
      st == 200 and d['shipment']['status'] == 'confirmed'
      and d['receivable']['amount'] == '160.00', f'{st} {d}')
check('S-AUD-08 审核留痕 audit log',
      AuditLog.objects.filter(company=company,
                              message__contains=f'出货审核：【{shp1_no}】').exists(), '')

# ================= 未交矩阵 =================
st, d = api('GET', f'/shipments/{shp1}/undelivered', T_view, CID)
rm = [x for x in d['items'] if x['color'] == '红'][0]
bl = [x for x in d['items'] if x['color'] == '蓝'][0]
check('S-UND-01 未交矩阵真口径（红/M 100-40=60、蓝/L 50-20=30，总未交 90）',
      st == 200 and rm['qty'] == 100 and rm['shipped_qty'] == 40
      and rm['undelivered_qty'] == 60
      and bl['undelivered_qty'] == 30
      and d['total_qty'] == 150 and d['total_shipped'] == 60
      and d['total_undelivered'] == 90, f'{st} {d}')

# ================= 改单（草稿/已审核） =================
st, d = create_shipment({'order': order1.id, 'customer': cust.id,
                         'items': [{'color': '红', 'size': 'M', 'qty': 5}]})
shp_draft = d['id']
st, d = api('PUT', f'/shipments/{shp_draft}', T_perm, CID,
            {'items': [{'color': '红', 'size': 'M', 'qty': 9}],
             'delivery_date': str(today)})
check('S-EDIT-01 草稿 PUT 明细全量替换（total_qty 刷新）',
      st == 200 and d['total_qty'] == 9 and len(d['items']) == 1
      and d['delivery_date'] == str(today), f'{st} {d}')
st, d = api('PUT', f'/shipments/{shp_draft}', T_perm, CID,
            {'order': order1.id, 'customer': cust.id})
check('S-EDIT-01b 草稿 PUT 换挂订单/客户 200', st == 200
      and d['order'] == order1.id and d['customer'] == cust.id, f'{st} {d}')
st, d = api('PUT', f'/shipments/{shp1}', T_perm, CID,
            {'logistics_no': 'SF123456789', 'ship_date': str(today),
             'remark': '已发顺丰'})
check('S-EDIT-02a 已审核 PUT 物流/发货日/备注 200', st == 200
      and d['logistics_no'] == 'SF123456789' and d['ship_date'] == str(today),
      f'{st} {d}')
check('S-EDIT-02a 已审核 PUT 仅物流/备注 200', st == 200
      and d['logistics_no'] == 'SF123456789', f'{st} {d}')
st, d = api('PUT', f'/shipments/{shp1}', T_perm, CID,
            {'items': [{'color': '红', 'size': 'M', 'qty': 1}]})
check('S-EDIT-02b 已审核改明细 400', st == 400, f'{st} {d}')
st, d = api('DELETE', f'/shipments/{shp1}', T_perm, CID)
check('S-EDIT-03 已审核删单 400（须走作废）', st == 400, f'{st}')

# ================= 作废 =================
st, d = api('POST', f'/shipments/{shp1}/void', T_perm, CID)
check('S-VOID-01 作废 200：warning 应收联动提示 + 挂单应收清单',
      st == 200 and d['voided'] is True
      and '不自动撤应收' in d['warning'] and ar_no in d['warning']
      and d['receivables'][0]['receivable_no'] == ar_no, f'{st} {d}')
check('S-VOID-02 应收不自动撤（仍活跃未收）',
      Receivable.objects.filter(pk=ar_id, is_deleted=False).exists()
      and Receivable.objects.get(pk=ar_id).status == 'unpaid', '')
st, d = api('POST', f'/shipments/{shp1}/void', T_perm, CID)
check('S-VOID-03 重复作废 404（软删即下架）', st == 404, f'{st}')
st, d = api('POST', f'/shipments/{shp1}/audit', T_perm, CID)
check('S-VOID-03b 作废单审核 404', st == 404, f'{st}')
# 作废回未交池：原红/M 已出 40 不再计 → 新单出 60 可过（负库存恰好 60-0-60=0 边界 OK）
st, d = create_shipment({'order': order1.id, 'customer': cust.id,
                         'items': [{'color': '红', 'size': 'M', 'qty': 60}]})
shp_after = d['id']
st, d = api('POST', f'/shipments/{shp_after}/audit', T_perm, CID)
check('S-VOID-04 作废回未交池（已出量不计作废单）→ 再审核 200',
      st == 200 and d['shipment']['status'] == 'partial'
      and d['receivable']['amount'] == '600.00', f'{st} {d}')
st, d = api('POST', f'/shipments/{shp_draft}/void', T_perm, CID)
check('S-VOID-05 草稿作废：warning 未生成应收',
      st == 200 and '未生成应收' in d['warning'], f'{st} {d}')

# ================= 装箱行 =================
st, d = api('POST', f'/shipments/{shp_after}/boxes', T_perm, CID,
            {'box_no': 'BOX-01', 'color': '红', 'size': 'M', 'qty': 30,
             'gross_wt': '12.50', 'net_wt': '11.00', 'box_spec': '60x40x40'})
check('S-BOX-01 加装箱行 201（boxes 汇总刷新）',
      st == 201 and Shipment.objects.get(pk=shp_after).boxes == 1, f'{st} {d}')
box_id = d['id']
st, d = api('GET', f'/shipments/{shp_after}/boxes', T_view, CID)
check('S-BOX-02 装箱行列表', st == 200 and len(d) == 1
      and d[0]['box_no'] == 'BOX-01' and d[0]['gross_wt'] == '12.50', f'{st} {d}')
st, d = api('DELETE', f'/shipments/{shp_after}/boxes/{box_id}', T_perm, CID)
check('S-BOX-03 删装箱行 204（boxes 回 0）',
      st == 204 and Shipment.objects.get(pk=shp_after).boxes == 0, f'{st}')
st, d = api('POST', f'/shipments/{shp_after}/boxes', T_perm, CID, {'qty': 5})
check('S-BOX-04 缺箱号 400', st == 400, f'{st} {d}')
st, d = api('DELETE', f'/shipments/{shp_after}/boxes/99999999', T_perm, CID)
check('S-BOX-05 删不存在装箱行 404', st == 404, f'{st}')

# ================= 审核防御分支（ORM 直造，绕过建单校验） =================
shp_noitem = Shipment.objects.create(
    company=company, order=order1, customer=cust,
    shipment_no='TSH-NOITEM', total_qty=0)
st, d = api('POST', f'/shipments/{shp_noitem.id}/audit', T_perm, CID)
check('S-AUD-09 明细为空审核 400', st == 400
      and '出货明细为空' in json.dumps(d, ensure_ascii=False), f'{st} {d}')
order4 = Order.objects.create(company=company, order_no='TSH-004',
                              style=style, customer=cust)
OrderItem.objects.create(order=order4, color='黑', size='XL', qty=10,
                         price=Decimal('3'))
shp_delord = Shipment.objects.create(
    company=company, order=order4, customer=cust,
    shipment_no='TSH-DELORD', total_qty=0)
ShipmentItem.objects.create(shipment=shp_delord, color='黑', size='XL', qty=5)
order4.is_deleted = True
order4.save(update_fields=['is_deleted', 'updated_at'])
st, d = api('POST', f'/shipments/{shp_delord.id}/audit', T_perm, CID)
check('S-AUD-10 订单已删审核 400', st == 400
      and '未挂有效订单' in json.dumps(d, ensure_ascii=False), f'{st} {d}')
# 作废服务防御分支：软删单直接走服务 → 400（视图层已 404，此处护服务自身）
try:
    shp_svc.void_shipment(company, Shipment.all_objects.get(pk=shp1), None)
    _void_guard = False
except Exception:
    _void_guard = True
check('S-VOID-06 作废服务对已作废单 400（双保险）', _void_guard, '')

# ================= 打印（TicketPrintLog 模式） =================
key1 = f'shipment:{shp_full}:shipment'
st, d = api('POST', f'/shipments/{shp_full}/print-logs', T_perm, CID,
            {'template': 'shipment', 'print_key': key1})
check('S-PRT-01 首打 201（reported 1/补打 False）',
      st == 201 and d['reported'] == 1 and d['is_reprint'] is False, f'{st} {d}')
st, d = api('POST', f'/shipments/{shp_full}/print-logs', T_perm, CID,
            {'template': 'shipment', 'print_key': key1})
check('S-PRT-02 同 print_key 幂等（reported 0/duplicated）',
      st == 200 and d['reported'] == 0 and d['duplicated'] == [key1], f'{st} {d}')
st, d = api('POST', f'/shipments/{shp_full}/print-logs', T_perm, CID,
            {'template': 'shipment', 'print_key': f'shipment:{shp_full}:shipment-2'})
check('S-PRT-03 换 key 再打 → 补打 is_reprint True',
      st == 201 and d['reported'] == 1 and d['is_reprint'] is True, f'{st} {d}')
st, d = api('POST', f'/shipments/{shp_full}/print-logs', T_print, CID,
            {'template': 'packing', 'print_key': f'shipment:{shp_full}:packing'})
check('S-PRT-04 print.manage 岗可上报装箱单', st == 201
      and d['reported'] == 1, f'{st} {d}')
st, d = api('GET', f'/shipments/{shp_full}/print-logs', T_view, CID)
check('S-PRT-05 打印历史 3 条', st == 200 and len(d) == 3, f'{st} {len(d) if isinstance(d, list) else d}')
st, d = api('GET', f'/shipments/{shp_full}/print-logs?template=packing', T_view, CID)
check('S-PRT-05b 模板过滤', st == 200 and len(d) == 1
      and d[0]['template_display'] == '装箱单', f'{st}')
st, d = api('POST', f'/shipments/{shp_full}/print-logs', T_perm, CID,
            {'template': 'bogus', 'print_key': 'x'})
check('S-PRT-06 非法模板 400', st == 400, f'{st}')
st, d = api('POST', f'/shipments/{shp_full}/print-logs', T_view, CID,
            {'template': 'shipment', 'print_key': 'x2'})
check('S-PRT-07 view 岗上报 403（无 manage/print.manage）', st == 403, f'{st}')
st, d = api('GET', f'/shipments/{shp_full}/print-logs?template=bogus', T_view, CID)
check('S-PRT-05c 历史模板非法 400', st == 400, f'{st}')
st, d = api('POST', f'/shipments/{shp_full}/print-logs', T_perm, CID,
            {'template': 'packing'})
check('S-PRT-02b 无 print_key 上报 201（print_key 空=历史兼容，不参与幂等约束）',
      st == 201 and d['reported'] == 1, f'{st} {d}')
# 并发兜底：create 抛 IntegrityError → 唯一约束兜底幂等（monkeypatch 单次触发）
from django.db import IntegrityError
_orig_mgr = ShipmentPrintLog.objects
class _FlakyLogMgr:
    fired = False
    def __init__(self, mgr):
        self._m = mgr
    def __getattr__(self, a):
        return getattr(self._m, a)
    def create(self, **kw):
        if not _FlakyLogMgr.fired:
            _FlakyLogMgr.fired = True
            raise IntegrityError('sim-concurrent')
        return self._m.create(**kw)
ShipmentPrintLog.objects = _FlakyLogMgr(_orig_mgr)
st, d = api('POST', f'/shipments/{shp_full}/print-logs', T_perm, CID,
            {'template': 'packing', 'print_key': 'flaky-key'})
ShipmentPrintLog.objects = _orig_mgr
check('S-PRT-08 并发撞 key 兜底（IntegrityError → reported 0）',
      st == 200 and d['reported'] == 0 and d['duplicated'] == ['flaky-key'],
      f'{st} {d}')

# ================= 手工应收不受出货域影响（回归） =================
st, d = api('POST', '/finance/receivables', T_owner, CID,
            {'customer': cust.id, 'order': order1.id, 'amount': 88})
check('S-AR-02 手工建应收正常（shipment_id 不传保持 null）',
      st == 201 and d['shipment_id'] is None, f'{st} {d}')

# ================= __str__ =================
api('POST', f'/shipments/{shp_after}/boxes', T_perm, CID,
    {'box_no': 'BOX-R', 'color': '红', 'size': 'M', 'qty': 60})
check('S-MISC-01 Shipment.__str__', str(Shipment.objects.get(pk=shp_full))
      == f'{CID}:{Shipment.objects.get(pk=shp_full).shipment_no}', '')
check('S-MISC-02 ShipmentItem.__str__',
      str(ShipmentItem.objects.filter(shipment=shp_full).first())
      == f'{shp_full}:白/XSx20', '')
check('S-MISC-03 PackingBox.__str__',
      str(PackingBox.objects.filter(shipment=shp_after).first())
      == f'{shp_after}:BOX-R', '')
check('S-MISC-04 ShipmentPrintLog.__str__',
      str(ShipmentPrintLog.objects.filter(shipment=shp_full).first())
      .startswith(f'{shp_full} '), '')

# ================= 汇总 =================
print()
print(f'===== test_shipment: {len(PASS)} pass / {len(FAIL)} fail =====')
if FAIL:
    print('FAILED:', FAIL)
    raise SystemExit(1)
