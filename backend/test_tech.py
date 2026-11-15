# -*- coding: utf-8 -*-
"""
技术域（test_tech）端到端后端验证 —— SA1 交付（2026-10-24 P1 tech 域）。

覆盖 P1 tech 域全部端点与规则：
  物料  T-MAT-01~20（CRUD/软删复用/查重/keyword+category 过滤/换算参数拦截/attrs/隔离）
  BOM   T-BOM-01~24（CRUD/items 校验/版本/单 active 激活降级/fork 改版/references/隔离）
  默认  T-LOSS-01~04（损耗率品类默认：梭织6/针织8/辅料0.4/attrs覆盖，可显式覆盖）
  导入  T-IMP-01~12（物料/BOM Excel+JSON 导入：created/updated/errors 带行号中文原因，
                     all-or-nothing 不做部分成功静默/坏表头/xlsx 真文件）
  权限  T-PERM-01~06（401/403/legacy 零回归/tech 点 effective_perms 下发/宫格注册）
  隔离  T-ISO-01~04（跨厂 404/同码各厂独立/列表不见）

跑法：manage.py shell -c "exec(compile(open('test_tech.py',encoding='utf-8').read(),'test_tech.py','exec'))"
幂等：自建「技术测试厂(可删)/技术测试B厂(可删)」并跨次复用（Company.creator PROTECT
不可删）；每轮 preclean 硬清本厂 tech 域业务行（BomItem→Bom→Material→Style/审计），
不依赖 DB 现状；绝不触碰真实厂（company_id=86 等）。
"""
import json
import os
import tempfile
from decimal import Decimal

from django.contrib.auth.hashers import make_password
from django.db import IntegrityError, transaction
from django.test import Client

from apps.accounts.models import Company, Post as PostM, User, UserCompany
from apps.accounts.services import create_company_with_owner
from apps.catalog.models import Style
from apps.ops.models import AuditLog
from apps.tech.models import Bom, BomItem, Material

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


OWNER, H_LEGACY, H_PLAIN, H_PERM, OWNER_B = (
    '13988711201', '13988711202', '13988711203', '13988711204', '13988711205')
CO_A, CO_B = '技术测试厂(可删)', '技术测试B厂(可删)'


def preclean(company):
    """幂等自清扫：硬清本厂 tech 域业务行（PROTECT 顺序：明细→BOM→物料→款式/审计）。"""
    BomItem.all_objects.filter(bom__company=company).delete()
    Bom.all_objects.filter(company=company).delete()
    Material.all_objects.filter(company=company).delete()
    Style.objects.filter(company=company, style_no__startswith='TTEC').delete()
    AuditLog.objects.filter(company=company).delete()


# ================= 建厂（复用） + preclean =================
company = Company.objects.filter(name=CO_A).first()
if company is None:
    owner = ensure_user(OWNER, '技术厂长')
    company, admin_post = create_company_with_owner(owner, CO_A)
else:
    owner = ensure_user(OWNER, '技术厂长')
    admin_post = PostM.objects.get(company=company, is_admin=True, is_deleted=False)
company_b = Company.objects.filter(name=CO_B).first()
if company_b is None:
    owner_b = ensure_user(OWNER_B, '技术B厂长')
    company_b, _ = create_company_with_owner(owner_b, CO_B)
else:
    owner_b = ensure_user(OWNER_B, '技术B厂长')
CID, CID_B = company.id, company_b.id

preclean(company)
preclean(company_b)

# 岗位矩阵：普通成员（无点）/ 旧裁床岗（legacy coarse 'production'）/ tech 点岗
post_plain = ensure_post(company, '技术普工', [])
post_legacy = ensure_post(company, '技术旧岗', ['production'])
post_tech = ensure_post(company, '技术员', ['tech.material', 'tech.bom'])
plain = ensure_user(H_PLAIN, '技术无权员')
legacy_u = ensure_user(H_LEGACY, '技术旧岗员')
perm_u = ensure_user(H_PERM, '技术持点员')
ensure_member(plain, company, post_plain)
ensure_member(legacy_u, company, post_legacy)
ensure_member(perm_u, company, post_tech)

TA = login(OWNER)          # A 厂管理员（'*'）
TB = login(OWNER_B)        # B 厂管理员
T_PLAIN = login(H_PLAIN)
T_LEGACY = login(H_LEGACY)
T_PERM = login(H_PERM)

style = Style.objects.create(company=company, style_no='TTEC-S1', name='技术测试款')
style_b = Style.objects.create(company=company_b, style_no='TTEC-S1B', name='B厂测试款')

# ================= T-PERM 权限体系 =================
print('--- T-PERM 权限 ---')
s, d = api('GET', '/tech/materials')
check('T-PERM-01 无token 401', s == 401, f's={s}')
s, d = api('GET', '/tech/materials', token=T_PLAIN, cid=CID)
check('T-PERM-02 无点岗位 403', s == 403, f's={s}')
s, d = api('GET', '/tech/boms', token=T_LEGACY, cid=CID)
check('T-PERM-03 legacy production 岗 403（零回归：旧key不覆盖tech）', s == 403, f's={s}')
s, d = api('GET', '/posts', token=TA, cid=CID)
plist = d.get('results', d if isinstance(d, list) else [])
pmap = {p['name']: p for p in plist} if isinstance(plist, list) else {}
check('T-PERM-04 tech点岗位 effective_perms 含 tech.material/tech.bom',
      'tech.material' in (pmap.get('技术员', {}).get('effective_perms') or [])
      and 'tech.bom' in (pmap.get('技术员', {}).get('effective_perms') or []),
      str(pmap.get('技术员', {}).get('effective_perms')))
check('T-PERM-05 legacy 岗 effective_perms 不含 tech.*（零回归）',
      all(not k.startswith('tech.')
          for k in (pmap.get('技术旧岗', {}).get('effective_perms') or [])),
      str(pmap.get('技术旧岗', {}).get('effective_perms')))
from apps.core.permissions import ALL_PERMISSION_KEYS, PERMISSION_GROUPS
tech_pts = [p['key'] for g in PERMISSION_GROUPS if g['module'] == 'tech' for p in g['points']]
check('T-PERM-06 权限点注册 tech.material/tech.bom 入 ALL_PERMISSION_KEYS',
      set(tech_pts) == {'tech.material', 'tech.bom'}
      and set(tech_pts) <= set(ALL_PERMISSION_KEYS), str(tech_pts))

# 持点岗位全链路可用（effective_perms 消费同 check_perm 判定）
style_perm = Style.objects.create(company=company, style_no='TTEC-S2', name='持点款')
s, d = api('POST', '/tech/materials', token=T_PERM, cid=CID,
           data={'code': 'TTEC-MPERM', 'name': '持点员物料', 'unit': '米'})
