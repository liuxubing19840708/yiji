# -*- coding: utf-8 -*-
"""
订单域（test_orders）端到端后端验证 —— SA1 交付（2026-10-20 P1）。

覆盖《端到端测试设计方案》§3.1 ORD-001~063：
  建单 ORD-001~008（自动 SO 顺延/手填单号/items 校验/同色码唯一/软删复用与活跃撞号）
  挂单 ORD-010~014（POST /productions 可选 order_id 全分支 + GET /productions?order_id）
  进度 ORD-020~025（完成率分子=登记分母=订单量/落后格/无床次0/两床累加/无单床次隔离/PUT 重算）
  状态 ORD-030~033（<100% 可完成/非法 status/有床次 400 不可删/无床次软删 204）
  权限 ORD-040~043（无点 403/legacy 旧 'production' 岗零回归/'*' 管理员放行）
  隔离 ORD-050~052（跨厂不可见/客户删除 SET_NULL 保留）
  列表 ORD-060~063（status/keyword/overdue 过滤 + days_left/交期灯）
  预警 ORD-070~072：交期预警通知属 P3，暂不实现 —— TODO(P3) 交付时补测
  （站内通知按「单+日」去重；无交期订单不触发）。

跑法：manage.py shell -c "exec(compile(open('test_orders.py',encoding='utf-8').read(),'test_orders.py','exec'))"
幂等：自建「订单测试厂(可删)/订单测试B厂(可删)」并跨次复用（Company.creator PROTECT 不可删）；
每轮 preclean 硬清本厂订单域业务行（订单/床次/菲票/登记/款式/客户），不依赖 DB 现状。
"""
import json
import re
from datetime import timedelta

from django.contrib.auth.hashers import make_password
from django.test import Client
from django.utils import timezone

from apps.accounts.models import Company, Post as PostM, User, UserCompany
from apps.accounts.services import create_company_with_owner
from apps.catalog.models import Customer, Process, Style, Ticket
from apps.ops.models import AuditLog
from apps.orders.models import Order, OrderItem
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


OWNER, H_LEGACY, H_PLAIN, OWNER_B = (
    '13988710101', '13988710102', '13988710103', '13988710104')
CO_A, CO_B = '订单测试厂(可删)', '订单测试B厂(可删)'


def preclean(company):
    """幂等自清扫：硬清本厂订单域业务行（订单/床次/菲票/登记/款式/客户/审计）。"""
    Order.all_objects.filter(company=company).delete()
    OrderItem.all_objects.filter(order__company=company).delete()
    Record.objects.filter(company=company).delete()
    Ticket.objects.filter(company=company, bed_no__startswith='TORD').delete()
    Production.objects.filter(company=company, bed_no__startswith='TORD').delete()
    from apps.tech.models import Bom  # noqa: E402  # D4 套件建了生效BOM，PROTECT 须先于款式删除
    Bom.all_objects.filter(company=company).delete()
    Style.objects.filter(company=company, style_no__startswith='TORD').delete()
    Customer.objects.filter(company=company, customer_no__startswith='TORD').delete()
    AuditLog.objects.filter(company=company).delete()


# ================= 建厂（复用） + preclean =================
company = Company.objects.filter(name=CO_A).first()
if company is None:
    owner = ensure_user(OWNER, '订单厂长')
    company, admin_post = create_company_with_owner(owner, CO_A)
else:
    owner = ensure_user(OWNER, '订单厂长')
    admin_post = PostM.objects.get(company=company, is_admin=True, is_deleted=False)
company_b = Company.objects.filter(name=CO_B).first()
if company_b is None:
    owner_b = ensure_user(OWNER_B, '订单B厂长')
    company_b, _ = create_company_with_owner(owner_b, CO_B)
else:
    owner_b = ensure_user(OWNER_B, '订单B厂长')
CID, CID_B = company.id, company_b.id

preclean(company)
preclean(company_b)

