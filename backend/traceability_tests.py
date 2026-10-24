# -*- coding: utf-8 -*-
"""质量追溯 + 轻量仓库 E2E（幂等：两连跑全过）。

运行：PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe manage.py shell -c \
  "exec(compile(open('traceability_tests.py', encoding='utf-8').read(), 'traceability_tests.py', 'exec'))"

覆盖（完成标准 2）：
- 批次 CRUD 权限（管理员可、production 权限岗可、无权限岗 403）
- 床次↔批次血缘写入两条路径：手工 PUT lot-links + 领料 confirm 钩子（含多次领料用量累加）
- 正向 /trace/lot/<id>、反查 /trace/reverse、终端 /trace/ticket/<id>、床次视角 /trace/production/<id>
- 旧票七段码 scan/parse 仍通并查出血缘
- 来源单号录入与修改（建床带入 + 床次更新）
- 采购/入库（批次诞生+来源回填）/领料三单据最小闭环
"""
import json

from django.test import Client
from django.utils import timezone

from apps.accounts.models import Company, User, UserCompany, Post as PostM
from apps.catalog.models import Style, StyleProcess, Process, Ticket
from apps.production.models import Production, ProductionProcess
from apps.records.models import Record
from apps.warehouse.models import (
    IssueItem, Lot, MaterialIssue, ProductionLotLink,
    PurchaseItem, PurchaseOrder, StockIn, StockInItem,
)

c = Client()
PASS, FAIL = [], []
BED = 'E2ETR01'
MARK = 'E2ETR'
PHONES = {'admin': '13877770001', 'limited': '13877770002', 'prod': '13877770003'}
CID = 0  # 套件自建公司 id（preclean 后创建）


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    print(('PASS' if cond else 'FAIL'), name, ('' if cond else f'| {detail}'))


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


def _raw_delete(qs):
    qs._raw_delete(qs.db)


def preclean():
    """幂等清理：本套件标记数据全量硬删（SQLite FK 无库级 CASCADE，先子后父）。"""
    _raw_delete(Record.all_objects.filter(ticket__production__bed_no=BED))
    _raw_delete(Ticket.all_objects.filter(production__bed_no=BED))
    _raw_delete(IssueItem.all_objects.filter(production__bed_no=BED))
    _raw_delete(ProductionLotLink.all_objects.filter(production__bed_no=BED))
    _raw_delete(ProductionProcess.all_objects.filter(production__bed_no=BED))
    _raw_delete(Production.all_objects.filter(bed_no=BED))
    _raw_delete(Record.all_objects.filter(ticket__production__style__style_no=BED))
    _raw_delete(Ticket.all_objects.filter(production__style__style_no=BED))
    _raw_delete(ProductionProcess.all_objects.filter(production__style__style_no=BED))
    _raw_delete(Production.all_objects.filter(style__style_no=BED))
    _raw_delete(IssueItem.all_objects.filter(issue__issue_no__contains=MARK))
    MaterialIssue.all_objects.filter(issue_no__contains=MARK).delete()
    StockInItem.all_objects.filter(stock_in__in_no__contains=MARK).delete()
    StockIn.all_objects.filter(in_no__contains=MARK).delete()
    PurchaseItem.all_objects.filter(order__order_no__contains=MARK).delete()
    PurchaseOrder.all_objects.filter(order_no__contains=MARK).delete()
    _raw_delete(ProductionLotLink.all_objects.filter(lot__lot_no__contains=MARK))
    Lot.all_objects.filter(lot_no__contains=MARK).delete()
    _raw_delete(StyleProcess.all_objects.filter(style__style_no=BED))
    Style.all_objects.filter(style_no=BED).delete()
    Process.all_objects.filter(name__contains=MARK).delete()
    # 自建测试公司整厂清理（上次中断残留）
    for cid in Company.all_objects.filter(name__contains=MARK).values_list('id', flat=True):
        _raw_delete(Record.all_objects.filter(company_id=cid))
        _raw_delete(Ticket.all_objects.filter(company_id=cid))
        _raw_delete(IssueItem.all_objects.filter(issue__company_id=cid))
        MaterialIssue.all_objects.filter(company_id=cid).delete()
        StockInItem.all_objects.filter(stock_in__company_id=cid).delete()
        StockIn.all_objects.filter(company_id=cid).delete()
        PurchaseItem.all_objects.filter(order__company_id=cid).delete()
        PurchaseOrder.all_objects.filter(company_id=cid).delete()
        _raw_delete(ProductionLotLink.all_objects.filter(company_id=cid))
        Lot.all_objects.filter(company_id=cid).delete()
        _raw_delete(ProductionProcess.all_objects.filter(production__company_id=cid))
        _raw_delete(Production.all_objects.filter(company_id=cid))
        _raw_delete(StyleProcess.all_objects.filter(style__company_id=cid))
        Style.all_objects.filter(company_id=cid).delete()
        Process.all_objects.filter(company_id=cid).delete()
        UserCompany.all_objects.filter(company_id=cid).delete()
        PostM.all_objects.filter(company_id=cid).delete()
        Company.all_objects.filter(pk=cid).delete()
    for ph in set(PHONES.values()):
        u = User.objects.filter(phone=ph).first()
        if u:
            _raw_delete(UserCompany.all_objects.filter(user=u))
            u.delete()


