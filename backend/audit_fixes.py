# -*- coding: utf-8 -*-
"""审计修复回归（2026-10-18 拍板项）——覆盖 business-flow-audit.md 问题清单修复：

① 裁完语义修复：is_cut_done 不再拦截登记/编菲加扎；auto_cut_done=「编菲后自动置裁完」，
   建床不再置裁完
② 工序集校验（不在床次工序集 400）+ 跳序拦截（StyleProcess.allow_skip=False）
③ 登记累计封顶：同扎同工序跨员工累计 ≤ 扎数
④ 增量补登：同扎同工序同人再次登记累加进同一条记录；撤销=整条扣减
⑤ 离职强制结清（未结清一律 400，删 need_confirm/force）；离职后薪酬仍可查
⑥ 协助扫菲硬拦截：目标员工非本公司在职成员 → 400
⑦ 床次软删级联菲票（删除床次时其菲票同事务软删）
⑧ plan_qty 计划裁货数录入/展示

跑法：manage.py shell -c "exec(compile(open('audit_fixes.py',encoding='utf-8').read(),'audit_fixes.py','exec'))"
幂等：自带 preclean（按 AF 标记清理），可连续重复运行。
"""
import json
from datetime import date

from django.db.models.deletion import ProtectedError
from django.test import Client

from apps.accounts.models import Post as PostM, User, UserCompany
from apps.catalog.models import Process, Style, StyleProcess, Ticket
from apps.ops.models import AuditLog, CompanySetting
from apps.production.models import Production, ProductionProcess
from apps.records.models import Record
from apps.salary.models import Settlement

PASS, FAIL = [], []
CID = 2
BED1, BED2, BED4 = 'AF-B1', 'AF-B2', 'AF-B4'
SNO = 'AF001'
P_JIA, P_YI, P_BING, P_OUT = 'AF工序甲', 'AF工序乙', 'AF工序丙', 'AF工序外'
EMP_PHONE, OUT_PHONE = '13977710001', '13977710002'


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    print(('PASS' if cond else 'FAIL'), name, ('' if cond else f'| {detail}'))


def req(method, path, token=None, data=None, cid=CID):
    extra = {}
    if token:
        extra['HTTP_AUTHORIZATION'] = f'Bearer {token}'
    if cid:
        extra['HTTP_X_COMPANY_ID'] = str(cid)
    resp = getattr(Client(), method)(f'/api{path}', data=data,
                                     content_type='application/json', **extra)
    try:
        return resp.status_code, json.loads(resp.content.decode('utf-8'))
    except Exception:
        return resp.status_code, {}


# ================= preclean（幂等） =================
Record.all_objects.filter(ticket__production__bed_no__in=[BED1, BED2, BED4]) \
    .update(settlement=None)
Record.all_objects.filter(ticket__production__bed_no__in=[BED1, BED2, BED4])._raw_delete(
    Record.all_objects.db)
Settlement.all_objects.filter(company_id=CID, user__phone=EMP_PHONE)._raw_delete(
    Settlement.all_objects.db)
Ticket.all_objects.filter(production__bed_no__in=[BED1, BED2, BED4])._raw_delete(
    Ticket.all_objects.db)
ProductionProcess.all_objects.filter(production__bed_no__in=[BED1, BED2, BED4])._raw_delete(
    ProductionProcess.all_objects.db)
Production.all_objects.filter(bed_no__in=[BED1, BED2, BED4])._raw_delete(Production.all_objects.db)
StyleProcess.all_objects.filter(style__style_no=SNO)._raw_delete(StyleProcess.all_objects.db)
Style.all_objects.filter(style_no=SNO)._raw_delete(Style.all_objects.db)
Process.all_objects.filter(company_id=CID, name__in=[P_JIA, P_YI, P_BING, P_OUT])._raw_delete(
    Process.all_objects.db)
for ph in (EMP_PHONE, OUT_PHONE):
    u = User.objects.filter(phone=ph).first()
    if u:
        try:
            u.delete()
        except ProtectedError:
            pass  # 并行测试把该号码用作了 Company.creator（PROTECT），跳过删除不阻塞
# F6a 前提保障：OUT 用户在公司内不得有任何成员关系（含待审），防止跨套件残留破坏「非本公司」语义
UserCompany.all_objects.filter(user__phone=OUT_PHONE, company_id=CID)._raw_delete(
    UserCompany.all_objects.db)
CompanySetting.objects.filter(company_id=CID, key='auto_cut_done').delete()
# F6a 前提保障：恢复 forbid_unapproved_register 默认关（e2e_fullchain 开启后 cleanup 失败会残留，
# 该开关开启时「未审核」校验先于「协助硬拦截」抛出，破坏 ⑥ 的预期文案）
CompanySetting.objects.filter(company_id=CID, key='forbid_unapproved_register').delete()