# 岗位矩阵：普通成员（无点）/ 旧裁床岗（legacy coarse 'production'）
post_plain = ensure_post(company, '订单普工', [])
post_legacy = ensure_post(company, '订单旧裁床岗', ['production'])
plain = ensure_user(H_PLAIN, '订单无权员')
legacy_u = ensure_user(H_LEGACY, '订单旧岗员')
ensure_member(plain, company, post_plain)
ensure_member(legacy_u, company, post_legacy)

TA = login(OWNER)          # A 厂管理员（'*'）
TB = login(OWNER_B)        # B 厂管理员
T_PLAIN = login(H_PLAIN)
T_LEGACY = login(H_LEGACY)

# 最小业务档案：款式 / 固定工序 / 客户
style = Style.objects.create(company=company, style_no='TORD-S1', name='订单测试款')
style_b = Style.objects.create(company=company_b, style_no='TORD-S1B', name='B厂测试款')
# D4 开裁缺 BOM 拦截（2026-10-24）：建床需款式有 active BOM，测试款补齐
from apps.tech.models import Bom  # noqa: E402
if not Bom.all_objects.filter(company=company, style=style).exists():
    Bom.objects.create(company=company, style=style, version=1, status=Bom.Status.ACTIVE)
if not Bom.all_objects.filter(company=company_b, style=style_b).exists():
    Bom.objects.create(company=company_b, style=style_b, version=1, status=Bom.Status.ACTIVE)
proc = Process.objects.filter(company=company, is_fixed=True).first() \
    or Process.objects.create(company=company, name='整件', is_fixed=True)
cust = Customer.objects.create(company=company, customer_no='TORD-C1', name='订单测试客户')
today = timezone.localdate()

TODAY = today.isoformat()


def make_order(token, cid, order_no='', items=None, delivery=None, customer=None, **kw):
    # P4 契约：订单必须关联款式（跨厂用例由调用方 kw 覆盖）
    body = {'style': style.id,
            'items': items if items is not None else
            [{'color': '黑', 'size': 'M', 'qty': 100}]}
    if order_no:
        body['order_no'] = order_no
    if delivery:
        body['delivery_date'] = delivery
    if customer:
        body['customer'] = customer
    body.update(kw)
    return api('POST', '/orders', token, cid, body)


def make_bed(token, cid, bed_no, style_id, order_id=None, **kw):
    body = {'style': style_id, 'bed_no': bed_no}
    if order_id:
        body['order_id'] = order_id
    body.update(kw)
    return api('POST', '/productions', token, cid, body)


def make_ticket(prod, bundle_no, color, size, qty):
    return Ticket.objects.create(company=company, production=prod, bed_no=prod.bed_no,
                                 bundle_no=bundle_no, color=color, size=size, qty=qty,
                                 qr_code='x')


def register(ticket, qty, user=None):
    """直接落登记行（分子口径=菲票登记 Σqty，绕过登记 API 的价格/工序前置）。"""
    Record.objects.create(company=company, ticket=ticket, process=proc,
                          user=user or owner, qty=qty, price='1.000',
                          registered_at=timezone.now())


# ================= 建单 ORD-001~008 =================
st, d = make_order(TA, CID, items=[{'color': '黑', 'size': 'M', 'qty': 100},
                                   {'color': '黑', 'size': 'L', 'qty': 50}])
check('ORD-001 正常建单 201', st == 201, f'{st} {d}')
check('ORD-001 自动单号 SO+日期+3位', bool(re.fullmatch(r'SO\d{8}\d{3}', (d or {}).get('order_no', ''))),
      str((d or {}).get('order_no')))
o_auto1 = d['id']

st, d2 = make_order(TA, CID)
check('ORD-001b 第二单自动顺延+1',
      st == 201 and int(d2['order_no'][2:]) == int(d['order_no'][2:]) + 1,
      f"{st} {d.get('order_no')} -> {d2.get('order_no') if isinstance(d2, dict) else d2}")

st, d = make_order(TA, CID, order_no='TORD-MANUAL-1')
check('ORD-002 手填单号 201', st == 201 and d.get('order_no') == 'TORD-MANUAL-1', f'{st} {d}')
o_manual = d['id']

