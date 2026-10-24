"""演示/验收种子数据（幂等：公司已存在则跳过）。

用法：
    .venv/Scripts/python manage.py seed_demo            # 已有则跳过
    .venv/Scripts/python manage.py seed_demo --reset    # 删除演示公司后重建

登录账号（密码统一 yisao1234）：
    13800000001 张厂长（管理员）
    13800000002 王小丽（平车工，在职，固定工资 5000/月）
    13800000003 李翠花（平车工，在职）
    13800000004 陈阿珍（待审核）
    13800000005 赵妹（离职）
"""
import random

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import Company, Post, User, UserCompany
from apps.catalog.models import Customer, CustomerGroup, Process, Style, StyleProcess, Ticket
from apps.core.utils import build_ticket_qr
from apps.production.models import Production, ProductionProcess
from apps.records.models import Record

PASSWORD = 'yisao1234'
COMPANY_NAME = '辉腾制衣（演示）'


class Command(BaseCommand):
    help = '生成演示种子数据'

    def add_arguments(self, parser):
        parser.add_argument('--reset', action='store_true', help='先删除演示公司再重建')

    def handle(self, *args, **options):
        # User 无软删除，直接物理清理演示账号；Company/BaseModel 走软删，reset 时物理删
        old = Company.all_objects.filter(name=COMPANY_NAME).first()
        if old and options['reset']:
            # 物理清理：先删依赖（PROTECT FK），最后删公司
            from apps.ops.models import AuditLog, CompanySetting, Notification
            from apps.salary.models import (SalaryAdjust, SettleApply, Settlement)
            Settlement.all_objects.filter(company=old).delete()
            SettleApply.all_objects.filter(company=old).delete()
            SalaryAdjust.all_objects.filter(company=old).delete()
            AuditLog.all_objects.filter(company=old).delete()
            Notification.all_objects.filter(company=old).delete()
            CompanySetting.all_objects.filter(company=old).delete()
            Record.all_objects.filter(company=old).delete()
            Ticket.all_objects.filter(company=old).delete()
            ProductionProcess.all_objects.filter(production__company=old).delete()
            Production.all_objects.filter(company=old).delete()
            StyleProcess.all_objects.filter(style__company=old).delete()
            Style.all_objects.filter(company=old).delete()
            Process.all_objects.filter(company=old).delete()
            Customer.all_objects.filter(company=old).delete()
            CustomerGroup.all_objects.filter(company=old).delete()
            UserCompany.all_objects.filter(company=old).delete()
            Post.all_objects.filter(company=old).delete()
            Company.all_objects.filter(pk=old.pk).delete()
            User.objects.filter(username__in=[
                '13800000001', '13800000002', '13800000003', '13800000004', '13800000005'
            ]).delete()
            old = None
        if old:
            self.stdout.write(self.style.WARNING(
                f'演示公司已存在：{old}（编号 {old.company_no}），跳过。用 --reset 重建。'))
            return

        with transaction.atomic():
            company = self._create()
        self.stdout.write(self.style.SUCCESS(
            f'种子数据完成：{company}，登录 13800000001 / {PASSWORD}'))

    def _create(self) -> Company:
        users = {}
        for i, name in enumerate(['张厂长', '王小丽', '李翠花', '陈阿珍', '赵妹'], start=1):
            phone = f'1380000000{i}'
            user, created = User.objects.get_or_create(
                username=phone,
                defaults={'phone': phone, 'real_name': name})
            if not created:
                User.objects.filter(pk=user.pk).update(phone=phone, real_name=name)
                user.refresh_from_db()
            user.set_password(PASSWORD)
            user.save(update_fields=['password'])
            users[name] = user

        company = Company.objects.create(
            name=COMPANY_NAME, city='揭阳',
            company_no=Company.next_company_no(), creator=users['张厂长'])

        post_admin = Post.objects.create(
            company=company, name='厂长', permissions=['*'], is_admin=True, is_system=True)
        # 后端实际权限 key 仅三个：styles / customers / production（其余模块 require_admin）
        post_worker = Post.objects.create(
            company=company, name='平车工', permissions=['production'], is_system=True)
        post_misc = Post.objects.create(
            company=company, name='杂工', permissions=['production'], is_system=True)

        today = timezone.localdate()
        UserCompany.objects.create(
            user=users['张厂长'], company=company, post=post_admin,
            status=UserCompany.Status.ACTIVE, hired_date=today, last_used_at=timezone.now())
        UserCompany.objects.create(
            user=users['王小丽'], company=company, post=post_worker,
            status=UserCompany.Status.ACTIVE, hired_date=today, is_pinned=True,
            fixed_salary=5000, fixed_type=UserCompany.FixedType.MONTH)
        UserCompany.objects.create(
            user=users['李翠花'], company=company, post=post_worker,
            status=UserCompany.Status.ACTIVE, hired_date=today)
        UserCompany.objects.create(
            user=users['陈阿珍'], company=company, post=post_misc,
            status=UserCompany.Status.PENDING)
        UserCompany.objects.create(
            user=users['赵妹'], company=company, post=post_misc,
            status=UserCompany.Status.RESIGNED, hired_date=today)

        g_old, _ = CustomerGroup.objects.get_or_create(company=company, name='老客户')
        g_new, _ = CustomerGroup.objects.get_or_create(company=company, name='新客户')
        customers = []
        for i, (no, name, grp) in enumerate([
            ('C001', '广州俏丽服饰', g_old), ('C002', '虎门名莎制衣', g_old),
            ('C003', '中山华衣达', g_new), ('C004', '普宁流沙服装城', g_new),
            ('C005', '汕头潮南织造', None),
        ], start=1):
            customers.append(Customer.objects.create(
                company=company, customer_no=no, name=name, group=grp,
                phone=f'1390000000{i}'))

        process_defs = [
            ('车领', '0.850', True), ('埋夹', '1.200', True), ('卷边', '0.500', True),
            ('打枣', '0.150', True), ('剪线', '0.050', True), ('烫衣', '0.600', True),
            ('钉扣', '0.100', False), ('查衣', '0.300', False),
        ]
        processes = {}
        for sort, (name, price, fixed) in enumerate(process_defs):
            processes[name] = Process.objects.create(
                company=company, name=name, default_price=price, is_fixed=fixed, sort=sort)

        style_defs = [
            ('ST001', '女装碎花连衣裙', customers[0], ['车领', '埋夹', '卷边', '打枣', '剪线', '烫衣']),
            ('ST002', '男装牛津纺衬衫', customers[1], ['车领', '埋夹', '卷边', '钉扣', '剪线', '查衣']),
            ('ST003', '童装短袖T恤', customers[2], ['卷边', '打枣', '剪线']),
        ]
        styles = {}
        for no, name, cust, proc_names in style_defs:
            style = Style.objects.create(
                company=company, style_no=no, name=name, customer=cust)
            for sort, pn in enumerate(proc_names):
                StyleProcess.objects.create(
                    style=style, process=processes[pn], price=processes[pn].default_price, sort=sort)
            style.recalc_price()
            styles[no] = style

        productions = []
        for bed, (style_no, cust, total, cut_done) in enumerate([
            ('ST001', customers[0], 560, False),
            ('ST002', customers[1], 420, False),
            ('ST001', customers[2], 300, True),
        ], start=1):
            style = styles[style_no]
            prod = Production.objects.create(
                company=company, style=style, bed_no=str(bed), total_qty=total,
                customer=cust, fabric='全棉 40支' if bed != 2 else '牛津纺',
                is_cut_done=cut_done)
            for sp in style.processes.all():
                ProductionProcess.objects.create(
                    production=prod, process=sp.process, price=sp.price)
            productions.append(prod)

        colors = ['白色', '碎花蓝', '黑色', '米黄']
        sizes = ['S', 'M', 'L', 'XL']
        tickets = []
        for idx, prod in enumerate(productions, start=1):
            for bundle in range(1, 11 if idx < 3 else 7):
                color = colors[bundle % len(colors)]
                size = sizes[bundle % len(sizes)]
                qty = random.choice([20, 25, 30, 35])
                tickets.append(Ticket.objects.create(
                    company=company, production=prod, bed_no=prod.bed_no,
                    bundle_no=str(bundle), color=color, size=size, qty=qty,
                    fabric=prod.fabric,
                    qr_code=build_ticket_qr(company.company_no, prod.bed_no,
                                            prod.style.style_no, str(bundle),
                                            color, size, qty)))

        # 少量今日登记，工作台/记录页有数据可看（唯一约束：票×工序×员工）
        wang = users['王小丽']
        li = users['李翠花']
        pairs = [
            (tickets[0], '车领', wang, 30), (tickets[1], '车领', wang, 25),
            (tickets[0], '埋夹', li, 30), (tickets[2], '卷边', li, 20),
            (tickets[3], '打枣', wang, 35), (tickets[4], '剪线', li, 25),
        ]
        for ticket, pname, user, qty in pairs:
            Record.objects.create(
                company=company, ticket=ticket, process=processes[pname],
                user=user, qty=qty, price=processes[pname].default_price)
        return company