# ================= 数据准备 =================
AT = req('post', '/auth/login', data={'phone': '13800000001', 'password': 'yisao1234'})[1].get('access')
assert AT, '管理员登录失败'

code, p_jia = req('post', '/processes', AT, {'name': P_JIA, 'default_price': '0.5'})
code, p_yi = req('post', '/processes', AT, {'name': P_YI, 'default_price': '0.6'})
code, p_bing = req('post', '/processes', AT, {'name': P_BING, 'default_price': '0.7'})
code, p_out = req('post', '/processes', AT, {'name': P_OUT, 'default_price': '0.9'})
JIA, YI, BING, OUT = p_jia['id'], p_yi['id'], p_bing['id'], p_out['id']

# 款式：甲(sort1,禁跳) 乙(sort2,可跳) 丙(sort3,禁跳)
code, style = req('post', '/styles', AT, {
    'style_no': SNO, 'name': '审计修复款式',
    'process_items': [
        {'process_id': JIA, 'price': '0.5', 'sort': 1, 'allow_skip': False},
        {'process_id': YI, 'price': '0.6', 'sort': 2, 'allow_skip': True},
        {'process_id': BING, 'price': '0.7', 'sort': 3, 'allow_skip': False},
    ]})
check('P0 款式+allow_skip 配置落库', code in (200, 201)
      and {(x['process'], x['allow_skip']) for x in style.get('processes', [])}
      == {(JIA, False), (YI, True), (BING, False)},
      f'{code} {str(style)[:120]}')

# 床次1：plan_qty=66（⑧），订单工序=甲乙丙；床次4：⑦软删级联用
code, prod = req('post', '/productions', AT, {'style': style['id'], 'bed_no': BED1,
                                              'plan_qty': 66, 'total_qty': 0})
PROD1 = prod.get('id')
check('P0 新建床次(plan_qty=66,建床不置裁完)', code in (200, 201) and PROD1
      and prod.get('plan_qty') == 66 and prod.get('is_cut_done') is False,
      f'{code} {str(prod)[:120]}')
code, ops_ = req('put', f'/productions/{PROD1}/order-processes', AT,
                 {'process_items': [{'process_id': JIA, 'price': '0.5'},
                                    {'process_id': YI, 'price': '0.6'},
                                    {'process_id': BING, 'price': '0.7'}]})
check('P0 配置订单工序', code == 200 and len(ops_) == 3, f'{code} {str(ops_)[:80]}')

code, tks = req('post', '/tickets/batch', AT,
                {'production_id': PROD1, 'items': [{'color': '红', 'size': 'M', 'qty': 10}]})
TK = (tks or [{}])[0]
check('P0 编菲1扎10件', code in (200, 201) and TK.get('qty') == 10, f'{code} {str(tks)[:80]}')

# 员工（在职，供协助登记/离职用）与厂外用户（⑥）
emp = User.objects.create(phone=EMP_PHONE, username=EMP_PHONE, real_name='审计修复员工',
                          password='yisao1234')
post = PostM.objects.filter(company_id=CID, is_admin=False, is_deleted=False).first()
UserCompany.objects.create(user=emp, company_id=CID, post=post,
                           status=UserCompany.Status.ACTIVE)
outsider = User.objects.create(phone=OUT_PHONE, username=OUT_PHONE, real_name='厂外用户',
                               password='yisao1234')

# ================= ① 裁完语义 =================
code, d = req('put', f'/productions/{PROD1}/cut-done', AT, {'is_cut_done': True})
check('F1a 标记裁完', code == 200 and d.get('is_cut_done') is True, f'{code} {str(d)[:60]}')
code, d = req('post', '/records', AT, {'ticket_id': TK['id'], 'process_id': YI, 'qty': 1})
check('F1b 裁完后登记放行(不再拦截)', code in (200, 201), f'{code} {str(d)[:80]}')
REC_YI = d.get('id')
code, d = req('post', '/tickets/batch', AT,
              {'production_id': PROD1, 'items': [{'color': '蓝', 'size': 'L', 'qty': 4}]})
check('F1c 裁完后编菲加扎放行(不再拦截)', code in (200, 201), f'{code} {str(d)[:80]}')
code, d = req('put', f'/productions/{PROD1}/cut-done', AT, {'is_cut_done': False})
check('F1d 取消裁完', code == 200 and d.get('is_cut_done') is False, f'{code}')

# auto_cut_done 新语义：编菲后自动置裁完；建床不再置
req('put', '/settings', AT, {'auto_cut_done': '1'})
code, prod2 = req('post', '/productions', AT, {'style': style['id'], 'bed_no': BED2})
check('F1e 开关开启时建床仍不置裁完', code in (200, 201)
      and (prod2 or {}).get('is_cut_done') is False, f'{code} {str(prod2)[:80]}')
