# -*- coding: utf-8 -*-
"""
报价域（test_quotation）端到端后端验证 —— SA-P2-3 交付（2026-10-24 P2 报价域）。

覆盖 P2 报价域全部端点与口径（docs/服装ERP全模块设计方案.md §4.8 + P1设计决策表
D2/D3/B3 行）：
  算定  Q-VAL-01~05（工价快照带出/倍率默认 2.3 与超界 warning 不拦截/
                     小单 addon 规则 qty=200→+30% qty=500→0 显式覆盖/
                     total_price 与 profit_rate 数字/bom_item 带出与行内矛盾拦截）
  端点  Q-API-06~08（列表过滤/PUT 全量替换重算/mark-status 流转与非法回退 400）
  对比  Q-BF-09~12（未关联订单提示/won 无成本行提示「先重算成本」/
                     与 OrderCost 对拍逐项差额+差异率/is_stale 软提醒）
  权限  Q-PERM-13~16（401/403/view-manage 两段式/权限点注册+effective_perms
                     下发+legacy 零回归）
  隔离  Q-ISO-17~18（跨厂 404/列表不见）
  其他  Q-NO-19~20（quote_no 自动号+软删流水不复用/软删后列表不显详情 404）

跑法：manage.py shell -c "exec(compile(open('test_quotation.py',encoding='utf-8').read(),'test_quotation.py','exec'))"
幂等：自建「报价测试厂(可删)/报价B厂(可删)」并跨次复用（Company.creator PROTECT
不可删）；每轮 preclean 硬清本厂业务行，不依赖 DB 现状；
绝不触碰真实厂（company_id=86 等）。
D3 回填对比为软提醒语义：断言只验证只读返回，不验证任何拦截行为。
"""
import json
from decimal import Decimal

from django.contrib.auth.hashers import make_password
from django.test import Client
from django.utils import timezone

from apps.accounts.models import Company, Post as PostM, User, UserCompany
from apps.accounts.services import create_company_with_owner
from apps.catalog.models import Process, Style, Ticket
from apps.core.permissions import ALL_PERMISSION_KEYS, effective_perms
from apps.cost.services import mark_orders_stale, recompute
from apps.ops.models import AuditLog
from apps.orders.models import Order, OrderItem
from apps.production.models import Production, ProductionProcess
from apps.records.models import Record
from apps.tech.models import Bom, BomItem, Material
from apps.warehouse.models import IssueItem, Lot, MaterialIssue
from apps.quotation.models import QuoteMaterial, Quotation

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
    for k, v in uc_kw.items():
        setattr(uc, k, v)
    uc.is_deleted = False
    uc.status = UserCompany.Status.ACTIVE
    uc.post = post
    uc.save()
    return uc


OWNER, H_PLAIN, H_VIEW, H_PERM, H_LEGACY, H_OWNER_B, H_W1 = (
    '13955671301', '13955671302', '13955671303', '13955671304', '13955671305',
    '13955671306', '13955671311')
CO_A, CO_B = '报价测试厂(可删)', '报价B厂(可删)'


def preclean(company):
    """幂等自清扫：硬清本厂业务行（PROTECT 逆序：报价→领料→登记→菲票→床次→订单）。"""
    Quotation.all_objects.filter(company=company).delete()
    IssueItem.all_objects.filter(issue__company=company).delete()
    MaterialIssue.all_objects.filter(company=company).delete()
    Record.all_objects.filter(company=company).delete()
    Ticket.all_objects.filter(company=company).delete()
    ProductionProcess.all_objects.filter(production__company=company).delete()
    Production.all_objects.filter(company=company).delete()
    OrderItem.all_objects.filter(order__company=company).delete()
    Order.all_objects.filter(company=company).delete()
    Lot.all_objects.filter(company=company).delete()
    BomItem.all_objects.filter(bom__company=company).delete()
    Bom.all_objects.filter(company=company).delete()
    Material.all_objects.filter(company=company).delete()
    Style.objects.filter(company=company).delete()
    Process.all_objects.filter(company=company, name__startswith='QTA').delete()
    AuditLog.objects.filter(company=company).delete()


# ================= 建厂（复用） + preclean =================
company = Company.objects.filter(name=CO_A).first()
if company is None:
    owner = ensure_user(OWNER, '报价厂长')
    company, admin_post = create_company_with_owner(owner, CO_A)
else:
    owner = ensure_user(OWNER, '报价厂长')
    admin_post = PostM.objects.get(company=company, is_admin=True, is_deleted=False)
company_b = Company.objects.filter(name=CO_B).first()
if company_b is None:
    owner_b = ensure_user(H_OWNER_B, '报价B厂长')
    company_b, _ = create_company_with_owner(owner_b, CO_B)
else:
    owner_b = ensure_user(H_OWNER_B, '报价B厂长')
CID, CID_B = company.id, company_b.id

preclean(company)
preclean(company_b)

post_plain = ensure_post(company, '报价普工', [])
post_view = ensure_post(company, '报价查看岗', ['quotation.view'])
post_perm = ensure_post(company, '报价管理岗', ['quotation.view', 'quotation.manage'])
post_legacy = ensure_post(company, '报价旧岗', ['production'])
plain = ensure_user(H_PLAIN, '报价无权员')
view_u = ensure_user(H_VIEW, '报价查看员')
perm_u = ensure_user(H_PERM, '报价管理员')
legacy_u = ensure_user(H_LEGACY, '报价旧岗员')
w1 = ensure_user(H_W1, '报价计件工')
ensure_member(plain, company, post_plain, group_no=0)
ensure_member(view_u, company, post_view, group_no=0)
ensure_member(perm_u, company, post_perm, group_no=0)
ensure_member(legacy_u, company, post_legacy, group_no=0)
ensure_member(w1, company, post_plain, group_no=0)

