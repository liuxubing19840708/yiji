# -*- coding: utf-8 -*-
"""看板模板（apps/kanban_template）幂等测试套件。

运行（与既有 e2e 同法）：
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe manage.py shell -c \
  "exec(compile(open('kanban_template_tests.py', encoding='utf-8').read(), 'kt', 'exec'))"

覆盖：模板 CRUD 权限（非管理员 403）/ 字段注册表与公式求值（四则+条件+注入拦截）/
Excel 两级表头解析 / 渲染接口按模板输出 / 默认模板回落 / 初始化命令幂等。
两连跑全过（预清理按「看板E2E」标记删除，结束恢复现场）。
"""
import io
import json

from django.core.management import call_command
from django.test import Client

from apps.accounts.models import Company, Post as PostM, User, UserCompany
from apps.catalog.models import Process, Style, Ticket
from apps.kanban_template.models import KanbanColumn, KanbanTemplate
from apps.production.models import Production, ProductionProcess
from apps.records.models import Record

c = Client()
PASS, FAIL = [], []
MARK = '看板E2E'
ADMIN_PHONE = '13800000001'
EMP_PHONE = '13899990001'
BED = 'KANE2E1'
STYLE_NO = 'KAN-E2E-1'


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    print(('PASS' if cond else 'FAIL'), name, ('' if cond else f'| {detail}'))


def req(method, path, token=None, cid=None, data=None, **kw):
    extra = {}
    if token:
        extra['HTTP_AUTHORIZATION'] = f'Bearer {token}'
    if cid:
        extra['HTTP_X_COMPANY_ID'] = str(cid)
    resp = getattr(c, method)(f'/api{path}', data=data, content_type='application/json',
                              **extra, **kw)
    try:
        body = json.loads(resp.content.decode('utf-8'))
    except Exception:
        body = {}
    return resp.status_code, body


def preclean():
    """幂等预清理：只删本套件标记的数据。"""
    # 看板模板（按标记公司的模板与列）
    for t in KanbanTemplate.all_objects.filter(name__contains=MARK):
        KanbanColumn.all_objects.filter(template=t).delete()
        KanbanTemplate.all_objects.filter(pk=t.pk).delete()
    # 渲染测试数据（床次 KANE2E1）：先子后父（SQLite FK 无 CASCADE）
    Record.all_objects.filter(ticket__production__bed_no=BED)._raw_delete(Record.all_objects.db)
    Ticket.all_objects.filter(production__bed_no=BED)._raw_delete(Ticket.all_objects.db)
    ProductionProcess.all_objects.filter(production__bed_no=BED)._raw_delete(ProductionProcess.all_objects.db)
    Production.all_objects.filter(bed_no=BED)._raw_delete(Production.all_objects.db)
    Style.all_objects.filter(style_no=STYLE_NO)._raw_delete(Style.all_objects.db)
    Process.all_objects.filter(name__contains=MARK)._raw_delete(Process.all_objects.db)
    # 员工与岗位
    u = User.objects.filter(phone=EMP_PHONE).first()
    if u:
        UserCompany.all_objects.filter(user=u)._raw_delete(UserCompany.all_objects.db)
        u.delete()
    PostM.all_objects.filter(name__contains=MARK)._raw_delete(PostM.all_objects.db)


def ensure_admin():
    """动态建厂（T0 决议①）：旧断言绑定 company_id=2/3 与固定管理员手机号，
    setup 负责保证其存在——库里有则复用，没有则按固定 id 播种（跨次幂等）。"""
    admin = User.objects.filter(phone=ADMIN_PHONE).first()
    if admin is None:
        admin = User.objects.create_user(username=ADMIN_PHONE, phone=ADMIN_PHONE,
                                         password='yisao1234',
                                         real_name=MARK + '管理员')
    return admin