check('T-PERM-07 持点岗位可建物料', s == 201, f's={s} {d}')
MPERM_ID = d.get('id')
s, d = api('POST', '/tech/boms', token=T_PERM, cid=CID,
           data={'style': style_perm.id, 'items': []})
check('T-PERM-08 持点岗位可建BOM', s == 201, f's={s} {d}')
BPERM_ID = d.get('id')

# ================= T-MAT 物料主档 =================
print('--- T-MAT 物料 ---')
s, d = api('POST', '/tech/materials', token=TA, cid=CID, data={
    'code': 'TTEC-M001', 'name': '全棉帆布', 'category': 'fabric', 'unit': '米',
    'shrink_rate': '4.50', 'attrs': {'成分': '棉100%', 'fabric_type': 'woven'},
    'legacy_code': 'OLD-001'})
check('T-MAT-01 建物料 201 默认 conversion_ready=False', s == 201 and d.get(
    'conversion_ready') is False, f's={s} {d}')
M1 = d['id']
s, d = api('POST', '/tech/materials', token=TA, cid=CID, data={
    'code': 'TTEC-M001', 'name': '重复码', 'unit': '米'})
check('T-MAT-02 同公司同码 400 物料编码已存在', s == 400 and '已存在' in json.dumps(
    d, ensure_ascii=False), f's={s} {d}')
s, d = api('POST', '/tech/materials', token=TA, cid=CID, data={
    'code': 'TTEC-M002', 'name': '缺参面料', 'unit': '公斤', 'unit_alt': '米'})
check('T-MAT-03 副单位缺克重/幅宽 → 400 拦截（A组换算防线）',
      s == 400 and '克重' in json.dumps(d, ensure_ascii=False), f's={s} {d}')
s, d = api('POST', '/tech/materials', token=TA, cid=CID, data={
    'code': 'TTEC-M002', 'name': '针织汗布', 'category': 'fabric', 'unit': '公斤',
    'unit_alt': '米', 'weight_gsm': '180', 'width_cm': '160',
    'attrs': {'fabric_type': 'knit'}})
check('T-MAT-04 副单位+克重+幅宽 → 201 conversion_ready=True',
      s == 201 and d.get('conversion_ready') is True, f's={s} {d}')
M2 = d['id']
s, d = api('POST', '/tech/materials', token=TA, cid=CID, data={
    'code': 'TTEC-M003', 'name': '负缩率', 'unit': '米', 'shrink_rate': '-1'})
check('T-MAT-05 负缩率 400', s == 400, f's={s}')
s, d = api('POST', '/tech/materials', token=TA, cid=CID, data={
    'code': 'TTEC-M004', 'name': '坏分类', 'unit': '米', 'category': 'metal'})
check('T-MAT-06 非法分类 400', s == 400, f's={s}')
s, d = api('GET', '/tech/materials?keyword=帆布', token=TA, cid=CID)
check('T-MAT-07 keyword 按品名检索', s == 200 and d.get('count') == 1, f's={s} {d}')
s, d = api('GET', '/tech/materials?keyword=OLD-001', token=TA, cid=CID)
check('T-MAT-08 keyword 按旧编码检索（E1 对照）', s == 200 and d.get('count') == 1,
      f's={s} {d}')
s, d = api('GET', '/tech/materials?category=accessory', token=TA, cid=CID)
check('T-MAT-09 category 过滤空', s == 200 and d.get('count') == 0, f's={s}')
s, d = api('POST', '/tech/materials', token=TA, cid=CID, data={
    'code': 'TTEC-M005', 'name': '纽扣', 'category': 'accessory', 'unit': '粒'})
ACC = d['id']
s, d = api('GET', '/tech/materials?category=accessory', token=TA, cid=CID)
check('T-MAT-10 category 过滤命中', s == 200 and d.get('count') == 1, f's={s}')
s, d = api('PUT', f'/tech/materials/{M1}', token=TA, cid=CID, data={
    'code': 'TTEC-M001', 'name': '全棉帆布2', 'category': 'fabric', 'unit': '米',
    'status': 'disabled', 'shrink_rate': '5.00',
    'attrs': {'成分': '棉100%', 'fabric_type': 'woven'}, 'legacy_code': 'OLD-001'})
check('T-MAT-11 PUT 改名/停用/缩率', s == 200 and d.get('status') == 'disabled'
      and d.get('name') == '全棉帆布2' and d.get('shrink_rate') == '5.00', f's={s} {d}')
s, d = api('PUT', f'/tech/materials/{M1}', token=TA, cid=CID, data={
    'code': 'TTEC-M002', 'name': '撞码改', 'category': 'fabric', 'unit': '米'})
check('T-MAT-12 PUT 撞他行编码 400', s == 400, f's={s} {d}')
s, d = api('DELETE', f'/tech/materials/{M1}', token=TA, cid=CID)
check('T-MAT-13 DELETE 软删', s in (200, 204), f's={s}')
s, d = api('GET', f'/tech/materials/{M1}', token=TA, cid=CID)
check('T-MAT-14 软删后详情 404', s == 404, f's={s}')
s, d = api('GET', '/tech/materials?keyword=TTEC-M001', token=TA, cid=CID)
check('T-MAT-15 软删后列表不见', s == 200 and d.get('count') == 0, f's={s} {d}')
s, d = api('POST', '/tech/materials', token=TA, cid=CID, data={
    'code': 'TTEC-M001', 'name': '帆布复用', 'category': 'fabric', 'unit': '米'})
check('T-MAT-16 软删后同码可复用（UK condition=is_deleted=False）', s == 201,
      f's={s} {d}')
M1_NEW = d.get('id')
# 跨次复用第二次删除场景（软删行复活路径二次验证）
api('DELETE', f'/tech/materials/{M1_NEW}', token=TA, cid=CID)
s, d = api('POST', '/tech/materials', token=TA, cid=CID, data={
    'code': 'TTEC-M001', 'name': '帆布三用', 'category': 'fabric', 'unit': '米'})
check('T-MAT-17 二次软删同码再复用（复活路径）', s == 201, f's={s} {d}')
M1 = d['id']

# ---- T-ISO 物料隔离 ----
print('--- T-ISO 隔离 ---')
s, d = api('GET', f'/tech/materials/{M1}', token=TB, cid=CID_B)
check('T-ISO-01 B厂管理员读A厂物料 404', s == 404, f's={s}')
s, d = api('PUT', f'/tech/materials/{M1}', token=TB, cid=CID_B, data={
    'code': 'X', 'name': 'X', 'unit': '米'})
check('T-ISO-02 B厂改A厂物料 404', s == 404, f's={s}')
s, d = api('DELETE', f'/tech/materials/{M1}', token=TB, cid=CID_B)
check('T-ISO-03 B厂删A厂物料 404', s == 404, f's={s}')
s, d = api('POST', '/tech/materials', token=TB, cid=CID_B, data={
    'code': 'TTEC-M001', 'name': 'B厂同码', 'unit': '米'})