T_owner = login(OWNER)
T_plain = login(H_PLAIN)
T_view = login(H_VIEW)
T_perm = login(H_PERM)
T_legacy = login(H_LEGACY)
T_owner_b = login(H_OWNER_B)
today = timezone.localdate()
LIST = '/quotation/quotations'

# ================= 数据工厂 =================
p_a = Process.objects.create(company=company, name='QTA车缝', default_price=1,
                             is_fixed=True, sort=1)
p_b = Process.objects.create(company=company, name='QTA锁边', default_price=0.6,
                             is_fixed=True, sort=2)
style1 = Style.objects.create(company=company, style_no='QTA-S1', name='报价测试款A')
from apps.catalog.models import StyleProcess
StyleProcess.objects.create(style=style1, process=p_a, price=Decimal('1'))
StyleProcess.objects.create(style=style1, process=p_b, price=Decimal('0.6'))

mat1 = Material.objects.create(company=company, code='QTA-M1', name='主面料',
                               category=Material.Category.FABRIC, unit='米')
mat2 = Material.objects.create(company=company, code='QTA-M2', name='辅料',
                               category=Material.Category.ACCESSORY, unit='个')
bom1 = Bom.objects.create(company=company, style=style1, version=1)
bom_item1 = BomItem.objects.create(bom=bom1, material=mat2, kind='accessory',
                                   unit='个', unit_usage=Decimal('0.3'))

# B 厂最小数据（隔离用例）
style_b = Style.objects.create(company=company_b, style_no='QTA-B1', name='B厂款')
order_b = Order.objects.create(company=company_b, order_no='QTA-B-001', style=style_b)


# ================= Q-VAL 创建/算定 =================
# Q-VAL-01 工价快照带出 + 默认倍率 + 小单规则（qty=200<300 → +30%）
st, body = api('POST', LIST, T_owner, CID, {
    'style': style1.id, 'qty': 200,
    'materials': [
        {'material': mat1.id, 'quote_unit_usage': '0.25', 'price': '40'},
        {'material': mat2.id, 'bom_item': bom_item1.id,
         'quote_unit_usage': '0.1', 'price': '20'},
    ]})
q1 = body
check('Q-VAL-01a 创建 201 且 quote_no 自动号 Q+日期-001',
      st == 201 and q1.get('quote_no') == f'Q{today:%Y%m%d}-001',
      f'{st} {body}')
labor = q1.get('labor') or {}
check('Q-VAL-01b 工价快照带出 labor_base=1.6（1+0.6）',
      Decimal(labor.get('labor_base')) == Decimal('1.6'), str(labor))
check('Q-VAL-01c 倍率默认 2.3 无 warning',
      Decimal(labor.get('rate')) == Decimal('2.3')
      and labor.get('rate_warning') is False,
      str(labor))
check('Q-VAL-01d 小单 qty=200 → addon 默认 +30% auto_applied',
      Decimal(labor.get('small_lot_addon')) == Decimal('30')
      and (labor.get('addon_rule') or {}).get('auto_applied') is True
      and (labor.get('addon_rule') or {}).get('threshold') == 300,
      str(labor))
check('Q-VAL-01e 材料合计 2400（(0.25×40+0.1×20)×200）',
      Decimal(q1.get('material_total')) == Decimal('2400.00'),
      str(q1.get('material_total')))
check('Q-VAL-01f 人工合计 4.78（1.6×2.3×1.3，addon_amount 缺省 0）',
      Decimal(labor.get('labor_total')) == Decimal('4.78'), str(labor))
check('Q-VAL-01g 报价总额 2404.78',
      Decimal(q1.get('total_price')) == Decimal('2404.78'),
      str(q1.get('total_price')))
check('Q-VAL-01h profit_rate=0.13（(total-(材料+base))/total×100）',
      Decimal(q1.get('profit_rate')) == Decimal('0.13'),
      str(q1.get('profit_rate')))
check('Q-VAL-01i 用料行 amount=单耗×单价 随行下发',
      [Decimal(m['amount']) for m in q1.get('materials', [])]
      == [Decimal('10'), Decimal('2')],
      str(q1.get('materials')))

# Q-VAL-02 小单规则：qty=500 → 0；显式传 small_lot_addon → auto_applied=False
st, body = api('POST', LIST, T_owner, CID, {'style': style1.id, 'qty': 500})
q2 = body
check('Q-VAL-02a qty=500 → addon 默认 0（规则仍在）',
      st == 201
      and Decimal((body.get('labor') or {}).get('small_lot_addon')) == 0
      and (body.get('labor') or {}).get('addon_rule', {}).get('auto_applied') is True,
      f'{st} {body}')
st, body = api('POST', LIST, T_owner, CID,
               {'style': style1.id, 'qty': 200,
                'labor': {'small_lot_addon': '10'}})
