# -*- coding: utf-8 -*-
"""看板模板全模块分支覆盖套件（apps/kanban_template + apps/core/smoke.py）。

目标：为全仓分支覆盖率 ≥80% 门禁补回 kanban_template 各文件缺口
（views/excel/web/formula/services/seed 命令），顺带跑通 core/smoke.py 冒烟。

覆盖手段：
  - HTTP 端到端：模板 CRUD 全校验分支 / Excel 表头导入（合法/两级/纵向合并/
    越界/超大/坏后缀/坏文件）/ 试跑 / 渲染 / 默认模板切换（django.test.Client）；
  - 函数级：formula DSL 白名单各拒绝分支 / services 归一与兜底 /
    excel 解析各形态（BytesIO 直调）；
  - web.py 静态托管：临时目录 monkeypatch（运行时替换，不改产品代码）；
  - seed_kanban_templates 命令：Company.objects.all 运行时限定为本厂
    （绝不触碰真实厂 86 等任何其它公司）；
  - smoke.py：savepoint 包裹执行 + 回滚（净写入为 0，前置校验目标公司非 86）。

数据隔离：专属测试厂「看板套件厂(可删)」，业务数据前缀 KBST；
每轮开头硬清本厂业务行（先子后父），幂等可重复跑；
绝不触碰 company id=86（汉川旭东服饰，真实厂）的任何数据。

跑法：
  cd "D:\\project\\扫菲\\backend" && PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe \
    manage.py shell -c "exec(compile(open('test_kanban_suite.py',encoding='utf-8').read(),'test_kanban_suite.py','exec'))"
"""
import io
import json
import os
import shutil
import sys
import tempfile
from io import BytesIO

from django.contrib.auth.hashers import make_password
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db import transaction
from django.test import Client
from django.utils import timezone
from openpyxl import Workbook

from apps.accounts.models import Company, Post as PostM, User, UserCompany
from apps.accounts.services import create_company_with_owner
from apps.catalog.models import Customer, Process, Style, StyleProcess, Ticket
from apps.kanban_template import excel as kb_excel
from apps.kanban_template import formula as kb_formula
from apps.kanban_template import services as kb_services
from apps.kanban_template import web as kb_web
from apps.kanban_template.models import KanbanColumn, KanbanTemplate
from apps.orders.models import Order, OrderItem
from apps.production.models import Production, ProductionProcess
from apps.records.models import Record

PASS, FAIL = [], []


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    print(('PASS' if cond else 'FAIL'), name, ('' if cond else f'| 预期外: {detail}'))


def api(method, path, token=None, cid=None, data=None):
    c = Client()
    extra = {}
    if token:
        extra['HTTP_AUTHORIZATION'] = f'Bearer {token}'
    if cid:
        extra['HTTP_X_COMPANY_ID'] = str(cid)
    fn = getattr(c, method.lower())
    r = fn(f'/api{path}',
           data=json.dumps(data) if data is not None else None,
           content_type='application/json', **extra)
    try:
        body = json.loads(r.content.decode('utf-8'))
    except Exception:
        body = {}
    return r.status_code, body


def upload(path, token, cid, data):
    """multipart 上传（import-header 用）。"""
    c = Client()
    r = c.post(f'/api{path}', data,
               HTTP_AUTHORIZATION=f'Bearer {token}',
               HTTP_X_COMPANY_ID=str(cid))
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


# ================= 建厂（专属测试厂，可整厂清；绝不触碰 86） =================
CO = '看板套件厂(可删)'
OWNER = '13700009801'
WORKER = '13700009802'

company = Company.objects.filter(name=CO).first()
if company is None:
    owner = ensure_user(OWNER, 'KBST厂长')
    company, _ = create_company_with_owner(owner, CO)
else:
    admin_post = PostM.objects.get(company=company, is_admin=True, is_deleted=False)
    uc = (UserCompany.objects.select_related('user')
          .filter(company=company, post=admin_post, is_deleted=False)
          .order_by('id').first())
    owner = uc.user if uc else ensure_user(OWNER, 'KBST厂长')
CID = company.id
assert CID != 86, '禁止在真实厂 86 上运行本套件'

# ================= 全清本公司业务数据（先子后父；专用厂允许整清） =================
Record.objects.filter(company=company).delete()
Ticket.all_objects.filter(company=company).delete()
ProductionProcess.objects.filter(production__company=company).delete()
Production.all_objects.filter(company=company).delete()
OrderItem.all_objects.filter(order__company=company).delete()
Order.all_objects.filter(company=company).delete()
StyleProcess.objects.filter(style__company=company).delete()
Style.all_objects.filter(company=company).delete()
Process.all_objects.filter(company=company).delete()
Customer.all_objects.filter(company=company).delete()
KanbanColumn.all_objects.filter(template__company=company).delete()
KanbanTemplate.all_objects.filter(company=company).delete()
UserCompany.all_objects.filter(company=company, post__is_admin=False).delete()
PostM.all_objects.filter(company=company, is_admin=False).delete()

worker = ensure_user(WORKER, 'KBST员工')
UserCompany.objects.create(
    user=worker, company=company,
    post=PostM.objects.create(company=company, name='KBST普工', permissions=[]),
    status=UserCompany.Status.ACTIVE)

TA = login(owner.phone)
TW = login(WORKER)
check('厂长登录成功', bool(TA), 'token 为空')
check('员工登录成功', bool(TW), 'token 为空')