st, d = make_order(TA, CID, items=[])
check('ORD-003 items 空列表 400', st == 400, f'{st} {d}')
st, d = api('POST', '/orders', TA, CID, {'order_no': 'TORD-NOITEMS'})
check('ORD-003b items 缺失 400', st == 400, f'{st} {d}')
st, d = make_order(TA, CID, items=[{'color': '黑', 'size': 'M', 'qty': 0}])
check('ORD-004 qty=0 400', st == 400, f'{st} {d}')
st, d = make_order(TA, CID, items=[{'color': '黑', 'size': 'M', 'qty': -5}])
check('ORD-004b qty<0 400', st == 400, f'{st} {d}')
st, d = make_order(TA, CID, items=[{'color': '黑', 'size': 'M', 'qty': 10},
                                   {'color': '黑', 'size': 'M', 'qty': 20}])
check('ORD-005 同单同色码重复 400', st == 400, f'{st} {d}')
st, d = make_order(TA, CID, items=[{'color': '黑', 'size': 'M', 'qty': 10},
                                   {'color': '白', 'size': 'M', 'qty': 20}])
check('ORD-006 跨色同码 201', st == 201, f'{st} {d}')

# 软删复用（condition UK 两分支）：无床次软删 204 → 同号可复用；活跃行撞号 400
st, d = make_order(TA, CID, order_no='TORD-REUSE-1')
rid = d['id']
st_del, _ = api('DELETE', f'/orders/{rid}', TA, CID)
st, d = make_order(TA, CID, order_no='TORD-REUSE-1')
check('ORD-007 软删后同号可复用 201', st_del == 204 and st == 201, f'del={st_del} re={st}')
o_reuse = d['id']
st, d = make_order(TA, CID, order_no='TORD-REUSE-1')
check('ORD-008 活跃行撞号 400', st == 400, f'{st} {d}')

# 列表统计字段（total_qty 汇总）
st, d = api('GET', f'/orders/{o_auto1}', TA, CID)
check('列表统计 total_qty=150', st == 200 and d.get('total_qty') == 150, f'{st} {d}')

# ================= 挂单 ORD-010~014 =================
# 挂单主单：黑/M x100 + 白/L x100（总量 200）
st, d = make_order(TA, CID, order_no='TORD-HANG-1',
                   items=[{'color': '黑', 'size': 'M', 'qty': 100},
                          {'color': '白', 'size': 'L', 'qty': 100}])
o_main = d['id']

st, d = make_bed(TA, CID, 'TORD-B1', style.id, order_id=o_main, total_qty=60)
check('ORD-010 挂单建床 201', st == 201, f'{st} {d}')
bed1 = d['id']
check('ORD-010 source_order_no 自动带出', d.get('source_order_no') == 'TORD-HANG-1',
      str(d.get('source_order_no')))
check('ORD-010 床次落 order FK', d.get('order') == o_main, str(d.get('order')))
st, d = api('GET', f'/productions?order_id={o_main}', TA, CID)
check('GET /productions?order_id 过滤命中',
      st == 200 and [r['id'] for r in d.get('results', [])] == [bed1],
      f'{st} {[r.get("bed_no") for r in d.get("results", [])]}')

st, d = make_bed(TA, CID, 'TORD-B2', style.id, order_id=o_main, source_order_no='EXT-9')
check('ORD-010b source_order_no 显式传入覆盖', st == 201 and d.get('source_order_no') == 'EXT-9',
      f'{st} {d.get("source_order_no")}')
bed2 = d['id']

st, d = make_order(TB, CID_B, order_no='TORD-B-ONLY', style=style_b.id)
o_b = d['id']
st, d = make_bed(TA, CID, 'TORD-BX', style.id, order_id=o_b)
check('ORD-011 跨公司订单挂单 400', st == 400, f'{st} {d}')

st, d = make_order(TA, CID, order_no='TORD-CXL-1')
o_cxl = d['id']
st, _ = api('PUT', f'/orders/{o_cxl}/status', TA, CID, {'status': 'cancelled'})
st, d = make_bed(TA, CID, 'TORD-BY', style.id, order_id=o_cxl)
check('ORD-012 已取消订单挂单 400', st == 400, f'{st} {d}')