q3 = body
check('Q-VAL-02b 显式小单溢价 10 → auto_applied=False',
      st == 201
      and Decimal((body.get('labor') or {}).get('small_lot_addon')) == 10
      and (body.get('labor') or {}).get('addon_rule', {}).get('auto_applied') is False,
      f'{st} {body}')

# Q-VAL-03 倍率超界 warning 不拦截
st, body = api('POST', LIST, T_owner, CID,
               {'style': style1.id, 'qty': 100, 'labor': {'rate': '3.5'}})
q_hi = body
check('Q-VAL-03a 倍率 3.5 → 201 + rate_warning=True（高于）',
      st == 201 and (body.get('labor') or {}).get('rate_warning') is True
      and '高于' in ((body.get('labor') or {}).get('rate_warning_msg') or ''),
      f'{st} {body}')
st, body = api('POST', LIST, T_owner, CID,
               {'style': style1.id, 'qty': 100, 'labor': {'rate': '1.5'}})
check('Q-VAL-03b 倍率 1.5 → warning（低于）不拦截',
      st == 201 and (body.get('labor') or {}).get('rate_warning') is True
      and '低于' in ((body.get('labor') or {}).get('rate_warning_msg') or ''),
      f'{st} {body}')
st, body = api('POST', LIST, T_owner, CID,
               {'style': style1.id, 'qty': 100, 'labor': {'rate': '2.0'}})
check('Q-VAL-03c 倍率 2.0 区间内 → 无 warning',
      st == 201 and (body.get('labor') or {}).get('rate_warning') is False,
      f'{st} {body}')

# Q-VAL-04 bom_item 带出与行内矛盾拦截
st, body = api('POST', LIST, T_owner, CID,
               {'style': style1.id, 'qty': 100,
                'materials': [{'bom_item': bom_item1.id,
                               'quote_unit_usage': '0.3', 'price': '5'}]})
check('Q-VAL-04a 只传 bom_item → material 自动带出（mat2）',
      st == 201 and (body.get('materials') or [{}])[0].get('material') == mat2.id,
      f'{st} {body}')
st, body = api('POST', LIST, T_owner, CID,
               {'style': style1.id, 'qty': 100,
                'materials': [{'material': mat1.id, 'bom_item': bom_item1.id,
                               'quote_unit_usage': '0.3', 'price': '5'}]})
check('Q-VAL-04b material 与 bom_item 物料矛盾 → 400',
      st == 400, f'{st} {body}')

# Q-VAL-05 数值防御：qty<=0 / usage<=0 / 负单价 / 跨厂物料
st, body = api('POST', LIST, T_owner, CID, {'style': style1.id, 'qty': 0})
check('Q-VAL-05a qty=0 → 400', st == 400, f'{st} {body}')
st, body = api('POST', LIST, T_owner, CID,
               {'style': style1.id, 'qty': 10,
                'materials': [{'material': mat1.id,
                               'quote_unit_usage': '0', 'price': '5'}]})
check('Q-VAL-05b 单耗 0 → 400', st == 400, f'{st} {body}')
st, body = api('POST', LIST, T_owner, CID,
               {'style': style1.id, 'qty': 10,
                'materials': [{'material': mat1.id,
                               'quote_unit_usage': '1', 'price': '-1'}]})
check('Q-VAL-05c 负单价 → 400', st == 400, f'{st} {body}')
st, body = api('POST', LIST, T_owner_b, CID_B,
               {'style': style_b.id, 'qty': 10,
                'materials': [{'material': mat1.id,
                               'quote_unit_usage': '1', 'price': '5'}]})
check('Q-VAL-05d 跨厂物料 → 400', st == 400, f'{st} {body}')
st, body = api('POST', LIST, T_owner, CID,
               {'style': style_b.id, 'qty': 10})
check('Q-VAL-05e 跨厂款式 → 400', st == 400, f'{st} {body}')

# ================= Q-NO 自动号 =================
check('Q-NO-19a 同日顺延 -002/-003',
      q2.get('quote_no') == f'Q{today:%Y%m%d}-002'
      and q3.get('quote_no') == f'Q{today:%Y%m%d}-003',
      f"{q2.get('quote_no')}/{q3.get('quote_no')}")
st, body = api('DELETE', f"{LIST}/{q3['id']}", T_owner, CID)
st, body = api('POST', LIST, T_owner, CID, {'style': style1.id, 'qty': 100})
q4 = body
check('Q-NO-19b 软删后流水不复用 → -008',
      st == 201 and q4.get('quote_no') == f'Q{today:%Y%m%d}-008',
      f'{st} {body}')

# ================= Q-API 列表/更新/流转 =================
st, body = api('GET', LIST + f'?status=draft&style_id={style1.id}', T_owner, CID)
ids = [r['id'] for r in body.get('results', body if isinstance(body, list) else [])]
check('Q-API-06a 列表过滤 status+style_id',
      st == 200 and q1['id'] in ids and q2['id'] in ids, f'{st} {body}')
st, body = api('GET', LIST + '?keyword=报价测试款A', T_owner, CID)
ids_kw = [r['id'] for r in body.get('results', [])]
check('Q-API-06b keyword 命中款式名', q1['id'] in ids_kw, f'{st} {body}')
st, body = api('GET', LIST + '?status=won', T_owner, CID)
check('Q-API-06c status=won 现为空', st == 200 and body.get('results') == [],
      f'{st} {body}')
st, body = api('GET', LIST + '?status=bogus', T_owner, CID)
check('Q-API-06d 非法 status → 400', st == 400, f'{st} {body}')

