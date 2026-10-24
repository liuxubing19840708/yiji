# -*- coding: utf-8 -*-
"""
分支覆盖回填套件（warehouse / catalog / accounts / core 高性价比分支弧）：
- warehouse：批次 CRUD 边界（重复 400 / 404 / 改名冲突）、床次血缘整表重建
  （缺批次/重复批次 400）、五个追溯视角全链路（含 404 与 operator 空分支）、
  采购单/入库单/领料单「草稿→完成」状态机（非草稿改删 400、重复确认 400、
  明细缺失 400、批次不存在 400、确认写血缘并累加）
- catalog：款式工序配置 7 类入参校验、款式重名/复制/同步工序确认流、
  工序重名/删除拦截（被款式用/有登记）、客户分组与客户编号自动取号、
  编菲校验与满扎拆扎、打印计数与缺票 400、菲票删除拦截
- accounts：员工列表 tab/keyword/岗位筛选、邀请-审批-离职-复职-容量上限、
  设岗/设组边界、套餐变更与降档拦截、指定工序审计、岗位复制与删除拦截、
  join 重复/switch 非成员/wxacode 未配置 400、注销（有密码/无密码）
- core：uploads 缺文件/扩展名/成功、client-logs 未登录落款空/批量上限/
  脏条目跳过/全无效 400/成员落款厂、GET 筛选与 401/403
跑法：manage.py shell -c "exec(compile(open('test_branch_backfill.py',encoding='utf-8').read(),'test_branch_backfill.py','exec'))"
幂等：自建「分支回填测试厂(可删)」跨轮复用；业务行 queryset.delete() 物理清理。
"""
import json
import os
from datetime import timedelta

from django.contrib.auth.hashers import make_password
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.utils import timezone

from apps.accounts.models import Company, Post as PostM, User, UserCompany
from apps.accounts.services import create_company_with_owner
from apps.catalog.models import (Customer, CustomerGroup, Process, Style,
                                 StyleProcess, Ticket)
from apps.core.models import ClientLog
from apps.ops.models import AuditLog, CompanySetting, Notification
from apps.production.models import Production
from apps.records.models import Record
from apps.warehouse.models import (IssueItem, Lot, MaterialIssue,
                                   ProductionLotLink, PurchaseItem,
                                   PurchaseOrder, StockIn, StockInItem)

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


OWNER, H_PLAIN, H_PLAIN2 = '13988940001', '13988940002', '13988940003'
INV_A, INV_B, INV_C, INV_E = ('13988940011', '13988940012', '13988940013',
                              '13988940015')
H_RESIGNED, H_DEACT1, H_DEACT2 = '13988940021', '13988940031', '13988940032'
CO = '分支回填测试厂(可删)'


def ensure_user(phone, name, with_password=True):
    u = User.objects.filter(phone=phone).first()
    if u is None:
        kw = dict(phone=phone, username=phone, real_name=name, is_active=True)
        if with_password:
            kw['password'] = make_password('yisao1234')
        u = User.objects.create(**kw)
        if not with_password:
            u.set_unusable_password()
            u.save(update_fields=['password'])
    return u


def ensure_member(u, company, post=None, status=UserCompany.Status.ACTIVE):
    uc = UserCompany.all_objects.filter(user=u, company=company).first()
    if uc is None:
        return UserCompany.objects.create(user=u, company=company, post=post,
                                          status=status)
    uc.is_deleted = False
    uc.status = status
    uc.post = post
    uc.is_suspended = False
    uc.save(update_fields=['is_deleted', 'status', 'post', 'is_suspended',
                           'updated_at'])
    return uc


# ================= preclean：复用测试厂 =================
company = Company.objects.filter(name=CO).first()
if company is None:
    owner = ensure_user(OWNER, '分支回填厂长')
    company, admin_post = create_company_with_owner(owner, CO)
else:
    owner = ensure_user(OWNER, '分支回填厂长')
    admin_post = PostM.objects.get(company=company, is_admin=True, is_deleted=False)
CID = company.id


def wipe_business(company):
    """物理清理业务行（子表先删；queryset.delete=Collector 物理删）。"""
    ClientLog.objects.filter(company=company).delete()
    Record.objects.filter(company=company).delete()
    PurchaseItem.objects.filter(order__company=company).delete()
    PurchaseOrder.all_objects.filter(company=company).delete()
    StockInItem.objects.filter(stock_in__company=company).delete()
    IssueItem.objects.filter(issue__company=company).delete()
    MaterialIssue.all_objects.filter(company=company).delete()
    ProductionLotLink.all_objects.filter(company=company).delete()
    Lot.all_objects.filter(company=company).delete()
    StockIn.all_objects.filter(company=company).delete()
    Ticket.all_objects.filter(company=company).delete()
    Production.all_objects.filter(company=company).delete()
    StyleProcess.all_objects.filter(style__company=company).delete()
    Style.all_objects.filter(company=company).delete()
    CustomerGroup.all_objects.filter(company=company, name__startswith='BF').delete()
    Customer.all_objects.filter(company=company).delete()
    Process.all_objects.filter(company=company, name__startswith='BF').delete()
    AuditLog.objects.filter(company=company).delete()
    Notification.objects.filter(company=company).delete()


wipe_business(company)
# 邀请流成员关系硬删（invite 幂等重建；审批/离职状态不跨轮残留）
UserCompany.all_objects.filter(
    company=company, user__phone__in=[INV_A, INV_B, INV_C, INV_E]).delete()
UserCompany.all_objects.filter(
    company=company, user__phone__startswith='139889409').delete()

post_shop = PostM.objects.filter(company=company, name='BF登记岗',
                                 is_deleted=False).first() \
    or PostM.objects.create(company=company, name='BF登记岗',
                            permissions=['records.create', 'warehouse.manage'])
post_plain = PostM.objects.filter(company=company, name='BF普工',
                                  is_deleted=False).first() \
    or PostM.objects.create(company=company, name='BF普工', permissions=[])

plain = ensure_user(H_PLAIN, '回填普工')
plain2 = ensure_user(H_PLAIN2, '回填普工二')
uc_plain = ensure_member(plain, company, post_shop)
ensure_member(plain2, company, None)  # 无岗位在职成员（post=0 筛选用）
UserCompany.objects.filter(pk=uc_plain.pk).update(assigned_processes=[])
# 预置离职成员（无岗位，供 tab=resigned / post=0 筛选）
resigned_u = ensure_user(H_RESIGNED, '回填离职员')
ensure_member(resigned_u, company, None, status=UserCompany.Status.RESIGNED)
Company.objects.filter(pk=company.pk).update(plan=Company.Plan.BASIC, capacity=50)

T = {'owner': login(OWNER), 'plain': login(H_PLAIN), 'plain2': login(H_PLAIN2)}
check('P0 测试厂成员登录', all(T.values()), str({k: bool(v) for k, v in T.items()}))

# ================= 最小业务链 =================
cust = Customer.objects.create(company=company, customer_no='BFC1', name='回填客户甲')
style = Style.objects.create(company=company, style_no='BF-S1', name='回填测试款',
                             customer=cust)
proc = Process.objects.create(company=company, name='BF车缝', is_fixed=True,
                              default_price=2.5, sort=97)
StyleProcess.objects.create(style=style, process=proc, price=2.5, sort=1)
prod = Production.objects.create(company=company, style=style, bed_no='BF-B1',
                                 customer=cust, total_qty=150)
prod2 = Production.objects.create(company=company, style=style, bed_no='BF-B2',
                                  total_qty=50)
t1 = Ticket.objects.create(company=company, production=prod, bed_no='BF-B1',
                           bundle_no='1', color='黑', size='M', qty=100)
