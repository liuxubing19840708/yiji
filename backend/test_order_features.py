# -*- coding: utf-8 -*-
"""
订单域新增功能（test_order_features）端到端后端验证 —— B1 交付（2026-10-21 用户 8 项需求）。

覆盖（实施方案 2026-10-21 增量）：
  工序模板 TPL-001~010（CRUD/重名 400/跨公司隔离/非法工序 400/负价 400/软删重建）
  订单增强 ORD-100~118（自动单号 SO+日期+3位/同日递增/并发序号不同/style 带出/
    images+process_items 落库回读/非法工序 400/PUT 显式清空 style 400/不带 style 保持）
  工艺说明 CRAFT-001~004（craft_note/craft_images/video POST/PUT 回读）
  视频上传 VID-001~003（mp4 放行/非 mp4 扩展名 400/mp4 扩展名假魔数 400）
  字段级报错 ERR-001~002（message 含中文字段名）
  挂单继承 HANG-001~003（带 order_id 继承订单款式/订单无款式且不传 400/存量路径不变）

跑法：manage.py shell -c "exec(compile(open('test_order_features.py',encoding='utf-8').read(),'test_order_features.py','exec'))"
幂等：复用「订单测试厂(可删)/订单测试B厂(可删)」（与 test_orders 同厂不同前缀互不干扰）；
每轮 preclean 硬清本套件前缀（TFOT）业务行（先子后父），上传的媒体文件同步清理。
"""
import os
import re
import threading
import time
from io import BytesIO

from django.contrib.auth.hashers import make_password
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.utils import timezone

from apps.accounts.models import Company, Post as PostM, User, UserCompany
from apps.accounts.services import create_company_with_owner
from apps.catalog.models import Customer, Process, ProcessTemplate, Style, Ticket
from apps.orders.models import Order, OrderItem
from apps.production.models import Production

PASS, FAIL = [], []
MEDIA_CLEANUP = []


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
    r = fn(f'/api{path}', data=json_dumps(data) if data is not None else None, **kw)
    try:
        body = json_loads(r.content.decode('utf-8'))
    except Exception:
        body = {}
    return r.status_code, body


def json_dumps(obj):
    import json
    return json.dumps(obj)


def json_loads(s):
    import json
    return json.loads(s)


def upload(token, filename, payload, content_type='application/octet-stream'):
    c = Client()
    r = c.post('/api/uploads/video', {'file': SimpleUploadedFile(filename, payload)},
               HTTP_AUTHORIZATION=f'Bearer {token}')
    try:
        body = json_loads(r.content.decode('utf-8'))
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


OWNER, H_B, H_PLAIN = '13988720101', '13988720102', '13988720103'
CO_A, CO_B = '订单测试厂(可删)', '订单测试B厂(可删)'
MARK = 'TFOT'  # 本套件数据前缀（与 test_orders 的 TORD 隔离）


def preclean(company, keep_phone=None):
    """幂等自清扫：只清本套件前缀（TFOT）数据，先子后父。

    同时清掉本套件自有账号在该厂的在职关系（上轮可能以第二管理员身份加入，
    会改变共享测试厂的管理员收件人口径，干扰交期预警等套件计数）；
    keep_phone 为本轮登录用的管理员号，保留其在职关系。
    """
    from apps.records.models import Record
    my_orders = Order.all_objects.filter(company=company).filter(
        Q_order_no() | Q_style_no())
    Record.objects.filter(company=company, ticket__production__bed_no__startswith=MARK).delete()
    Ticket.all_objects.filter(company=company, bed_no__startswith=MARK).delete()
    Production.all_objects.filter(company=company, bed_no__startswith=MARK).delete()
    OrderItem.all_objects.filter(order__in=my_orders).delete()
    my_orders.delete()
    ProcessTemplate.all_objects.filter(company=company, name__startswith=MARK).delete()
    from apps.tech.models import Bom  # noqa: E402  # D4 套件建了生效BOM，PROTECT 须先于款式删除
    Bom.all_objects.filter(style__company=company, style__style_no__startswith=MARK).delete()
    Style.all_objects.filter(company=company, style_no__startswith=MARK).delete()
    Process.all_objects.filter(company=company, name__startswith=MARK).delete()
    Customer.all_objects.filter(company=company, customer_no__startswith=MARK).delete()
    uc_qs = UserCompany.all_objects.filter(company=company, user__phone__in=[OWNER, H_B])
    if keep_phone:
        uc_qs = uc_qs.exclude(user__phone=keep_phone)
    uc_qs.delete()