# Q-API-07 PUT：改倍率重算 + materials 全量替换
st, body = api('PUT', f"{LIST}/{q1['id']}", T_owner, CID,
               {'labor': {'rate': '2.0'}})
labor1 = body.get('labor') or {}
check('Q-API-07a PUT 改倍率 2.0 → labor_total 重算 4.16 且无 warning',
      st == 200
      and Decimal(labor1.get('labor_total')) == Decimal('4.16')
      and labor1.get('rate_warning') is False, f'{st} {body}')
st, body = api('PUT', f"{LIST}/{q1['id']}", T_owner, CID,
               {'materials': [{'material': mat1.id,
                               'quote_unit_usage': '0.5', 'price': '40'}]})
check('Q-API-07b PUT materials 全量替换（1 行）→ 材料合计 4000',
      st == 200 and len(body.get('materials') or []) == 1
      and Decimal(body.get('material_total')) == Decimal('4000.00')
      and Decimal(body.get('total_price')) == Decimal('4004.16'),
      f'{st} {body}')

# Q-API-08 mark-status 流转
st, body = api('POST', f"{LIST}/{q1['id']}/mark-status", T_owner, CID,
               {'status': 'sent'})
check('Q-API-08a draft→sent 200', st == 200 and body.get('status') == 'sent',
      f'{st} {body}')
st, body = api('POST', f"{LIST}/{q1['id']}/mark-status", T_owner, CID,
               {'status': 'sent'})
check('Q-API-08b 同状态 → 400', st == 400, f'{st} {body}')
st, body = api('POST', f"{LIST}/{q1['id']}/mark-status", T_owner, CID,
               {'status': 'draft'})
check('Q-API-08c 目标 draft 非法 → 400', st == 400, f'{st} {body}')
st, body = api('POST', f"{LIST}/{q1['id']}/mark-status", T_owner, CID,
               {'status': 'sent'})
check('Q-API-08d sent→sent 400（流转矩阵不含自环）', st == 400, f'{st} {body}')
# won 关联订单（先建订单）
order1 = Order.objects.create(company=company, order_no='QTA-001', style=style1)
OrderItem.objects.create(order=order1, color='红', size='M', qty=100,
                         price=Decimal('10'))
st, body = api('POST', f"{LIST}/{q1['id']}/mark-status", T_owner, CID,
               {'status': 'won', 'order_id': order1.id})
check('Q-API-08e sent→won 带 order_id 关联 200',
      st == 200 and body.get('status') == 'won'
      and (body.get('order')) == order1.id, f'{st} {body}')
st, body = api('POST', f"{LIST}/{q1['id']}/mark-status", T_owner, CID,
               {'status': 'lost'})
check('Q-API-08f won→lost 终态回退 → 400', st == 400, f'{st} {body}')
st, body = api('POST', f"{LIST}/{q2['id']}/mark-status", T_owner, CID,
               {'status': 'sent'})
st, body = api('POST', f"{LIST}/{q2['id']}/mark-status", T_owner, CID,
               {'status': 'lost'})
check('Q-API-08g sent→lost 200', st == 200 and body.get('status') == 'lost',
      f'{st} {body}')
st, body = api('POST', f"{LIST}/{q2['id']}/mark-status", T_owner, CID,
               {'status': 'sent'})
check('Q-API-08h lost→sent 终态回退 → 400', st == 400, f'{st} {body}')
st, body = api('POST', f"{LIST}/{q2['id']}/mark-status", T_owner, CID,
               {'status': 'won', 'order_id': order_b.id})
check('Q-API-08i won 带跨厂 order_id → 400', st == 400, f'{st} {body}')
st, body = api('POST', f"{LIST}/{q2['id']}/mark-status", T_owner, CID,
               {'status': 'won', 'order_id': 99999999})
check('Q-API-08j won 带不存在 order_id → 400', st == 400, f'{st} {body}')
st, body = api('POST', f"{LIST}/{q4['id']}/mark-status", T_owner, CID,
               {'status': 'sent', 'order_id': order1.id})
check('Q-API-08k 非 won 场景带 order_id → 400', st == 400, f'{st} {body}')

# won 后补关联（INT 2026-10-24 修缺口 C：won→won 带 order_id 允许补挂/改挂）
st, body = api('POST', f"{LIST}/{q1['id']}/mark-status", T_owner, CID,
               {'status': 'won'})
check('Q-API-08l won→won 无 order_id → 400（状态未变化）', st == 400, f'{st} {body}')
order1b = Order.objects.create(company=company, order_no='QTA-001B', style=style1)
st, body = api('POST', f"{LIST}/{q1['id']}/mark-status", T_owner, CID,
               {'status': 'won', 'order_id': order1b.id})
check('Q-API-08m won→won 带 order_id 改挂 200',
      st == 200 and body.get('order') == order1b.id, f'{st} {body}')
st, body = api('POST', f"{LIST}/{q1['id']}/mark-status", T_owner, CID,
               {'status': 'won', 'order_id': order_b.id})
check('Q-API-08n won→won 跨厂 order_id → 400', st == 400, f'{st} {body}')

# ================= Q-BF 回填对比（D3，与 OrderCost 对拍） =================
st, body = api('GET', f"{LIST}/{q4['id']}/backfill-compare", T_owner, CID)
check('Q-BF-09a 未关联订单 → actual/diff null + 提示 won',
      st == 200 and body.get('actual') is None and body.get('diff') is None
      and 'won' in (body.get('message') or ''), f'{st} {body}')

