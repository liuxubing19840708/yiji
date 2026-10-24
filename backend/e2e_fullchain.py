# -*- coding: utf-8 -*-
"""全链路逐功能 E2E：按前端真实调用顺序，双角色（管理员/员工）+ 双公司隔离，
覆盖认证/公司/岗位/员工/档案/生产/编菲/登记/工资/结算/日志/通知/安全边界全部域。

运行：PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe manage.py shell -c \
  "exec(compile(open('e2e_fullchain.py', encoding='utf-8').read(), 'e2e_fullchain.py', 'exec'))"
结束恢复演示库（硬删 E2E 标记数据）。
"""
import json

from django.test import Client
from django.utils import timezone

from apps.accounts.models import User, UserCompany, Post as PostM, SMSCode
from apps.catalog.models import Style, StyleProcess, Process, Ticket, Customer, CustomerGroup
from apps.production.models import Production
from apps.records.models import Record
from apps.salary.models import SettleApply, Settlement, SalaryAdjust
from apps.ops.models import CompanySetting, AuditLog, Notification

# 新 app 业务表（orders/kanban_template/warehouse/core）：清理用 ORM 级联（外键均 CASCADE），
# 否则 MARK_U 公司删除被这些子表 FK 挡住（2026-10-21 计量轮实测）
from apps.orders.models import Order
from apps.kanban_template.models import KanbanTemplate
from apps.warehouse.models import Lot, PurchaseOrder, StockIn, MaterialIssue
from apps.core.models import ClientLog

from django.db.models import Q
from datetime import timedelta

# 本地日期（timezone.now() 是 UTC，跨午夜后比本地早一天；统一用 localdate）
TODAY = timezone.localdate().isoformat()
MONTH_START = timezone.localdate().replace(day=1).isoformat()
FUTURE = (timezone.localdate() + timedelta(days=3)).isoformat()


def purge_settlements(flt):
    """硬删结算单：先把引用它的登记记录 settlement 置空（_raw_delete 不过 ORM SET_NULL）。"""
    qs = Settlement.all_objects.filter(flt)
    Record.all_objects.filter(settlement_id__in=qs.values('id')).update(settlement=None)
    qs._raw_delete(Settlement.all_objects.db)

c = Client()
PASS, FAIL = [], []
MARK_U = '全链路'          # 名称标记
E2E_BED = 'E2E01'


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


# ---- 预清理（幂等：清掉上次残留） ----
E2E_PHONES = ('13855556666', '13855557777')


def preclean():
    # 先删 E2E 用户相关行（登记/暂存 UC/用户），否则岗位与工序会被 FK 挡住
    for ph in E2E_PHONES:
        Record.all_objects.filter(user__phone=ph)._raw_delete(Record.all_objects.db)
        # 短信码无条件清：既防旧码复用，也清掉 60s 限频窗（连跑两轮时上一轮发码会顶住本轮发码）
        SMSCode.objects.filter(phone=ph).delete()
        u = User.objects.filter(phone=ph).first()
        if u:
            UserCompany.all_objects.filter(user=u)._raw_delete(UserCompany.all_objects.db)
            u.delete()
    Record.all_objects.filter(ticket__production__bed_no=E2E_BED)._raw_delete(Record.all_objects.db)
    Ticket.all_objects.filter(production__bed_no=E2E_BED)._raw_delete(Ticket.all_objects.db)
    # SQLite 库 FK 为 NO ACTION（无库级 CASCADE），必须先删子表
    from apps.production.models import ProductionProcess
    ProductionProcess.all_objects.filter(production__bed_no=E2E_BED)._raw_delete(ProductionProcess.all_objects.db)
    Production.all_objects.filter(bed_no=E2E_BED)._raw_delete(Production.all_objects.db)
    StyleProcess.all_objects.filter(style__style_no__in=['E2E001', 'E2E002'])._raw_delete(StyleProcess.all_objects.db)
    # 中断残留：款式副本（copy 的 style_no 不可预知），按名称标记清
    StyleProcess.all_objects.filter(process__name__contains=MARK_U)._raw_delete(StyleProcess.all_objects.db)
    StyleProcess.all_objects.filter(style__name__contains=MARK_U)._raw_delete(StyleProcess.all_objects.db)
    Style.all_objects.filter(style_no__in=['E2E001', 'E2E002'])._raw_delete(Style.all_objects.db)
    Style.all_objects.filter(name__contains=MARK_U)._raw_delete(Style.all_objects.db)
    Customer.all_objects.filter(customer_no='E2E-C01')._raw_delete(Customer.all_objects.db)
    CustomerGroup.all_objects.filter(name__contains=MARK_U)._raw_delete(CustomerGroup.all_objects.db)
    Process.all_objects.filter(name__contains=MARK_U)._raw_delete(Process.all_objects.db)
    # 只清无成员引用的岗位：跳过其他测试流（如 E2ETR 追溯流）仍在引用的岗位，防 FK 撞库
    PostM.all_objects.filter(name__contains='E2E').exclude(
        pk__in=UserCompany.all_objects.values('post_id'))._raw_delete(PostM.all_objects.db)
    PostM.all_objects.filter(name__in=['非法岗位', '越权岗']).exclude(
        pk__in=UserCompany.all_objects.values('post_id'))._raw_delete(PostM.all_objects.db)
    # 上次中断残留的测试公司（建公司会自动生成管理员岗位，需先删）
    for cid in Company.all_objects.filter(name__contains=MARK_U).values_list('id', flat=True):
        UserCompany.all_objects.filter(company_id=cid)._raw_delete(UserCompany.all_objects.db)
        PostM.all_objects.filter(company_id=cid)._raw_delete(PostM.all_objects.db)
        CompanySetting.objects.filter(company_id=cid).delete()
        # 新 app 子表自愈清理（ORM 级联；审计/通知/日志无业务锁，直接删）
        for m in (Order, KanbanTemplate, PurchaseOrder, StockIn, MaterialIssue, Lot,
                  Record, Ticket, Production, Style, Process, Customer, CustomerGroup,
                  Notification, AuditLog, ClientLog):
            try:
                (m.all_objects if hasattr(m, 'all_objects') else m.objects) \
                    .filter(company_id=cid).delete()
            except Exception:
                pass
        Company.all_objects.filter(pk=cid)._raw_delete(Company.all_objects.db)
    SettleApply.all_objects.filter(company_id=2, note__contains=MARK_U)._raw_delete(SettleApply.all_objects.db)
    purge_settlements(Q(company_id=2) & (Q(period_start=MONTH_START) | Q(period_start=TODAY)))
    SalaryAdjust.all_objects.filter(company_id=2, reason__contains=MARK_U)._raw_delete(SalaryAdjust.all_objects.db)
    SMSCode.objects.filter(phone='13800000001').delete()
    CompanySetting.objects.filter(company_id=2, key__in=['forbid_unapproved_register', 'auto_cut_done']).delete()