def _make_user(phone, real_name, company, post):
    u = User.objects.create(username=phone, phone=phone, real_name=real_name)
    u.set_password('yisao1234')
    u.save()
    UserCompany.objects.create(user=u, company=company, post=post,
                               status=UserCompany.Status.ACTIVE)
    return u


def setup_company():
    """套件自建公司 + 三种角色（不依赖演示库成员关系，防并行套件挪动成员）。"""
    admin = User.objects.create(username=PHONES['admin'], phone=PHONES['admin'],
                                real_name=f'{MARK}厂长')
    admin.set_password('yisao1234')
    admin.save()
    company = Company.objects.create(name=f'{MARK}追溯测试厂', creator=admin)
    admin_post = PostM.objects.create(company=company, name=f'{MARK}管理岗',
                                      permissions=['*'], is_admin=True, is_system=True)
    UserCompany.objects.create(user=admin, company=company, post=admin_post,
                               status=UserCompany.Status.ACTIVE)
    limited_post = PostM.objects.create(company=company, name=f'{MARK}受限岗',
                                        permissions=['customers'])
    _make_user(PHONES['limited'], f'{MARK}受限员', company, limited_post)
    prod_post = PostM.objects.create(company=company, name=f'{MARK}生产岗',
                                     permissions=['production'])
    _make_user(PHONES['prod'], f'{MARK}生产员', company, prod_post)
    return company


def login(phone):
    code, d = req('post', '/auth/login', cid=None, data={'phone': phone, 'password': 'yisao1234'})
    assert code == 200 and d.get('access'), f'login failed {phone} {code} {str(d)[:120]}'
    return d['access']


preclean()
company = setup_company()
CID = company.id
AT = login(PHONES['admin'])
LT = login(PHONES['limited'])
PT = login(PHONES['prod'])

# ---- 基础档案（直接 ORM，追溯套件不测档案 CRUD） ----
style = Style.objects.create(company=company, style_no=BED, name=f'{MARK}款式')
process = Process.objects.create(company=company, name=f'{MARK}工序', default_price=1.5)
StyleProcess.objects.create(style=style, process=process, price=1.5)

# ============================================================
# T1 批次 CRUD 与权限
# ============================================================
code, d = req('post', '/lots', AT, CID, {'lot_no': f'{MARK}-LOT1', 'fabric_name': '32支精棉',
                                       'color': '宝蓝', 'qty': '800', 'unit': '米'})
check('T1.1 建批次', code == 201 and d.get('lot_no') == f'{MARK}-LOT1', f'{code} {str(d)[:100]}')
LOT1 = d['id']

code, d = req('post', '/lots', AT, CID, {'lot_no': f'{MARK}-LOT1'})
check('T1.2 重复批次号被拒', code == 400, f'{code}')

code, d = req('post', '/lots', AT, CID, {'lot_no': f'{MARK}-LOT2', 'color': '大红', 'qty': '300'})
check('T1.3 建第二批次', code == 201, f'{code}')
LOT2 = d['id']

code, d = req('get', '/lots?keyword=LOT1', AT, CID)
check('T1.4 批次列表 keyword 命中', code == 200 and any(
    x['lot_no'] == f'{MARK}-LOT1' for x in d.get('results', [])), f'{code} {str(d)[:100]}')

code, d = req('put', f'/lots/{LOT1}', AT, CID, {'color': '宝蓝(改)'})
check('T1.5 改批次', code == 200 and d.get('color') == '宝蓝(改)', f'{code}')

code, d = req('post', '/lots', LT, CID, {'lot_no': f'{MARK}-X'})
check('T1.6 无权限岗建批次 403', code == 403, f'{code}')
code, d = req('get', '/trace/lot', LT, CID)
check('T1.7 无权限岗追溯查询 403', code == 403, f'{code}')
code, d = req('get', '/lots', PT, CID)
check('T1.8 production 权限岗可查批次', code == 200, f'{code}')