# won 关联订单但未重算成本（q4：08k 非法尝试后保持 draft，走 sent→won）
order2 = Order.objects.create(company=company, order_no='QTA-002', style=style1)
st, body = api('POST', f"{LIST}/{q4['id']}/mark-status", T_owner, CID,
               {'status': 'sent'})
st, body = api('POST', f"{LIST}/{q4['id']}/mark-status", T_owner, CID,
               {'status': 'won', 'order_id': order2.id})
check('Q-BF-09b won 后补关联订单 200',
      st == 200 and body.get('order') == order2.id, f'{st} {body}')
st, body = api('GET', f"{LIST}/{q4['id']}/backfill-compare", T_owner, CID)
check('Q-BF-10 无成本行 → 提示「先重算成本」',
      st == 200 and body.get('actual') is None and '重算' in (body.get('message') or ''),
      f'{st} {body}')

# 对拍：报价 QB（q4 另建对照款）假设 vs OrderCost 实际
# 报价：qty=100，材料 0.25×40 → 假设材料 1000；工价 base=2.0 → 假设人工 2；
#       假设成本 1002；报价总额 = 1000 + 2×2.3×1.3 = 1005.98
p_c = Process.objects.create(company=company, name='QTA工序C', default_price=2,
                             is_fixed=True, sort=3)
style2 = Style.objects.create(company=company, style_no='QTA-S2', name='报价测试款B')
StyleProcess.objects.create(style=style2, process=p_c, price=Decimal('2'))
st, body = api('POST', LIST, T_owner, CID,
               {'style': style2.id, 'qty': 100,
                'materials': [{'material': mat1.id,
                               'quote_unit_usage': '0.25', 'price': '40'}]})
qb = body
st, body = api('POST', f"{LIST}/{qb['id']}/mark-status", T_owner, CID,
               {'status': 'won', 'order_id': order1.id})
check('Q-BF-11a 对拍报价单 won 关联 order1',
      st == 200 and body.get('order') == order1.id, f'{st} {body}')

# 实际侧：领料 20×5=100（确认）；登记 100×2=200（process 工）；收入 100×10=1000
bed1 = Production.objects.create(company=company, style=style2, bed_no='QTA-B1',
                                 total_qty=100, order=order1)
lot1 = Lot.objects.create(company=company, lot_no='QTA-L1', fabric_name='面料',
                          qty=100, unit='米', unit_cost=Decimal('5'))
from django.utils import timezone as tz
issue1 = MaterialIssue.objects.create(company=company, issue_no='QTA-I1',
                                      type=MaterialIssue.Type.ISSUE,
                                      issue_at=tz.now(),
                                      status=MaterialIssue.Status.CONFIRMED)
IssueItem.objects.create(issue=issue1, production=bed1, lot=lot1,
                         material=mat1, qty=Decimal('20'))
ticket1 = Ticket.objects.create(company=company, production=bed1, bed_no='QTA-B1',
                                bundle_no='1', color='红', size='M', qty=100,
                                qr_code='QTA-x1')
ProductionProcess.objects.create(production=bed1, process=p_c, price=Decimal('2'))
Record.objects.create(company=company, ticket=ticket1, process=p_c, user=w1,
                      qty=100, price=Decimal('2'))
oc = recompute(company, order1, user=owner)
check('Q-BF-11b OrderCost 实际侧：材料100/人工200/收入1000/总成本300',
      oc.material_cost == Decimal('100.00') and oc.labor_cost == Decimal('200.00')
      and oc.revenue == Decimal('1000.00') and oc.total_cost == Decimal('300.00'),
      f'{oc.material_cost}/{oc.labor_cost}/{oc.revenue}/{oc.total_cost}')

st, body = api('GET', f"{LIST}/{qb['id']}/backfill-compare", T_owner, CID)
assumed = body.get('assumed') or {}
actual = body.get('actual') or {}
diff = body.get('diff') or {}
check('Q-BF-11c 并排：假设侧 材料1000/成本1002/总额1005.98',
      Decimal(assumed.get('material')) == Decimal('1000.00')
      and Decimal(assumed.get('cost_assumed')) == Decimal('1002.00')
      and Decimal(assumed.get('total_price')) == Decimal('1005.98'),
      str(assumed))
check('Q-BF-11d 实际侧透传 OrderCost（含 is_stale）',
      Decimal(actual.get('material_cost')) == Decimal('100.00')
      and Decimal(actual.get('labor_cost')) == Decimal('200.00')
      and Decimal(actual.get('total_cost')) == Decimal('300.00')
      and Decimal(actual.get('revenue')) == Decimal('1000.00')
      and actual.get('is_stale') is False, str(actual))
check('Q-BF-11e 差额：材料-900/-90% 人工+198/+9900%',
      Decimal((diff.get('material') or {}).get('diff')) == Decimal('-900.00')
      and Decimal((diff.get('material') or {}).get('diff_rate')) == Decimal('-90.00')
      and Decimal((diff.get('labor') or {}).get('diff')) == Decimal('198.00')
      and Decimal((diff.get('labor') or {}).get('diff_rate')) == Decimal('9900.00'),
      str(diff))