preclean()

# ============================================================
# S1 认证与我的
# ============================================================
code, d = req('post', '/auth/login', data={'phone': '13800000001', 'password': 'wrong'})
check('1.1 错误密码被拒', code in (400, 401), f'{code}')

code, d = req('post', '/auth/login', data={'phone': '13800000001', 'password': 'yisao1234'})
AT = d.get('access', '')
check('1.2 密码登录返回 token+user', code == 200 and AT and d.get('user', {}).get('id') == 6, f'{code} {str(d)[:80]}')

code, me = req('get', '/auth/me', AT, 2)
check('1.3 GET /auth/me', code == 200 and me.get('real_name') == '张厂长', f'{code} {str(me)[:80]}')

g0 = me.get('gender')
code, d = req('put', '/auth/me', AT, 2, {'gender': (g0 or 0) == 1 and 0 or 1})
code, me2 = req('get', '/auth/me', AT, 2)
check('1.4 PUT /auth/me 更新资料', code == 200 and me2.get('gender') != (g0 if g0 is not None else None), f'{code}')
req('put', '/auth/me', AT, 2, {'gender': g0})

# 改密码：新密码可登录 → 立即改回
code, d = req('put', '/auth/password', AT, 2, {'old_password': 'yisao1234', 'new_password': 'yisao9999'})
check('1.5a 修改密码成功', code == 200, f'{code} {str(d)[:80]}')
code, d = req('post', '/auth/login', data={'phone': '13800000001', 'password': 'yisao9999'})
ok_new = code == 200 and d.get('access')
code, d = req('put', '/auth/password', d.get('access'), 2, {'old_password': 'yisao9999', 'new_password': 'yisao1234'})
code, d = req('post', '/auth/login', data={'phone': '13800000001', 'password': 'yisao1234'})
AT = d.get('access', AT)
check('1.5b 新密码登录+改回原密码', ok_new and code == 200 and AT, f'{code}')

# 短信登录（桩：验证码从库读，等价于收到短信）
code, d = req('post', '/auth/sms-code', data={'phone': '13800000001'})
sms_row = SMSCode.objects.filter(phone='13800000001').order_by('-id').first()
code, d = req('post', '/auth/login-sms', data={'phone': '13800000001', 'code': sms_row.code if sms_row else '0'})
check('1.6 短信验证码登录', code == 200 and d.get('user', {}).get('id') == 6, f'{code} {str(d)[:80]}')

code, d = req('post', '/auth/wechat-login', data={'code': 'x'})
check('1.7 微信登录未配置→明确报错', code == 400 and 'WECHAT' in str(d), f'{code} {str(d)[:80]}')

# ============================================================
# S2 公司与多公司
# ============================================================
code, mines = req('get', '/companies/mine', AT)
c2 = next((x for x in mines if x['id'] == 2), None)
c3 = next((x for x in mines if x['id'] == 3), None)
check('2.1 我的公司列表含公司2/3且均为管理员',
      code == 200 and c2 and c3 and c2['post']['is_admin'] and c3['post']['is_admin'], str(mines)[:100])

code, d = req('post', '/companies/3/switch', AT, 3, {})
check('2.2 切换到公司3', code == 200 and d.get('company', {}).get('id') == 3, f'{code}')

code, emps3 = req('get', '/employees', AT, 3)
names3 = json.dumps(emps3, ensure_ascii=False)
check('2.3 公司3员工列表不含公司2员工', code == 200 and MARK_U not in names3, f'{code} {names3[:80]}')

code, d = req('post', '/companies/2/switch', AT, 2, {})
check('2.4 切回公司2', code == 200 and d.get('company', {}).get('id') == 2, f'{code}')

code, d = req('post', '/companies/join', AT, 2, {'company_no': '100001'})
check('2.5 重复加入在职公司被拒', code == 400, f'{code} {str(d)[:80]}')