check('T-ISO-04 B厂可建同码物料（UNIQUE 按公司隔离）', s == 201, f's={s} {d}')
s, d = api('GET', '/tech/materials?keyword=TTEC-M001', token=TB, cid=CID_B)
check('T-ISO-05 B厂列表只见B厂行', s == 200 and d.get('count') == 1
      and d['results'][0]['name'] == 'B厂同码', f's={s} {d}')

# ================= T-LOSS 损耗率默认值 =================
print('--- T-LOSS 品类默认损耗 ---')
m_fabric = Material.objects.get(pk=M1)
m_knit = Material.objects.get(pk=M2)
m_acc = Material.objects.get(pk=ACC)
check('T-LOSS-01 梭织面料默认 6', m_fabric.default_loss_rate() == Decimal('6'),
      str(m_fabric.default_loss_rate()))
check('T-LOSS-02 针织(attrs.fabric_type=knit)默认 8',
      m_knit.default_loss_rate() == Decimal('8'), str(m_knit.default_loss_rate()))
check('T-LOSS-03 辅料默认 0.4', m_acc.default_loss_rate() == Decimal('0.4'),
      str(m_acc.default_loss_rate()))
m_acc.attrs = {'loss_rate': 10}
check('T-LOSS-04 attrs.loss_rate 显式覆盖 10（高频辅料）',
      m_acc.default_loss_rate() == Decimal('10'), str(m_acc.default_loss_rate()))

# ================= T-BOM 版式BOM =================
print('--- T-BOM ---')
s, d = api('POST', '/tech/boms', token=TA, cid=CID, data={
    'style': style.id, 'remark': '初版',
    'items': [
        {'material': M1, 'kind': 'main', 'unit_usage': '2.5', 'color': '红', 'size': 'L'},
        {'material': ACC, 'kind': 'accessory', 'unit_usage': '1', 'loss_rate': '2'},
    ]})
check('T-BOM-01 建BOM 201 draft+items', s == 201 and d.get('status') == 'draft'
      and len(d.get('items', [])) == 2, f's={s} {d}')
BOM1 = d['id']
it_fabric = next(i for i in d['items'] if i['material'] == M1)
it_acc = next(i for i in d['items'] if i['material'] == ACC)
check('T-BOM-02 item 单位=物料主单位快照', it_fabric['unit'] == '米', str(it_fabric))
check('T-BOM-03 item 损耗缺省按品类默认（面料6/辅料显式2）',
      it_fabric['loss_rate'] == '6.00' and it_acc['loss_rate'] == '2.00',
      f"{it_fabric['loss_rate']}/{it_acc['loss_rate']}")
s, d = api('POST', '/tech/boms', token=TA, cid=CID, data={
    'style': style.id,
    'items': [{'material': M1, 'unit_usage': '2.5', 'color': '红', 'size': 'L'},
              {'material': M1, 'unit_usage': '1', 'color': '红', 'size': 'L'}]})
check('T-BOM-04 同bom同物料同色码重复 400', s == 400 and '重复' in json.dumps(
    d, ensure_ascii=False), f's={s} {d}')
s, d = api('POST', '/tech/boms', token=TA, cid=CID, data={
    'style': style.id, 'items': [{'material': 99999999, 'unit_usage': '1'}]})
check('T-BOM-05 不存在物料 400', s == 400, f's={s}')
s, d = api('POST', '/tech/boms', token=TA, cid=CID, data={
    'style': style_b.id, 'items': []})
check('T-BOM-06 跨厂款式 400', s == 400, f's={s} {d}')
s, d = api('POST', '/tech/boms', token=TB, cid=CID_B, data={
    'style': style_b.id, 'items': [
        {'material': Material.objects.get(company=company_b,
                                          code='TTEC-M001').id,
         'unit_usage': '1.2'}]})
check('T-BOM-07 B厂建自己的BOM OK', s == 201, f's={s} {d}')
BOM_B = d['id']
s, d = api('POST', '/tech/boms', token=TA, cid=CID, data={'style': style.id,
                                                         'items': []})
check('T-BOM-08 同款第二版自动 version=2', s == 201 and d.get('version') == 2,
      f's={s} {d}')
BOM2 = d['id']
s, d = api('POST', '/tech/boms', token=TA, cid=CID, data={'style': style.id,
                                                         'version': 2, 'items': []})
check('T-BOM-09 显式重复版本 400', s == 400 and '已存在' in json.dumps(
    d, ensure_ascii=False), f's={s} {d}')
s, d = api('GET', '/tech/boms?style_id=%d' % style.id, token=TA, cid=CID)
check('T-BOM-10 style_id 过滤 2 条', s == 200 and d.get('count') == 2, f's={s} {d}')
s, d = api('GET', '/tech/boms?style_id=%d&version=1' % style.id, token=TA, cid=CID)
check('T-BOM-11 style_id+version 过滤 1 条', s == 200 and d.get('count') == 1,
      f's={s} {d}')
# 激活：v1 → active；v2 → active 时 v1 自动降级
s, d = api('POST', f'/tech/boms/{BOM1}/activate', token=TA, cid=CID)
check('T-BOM-12 激活 v1 → active', s == 200 and d.get('status') == 'active',
      f's={s} {d}')
s, d = api('POST', f'/tech/boms/{BOM1}/activate', token=TA, cid=CID)
check('T-BOM-13 重复激活 400', s == 400, f's={s} {d}')
s, d = api('POST', f'/tech/boms/{BOM2}/activate', token=TA, cid=CID)
check('T-BOM-14 激活 v2 → v1 自动降级 deactivated',
      s == 200 and d.get('status') == 'active'
      and Bom.objects.get(pk=BOM1).status == 'deactivated', f's={s} {d}')
# 反激活：v1（deactivated）重新激活 → v2 降级（单 active 恒成立）
s, d = api('POST', f'/tech/boms/{BOM1}/activate', token=TA, cid=CID)
check('T-BOM-15 停用版可重新激活，旧 active 自动降级',
      s == 200 and Bom.objects.get(pk=BOM2).status == 'deactivated', f's={s} {d}')
# DB 级单 active 兜底（部分唯一约束）
forced = False
try:
    with transaction.atomic():
        Bom.objects.create(company=company, style=style, version=99,
                           status=Bom.Status.ACTIVE)
except IntegrityError:
    forced = True
check('T-BOM-16 单 active 部分唯一约束 DB 兜底生效', forced, '')
if not forced:
    Bom.all_objects.filter(company=company, style=style, version=99).delete()
# fork 改版
s, d = api('POST', f'/tech/boms/{BOM1}/fork', token=TA, cid=CID)
check('T-BOM-17 fork → 新版本=max+1 草稿', s == 200 and d.get('version') == 3
      and d.get('status') == 'draft', f's={s} {d}')