# ================= 最小渲染数据：1 款 2 床（同款同日两色 → 行/块复用分支） =================
style = Style.objects.create(company=company, style_no='KBST-S1', name='看板套件测试款')
proc = Process.objects.create(company=company, name='KBST缝制', is_fixed=False)
for bed_no, color in [('KBST-A', '黑'), ('KBST-B', '白')]:
    p = Production.objects.create(company=company, style=style, bed_no=bed_no,
                                  total_qty=100, plan_qty=100)
    Ticket.objects.create(company=company, production=p, bed_no=bed_no,
                          bundle_no='1', color=color, size='M', qty=100,
                          qr_code=f'KBST|{bed_no}|1|{color}|M|100')

_name_seq = [0]


def nm():
    _name_seq[0] += 1
    return f'KBST模板{_name_seq[0]}'


def col(seq, title='列', bind='field', key='style_no', expr='', static_text='',
        extra=None):
    return {'seq': seq, 'title': title, 'bind_type': bind, 'field_key': key,
            'expr': expr, 'static_text': static_text, 'extra': extra or {}}


# ================= 一、字段字典 / 权限边界 =================
print('\n===== 1. 字段字典与权限边界 =====')
st, d = api('GET', '/kanban/fields', TA, CID)
keys = [r['key'] for r in d.get('results', [])]
check('fields 200 且注册表非空', st == 200 and len(keys) >= 30, f'http {st}')
from apps.kanban_template.fields import field_label
check('field_label 在册/不在册', field_label('style_no') == '款号'
      and field_label('nope_key') == 'nope_key')
check('fields 类目按序（款式→生产→菲票→订单→时间→占位）',
      [r['category'] for r in d.get('results', [])][0] == '款式'
      and '订单' in {r['category'] for r in d.get('results', [])}, '类目异常')
check('fields 含占位字段 schedule(todo)', 'schedule' in keys and
      next(r for r in d['results'] if r['key'] == 'schedule')['kind'] == 'todo')

st, d = api('GET', '/kanban/fields', TW, CID)
check('fields 员工可读（无 kanban.manage 门槛）', st == 200, f'http {st}')

st, d = api('GET', '/kanban/templates', TW, CID)
check('templates 员工无管理权 → 403', st == 403, f'http {st} {d}')
st, d = api('POST', '/kanban/templates', TW, CID,
            {'name': 'KBST越权', 'columns': [col(0)]})
check('templates 员工新建 → 403', st == 403, f'http {st} {d}')
st, d = api('GET', '/kanban/templates')
check('templates 未登录 → 401', st in (401, 403), f'http {st}')

# ================= 二、模板新建校验分支（POST 全拒绝路径） =================
print('\n===== 2. 模板新建校验分支 =====')
bad_bodies = [
    ('名称为空', {'name': '', 'columns': [col(0)]}),
    ('名称超64', {'name': '长' * 65, 'columns': [col(0)]}),
    ('columns非列表', {'name': nm(), 'columns': 'x'}),
    ('columns为空', {'name': nm(), 'columns': []}),
    ('列数超60', {'name': nm(),
                  'columns': [col(i) for i in range(61)]}),
    ('列非对象', {'name': nm(), 'columns': ['x']}),
    ('列缺标题', {'name': nm(), 'columns': [{'seq': 0, 'bind_type': 'field',
                                             'field_key': 'style_no'}]}),
    ('绑定类型非法', {'name': nm(), 'columns': [col(0, bind='magic')]}),
    ('seq重复', {'name': nm(), 'columns': [col(0), col(0, title='列2')]}),
    ('seq负数', {'name': nm(), 'columns': [col(-1)]}),
    ('未知字段', {'name': nm(), 'columns': [col(0, key='nope')]}),
    ('公式非法', {'name': nm(), 'columns': [col(0, bind='formula', expr='cut_qty +')]}),
    ('数据源非法', {'name': nm(), 'columns': [col(0)], 'dataset': 'bogus'}),
    ('filters非对象', {'name': nm(), 'columns': [col(0)], 'filters': [1]}),
]
for label, body in bad_bodies:
    st, d = api('POST', '/kanban/templates', TA, CID, body)
    check(f'POST 拒绝：{label} → 400', st == 400, f'http {st} {d}')

st, d = api('POST', '/kanban/templates', TA, CID,
            {'name': nm(), 'columns': [
                col(0, title='款号', extra={'link': 'production', 'width': 200}),
                col(1, bind='formula', expr='cut_qty + 1', title='已裁加一'),
                col(2, bind='static', static_text='固定文本', title='固定'),
                col(3, title='extra非对象兜底', extra='x'),
            ], 'filters': {'color': '黑'}})
check('POST 合法（field/formula/static/extra兜底）→ 201', st == 201, f'http {st} {d}')
T1 = d.get('id') if isinstance(d, dict) else None
check('POST 返回4列且extra兜底为{}', st == 201 and len(d.get('columns', [])) == 4
      and d['columns'][3]['extra'] == {}, f"实际 {d.get('columns')}")
check('POST filters 落库回显', st == 201 and d.get('filters') == {'color': '黑'},
      f"实际 {d.get('filters')}")

st, d = api('POST', '/kanban/templates', TA, CID,
            {'name': nm(), 'columns': [col(0), col(1, title='颜色', key='color')],
             'is_default': True})
check('POST is_default=True → 201 且默认', st == 201 and d.get('is_default') is True,
      f'http {st} {d}')