def Q_order_no():
    from django.db.models import Q
    return Q(order_no__startswith=MARK)


def Q_style_no():
    from django.db.models import Q
    return Q(style__style_no__startswith=MARK)


# ================= 建厂（复用 test_orders 的测试厂） + preclean =================
company = Company.objects.filter(name=CO_A).first()
if company is None:
    owner = ensure_user(OWNER, '订单厂长')
    company, _ = create_company_with_owner(owner, CO_A)
else:
    # 复用厂：直接用厂内既有管理员（test_orders 厂长）登录，
    # 不另立管理员（避免改变共享测试厂的在管人数，干扰交期预警等套件的收件人口径）
    admin_post_a = PostM.objects.get(company=company, is_admin=True, is_deleted=False)
    uc_a = (UserCompany.objects.select_related('user')
            .filter(company=company, post=admin_post_a, is_deleted=False)
            .order_by('id').first())
    owner = uc_a.user if uc_a else ensure_user(OWNER, '订单厂长')
company_b = Company.objects.filter(name=CO_B).first()
if company_b is None:
    owner_b = ensure_user(H_B, '订单B厂长')
    company_b, _ = create_company_with_owner(owner_b, CO_B)
else:
    admin_post_b = PostM.objects.get(company=company_b, is_admin=True, is_deleted=False)
    uc_b = (UserCompany.objects.select_related('user')
            .filter(company=company_b, post=admin_post_b, is_deleted=False)
            .order_by('id').first())
    owner_b = uc_b.user if uc_b else ensure_user(H_B, '订单B厂长')
CID, CID_B = company.id, company_b.id

preclean(company, keep_phone=owner.phone)
preclean(company_b, keep_phone=owner_b.phone)

post_plain = ensure_post(company, '订单普工', [])
plain = ensure_user(H_PLAIN, '订单无权员')
ensure_member(plain, company, post_plain)

TA = login(owner.phone)    # A 厂管理员（'*'，复用厂时取厂内既有管理员）
TB = login(owner_b.phone)  # B 厂管理员
T_PLAIN = login(H_PLAIN)

# 最小业务档案
style = Style.objects.create(company=company, style_no=f'{MARK}-S1', name='功能测试款',
                             image='/media/uploads/tfot-s1.jpg')
style_b = Style.objects.create(company=company_b, style_no=f'{MARK}-SB', name='B厂测试款')
# D4 开裁缺 BOM 拦截（2026-10-24）：建床需款式有 active BOM，测试款补齐
from apps.tech.models import Bom  # noqa: E402
if not Bom.all_objects.filter(company=company, style=style).exists():
    Bom.objects.create(company=company, style=style, version=1, status=Bom.Status.ACTIVE)
if not Bom.all_objects.filter(company=company_b, style=style_b).exists():
    Bom.objects.create(company=company_b, style=style_b, version=1, status=Bom.Status.ACTIVE)
proc = Process.objects.create(company=company, name=f'{MARK}-P1', is_fixed=True,
                              default_price='2.500')
proc_b = Process.objects.create(company=company_b, name=f'{MARK}-PB', is_fixed=True)
cust = Customer.objects.create(company=company, customer_no=f'{MARK}-C1', name='功能测试客户')
today = timezone.localdate()

# ================= 工序模板 TPL-001~010 =================
st, d = api('POST', '/process-templates', TA, CID,
            {'name': f'{MARK}-模板A',
             'items': [{'process_id': proc.id, 'price': 2.5}]})