BOM3 = d['id']
check('T-BOM-18 fork 明细整表复制', len(d.get('items', [])) == 2
      and all(i['material'] in (M1, ACC) for i in d['items']),
      str(d.get('items')))
check('T-BOM-19 fork 后旧 active 停用留痕',
      Bom.objects.get(pk=BOM1).status == 'deactivated', '')
s, d = api('PUT', f'/tech/boms/{BOM3}', token=TA, cid=CID,
           data={'remark': '改备注', 'items': [
               {'material': M2, 'kind': 'main', 'unit_usage': '1.8',
                'loss_rate': '7.5'}]})
check('T-BOM-20 PUT items 全量替换+remark', s == 200 and d.get('remark') == '改备注'
      and len(d['items']) == 1 and d['items'][0]['loss_rate'] == '7.50',
      f's={s} {d}')
s, d = api('PUT', f'/tech/boms/{BOM3}', token=TA, cid=CID, data={'remark': '只改备注'})
check('T-BOM-21 PUT 不传 items 保持原明细', s == 200 and len(d['items']) == 1,
      f's={s} {d}')
s, d = api('GET', f'/tech/boms/{BOM1}/references', token=TA, cid=CID)
check('T-BOM-22 references 预留 count=0', s == 200 and d.get('count') == 0,
      f's={s} {d}')
s, d = api('DELETE', f'/tech/boms/{BOM3}', token=TA, cid=CID)
check('T-BOM-23 DELETE 软删', s in (200, 204), f's={s}')
s, d = api('GET', '/tech/boms?style_id=%d' % style.id, token=TA, cid=CID)
check('T-BOM-24 软删后列表不见', s == 200 and d.get('count') == 2, f's={s} {d}')
s, d = api('GET', f'/tech/boms/{BOM_B}', token=TA, cid=CID)
check('T-ISO-06 A厂读B厂BOM 404', s == 404, f's={s}')

# ================= T-IMP 导入 =================
print('--- T-IMP 导入 ---')
# 物料 JSON 导入：错误行（缺码/负数/换算缺参/批内重复码）→ 整批不入
rows_bad = [
    {'编码': '', '品名': '无码行', '分类': '面料', '主单位': '米'},
    {'编码': 'TTEC-M901', '品名': '负克重', '分类': '面料', '主单位': '公斤',
     '副单位': '米', '克重': -5, '幅宽': 150},
    {'编码': 'TTEC-M902', '品名': '缺换算参', '分类': '面料', '主单位': '公斤',
     '副单位': '米'},
    {'编码': 'TTEC-M901', '品名': '批内重复码', '分类': '辅料', '主单位': '粒'},
]
s, d = api('POST', '/tech/materials/import', token=TA, cid=CID, data={'rows': rows_bad})
errs = d.get('errors', [])
check('T-IMP-01 物料导入坏行 → created=0 全批不入库',
      s == 200 and d.get('created') == 0 and d.get('updated') == 0
      and Material.objects.filter(company=company, code='TTEC-M901').count() == 0,
      f's={s} {d}')
check('T-IMP-02 错误行带行号（2/3/4/5）与中文原因',
      sorted(e['row'] for e in errs) == [2, 3, 4, 5]
      and all(e.get('reason') for e in errs), json.dumps(errs, ensure_ascii=False))
check('T-IMP-03 错误原因含中文（换算缺参/负数/批内重复）',
      any('克重' in e['reason'] for e in errs)
      and any('负' in e['reason'] or '不合法' in e['reason'] for e in errs)
      and any('重复' in e['reason'] for e in errs),
      json.dumps(errs, ensure_ascii=False))
rows_ok = [
    {'编码': 'TTEC-M901', '品名': '涤塔夫', '分类': '里料', '主单位': '米',
     '缩率': '3.20', '旧编码': 'OLD-901'},
    {'编码': 'TTEC-M902', '品名': '针织罗纹', '分类': '面料', '主单位': '公斤',
     '副单位': '米', '克重': '250', '幅宽': '90', '旧编码': 'OLD-902'},
    {'编码': 'TTEC-M001', '品名': '帆布三用(导入改)', '分类': '面料', '主单位': '米'},
]
s, d = api('POST', '/tech/materials/import', token=TA, cid=CID, data={'rows': rows_ok})
check('T-IMP-04 物料导入 created=2 updated=1 errors=[]',
      s == 200 and d.get('created') == 2 and d.get('updated') == 1
      and d.get('errors') == [], f's={s} {d}')
m901 = Material.objects.get(company=company, code='TTEC-M901')
check('T-IMP-05 导入字段落库（缩率/旧编码/分类）',
      m901.shrink_rate == Decimal('3.20') and m901.legacy_code == 'OLD-901'
      and m901.category == 'lining', '')
m902 = Material.objects.get(company=company, code='TTEC-M902')
check('T-IMP-06 导入双单位物料 conversion_ready=True',
      m902.weight_gsm == Decimal('250') and m902.conversion_ready, '')
s, d = api('POST', '/tech/materials/import', token=T_PLAIN, cid=CID,
           data={'rows': rows_ok})
check('T-IMP-07 无权导入 403（import 并入 tech.material）', s == 403, f's={s}')
# xlsx 真文件导入 + 坏表头
tmpdir = tempfile.mkdtemp(prefix='tech_imp_')
from openpyxl import Workbook, load_workbook
xlsx_path = os.path.join(tmpdir, 'materials.xlsx')
wb = Workbook()
ws = wb.active
ws.append(['编码', '品名', '分类', '主单位', '副单位', '克重', '幅宽', '缩率', '旧编码'])
ws.append(['TTEC-M903', '导入帆布', '面料', '米', '', '', '', '2.00', 'OLD-903'])
ws.append(['TTEC-M904', '导入纽扣', '辅料', '粒', '', None, None, None, ''])
ws.append([None, None, None, None, None, None, None, None, None])  # 空行跳过
ws.append(['TTEC-M905', '导入坏行', '面料', '米', '米', None, None, None, ''])  # 副单位缺参
wb.save(xlsx_path)
s, d = api_upload('/tech/materials/import', TA, CID, xlsx_path)
check('T-IMP-08 xlsx 坏行 → all-or-nothing created=0 errors 带 Excel 行号5（空行跳过）',
      s == 200 and d.get('created') == 0 and d.get('updated') == 0
      and len(d.get('errors', [])) == 1 and d['errors'][0]['row'] == 5,
      f's={s} {d}')