T2 = d.get('id')

st, d = api('POST', '/kanban/templates', TA, CID,
            {'name': nm(), 'columns': [col(0)]})
T3 = d.get('id')
check('POST 普通模板 T3 → 201', st == 201, f'http {st} {d}')

st, d = api('GET', '/kanban/templates', TA, CID)
names = [t['name'] for t in d.get('results', [])]
check('GET 列表含 3 套模板', st == 200 and len(d.get('results', [])) == 3,
      f'http {st} 实际 {len(d.get("results", []))}')
check('列表含默认标记', st == 200 and
      sum(1 for t in d['results'] if t['is_default']) == 1, f'{d}')

# ================= 三、详情 / 更新 / 删除 / 默认切换 =================
print('\n===== 3. 详情/更新/删除/默认切换 =====')
st, d = api('GET', f'/kanban/templates/{T1}', TA, CID)
check('GET 详情 200 含列定义', st == 200 and d.get('id') == T1
      and len(d.get('columns', [])) == 4, f'http {st} {d}')
st, d = api('GET', '/kanban/templates/99999999', TA, CID)
check('GET 详情不存在 → 404', st == 404, f'http {st} {d}')

st, d = api('PUT', f'/kanban/templates/{T1}', TA, CID,
            {'name': nm(), 'columns': [col(0)]})
check('PUT 改名换列 → 200 版本+1', st == 200 and d.get('version') >= 2
      and len(d.get('columns', [])) == 1, f'http {st} {d}')

t2name = KanbanTemplate.objects.get(pk=T2).name
st, d = api('PUT', f'/kanban/templates/{T1}', TA, CID,
            {'name': t2name, 'columns': [col(0)]})
check('PUT 改成同名（活跃）→ 400', st == 400, f'http {st} {d}')

st, d = api('DELETE', f'/kanban/templates/{T2}', TA, CID)
check('DELETE 默认模板 T2 → 软删成功', st == 200 and d.get('deleted') is True,
      f'http {st} {d}')
st, d = api('PUT', f'/kanban/templates/{T1}', TA, CID,
            {'name': t2name, 'columns': [col(0)]})
check('PUT 改成软删同名 → 200（先物理清软删行）', st == 200, f'http {st} {d}')
check('软删行已被物理清理',
      KanbanTemplate.all_objects.filter(company=company, name=t2name,
                                        is_deleted=True).count() == 0)

st, d = api('PUT', f'/kanban/templates/{T1}', TA, CID,
            {'columns': [col(0)], 'is_default': True})
check('PUT 不传 name 回落原名 + 设默认 → 200', st == 200
      and d.get('is_default') is True, f'http {st} {d}')
st, d = api('PUT', f'/kanban/templates/{T1}', TA, CID,
            {'columns': 'bad'})
check('PUT 列定义非法 → 400', st == 400, f'http {st} {d}')
st, d = api('PUT', '/kanban/templates/99999999', TA, CID, {'columns': [col(0)]})
check('PUT 不存在 → 404', st == 404, f'http {st} {d}')

st, d = api('PUT', f'/kanban/templates/{T3}/default', TA, CID)
check('PUT default 端点 → 200', st == 200 and d.get('is_default') is True,
      f'http {st} {d}')
check('T1 默认被清掉', KanbanTemplate.objects.get(pk=T1).is_default is False)
st, d = api('PUT', '/kanban/templates/99999999/default', TA, CID)
check('PUT default 不存在 → 404', st == 404, f'http {st} {d}')

st, d = api('PUT', f'/kanban/templates/{T1}/default', TA, CID)
st2, d2 = api('DELETE', f'/kanban/templates/{T1}', TA, CID)
check('DELETE 默认模板且存在其它 → 默认转移', st2 == 200 and
      KanbanTemplate.objects.get(pk=T3).is_default is True, f'http {st2} {d2}')
st2, d2 = api('DELETE', f'/kanban/templates/{T3}', TA, CID)
check('DELETE 最后一套默认模板 → 允许（渲染回落内置）', st2 == 200, f'http {st2} {d2}')
check('本厂模板清空', KanbanTemplate.objects.filter(company=company).count() == 0)
st, d = api('DELETE', '/kanban/templates/99999999', TA, CID)
check('DELETE 不存在 → 404', st == 404, f'http {st} {d}')

# ================= 四、Excel 表头导入（multipart 全分支） =================
print('\n===== 4. Excel 表头导入 =====')


def make_xlsx(cells, merges=()):
    wb = Workbook()
    ws = wb.worksheets[0]
    for (r, c), v in cells.items():
        ws.cell(row=r, column=c, value=v)
    for m in merges:
        ws.merge_cells(m)
    bio = BytesIO()
    wb.save(bio)
    return bio.getvalue()


st, d = upload('/kanban/import-header', TA, CID, {})
check('import-header 缺文件 → 400', st == 400, f'http {st} {d}')
st, d = upload('/kanban/import-header', TA, CID,
               {'file': SimpleUploadedFile('big.xlsx', b'\x00' * (5 * 1024 * 1024 + 1))})
check('import-header 超5MB → 400', st == 400, f'http {st} {d}')
st, d = upload('/kanban/import-header', TA, CID,
               {'file': SimpleUploadedFile('a.txt', b'x')})
check('import-header 坏后缀 → 400', st == 400, f'http {st} {d}')
st, d = upload('/kanban/import-header', TA, CID,
               {'file': SimpleUploadedFile('bad.xlsx', b'not an excel'),
                'header_row': 'abc'})