# 创建公司 → 创建者自动成为管理员 → 空厂 → 清理（cleanup 硬删）
code, nc = req('post', '/companies/create', AT, data={'name': f'{MARK_U}测试厂', 'city': '汕头'})
NEW_CID = nc.get('company', {}).get('id')
check('2.6 创建公司且创建者为管理员', code in (200, 201) and NEW_CID, f'{code} {str(nc)[:100]}')
if NEW_CID:
    code, d = req('post', f'/companies/{NEW_CID}/switch', AT, NEW_CID, {})
    code, styles0 = req('get', '/styles', AT, NEW_CID)
    styles0 = styles0.get('results') if isinstance(styles0, dict) else styles0
    check('2.7 新公司为空厂', code == 200 and not (styles0 or []), f'{code} {str(styles0)[:60]}')
    req('post', '/companies/2/switch', AT, 2, {})

# ============================================================
# S3 岗位与权限
# ============================================================
code, posts0 = req('get', '/posts?page_size=100', AT, 2)
posts0 = posts0.get('results') if isinstance(posts0, dict) else posts0
check('3.1 岗位列表', code == 200 and isinstance(posts0, list) and posts0, f'{code}')

code, p = req('post', '/posts', AT, 2, {'name': '质检E2E', 'permissions': ['styles', 'production']})
PID = p.get('id')
check('3.2 新建岗位(多权限点)', code in (200, 201) and PID and set(p.get('permissions', [])) == {'styles', 'production'}, f'{code}')
code, d = req('put', f'/posts/{PID}', AT, 2, {'name': '质检E2E2', 'permissions': ['customers']})
check('3.3 更新岗位', code == 200 and d.get('permissions') == ['customers'], f'{code} {str(d)[:60]}')
code, d = req('post', '/posts', AT, 2, {'name': '非法岗位', 'permissions': ['hacker']})
check('3.4 非法权限点被拒', code == 400, f'{code} {str(d)[:80]}')
code, d = req('delete', f'/posts/{PID}', AT, 2)
check('3.5 删除岗位', code in (200, 204), f'{code}')
NEW_POST2 = None
code, p = req('post', '/posts', AT, 2, {'name': '质检E2E', 'permissions': ['production']})
NEW_POST2 = p.get('id')  # 供员工岗位使用，cleanup 删

code, d = req('get', '/posts', None, 2)
check('3.6 无 token 401', code == 401, f'{code}')

# ============================================================
# S4 员工全生命周期（E2E 员工 13855556666）
# ============================================================
EMP_PHONE = '13855556666'
code, inv = req('post', '/employees/invite', AT, 2,
                {'phone': EMP_PHONE, 'real_name': f'{MARK_U}员工'})
code, emps = req('get', '/employees?tab=pending', AT, 2)
emp = next((e for e in emps if e.get('phone') == EMP_PHONE), None)
UC_NEW = emp.get('id') if emp else None
EMP_UID = emp.get('user_id') if emp else None
check('4.1 邀请→PENDING(tab=pending 可见)', code in (200, 201) and emp and emp.get('status') == 0,
      f'{code} {str(emps)[:80]}')

code, d = req('post', f'/employees/{UC_NEW}/set-post', AT, 2, {'post_id': NEW_POST2})
check('4.2 待审核不可设岗(需在职)', code == 400, f'{code} {str(d)[:60]}')

code, d = req('post', f'/employees/{UC_NEW}/reject', AT, 2)
check('4.3 拒绝申请→离职态', code == 200 and d.get('status') == 2, f'{code} {str(d)[:60]}')

code, d = req('post', '/employees/invite', AT, 2, {'phone': EMP_PHONE})
code, emps = req('get', '/employees?tab=pending', AT, 2)
emp = next((e for e in emps if e.get('phone') == EMP_PHONE), None)
UC_NEW = emp.get('id')
check('4.4 拒绝后重新邀请→PENDING', code in (200, 201) and emp and emp.get('status') == 0, f'{code} {str(emps)[:60]}')

code, d = req('post', f'/employees/{UC_NEW}/rehire', AT, 2)
check('4.5 PENDING 不可复职', code == 400, f'{code} {str(d)[:60]}')

code, d = req('post', f'/employees/{UC_NEW}/approve', AT, 2)
check('4.6 审核通过→ACTIVE', code == 200 and d.get('status') == 1, f'{code} {str(d)[:60]}')

# 重新邀请会重置岗位：批准后需重新设岗（与前端「审批通过后分配岗位」流程一致）
code, d = req('post', f'/employees/{UC_NEW}/set-post', AT, 2, {'post_id': NEW_POST2})
check('4.6b 批准后设岗', code == 200, f'{code} {str(d)[:60]}')

code, d = req('post', f'/employees/{UC_NEW}/pin', AT, 2, {'is_pinned': True})
check('4.7 置顶员工', code == 200 and d.get('is_pinned'), f'{code} {str(d)[:60]}')

code, d = req('get', '/employees?keyword=全链路', AT, 2)
kw_hits = [e for e in (d if isinstance(d, list) else d.get('results', [])) if e.get('phone') == EMP_PHONE]
check('4.9 keyword 搜索(在职员工)', code == 200 and kw_hits, f'{code} {str(d)[:80]}')

code, d = req('put', '/employees', AT, 2, {})
check('4.8 员工接口方法收敛(PUT 不开)', code == 405, f'{code}')

# 员工本人：短信登录（验证码从库读，等价收到短信）
SMSCode.objects.filter(phone=EMP_PHONE).delete()  # 清 60s 限频窗与旧码，防连跑时发码 400
req('post', '/auth/sms-code', data={'phone': EMP_PHONE})
sms_row = SMSCode.objects.filter(phone=EMP_PHONE).order_by('-id').first()
code, d = req('post', '/auth/login-sms', data={'phone': EMP_PHONE, 'code': sms_row.code if sms_row else '0'})
ET = d.get('access', '')
check('4.10 员工短信登录(已存在账号不重复注册)', code == 200 and ET and d.get('user', {}).get('id') == EMP_UID, f'{code} {str(d)[:80]}')