xlsx_ok = os.path.join(tmpdir, 'materials_ok.xlsx')
wb1b = Workbook()
ws1b = wb1b.active
ws1b.append(['编码', '品名', '分类', '主单位', '副单位', '克重', '幅宽', '缩率', '旧编码'])
ws1b.append(['TTEC-M903', '导入帆布', '面料', '米', '', '', '', '2.00', 'OLD-903'])
ws1b.append(['TTEC-M904', '导入纽扣', '辅料', '粒', '', None, None, None, ''])
ws1b.append(['TTEC-M903', '导入帆布改', '面料', '米', '', '', '', '2.50', 'OLD-903'])
wb1b.save(xlsx_ok)
s, d = api_upload('/tech/materials/import', TA, CID, xlsx_ok)
check('T-IMP-08b xlsx 批内同码重复 → all-or-nothing created=0 errors 批内重复',
      s == 200 and d.get('created') == 0 and d.get('updated') == 0
      and len(d.get('errors', [])) == 1 and '重复' in d['errors'][0]['reason'],
      f's={s} {d}')
xlsx_upd = os.path.join(tmpdir, 'materials_upd.xlsx')
wb1c = Workbook()
ws1c = wb1c.active
ws1c.append(['编码', '品名', '分类', '主单位', '副单位', '克重', '幅宽', '缩率', '旧编码'])
ws1c.append(['TTEC-M903', '导入帆布改', '面料', '米', '', '', '', '2.50', 'OLD-903'])
ws1c.append(['TTEC-M901', '导入更新行', '面料', '米', '', '', '', '', ''])
wb1c.save(xlsx_upd)
s, d = api_upload('/tech/materials/import', TA, CID, xlsx_upd)
check('T-IMP-08c xlsx created=1 updated=1（存在即更新，无静默）',
      s == 200 and d.get('created') == 1 and d.get('updated') == 1
      and d.get('errors') == [], f's={s} {d}')
xlsx_bad = os.path.join(tmpdir, 'bad_head.xlsx')
wb2 = Workbook()
ws2 = wb2.active
ws2.append(['乱列', '随便'])
ws2.append(['x', 'y'])
wb2.save(xlsx_bad)
s, d = api_upload('/tech/materials/import', TA, CID, xlsx_bad)
check('T-IMP-09 坏表头 400 表头列不识别', s == 400, f's={s} {d}')
# BOM JSON 导入：两行同(款,版本)归并一个草稿；错误行全批不入
bom_rows_bad = [
    {'款号': 'TTEC-S1', '版本': 10, '物料编码': 'TTEC-M901', '单耗': '1.1'},
    {'款号': 'TTEC-SX', '版本': 1, '物料编码': 'TTEC-M901', '单耗': '1.1'},
    {'款号': 'TTEC-S1', '版本': 10, '物料编码': 'TTEC-NOPE', '单耗': '1.1'},
    {'款号': 'TTEC-S1', '版本': 10, '物料编码': 'TTEC-M901', '单耗': '0'},
]
s, d = api('POST', '/tech/boms/import', token=TA, cid=CID, data={'rows': bom_rows_bad})
errs = d.get('errors', [])
check('T-IMP-10 BOM导入坏行 全批不入库 errors 行号3/4/5（首行合法也不入库）',
      d.get('created') == 0 and sorted(e['row'] for e in errs) == [3, 4, 5],
      f'{d} {json.dumps(errs, ensure_ascii=False)}')
check('T-IMP-11 BOM错误行中文原因（款号/物料不存在/单耗）',
      any('不存在' in e['reason'] for e in errs)
      and any('单耗' in e['reason'] for e in errs),
      json.dumps(errs, ensure_ascii=False))
bom_rows_ok = [
    {'款号': 'TTEC-S1', '版本': 10, '物料编码': 'TTEC-M901', '类别': '里料',
     '单耗': '1.5', '损耗率': '3'},
    {'款号': 'TTEC-S1', '版本': 10, '物料编码': 'TTEC-M005', '类别': '辅料',
     '颜色': '黑', '单耗': '2'},
    {'款号': 'TTEC-S1', '版本': 11, '物料编码': 'TTEC-M901', '单耗': '1.6'},
]
s, d = api('POST', '/tech/boms/import', token=TA, cid=CID, data={'rows': bom_rows_ok})
check('T-IMP-12 BOM导入 created=2 updated=0',
      s == 200 and d.get('created') == 2 and d.get('updated') == 0
      and d.get('errors') == [], f's={s} {d}')
bom10 = Bom.objects.get(company=company, style=style, version=10)
acc_item = bom10.items.filter(material__code='TTEC-M005').first()
check('T-IMP-13 BOM导入明细归并+损耗缺省品类默认（辅料0.4）',
      bom10.items.count() == 2 and acc_item is not None
      and acc_item.loss_rate == Decimal('0.40'), '')
s, d = api('POST', '/tech/boms/import', token=TA, cid=CID, data={'rows': [
    {'款号': 'TTEC-S1', '版本': 10, '物料编码': 'TTEC-M901', '单耗': '1.1'}]})
check('T-IMP-14 重复导入已有版本 → errors 已存在', d.get('created') == 0
      and any('已存在' in e['reason'] for e in d.get('errors', [])),
      f'{d}')
s, d = api('POST', '/tech/boms/import', token=T_PLAIN, cid=CID, data={'rows': []})
check('T-IMP-15 无权 BOM 导入 403（import 并入 tech.bom）', s == 403, f's={s}')

# ================= T-COV 覆盖补缺（INT 门禁 2026-10-24：views 分支清零） =================
print('--- T-COV 覆盖补缺 ---')
from decimal import InvalidOperation
from rest_framework.exceptions import ValidationError as DRFValidationError
from unittest import mock

from apps.tech import views as tech_views

# ---- 单元级：_to_decimal / _purge_deleted / models 计算属性 / __str__ ----
check('T-COV-01 _to_decimal(None/空串) → None',
      tech_views._to_decimal(None) is None and tech_views._to_decimal('') is None)
try:
    tech_views._to_decimal('abc')
    _bad = False
except InvalidOperation:
    _bad = True
check('T-COV-02 _to_decimal 非法数字抛 InvalidOperation', _bad)

m_purge = Material.objects.create(company=company, code='TTEC-MPURGE',
                                  name='待清物料', unit='米')
m_purge.delete()  # 软删
tech_views._purge_deleted(Material, company=company, code='TTEC-MPURGE')
check('T-COV-03 _purge_deleted 物理清理无引用软删行',
      Material.all_objects.filter(code='TTEC-MPURGE').count() == 0)

m_cv = Material.objects.get(pk=M1)
m_cv.attrs = {'loss_rate': 'abc'}  # 仅内存：attrs.loss_rate 非法回落
check('T-COV-04 attrs.loss_rate 非法 → 回落品类默认 6（except 分支）',
      m_cv.default_loss_rate() == Decimal('6'))
check('T-COV-05 gross_usage 计算属性 = 单耗×(1+损耗率)',
      BomItem(bom=Bom.objects.get(pk=BOM1), material=m_cv, unit='米',
              unit_usage=Decimal('2'), loss_rate=Decimal('10')).gross_usage
      == Decimal('2.2000'))