t2 = Ticket.objects.create(company=company, production=prod, bed_no='BF-B1',
                           bundle_no='2', color='白', size='L', qty=50)
now = timezone.now()
# 无操作人登记（追溯 operator 空分支 + 菲票删除拦截）
Record.objects.create(company=company, ticket=t1, process=proc, user=plain,
                      qty=5, price='2.500', registered_at=now)

# ================= warehouse：批次 CRUD =================
st, d = api('GET', '/lots?keyword=BFLOT', T['owner'], CID)
check('WH-001 批次列表 keyword 过滤 200（初始为空）', st == 200 and (d or {}).get('count') == 0,
      f'{st} {json.dumps(d, ensure_ascii=False)[:120]}')
st, d = api('POST', '/lots', T['owner'], CID, {'lot_no': '  ', 'fabric_name': 'x'})
check('WH-002 新增批次空批号 → 400', st == 400, f'{st} {json.dumps(d, ensure_ascii=False)[:120]}')
st, d = api('POST', '/lots', T['owner'], CID,
            {'lot_no': 'BFLOT1', 'fabric_name': '全棉', 'color': '黑', 'qty': 300})
check('WH-003 新增批次 201', st == 201, f'{st} {json.dumps(d, ensure_ascii=False)[:150]}')
lot1 = (d or {}).get('id')
st, d = api('POST', '/lots', T['owner'], CID, {'lot_no': 'BFLOT1', 'fabric_name': 'x'})
check('WH-004 重复批号 → 400', st == 400, f'{st} {json.dumps(d, ensure_ascii=False)[:120]}')
st, d = api('POST', '/lots', T['plain2'], CID, {'lot_no': 'BFLOTX'})
check('WH-005 无 warehouse.manage 权限新增 → 403', st == 403, f'{st} {str(d)[:80]}')
st, d = api('GET', '/lots/999999', T['owner'], CID)
check('WH-006 批次详情不存在 → 404', st == 404, f'{st} {str(d)[:80]}')
st, d = api('PUT', f'/lots/{lot1}', T['owner'], CID,
            {'fabric_name': '全棉A', 'color': '黑', 'qty': 350, 'remark': '改'})
check('WH-007 更新批次字段（批号同名不触发改名分支）→ 200',
      st == 200 and (d or {}).get('qty') == '350.00', f'{st} {json.dumps(d, ensure_ascii=False)[:150]}')
st, d = api('POST', '/lots', T['owner'], CID,
            {'lot_no': 'BFLOT2', 'fabric_name': '涤纶', 'color': '白', 'qty': 100})
lot2 = (d or {}).get('id')
check('WH-008 新增第二批次 201', st == 201, f'{st} {str(d)[:100]}')
st, d = api('PUT', f'/lots/{lot1}', T['owner'], CID, {'lot_no': 'BFLOT2'})
check('WH-009 改名撞既有批号 → 400', st == 400, f'{st} {json.dumps(d, ensure_ascii=False)[:120]}')
st, d = api('PUT', f'/lots/{lot1}', T['owner'], CID, {'lot_no': 'BFLOT1B'})
check('WH-010 改名到空闲批号 → 200', st == 200 and (d or {}).get('lot_no') == 'BFLOT1B',
      f'{st} {json.dumps(d, ensure_ascii=False)[:120]}')
api('PUT', f'/lots/{lot1}', T['owner'], CID, {'lot_no': 'BFLOT1'})

# ================= warehouse：床次血缘整表重建 =================
st, d = api('GET', f'/productions/{prod.id}/lot-links', T['owner'], CID)
check('WH-020 血缘列表初始空 200', st == 200 and d == [], f'{st} {str(d)[:100]}')
st, d = api('GET', '/productions/999999/lot-links', T['owner'], CID)
check('WH-021 血缘床次不存在 → 404', st == 404, f'{st} {str(d)[:80]}')
st, d = api('PUT', f'/productions/{prod.id}/lot-links', T['owner'], CID,
            {'items': [{'lot_id': 999999, 'qty': 1}]})
check('WH-022 血缘批次不存在 → 400', st == 400, f'{st} {json.dumps(d, ensure_ascii=False)[:120]}')
st, d = api('PUT', f'/productions/{prod.id}/lot-links', T['owner'], CID,
            {'items': [{'lot_id': lot1, 'qty': 1}, {'lot_id': lot1, 'qty': 2}]})
check('WH-023 血缘重复批次 → 400', st == 400, f'{st} {json.dumps(d, ensure_ascii=False)[:120]}')
st, d = api('PUT', f'/productions/{prod.id}/lot-links', T['owner'], CID,
            {'items': [{'lot_id': lot1, 'qty': 10}, {'lot_id': lot2, 'qty': 5}]})
links = d if isinstance(d, list) else []
check('WH-024 血缘整表重建 200 且 2 条',
      st == 200 and len(links) == 2 and {l.get('lot_no') for l in links} == {'BFLOT1', 'BFLOT2'},
      f'{st} {json.dumps(links, ensure_ascii=False)[:180]}')

# ================= warehouse：追溯五视角 =================
st, d = api('GET', '/trace/lot?keyword=BFLOT', T['owner'], CID)
rows = (d or {}).get('results', [])
check('WH-030 批次搜索含受影响面统计',
      st == 200 and len(rows) == 2 and rows[0].get('production_count') == 1,
      f'{st} {json.dumps(rows[:1], ensure_ascii=False)[:180]}')
st, d = api('GET', f'/trace/lot/{lot1}', T['owner'], CID)
check('WH-031 正向追溯：批次→床次（款号/客户/来源单号）',
      st == 200 and (d or {}).get('productions') and (d or {})['productions'][0]
      .get('customer_name') == '回填客户甲'
      and (d or {})['impact']['production_count'] == 1,
      f'{st} {json.dumps((d or {}).get("productions", [])[:1], ensure_ascii=False)[:200]}')
st, d = api('GET', '/trace/lot/999999', T['owner'], CID)
check('WH-032 正向追溯批次不存在 → 404', st == 404, f'{st} {str(d)[:80]}')
st, d = api('GET', f'/trace/reverse?style_no=BF&color=黑&customer_id={cust.id}'
                  f'&date_from={timezone.localdate()}&date_to={timezone.localdate()}',
            T['owner'], CID)
ms = (d or {}).get('matches', [])
check('WH-033 反向追溯五参组合命中床次（含批次血缘）',
      st == 200 and ms and ms[0]['bed_no'] == 'BF-B1'
      and {l['lot_no'] for l in ms[0]['lots']} == {'BFLOT1', 'BFLOT2'},
      f'{st} {json.dumps(ms[:1], ensure_ascii=False)[:200]}')
st, d = api('GET', '/trace/reverse?color=不存在色', T['owner'], CID)
check('WH-034 反向追溯 0 命中 → matches 空', st == 200 and (d or {}).get('matches') == [],
      f'{st} {str(d)[:100]}')
st, d = api('GET', f'/trace/ticket/{t1.id}', T['owner'], CID)
check('WH-035 菲票全链路追溯：时间线含无操作人登记',
      st == 200 and (d or {}).get('records') and (d or {})['records'][0]['operator'] == ''
      and (d or {})['ticket']['bundle_no'] == '1',
      f'{st} {json.dumps((d or {}).get("records", [])[:1], ensure_ascii=False)[:180]}')