check('TPL-001 创建模板 201', st == 201 and d.get('id'), f'{st} {d}')
tpl_a = d.get('id')
check('TPL-002 items 回读', (d.get('items') or [{}])[0].get('process_id') == proc.id
      and str((d.get('items') or [{}])[0].get('price')) in ('2.5', '2.500'),
      str(d.get('items')))

st, d = api('GET', '/process-templates', TA, CID)
check('TPL-003 列表命中', st == 200
      and any(r.get('id') == tpl_a for r in d.get('results', [])), f'{st} {str(d)[:120]}')

st, d = api('POST', '/process-templates', TA, CID,
            {'name': f'{MARK}-模板A', 'items': [{'process_id': proc.id, 'price': 1}]})
check('TPL-004 重名 400 模板名称已存在',
      st == 400 and '模板名称已存在' in str(d), f'{st} {d}')

st, d = api('POST', '/process-templates', TA, CID,
            {'name': f'{MARK}-模板B', 'items': [{'process_id': 99999999, 'price': 1}]})
check('TPL-005 非法工序 400', st == 400 and '不存在' in str(d), f'{st} {d}')

st, d = api('POST', '/process-templates', TB, CID_B,
            {'name': f'{MARK}-模板B', 'items': [{'process_id': proc_b.id, 'price': 1}]})
tpl_b = d.get('id') if st == 201 else None
check('TPL-006 B厂自建模板 201', st == 201, f'{st} {d}')

st, d = api('POST', '/process-templates', TB, CID_B,
            {'name': f'{MARK}-模板C', 'items': [{'process_id': proc.id, 'price': 1}]})
check('TPL-007 跨公司工序 400', st == 400 and '不存在' in str(d), f'{st} {d}')

st, d = api('GET', f'/process-templates/{tpl_a}', TB, CID_B)
check('TPL-008 跨公司详情 404', st == 404, f'{st} {d}')

st, d = api('POST', '/process-templates', TA, CID,
            {'name': f'{MARK}-模板D', 'items': [{'process_id': proc.id, 'price': -1}]})
check('TPL-009 负价 400', st == 400 and ('不能为负数' in str(d)), f'{st} {d}')

st, d = api('POST', '/process-templates', T_PLAIN, CID,
            {'name': f'{MARK}-模板E', 'items': []})
check('TPL-010 无权限 403', st == 403, f'{st} {d}')

st, d = api('PUT', f'/process-templates/{tpl_a}', TA, CID,
            {'name': f'{MARK}-模板A2', 'items': [{'process_id': proc.id, 'price': 3}]})
check('TPL-011 PUT 改名改价 200',
      st == 200 and d.get('name') == f'{MARK}-模板A2'
      and str((d.get('items') or [{}])[0].get('price')) in ('3', '3.000'),
      f'{st} {d}')

st, d = api('DELETE', f'/process-templates/{tpl_b}', TB, CID_B)
check('TPL-012 DELETE 204', st == 204, f'{st} {d}')

st, d = api('POST', '/process-templates', TB, CID_B,
            {'name': f'{MARK}-模板B', 'items': [{'process_id': proc_b.id, 'price': 2}]})
check('TPL-013 软删后同名重建 201(condition UK)',
      st == 201, f'{st} {d}')

# ================= 订单增强 ORD-100~118 =================
# 自动单号：SO + YYYYMMDD + 3 位顺延
st, d = api('POST', '/orders', TA, CID,
            {'style': style.id, 'images': [f'/media/uploads/{MARK}-a.jpg'],
             'process_items': [{'process_id': proc.id, 'price': 2.5}],
             'items': [{'color': '黑', 'size': 'M', 'qty': 100}]})
check('ORD-100 建单 201', st == 201, f'{st} {d}')
no1 = (d or {}).get('order_no', '')
check('ORD-101 自动单号 SO+日期+3位',
      bool(re.fullmatch(r'SO\d{8}\d{3}', no1)), no1)
o1 = d['id']
seq1 = int(no1[-3:])

st, d = api('POST', '/orders', TA, CID, {'style': style.id,
                                         'items': [{'color': '白', 'size': 'M', 'qty': 50}]})