check('import-header header_row 非数字 → 400', st == 400, f'http {st} {d}')
st, d = upload('/kanban/import-header', TA, CID,
               {'file': SimpleUploadedFile('bad.xlsx', b'not an excel')})
check('import-header 坏文件 → 400（解析失败兜底）',
      st == 400 and '解析失败' in json.dumps(d, ensure_ascii=False), f'http {st} {d}')

single = make_xlsx({(1, 1): '成品', (1, 2): '数量', (1, 3): '备注',
                    (2, 1): '黑', (2, 2): 10})
st, d = upload('/kanban/import-header', TA, CID,
               {'file': SimpleUploadedFile('single.xlsx', single)})
check('import-header 单级表头自动探测 → 3列', st == 200 and
      [(c['col_index'], c['title'], c['parent']) for c in d.get('columns', [])]
      == [(1, '成品', ''), (2, '数量', ''), (3, '备注', '')]
      and d.get('two_level') is False and d.get('header_row') == 1,
      f'http {st} {d}')

st, d = upload('/kanban/import-header', TA, CID,
               {'file': SimpleUploadedFile('single.xlsx', single),
                'header_row': '1'})
check('import-header 指定 header_row=1 → 200', st == 200 and d.get('header_row') == 1,
      f'http {st} {d}')

two = make_xlsx({(1, 1): '1组', (1, 3): '数量',
                 (2, 1): '成品', (2, 2): '半成品', (2, 3): '备注'}, merges=['A1:B1'])
st, d = upload('/kanban/import-header', TA, CID,
               {'file': SimpleUploadedFile('two.xlsx', two)})
check('import-header 两级表头 → parent=1组',
      st == 200 and d.get('two_level') is True and
      [(c['title'], c['parent']) for c in d.get('columns', [])]
      == [('成品', '1组'), ('半成品', '1组'), ('备注', '')], f'http {st} {d}')

vert = make_xlsx({(1, 1): '颜色', (2, 2): '备注'}, merges=['A1:A2'])
st, d = upload('/kanban/import-header', TA, CID,
               {'file': SimpleUploadedFile('vert.xlsx', vert), 'header_row': '2'})
check('import-header 纵向合并(跨表头行) → 标题取左上值',
      st == 200 and [(c['col_index'], c['title']) for c in d.get('columns', [])]
      == [(1, '颜色'), (2, '备注')], f'http {st} {d}')

st, d = upload('/kanban/import-header', TA, CID,
               {'file': SimpleUploadedFile('oor.xlsx', single), 'header_row': '99'})
check('import-header 表头行越界 → 400', st == 400, f'http {st} {d}')

# excel.py 直调补充分支
try:
    kb_excel.parse_header(BytesIO(single), header_row=99)
    direct_oor = False
except ValueError:
    direct_oor = True
check('parse_header 直调越界 → ValueError', direct_oor)
check('_cell_text(None) → 空串', kb_excel._cell_text(None) == ''
      and kb_excel._cell_text(' x ') == 'x')
merged_hdr = make_xlsx({(1, 1): '合计', (1, 3): '数量'}, merges=['A1:B1'])
pd = kb_excel.parse_header(BytesIO(merged_hdr))
check('parse_header 横向合并块 col_span=2 且覆盖列去重',
      [(c['col_index'], c['title'], c['col_span']) for c in pd['columns']]
      == [(1, '合计', 2), (3, '数量', 1)], f'实际 {pd["columns"]}')
empty_col = make_xlsx({(1, 1): '成品', (1, 3): '数量'})
pd = kb_excel.parse_header(BytesIO(empty_col))
check('parse_header 空标题列跳过', [c['col_index'] for c in pd['columns']] == [1, 3])
# 合并块标题为空且非纵向父级 → 跳过（91-92）
blank_merge = make_xlsx({(2, 2): '备注'}, merges=['A1:A2'], )
pd = kb_excel.parse_header(BytesIO(blank_merge), header_row=2)
check('parse_header 合并块空标题（非父级）跳过', [c['col_index'] for c in pd['columns']]
      == [2], f'实际 {pd["columns"]}')
# 两级 + 纵向合并空标题列 → 父块兜底（101-105）
blank_v = make_xlsx({(1, 4): '其它', (2, 2): '成品'}, merges=['A1:A2', 'D1:E1'])
pd = kb_excel.parse_header(BytesIO(blank_v), header_row=2)
check('parse_header 两级下纵向空标题列不产出（父值也为空）',
      pd['two_level'] is True and [c['col_index'] for c in pd['columns']] == [2],
      f'实际 {pd}')
# 表头行自身合并块左上值为空 → 该列跳过（91-92）
blank_top = make_xlsx({(1, 3): '数量'}, merges=['A1:B1'])
pd = kb_excel.parse_header(BytesIO(blank_top))
check('parse_header 表头行合并块空标题跳过',
      [c['col_index'] for c in pd['columns']] == [3], f'实际 {pd}')

# ================= 五、试跑 / 渲染 =================
print('\n===== 5. 试跑与渲染 =====')
today = timezone.localdate()
DF = today.isoformat()

st, d = api('POST', '/kanban/preview', TA, CID, {'columns': [col(0, title='款号')]})
check('preview 内联列 → 200（渲染核心三路共用）', st == 200
      and d.get('template', {}).get('name') == '预览'
      and len(d.get('columns', [])) == 1, f'http {st} {str(d)[:200]}')