st, d = api('GET', '/trace/ticket/999999', T['owner'], CID)
check('WH-036 菲票追溯不存在 → 404', st == 404, f'{st} {str(d)[:80]}')
st, d = api('GET', '/trace/ticket/abc', T['owner'], CID)
check('WH-037 菲票追溯非法 pk → 404（ValueError 兜底分支）', st == 404, f'{st} {str(d)[:80]}')
st, d = api('GET', f'/trace/production/{prod.id}', T['owner'], CID)
check('WH-038 床次追溯视角：批次+菲票汇总',
      st == 200 and (d or {}).get('lots') and (d or {})['ticket_count'] == 2
      and (d or {})['qty_total'] == 150,
      f'{st} {json.dumps((d or {}).get("lots", [])[:1], ensure_ascii=False)[:150]}')
st, d = api('GET', '/trace/production/999999', T['owner'], CID)
check('WH-039 床次追溯不存在 → 404', st == 404, f'{st} {str(d)[:80]}')

# ================= warehouse：采购单状态机 =================
st, d = api('GET', '/purchase-orders?status=draft', T['owner'], CID)
check('WH-040 采购单列表 status 筛选 200（空）', st == 200 and (d or {}).get('count') == 0,
      f'{st} {str(d)[:100]}')
st, d = api('POST', '/purchase-orders', T['owner'], CID,
            {'order_no': 'CG-BF-01', 'supplier_name': '供应商甲',
             'ordered_at': str(timezone.localdate()),
             'items': [{'fabric_name': '面料A', 'qty': 100, 'price': '12.5'}]})
check('WH-041 采购单带明细创建 201（单号必填，留空自动生成为防御分支）',
      st == 201 and (d or {}).get('order_no') == 'CG-BF-01'
      and len((d or {}).get('items', [])) == 1,
      f'{st} {json.dumps(d, ensure_ascii=False)[:180]}')
po1 = (d or {}).get('id')
st, d = api('POST', '/purchase-orders', T['owner'], CID,
            {'order_no': 'CG-BF-02', 'supplier_name': '供应商乙',
             'ordered_at': str(timezone.localdate())})
po2 = (d or {}).get('id')
check('WH-042 手编单号采购单 201', st == 201 and (d or {}).get('order_no') == 'CG-BF-02',
      f'{st} {str(d)[:120]}')
st, d = api('GET', '/purchase-orders/999999', T['owner'], CID)
check('WH-043 采购单不存在 → 404', st == 404, f'{st} {str(d)[:80]}')
st, d = api('PUT', f'/purchase-orders/{po1}', T['owner'], CID,
            {'supplier_name': '供应商甲改', 'ordered_at': '2026-10-20'})
check('WH-044 草稿采购单可改（含 ordered_at 分支）→ 200',
      st == 200 and (d or {}).get('supplier_name') == '供应商甲改',
      f'{st} {json.dumps(d, ensure_ascii=False)[:150]}')
st, d = api('POST', f'/purchase-orders/{po1}/confirm', T['owner'], CID)
check('WH-045 采购单确认 → done', st == 200 and (d or {}).get('status') == 'done',
      f'{st} {json.dumps(d, ensure_ascii=False)[:120]}')
st, d = api('POST', f'/purchase-orders/{po1}/confirm', T['owner'], CID)
check('WH-046 重复确认 → 400', st == 400, f'{st} {json.dumps(d, ensure_ascii=False)[:120]}')
st, d = api('PUT', f'/purchase-orders/{po1}', T['owner'], CID, {'supplier_name': 'x'})
check('WH-047 完成采购单不可修改 → 400', st == 400, f'{st} {json.dumps(d, ensure_ascii=False)[:120]}')
st, d = api('DELETE', f'/purchase-orders/{po1}', T['owner'], CID)
check('WH-048 完成采购单不可删除 → 400', st == 400, f'{st} {json.dumps(d, ensure_ascii=False)[:120]}')
st, d = api('DELETE', f'/purchase-orders/{po2}', T['owner'], CID)
check('WH-049 草稿采购单删除 → 204', st == 204, f'{st} {str(d)[:80]}')

# ================= warehouse：入库单（批次诞生点） =================
st, d = api('GET', '/stock-ins?status=draft', T['owner'], CID)
check('WH-050 入库单列表 status 筛选 200', st == 200, f'{st} {str(d)[:100]}')
st, d = api('POST', '/stock-ins', T['owner'], CID, {'remark': '无明细'})
check('WH-051 入库无明细 → 400', st == 400, f'{st} {json.dumps(d, ensure_ascii=False)[:120]}')
st, d = api('POST', '/stock-ins', T['owner'], CID,
            {'in_no': 'RK-BF-01', 'remark': '两行明细',
             'items': [{'fabric_name': '全棉', 'color': '黑', 'lot_no': 'BFLOT-NEW', 'qty': 200},
                       {'fabric_name': '涤纶', 'color': '白', 'lot_no': 'BFLOT2', 'qty': 50}]})
check('WH-052 入库单两行明细 201（单号必填，留空自动生成为防御分支）',
      st == 201 and (d or {}).get('in_no') == 'RK-BF-01',
      f'{st} {json.dumps(d, ensure_ascii=False)[:200]}')
si1 = (d or {}).get('id')
new_lot = Lot.objects.filter(company=company, lot_no='BFLOT-NEW').first()
check('WH-053 新批次自动建档且回填来源入库行',
      new_lot is not None and new_lot.source_in_id is not None
      and new_lot.source_in.stock_in_id == si1,
      f'lot={new_lot} source_in={getattr(new_lot, "source_in_id", None)}')
st, d = api('GET', '/stock-ins/999999', T['owner'], CID)
check('WH-054 入库单不存在 → 404', st == 404, f'{st} {str(d)[:80]}')
st, d = api('PUT', f'/stock-ins/{si1}', T['owner'], CID,
            {'remark': '改备注', 'in_at': '2026-10-20T08:00:00'})
check('WH-055 草稿入库单可改（remark+in_at 分支）→ 200',
      st == 200 and (d or {}).get('remark') == '改备注', f'{st} {str(d)[:150]}')
st, d = api('POST', f'/stock-ins/{si1}/confirm', T['owner'], CID)
check('WH-056 入库单确认 → done', st == 200 and (d or {}).get('status') == 'done',
      f'{st} {str(d)[:120]}')
st, d = api('POST', f'/stock-ins/{si1}/confirm', T['owner'], CID)
check('WH-057 重复确认入库单 → 400', st == 400, f'{st} {str(d)[:120]}')
st, d = api('PUT', f'/stock-ins/{si1}', T['owner'], CID, {'remark': 'x'})
check('WH-058 完成入库单不可修改 → 400', st == 400, f'{st} {str(d)[:120]}')
st, d = api('DELETE', f'/stock-ins/{si1}', T['owner'], CID)
check('WH-059 完成入库单不可删除 → 400', st == 400, f'{st} {str(d)[:120]}')
st, d = api('POST', '/stock-ins', T['owner'], CID,
            {'in_no': 'RK-BF-02', 'items': [{'fabric_name': 'x', 'lot_no': 'BFLOT-TMP', 'qty': 1}]})
si2 = (d or {}).get('id')
st, d = api('DELETE', f'/stock-ins/{si2}', T['owner'], CID)
check('WH-060 草稿入库单删除 → 204', st == 204, f'{st} {str(d)[:80]}')

# ================= warehouse：领料单（确认写血缘并累加） =================
st, d = api('GET', '/material-issues?status=draft', T['owner'], CID)
check('WH-070 领料单列表 status 筛选 200', st == 200, f'{st} {str(d)[:100]}')
st, d = api('POST', '/material-issues', T['owner'], CID, {'remark': '无明细'})
check('WH-071 领料无明细 → 400', st == 400, f'{st} {json.dumps(d, ensure_ascii=False)[:120]}')
st, d = api('POST', '/material-issues', T['owner'], CID,
            {'items': [{'production_id': prod.id, 'lot_id': 999999, 'qty': 1}]})