def ensure_company(cid, admin):
    """按固定 id 补齐测试厂 + 管理员岗位 + 管理员在职关系（幂等）。"""
    co = Company.all_objects.filter(pk=cid).first()
    if co is None:
        co = Company.objects.create(id=cid, name=f'{MARK}厂{cid}',
                                    company_no=f'9000{cid:02d}', creator=admin)
    elif co.is_deleted:
        Company.objects.filter(pk=cid).update(is_deleted=False)
        co.refresh_from_db()
    post = PostM.all_objects.filter(company_id=cid, is_admin=True).first()
    if post is None:
        # 岗位名不带 MARK：随厂持久复用，避免 preclean 按 MARK 清理时撞 UserCompany 外键
        post = PostM.objects.create(company_id=cid, name=f'系统管理岗{cid}',
                                    is_admin=True, is_system=True)
    elif post.is_deleted:
        PostM.objects.filter(pk=post.pk).update(is_deleted=False)
    uc = UserCompany.all_objects.filter(user=admin, company_id=cid).first()
    if uc is None:
        UserCompany.objects.create(user=admin, company_id=cid, post=post,
                                   status=UserCompany.Status.ACTIVE)
    else:
        UserCompany.objects.filter(pk=uc.pk).update(
            is_deleted=False, status=UserCompany.Status.ACTIVE, post_id=post.pk)
    return co


def setup_fixtures():
    """动态建厂：管理员 + 公司2/公司3（旧断言的固定 id）+ 普通员工 + 渲染数据。"""
    code, d = req('post', '/auth/login', data={'phone': ADMIN_PHONE, 'password': 'yisao1234'})
    assert code == 200, f'管理员登录失败 {code} {d}'
    at = d['access']
    admin = ensure_admin()
    ensure_company(2, admin)
    ensure_company(3, admin)

    user = User.objects.create_user(username=EMP_PHONE, phone=EMP_PHONE,
                                    password='kane2e1234', real_name=MARK + '员工')
    post = PostM.objects.filter(company_id=2, is_admin=False, is_deleted=False).first()
    if not post:
        post = PostM.objects.create(company_id=2, name=MARK + '普通岗', is_admin=False)
    UserCompany.objects.create(user=user, company_id=2, post=post,
                               status=UserCompany.Status.ACTIVE)

    style = Style.objects.create(company_id=2, style_no=STYLE_NO, name=MARK + '款')
    prod = Production.objects.create(company_id=2, style=style, bed_no=BED, total_qty=100,
                                     plan_qty=80, fabric='棉布', source_order_no='SO-E2E',
                                     order_type='外发')
    ticket = Ticket.objects.create(company_id=2, production=prod, bed_no=BED, bundle_no='B1',
                                   color='红色', size='M', qty=100, qr_code='KAN-E2E-QR')
    # 工序进度数据：1 道订单工序 + 1 条登记（进度 = 1/1 = 100%）
    proc = Process.objects.create(company_id=2, name=MARK + '缝制', is_fixed=False)
    ProductionProcess.objects.create(production=prod, process=proc, price=1)
    Record.objects.create(company_id=2, ticket=ticket, process=proc, user=user, qty=5)
    return at, user


preclean()
AT, EMP_USER = setup_fixtures()
code, me = req('get', '/auth/me', AT, 2)
CID = 2

# 员工登录（非管理员 token）
code, d = req('post', '/auth/login', data={'phone': EMP_PHONE, 'password': 'kane2e1234'})
EMP_AT = d.get('access', '')
check('0.1 员工登录', code == 200 and EMP_AT, f'{code}')

# ============================================================
# S1 字段注册表 + 模板 CRUD 权限
# ============================================================
code, d = req('get', '/kanban/fields', EMP_AT, CID)
check('1.1 员工可读字段字典', code == 200 and len(d.get('results', [])) >= 10, f'{code}')

code, d = req('get', '/kanban/templates', EMP_AT, CID)
check('1.2 非管理员 GET 模板列表 403', code == 403, f'{code}')

cols_ok = {'name': MARK + '模板A', 'is_default': True, 'columns': [
    {'seq': 0, 'title': '款号', 'bind_type': 'field', 'field_key': 'style_no',
     'extra': {'merge': 'block'}},
    {'seq': 1, 'title': '订单数量', 'bind_type': 'field', 'field_key': 'order_qty'},
    {'seq': 2, 'title': '差额', 'bind_type': 'formula', 'expr': 'cut_qty - order_qty'},
    {'seq': 3, 'title': '完成率%', 'bind_type': 'formula',
     'expr': 'round(cut_qty / cut_total * 100, 1)'},
    {'seq': 4, 'title': '是否裁完', 'bind_type': 'formula',
     'expr': 'IF(cut_qty >= cut_total, 1, 0)'},
    {'seq': 5, 'title': '排单情况', 'bind_type': 'field', 'field_key': 'schedule'},
    {'seq': 6, 'title': '固定列', 'bind_type': 'static', 'static_text': 'OK'},
]}
code, d = req('post', '/kanban/templates', EMP_AT, CID, cols_ok)
check('1.3 非管理员新建模板 403', code == 403, f'{code}')