blocks = [b for g in d.get('groups', []) for b in g['blocks']]
check('preview 渲染出同款两色两行（同日期同块）',
      len(blocks) == 1 and len(blocks[0]['rows']) == 2,
      f'实际 blocks={len(blocks)} rows={[len(b["rows"]) for b in blocks]}')
check('preview 行 key=款式id-颜色', {r['key'] for r in blocks[0]['rows']}
      == {f'{style.id}-黑', f'{style.id}-白'},
      f'实际 {[r["key"] for r in blocks[0]["rows"]]}')
st, d = api('POST', '/kanban/preview', TA, CID, {'columns': 'bad'})
check('preview 内联列非法 → 400', st == 400, f'http {st} {d}')
st, d = api('POST', '/kanban/preview', TA, CID, {'template_id': 99999999})
check('preview 模板不存在 → 404', st == 404, f'http {st} {d}')

st, d = api('POST', '/kanban/preview', TA, CID,
            {'columns': [col(0)], 'range': 'custom',
             'start_date': DF, 'end_date': DF})
check('preview range=custom 合法 → 200', st == 200, f'http {st}')
st, d = api('POST', '/kanban/preview', TA, CID,
            {'columns': [col(0)], 'range': 'custom',
             'start_date': 'oops', 'end_date': DF})
check('preview custom 非法日期回落本月 → 200', st == 200, f'http {st}')

# 建一套带默认标记的模板供渲染
st, d = api('POST', '/kanban/templates', TA, CID,
            {'name': nm(), 'columns': [
                col(0, title='款号', extra={'link': 'order', 'width': 'auto',
                                            'group': '基础'}),
                col(1, title='菲票数量', key='order_qty', extra={'width': 0}),
            ], 'is_default': True})
T4 = d.get('id')
check('渲染用默认模板建立', st == 201 and d.get('is_default') is True, f'http {st} {d}')

st, d = api('POST', '/kanban/preview', TA, CID, {'template_id': T4})
check('preview 指定既有模板 → 200（render_with_fallback 路径）', st == 200
      and d.get('template', {}).get('id') == T4, f'http {st} {str(d)[:160]}')

st, d = api('GET', f'/kanban/render?range=month&template_id={T4}', TA, CID)
check('render 指定模板 → 200 列下发含 link/group/width',
      st == 200 and d.get('template', {}).get('id') == T4
      and d['columns'][0]['link'] == 'order' and d['columns'][0]['width'] == 0
      and d['columns'][0]['group'] == '基础', f'http {st} {str(d)[:200]}')
check('render 组块结构与预览同构',
      len([b for g in d.get('groups', []) for b in g['blocks']]) == 1, '')
st, d = api('GET', '/kanban/render?range=month', TW, CID)
check('render 缺省用默认模板（员工可渲染）', st == 200
      and d.get('template', {}).get('id') == T4, f'http {st} {str(d)[:200]}')
st, d = api('GET', '/kanban/render?range=month&template_id=99999999', TA, CID)
check('render 模板不存在 → 404', st == 404, f'http {st} {d}')
st, d = api('GET', '/kanban/render?range=bogusrange', TA, CID)
check('render 非法 range 回落本月 → 200', st == 200, f'http {st}')
st, d = api('GET', f'/kanban/render?range=custom&start_date={DF}&end_date={DF}',
            TA, CID)
check('render custom 闭区间 → 200', st == 200, f'http {st}')

# ================= 六、services 直调（归一/兜底/解析分支） =================
print('\n===== 6. services 函数级分支 =====')
S = kb_services
check('_fmt_num 整数浮点去尾', S._fmt_num(3.0) == 3 and S._fmt_num(3.5) == 3.5
      and S._fmt_num('x') == 'x')
check('_time_derived 空串', S._time_derived('') == ('', '否', '否'))
check('_time_derived 非法日期 → ValueError 兜底',
      S._time_derived('bad-date') == ('bad-dat', '否', '否'))
td = S._time_derived(DF)
check('_time_derived 今日 → 本月/本周是', td[1] == '是' and td[2] == '是'
      and td[0] == DF[:7], f'实际 {td}')
check('_time_derived 往月 → 否/否', S._time_derived('2020-01-01') == ('2020-01', '否', '否'))
check('_date_label 合法/空/非日期', S._date_label('2026-10-15') == '10月15日'
      and S._date_label('') == '未填日期' and S._date_label('abc') == 'abc')
check('_normalize_width 缺省None→150 / 空串→0',
      S._normalize_width(None) == 150 and S._normalize_width('') == 0)
check('_normalize_width auto/AUTO/0/200/abc',
      S._normalize_width('auto') == 0 and S._normalize_width('AUTO') == 0
      and S._normalize_width('0') == 0 and S._normalize_width('200') == 200
      and S._normalize_width('abc') == 150)
check('_normalize_width 数值/浮点/其它类型',
      S._normalize_width(0) == 0 and S._normalize_width(120) == 120
      and S._normalize_width(120.9) == 120 and S._normalize_width([]) == 150)
check('_cell_text_for_column static',
      S._cell_text_for_column({'bind_type': 'static', 'static_text': '固定文本'}, {})
      == ('固定文本', False))
check('_cell_text_for_column formula 求值异常 → 占位',
      S._cell_text_for_column({'bind_type': 'formula', 'expr': 'order_qty / cut_qty',
                               'extra': {}},
                              {'order_qty': 100, 'cut_qty': 0}) == ('—', False))