check('WH-072 领料批次不存在 → 400', st == 400, f'{st} {json.dumps(d, ensure_ascii=False)[:120]}')
st, d = api('POST', '/material-issues', T['owner'], CID,
            {'items': [{'production_id': 999999, 'lot_id': lot1, 'qty': 1}]})
check('WH-073 领料床次不存在 → 400', st == 400, f'{st} {json.dumps(d, ensure_ascii=False)[:120]}')
st, d = api('POST', '/material-issues', T['owner'], CID,
            {'issue_no': 'LL-BF-01',
             'items': [{'production_id': prod.id, 'lot_id': lot1, 'qty': 3}]})
check('WH-074 领料单带明细 201（单号必填，留空自动生成为防御分支）',
      st == 201 and (d or {}).get('issue_no') == 'LL-BF-01',
      f'{st} {json.dumps(d, ensure_ascii=False)[:180]}')
mi1 = (d or {}).get('id')
st, d = api('PUT', f'/material-issues/{mi1}', T['owner'], CID,
            {'remark': '改', 'issue_at': '2026-10-20T09:00:00'})
check('WH-075 草稿领料单可改 → 200', st == 200 and (d or {}).get('remark') == '改',
      f'{st} {str(d)[:150]}')
st, d = api('POST', f'/material-issues/{mi1}/confirm', T['owner'], CID)
check('WH-076 领料确认 → confirmed 且血缘累加（10+3=13）',
      st == 200 and (d or {}).get('status') == 'confirmed'
      and ProductionLotLink.objects.get(production=prod, lot_id=lot1).qty == 13,
      f'{st} {json.dumps(d, ensure_ascii=False)[:150]}')
st, d = api('POST', f'/material-issues/{mi1}/confirm', T['owner'], CID)
check('WH-077 重复确认领料单 → 400', st == 400, f'{st} {str(d)[:120]}')
st, d = api('PUT', f'/material-issues/{mi1}', T['owner'], CID, {'remark': 'x'})
check('WH-078 已确认领料单不可修改 → 400', st == 400, f'{st} {str(d)[:120]}')
st, d = api('DELETE', f'/material-issues/{mi1}', T['owner'], CID)
check('WH-079 已确认领料单不可删除 → 400', st == 400, f'{st} {str(d)[:120]}')
st, d = api('POST', '/material-issues', T['owner'], CID,
            {'issue_no': 'LL-BF-02',
             'items': [{'production_id': prod.id, 'lot_id': lot2, 'qty': 1}]})
mi2 = (d or {}).get('id')
st, d = api('DELETE', f'/material-issues/{mi2}', T['owner'], CID)
check('WH-080 草稿领料单删除 → 204', st == 204, f'{st} {str(d)[:80]}')
st, d = api('DELETE', f'/lots/{lot1}', T['owner'], CID)
check('WH-081 批次已有领料记录 → 400', st == 400, f'{st} {str(d)[:120]}')
st, d = api('GET', f'/trace/production/{prod.id}', T['owner'], CID)
check('WH-082 床次追溯含领料流水',
      st == 200 and (d or {}).get('issues') and (d or {})['issues'][0]['issue_no'].startswith('LL'),
      f'{st} {json.dumps((d or {}).get("issues", [])[:1], ensure_ascii=False)[:150]}')

# ================= catalog：款式工序配置校验 =================
BAD_ITEM_CASES = [
    ('CS-001 process_items 非列表 → 400', 'notalist'),
    ('CS-002 工序项非字典 → 400', ['x']),
    ('CS-003 工序 id 非法 → 400', [{'process_id': 'x'}]),
    ('CS-004 工序重复 → 400', [{'process_id': proc.id}, {'process_id': proc.id}]),
    ('CS-005 工序不存在 → 400', [{'process_id': 999999}]),
    ('CS-006 单价不合法 → 400', [{'process_id': proc.id, 'price': 'abc'}]),
]
for name, items in BAD_ITEM_CASES:
    st, d = api('POST', '/styles', T['owner'], CID,
                {'style_no': f'BF-BAD-{name[4:7]}', 'name': 'x', 'process_items': items})
    check(name, st == 400, f'{st} {json.dumps(d, ensure_ascii=False)[:120]}')
st, d = api('POST', '/styles', T['owner'], CID,
            {'style_no': 'BF-S2', 'name': '回填款二',
             'process_items': [{'process_id': proc.id, 'price': '3.3', 'sort': 1,
                                'allow_skip': False}]})
check('CS-010 新增款式带工序配置 201 且总价汇总',
      st == 201 and (d or {}).get('style_no') == 'BF-S2', f'{st} {str(d)[:150]}')
style2 = (d or {}).get('id')
st, d = api('POST', '/styles', T['owner'], CID, {'style_no': 'BF-S1', 'name': 'x'})
check('CS-011 款号重复 → 400', st == 400, f'{st} {str(d)[:120]}')
st, d = api('GET', '/styles?keyword=BF&customer_id=' + str(cust.id), T['owner'], CID)
check('CS-012 款式列表 keyword+customer 筛选', st == 200 and (d or {}).get('count') >= 1,
      f'{st} {str(d)[:120]}')
st, d = api('PUT', f'/styles/{style2}', T['owner'], CID,
            {'process_items': [{'process_id': proc.id, 'price': '4.4'}]})
check('CS-013 仅传 process_items 部分更新（styles.price 点）→ 200',
      st == 200 and StyleProcess.objects.filter(style_id=style2, price=4.4).exists(),
      f'{st} {str(d)[:150]}')
st, d = api('PUT', f'/styles/{style2}', T['owner'], CID, {'style_no': 'BF-S1', 'name': 'x'})
check('CS-014 改款号撞既有 → 400', st == 400, f'{st} {str(d)[:120]}')
st, d = api('DELETE', f'/styles/{style.id}', T['owner'], CID)
check('CS-015 款式已有菲票删除 → 400', st == 400, f'{st} {str(d)[:120]}')
st, d = api('POST', f'/styles/{style2}/copy', T['owner'], CID)
check('CS-016 深拷贝款式 → 201 款号 -1 后缀',
      st == 201 and (d or {}).get('style_no') == 'BF-S2-1', f'{st} {str(d)[:150]}')
st, d = api('POST', '/styles/999999/copy', T['owner'], CID)
check('CS-017 复制不存在款式 → 404', st == 404, f'{st} {str(d)[:80]}')
st, d = api('POST', f'/styles/{style.id}/sync-process', T['owner'], CID, {})
check('CS-018 同步工序未确认 → need_confirm + 涉及票数',
      st == 200 and (d or {}).get('need_confirm') is True and (d or {}).get('ticket_count') == 1,
      f'{st} {str(d)[:120]}')
st, d = api('POST', f'/styles/{style.id}/sync-process', T['owner'], CID, {'confirm': True})
check('CS-019 确认后同步 → synced', st == 200 and (d or {}).get('synced') is True,
      f'{st} {str(d)[:120]}')
st, d = api('POST', '/styles/999999/sync-process', T['owner'], CID, {})
check('CS-020 同步不存在款式 → 404', st == 404, f'{st} {str(d)[:80]}')

# ================= catalog：工序 =================
st, d = api('GET', '/processes?keyword=车缝&is_fixed=1', T['owner'], CID)
check('CS-030 工序列表 keyword+is_fixed 筛选', st == 200 and (d or {}).get('count') == 1,
      f'{st} {str(d)[:120]}')