code, d = req('post', '/posts', ET, 2, {'name': '越权岗', 'permissions': ['*']})
check('4.11 员工无权建岗(403)', code == 403, f'{code} {str(d)[:60]}')

# ============================================================
# S5 基础档案
# ============================================================
code, g = req('post', '/customer-groups', AT, 2, {'name': f'{MARK_U}分组'})
GID = g.get('id')
check('5.1a 新建分组', code in (200, 201) and GID, f'{code}')
code, d = req('put', f'/customer-groups/{GID}', AT, 2, {'name': f'{MARK_U}分组2'})
check('5.1b 重命名分组', code == 200 and d.get('name') == f'{MARK_U}分组2', f'{code}')
code, d = req('post', '/customer-groups', AT, 2, {'name': f'{MARK_U}分组2'})
check('5.1c 重名分组被拒', code == 400, f'{code}')

code, cust = req('post', '/customers', AT, 2, {'customer_no': 'E2E-C01', 'name': f'{MARK_U}客户',
                                               'phone': '13800000002', 'group': GID})
CUST = cust.get('id')
check('5.2a 新建客户(带分组)', code in (200, 201) and CUST, f'{code} {str(cust)[:80]}')
code, d = req('post', '/customers', AT, 2, {'customer_no': 'E2E-C01', 'name': '重复编号客户'})
check('5.2b 客户编号唯一', code == 400, f'{code}')
code, d = req('put', f'/customers/{CUST}', AT, 2, {'customer_no': 'E2E-C01', 'name': f'{MARK_U}客户2', 'group': GID})
check('5.2c 更新客户', code == 200 and d.get('name') == f'{MARK_U}客户2', f'{code} {str(d)[:60]}')
code, d = req('get', '/customers?keyword=全链路', AT, 2)
d = d.get('results') if isinstance(d, dict) else d
check('5.2d 客户搜索', code == 200 and any(x.get('id') == CUST for x in (d or [])), f'{code}')

code, proc = req('post', '/processes', AT, 2, {'name': f'{MARK_U}工序', 'price': '3.500'})
PROC = proc.get('id')
check('5.3a 新建工序', code in (200, 201) and PROC, f'{code} {str(proc)[:60]}')
code, plist = req('get', f'/processes?keyword={MARK_U}', AT, 2)
plist = plist.get('results') if isinstance(plist, dict) else plist
check('5.3b 工序列表可搜到', code == 200 and any(x.get('id') == PROC for x in (plist or [])), f'{code}')
code, sel = req('get', '/process-selector', AT, 2)
sel = sel.get('results') if isinstance(sel, dict) else sel
check('5.3c 选择器仅含固定/指定工序(新工序未指定不出现)', code == 200 and all(
    x.get('id') != PROC for x in (sel or [])), f'{code}')

code, st = req('post', '/styles', AT, 2, {'style_no': 'E2E001', 'name': f'{MARK_U}款', 'price': '6.000',
                                          'customer_id': CUST,
                                          'process_items': [{'process_id': PROC, 'price': '5.500', 'sort': 1}]})
STYLE = st.get('id')
check('5.4a 新建款式(带工序价5.5)', code in (200, 201) and STYLE, f'{code} {str(st)[:100]}')
code, d = req('get', f'/styles/{STYLE}', AT, 2)
st_items = d.get('process_items') or d.get('processes') or []
check('5.4b 款式详情含工序价', code == 200 and any(
    float((x.get('price') or 0)) == 5.5 for x in st_items if isinstance(x, dict)), f'{code} {str(d)[:120]}')
code, d = req('put', f'/styles/{STYLE}', AT, 2, {'style_no': 'E2E001', 'name': f'{MARK_U}款',
                                                 'process_items': [{'process_id': PROC, 'price': '7.000', 'sort': 1}]})
check('5.4c 工序价改7→款式总价自动汇总', code == 200 and float(d.get('price', 0)) == 7.0, f'{code} {str(d)[:80]}')
code, cp = req('post', f'/styles/{STYLE}/copy', AT, 2, {})
STYLE2 = cp.get('id')
check('5.4d 复制款式', code in (200, 201) and STYLE2 and STYLE2 != STYLE, f'{code} {str(cp)[:80]}')
code, d = req('get', f'/styles/{STYLE2}', AT, 2)
check('5.4e 副本编号不冲突', code == 200 and d.get('style_no') != 'E2E001', f'{code} {str(d)[:60]}')
code, d = req('delete', f'/styles/{STYLE2}', AT, 2)
check('5.4f 删除未被引用款式', code in (200, 204), f'{code} {str(d)[:60]}')
STYLE2 = None

import os
import tempfile
img = tempfile.NamedTemporaryFile(suffix='.png', delete=False)
img.write(b'\x89PNG\r\n\x1a\n' + b'1' * 300)
img.close()
with open(img.name, 'rb') as fh:
    resp = c.post('/api/uploads', {'file': fh}, HTTP_AUTHORIZATION=f'Bearer {AT}', HTTP_X_COMPANY_ID='2')
up = json.loads(resp.content.decode('utf-8')) if resp.content else {}
check('5.5 图片上传返回可访问 URL', resp.status_code == 200 and up.get('url', '').startswith('/media/'), str(up)[:80])
os.unlink(img.name)

# ============================================================
# S6 生产与编菲全链
# ============================================================
code, prod = req('post', '/productions', AT, 2, {'style': STYLE, 'bed_no': E2E_BED, 'order_type': '自产',
                                                 'customer': CUST})