# ============================================================
# T2 建床（带来源单号）+ 编菲 + 登记（追溯时间线素材）
# ============================================================
code, d = req('post', '/productions', AT, CID, {'style': style.id, 'bed_no': BED,
                                              'source_order_no': 'PO-E2ETR-001'})
check('T2.1 建床带来源单号', code == 201 and d.get('source_order_no') == 'PO-E2ETR-001',
      f'{code} {str(d)[:120]}')
PROD = d['id']

code, d = req('put', f'/productions/{PROD}', AT, CID,
              {'style': style.id, 'bed_no': BED, 'source_order_no': 'PO-E2ETR-002'})
check('T2.2 床次补改来源单号', code == 200 and d.get('source_order_no') == 'PO-E2ETR-002',
      f'{code} {str(d)[:100]}')

code, d = req('post', '/tickets/batch', AT, CID,
              {'production_id': PROD, 'items': [{'color': '宝蓝', 'size': '110', 'qty': 120}]})
check('T2.3 编菲', code == 201 and d and d[0].get('bundle_no'), f'{code} {str(d)[:100]}')
TICKET = d[0]['id']

code, d = req('post', '/records', AT, CID, {'ticket_id': TICKET, 'process_id': process.id,
                                          'qty': 60})
check('T2.4 登记记录', code == 201, f'{code} {str(d)[:100]}')

# ============================================================
# T3 血缘写入路径一：手工挂批次（一床多批次 + 用量）
# ============================================================
code, d = req('put', f'/productions/{PROD}/lot-links', AT, CID,
              {'items': [{'lot_id': LOT1, 'qty': '800'}, {'lot_id': LOT2, 'qty': '200'}]})
check('T3.1 手工挂两批次含用量', code == 200 and len(d) == 2
      and {x['lot_no'] for x in d} == {f'{MARK}-LOT1', f'{MARK}-LOT2'}, f'{code} {str(d)[:120]}')

code, d = req('put', f'/productions/{PROD}/lot-links', AT, CID,
              {'items': [{'lot_id': LOT1, 'qty': '900'}]})
check('T3.2 血缘整表重建', code == 200 and len(d) == 1 and d[0]['qty'] == '900.00',
      f'{code} {str(d)[:120]}')

code, d = req('put', f'/productions/{PROD}/lot-links', LT, CID, {'items': []})
check('T3.3 无权限岗挂血缘 403', code == 403, f'{code}')

code, d = req('delete', f'/lots/{LOT1}', AT, CID)
check('T3.4 有血缘批次禁删', code == 400, f'{code}')

# ============================================================
# T4 追溯三接口（手工血缘路径）
# ============================================================
code, d = req('get', f'/trace/lot/{LOT1}', AT, CID)
check('T4.1 正向：批次→床次', code == 200 and d['impact']['production_count'] == 1
      and d['productions'][0]['bed_no'] == BED
      and d['productions'][0]['source_order_no'] == 'PO-E2ETR-002'
      and d['impact']['ticket_count'] >= 1, f'{code} {str(d)[:200]}')

code, d = req('get', f'/trace/reverse?style_no={BED}&color=宝蓝', AT, CID)
ok = (code == 200 and any(m['production_id'] == PROD and m['source_order_no'] == 'PO-E2ETR-002'
                          for m in d.get('matches', [])))
check('T4.2 反查：款式+颜色→床次+批次', ok, f'{code} {str(d)[:200]}')

code, d = req('get', f'/trace/ticket/{TICKET}', AT, CID)
check('T4.3 终端：菲票全链路（血缘+时间线）', code == 200
      and d['production']['source_order_no'] == 'PO-E2ETR-002'
      and len(d['lots']) == 1 and d['lots'][0]['lot_no'] == f'{MARK}-LOT1'
      and len(d['records']) == 1 and d['records'][0]['process'] == f'{MARK}工序',
      f'{code} {str(d)[:250]}')

code, d = req('get', f'/trace/production/{PROD}', AT, CID)
check('T4.4 床次视角追溯', code == 200 and d['ticket_count'] == 1
      and d['lots'][0]['lot_no'] == f'{MARK}-LOT1', f'{code} {str(d)[:150]}')

# 旧票七段码兼容：手工拼 qr → scan/parse → 终端
comp = company
qr = '|'.join([comp.company_no, BED, style.style_no, '1', '宝蓝', '110', '120'])
code, d = req('post', '/scan/parse', AT, CID, {'qr': qr})
check('T4.5 旧票七段码 parse 仍通', code == 200 and d.get('ticket', {}).get('id') == TICKET,
      f'{code} {str(d)[:150]}')