st, d = make_bed(TA, CID, 'TORD-B3', style.id, source_order_no='KEEP-1')
check('ORD-013 不带 order_id 存量路径不变',
      st == 201 and d.get('order') in (None, '') and d.get('source_order_no') == 'KEEP-1',
      f'{st} order={d.get("order")} src={d.get("source_order_no")}')
bed3 = d['id']

st, d = make_bed(TA, CID, 'TORD-BZ', style.id, order_id=999999999)
check('ORD-014 order_id 不存在 404', st == 404, f'{st} {d}')

# ================= 进度 ORD-020~025 =================
t1 = make_ticket(Production.objects.get(pk=bed1), '1', '黑', 'M', 60)
register(t1, 40)   # 登记 40 < 菲票裁数 60：分子=登记而非裁数

st, d = api('GET', f'/orders/{o_main}/progress', TA, CID)
item = next((i for i in d.get('items', []) if i['color'] == '黑' and i['size'] == 'M'), {})
check('ORD-020 完成率=登记/订单量 40%', st == 200 and item.get('progress') == 40.0,
      f'{st} {item}')
check('ORD-020 分子=登记(40)非裁数(60)', item.get('registered_qty') == 40, str(item))
check('ORD-020 整单汇总 40/200=20%', d.get('progress') == 20.0 and d.get('total_qty') == 200,
      f'{d.get("progress")} {d.get("total_qty")}')
check('ORD-020 关联床次列表',
      sorted(b['bed_no'] for b in d.get('productions', [])) == ['TORD-B1', 'TORD-B2'],
      str(d.get('productions')))
check('ORD-020b 已裁口径 cut_qty=60（菲票聚合，≠登记40）',
      item.get('cut_qty') == 60 and d.get('cut_qty') == 60, str(item))

# 两床同色码登记累加不重复（每票只属于一床，跨床累加各自计数一次）
register(make_ticket(Production.objects.get(pk=bed2), '1', '黑', 'M', 30), 45)
register(make_ticket(Production.objects.get(pk=bed2), '2', '白', 'L', 30), 12)
st, d = api('GET', f'/orders/{o_main}/progress', TA, CID)
black = next((i for i in d['items'] if i['color'] == '黑'), {})
white = next((i for i in d['items'] if i['color'] == '白'), {})
check('ORD-023 黑 85%(40+45) 不落后', black.get('progress') == 85.0 and black.get('lagging') is False,
      str(black))
check('ORD-023b 白 12% 落后格(<80%)', white.get('progress') == 12.0 and white.get('lagging') is True,
      str(white))
check('ORD-023c 整单 97/200=48.5%', d.get('registered_qty') == 97 and d.get('progress') == 48.5,
      f'{d.get("registered_qty")} {d.get("progress")}')
check('ORD-023d 已裁黑M=90(60+30) 白L=30 合计120',
      black.get('cut_qty') == 90 and white.get('cut_qty') == 30 and d.get('cut_qty') == 120,
      f'{black.get("cut_qty")} {white.get("cut_qty")} {d.get("cut_qty")}')

st, d = make_order(TA, CID, order_no='TORD-EMPTY-1', items=[{'color': '红', 'size': 'S', 'qty': 50}])
o_empty = d['id']
st, d = api('GET', f'/orders/{o_empty}/progress', TA, CID)
check('ORD-022 无床次订单 progress=0',
      st == 200 and d.get('progress') == 0.0 and d.get('registered_qty') == 0, f'{st} {d}')

# 历史无单床次：登记不进任何订单（o_main 进度不变）
p_none = Production.objects.create(company=company, style=style, bed_no='TORD-B-NOORDER',
                                   total_qty=10)
t_none = make_ticket(p_none, '1', '黑', 'M', 10)
register(t_none, 10)
st, d = api('GET', f'/orders/{o_main}/progress', TA, CID)
check('ORD-024 无单床次登记不进订单', d.get('registered_qty') == 97, str(d.get('registered_qty')))