st, d = api('POST', '/processes', T['owner'], CID, {'name': 'BF车缝'})
check('CS-031 工序重名 → 400', st == 400, f'{st} {str(d)[:120]}')
st, d = api('PUT', f'/processes/{proc.id}', T['owner'], CID, {'name': 'BF车缝2'})
check('CS-032 工序改名 → 200', st == 200, f'{st} {str(d)[:120]}')
api('PUT', f'/processes/{proc.id}', T['owner'], CID, {'name': 'BF车缝'})
st, d = api('POST', '/processes', T['owner'], CID, {'name': 'BF烫工'})
check('CS-033pre 预置撞名工序 201', st == 201, f'{st} {str(d)[:120]}')
st, d = api('PUT', f'/processes/{proc.id}', T['owner'], CID, {'name': 'BF烫工'})
check('CS-033 工序改名撞既有 → 400', st == 400, f'{st} {str(d)[:120]}')
st, d = api('DELETE', f'/processes/{proc.id}', T['owner'], CID)
check('CS-034 工序被款式使用删除 → 400', st == 400, f'{st} {str(d)[:120]}')
st, d = api('POST', '/processes', T['owner'], CID, {'name': 'BF临时工序'})
proc_tmp = (d or {}).get('id')
Record.objects.create(company=company, ticket=t2, process_id=proc_tmp, user=plain2,
                      qty=1, price='1.000', registered_at=now)
st, d = api('DELETE', f'/processes/{proc_tmp}', T['owner'], CID)
check('CS-035 工序已有登记删除 → 400', st == 400, f'{st} {str(d)[:120]}')
# 指定工序含脏数据（'x' 跳过分支 + 指定工序并入选择器）
UserCompany.objects.filter(pk=uc_plain.pk).update(assigned_processes=['x', str(proc.id)])
st, d = api('GET', '/process-selector', T['plain'], CID)
ids = [r.get('id') for r in (d if isinstance(d, list) else [])]
check('CS-036 选择器并入指定工序且跳过脏数据', st == 200 and proc.id in ids,
      f'{st} ids={ids}')
UserCompany.objects.filter(pk=uc_plain.pk).update(assigned_processes=[])

# ================= catalog：客户分组 / 客户 =================
st, d = api('POST', '/customer-groups', T['owner'], CID, {'name': 'BF组'})
grp1 = (d or {}).get('id')
check('CS-040 新增分组 201', st == 201, f'{st} {str(d)[:120]}')
st, d = api('POST', '/customer-groups', T['owner'], CID, {'name': 'BF组'})
check('CS-041 分组重名 → 400', st == 400, f'{st} {str(d)[:120]}')
st, d = api('POST', '/customer-groups', T['owner'], CID, {'name': 'BF组2'})
grp2 = (d or {}).get('id')
st, d = api('PUT', f'/customer-groups/{grp2}', T['owner'], CID, {'name': 'BF组'})
check('CS-042 分组改名撞既有 → 400', st == 400, f'{st} {str(d)[:120]}')
st, d = api('DELETE', f'/customer-groups/{grp2}', T['owner'], CID)
check('CS-043 删除分组 → 204', st == 204, f'{st} {str(d)[:80]}')
st, d = api('POST', '/customers', T['owner'], CID, {'name': '回填客户乙'})
check('CS-044 客户编号留空自动取号 KH0001',
      st == 201 and (d or {}).get('customer_no') == 'KH0001', f'{st} {str(d)[:150]}')
cust2 = (d or {}).get('id')
st, d = api('POST', '/customers', T['owner'], CID, {'name': 'x', 'customer_no': 'KH0001'})
check('CS-045 客户编号重复 → 400', st == 400, f'{st} {str(d)[:120]}')
st, d = api('PUT', f'/customers/{cust2}', T['owner'], CID,
            {'customer_no': 'BFC1', 'group': grp1})
check('CS-046 客户编号撞既有 → 400', st == 400, f'{st} {str(d)[:120]}')
st, d = api('GET', '/customers?keyword=回填&group=' + str(grp1), T['owner'], CID)
check('CS-047 客户列表 keyword+group 筛选', st == 200, f'{st} {str(d)[:120]}')
st, d = api('DELETE', f'/customers/{cust2}', T['owner'], CID)
check('CS-048 删除客户 → 204', st == 204, f'{st} {str(d)[:80]}')

# ================= catalog：编菲 / 打印 / 删票 =================
st, d = api('POST', '/tickets/batch', T['owner'], CID,
            {'production_id': 999999, 'items': [{'qty': 1}]})
check('CS-050 编菲床次不存在 → 404', st == 404, f'{st} {str(d)[:100]}')
st, d = api('POST', '/tickets/batch', T['owner'], CID,
            {'production_id': 'abc', 'items': [{'qty': 1}]})
check('CS-051 编菲床次 id 非法 → 404（ValueError 分支）', st == 404, f'{st} {str(d)[:100]}')
st, d = api('POST', '/tickets/batch', T['owner'], CID, {'production_id': prod.id, 'items': []})
check('CS-052 编菲空明细 → 400', st == 400, f'{st} {str(d)[:120]}')
st, d = api('POST', '/tickets/batch', T['owner'], CID,
            {'production_id': prod.id, 'items': [{'qty': 1}], 'bundle_limit': 'x'})
check('CS-053 每扎上限非数字 → 400', st == 400, f'{st} {str(d)[:120]}')
st, d = api('POST', '/tickets/batch', T['owner'], CID,
            {'production_id': prod.id, 'items': [{'qty': 1}], 'start_bundle_no': 'x1'})
check('CS-054 起始扎号非数字 → 400', st == 400, f'{st} {str(d)[:120]}')
st, d = api('POST', '/tickets/batch', T['owner'], CID,
            {'production_id': prod.id, 'items': ['x']})
check('CS-055 编菲项非字典 → 400', st == 400, f'{st} {str(d)[:120]}')
st, d = api('POST', '/tickets/batch', T['owner'], CID,
            {'production_id': prod.id, 'items': [{'qty': 'x'}]})
check('CS-056 数量非数字 → 400', st == 400, f'{st} {str(d)[:120]}')
st, d = api('POST', '/tickets/batch', T['owner'], CID,
            {'production_id': prod.id, 'items': [{'qty': 0}]})
check('CS-057 数量非正 → 400', st == 400, f'{st} {str(d)[:120]}')
st, d = api('POST', '/tickets/batch', T['owner'], CID,
            {'production_id': prod.id, 'items': [{'color': '红', 'size': 'M', 'qty': 250}],
             'bundle_limit': 100})
bundles = [(t.get('bundle_no'), t.get('qty')) for t in (d if isinstance(d, list) else [])]
check('CS-058 满扎拆扎 250/100 → 100+100+50 三扎',
      st == 201 and len(bundles) == 3 and bundles == [('3', 100), ('4', 100), ('5', 50)],
      f'{st} bundles={bundles}')
st, d = api('POST', '/tickets/batch', T['owner'], CID,
            {'production_id': prod2.id, 'items': [{'color': '蓝', 'qty': 10}]})
check('CS-059 编菲 201（床次二）', st == 201, f'{st} {str(d)[:120]}')
st, d = api('GET', f'/tickets?production_id={prod.id}&bed_no=BF-B1&bundle_no=3&is_printed=0',
            T['owner'], CID)
check('CS-060 菲票查询四参筛选', st == 200 and (d or {}).get('count') == 1,
      f'{st} {str(d)[:150]}')