no2 = (d or {}).get('order_no', '')
check('ORD-102 同日第二单递增+1',
      bool(re.fullmatch(r'SO\d{8}\d{3}', no2)) and int(no2[-3:]) == seq1 + 1,
      f'{no1} -> {no2}')
o2 = d['id']

# 并发两次建单：序号必须不同（线程内撞库/写锁重试，SQLite 下仍不得重号）
concurrent_results = []


def _concurrent_create():
    import random
    for i in range(5):
        st_c, d_c = api('POST', '/orders', TA, CID,
                        {'style': style.id, 'items': [{'color': '红', 'size': 'S', 'qty': 1}]})
        if st_c == 201:
            concurrent_results.append(d_c.get('order_no'))
            break
        time.sleep(random.uniform(0.1 * (i + 1), 0.3 * (i + 1)))
    from django.db import connection
    connection.close()


threads = [threading.Thread(target=_concurrent_create) for _ in range(2)]
for t in threads:
    t.start()
for t in threads:
    t.join()
check('ORD-103 并发两次建单序号不同',
      len(concurrent_results) == 2 and concurrent_results[0] != concurrent_results[1],
      str(concurrent_results))

# style 带出 + images/process_items 落库回读
st, d = api('GET', f'/orders/{o1}', TA, CID)
check('ORD-104 style_no/style_name/style_image 带出',
      st == 200 and d.get('style') == style.id
      and d.get('style_no') == f'{MARK}-S1' and d.get('style_name') == '功能测试款'
      and d.get('style_image') == f'/media/uploads/tfot-s1.jpg', f'{st} {str(d)[:200]}')
check('ORD-105 images 回读', d.get('images') == [f'/media/uploads/{MARK}-a.jpg'],
      str(d.get('images')))
pi = d.get('process_items') or []
check('ORD-106 process_items 回读',
      len(pi) == 1 and pi[0].get('process_id') == proc.id and pi[0].get('price') == '2.500',
      str(pi))

st, d = api('GET', '/orders?keyword=功能测试款', TA, CID)
check('ORD-107 列表 style 带出',
      st == 200 and any(r.get('id') == o1 and r.get('style_no') == f'{MARK}-S1'
                        for r in d.get('results', [])), f'{st} {str(d)[:160]}')

st, d = api('POST', '/orders', TA, CID,
            {'style': style.id,
             'process_items': [{'process_id': 99999999, 'price': 1}],
             'items': [{'color': '黑', 'size': 'M', 'qty': 10}]})
check('ORD-108 非法订单工序 400', st == 400 and '不存在' in str(d), f'{st} {d}')

st, d = api('PUT', f'/orders/{o1}', TA, CID, {'style': None})
check('ORD-109 PUT 显式清空款式 400 请关联款式',
      st == 400 and '请关联款式' in str(d), f'{st} {d}')

st, d = api('PUT', f'/orders/{o1}', TA, CID, {'remark': '不带动款式'})
check('ORD-110 PUT 不带 style 400 请关联款式（修改必填）',
      st == 400 and '请关联款式' in str(d), f'{st} {d}')

st, d = api('PUT', f'/orders/{o1}', TA, CID, {'style': style.id})
check('ORD-110b PUT 带款式 200', st == 200 and d.get('style') == style.id, f'{st} {d}')

st, d = api('PUT', f'/orders/{o1}', TA, CID,
            {'style': style.id,
             'images': [f'/media/uploads/{MARK}-a.jpg', f'/media/uploads/{MARK}-b.jpg'],
             'process_items': [{'process_id': proc.id, 'price': 3.2}]})
st, d = api('GET', f'/orders/{o1}', TA, CID)
pi = d.get('process_items') or []
check('ORD-111 PUT 更新 images/process_items',
      len(d.get('images') or []) == 2 and len(pi) == 1 and pi[0].get('price') == '3.200',
      f'{d.get("images")} {pi}')

# ================= 款式工艺说明/视频字段 CRAFT-001~004 =================
st, d = api('POST', '/styles', TA, CID,
            {'style_no': f'{MARK}-S2', 'name': '工艺测试款',
             'craft_note': '先缝领口再上袖',
             'craft_images': [f'/media/uploads/{MARK}-c1.jpg'],
             'video': f'/media/uploads/{MARK}-v1.mp4'})