code, d = req('post', '/tickets/batch', AT,
              {'production_id': prod2['id'], 'items': [{'color': '红', 'size': 'M', 'qty': 5}]})
code, d2 = req('get', f"/productions/{prod2['id']}", AT)
check('F1f 编菲后自动置裁完(新语义)', code in (200, 201)
      and d2.get('is_cut_done') is True, f'{code} {str(d2)[:80]}')
req('put', '/settings', AT, {'auto_cut_done': '0'})

# ================= ② 工序集校验 + 跳序拦截 =================
code, d = req('post', '/records', AT, {'ticket_id': TK['id'], 'process_id': OUT, 'qty': 1})
check('F2a 未配置工序登记被拒(400)', code == 400 and '工序配置' in str(d), f'{code} {str(d)[:80]}')
code, d = req('post', '/records', AT, {'ticket_id': TK['id'], 'process_id': BING, 'qty': 1})
check('F2b 跳过甲直接登丙被拒(allow_skip=False)', code == 400 and '需先完成' in str(d),
      f'{code} {str(d)[:80]}')
code, d = req('post', '/records', AT, {'ticket_id': TK['id'], 'process_id': JIA, 'qty': 4})
REC_JIA = d.get('id')
check('F2c 按序登记甲(201)', code in (200, 201), f'{code} {str(d)[:80]}')
code, d = req('post', '/records', AT, {'ticket_id': TK['id'], 'process_id': BING, 'qty': 2})
check('F2d 甲完成后丙放行', code in (200, 201), f'{code} {str(d)[:80]}')

# ================= ③ 累计封顶 + ④ 增量补登/撤销扣减 =================
# 此前甲已登记 4 件（F2c），本块从 4 起步，扎数 10
code, d = req('post', '/records', AT, {'ticket_id': TK['id'], 'process_id': JIA, 'qty': 4})
check('F3a 增量补登累加(4+4=8同一条)', code in (200, 201) and d.get('id') == REC_JIA
      and d.get('qty') == 8, f'{code} {str(d)[:80]}')
code, d = req('post', '/records', AT, {'ticket_id': TK['id'], 'process_id': JIA, 'qty': 3})
check('F3b 超扎数被拒(8+3>10)', code == 400 and '超限' in str(d), f'{code} {str(d)[:80]}')
code, d = req('post', '/records', AT, {'ticket_id': TK['id'], 'process_id': JIA, 'qty': 2})
check('F3c 补到恰满(8+2=10)', code in (200, 201) and d.get('id') == REC_JIA
      and d.get('qty') == 10, f'{code} {str(d)[:80]}')
code, d = req('post', '/records', AT, {'ticket_id': TK['id'], 'process_id': JIA, 'qty': 1})
check('F3d 满扎后再登被拒', code == 400 and '超限' in str(d), f'{code} {str(d)[:80]}')
code, d = req('post', '/records', AT, {'ticket_id': TK['id'], 'process_id': JIA, 'qty': 1,
                                       'user_id': emp.id})
check('F3e 跨员工累计同样封顶', code == 400 and '超限' in str(d), f'{code} {str(d)[:80]}')

code, d = req('delete', f'/records/{REC_JIA}', AT)
check('F4a 撤销登记(整条扣减)', code in (200, 204), f'{code} {str(d)[:60]}')
code, d = req('post', '/records', AT, {'ticket_id': TK['id'], 'process_id': JIA, 'qty': 2})
check('F4b 撤后重登为新记录(qty=2)', code in (200, 201) and d.get('id') != REC_JIA
      and d.get('qty') == 2, f'{code} {str(d)[:80]}')

# 协助登记（在职员工，走增量/新建分支）+ 乙工序允许跳序
code, d = req('post', '/records', AT, {'ticket_id': TK['id'], 'process_id': YI, 'qty': 2,
                                       'user_id': emp.id})
REC_EMP_YI = d.get('id')
check('F4c 协助登记在职员工(201)', code in (200, 201), f'{code} {str(d)[:80]}')
code, d = req('post', '/records', AT, {'ticket_id': TK['id'], 'process_id': YI, 'qty': 1,
                                       'user_id': emp.id})
check('F4d 协助增量补登(2+1=3同一条)', code in (200, 201) and d.get('id') == REC_EMP_YI
      and d.get('qty') == 3, f'{code} {str(d)[:80]}')

# ================= ⑥ 协助跨厂硬拦截 =================
code, d = req('post', '/records', AT, {'ticket_id': TK['id'], 'process_id': YI, 'qty': 1,
                                       'user_id': outsider.id})
check('F6a 协助非本公司成员被拒(400)', code == 400 and '在职成员' in str(d),
      f'{code} {str(d)[:80]}')