check('_cell_text_for_column formula 自定义占位',
      S._cell_text_for_column({'bind_type': 'formula', 'expr': 'order_qty / cut_qty',
                               'extra': {'placeholder': 'N/A'}},
                              {'order_qty': 100, 'cut_qty': 0}) == ('N/A', False))
check('_cell_text_for_column formula 正常',
      S._cell_text_for_column({'bind_type': 'formula', 'expr': 'order_qty + 1',
                               'extra': {}}, {'order_qty': 100}) == ('101', False))
check('_cell_text_for_column 未知字段 → 占位',
      S._cell_text_for_column({'bind_type': 'field', 'field_key': 'nope'},
                              {}) == ('—', False))
check('_cell_text_for_column todo 字段 → 占位+is_todo',
      S._cell_text_for_column({'bind_type': 'field', 'field_key': 'schedule'},
                              {}) == ('—', True))
check('_cell_text_for_column 字段值为 None → 空串',
      S._cell_text_for_column({'bind_type': 'field', 'field_key': 'order_no'},
                              {'order_no': None}) == ('', False))
cp = S._columns_payload([{'id': 2, 'seq': 1, 'title': '甲',
                          'extra': {'link': 'bed', 'merge': 'value'}},
                         {'id': 1, 'seq': 0, 'title': '乙', 'extra': None}])
check('_columns_payload 排序+link白名单外回落+extra缺省',
      [c['id'] for c in cp] == [1, 2] and cp[1]['link'] == ''
      and cp[1]['merge'] == 'value' and cp[0]['width'] == 150
      and cp[0]['group'] == '' and cp[0]['placeholder'] == '—', f'实际 {cp}')
check('builtin_default_template 元信息',
      S.builtin_default_template()['id'] is None
      and S.builtin_default_template()['name'] == '默认看板'
      and len(S.builtin_default_columns()) == 13)
month_range = __import__('apps.production.views', fromlist=['_range_dates']) \
    ._range_dates('month')[:2]
check('_parse_range 非法 key 回落本月', S._parse_range('bogus', '', '') == month_range)
check('_parse_range custom 起止倒序自动交换',
      S._parse_range('custom', '2026-01-05', '2026-01-01')
      == (__import__('datetime').date(2026, 1, 1),
          __import__('datetime').date(2026, 1, 5)))
check('_parse_range custom 非法日期回落本月',
      S._parse_range('custom', 'oops', '2026-01-01') == month_range)

# 渲染兜底壳：未保存实例（无 pk）触发渲染异常 → 回落内置默认模板
ghost = KanbanTemplate(company=company, name='KBST幽灵模板')
fb = S.render_with_fallback(company, 'month', '', '', template=ghost)
check('render_with_fallback 渲染异常 → 回落内置默认（不白屏）',
      fb.get('template', {}).get('id') is None
      and len(fb.get('columns', [])) == 13, f'实际 {str(fb)[:120]}')
fb2 = S.build_render_payload(company, None, 'month', '', '')
check('build_render_payload template=None → 内置默认列',
      len(fb2.get('columns', [])) == 13, '')

# ORM 直造坏列模板：未知字段占位 / 公式除零占位 / auto 宽
bad_tpl = KanbanTemplate.objects.create(company=company, name='KBST坏列模板')
KanbanColumn.objects.bulk_create([
    KanbanColumn(template=bad_tpl, seq=0, title='未知字段列',
                 bind_type='field', field_key='nope'),
    KanbanColumn(template=bad_tpl, seq=1, title='除零列',
                 bind_type='formula', expr='order_qty / (cut_qty - cut_qty)'),
    KanbanColumn(template=bad_tpl, seq=2, title='正常公式列',
                 bind_type='formula', expr='order_qty + 1'),
    KanbanColumn(template=bad_tpl, seq=3, title='todo列',
                 bind_type='field', field_key='schedule'),
])
st, d = api('GET', f'/kanban/render?range=month&template_id={bad_tpl.pk}', TA, CID)
texts = [c['text'] for b in d.get('groups', []) for blk in b['blocks']
         for r in blk['rows'] for c in r['cells']]
check('渲染：未知字段列→占位 / 除零公式→占位 / 正常公式→数值 / todo→占位',
      st == 200 and len(texts) == 8 and texts[0] == '—' and texts[1] == '—'
      and texts[2] == '101' and texts[3] == '—', f'http {st} 实际 {texts}')
check('渲染坏列模板列下发宽度归一', st == 200 and
      all(c['width'] == 150 for c in d['columns']), f"实际 {[c['width'] for c in d['columns']]}")

# 非默认模板删除（DELETE 的 is_default=False 分支）+ ORM 空名模板 PUT 空名拒绝
st, d = api('DELETE', f'/kanban/templates/{bad_tpl.pk}', TA, CID)
check('DELETE 非默认模板 → 软删（不动默认标记）', st == 200
      and d.get('deleted') is True
      and KanbanTemplate.objects.get(pk=T4).is_default is True, f'http {st} {d}')
blank = KanbanTemplate.objects.create(company=company, name='', dataset='dashboard_plans')
st, d = api('PUT', f'/kanban/templates/{blank.pk}', TA, CID, {'columns': [col(0)]})
check('PUT 空名模板不传 name → 400（名称不能为空）', st == 400, f'http {st} {d}')
KanbanTemplate.all_objects.filter(pk=blank.pk).delete()