check('T-COV-06 三模型 __str__ 不抛异常',
      all([str(m_cv), str(Bom.objects.get(pk=BOM1)),
           str(BomItem.objects.filter(bom=BOM1).first())]))

# ---- 物料导入通道：绕开序列化器 ChoiceField 前置拦截，直达 _clean_material_payload 分支 ----
s, d = api('POST', '/tech/materials/import', token=TA, cid=CID, data={'rows': [
    {'编码': 'TTEC-CV1', '品名': '', '分类': '面料', '主单位': '米'},
    {'编码': 'TTEC-CV2', '品名': 'x', '分类': '面料', '主单位': ''},
    {'编码': 'TTEC-CV3', '品名': 'x', '分类': 123, '主单位': '米'},
    {'编码': 'TTEC-CV4', '品名': 'x', '分类': '面料', '主单位': '米', '克重': 'abc'},
]})
errs = {e['row']: e['reason'] for e in d.get('errors', [])}
check('T-COV-07 导入缺品名(行2)/缺主单位(行3) 中文原因',
      '品名' in errs.get(2, '') and '主单位' in errs.get(3, ''),
      json.dumps(errs, ensure_ascii=False))
check('T-COV-08 导入非法分类（数字单元格）→ 分类须为 面料/里料/辅料/包装（行4）',
      '分类' in errs.get(4, ''), json.dumps(errs, ensure_ascii=False))
check('T-COV-09 导入克重非数字 → 数值不合法（行5）',
      '不合法' in errs.get(5, ''), json.dumps(errs, ensure_ascii=False))
check('T-COV-10 坏行全批不入库（all-or-nothing）',
      d.get('created') == 0 and d.get('updated') == 0, f'{d}')
try:
    tech_views._clean_material_payload(company, {'code': 'X', 'name': 'x',
                                                 'unit': '米', 'status': 'banned'})
    _st = False
except DRFValidationError as e:
    _st = 'status' in e.detail
check('T-COV-11 status 非法 → 状态须为 active/disabled', _st)
s, d = api('POST', '/tech/materials', token=TA, cid=CID, data={
    'code': 'TTEC-CV5', 'name': 'attrs坏', 'unit': '米', 'attrs': [1, 2]})
check('T-COV-12 attrs 非对象 → 400 扩展属性须为对象',
      s == 400 and '扩展属性' in json.dumps(d, ensure_ascii=False), f'{s} {d}')

# ---- _validated_bom_items 校验分支（API PUT 直达） ----
s, d = api('PUT', f'/tech/boms/{BOM2}', token=TA, cid=CID, data={'items': 'x'})
check('T-COV-13 PUT items 非列表 → 400 明细须为列表',
      s == 400 and '明细须为列表' in json.dumps(d, ensure_ascii=False), f'{s} {d}')
s, d = api('PUT', f'/tech/boms/{BOM2}', token=TA, cid=CID, data={'items': ['x']})
check('T-COV-14 明细项非对象 → 400 格式不正确',
      s == 400 and '格式不正确' in json.dumps(d, ensure_ascii=False), f'{s} {d}')
s, d = api('PUT', f'/tech/boms/{BOM2}', token=TA, cid=CID, data={'items': [{'unit_usage': '1'}]})
check('T-COV-15 明细缺物料 → 400 缺少物料',
      s == 400 and '缺少物料' in json.dumps(d, ensure_ascii=False), f'{s} {d}')
s, d = api('PUT', f'/tech/boms/{BOM2}', token=TA, cid=CID, data={
    'items': [{'material': 99999999, 'unit_usage': '1'}]})
check('T-COV-16 明细物料不存在 → 400', s == 400, f'{s} {d}')
s, d = api('PUT', f'/tech/boms/{BOM2}', token=TA, cid=CID, data={'items': [{'material': M1}]})
check('T-COV-17 明细缺单耗 → 400 单耗必须大于0（含 _to_decimal(None)）',
      s == 400 and '单耗必须大于0' in json.dumps(d, ensure_ascii=False), f'{s} {d}')
s, d = api('PUT', f'/tech/boms/{BOM2}', token=TA, cid=CID, data={
    'items': [{'material': M1, 'unit_usage': 'abc'}]})
check('T-COV-18 明细单耗非法 → 400 单耗不合法',
      s == 400 and '单耗不合法' in json.dumps(d, ensure_ascii=False), f'{s} {d}')
s, d = api('PUT', f'/tech/boms/{BOM2}', token=TA, cid=CID, data={
    'items': [{'material': M1, 'unit_usage': '0'}]})
check('T-COV-19 明细单耗为0 → 400 单耗必须大于0',
      s == 400 and '单耗必须大于0' in json.dumps(d, ensure_ascii=False), f'{s} {d}')
s, d = api('PUT', f'/tech/boms/{BOM2}', token=TA, cid=CID, data={
    'items': [{'material': M1, 'unit_usage': '1', 'loss_rate': 'abc'}]})
check('T-COV-20 明细损耗率非法 → 400',
      s == 400 and '损耗率不合法' in json.dumps(d, ensure_ascii=False), f'{s} {d}')
s, d = api('PUT', f'/tech/boms/{BOM2}', token=TA, cid=CID, data={
    'items': [{'material': M1, 'unit_usage': '1', 'loss_rate': '-1'}]})
check('T-COV-21 明细损耗率为负 → 400',
      s == 400 and '不能为负数' in json.dumps(d, ensure_ascii=False), f'{s} {d}')
s, d = api('PUT', f'/tech/boms/{BOM2}', token=TA, cid=CID, data={
    'items': [{'material': M1, 'unit_usage': '1', 'kind': 'weird'}]})
check('T-COV-22 非法 kind 归一为 main → 200', s == 200 and len(d.get('items', [])) == 1
      and d['items'][0]['kind'] == 'main', f'{s} {d}')

# ---- 物料 PUT 撞软删码 + 建物料竞态 IntegrityError ----
s, d = api('POST', '/tech/materials', token=TA, cid=CID, data={
    'code': 'TTEC-DEADY', 'name': '待删Y', 'unit': '米'})
YID = d['id']
api('DELETE', f'/tech/materials/{YID}', token=TA, cid=CID)
s, d = api('POST', '/tech/materials', token=TA, cid=CID, data={
    'code': 'TTEC-ALIVEX', 'name': '存活X', 'unit': '米'})
XID = d['id']
s, d = api('PUT', f'/tech/materials/{XID}', token=TA, cid=CID, data={
    'code': 'TTEC-DEADY', 'name': '撞软删', 'unit': '米'})
check('T-COV-23 PUT 改码撞软删行 → 400 物料编码已存在',
      s == 400 and '已存在' in json.dumps(d, ensure_ascii=False), f'{s} {d}')
api('POST', '/tech/materials', token=TA, cid=CID, data={
    'code': 'TTEC-RACE1', 'name': '占位', 'unit': '米'})