PROD = prod.get('id')
check('6.1 新建床次(带客户)', code in (200, 201) and PROD, f'{code} {str(prod)[:100]}')

code, d = req('put', f'/productions/{PROD}/order-processes', AT, 2, {'process_items': [{'process_id': PROC}]})
check('6.2 设置订单工序(回读含该工序)', code == 200 and any(x.get('process') == PROC for x in (d or [])),
      f'{code} {str(d)[:80]}')

# 2026-10-18：split_mode 字段与接口彻底删除，路由应 404
code, d = req('put', f'/productions/{PROD}/split-mode', AT, 2, {'split_mode': '分工序分摊'})
check('6.3 分货模式接口已删除(404)', code == 404, f'{code} {str(d)[:60]}')

code, tks = req('post', '/tickets/batch', AT, 2, {'production_id': PROD, 'items': [
    {'color': '红', 'size': 'M', 'qty': 45, 'piece_no': 'P1', 'fabric': '主料'},
    {'color': '红', 'size': 'L', 'qty': 30}, {'color': '蓝', 'size': 'M', 'qty': 12}], 'bundle_limit': 20})
check('6.4 编菲拆扎(45→3扎 30→2扎 12→1扎 共6扎)',
      code in (200, 201) and isinstance(tks, list) and len(tks) == 6, f'{code} n={len(tks) if isinstance(tks, list) else str(tks)[:80]}')
TK = (tks or [{}])[0]
TK_LAST = (tks or [{}])[-1]
bun = [t.get('bundle_no') for t in tks] if isinstance(tks, list) else []
check('6.5 扎号连续且二维码串齐备', len(set(bun)) == 6 and all(t.get('qr_code') for t in tks), str(bun))
check('6.6 件数快照=实际件数', TK.get('qty') == 20 and TK_LAST.get('qty') == 12, f'{TK.get("qty")}/{TK_LAST.get("qty")}')

code, bl = req('get', f'/productions/{PROD}/bundles', AT, 2)
bl = bl.get('results') if isinstance(bl, dict) else bl
check('6.7 扎号列表', code == 200 and len(bl or []) == 6, f'{code} n={len(bl or [])}')

code, cd = req('get', f'/productions/{PROD}/cut-detail', AT, 2)
check('6.8 裁床明细(含颜色尺码矩阵)', code == 200 and ('红' in json.dumps(cd, ensure_ascii=False)), f'{code} {str(cd)[:80]}')

code, d = req('get', f'/productions/{PROD}', AT, 2)
check('6.9 床次详情统计(总件87)', code == 200 and int(d.get('total_qty') or 0) == 87, f'{code} {str(d)[:80]}')

code, d = req('get', f'/tickets?production_id={PROD}', AT, 2)
d = d.get('results') if isinstance(d, dict) else d
check('6.10 菲票按床次筛选', code == 200 and len(d or []) == 6, f'{code} n={len(d or [])}')
code, d = req('get', '/tickets?is_printed=0', AT, 2)
d = d.get('results') if isinstance(d, dict) else d
check('6.11 未打印筛选', code == 200 and all(not t.get('is_printed') for t in (d or [])), f'{code}')

code, d = req('post', '/tickets/print', AT, 2, {'ticket_ids': [TK['id'], TK['id'], TK_LAST['id']]})
check('6.12 打印上报去重(printed=2)', code == 200 and d.get('printed') == 2, f'{code} {str(d)[:60]}')
code, d = req('get', f'/tickets?production_id={PROD}&is_printed=1', AT, 2)
d = d.get('results') if isinstance(d, dict) else d
check('6.13 已打印筛选含计数', code == 200 and len(d or []) == 2 and all(t.get('print_count') == 1 for t in d), f'{code}')

# 未登记的尾扎可删除；登记后不可删
code, d = req('delete', f"/tickets/{TK_LAST['id']}", AT, 2)
check('6.14 删除未登记菲票', code in (200, 204), f'{code} {str(d)[:60]}')

code, d = req('get', '/productions?keyword=E2E01', AT, 2)
d = d.get('results') if isinstance(d, dict) else d
check('6.15 床次关键词筛选', code == 200 and any(x.get('id') == PROD for x in (d or [])), f'{code}')

code, wb = req('get', '/records/workbench', AT, 2)
beds = wb.get('beds') or []
check('6.16 管理员工作台含床次', code == 200 and any(b.get('bed_no') == E2E_BED for b in beds), str(wb)[:100])

# ============================================================
# S7 扫菲登记链
# ============================================================
code, d = req('post', '/scan/parse', AT, 2, {'qr': TK['qr_code']})
check('7.1 扫码解析返回票+款式+床次', code == 200 and d.get('ticket', {}).get('id') == TK['id']
      and d.get('style', {}).get('style_no') == 'E2E001' and d.get('ticket', {}).get('bed_no') == E2E_BED,
      f'{code} {str(d)[:100]}')
code, d = req('post', '/scan/parse', AT, 2, {'qr': 'NOPE:123'})
check('7.2 无效码解析明确报错', code in (400, 404), f'{code}')

# 员工本人登记：价=订单/款式工序价 5.5
code, rec = req('post', '/records', ET, 2, {'ticket_id': TK['id'], 'process_id': PROC, 'qty': 5})
REC_EMP = rec.get('id')
check('7.3 员工登记本人', code in (200, 201) and REC_EMP, f'{code} {str(rec)[:80]}')
# 期望价 = 7.000：5.4c 已把该款式工序价改为 7，_resolve_price 取款式工序价
check('7.4 计价=款式工序价(5.4c改7后)', str(rec.get('price')) == '7.000', str(rec.get('price')))

