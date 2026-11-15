# -*- coding: utf-8 -*-
"""
交期预警通知（P3A）端到端后端验证 —— 覆盖《端到端测试设计方案》§3.1 ORD-070~072。

  ORD-070  交期前 7 天（含当天）生成站内通知，管理员岗在职成员收到；
  ORD-071  过期未完成每日一条，按「单+日」去重（同日重复执行只 1 条新增）；
  ORD-072  无交期订单 / 已完成（status=done）订单不触发；
  补充     完成率 100%（全部色码登记满）不触发临近/逾期；
           非管理员岗 / 离职管理员不收通知；title 恒以【单号】开头（去重锚点）。

跑法：manage.py shell -c "exec(compile(open('test_deadline_notify.py',encoding='utf-8').read(),'test_deadline_notify.py','exec'))"
幂等：复用 test_orders.py 的「订单测试厂(可删)」，每轮 preclean 硬清本厂订单域
业务行 + 本厂两类交期通知行（Notification.all_objects 物理删），不依赖 DB 现状。
"""
import io
from datetime import timedelta

from django.contrib.auth.hashers import make_password
from django.core.management import call_command
from django.utils import timezone

from apps.accounts.models import Company, Post as PostM, User, UserCompany
from apps.accounts.services import create_company_with_owner
from apps.catalog.models import Process, Style, Ticket
from apps.ops.models import Notification
from apps.orders.models import Order, OrderItem
from apps.production.models import Production
from apps.records.models import Record

PASS, FAIL = [], []


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    print(('PASS' if cond else 'FAIL'), name, ('' if cond else f'| {detail}'))


def ensure_user(phone, name):
    u = User.objects.filter(phone=phone).first()
    if u is None:
        u = User.objects.create(phone=phone, username=phone, real_name=name,
                                password=make_password('yisao1234'), is_active=True)
    return u


def ensure_member(u, company, post, status=UserCompany.Status.ACTIVE):
    uc = UserCompany.all_objects.filter(user=u, company=company).first()
    if uc is None:
        return UserCompany.objects.create(user=u, company=company, post=post, status=status)
    uc.is_deleted = False
    uc.status = status
    uc.post = post
    uc.save(update_fields=['is_deleted', 'status', 'post', 'updated_at'])
    return uc


OWNER, H_PLAIN, H_RESIGNED = '13988710101', '13988710105', '13988710106'
CO_A = '订单测试厂(可删)'
CODE_NEAR, CODE_OVERDUE = 'deadline_near', 'deadline_overdue'

# ================= 建厂（复用 test_orders 的「订单测试厂(可删)」） + preclean =================
company = Company.objects.filter(name=CO_A).first()
if company is None:
    owner = ensure_user(OWNER, '订单厂长')
    company, admin_post = create_company_with_owner(owner, CO_A)
else:
    owner = ensure_user(OWNER, '订单厂长')
    admin_post = PostM.objects.get(company=company, is_admin=True, is_deleted=False)


def preclean():
    """幂等自清扫：硬清本厂订单域业务行 + 本厂两类交期通知（测试厂行可删）。"""
    Record.objects.filter(company=company).delete()
    Ticket.objects.filter(company=company, bed_no__startswith='TORD').delete()
    Production.objects.filter(company=company, bed_no__startswith='TORD').delete()
    OrderItem.all_objects.filter(order__company=company).delete()
    Order.all_objects.filter(company=company).delete()
    from apps.tech.models import Bom  # noqa: E402  # D4：订单域套件建了生效BOM，PROTECT 须先于款式删除
    Bom.all_objects.filter(company=company).delete()
    Style.objects.filter(company=company, style_no__startswith='TORD').delete()
    Notification.all_objects.filter(
        company=company, code__in=[CODE_NEAR, CODE_OVERDUE]).delete()


preclean()

# 成员矩阵：非管理员普工（不应收到）+ 离职管理员（不应收到）
post_plain = PostM.objects.filter(company=company, name='预警普工', is_deleted=False).first()
if post_plain is None:
    post_plain = PostM.objects.create(company=company, name='预警普工', permissions=[])
plain = ensure_user(H_PLAIN, '预警普工员')
ensure_member(plain, company, post_plain)
r_admin = ensure_user(H_RESIGNED, '预警离职管理员')
ensure_member(r_admin, company, admin_post, status=UserCompany.Status.RESIGNED)

today = timezone.localdate()
style = Style.objects.create(company=company, style_no='TORD-DL-S1', name='预警测试款')
proc = Process.objects.filter(company=company, is_fixed=True).first() \
    or Process.objects.create(company=company, name='整件', is_fixed=True)


def mk_order(no, days=None, status=Order.Status.PRODUCING, qty=100):
    """直接建单（绕过 API）：days=None 无交期；days 负数=已过期。"""
    o = Order.objects.create(
        company=company, order_no=no, status=status,
        delivery_date=today + timedelta(days=days) if days is not None else None)
    OrderItem.objects.create(order=o, color='黑', size='M', qty=qty)
    return o


def make_full(o):
    """给订单挂一床一票并登记满（完成率 100% 的分子口径=菲票登记 Σqty）。"""
    prod = Production.objects.create(
        company=company, style=style, bed_no=f'TORD-DL-{o.order_no[-2:]}',
        order=o, total_qty=100)
    tk = Ticket.objects.create(company=company, production=prod, bed_no=prod.bed_no,
                               bundle_no='TORD-DL-B1', color='黑', size='M', qty=100,
                               qr_code='x')
    Record.objects.create(company=company, ticket=tk, process=proc, user=owner,
                          qty=100, price='1.000', registered_at=timezone.now())