check('Q-BF-11f 差额：总成本-702/-70.06% 收入-5.98/-0.59%',
      Decimal((diff.get('total') or {}).get('diff')) == Decimal('-702.00')
      and Decimal((diff.get('total') or {}).get('diff_rate')) == Decimal('-70.06')
      and Decimal((diff.get('revenue') or {}).get('diff')) == Decimal('-5.98')
      and Decimal((diff.get('revenue') or {}).get('diff_rate')) == Decimal('-0.59'),
      str(diff))

mark_orders_stale(company, [order1.id])
st, body = api('GET', f"{LIST}/{qb['id']}/backfill-compare", T_owner, CID)
check('Q-BF-12 is_stale → 软提醒「过期」不拦截（只读）',
      st == 200 and '过期' in (body.get('message') or '')
      and (body.get('actual') or {}).get('is_stale') is True, str(body.get('message')))

# ================= Q-PERM 权限 =================
st, _ = api('GET', LIST)
check('Q-PERM-13a 无 token → 401', st == 401, str(st))
st, _ = api('GET', LIST, T_plain, CID)
check('Q-PERM-13b 普工 GET → 403', st == 403, str(st))
st, _ = api('POST', LIST, T_plain, CID, {'style': style1.id, 'qty': 10})
check('Q-PERM-13c 普工 POST → 403', st == 403, str(st))
st, _ = api('POST', f"{LIST}/{q4['id']}/mark-status", T_view, CID,
            {'status': 'sent'})
check('Q-PERM-14a view-only 岗 mark-status → 403', st == 403, str(st))
st, _ = api('PUT', f"{LIST}/{q4['id']}", T_view, CID, {'remark': 'x'})
check('Q-PERM-14b view-only 岗 PUT → 403', st == 403, str(st))
st, _ = api('DELETE', f"{LIST}/{q4['id']}", T_view, CID)
check('Q-PERM-14c view-only 岗 DELETE → 403', st == 403, str(st))
st, body = api('GET', f"{LIST}/{q4['id']}/backfill-compare", T_plain, CID)
check('Q-PERM-14d 普工 backfill-compare → 403', st == 403, f'{st} {body}')
st, body = api('GET', LIST + '?keyword=x', T_legacy, CID)
check('Q-PERM-15a legacy 岗（production）GET → 403（零回归默认零授权）',
      st == 403, f'{st} {body}')
check('Q-PERM-16a 权限点注册 ALL_PERMISSION_KEYS',
      'quotation.view' in ALL_PERMISSION_KEYS
      and 'quotation.manage' in ALL_PERMISSION_KEYS, '')
check('Q-PERM-16b effective_perms 下发（管理岗两点/查看岗一点/旧岗不含）',
      set(effective_perms(post_perm)) >= {'quotation.view', 'quotation.manage'}
      and set(effective_perms(post_view)) == {'quotation.view'}
      and not {'quotation.view', 'quotation.manage'}
      & set(effective_perms(post_legacy)), '')

# ================= Q-ISO 跨厂隔离 / Q-NO 软删 =================
st, body = api('GET', LIST, T_owner_b, CID_B)
rows_b = body.get('results', [])
check('Q-ISO-17a B 厂列表不见 A 厂报价',
      st == 200 and all(r['id'] not in {q1['id'], q2['id'], q4['id'], qb['id']}
                        for r in rows_b), f'{st} {rows_b}')
st, body = api('GET', f"{LIST}/{q1['id']}", T_owner_b, CID_B)
check('Q-ISO-17b B 厂 GET A 厂详情 → 404', st == 404, str(st))
st, body = api('POST', f"{LIST}/{q1['id']}/mark-status", T_owner_b, CID_B,
               {'status': 'sent'})
check('Q-ISO-17c B 厂流转 A 厂单 → 404', st == 404, str(st))
st, body = api('GET', f"{LIST}/{q1['id']}/backfill-compare", T_owner_b, CID_B)
check('Q-ISO-17d B 厂回填对比 A 厂单 → 404', st == 404, str(st))

st, body = api('DELETE', f"{LIST}/{qb['id']}", T_owner, CID)
st, body = api('GET', LIST + '?keyword=报价测试款B', T_owner, CID)
ids_after = [r['id'] for r in body.get('results', [])]
check('Q-NO-20a 软删后列表不显', qb['id'] not in ids_after, str(ids_after))
st, body = api('GET', f"{LIST}/{qb['id']}", T_owner, CID)
check('Q-NO-20b 软删后详情 → 404', st == 404, str(st))

# ================= Q-COV 覆盖补齐（校验分支/边界全走通） =================
# —— 参数类型不合法（int() 失败分支）——
st, _ = api('POST', LIST, T_owner, CID, {'style': 'abc', 'qty': 10})
check('Q-COV-21a style 非整数 → 400', st == 400, str(st))
st, _ = api('POST', LIST, T_owner, CID,
            {'style': style1.id, 'qty': 10, 'customer': 'abc'})
check('Q-COV-21b customer 非整数 → 400', st == 400, str(st))
st, body = api('POST', LIST, T_owner, CID,
               {'style': style1.id, 'qty': 10, 'customer': 99999999})
check('Q-COV-21c customer 不存在 → 400', st == 400, str(st))
st, _ = api('POST', LIST, T_owner, CID, {'style': style1.id, 'qty': 'abc'})
check('Q-COV-21d qty 非整数 → 400', st == 400, str(st))
st, _ = api('POST', LIST, T_owner, CID, {'style': style1.id})
check('Q-COV-21e qty 缺失 → 400', st == 400, str(st))
st, _ = api('POST', LIST, T_owner, CID,
            {'style': style1.id, 'qty': 10, 'materials': 'x'})