code, d = req('post', '/kanban/templates', AT, CID, cols_ok)
check('1.4 管理员新建模板 201', code == 201 and d.get('id'), f'{code} {str(d)[:120]}')
TPL_ID = d.get('id')

code, d = req('get', '/kanban/templates', AT, CID)
lst = d.get('results', [])
mine = next((t for t in lst if t['id'] == TPL_ID), None)
check('1.5 列表含新模板且默认标记', mine is not None and mine['is_default'] is True, f'{code}')

# 公式注入/未知字段拦截（is_default=False：坏模板不得劫持默认模板）
bad_bodies = [
    ('2.1 __import__ 拦截', dict(cols_ok, name=MARK + '坏1', is_default=False, columns=[
        {'seq': 0, 'title': 'x', 'bind_type': 'formula',
         'expr': '__import__("os").system("dir")'}])),
    ('2.2 未知字段拦截', dict(cols_ok, name=MARK + '坏2', is_default=False, columns=[
        {'seq': 0, 'title': 'x', 'bind_type': 'formula', 'expr': 'foo_qty + 1'}])),
    ('2.3 属性访问拦截', dict(cols_ok, name=MARK + '坏3', is_default=False, columns=[
        {'seq': 0, 'title': 'x', 'bind_type': 'formula', 'expr': 'cut_qty.__class__'}])),
    ('2.4 超长字符串常量拦截', dict(cols_ok, name=MARK + '坏4', is_default=False, columns=[
        {'seq': 0, 'title': 'x', 'bind_type': 'formula', 'expr': "'" + 'x' * 65 + "'"}])),
    ('2.5 任意函数调用拦截', dict(cols_ok, name=MARK + '坏5', is_default=False, columns=[
        {'seq': 0, 'title': 'x', 'bind_type': 'formula', 'expr': 'eval("1+1")'}])),
]
for name, body in bad_bodies:
    code, d = req('post', '/kanban/templates', AT, CID, body)
    check(name, code == 400, f'{code} {str(d)[:100]}')

# 三元/布尔 DSL（应通过）
code, d = req('post', '/kanban/templates', AT, CID, dict(
    cols_ok, name=MARK + '条件列', is_default=False, columns=[
    {'seq': 0, 'title': '状态', 'bind_type': 'formula',
     'expr': 'IF(order_qty > 50 and cut_qty > 0, 1, 0)'},
    {'seq': 1, 'title': '三元', 'bind_type': 'formula',
     'expr': '100 if cut_qty > 0 else 0'},
]))
check('2.6 条件表达式（IF函数/and/三元）通过', code == 201, f'{code} {str(d)[:120]}')
COND_TPL = d.get('id')

# ============================================================
# S3 Excel 表头导入（两级/合并表头）
# ============================================================
def build_xlsx(two_level=True):
    from openpyxl import Workbook
    from openpyxl.utils import get_column_letter
    wb = Workbook()
    ws = wb.active
    if two_level:
        ws['A1'] = '1组'
        ws.merge_cells('A1:C1')
        ws['D1'] = '基础信息'
        ws.merge_cells('D1:E1')
        ws['A2'], ws['B2'], ws['C2'] = '成品', '半成品', '退货'
        ws['D2'], ws['E2'] = '款号', '颜色'
    else:
        ws['A1'], ws['B1'] = '款号', '已裁数量'
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    buf.name = 'header.xlsx'
    return buf


def upload(filebuf, header_row=None):
    data = {}
    if header_row:
        data['header_row'] = str(header_row)
    extra = {'HTTP_AUTHORIZATION': f'Bearer {AT}', 'HTTP_X_COMPANY_ID': str(CID)}
    resp = c.post('/api/kanban/import-header', data={'file': filebuf, **data}, **extra)
    try:
        return resp.status_code, json.loads(resp.content.decode('utf-8'))
    except Exception:
        return resp.status_code, {}