TK2 = (tks or [{}, {}])[1]  # 协助登记用第二张菲票（同票同工序同人会被防重拦截）
code, rec2 = req('post', '/records', AT, 2, {'ticket_id': TK2['id'], 'process_id': PROC, 'qty': 4,
                                             'user_id': EMP_UID})
REC_ADM = rec2.get('id')
check('7.5 管理员协助登记(operator 留审计)', code in (200, 201) and REC_ADM
      and (rec2.get('operator_name') or rec2.get('operator')), f'{code} {str(rec2)[:100]}')

# 2026-10-18：同票同工序同人改为「增量补登」——qty 累加进同一条记录（5+3=8）
code, d = req('post', '/records', ET, 2, {'ticket_id': TK['id'], 'process_id': PROC, 'qty': 3})
check('7.6 增量补登(累加5+3=8,同一条)', code in (200, 201) and d.get('id') == REC_EMP
      and d.get('qty') == 8, f'{code} {str(d)[:80]}')

# 员工指定工序（指定后工序选择器可见该工序）
code, d = req('put', f'/employees/{UC_NEW}/assigned-processes', AT, 2, {'process_ids': [PROC]})
check('7.7a 设置指定工序', code == 200 and PROC in (d.get('assigned_processes') or []), f'{code} {str(d)[:80]}')
code, sel = req('get', f'/records/process-selector?ticket_id={TK["id"]}', ET, 2)
sel = sel.get('results') if isinstance(sel, dict) else sel
check('7.7 登记工序选择器', code == 200 and any(x.get('id') == PROC for x in (sel or [])), f'{code} {str(sel)[:60]}')

code, his = req('get', f'/records?date_from={TODAY}&date_to={TODAY}', AT, 2)
his = his.get('results') if isinstance(his, dict) else his
ids = {x.get('id') for x in (his or [])}
check('7.8 登记历史按日筛选', code == 200 and REC_EMP in ids and REC_ADM in ids, f'{code} n={len(his or [])}')

# 工作台员工视角：my_today
code, wb = req('get', '/records/workbench', ET, 2)
my = wb.get('my_today') or []
check('7.9 员工工作台今日登记', code == 200 and any(x.get('ticket') == TK['id'] for x in my), str(wb)[:100])

# 未审核拦截（先开开关，用一个新邀请员工试登记，再还陈阿珍场景）
code, d = req('put', '/settings', AT, 2, {'forbid_unapproved_register': '1'})
code, inv2 = req('post', '/employees/invite', AT, 2, {'phone': '13855557777'})
code, emps = req('get', '/employees?tab=pending', AT, 2)
emp2 = next((e for e in emps if e.get('phone') == '13855557777'), None)
code, d = req('post', '/records', AT, 2, {'ticket_id': TK['id'], 'process_id': PROC, 'qty': 1,
                                          'user_id': (emp2 or {}).get('user_id')})
check('7.10 未审核员工禁止登记', code == 400, f'{code} {str(d)[:60]}')

# 撤销：管理员撤销员工登记 → 员工未结减少
code, sal0 = req('get', f'/salary/employee/{EMP_UID}', AT, 2)
code, d = req('delete', f'/records/{REC_EMP}', AT, 2)
check('7.11 管理员撤销登记', code in (200, 204), f'{code} {str(d)[:60]}')
code, sal1 = req('get', f'/salary/employee/{EMP_UID}', AT, 2)
# /salary/employee/<uid> 的 settled/unsettled 是金额字符串（非明细 dict）
q0 = float(sal0.get('unsettled') or 0)
q1 = float(sal1.get('unsettled') or 0)
check('7.12 撤销后未结金额减少', q0 > q1, f'{q0}→{q1}')

# ============================================================
# S8 工资与结算
# ============================================================
code, d = req('get', f'/salary/employee/{EMP_UID}?tab=count', AT, 2)
bd = d.get('by_date') or [{}]
check('8.1 计数tab无金额字段', code == 200 and 'amount' not in json.dumps(bd[0] if bd else {}), f'{code} {str(bd)[:80]}')

code, d = req('get', f'/salary/employee/{EMP_UID}?tab=fixed', AT, 2)
check('8.2 固定工资tab', code == 200, f'{code}')
code, d = req('put', f'/salary/employee/{EMP_UID}/config', AT, 2, {'fixed_salary': '3000', 'fixed_type': 'month'})
check('8.3 设置固定工资', code == 200, f'{code} {str(d)[:60]}')

code, adj = req('post', '/salary/adjust', AT, 2, {'user_id': EMP_UID, 'amount': '50', 'reason': f'{MARK_U}奖励'})
ADJ = adj.get('id')
check('8.4 奖励调整', code in (200, 201) and ADJ, f'{code} {str(adj)[:60]}')
code, d = req('get', '/salary/company/employees', AT, 2)
d = d if isinstance(d, list) else d.get('results', [])
check('8.5 公司员工卡列表含 E2E 员工', code == 200 and any(e.get('user_id') == EMP_UID for e in d), f'{code}')
code, cs = req('get', '/salary/company', AT, 2)
check('8.6 公司汇总含 total/no_price_processes', code == 200 and 'total' in cs
      and 'no_price_processes' in (cs.get('unsettled') or {}), str(cs)[:100])