check('Q-COV-22a materials 非数组 → 400', st == 400, str(st))
st, _ = api('POST', LIST, T_owner, CID,
            {'style': style1.id, 'qty': 10, 'materials': ['x']})
check('Q-COV-22b 用料行非对象 → 400', st == 400, str(st))
st, _ = api('POST', LIST, T_owner, CID,
            {'style': style1.id, 'qty': 10,
             'materials': [{'material': 'abc', 'quote_unit_usage': '1',
                            'price': '1'}]})
check('Q-COV-22c material 非整数 → 400', st == 400, str(st))
st, _ = api('POST', LIST, T_owner, CID,
            {'style': style1.id, 'qty': 10,
             'materials': [{'bom_item': 'abc', 'quote_unit_usage': '1',
                            'price': '1'}]})
check('Q-COV-22d bom_item 非整数 → 400', st == 400, str(st))
st, _ = api('POST', LIST, T_owner, CID,
            {'style': style1.id, 'qty': 10,
             'materials': [{'quote_unit_usage': '1', 'price': '1'}]})
check('Q-COV-22e 行缺物料 → 400', st == 400, str(st))
st, _ = api('POST', LIST, T_owner, CID,
            {'style': style1.id, 'qty': 10,
             'materials': [{'material': mat1.id, 'quote_unit_usage': 'x',
                            'price': '1'}]})
check('Q-COV-22f 单耗非数字 → 400', st == 400, str(st))
st, _ = api('POST', LIST, T_owner, CID,
            {'style': style1.id, 'qty': 10,
             'materials': [{'material': mat1.id, 'price': '1'}]})
check('Q-COV-22g 单耗缺失 → 400', st == 400, str(st))
st, _ = api('POST', LIST, T_owner, CID,
            {'style': style1.id, 'qty': 10,
             'materials': [{'material': mat1.id, 'quote_unit_usage': '1',
                            'price': 'x'}]})
check('Q-COV-22h 单价非数字 → 400', st == 400, str(st))
st, _ = api('POST', LIST, T_owner, CID,
            {'style': style1.id, 'qty': 10,
             'materials': [{'material': mat1.id, 'quote_unit_usage': '1'}]})
check('Q-COV-22i 单价缺失 → 400', st == 400, str(st))

# —— labor 行校验分支 ——
st, _ = api('POST', LIST, T_owner, CID,
            {'style': style1.id, 'qty': 10, 'labor': 'x'})
check('Q-COV-23a labor 非对象 → 400', st == 400, str(st))
st, _ = api('POST', LIST, T_owner, CID,
            {'style': style1.id, 'qty': 10, 'labor': {'labor_base': '-1'}})
check('Q-COV-23b labor_base 负数 → 400', st == 400, str(st))
st, _ = api('POST', LIST, T_owner, CID,
            {'style': style1.id, 'qty': 10, 'labor': {'labor_base': 'x'}})
check('Q-COV-23c labor_base 非数字 → 400', st == 400, str(st))
st, _ = api('POST', LIST, T_owner, CID,
            {'style': style1.id, 'qty': 10, 'labor': {'rate': '0'}})
check('Q-COV-23d rate<=0 → 400', st == 400, str(st))
st, _ = api('POST', LIST, T_owner, CID,
            {'style': style1.id, 'qty': 10, 'labor': {'rate': 'x'}})
check('Q-COV-23e rate 非数字 → 400', st == 400, str(st))
st, _ = api('POST', LIST, T_owner, CID,
            {'style': style1.id, 'qty': 10, 'labor': {'addon_amount': '-1'}})
check('Q-COV-23f 附加费负数 → 400', st == 400, str(st))
st, _ = api('POST', LIST, T_owner, CID,
            {'style': style1.id, 'qty': 10, 'labor': {'addon_amount': 'x'}})
check('Q-COV-23g 附加费非数字 → 400', st == 400, str(st))
st, _ = api('POST', LIST, T_owner, CID,
            {'style': style1.id, 'qty': 10,
             'labor': {'small_lot_addon': '150'}})
check('Q-COV-23h 小单溢价>100 → 400', st == 400, str(st))
st, _ = api('POST', LIST, T_owner, CID,
            {'style': style1.id, 'qty': 10, 'labor': {'addon_rule': 'x'}})
check('Q-COV-23i addon_rule 非对象 → 400', st == 400, str(st))
# addon_rule 合法自定义对象透传
st, body = api('POST', LIST, T_owner, CID,
               {'style': style1.id, 'qty': 10,
                'labor': {'addon_rule': {'threshold': 500, 'addon_pct': 20,
                                         'auto_applied': False}}})
check('Q-COV-23j addon_rule 自定义对象透传',
      st == 201 and (body.get('labor') or {}).get('addon_rule', {}).get('threshold') == 500,
      f'{st} {body}')
q5 = body
# 附加费正向：addon_amount 计入 labor_total（1.6×2.3×1.3+200=204.78，qty=10<300 触发小单溢价）
st, body = api('POST', LIST, T_owner, CID,
               {'style': style1.id, 'qty': 10,
                'labor': {'addon_amount': '200'}})