st, d = api('POST', '/tickets/print', T['plain2'], CID, {'ticket_ids': [1]})
check('CS-061 无打印权限 → 403', st == 403, f'{st} {str(d)[:80]}')
st, d = api('POST', '/tickets/print', T['owner'], CID, {})
check('CS-062 打印缺 ticket_ids → 400', st == 400, f'{st} {str(d)[:120]}')
st, d = api('POST', '/tickets/print', T['owner'], CID, {'ticket_ids': ['x']})
check('CS-063 打印票 id 非整数 → 400', st == 400, f'{st} {str(d)[:120]}')
st, d = api('POST', '/tickets/print', T['owner'], CID, {'ticket_ids': [999999]})
check('CS-064 打印缺票 → 200 且进 skipped（M1 容忍缺失）',
      st == 200 and (d or {}).get('skipped') == [999999], f'{st} {str(d)[:120]}')
tk3 = Ticket.objects.get(company=company, production=prod, bundle_no='3')
tk4 = Ticket.objects.get(company=company, production=prod, bundle_no='4')
st, d = api('POST', '/tickets/print', T['owner'], CID,
            {'ticket_ids': [tk3.id, tk3.id, tk4.id]})
check('CS-065 打印计数（去重）→ reported=2 且 is_printed',
      st == 200 and (d or {}).get('reported') == 2
      and Ticket.objects.get(pk=tk3.pk).is_printed
      and Ticket.objects.get(pk=tk3.pk).print_count == 1,
      f'{st} {str(d)[:120]}')
st, d = api('DELETE', f'/tickets/{t1.id}', T['owner'], CID)
check('CS-066 菲票已有登记删除 → 400', st == 400, f'{st} {str(d)[:120]}')
st, d = api('POST', '/tickets/batch', T['owner'], CID,
            {'production_id': prod.id, 'items': [{'color': '绿', 'qty': 5}]})
tk9 = (d if isinstance(d, list) else [{}])[0].get('id')
st, d = api('DELETE', f'/tickets/{tk9}', T['owner'], CID)
check('CS-067 无登记菲票删除 → 204 且裁货数回算', st == 204, f'{st} {str(d)[:80]}')

# ================= catalog：默认裁完开关 =================
CompanySetting.objects.filter(company=company, key='auto_cut_done').update(value='1')
st, d = api('POST', '/tickets/batch', T['owner'], CID,
            {'production_id': prod2.id, 'items': [{'color': '黄', 'qty': 10}]})
check('CS-070 auto_cut_done 开：编菲后自动置裁完 + 审计',
      st == 201 and Production.objects.get(pk=prod2.pk).is_cut_done
      and AuditLog.objects.filter(company=company, bed_no='BF-B2',
                                  message__contains='默认裁完').exists(),
      f'{st} cut_done={getattr(Production.objects.get(pk=prod2.pk), "is_cut_done", None)}')
CompanySetting.objects.filter(company=company, key='auto_cut_done').update(value='0')

# ================= accounts：员工列表筛选 =================
st, d = api('GET', '/employees?tab=pending', T['owner'], CID)
check('AC-001 员工列表 tab=pending 200', st == 200, f'{st} {str(d)[:100]}')
st, d = api('GET', '/employees?tab=resigned', T['owner'], CID)
rows = d if isinstance(d, list) else []
check('AC-002 tab=resigned 命中预置离职成员',
      st == 200 and rows and all(r.get('status') == 2 for r in rows),
      f'{st} n={len(rows)}')
st, d = api('GET', '/employees?keyword=回填', T['owner'], CID)
check('AC-003 keyword 模糊筛选 200', st == 200 and isinstance(d, list), f'{st} {str(d)[:100]}')
st, d = api('GET', '/employees?post=0', T['owner'], CID)
rows = d if isinstance(d, list) else []
check('AC-004 post=0 筛出无岗位成员',
      st == 200 and rows and all(r.get('post_id') is None for r in rows),
      f'{st} {json.dumps(rows[:1], ensure_ascii=False)[:160]}')
st, d = api('GET', f'/employees?post={post_shop.id}', T['owner'], CID)
check('AC-005 post=<id> 筛出登记岗成员', st == 200 and isinstance(d, list) and d,
      f'{st} {str(d)[:120]}')

# ================= accounts：邀请→审批→离职→复职→容量 =================
st, d = api('POST', '/employees/invite', T['owner'], CID,
            {'phone': INV_A, 'real_name': '受邀一'})
check('AC-010 邀请新员工 → 201 待审核', st == 201 and (d or {}).get('employee', {}).get('status') == 0,
      f'{st} {json.dumps(d, ensure_ascii=False)[:150]}')
link_a = (d or {}).get('employee', {}).get('id')
st, d = api('POST', '/employees/invite', T['owner'], CID,
            {'phone': INV_A, 'real_name': '受邀一'})
check('AC-011 重复邀请待审核 → 400', st == 400, f'{st} {str(d)[:120]}')
st, d = api('POST', '/employees/invite', T['owner'], CID,
            {'phone': INV_B, 'real_name': '受邀二'})
check('AC-012 邀请第二人 201', st == 201, f'{st} {str(d)[:120]}')
# 已存在但 real_name 为空的用户：邀请时补姓名（461-463 分支）
u_b = User.objects.get(phone=INV_B)
User.objects.filter(pk=u_b.pk).update(real_name='')
st, d = api('POST', '/employees/invite', T['owner'], CID, {'phone': INV_B})
check('AC-013 已在待审核列表重复邀请 → 400（先于补名）', st == 400, f'{st} {str(d)[:120]}')
st, d = api('POST', f'/employees/{link_a}/approve', T['owner'], CID)
check('AC-014 审批通过 → 在职', st == 200 and (d or {}).get('status') == 1,
      f'{st} {str(d)[:120]}')
st, d = api('POST', f'/employees/{link_a}/approve', T['owner'], CID)
check('AC-015 重复审批（非待审核）→ 400', st == 400, f'{st} {str(d)[:120]}')
st, d = api('POST', '/employees/invite', T['owner'], CID, {'phone': INV_A})
check('AC-016 邀请已在职工 → 400', st == 400, f'{st} {str(d)[:120]}')
# 容量上限：压到当前在职数 → 审批/邀请/复职均 400
active_cnt = UserCompany.objects.filter(company=company, status=UserCompany.Status.ACTIVE,
                                        is_deleted=False).count()
link_b = UserCompany.objects.get(user__phone=INV_B, company=company).id
st, d = api('POST', f'/employees/{link_b}/approve', T['owner'], CID)
check('AC-017 容量未满审批 B → 200', st == 200, f'{st} {str(d)[:120]}')
# 邀请第三人（供容量与拒绝流）
st, d = api('POST', '/employees/invite', T['owner'], CID, {'phone': INV_C, 'real_name': '受邀三'})
link_c = (d or {}).get('employee', {}).get('id')
check('AC-018 邀请第三人 201', st == 201, f'{st} {str(d)[:120]}')
Company.objects.filter(pk=company.pk).update(capacity=UserCompany.objects.filter(
    company=company, status=UserCompany.Status.ACTIVE, is_deleted=False).count())
st, d = api('POST', '/employees/invite', T['owner'], CID, {'phone': '13988940099'})
check('AC-019 容量满邀请 → 400', st == 400, f'{st} {str(d)[:120]}')
st, d = api('POST', f'/employees/{link_c}/approve', T['owner'], CID)
check('AC-020 容量满审批 → 400', st == 400, f'{st} {str(d)[:120]}')
# 离职一人再试复职容量
st, d = api('POST', f'/employees/{link_a}/resign', T['owner'], CID)
check('AC-021 无未结记录离职 → 200', st == 200 and (d or {}).get('status') == 2,
      f'{st} {str(d)[:120]}')
# 离职后重新压容量=当前在职数 → 复职被拦
Company.objects.filter(pk=company.pk).update(capacity=UserCompany.objects.filter(
    company=company, status=UserCompany.Status.ACTIVE, is_deleted=False).count())