# ================= 七、公式 DSL 白名单（保存期/求值期） =================
print('\n===== 7. 公式 DSL 白名单 =====')
V = kb_formula.validate_expr
ok_exprs = [
    'cut_qty + order_qty * 2',
    'IF(order_qty > 0, cut_qty / order_qty, 0)',
    'round(process_percent, 1)',
    'min(cut_qty, order_qty)',
    'max(cut_qty, order_qty, 0)',
    'sum((cut_qty,))' if False else 'abs(0 - order_qty)',
    'cut_qty ** 2',
    'cut_qty // 2',
    'cut_qty % 2',
    '-cut_qty',
    '+cut_qty',
    'not cut_qty',
    'cut_qty and order_qty',
    'cut_qty or order_qty',
    'cut_qty == order_qty',
    'cut_qty != order_qty',
    'cut_qty < order_qty',
    'cut_qty <= order_qty',
    'cut_qty > order_qty',
    'cut_qty >= order_qty',
    '"外发" if order_type == "外发" else 0',
    '1 if cut_qty >= 0 else 2',
]
all_ok = True
for e in ok_exprs:
    try:
        V(e)
    except kb_formula.FormulaError as ex:
        all_ok = False
        print('  意外拒绝:', e, ex)
check(f'validate_expr 合法公式全放行（{len(ok_exprs)} 条）', all_ok)

bad_exprs = [
    ('空串', ''),
    ('纯空白', '   '),
    ('非字符串', None),
    ('超长', 'cut_qty + ' + '1' * 300),
    ('语法错误', 'cut_qty +'),
    ('属性访问', 'cut_qty.real'),
    ('下标访问', 'cut_qty[0]'),
    ('lambda', '(lambda x: x)(1)'),
    ('f-string', 'f"{cut_qty}"'),
    ('布尔常量', 'True'),
    ('超长字符串常量', '"' + 'x' * 65 + '"'),
    ('非白名单函数', 'foo(1)'),
    ('import 伪装', '__import__("os")'),
    ('关键字参数', 'round(cut_qty=1)'),
    ('未知字段', 'cut_qty + unknown_var'),
]
for label, e in bad_exprs:
    try:
        V(e)
        got = False
    except kb_formula.FormulaError:
        got = True
    check(f'validate_expr 拒绝：{label}', got, '未被拒绝')

check('FormulaError 是 ValueError 子类',
      issubclass(kb_formula.FormulaError, ValueError))
check('eval_expr 基本求值',
      kb_formula.eval_expr('order_qty + 1', {'order_qty': 100}) == 101)
check('eval_expr names 白名单过滤（非字段名剔除）',
      kb_formula.eval_expr('order_qty', {'order_qty': 5, 'hacker': 9}) == 5)
check('eval_expr IF 函数',
      kb_formula.eval_expr('IF(order_qty > 0, 1, 2)', {'order_qty': 5}) == 1)
try:
    kb_formula.eval_expr('1/0', {})
    div_ok = False
except ZeroDivisionError:
    div_ok = True
check('eval_expr 异常向上抛（渲染层落占位）', div_ok)

# ================= 八、Vue3 配置端静态托管（web.py，运行时替换目录） =================
print('\n===== 8. 配置端静态托管 =====')
tmpdir = tempfile.mkdtemp(prefix='kbst_web_')
orig_index, orig_dist = kb_web.INDEX_HTML, kb_web.DIST_DIR
try:
    c = Client()
    r = c.get('/api/kanban-admin')
    idx_head = b''.join(r.streaming_content)[:200] if r.status_code == 200 else b''
    check('spa_index 已构建 → 200 HTML', r.status_code == 200
          and 'text/html' in r['Content-Type']
          and b'KBST' not in idx_head, f'http {r.status_code} {r["Content-Type"]}')
    kb_web.INDEX_HTML = os.path.join(tmpdir, 'missing.html')
    c = Client()
    r = c.get('/api/kanban-admin')
    check('spa_index 产物缺失 → 构建提示页',
          r.status_code == 200 and '未构建' in r.content.decode('utf-8'),
          f'http {r.status_code}')
    # 造临时产物目录
    kb_web.DIST_DIR = tmpdir
    with open(os.path.join(tmpdir, 'index.html'), 'wb') as f:
        f.write('<html>KBST</html>'.encode('utf-8'))
    for fname, ctype in [('app.js', 'text/javascript'), ('s.css', 'text/css'),
                         ('p.html', 'text/html'), ('i.svg', 'image/svg+xml'),
                         ('README', 'application/octet-stream')]:
        with open(os.path.join(tmpdir, fname), 'wb') as f:
            f.write(b'KBST')
        kb_web.INDEX_HTML = os.path.join(tmpdir, 'index.html')
        r = c.get(f'/api/kanban-admin/{fname}')
        check(f'spa_asset {fname} → {ctype}', r.status_code == 200
              and ctype in r['Content-Type'], f'http {r.status_code} {r["Content-Type"]}')
    r = c.get('/api/kanban-admin/nope.js')
    check('spa_asset 资源不存在 → 404', r.status_code == 404, f'http {r.status_code}')
    r = c.get('/api/kanban-admin/sub/../../outside.js')
    check('spa_asset 路径穿越 → 404', r.status_code == 404, f'http {r.status_code}')
finally:
    kb_web.INDEX_HTML, kb_web.DIST_DIR = orig_index, orig_dist
    shutil.rmtree(tmpdir, ignore_errors=True)