# PUT items 全量替换后进度重算
st, d = api('PUT', f'/orders/{o_main}', TA, CID,
            {'style': style.id, 'items': [{'color': '灰', 'size': 'XL', 'qty': 200}]})
check('ORD-025 PUT items 全量替换 200', st == 200, f'{st} {d}')
st, d = api('GET', f'/orders/{o_main}/progress', TA, CID)
check('ORD-025b 进度按新矩阵重算',
      [i['color'] for i in d.get('items', [])] == ['灰'] and d.get('total_qty') == 200,
      str([(i.get('color'), i.get('qty')) for i in d.get('items', [])]))

# ================= 状态 ORD-030~033 =================
st, d = make_order(TA, CID, order_no='TORD-DONE-1', customer=cust.id)
o_done = d['id']
st, d = api('PUT', f'/orders/{o_done}/status', TA, CID, {'status': 'done'})
check('ORD-030 <100% 允许完成 200', st == 200 and d.get('status') == 'done', f'{st} {d}')
st, d = api('PUT', f'/orders/{o_done}/status', TA, CID, {'status': 'producing'})
# 契约 §四：status 端点仅接受 done/cancelled（状态机 producing→done/cancelled，不回退）
check('ORD-030b 状态回退 producing 400（契约仅允许 done/cancelled）', st == 400, f'{st} {d}')
st, d = api('PUT', f'/orders/{o_done}/status', TA, CID, {'status': 'bogus'})
check('ORD-032 非法 status 400', st == 400, f'{st} {d}')

st, d = api('DELETE', f'/orders/{o_main}', TA, CID)
check('ORD-033 DELETE 已挂床次 400', st == 400, f'{st} {d}')
st, d = api('DELETE', f'/orders/{o_empty}', TA, CID)
check('ORD-033b DELETE 无床次软删 204', st == 204, f'{st} {d}')
st, d = api('GET', f'/orders/{o_empty}', TA, CID)
check('ORD-033c 软删后详情 404', st == 404, f'{st} {d}')

# ================= 权限 ORD-040~043 =================
st, d = api('GET', '/orders', T_PLAIN, CID)
check('ORD-040 无点读列表 403', st == 403, f'{st} {d}')
st, d = make_order(T_PLAIN, CID)
check('ORD-040b 无点建单 403', st == 403, f'{st} {d}')
st, d = api('PUT', f'/orders/{o_done}/status', T_PLAIN, CID, {'status': 'done'})
check('ORD-040c 无点改状态 403', st == 403, f'{st} {d}')
st, d = api('GET', f'/orders/{o_done}/progress', T_PLAIN, CID)
check('ORD-040d 无点读进度 403', st == 403, f'{st} {d}')
st, d = make_order(T_LEGACY, CID, order_no='TORD-LEGACY-1')
check('ORD-041 legacy 旧裁床岗可建单（零回归）', st == 201, f'{st} {d}')
o_legacy = d.get('id')
st, d = api('GET', '/orders', T_LEGACY, CID)
check('ORD-041b legacy 旧岗可读列表', st == 200, f'{st} {d}')
st, d = api('PUT', f'/orders/{o_done}/status', T_LEGACY, CID, {'status': 'done'})
check('ORD-041c legacy 旧岗可改状态', st == 200, f'{st} {d}')
st, d = api('GET', '/orders', TA, CID)
check("ORD-042 '*' 管理员放行", st == 200, f'{st} {d}')
# ORD-043 列表写按钮显隐由前端按 effective_perms 管（后端不做）——无需断言

# ================= 隔离 ORD-050~052 =================
st, d = api('GET', '/orders', TB, CID_B)
nos = [r['order_no'] for r in d.get('results', [])]
check('ORD-050 B 厂读不到 A 厂订单', st == 200 and all(not n.startswith('TORD-HANG') for n in nos),
      f'{st} {nos}')