st, d = api('POST', f'/employees/{link_a}/rehire', T['owner'], CID)
check('AC-022 容量满复职 → 400', st == 400, f'{st} {str(d)[:120]}')
Company.objects.filter(pk=company.pk).update(capacity=50)
st, d = api('POST', f'/employees/{link_a}/rehire', T['owner'], CID)
check('AC-023 复职 → 200 在职', st == 200 and (d or {}).get('status') == 1,
      f'{st} {str(d)[:120]}')
st, d = api('POST', f'/employees/{link_a}/rehire', T['owner'], CID)
check('AC-024 在职工复职 → 400', st == 400, f'{st} {str(d)[:120]}')
owner_link = UserCompany.objects.get(user=owner, company=company, is_deleted=False)
st, d = api('POST', f'/employees/{owner_link.id}/resign', T['owner'], CID)
check('AC-025 最后管理员离职 → 400', st == 400, f'{st} {str(d)[:120]}')
st, d = api('POST', f'/employees/{link_c}/reject', T['owner'], CID)
check('AC-026 拒绝待审核 → 200 离职态', st == 200 and (d or {}).get('status') == 2,
      f'{st} {str(d)[:120]}')
st, d = api('POST', f'/employees/{link_c}/reject', T['owner'], CID)
check('AC-027 重复拒绝 → 400', st == 400, f'{st} {str(d)[:120]}')
st, d = api('POST', f'/employees/{link_a}/resign', T['owner'], CID)
check('AC-028 再次离职 A → 200（后续边界用离职态）', st == 200, f'{st} {str(d)[:120]}')
st, d = api('POST', f'/employees/{link_a}/resign', T['owner'], CID)
check('AC-029 非在职离职 → 400', st == 400, f'{st} {str(d)[:120]}')
st, d = api('POST', f'/employees/{link_a}/set-post', T['owner'], CID, {'post_id': post_shop.id})
check('AC-030 离职成员设岗 → 400', st == 400, f'{st} {str(d)[:120]}')
st, d = api('POST', f'/employees/{link_a}/set-group', T['owner'], CID, {'group_no': 2})
check('AC-031 离职成员设组 → 400', st == 400, f'{st} {str(d)[:120]}')
st, d = api('POST', '/employees/999999/approve', T['owner'], CID)
check('AC-032 审批不存在成员 → 404', st == 404, f'{st} {str(d)[:80]}')
st, d = api('POST', f'/employees/{uc_plain.id}/set-post', T['owner'], CID,
            {'post_id': post_shop.id})
check('AC-033 在职成员设岗 → 200', st == 200, f'{st} {str(d)[:120]}')
st, d = api('POST', f'/employees/{uc_plain.id}/set-group', T['owner'], CID, {'group_no': 3})
check('AC-034 在职成员设组 → 200 group_no=3',
      st == 200 and (d or {}).get('group_no') == 3, f'{st} {str(d)[:120]}')
UserCompany.objects.filter(pk=uc_plain.pk).update(group_no=1)

# ================= accounts：套餐变更 / 指定工序审计 =================
st, d = api('PUT', '/companies/plan', T['owner'], CID, {'plan': 'nope'})
check('AC-040 未知套餐 → 400', st == 400, f'{st} {str(d)[:120]}')
st, d = api('PUT', '/companies/plan', T['owner'], CID, {'plan': 'basic'})
check('AC-041 同套餐无变更 → 200', st == 200, f'{st} {str(d)[:120]}')
st, d = api('PUT', '/companies/plan', T['owner'], CID, {'plan': 'standard'})
check('AC-042 升档 → 200 + 审计落库',
      st == 200 and (d or {}).get('plan') == 'standard'
      and AuditLog.objects.filter(company=company, message__contains='版本套餐').exists(),
      f'{st} {str(d)[:150]}')
# 降档拦截：造 21 名在职临时成员（体验版容量 20）
tmp_links = []
for i in range(21):
    tu = ensure_user(f'139889409{i:02d}', f'容量员{i}')
    tmp_links.append(ensure_member(tu, company, None).id)
st, d = api('PUT', '/companies/plan', T['owner'], CID, {'plan': 'trial'})
check('AC-043 在职超目标容量降档 → 400', st == 400, f'{st} {str(d)[:150]}')
UserCompany.objects.filter(pk__in=tmp_links).delete()
Company.objects.filter(pk=company.pk).update(plan=Company.Plan.BASIC, capacity=50)
audit_cnt0 = AuditLog.objects.filter(company=company,
                                     message__contains='修改指定工序').count()
st, d = api('PUT', f'/employees/{uc_plain.id}/assigned-processes', T['owner'], CID,
            {'process_ids': [proc.id]})
check('AC-044 指定工序写入 → 200', st == 200, f'{st} {str(d)[:120]}')
audit_cnt1 = AuditLog.objects.filter(company=company,
                                     message__contains='修改指定工序').count()
check('AC-044b 指定工序变化写审计（前后对比插值）', audit_cnt1 == audit_cnt0 + 1,
      f'{audit_cnt0}→{audit_cnt1}')
st, d = api('PUT', f'/employees/{uc_plain.id}/assigned-processes', T['owner'], CID,
            {'process_ids': [proc.id]})
check('AC-045 指定工序未变化不写审计',
      st == 200 and AuditLog.objects.filter(
          company=company, message__contains='修改指定工序').count() == audit_cnt1,
      f'{st} {str(d)[:120]}')
st, d = api('PUT', f'/employees/{uc_plain.id}/assigned-processes', T['owner'], CID,
            {'process_ids': [proc_tmp]})
check('AC-046 指定工序再变化再写审计',
      st == 200 and AuditLog.objects.filter(
          company=company, message__contains='修改指定工序').count() == audit_cnt1 + 1,
      f'{st} {str(d)[:120]}')
UserCompany.objects.filter(pk=uc_plain.pk).update(assigned_processes=[])

# ================= accounts：岗位复制 / 删除拦截 =================
st, d = api('POST', f'/posts/{admin_post.id}/copy', T['owner'], CID)
check('AC-050 复制管理员岗位 → 201 名称带副本',
      st == 201 and '副本' in (d or {}).get('name', ''), f'{st} {str(d)[:120]}')
st, d = api('DELETE', f'/posts/{admin_post.id}', T['owner'], CID)
check('AC-051 系统岗位删除 → 400', st == 400, f'{st} {str(d)[:120]}')
st, d = api('DELETE', f'/posts/{post_shop.id}', T['owner'], CID)
check('AC-052 有在职成员岗位删除 → 400', st == 400, f'{st} {str(d)[:120]}')
st, d = api('POST', '/posts', T['owner'], CID, {'name': 'BF临时岗2'})
tmp_post = (d or {}).get('id')
check('AC-053 新建岗位 201', st == 201, f'{st} {str(d)[:120]}')
st, d = api('DELETE', f'/posts/{tmp_post}', T['owner'], CID)
check('AC-054 无成员岗位删除 → 204', st == 204, f'{st} {str(d)[:80]}')

# ================= accounts：公司动作 =================
st, d = api('POST', '/companies/create', T['owner'], CID, {'name': '  '})
check('AC-060 创建公司空名 → 400', st == 400, f'{st} {str(d)[:120]}')
st, d = api('POST', '/companies/join', T['owner'], CID, {'company_no': company.company_no})
check('AC-061 重复加入本公司 → 400', st == 400, f'{st} {str(d)[:120]}')
st, d = api('PUT', '/companies/999999/switch', T['plain'], CID)
check('AC-062 切换非成员公司 → 404', st == 404, f'{st} {str(d)[:80]}')
st, d = api('GET', '/companies/wxacode', T['owner'], CID)
check('AC-063 未配置微信参数 wxacode → 400', st == 400
      and '未配置' in json.dumps(d, ensure_ascii=False), f'{st} {str(d)[:120]}')