code, d = upload(build_xlsx(True))
cols = d.get('columns', [])
check('3.1 两级表头解析', code == 200 and d.get('two_level') is True and len(cols) == 5, f'{code} {str(d)[:160]}')
check('3.2 父级/跨列正确', cols and cols[0]['parent'] == '1组' and cols[0]['col_span'] == 1
      and cols[3]['parent'] == '基础信息', str(cols[:4]))
check('3.3 叶子标题正确', [x['title'] for x in cols] == ['成品', '半成品', '退货', '款号', '颜色'],
      str([x['title'] for x in cols]))

code, d = upload(build_xlsx(False))
check('3.4 单级表头', code == 200 and d.get('two_level') is False
      and [x['title'] for x in d.get('columns', [])] == ['款号', '已裁数量'], f'{code} {str(d)[:120]}')

code, d = upload(build_xlsx(True), header_row=2)
check('3.5 指定表头行', code == 200 and d.get('header_row') == 2, f'{code} {str(d)[:120]}')

# ============================================================
# S4 渲染接口（按模板输出 + 默认回落）
# ============================================================
def get_render(qs, token=AT, cid=CID):
    extra = {'HTTP_AUTHORIZATION': f'Bearer {token}', 'HTTP_X_COMPANY_ID': str(cid)}
    resp = c.get(f'/api/kanban/render?{qs}', **extra)
    try:
        return resp.status_code, json.loads(resp.content.decode('utf-8'))
    except Exception:
        return resp.status_code, {}


code, d = get_render('range=today')
check('4.1 默认模板渲染 200', code == 200 and d.get('columns') and d.get('groups') is not None, f'{code}')
check('4.2 默认标记=本模板（模板A已设默认）', d.get('template', {}).get('name') == MARK + '模板A',
      str(d.get('template'))[:80])

blocks = [b for g in d.get('groups', []) for b in g.get('blocks', [])]
target = next((b for b in blocks if any(
    cell.get('text') == STYLE_NO for row in b.get('rows', []) for cell in row.get('cells', []))), None)
check('4.3 含测试款块', target is not None, str(d.get('groups'))[:160])
if target:
    row0 = target['rows'][0]
    texts = [cell['text'] for cell in row0['cells']]
    # 列序：款号|订单数量|差额|完成率%|是否裁完|排单情况|固定列
    check('4.4 公式列求值（cut_qty-order_qty=0）', texts[2] == '0', str(texts))
    check('4.5 百分比公式（100）', texts[3] == '100', str(texts))
    check('4.6 IF 条件列（1）', texts[4] == '1', str(texts))
    check('4.7 todo 字段占位「—」', texts[5] == '—', str(texts))
    check('4.8 静态列', texts[6] == 'OK', str(texts))
    # block 合并列：款号列在块内行数>1 时才一格；这里单行，检查段存在即可
    check('4.9 列带 merge 配置', any(c.get('merge') == 'block' for c in d.get('columns', [])),
          str(d.get('columns'))[:120])

code, d = get_render('range=today', token=EMP_AT, cid=CID)
check('4.10 员工可读渲染接口', code == 200, f'{code}')

# 渲染失败回落：公式在保存期合法但渲染期抛错（除零）→ 单元格落占位，不白屏
code, d = req('post', '/kanban/templates', AT, CID, dict(
    cols_ok, name=MARK + '除零', is_default=False, columns=[
    {'seq': 0, 'title': 'boom', 'bind_type': 'formula', 'expr': 'cut_qty / 0'}]))
check('4.11 除零公式保存期放行（渲染期兜底）', code == 201, f'{code}')
ZTPL = d.get('id')
if ZTPL:
    code, d = get_render(f'range=today&template_id={ZTPL}')
    cells = [cell['text'] for g in d.get('groups', []) for b in g.get('blocks', [])
             for row in b.get('rows', []) for cell in row.get('cells', [])]
    check('4.12 求值异常落占位符', code == 200 and cells and all(x == '—' for x in cells),
          f'{code} {str(cells)[:60]}')

# ============================================================
# S7 全量字段注册表 + detail 渲染 + 宽度归一（P3）
# ============================================================
from apps.kanban_template.fields import KANBAN_FIELDS, fields_payload

need_keys = ['plan_qty', 'source_order_no', 'fabric', 'process_percent', 'process_done',
             'process_total', 'ticket_count', 'size_text', 'bed_count', 'order_ym',
             'in_this_month', 'in_this_week', 'style_customer_name', 'style_price']
