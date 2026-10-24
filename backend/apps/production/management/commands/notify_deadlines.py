# -*- coding: utf-8 -*-
r"""交期预警站内通知（P3A，实施方案 §2.5.4 / 端到端测试 §3.1 ORD-070~072）。

规则（每日跑一次）：
  1. 临近：status=producing、delivery_date 非空、距交期 ≤7 天（含当天，
     days_left ∈ [0,7]）且完成率 <100% → 给该公司「管理员岗在职成员」发站内
     通知，标题含单号与剩余天数；
  2. 逾期：days_left < 0 且完成率 <100% → 每日一条（按「单+日」去重）；
  3. 全部色码登记满（完成率 100%，口径复用 apps/orders/views.py 的
     _aggregate_stats——分子=挂单床次菲票登记 Σqty，分母=OrderItem.qty，
     封顶 100）的订单不再发临近提醒（逾期同样要求 <100%，自然不发）。

去重实现（无新增 DDL，禁 makemigrations）：
  Notification 现有字段组合表达「同单同日同类通知」——
    code   固定两种：deadline_near（临近）/ deadline_overdue（逾期）；
    title  恒以「【单号】」开头（临近含剩余天数、逾期含逾期天数，同一天内
           天数不变 → 同单同日 title 恒定）；
    created_at 取本地日期与今天比较。
  查重条件 = company + code + title__startswith('【单号】') + created_at 当日，
  命中即跳过整单（含全部收件人）→ 同一天重复执行零重复，幂等。

调度方式建议（每天一次即可，去重保证当天补跑/重跑不重复）：
  Linux cron（每天 08:00；venv 路径按实际部署替换）：
    0 8 * * * cd /path/to/backend && .venv/bin/python manage.py notify_deadlines --noinput >> logs/notify_deadlines.log 2>&1
  Windows 计划任务（schtasks 一行创建，或用「任务计划程序」GUI 配每日 08:00）：
    schtasks /Create /TN "扫菲交期预警" /SC DAILY /ST 08:00 ^
      /TR "D:\project\扫菲\backend\.venv\Scripts\python.exe D:\project\扫菲\backend\manage.py notify_deadlines --noinput"
  手动执行：python manage.py notify_deadlines --noinput
"""
from collections import defaultdict

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.ops.models import Notification
from apps.orders.models import Order

# 通知类型 code（title 前缀【单号】+ code + 当日 created_at 组成「单+日」查重键）
CODE_NEAR = 'deadline_near'
CODE_OVERDUE = 'deadline_overdue'
# 临近预警窗口（含当天），与 orders/views.py DELIVERY_WARN_DAYS 同口径
WARN_DAYS = 7


class Command(BaseCommand):
    help = '交期预警：临近 7 天内/过期未完成的 producing 订单 → 站内通知管理员岗（单+日去重，幂等）'

    def add_arguments(self, parser):
        # 计划任务惯例参数，无交互逻辑，仅兼容 --noinput 调用
        parser.add_argument('--noinput', action='store_true',
                            help='无交互运行（供计划任务使用，实际无交互逻辑）')

    def handle(self, *args, **options):
        from apps.accounts.models import UserCompany
        from apps.orders.views import _aggregate_stats
        from apps.ops.services import notify

        today = timezone.localdate()
        # 扫描全部公司 producing 且有交期的订单（objects 默认滤软删）
        orders = list(Order.objects.filter(
            status=Order.Status.PRODUCING, delivery_date__isnull=False
        ).select_related('company').order_by('company_id', 'id'))
        if not orders:
            self.stdout.write('无可扫描订单（producing 且有交期），结束')
            return

        # 按公司分组批量算完成率（口径唯一：复用订单页签 _aggregate_stats）
        by_company = defaultdict(list)
        for o in orders:
            by_company[o.company_id].append(o)

        sent_orders, skipped = 0, 0
        for company_id, group in by_company.items():
            company = group[0].company
            stats = _aggregate_stats(company, [o.id for o in group])
            # 管理员岗在职成员（收件人，一次查出全组复用）
            admin_ids = list(UserCompany.objects.filter(
                company_id=company_id,
                status=UserCompany.Status.ACTIVE,
                post__is_admin=True, post__is_deleted=False,
                user__is_active=True,
            ).values_list('user_id', flat=True))
            for order in group:
                total, registered, progress = stats.get(order.id, (0, 0, 0.0))
                days_left = (order.delivery_date - today).days
                if total == 0:
                    # 无明细无法计算完成率，防误报
                    self.stdout.write(f'{company.name}/{order.order_no}/-/跳过(无明细)')
                    skipped += 1
                    continue
                if days_left < 0:
                    kind, code = '逾期', CODE_OVERDUE
                    title = f'【{order.order_no}】已逾期{-days_left}天未完成'
                    tip = f'已逾期{-days_left}天'
                elif days_left <= WARN_DAYS:
                    kind, code = '临近', CODE_NEAR
                    title = (f'【{order.order_no}】今日交期' if days_left == 0
                             else f'【{order.order_no}】距交期还有{days_left}天')
                    tip = ('今日交期' if days_left == 0
                           else f'距交期还有{days_left}天')
                else:
                    self.stdout.write(f'{company.name}/{order.order_no}/-/跳过(未进预警期)')
                    skipped += 1
                    continue
                if progress >= 100.0:
                    # 全部色码登记满 → 不再提醒（临近/逾期均不发）
                    self.stdout.write(f'{company.name}/{order.order_no}/{kind}/跳过(完成率100%)')
                    skipped += 1
                    continue
                # 「单+日」查重：同公司同类型同单号前缀且当日已落库 → 跳过
                dup = Notification.objects.filter(
                    company=company, code=code,
                    title__startswith=f'【{order.order_no}】',
                    created_at__date=today,
                ).exists()
                if dup:
                    self.stdout.write(f'{company.name}/{order.order_no}/{kind}/跳过(当日已发)')
                    skipped += 1
                    continue
                if not admin_ids:
                    self.stdout.write(f'{company.name}/{order.order_no}/{kind}/跳过(无在职管理员)')
                    skipped += 1
                    continue
                message = (f'订单 {order.order_no}（{company.name}）{tip}，'
                           f'当前完成率 {progress}%（{registered}/{total}），请及时跟进。')
                n = notify(company, admin_ids, code, title, message)
                sent_orders += 1
                self.stdout.write(f'{company.name}/{order.order_no}/{kind}/已发({n}人)')

        self.stdout.write(f'==== 交期预警完成：扫描 {len(orders)} 单，'
                          f'发通知 {sent_orders} 单，跳过 {skipped} 单 ====')