with mock.patch.object(tech_views, '_clean_material_payload',
                       return_value={'code': 'TTEC-RACE1', 'name': '竞态',
                                     'category': 'fabric', 'unit': '米',
                                     'unit_alt': '', 'status': 'active',
                                     'legacy_code': '', 'attrs': {}}):
    s, d = api('POST', '/tech/materials', token=TA, cid=CID, data={
        'code': 'TTEC-RACE1', 'name': '竞态', 'unit': '米'})
check('T-COV-24 建物料竞态 IntegrityError → 400 物料编码已存在',
      s == 400 and '已存在' in json.dumps(d, ensure_ascii=False), f'{s} {d}')

# ---- 导入行异常兜底（mock 注入非预期异常） ----
with mock.patch.object(tech_views, '_clean_material_payload',
                       side_effect=ValueError('boom')):
    s, d = api('POST', '/tech/materials/import', token=TA, cid=CID, data={'rows': [
        {'编码': 'TTEC-BOOM', '品名': 'x', '主单位': '米'}]})
check('T-COV-25 导入行未知异常 → 格式不正确兜底',
      d.get('errors') and '格式不正确' in d['errors'][0]['reason'], f'{d}')
with mock.patch.object(tech_views, '_clean_material_payload',
                       side_effect=DRFValidationError('整行坏')):
    s, d = api('POST', '/tech/materials/import', token=TA, cid=CID, data={'rows': [
        {'编码': 'TTEC-BOOM2', '品名': 'x', '主单位': '米'}]})
check('T-COV-26 导入行非字典 ValidationError → reason 原文',
      d.get('errors') and '整行坏' in d['errors'][0]['reason'], f'{d}')

# ---- 导入命中软删行复活路径 ----
s, d = api('POST', '/tech/materials', token=TA, cid=CID, data={
    'code': 'TTEC-REVR', 'name': '复活体', 'unit': '米'})
RID = d['id']
api('DELETE', f'/tech/materials/{RID}', token=TA, cid=CID)
s, d = api('POST', '/tech/materials/import', token=TA, cid=CID, data={'rows': [
    {'编码': 'TTEC-REVR', '品名': '复活改', '分类': '面料', '主单位': '米'}]})
check('T-COV-27 物料导入命中软删行 → 复活 updated=1 created=0',
      d.get('created') == 0 and d.get('updated') == 1
      and Material.objects.get(code='TTEC-REVR').name == '复活改', f'{d}')

# ---- 导入文件通道错误分支 ----
bad_ext = os.path.join(tmpdir, 'x.csv')
with open(bad_ext, 'w') as f:
    f.write('a,b\n')
s, d = api_upload('/tech/materials/import', TA, CID, bad_ext)
check('T-COV-28 非 xlsx 扩展名 → 400 仅支持 .xlsx', s == 400, f'{s} {d}')
corrupt = os.path.join(tmpdir, 'corrupt.xlsx')
with open(corrupt, 'wb') as f:
    f.write(b'this is not a zip archive')
s, d = api_upload('/tech/materials/import', TA, CID, corrupt)
check('T-COV-29 损坏 xlsx → 400 文件解析失败', s == 400, f'{s} {d}')
empty_x = os.path.join(tmpdir, 'empty.xlsx')
wbe = Workbook()
wbe.save(empty_x)
s, d = api_upload('/tech/materials/import', TA, CID, empty_x)
check('T-COV-30 空白工作簿 → 400 文件内容为空', s == 400, f'{s} {d}')
s, d = api('POST', '/tech/materials/import', token=TA, cid=CID)
check('T-COV-31 无 file 无 rows → 400', s == 400, f'{s} {d}')
s, d = api('POST', '/tech/materials/import', token=TA, cid=CID, data={'rows': ['x']})
check('T-COV-32 rows 含非对象 → 400 第1行格式不正确',
      s == 400 and '格式不正确' in json.dumps(d, ensure_ascii=False), f'{s} {d}')
s, d = api('POST', '/tech/materials/import', token=TA, cid=CID, data={'rows': []})
check('T-COV-33 物料导入空行集 → 无数据行',
      d.get('errors', [{}])[0].get('reason') == '无数据行', f'{d}')

# ---- BOM 导入校验分支（缺款号/版本非法/缺物料编码/单耗/损耗率/空行集/版本缺省/明细重复/复活） ----
s, d = api('POST', '/tech/boms/import', token=TA, cid=CID, data={'rows': []})
check('T-COV-34 BOM导入空行集 → 无数据行',
      d.get('errors', [{}])[0].get('reason') == '无数据行', f'{d}')
s, d = api('POST', '/tech/boms/import', token=TA, cid=CID, data={'rows': [
    {'款号': '', '物料编码': 'TTEC-M001', '单耗': '1'},
    {'款号': 'TTEC-S1', '版本': 'abc', '物料编码': 'TTEC-M001', '单耗': '1'},
    {'款号': 'TTEC-S1', '版本': 0, '物料编码': 'TTEC-M001', '单耗': '1'},
    {'款号': 'TTEC-S1', '版本': 30, '物料编码': '', '单耗': '1'},
    {'款号': 'TTEC-S1', '版本': 30, '物料编码': 'TTEC-M001', '单耗': 'abc'},
    {'款号': 'TTEC-S1', '版本': 30, '物料编码': 'TTEC-M001', '单耗': '1', '损耗率': 'x'},
    {'款号': 'TTEC-S1', '版本': 30, '物料编码': 'TTEC-M001', '单耗': '1', '损耗率': '-2'},
]})
errs = {e['row']: e['reason'] for e in d.get('errors', [])}
check('T-COV-35 BOM导入 缺款号(2)/版本非法(3,4)/缺物料编码(5)/单耗非法(6)/损耗率非法(7,8)',
      sorted(errs) == [2, 3, 4, 5, 6, 7, 8]
      and '款号' in errs.get(2, '') and '版本' in errs.get(3, '')
      and '版本' in errs.get(4, '') and '物料编码' in errs.get(5, '')
      and '单耗' in errs.get(6, '') and '损耗率' in errs.get(7, '')
      and '损耗率' in errs.get(8, ''), json.dumps(d, ensure_ascii=False))
check('T-COV-35b BOM坏行全批不入库', d.get('created') == 0 and d.get('updated') == 0)
style_cov = Style.objects.create(company=company, style_no='TTEC-SCOV', name='覆盖款')
s, d = api('POST', '/tech/boms/import', token=TA, cid=CID, data={'rows': [
    {'款号': 'TTEC-SCOV', '物料编码': 'TTEC-M001', '单耗': '1.5'}]})
check('T-COV-36 BOM导入版本缺省=1 → created=1',
      d.get('created') == 1 and d.get('errors') == [], f'{d}')