missing = [k for k in need_keys if k not in KANBAN_FIELDS]
check('7.1 注册表含全量新字段', not missing, f'缺 {missing}')

fp = fields_payload()
check('7.2 注册表每项带类目+说明', all(p.get('category') and p.get('desc') for p in fp),
      str(fp[:1]))
cats = [p['category'] for p in fp]
check('7.3 注册表按类目分组排序', cats == sorted(cats, key=cats.index) and len(set(cats)) >= 4,
      str(set(cats)))

# detail 渲染：模板绑全量新字段，渲染值来自 build_plan_rows(detail=True)
cols_full = {'name': MARK + '全字段', 'is_default': False, 'columns': [
    {'seq': 0, 'title': '计划', 'bind_type': 'field', 'field_key': 'plan_qty'},
    {'seq': 1, 'title': '面料', 'bind_type': 'field', 'field_key': 'fabric'},
    {'seq': 2, 'title': '来源单号', 'bind_type': 'field', 'field_key': 'source_order_no'},
    {'seq': 3, 'title': '订单类型', 'bind_type': 'field', 'field_key': 'order_type'},
    {'seq': 4, 'title': '进度', 'bind_type': 'field', 'field_key': 'process_percent'},
    {'seq': 5, 'title': '扎数', 'bind_type': 'field', 'field_key': 'ticket_count'},
    {'seq': 6, 'title': '尺码集合', 'bind_type': 'field', 'field_key': 'size_text'},
    {'seq': 7, 'title': '床次数', 'bind_type': 'field', 'field_key': 'bed_count'},
]}
code, d = req('post', '/kanban/templates', AT, CID, cols_full)
check('7.4 全字段模板 201', code == 201 and d.get('id'), f'{code} {str(d)[:120]}')
TPLF = d.get('id')
if TPLF:
    code, d = get_render(f'range=today&template_id={TPLF}')
    blk = next((b for g in d.get('groups', []) for b in g.get('blocks', []) if b.get('rows')),
               None)
    texts = [cell['text'] for cell in blk['rows'][0]['cells']] if blk else []
    check('7.5 计划裁货数', code == 200 and texts and texts[0] == '80', str(texts))
    check('7.6 面料/来源单号/订单类型', texts[1:4] == ['棉布', 'SO-E2E', '外发'], str(texts))
    check('7.7 工序进度 100%', len(texts) > 4 and texts[4] == '100', str(texts))
    check('7.8 扎数/尺码/床次数', len(texts) > 7 and texts[5:8] == ['1', 'M', '1'], str(texts))

# 公式引用新字段 + 字符串常量比较（≤64 字符放行）
code, d = req('post', '/kanban/templates', AT, CID, dict(
    cols_ok, name=MARK + '串常量', is_default=False, columns=[
    {'seq': 0, 'title': '外发缺口', 'bind_type': 'formula',
     'expr': "IF(order_type == '外发', plan_qty - cut_qty, 0)"}]))
check('7.9 公式可用字符串常量+新字段', code == 201, f'{code} {str(d)[:120]}')
STPL = d.get('id')
if STPL:
    code, d = get_render(f'range=today&template_id={STPL}')
    blk = next((b for g in d.get('groups', []) for b in g.get('blocks', []) if b.get('rows')),
               None)
    v = blk['rows'][0]['cells'][0]['text'] if blk else ''
    check('7.10 串常量公式求值（80-100=-20）', v == '-20', str(v))

code, d = req('post', '/kanban/templates', AT, CID, dict(
    cols_ok, name=MARK + '坏6', columns=[
    {'seq': 0, 'title': 'x', 'bind_type': 'formula', 'expr': "'" + 'x' * 65 + "'"}]))
check('7.11 超长字符串常量拒绝', code == 400, f'{code}')