check('CRAFT-001 款式带工艺字段 201', st == 201 and d.get('craft_note') == '先缝领口再上袖'
      and d.get('craft_images') == [f'/media/uploads/{MARK}-c1.jpg']
      and d.get('video') == f'/media/uploads/{MARK}-v1.mp4', f'{st} {str(d)[:200]}')
s2 = d.get('id')

st, d = api('PUT', f'/styles/{s2}', TA, CID, {'craft_note': '改：先上袖'})
check('CRAFT-002 PUT 更新工艺说明',
      st == 200 and d.get('craft_note') == '改：先上袖', f'{st} {d}')

st, d = api('GET', f'/styles/{s2}', TA, CID)
check('CRAFT-003 GET 工艺字段回读', st == 200 and d.get('video') == f'/media/uploads/{MARK}-v1.mp4',
      f'{st} {str(d)[:160]}')

st, d = api('GET', '/styles', TA, CID)
row_s1 = next((r for r in d.get('results', []) if r.get('style_no') == f'{MARK}-S1'), {})
check('CRAFT-004 未传工艺字段缺省空（ORM 建款回读）',
      st == 200 and row_s1.get('craft_note') == '' and row_s1.get('video') == '',
      f'{st} {str(row_s1)[:160]}')

# ================= 视频上传 VID-001~003 =================
real_mp4 = b'\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom' + b'\x00' * 2048
st, d = upload(TA, f'{MARK}-demo.mp4', real_mp4, 'video/mp4')
url = (d or {}).get('url', '')
check('VID-001 mp4 上传成功 相对路径（同图片上传 200 格式）',
      st == 200 and url.startswith('/media/uploads/') and url.endswith('.mp4'),
      f'{st} {d}')
if url.startswith('/media/'):
    MEDIA_CLEANUP.append(url[len('/media/'):])

st, d = upload(TA, f'{MARK}-demo.avi', real_mp4, 'video/x-msvideo')
check('VID-002 非 mp4 扩展名 400', st == 400 and 'mp4' in str(d), f'{st} {d}')

st, d = upload(TA, f'{MARK}-fake.mp4', b'not-a-real-video-file' * 64, 'video/mp4')
check('VID-003 mp4 扩展名假魔数 400', st == 400, f'{st} {d}')

# ================= 字段级报错 ERR-001~002 =================
st, d = api('POST', '/styles', TA, CID, {'style_no': f'{MARK}-NONAME'})
check('ERR-001 缺名称 message 含中文字段名',
      st == 400 and '名称' in str(d.get('message', '')), f'{st} {d}')
st, d = api('POST', '/orders', TA, CID,
            {'style': style.id, 'items': [{'size': 'M', 'qty': 10}]})
check('ERR-002 订单明细缺颜色 400 含字段提示',
      st == 400 and '颜色' in str(d), f'{st} {d}')

st, d = api('POST', '/orders', TA, CID,
            {'items': [{'color': '黑', 'size': 'M', 'qty': 10}]})
check('ERR-003 POST 不带 style 400 请关联款式（新建必填）',
      st == 400 and '请关联款式' in str(d), f'{st} {d}')

st, d = api('POST', '/orders', TA, CID,
            {'style': 99999999, 'items': [{'color': '黑', 'size': 'M', 'qty': 10}]})
check('ERR-004 style 不存在 400 款式不存在',
      st == 400 and '款式不存在' in str(d), f'{st} {d}')

st, d = api('POST', '/orders', TA, CID,
            {'style': style.id, 'order_type': '外发',
             'items': [{'color': '黑', 'size': 'M', 'qty': 10}]})
check('ERR-005 order_type 外发 200 不拒收',
      st == 201 and d.get('order_type') == '外发', f'{st} {str(d)[:160]}')

st, d = api('POST', '/orders', TA, CID,
            {'style': style.id, 'order_type': '未知类型',
             'items': [{'color': '黑', 'size': 'M', 'qty': 10}]})