# ================= ⑤ 离职强制结清 + 离职后薪酬可查 =================
emp_uc = UserCompany.objects.get(company_id=CID, user=emp)
code, d = req('post', f'/employees/{emp_uc.id}/resign', AT, {})
check('F5a 未结清离职被拒(400,无confirm通道)', code == 400 and '结算' in str(d),
      f'{code} {str(d)[:80]}')
today = date.today().isoformat()
code, d = req('post', '/salary/settlements', AT,
              {'period_start': today, 'period_end': today, 'user_id': emp.id})
check('F5b 管理员为员工结算', code == 201, f'{code} {str(d)[:80]}')
code, d = req('post', f'/employees/{emp_uc.id}/resign', AT, {})
check('F5c 结清后离职成功', code == 200, f'{code} {str(d)[:80]}')
code, d = req('get', f"/salary/employee/{emp.id}", AT)
check('F5d 离职员工薪酬可查(200,管理员)', code == 200 and 'unsettled' in d,
      f'{code} {str(d)[:80]}')
code, d = req('get', '/salary/company/employees', AT)
row = next((e for e in (d or []) if e.get('user_id') == emp.id), None)
check('F5e 薪酬卡含离职成员(status=离职)', code == 200 and row is not None
      and row.get('status') == UserCompany.Status.RESIGNED,
      f'{code} {str(row)[:80]}')

# ================= ⑦ 床次软删级联菲票 =================
code, prod4 = req('post', '/productions', AT, {'style': style['id'], 'bed_no': BED4})
req('post', '/tickets/batch', AT, {'production_id': prod4['id'],
                                   'items': [{'color': '红', 'size': 'M', 'qty': 6}]})
code, d = req('delete', f"/productions/{prod4['id']}", AT)
p4 = Production.all_objects.get(pk=prod4['id'])
t4 = list(Ticket.all_objects.filter(production_id=prod4['id']))
check('F7a 删床次成功且床次软删', code in (200, 204) and p4.is_deleted, f'{code}')
check('F7b 其下菲票级联软删', len(t4) > 0 and all(t.is_deleted for t in t4),
      f'n={len(t4)} deleted={[t.is_deleted for t in t4]}')
code, d = req('get', f'/tickets?bed_no={BED4}', AT)
check('F7c 票查询不再返回已删床次菲票', code == 200
      and not (d.get('results') if isinstance(d, dict) else d), f'{code} {str(d)[:80]}')

# ================= ⑧ plan_qty =================
code, d = req('get', f'/productions/{PROD1}', AT)
check('F8a 床次详情返回plan_qty', code == 200 and d.get('plan_qty') == 66,
      f'{code} {str(d)[:80]}')
code, d = req('get', '/productions?bed_no=' + BED1, AT)
rows = d.get('results') if isinstance(d, dict) else d
check('F8b 床次列表返回plan_qty', code == 200 and rows and rows[0].get('plan_qty') == 66,
      f'{code} {str(rows)[:80]}')

# ================= cleanup（再清一次，保持库净） =================
Record.all_objects.filter(ticket__production__bed_no__in=[BED1, BED2, BED4]) \
    .update(settlement=None)
Record.all_objects.filter(ticket__production__bed_no__in=[BED1, BED2, BED4])._raw_delete(
    Record.all_objects.db)
Settlement.all_objects.filter(company_id=CID, user__phone=EMP_PHONE)._raw_delete(
    Settlement.all_objects.db)
Ticket.all_objects.filter(production__bed_no__in=[BED1, BED2, BED4])._raw_delete(
    Ticket.all_objects.db)
ProductionProcess.all_objects.filter(production__bed_no__in=[BED1, BED2, BED4])._raw_delete(
    ProductionProcess.all_objects.db)
Production.all_objects.filter(bed_no__in=[BED1, BED2, BED4])._raw_delete(Production.all_objects.db)
StyleProcess.all_objects.filter(style__style_no=SNO)._raw_delete(StyleProcess.all_objects.db)
Style.all_objects.filter(style_no=SNO)._raw_delete(Style.all_objects.db)
Process.all_objects.filter(company_id=CID, name__in=[P_JIA, P_YI, P_BING, P_OUT])._raw_delete(
    Process.all_objects.db)
for ph in (EMP_PHONE, OUT_PHONE):
    u = User.objects.filter(phone=ph).first()
    if u:
        AuditLog.objects.filter(target_user=u).delete()
        AuditLog.objects.filter(operator=u).delete()
        try:
            u.delete()
        except ProtectedError:
            pass  # 同 preclean：PROTECT 引用时跳过
CompanySetting.objects.filter(company_id=CID, key='auto_cut_done').delete()

print(f'== audit_fixes: {len(PASS)} passed, {len(FAIL)} failed ==')
if FAIL:
    print('FAILED:', FAIL)