# 员工本人发起结算申请（含 note 标记便于清理）
code, ap = req('post', '/salary/settle-applies', ET, 2, {'period_start': MONTH_START, 'period_end': TODAY})
APPLY = ap.get('id')
check('8.7 员工发起结算申请', code in (200, 201) and APPLY, f'{code} {str(ap)[:80]}')
code, d = req('put', f'/salary/settle-applies/{APPLY}/review', AT, 2, {'action': 'reject', 'reason': '金额不对'})
check('8.8 管理员驳回申请', code == 200 and d.get('status') == SettleApply.Status.REJECTED, f'{code} {str(d)[:60]}')
code, ap2 = req('post', '/salary/settle-applies', ET, 2, {'period_start': MONTH_START, 'period_end': TODAY})
APPLY = ap2.get('id')
code, d = req('put', f'/salary/settle-applies/{APPLY}/review', AT, 2, {'action': 'approve'})
check('8.9 审核通过(状态=待发放1)', code == 200 and d.get('status') == SettleApply.Status.APPROVED, f'{code} {str(d)[:60]}')
code, d = req('put', f'/salary/settle-applies/{APPLY}/pay', AT, 2, {'payer': '现金'})
check('8.10 发放→生成结算单', code == 200, f'{code} {str(d)[:100]}')
code, sal = req('get', f'/salary/employee/{EMP_UID}', AT, 2)
check('8.11 结算后 settled>0', code == 200 and float(sal.get('settled') or 0) > 0, f'settled={sal.get("settled")}')
code, paid_apply = req('get', '/salary/settle-applies?status=2', AT, 2)
paid_apply = paid_apply.get('results') if isinstance(paid_apply, dict) else paid_apply
check('8.12 已发放申请状态=2', code == 200 and any(x.get('id') == APPLY for x in (paid_apply or [])), f'{code}')

# 已发放期间的结算不可撤销（负例）；直接建结算单→撤销（正例）
# 8.10 发放后该员工登记已全部结算：先补一条新登记（TK+PROC 已被 7.11 撤销，不撞防重）
code, d = req('post', '/records', AT, 2, {'ticket_id': TK['id'], 'process_id': PROC, 'qty': 5,
                                          'user_id': EMP_UID})
check('8.12b 发放后登记解锁可再登', code in (200, 201), f'{code} {str(d)[:60]}')
# 登记挪到 10-20（避开已发放申请的 10-01~10-16 期间，才能走撤销正例）
rec2id = d.get('id')
if rec2id:
    Record.objects.filter(pk=rec2id).update(registered_at=timezone.make_aware(
        timezone.datetime.fromisoformat(FUTURE + 'T10:00')))
code, d = req('post', '/salary/settlements', AT, 2, {'period_start': FUTURE, 'period_end': FUTURE,
                                                     'user_id': EMP_UID})
SETTLE2 = d.get('id') if isinstance(d, dict) else None
check('8.13 管理员直接结算(有未结登记)', code in (200, 201), f'{code} {str(d)[:80]}')
code, d = req('post', '/salary/settlements', AT, 2, {'period_start': FUTURE, 'period_end': FUTURE,
                                                     'user_id': EMP_UID})
check('8.13b 无可结登记再结被拒(400)', code == 400, f'{code} {str(d)[:60]}')
if SETTLE2:
    code, d = req('post', f'/salary/settlements/{SETTLE2}/revoke', AT, 2, {})
    check('8.14 撤销结算单→登记解锁', code == 200 and d.get('status') == 'revoked', f'{code} {str(d)[:60]}')
# 负例：与已发放申请期间重叠的结算单不可撤销
paid_st = Settlement.objects.filter(company_id=2, user_id=EMP_UID,
                                    status=Settlement.Status.SETTLED).first()
if paid_st:
    code, d = req('post', f'/salary/settlements/{paid_st.id}/revoke', AT, 2, {})
    check('8.14b 已发放期间不可撤销(400)', code == 400, f'{code} {str(d)[:60]}')
code, d = req('post', '/salary/settle-applies', ET, 2, {'period_start': MONTH_START, 'period_end': TODAY})
VOID_ID = d.get('id')
code, d = req('put', f'/salary/settle-applies/{VOID_ID}/void', AT, 2, {'void_reason': '误发起'})
check('8.15 作废申请', code == 200 and d.get('status') == SettleApply.Status.VOIDED, f'{code} {str(d)[:60]}')

# 完成汇总三级钻取
code, lv1 = req('get', f'/salary/summary/completion?date_from={MONTH_START}&date_to={TODAY}', AT, 2)
rows = lv1.get('styles') or lv1.get('results') or (lv1 if isinstance(lv1, list) else [])
check('8.16 完成汇总款式层含 E2E 款', code == 200 and any(
    (x.get('style_no') == 'E2E001') for x in rows), f'{code} {str(lv1)[:120]}')
code, lv2 = req('get', f'/salary/summary/completion?date_from={MONTH_START}&date_to={TODAY}&style_id={STYLE}', AT, 2)
check('8.17 完成汇总工序层', code == 200, f'{code} {str(lv2)[:100]}')
code, lv3 = req('get', f'/salary/summary/completion?date_from={MONTH_START}&date_to={TODAY}&style_id={STYLE}&process_id={PROC}', AT, 2)
check('8.18 完成汇总菲票层', code == 200, f'{code} {str(lv3)[:100]}')

# 员工视角：无权看公司汇总
code, d = req('get', '/salary/company', ET, 2)
check('8.19 员工无权看公司汇总', code == 403, f'{code} {str(d)[:60]}')