# ============================================================
# T5 仓库三单据 → 批次诞生 + 领料钩子写血缘
# ============================================================
code, d = req('post', '/purchase-orders', AT, CID,
              {'order_no': f'{MARK}-CG01', 'supplier_name': f'{MARK}布行',
               'ordered_at': timezone.localdate().isoformat(),
               'items': [{'fabric_name': '32支精棉', 'color': '宝蓝', 'qty': '500'}]})
check('T5.1 建采购单', code == 201 and d.get('status') == 'draft', f'{code} {str(d)[:120]}')
PO = d['id']

code, d = req('post', f'/purchase-orders/{PO}/confirm', AT, CID)
check('T5.2 采购确认', code == 200 and d.get('status') == 'done', f'{code}')

code, d = req('put', f'/purchase-orders/{PO}', AT, CID, {'supplier_name': 'x'})
check('T5.3 完成采购单禁改', code == 400, f'{code}')

code, d = req('post', '/stock-ins', AT, CID,
              {'in_no': f'{MARK}-RK01', 'purchase': PO, 'source_type': 'purchase',
               'items': [{'fabric_name': '32支精棉', 'color': '宝蓝',
                          'lot_no': f'{MARK}-SUP-889', 'qty': '500', 'unit': '米'}]})
check('T5.4 入库（新批次诞生）', code == 201, f'{code} {str(d)[:150]}')
RK = d['id']
LOT3 = d['items'][0]['lot_id']
lot3 = Lot.all_objects.get(pk=LOT3)
check('T5.5 入库行回填批次来源', lot3.lot_no == f'{MARK}-SUP-889'
      and lot3.source_in_id is not None
      and lot3.source_in.stock_in_id == RK, f'{lot3.lot_no} src={lot3.source_in_id}')

code, d = req('post', f'/stock-ins/{RK}/confirm', AT, CID)
check('T5.6 入库确认', code == 200 and d.get('status') == 'done', f'{code}')

code, d = req('post', '/material-issues', AT, CID,
              {'issue_no': f'{MARK}-LL01',
               'items': [{'production_id': PROD, 'lot_id': LOT3, 'qty': '100'}]})
check('T5.7 建领料单', code == 201 and d.get('status') == 'draft', f'{code} {str(d)[:150]}')
LL = d['id']

code, d = req('post', f'/material-issues/{LL}/confirm', AT, CID)
check('T5.8 领料确认（钩子写血缘）', code == 200 and d.get('status') == 'confirmed', f'{code}')

links = {l.lot_id: l for l in ProductionLotLink.all_objects.filter(production_id=PROD)}
check('T5.9 血缘含领料批次', LOT3 in links, f'{sorted(links)}')

code, d = req('post', '/material-issues', AT, CID,
              {'issue_no': f'{MARK}-LL02',
               'items': [{'production_id': PROD, 'lot_id': LOT3, 'qty': '50'}]})
LL2 = d['id']
req('post', f'/material-issues/{LL2}/confirm', AT, CID)
links = {l.lot_id: l for l in ProductionLotLink.all_objects.filter(production_id=PROD)}
check('T5.10 同床次同批次多次领料用量累加', links[LOT3].qty == 150,
      f'qty={links[LOT3].qty if LOT3 in links else None}')

code, d = req('get', f'/trace/lot/{LOT3}', AT, CID)
check('T5.11 领料路径血缘可正向追溯', code == 200 and d['impact']['production_count'] == 1,
      f'{code} {str(d)[:150]}')

code, d = req('post', '/material-issues', AT, CID,
              {'issue_no': f'{MARK}-LL03', 'items': [{'production_id': PROD,
                                                      'lot_id': LOT3, 'qty': '10'}]})
LL3 = d['id']
req('post', f'/material-issues/{LL3}/confirm', AT, CID)
code, d = req('delete', f'/material-issues/{LL3}', AT, CID)
check('T5.12 已确认领料单禁删', code == 400, f'{code}')

# ============================================================
# T6 边界与幂等回归素材
# ============================================================
code, d = req('get', f'/trace/ticket/{TICKET}', AT, CID)
check('T6.1 终端含领料后完整血缘（两批次）', code == 200 and len(d['lots']) == 2,
      f'{code} lots={len(d.get("lots", []))}')

code, d = req('post', '/lots', AT, CID, {'lot_no': ''})
check('T6.2 空批次号被拒', code == 400, f'{code}')

code, d = req('get', f'/lots/{LOT2}', AT, CID)
code2, d2 = req('delete', f'/lots/{LOT2}', AT, CID)
check('T6.3 无引用批次可删', code2 == 204, f'{code2}')

print()
print(f'==== traceability_tests: PASS={len(PASS)} FAIL={len(FAIL)} ====')
for f in FAIL:
    print('  FAIL:', f)