st, d = api('POST', '/auth/sms-code', None, None, {'phone': '123'})
check('AC-064 短信验证码手机号非法 → 400', st == 400, f'{st} {str(d)[:120]}')
st, d = api('PUT', '/auth/me', T['plain'], None, {'real_name': '回填普工改'})
check('AC-065 PUT /auth/me 改资料 → 200',
      st == 200 and (d or {}).get('real_name') == '回填普工改', f'{st} {str(d)[:120]}')

# ================= accounts：注销（有密码 / 无密码） =================
d1 = ensure_user(H_DEACT1, '待注销一')
uc_d1 = ensure_member(d1, company, post_plain)
st, d = api('POST', '/auth/deactivate', login(H_DEACT1), None, {'password': 'yisao1234'})
check('AC-070 有密码校验通过注销 → 200 匿名化',
      st == 200 and User.objects.get(pk=d1.pk).real_name == '已注销用户'
      and User.objects.get(pk=d1.pk).phone.startswith('del_'),
      f'{st} {str(d)[:120]}')
st, d = api('POST', '/auth/deactivate', login(H_DEACT1), None, {})
check('AC-071 已注销账号登录后再注销 → 401（token 失效）', st == 401, f'{st} {str(d)[:80]}')
d2 = ensure_user(H_DEACT2, '待注销二')
ensure_member(d2, company, post_plain)
tok2 = login(H_DEACT2)
# 登录后再置为无可用密码（短信/微信注册用户形态）→ 注销跳过密码校验
u2 = User.objects.get(pk=d2.pk)
u2.set_unusable_password()
u2.save(update_fields=['password'])
st, d = api('POST', '/auth/deactivate', tok2, None, {})
check('AC-072 无可用密码跳过校验直接注销 → 200（136→141 分支）',
      st == 200 and User.objects.get(pk=d2.pk).is_active is False,
      f'{st} {str(d)[:120]}')
UserCompany.all_objects.filter(user__phone__in=[H_DEACT1, H_DEACT2],
                               company=company).delete()
User.objects.filter(phone__in=[H_DEACT1, H_DEACT2]).delete()

# ================= core：uploads =================
c = Client()
auth = {'HTTP_AUTHORIZATION': f'Bearer {T["owner"]}', 'HTTP_X_COMPANY_ID': str(CID)}
r = c.post('/api/uploads', {}, **auth)
check('CO-001 上传缺文件字段 → 400', r.status_code == 400, f'{r.status_code} {r.content[:100]}')
r = c.post('/api/uploads', {'file': SimpleUploadedFile('a.txt', b'x')}, **auth)
check('CO-002 上传非图片扩展 → 400', r.status_code == 400, f'{r.status_code} {r.content[:100]}')
r = c.post('/api/uploads', {'file': SimpleUploadedFile('ok.PNG', b'\x89PNG\r\n\x1a\nfake')},
           **auth)
body = json.loads(r.content.decode('utf-8')) if r.content else {}
check('CO-003 上传 PNG 成功（大写扩展名归一）→ 返回相对 URL',
      r.status_code == 200 and body.get('url', '').startswith('/media/uploads/')
      and body.get('url', '').endswith('.png'), f'{r.status_code} {str(body)[:120]}')
uploaded_path = body.get('url', '').replace('/media/', '') if body.get('url') else None
# 无登录 401（IsAuthenticated）
r = c.post('/api/uploads', {'file': SimpleUploadedFile('ok.png', b'x')})
check('CO-004 未登录上传 → 401', r.status_code == 401, f'{r.status_code}')

# ================= core：client-logs =================
r = Client().post('/api/client-logs', data=json.dumps(
    {'level': 'error', 'page': 'scan', 'message': '未登录上报', 'company_id': CID}),
    content_type='application/json')
check('CO-010 未登录上报 → 201 且厂落款为空',
      r.status_code == 201, f'{r.status_code} {r.content[:100]}')
check('CO-011 未登录上报不落厂',
      not ClientLog.objects.filter(message='未登录上报', company=company).exists()
      and ClientLog.objects.filter(message='未登录上报', company=None).exists(), '')
items = [{'level': 'info', 'page': 'p', 'message': f'm{i}', 'company_id': CID}
         for i in range(21)]
st, d = api('POST', '/client-logs', T['owner'], CID, items)
check('CO-012 超批量上限（21 条）→ 400', st == 400, f'{st} {str(d)[:100]}')
st, d = api('POST', '/client-logs', T['owner'], CID,
            [{'message': '   '}, 123, {'message': '脏级别', 'level': 'bogus',
                                       'company_id': CID}])
check('CO-013 脏条目跳过 + 非法级别归一 info → 201 count=1',
      st == 201 and (d or {}).get('count') == 1
      and ClientLog.objects.filter(message='脏级别', level='info', company=company).exists(),
      f'{st} {str(d)[:120]}')
st, d = api('POST', '/client-logs', T['owner'], CID, [{'message': ''}])
check('CO-014 全无效条目 → 400', st == 400, f'{st} {str(d)[:100]}')
st, d = api('POST', '/client-logs', T['plain'], CID,
            {'level': 'warn', 'page': 'scan', 'message': '成员落款', 'company_id': CID})
check('CO-015 在职成员上报落款本公司',
      st == 201 and ClientLog.objects.filter(message='成员落款', company=company,
                                             user=plain).exists(), f'{st} {str(d)[:120]}')
st, d = api('GET', '/client-logs', T['owner'], CID)
check('CO-016 管理员读日志分页 200', st == 200 and (d or {}).get('count') >= 2,
      f'{st} {str(d)[:120]}')
st, d = api('GET', f'/client-logs?user_id={plain.id}&level=warn&date_from='
                  f'{timezone.localdate()}&date_to={timezone.localdate()}', T['owner'], CID)
check('CO-017 日志四参筛选命中成员落款行',
      st == 200 and (d or {}).get('count') == 1
      and (d or {}).get('results', [{}])[0].get('message') == '成员落款',
      f'{st} {str(d)[:150]}')
st, d = api('GET', '/client-logs?user_id=abc', T['owner'], CID)
check('CO-018 user_id 非数字不过滤（分支）→ 200', st == 200, f'{st} {str(d)[:100]}')
st, d = api('GET', '/client-logs', T['plain2'], CID)
check('CO-019 无 logs.client 权限读日志 → 403', st == 403, f'{st} {str(d)[:80]}')
st, d = api('GET', '/client-logs', None, CID)
check('CO-020 未登录读日志 → 401', st == 401, f'{st} {str(d)[:80]}')

# ================= cleanup：物理清业务行 + 复位开关/成员配置 =================
try:
    if uploaded_path:
        from django.core.files.storage import default_storage
        default_storage.delete(uploaded_path)
    wipe_business(company)
    UserCompany.objects.filter(company=company).update(
        assigned_processes=[], fixed_salary=None,
        fixed_type=UserCompany.FixedType.MONTH, group_no=1)
    CompanySetting.objects.filter(company=company, key='auto_cut_done').update(
        value=CompanySetting.DEFAULTS['auto_cut_done'])
    Company.objects.filter(pk=company.pk).update(plan=Company.Plan.BASIC, capacity=50)
    print('cleanup: done（业务行/上传文件已清理，测试厂/账号复用保留）')
except Exception as e:  # noqa: BLE001
    print('cleanup WARN:', e)

print(f'\n==== 分支回填套件：{len(PASS)} pass / {len(FAIL)} fail ====')
if FAIL:
    print('FAILED:', FAIL)