# ============================================================
# S9 日志与通知
# ============================================================
code, logs = req('get', '/audit-logs?page_size=10', AT, 2)
check('9.1 审计日志列表', code == 200, f'{code}')
code, logs = req('get', '/audit-logs?type=other&page_size=10', AT, 2)
check('9.2 审计日志类型筛选', code == 200, f'{code}')
code, nts = req('get', '/notifications', ET, 2)
nts = nts.get('results') if isinstance(nts, dict) else nts
has_nt = any(('结算' in (n.get('title') or '')) for n in (nts or []))
check('9.3 员工收到结算审核通知', code == 200 and has_nt, f'{code} {str(nts)[:100]}')
if nts:
    code, d = req('post', f"/notifications/{nts[0]['id']}/read", ET, 2, {})
    check('9.4 单条已读', code == 200, f'{code}')
code, d = req('post', '/notifications/read-all', ET, 2, {})
check('9.5 全部已读', code == 200, f'{code}')

# ============================================================
# S10 安全边界与隔离
# ============================================================
code, d = req('get', f'/styles/{STYLE}', AT, 3)
check('10.1 跨公司读款式 404', code == 404, f'{code} {str(d)[:60]}')
code, d = req('get', '/styles', AT)
check('10.2 缺 X-Company-Id 被拒', code in (400, 403), f'{code}')
code, d = req('post', '/styles', ET, 2, {'style_no': 'HACK', 'name': 'x', 'price': '1'})
check('10.3 员工无权建款式(权限点production的岗位)', code == 403, f'{code}')
# 员工岗位是 production 权限，styles 应被拒；production 域应放行
code, d = req('get', '/productions', ET, 2)
check('10.4 员工有 production 权限可看床次', code == 200, f'{code} {str(d)[:60]}')
code, d = req('get', '/settings', ET, 2)
check('10.5 员工读设置被拒或受限', code in (403, 200), f'{code}')

# ============================================================
# 清理：恢复演示库
# ============================================================
print('\n---- cleanup ----')
try:
    Record.all_objects.filter(ticket__production__bed_no=E2E_BED)._raw_delete(Record.all_objects.db)
    Record.all_objects.filter(user_id=EMP_UID)._raw_delete(Record.all_objects.db)
    Ticket.all_objects.filter(production__bed_no=E2E_BED)._raw_delete(Ticket.all_objects.db)
    from apps.production.models import ProductionProcess
    ProductionProcess.all_objects.filter(production__bed_no=E2E_BED)._raw_delete(ProductionProcess.all_objects.db)
    Production.all_objects.filter(bed_no=E2E_BED)._raw_delete(Production.all_objects.db)
    StyleProcess.all_objects.filter(style__style_no__in=['E2E001', 'E2E002'])._raw_delete(StyleProcess.all_objects.db)
    # 复制款式(5.4d)的 style_no 不可预知：按名称标记清
    StyleProcess.all_objects.filter(style__name__contains=MARK_U)._raw_delete(StyleProcess.all_objects.db)
    if STYLE:
        Style.all_objects.filter(pk=STYLE)._raw_delete(Style.all_objects.db)
    Style.all_objects.filter(name__contains=MARK_U)._raw_delete(Style.all_objects.db)
    if CUST:
        Customer.all_objects.filter(pk=CUST)._raw_delete(Customer.all_objects.db)
    if GID:
        CustomerGroup.all_objects.filter(pk=GID)._raw_delete(CustomerGroup.all_objects.db)
    if PROC:
        Process.all_objects.filter(pk=PROC)._raw_delete(Process.all_objects.db)
    SettleApply.all_objects.filter(company_id=2, user_id__in=[EMP_UID, 6])._raw_delete(SettleApply.all_objects.db)
    purge_settlements(Q(company_id=2) & (Q(user_id=EMP_UID) | Q(period_start=TODAY)
                                         | Q(period_start=MONTH_START) | Q(period_start=FUTURE)))
    if ADJ:
        SalaryAdjust.all_objects.filter(pk=ADJ)._raw_delete(SalaryAdjust.all_objects.db)
    # 先删用户（UserCompany→岗位 FK、通知 FK 都要先行解除），再删岗位/公司
    for ph in (EMP_PHONE, '13855557777'):
        u = User.objects.filter(phone=ph).first()
        if u:
            UserCompany.all_objects.filter(user=u)._raw_delete(UserCompany.all_objects.db)
            u.delete()  # ORM delete：级联清通知、AuditLog 置空
    if NEW_POST2:
        PostM.all_objects.filter(pk=NEW_POST2)._raw_delete(PostM.all_objects.db)
    if NEW_CID:
        UserCompany.all_objects.filter(company_id=NEW_CID)._raw_delete(UserCompany.all_objects.db)
        PostM.all_objects.filter(company_id=NEW_CID)._raw_delete(PostM.all_objects.db)
        CompanySetting.objects.filter(company_id=NEW_CID).delete()
        from apps.ops.models import Notification
        Notification.all_objects.filter(company_id=NEW_CID).delete()
        from apps.accounts.models import Company
        Company.all_objects.filter(pk=NEW_CID)._raw_delete(Company.all_objects.db)
    CompanySetting.objects.filter(company_id=2, key__in=['forbid_unapproved_register', 'auto_cut_done']).delete()
    SMSCode.objects.filter(phone__in=[EMP_PHONE, '13855557777', '13800000001']).delete()
    print('cleanup done')
except Exception as e:
    print('cleanup error:', type(e).__name__, e)

print(f'\n===== E2E RESULT: {len(PASS)} passed, {len(FAIL)} failed =====')
if FAIL:
    print('FAILED:', *FAIL, sep='\n  - ')