s, d = api('POST', '/tech/boms/import', token=TA, cid=CID, data={'rows': [
    {'款号': 'TTEC-SCOV', '版本': 40, '物料编码': 'TTEC-M001', '单耗': '1'},
    {'款号': 'TTEC-SCOV', '版本': 40, '物料编码': 'TTEC-M001', '单耗': '2'}]})
check('T-COV-37 BOM导入组内明细重复 → all-or-nothing created=0 明细重复',
      d.get('created') == 0
      and any('明细重复' in e['reason'] for e in d.get('errors', [])), f'{d}')
s, d = api('POST', '/tech/boms', token=TA, cid=CID, data={
    'style': style.id, 'version': 50,
    'items': [{'material': M1, 'unit_usage': '1'}]})
BDEAD = d['id']
api('DELETE', f'/tech/boms/{BDEAD}', token=TA, cid=CID)
s, d = api('POST', '/tech/boms/import', token=TA, cid=CID, data={'rows': [
    {'款号': 'TTEC-S1', '版本': 50, '物料编码': 'TTEC-M001', '单耗': '3.3'}]})
check('T-COV-38 BOM导入命中软删BOM → 复活 updated=1 明细替换 draft',
      d.get('created') == 0 and d.get('updated') == 1
      and Bom.objects.get(pk=BDEAD).status == 'draft'
      and BomItem.objects.filter(bom=BDEAD).count() == 1, f'{d}')

# ---- BOM 建/改/激活/列表 分支（序列化器前置拦截不可达的走 perform_create 单元桩） ----
s, d = api('GET', '/tech/boms', token=TA, cid=CID)
check('T-COV-39 GET /tech/boms 无过滤参数 → 200', s == 200, f'{s}')
s, d = api('GET', '/tech/boms?status=draft', token=TA, cid=CID)
check('T-COV-40 status 过滤全部为 draft', s == 200
      and all(r['status'] == 'draft' for r in d.get('results', [])), f'{s}')
s, d = api('PUT', f'/tech/boms/{BOM2}', token=TA, cid=CID, data={
    'items': [{'material': M2, 'unit_usage': '2'}]})
check('T-COV-41 PUT 只传 items 不传 remark → 200', s == 200
      and len(d.get('items', [])) == 1, f'{s} {d}')
s, d = api('POST', f'/tech/boms/{BPERM_ID}/fork', token=TA, cid=CID)
check('T-COV-42 fork 草稿源 → 新草稿且源保持 draft',
      s == 200 and d.get('status') == 'draft'
      and Bom.objects.get(pk=BPERM_ID).status == 'draft', f'{s} {d}')
with mock.patch.object(Bom, 'save', side_effect=IntegrityError('race')):
    s, d = api('POST', f'/tech/boms/{BOM2}/activate', token=TA, cid=CID)
check('T-COV-43 激活竞态 IntegrityError → 400 激活冲突',
      s == 400 and '激活冲突' in json.dumps(d, ensure_ascii=False), f'{s} {d}')
with mock.patch.object(BomItem.objects, 'bulk_create',
                       side_effect=IntegrityError('race')):
    s, d = api('POST', '/tech/boms', token=TA, cid=CID, data={
        'style': style.id, 'items': [{'material': M1, 'unit_usage': '1'}]})
check('T-COV-44 建BOM竞态 IntegrityError → 400 该款式此版本已存在',
      s == 400 and '已存在' in json.dumps(d, ensure_ascii=False), f'{s} {d}')


class _StubBomSer:
    """perform_create 单元桩：绕开 DRF 序列化器前置校验，直达视图分支。"""

    def __init__(self):
        self.instance = None

    def save(self, **kw):
        return Bom.objects.create(company=kw['company'], style=kw['style'],
                                  version=kw['version'], status=kw['status'],
                                  remark=kw['remark'])


_uc_admin = UserCompany.objects.filter(company=company, post=admin_post).first()


def _pc(data):
    v = tech_views.BomListCreateView()
    v.company = company
    v.user_company = _uc_admin
    v.request = type('R', (), {'data': data, 'user': None})()
    try:
        v.perform_create(_StubBomSer())
        return None
    except DRFValidationError as e:
        return e.detail


check('T-COV-45 perform_create style 非整数 → 款式不存在',
      _pc({'style': 'abc', 'items': []}) == {'style': '款式不存在'})
check('T-COV-46 perform_create version 非整数 → 版本须为正整数',
      _pc({'style': style.id, 'version': 'abc', 'items': []})
      == {'version': '版本须为正整数'})
check('T-COV-47 perform_create version=0 → 版本须为正整数',
      _pc({'style': style.id, 'version': 0, 'items': []})
      == {'version': '版本须为正整数'})

check('T-COV-48 PUT 不传 items 键 → 200 且原明细保持',
      api('PUT', f'/tech/boms/{BOM2}', token=TA, cid=CID,
          data={'remark': '不传items'})[0] == 200
      and len(BomItem.objects.filter(bom=BOM2)) == 1)

xlsx_trail = os.path.join(tmpdir, 'trail_head.xlsx')
wbt = Workbook()
wst = wbt.active
wst.append(['编码', '品名', '分类', '主单位', None])  # 尾部空表头：未知列检查放行
wst.append(['TTEC-XH1', '尾空表头料', '面料', '米', '废弃格'])
wbt.save(xlsx_trail)
s, d = api_upload('/tech/materials/import', TA, CID, xlsx_trail)
check('T-COV-49 xlsx 尾部空表头列被忽略 → created=1',
      d.get('created') == 1
      and Material.objects.filter(code='TTEC-XH1').exists(), f'{s} {d}')

with mock.patch.object(tech_views, '_clean_material_payload',
                       return_value={'code': 'TTEC-NOCODE', 'name': '无码行',
                                     'category': 'fabric', 'unit': '米',
                                     'unit_alt': '', 'status': 'active',
                                     'legacy_code': '', 'attrs': {}}):
    s, d = api('POST', '/tech/materials/import', token=TA, cid=CID, data={'rows': [
        {'品名': '无码行', '主单位': '米'}]})
check('T-COV-50 导入行无编码（防御分支）→ 仍按 payload 入库 created=1',
      d.get('created') == 1
      and Material.objects.filter(code='TTEC-NOCODE').exists(), f'{s} {d}')

check('T-COV-51 PUT items=[] → 200 且明细清空（跳过 bulk_create）',
      api('PUT', f'/tech/boms/{BOM2}', token=TA, cid=CID,
          data={'items': []})[0] == 200
      and BomItem.objects.filter(bom=BOM2).count() == 0)

check('T-COV-52 _validated_bom_items(raw=None) → None 保持原明细（防御分支）',
      tech_views._validated_bom_items(company, Bom.objects.get(pk=BOM2), None) is None)

# ================= 汇总 =================
print()
print(f'==== PASS {len(PASS)} / FAIL {len(FAIL)} ====')
if FAIL:
    print('FAILED:')
    for f in FAIL:
        print('  -', f)