# ================= 九、seed 命令（限定本厂，绝不触碰其它公司） =================
print('\n===== 9. seed_kanban_templates 命令 =====')
ktpl_before_86 = KanbanTemplate.all_objects.filter(company_id=86).count()


def _kbst_only(*_a, **_k):
    return Company.objects.filter(pk=company.pk)


KanbanTemplate.all_objects.filter(company=company).delete()
Company.objects.all = _kbst_only  # 运行时限定：命令只看得到本厂
try:
    buf = io.StringIO()
    call_command('seed_kanban_templates', stdout=buf)
    t = KanbanTemplate.objects.filter(company=company, name='默认看板').first()
    check('seed 为无模板公司创建默认模板（13列/默认/版本1）',
          bool(t) and t.is_default and t.version == 1
          and t.columns.count() == 13, f'实际 {buf.getvalue()}')
    check('seed 输出 created', 'created=1' in buf.getvalue(), buf.getvalue())
    buf2 = io.StringIO()
    call_command('seed_kanban_templates', stdout=buf2)
    check('seed 第二次跳过（已有模板，幂等）',
          KanbanTemplate.all_objects.filter(company=company, name='默认看板').count() == 1
          and 'skipped(已有模板)=1' in buf2.getvalue(), buf2.getvalue())
finally:
    del Company.objects.all  # 还原 Manager 方法
check('seed 前后真实厂 86 模板数不变（零触碰）',
      KanbanTemplate.all_objects.filter(company_id=86).count() == ktpl_before_86)

# ================= 十、模型 __str__ =================
print('\n===== 10. 模型 __str__ =====')
t = KanbanTemplate.objects.filter(company=company).first()
c0 = t.columns.first() if t else None
check('KanbanTemplate.__str__', t is not None and str(t) == f'{t.company_id}:{t.name}')
check('KanbanColumn.__str__',
      c0 is not None and str(c0) == f'{c0.template_id}:{c0.seq}:{c0.title}')

# ================= 十一、core/smoke.py 冒烟（savepoint 包裹，净写入 0） =================
print('\n===== 11. core/smoke.py 冒烟 =====')
smoke_ran, smoke_err = False, ''
smoke_path = os.path.join(os.getcwd(), 'apps', 'core', 'smoke.py')
check('smoke.py 路径存在', os.path.exists(smoke_path), smoke_path)
old_stdout = sys.stdout
sid = None
try:
    guard = Client()
    rt = guard.post('/api/auth/login',
                    {'phone': '13800000001', 'password': 'yisao1234'},
                    content_type='application/json')
    target_cid = None
    if rt.status_code == 200:
        mine = guard.get('/api/companies/mine',
                         HTTP_AUTHORIZATION=f"Bearer {rt.json()['access']}")
        # 真机验证后 demo 账号可能被拉入真实厂 86（companies/mine 排序不定），
        # 从其公司列表中挑第一个非 86 的公司作 smoke 目标，绝不触碰真实厂
        target_cid = next((c.get('id') for c in (mine.json() or [])
                           if c.get('id') != 86), None)
    if target_cid is None:
        smoke_err = '跳过：smoke 无可用非 86 公司'
    elif target_cid == 86:
        smoke_err = '跳过：smoke 目标公司为真实厂 86'
    else:
        sid = transaction.savepoint()
        # 临时补齐 smoke 依赖（demo 公司：款式工序 + 冒烟员工归属）。
        # 全部在 savepoint 内，跑完立即回滚，对该公司净写入为 0。
        from apps.catalog.models import StyleProcess
        t1 = Ticket.objects.filter(company_id=target_cid).first()
        if t1 is not None:
            _style = t1.production.style
            if not StyleProcess.objects.filter(style=_style).exists():
                _p = Process.objects.create(company_id=target_cid,
                                            name='SMOKE临时工序', is_fixed=False)
                StyleProcess.objects.create(style=_style, process=_p,
                                            price='1.000', sort=1)
        _wu = User.objects.filter(phone='13800000003').first()
        if _wu is not None and not UserCompany.objects.filter(
                user=_wu, company_id=target_cid, is_deleted=False).exists():
            _post = PostM.objects.create(company_id=target_cid, name='SMOKE临时岗',
                                         permissions=['*'])
            UserCompany.objects.create(user=_wu, company_id=target_cid, post=_post,
                                       status=UserCompany.Status.ACTIVE)
        g = {'__name__': '__smoke__', '__file__': smoke_path}
        src = compile(open(smoke_path, encoding='utf-8').read(), 'smoke.py', 'exec')
        exec(src, g)  # smoke 内部会替换 sys.stdout（下方 finally 还原）
        smoke_ran = True
except Exception as e:  # noqa: BLE001
    smoke_err = repr(e)
finally:
    wrapped = sys.stdout
    if wrapped is not old_stdout and hasattr(wrapped, 'detach'):
        try:
            wrapped.detach()  # 防 GC 关闭底层 stdout buffer
        except Exception:  # noqa: BLE001
            pass
    sys.stdout = old_stdout
    if sid is not None:
        transaction.savepoint_rollback(sid)
check('smoke.py 端到端冒烟跑通（SMOKE DONE，savepoint 已回滚）',
      smoke_ran, smoke_err)

# ================= 汇总 =================
print(f'\n==== 看板套件：{len(PASS)} pass / {len(FAIL)} fail ====')
for f in FAIL:
    print('FAIL:', f)