check('Q-COV-23k addon_amount=200 计入 labor_total=203.68',
      st == 201
      and Decimal((body.get('labor') or {}).get('labor_total')) == Decimal('204.78'),
      f'{st} {body}')

# —— mark-status order_id 类型不合法分支 + 404 id 类型 ——
st, _ = api('POST', f"{LIST}/{q5['id']}/mark-status", T_owner, CID,
            {'status': 'sent'})
st, _ = api('POST', f"{LIST}/{q5['id']}/mark-status", T_owner, CID,
            {'status': 'won', 'order_id': 'abc'})
check('Q-COV-24a order_id 非整数 → 400', st == 400, str(st))
st, _ = api('GET', LIST + '/abc', T_owner, CID)
check('Q-COV-24b 详情 id 非整数 → 404（URL 转换器）', st == 404, str(st))

# —— 列表 customer_id 过滤 + PUT 仅 remark（不触发子表重算分支）——
from apps.catalog.models import Customer as Cust
cust = Cust.all_objects.filter(company=company, customer_no='QTA-C1').first()
if cust is None:
    cust = Cust.objects.create(company=company, customer_no='QTA-C1',
                               name='报价客户甲')
else:
    Cust.objects.filter(pk=cust.pk).update(is_deleted=False, name='报价客户甲')
    cust.refresh_from_db()
st, body = api('POST', LIST, T_owner, CID,
               {'style': style1.id, 'qty': 10, 'customer': cust.id})
q6 = body
st, body = api('GET', LIST + f'?customer_id={cust.id}', T_owner, CID)
check('Q-COV-25a customer_id 过滤命中',
      [r['id'] for r in body.get('results', [])] == [q6['id']], str(body))
st, body = api('PUT', f"{LIST}/{q6['id']}", T_owner, CID, {'remark': '仅改备注'})
check('Q-COV-25b PUT 仅 remark → 子表不动（materials/labor 保留）',
      st == 200 and len(body.get('materials') or []) == 0
      and (body.get('labor') or {}).get('rate') is not None
      and body.get('remark') == '仅改备注', f'{st} {body}')
st, body = api('PUT', f"{LIST}/{q6['id']}", T_owner, CID,
               {'labor': {'addon_amount': '50'}})
check('Q-COV-25c PUT 仅 labor → 保留既有 materials 重算',
      st == 200 and Decimal(body.get('total_price')) > 0, f'{st} {body}')

# —— models __str__ / labor_base 显式合法值（elif<0 False 分支）——
st, body = api('POST', LIST, T_owner, CID,
               {'style': style1.id, 'qty': 10, 'labor': {'labor_base': '2'}})
check('Q-COV-26a labor_base 显式合法值 2 → 带出不触发',
      st == 201
      and Decimal((body.get('labor') or {}).get('labor_base')) == Decimal('2'),
      f'{st} {body}')
check('Q-COV-26b 模型 __str__ 可渲染',
      bool(str(Quotation.objects.get(pk=q6['id'])))
      and bool(str(Quotation.objects.get(pk=q6['id']).labor))
      and bool(str(QuoteMaterial.objects.filter(quotation=q1['id']).first())),
      '')

# ================= W-Q-RELINK 报价→转单回写全链（测试设计 2026-11-02 新补） =================
# F-03 口径：前端转单 = orders?quote= 预填 → createOrder → markQuotationStatus(won+order_id)。
# 后端可见断言：报价 won 后 detail 回带 order/order_no，列表回显转单来源（前端跳转依赖）。
st, body = api('POST', LIST, T_owner, CID,
               {'style': style1.id, 'qty': 50,
                'materials': [{'material': mat1.id, 'quote_unit_usage': '0.3', 'price': '30'}]})
check('W-Q-RELINK-01 转单源报价创建 201', st == 201 and body.get('id'),
      f'{st} {str(body)[:120]}')
qr = body
st, body = api('POST', f"{LIST}/{qr['id']}/mark-status", T_owner, CID, {'status': 'sent'})
check('W-Q-RELINK-02 报价发送 200', st == 200 and body.get('status') == 'sent', f'{st} {body}')
order_r = Order.objects.create(company=company, order_no='QTA-RELINK', style=style1)
OrderItem.objects.create(order=order_r, color='黑', size='L', qty=50, price=Decimal('12'))
st, body = api('POST', f"{LIST}/{qr['id']}/mark-status", T_owner, CID,
               {'status': 'won', 'order_id': order_r.id})
check('W-Q-RELINK-03 转单回写 won+order_id 200',
      st == 200 and body.get('order') == order_r.id, f'{st} {body}')
st, body = api('GET', f"{LIST}/{qr['id']}", T_owner, CID)
check('W-Q-RELINK-04 detail 回带 order/order_no',
      st == 200 and body.get('order') == order_r.id
      and body.get('order_no') == 'QTA-RELINK', f'{st} {str(body)[:150]}')
st, body = api('GET', f"{LIST}?keyword={qr['quote_no']}", T_owner, CID)
check('W-Q-RELINK-05 转单后报价按单号检索可见（won 不出列表）',
      st == 200 and any(x.get('id') == qr['id'] for x in (body.get('results') or [])),
      f'{st} {str(body)[:150]}')

print(f'\n==== 报价域套件：{len(PASS)} pass / {len(FAIL)} fail ====')
if FAIL:
    print('FAIL 列表:', FAIL)
    raise SystemExit(1)