# 订单矩阵：临近(3/7/0 天) + 预警期外(8 天) + 无交期 + done + 逾期(-5) + 两个满登记
o_near = mk_order('TORD-DL-NEAR', 3)
o_edge = mk_order('TORD-DL-EDGE', 7)
o_today = mk_order('TORD-DL-TODAY', 0)
o_future = mk_order('TORD-DL-FUTURE', 8)
o_nodue = mk_order('TORD-DL-NODUE', None)
o_done = mk_order('TORD-DL-DONE', 2, status=Order.Status.DONE)
o_over = mk_order('TORD-DL-OVER', -5)
o_full = mk_order('TORD-DL-FULL', 2)
o_full_over = mk_order('TORD-DL-FULLOV', -3)
make_full(o_full)
make_full(o_full_over)


def run_cmd():
    out = io.StringIO()
    call_command('notify_deadlines', noinput=True, stdout=out)
    return out.getvalue()


def rows(code=None, user=None, title_part=None):
    qs = Notification.all_objects.filter(company=company)
    if code:
        qs = qs.filter(code=code)
    if user:
        qs = qs.filter(user=user)
    if title_part:
        qs = qs.filter(title__contains=title_part)
    return list(qs)


def total_rows():
    return Notification.all_objects.filter(
        company=company, code__in=[CODE_NEAR, CODE_OVERDUE]).count()


# ================= 第一遍执行（发通知） =================
out1 = run_cmd()
print('--- 第一遍输出 ---')
print(out1)

near_rows = rows(code=CODE_NEAR, user=owner)
over_rows = rows(code=CODE_OVERDUE, user=owner)

check('ORD-070 输出含临近已发标记', '临近/已发' in out1, out1)
check('ORD-070 临近3天 管理员收到通知', len(rows(code=CODE_NEAR, user=owner, title_part='TORD-DL-NEAR')) == 1,
      str(len(rows(code=CODE_NEAR, user=owner, title_part='TORD-DL-NEAR'))))
check('ORD-070 标题含剩余天数', any('3天' in n.title for n in rows(code=CODE_NEAR, user=owner, title_part='TORD-DL-NEAR')),
      str([n.title for n in rows(code=CODE_NEAR, user=owner, title_part='TORD-DL-NEAR')]))
check('ORD-070 边界7天也触发', len(rows(code=CODE_NEAR, user=owner, title_part='TORD-DL-EDGE')) == 1, '')
check('ORD-070 当天交期(days_left=0)触发', len(rows(code=CODE_NEAR, user=owner, title_part='TORD-DL-TODAY')) == 1, '')
check('ORD-072 8天外不触发', len(rows(code=CODE_NEAR, user=owner, title_part='TORD-DL-FUTURE')) == 0, '')
check('ORD-072 无交期订单不触发', len(rows(code=CODE_NEAR, user=owner, title_part='TORD-DL-NODUE')) == 0, '')
check('ORD-072 done 状态订单不触发', len(rows(code=CODE_NEAR, user=owner, title_part='TORD-DL-DONE')) == 0, '')
check('ORD-071 逾期订单发出逾期通知',
      any('已逾期5天' in n.title for n in rows(code=CODE_OVERDUE, user=owner, title_part='TORD-DL-OVER')),
      str([n.title for n in rows(code=CODE_OVERDUE, user=owner)]))
check('去重锚点 title 恒以【单号】开头', all(n.title.startswith('【') and '】' in n.title for n in near_rows + over_rows), '')

check('补充 完成率100% 不发临近', len(rows(code=CODE_NEAR, user=owner, title_part='TORD-DL-FULL')) == 0, '')
check('补充 完成率100% 过期也不发逾期', len(rows(code=CODE_OVERDUE, user=owner, title_part='TORD-DL-FULLOV')) == 0, '')
check('补充 非管理员岗成员未收到', len(rows(code=CODE_NEAR, user=plain)) == 0 and len(rows(code=CODE_OVERDUE, user=plain)) == 0, '')
check('补充 离职管理员未收到', len(rows(user=r_admin)) == 0, '')

n_near1 = len(near_rows)
n_over1 = len(over_rows)
total1 = total_rows()
check('统计 第一遍 临近3单+逾期1单', n_near1 == 3 and n_over1 == 1, f'near={n_near1} over={n_over1}')

# ================= 第二遍执行（同日幂等，ORD-071 去重） =================
out2 = run_cmd()
print('--- 第二遍输出 ---')
print(out2)

total2 = total_rows()
check('ORD-071 同日重复执行零新增', total1 == total2 and total1 == 4, f'{total1} -> {total2}')
check('ORD-071 第二遍输出含当日已发跳过标记', '跳过(当日已发)' in out2, out2)
check('ORD-071 第二遍临近无重复', len(rows(code=CODE_NEAR, user=owner)) == n_near1, '')
check('ORD-071 第二遍逾期无重复', len(rows(code=CODE_OVERDUE, user=owner)) == n_over1, '')

# ================= 第三遍：完成后再补一单验证幂等仍按单隔离 =================
o_late = mk_order('TORD-DL-LATE', 1)
out3 = run_cmd()
check('补充 新单独立触发旧单不重发',
      len(rows(code=CODE_NEAR, user=owner, title_part='TORD-DL-LATE')) == 1
      and len(rows(code=CODE_NEAR, user=owner)) == n_near1 + 1,
      f"{out3} near={len(rows(code=CODE_NEAR, user=owner))}")

# ================= cleanup =================
try:
    preclean()
    print('cleanup: done（订单域业务行+两类交期通知已清，测试厂/账号复用保留）')
except Exception as e:  # noqa: BLE001
    print('cleanup WARN:', e)

print(f'\n==== 交期预警套件：{len(PASS)} pass / {len(FAIL)} fail ====')
if FAIL:
    print('FAILED:', FAIL)