check('ERR-006 order_type 非法值归一自产不 400',
      st == 201 and d.get('order_type') == '自产', f'{st} {str(d)[:160]}')

# ================= 挂单款式继承 HANG-001~003 =================
st, d = api('POST', '/orders', TA, CID,
            {'style': style.id, 'items': [{'color': '黑', 'size': 'M', 'qty': 100}]})
o_hang = d['id']
st, d = api('POST', '/productions', TA, CID,
            {'bed_no': f'{MARK}-B1', 'order_id': o_hang, 'total_qty': 60})
check('HANG-001 挂单不传 style 继承订单款式',
      st == 201 and d.get('style') == style.id and d.get('style_no') == f'{MARK}-S1',
      f'{st} {str(d)[:160]}')

o_nostyle = Order.objects.create(company=company, order_no=f'{MARK}-NOSTYLE')
OrderItem.objects.create(order=o_nostyle, color='白', size='L', qty=40)
st, d = api('POST', '/productions', TA, CID,
            {'bed_no': f'{MARK}-B2', 'order_id': o_nostyle.id})
check('HANG-002 挂无款式订单且不传 style 400',
      st == 400 and '款式' in str(d), f'{st} {d}')

st, d = api('POST', '/productions', TA, CID,
            {'style': style.id, 'bed_no': f'{MARK}-B3', 'total_qty': 30})
check('HANG-003 存量路径（不带 order_id）不变',
      st == 201 and d.get('order') in (None, ''), f'{st} {str(d)[:160]}')

# ================= 挂单继承订单工序 HANG-010~012（2026-10-22 用户反馈） =================
# 订单配了关联工序（模板填充）→ 挂单开床不传 process_items 时自动继承为床次订单工序
st, d = api('POST', '/orders', TA, CID,
            {'style': style.id, 'items': [{'color': '黑', 'size': 'M', 'qty': 100}],
             'process_items': [{'process_id': proc.id, 'price': 2.5}]})
o_proc = d['id']
st, d = api('POST', '/productions', TA, CID,
            {'bed_no': f'{MARK}-B4', 'order_id': o_proc, 'total_qty': 50})
check('HANG-010 挂单开床 201', st == 201, f'{st} {str(d)[:120]}')
bed_inh = d.get('id') if st == 201 else 0
st, d = api('GET', f'/productions/{bed_inh}/order-processes', TA, CID)
rows = d if isinstance(d, list) else (d or {}).get('results', [])
check('HANG-011 订单工序自动继承（1 行，价 2.5）',
      st == 200 and len(rows) == 1 and rows[0].get('process') == proc.id
      and abs(float(rows[0].get('price')) - 2.5) < 1e-9, f'{st} {str(d)[:160]}')
# 显式传空 process_items = 明确不要继承，以请求为准
st, d = api('POST', '/orders', TA, CID,
            {'style': style.id, 'items': [{'color': '黑', 'size': 'M', 'qty': 100}],
             'process_items': [{'process_id': proc.id, 'price': 2.5}]})
o_proc2 = d['id']
st, d = api('POST', '/productions', TA, CID,
            {'bed_no': f'{MARK}-B5', 'order_id': o_proc2, 'process_items': []})
bed_no_inherit = d.get('id') if st == 201 else 0
st, d = api('GET', f'/productions/{bed_no_inherit}/order-processes', TA, CID)
rows2 = d if isinstance(d, list) else (d or {}).get('results', [])
check('HANG-012 显式空 process_items 不继承（显式优先）',
      st == 200 and not rows2, f'{st} {str(d)[:120]}')

# ================= 媒体文件清理（幂等） =================
from django.conf import settings as dj_settings  # noqa: E402
for rel in MEDIA_CLEANUP:
    p = os.path.join(str(dj_settings.MEDIA_ROOT), rel)
    try:
        if os.path.exists(p):
            os.remove(p)
    except OSError:
        pass

print(f'\n==== 订单新增功能套件：{len(PASS)} pass / {len(FAIL)} fail ====')
if FAIL:
    print('FAILED:', FAIL)