# 宽度归一：0/''/'auto'→0 自适应；缺省→150；数值原样
code, d = req('post', '/kanban/templates', AT, CID, dict(
    cols_ok, name=MARK + '宽度', is_default=False, columns=[
    {'seq': 0, 'title': 'w0', 'bind_type': 'static', 'static_text': 'a', 'extra': {'width': 0}},
    {'seq': 1, 'title': 'w1', 'bind_type': 'static', 'static_text': 'b', 'extra': {'width': ''}},
    {'seq': 2, 'title': 'w2', 'bind_type': 'static', 'static_text': 'c', 'extra': {}},
    {'seq': 3, 'title': 'w3', 'bind_type': 'static', 'static_text': 'd', 'extra': {'width': 'auto'}},
    {'seq': 4, 'title': 'w4', 'bind_type': 'static', 'static_text': 'e', 'extra': {'width': 210}},
]))
check('7.12 宽度模板 201', code == 201 and d.get('id'), f'{code} {str(d)[:120]}')
WTPL = d.get('id')
if WTPL:
    code, d = get_render(f'range=today&template_id={WTPL}')
    ws = [col.get('width') for col in d.get('columns', [])]
    check('7.13 宽度归一 [0,0,150,0,210]', ws == [0, 0, 150, 0, 210], str(ws))


before = KanbanTemplate.objects.filter(company_id=CID).count()
# 用独立公司（公司 3）验证无模板回落：删其模板 → 渲染 → 恢复
code, mines = req('get', '/companies/mine', AT)
C3 = next((x['id'] for x in mines if x['id'] == 3), None)
check('5.1 管理员是公司3成员', C3 == 3, str(mines)[:80])
if C3:
    n3 = KanbanTemplate.all_objects.filter(company_id=C3).count()
    KanbanColumn.all_objects.filter(template__company_id=C3).delete()
    KanbanTemplate.all_objects.filter(company_id=C3).delete()
    code, d = get_render('range=month', cid=C3)
    # 内置默认列 11+订单号/订单数量=13（2026-10-21）
    check('5.2 无模板回落内置默认', code == 200 and d.get('template', {}).get('id') is None
          and len(d.get("columns", [])) == 13, f"{code} {str(d.get('template'))[:80]}")
    call_command('seed_kanban_templates')
    check('5.3 seed 恢复公司3默认模板', KanbanTemplate.objects.filter(company_id=C3).count() == 1)

cnt0 = KanbanTemplate.objects.count()
call_command('seed_kanban_templates')
cnt1 = KanbanTemplate.objects.count()
call_command('seed_kanban_templates')
cnt2 = KanbanTemplate.objects.count()
check('5.4 初始化命令幂等（两连跑数量不变）', cnt0 == cnt1 and cnt1 == cnt2,
      f'{cnt0}->{cnt1}->{cnt2}')

# 模板更新/删除
code, d = req('put', f'/kanban/templates/{TPL_ID}', AT, CID, dict(cols_ok, name=MARK + '模板A2'))
check('6.1 更新模板', code == 200 and d.get('name') == MARK + '模板A2', f'{code}')
code, d = req('put', f'/kanban/templates/{COND_TPL}/default', AT, CID)
check('6.2 设默认模板', code == 200, f'{code}')
code, d = req('delete', f'/kanban/templates/{COND_TPL}', AT, CID)
check('6.3 删除模板', code == 200, f'{code}')
code, d = req('get', f'/kanban/templates/{COND_TPL}', AT, CID)
check('6.4 删除后详情 404', code == 404, f'{code}')

# ============================================================
# 清理（保证两连跑幂等）
# ============================================================
for t in KanbanTemplate.all_objects.filter(name__contains=MARK):
    KanbanColumn.all_objects.filter(template=t).delete()
    KanbanTemplate.all_objects.filter(pk=t.pk).delete()
Record.all_objects.filter(ticket__production__bed_no=BED)._raw_delete(Record.all_objects.db)
Ticket.all_objects.filter(production__bed_no=BED)._raw_delete(Ticket.all_objects.db)
ProductionProcess.all_objects.filter(production__bed_no=BED)._raw_delete(ProductionProcess.all_objects.db)
Production.all_objects.filter(bed_no=BED)._raw_delete(Production.all_objects.db)
Style.all_objects.filter(style_no=STYLE_NO)._raw_delete(Style.all_objects.db)
Process.all_objects.filter(name__contains=MARK)._raw_delete(Process.all_objects.db)
u = User.objects.filter(phone=EMP_PHONE).first()
if u:
    UserCompany.all_objects.filter(user=u)._raw_delete(UserCompany.all_objects.db)
    u.delete()
PostM.all_objects.filter(name__contains=MARK)._raw_delete(PostM.all_objects.db)

print()
print(f'=== 看板模板测试完成：PASS={len(PASS)} FAIL={len(FAIL)} ===')
if FAIL:
    print('失败项:', FAIL)