st, d = api('GET', f'/orders/{o_main}', TB, CID_B)
check('ORD-050b B 厂读 A 厂单详情 404', st == 404, f'{st} {d}')
# ORD-051 /tickets/lookup 跨厂 404 属 SA3 手输码端点，交付后由 test_scan_enhance 覆盖——TODO(SA3)
st, d = api('GET', f'/orders/{o_done}', TA, CID)
check('ORD-052 订单带客户', st == 200 and d.get('customer') == cust.id, f'{st} {d.get("customer")}')
Customer.objects.filter(pk=cust.pk).delete()   # 硬删触发 FK SET_NULL（软删不动 FK）
st, d = api('GET', f'/orders/{o_done}', TA, CID)
check('ORD-052b 客户删除后订单 SET_NULL 保留', st == 200 and d.get('customer') is None,
      f'{st} customer={d.get("customer")}')

# ================= 列表 ORD-060~063 =================
st, d = make_order(TA, CID, order_no='TORD-KW-1',
                   delivery=(today - timedelta(days=3)).isoformat())
o_over = d['id']
st, d = api('GET', '/orders?status=done', TA, CID)
check('ORD-060 status=done 过滤',
      st == 200 and o_done in [r['id'] for r in d.get('results', [])]
      and o_main not in [r['id'] for r in d.get('results', [])], f'{st} {d}')
st, d = api('GET', '/orders?status=cancelled', TA, CID)
check('ORD-060b status=cancelled 过滤', st == 200 and o_cxl in [r['id'] for r in d.get('results', [])],
      f'{st} {d}')
st, d = api('GET', '/orders?keyword=KW-1', TA, CID)
check('ORD-061 keyword 单号命中', st == 200 and [r['id'] for r in d.get('results', [])] == [o_over],
      f'{st} {d}')
c2 = Customer.objects.create(company=company, customer_no='TORD-C2', name='订单KW客户')
st, d = make_order(TA, CID, order_no='TORD-KW-2', customer=c2.id)
o_kw2 = d['id']
st, d = api('GET', '/orders?keyword=订单KW客户', TA, CID)
check('ORD-061b keyword 客户名命中', st == 200 and [r['id'] for r in d.get('results', [])] == [o_kw2],
      f'{st} {d}')
st, d = api('GET', '/orders?overdue=1', TA, CID)
overs = [r['id'] for r in d.get('results', [])]
check('ORD-062 overdue=1 只含过期生产中单', st == 200 and o_over in overs
      and o_done not in overs and o_cxl not in overs, f'{st} {overs}')
st, d = api('GET', f'/orders/{o_over}', TA, CID)
check('ORD-063 逾期 days_left 为负', d.get('days_left', 0) == -3, str(d.get('days_left')))
check('ORD-063b 逾期生产中交期灯红', d.get('delivery_light') == 'red', str(d.get('delivery_light')))
st, d = make_order(TA, CID, order_no='TORD-FUTURE-1',
                   delivery=(today + timedelta(days=7)).isoformat())
o_future = d['id']
st, d = api('GET', f'/orders/{o_future}', TA, CID)
check('ORD-063c 距交期7天 days_left=7 灯黄',
      d.get('days_left') == 7 and d.get('delivery_light') == 'yellow', f'{d}')
st, d = api('GET', f'/orders/{o_reuse}', TA, CID)
check('ORD-063d 无交期 days_left=None 灯绿',
      d.get('days_left') is None and d.get('delivery_light') == 'green', f'{d}')

# ORD-070~072（交期预警站内通知，P3）：TODO(P3) —— 通知触发+「单+日」去重+无交期不触发，交付时补测

# ================= cleanup =================
try:
    preclean(company)
    preclean(company_b)
    PostM.objects.filter(pk=post_plain.pk).update(permissions=[])
    PostM.objects.filter(pk=post_legacy.pk).update(permissions=['production'])
    print('cleanup: done（订单域业务行已清，测试厂/账号复用保留）')
except Exception as e:  # noqa: BLE001
    print('cleanup WARN:', e)

print(f'\n==== 订单域套件：{len(PASS)} pass / {len(FAIL)} fail ====')
if FAIL:
    print('FAILED:', FAIL)
