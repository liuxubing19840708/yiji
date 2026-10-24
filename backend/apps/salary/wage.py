# -*- coding: utf-8 -*-
"""四类工资计算唯一口径（2026-10-23 用户拍板，结算/展示/申请共用本模块）。

工资计算方式 = UserCompany.salary_type（每人四选一，互斥）：
  - fixed      固定工资：日薪×期间有登记去重天数 / 月薪×期间月数（沿用原 _fixed_block 口径）
  - whole      整件计件：期间内有登记且「已完成」（distinct 工序数 ≥ 床次配置工序数，
               口径同 stats.py）的菲票，按该菲票全部参与登记员工数均摊 1/N 件，
               × 员工整件单价 piece_rate；多人合作不重复计件
  - process    工序计件：Σ 登记 qty×price（原口径，存量默认，行为零变化）
  - commission 计件提成：本组成员期间登记计件总额（未结算部分）× 提成比例% commission_rate
               （组口径：UserCompany.group_no，含组内全部成员的登记金额，与其个人
               工资方式无关；未分组（group_no=0/空）无组基数 → 提成为 0）

防重复计酬统一规则：三类计件口径（process/whole/commission 的基数）只统计
settlement__isnull=True 的登记——已进结算单快照的记录不再参与任何工资计算。
（fixed 不依赖登记金额，出勤天数仍按全部登记统计。）
"""
from decimal import Decimal

from django.db.models import Count, DecimalField, F, Sum

from apps.accounts.models import UserCompany
from apps.core.utils import q2
from apps.production.stats import production_pc_map

_DEC2 = DecimalField(max_digits=12, decimal_places=2)

_UNSETTLED = dict(settlement__isnull=True)


def month_end(d):
    """所在月份的最后一天。"""
    import calendar
    return d.replace(day=calendar.monthrange(d.year, d.month)[1])


def month_count(date_from, date_to):
    """期间覆盖的自然月数（固定工资按月计薪用），不足整月按一个月计。"""
    if not date_from or not date_to or date_to < date_from:
        return 0
    months = (date_to.year - date_from.year) * 12 + (date_to.month - date_from.month)
    if date_from.day > 1 or date_to.day < month_end(date_to).day:
        months += 1
    return max(months, 1)


def _period_records(company, date_from, date_to, user_id=None, **extra):
    """期间登记记录集（registered_at 落区间；order_by() 前置防 Meta.ordering 混入聚合）。"""
    from apps.records.models import Record
    qs = (Record.objects.filter(company=company, **extra).order_by())
    if user_id:
        qs = qs.filter(user_id=user_id)
    if date_from:
        qs = qs.filter(registered_at__date__gte=date_from)
    if date_to:
        qs = qs.filter(registered_at__date__lte=date_to)
    return qs


def worked_days(company, user_id, date_from, date_to):
    """期间有登记的去重天数（固定工资按天计薪用）。"""
    return (_period_records(company, date_from, date_to, user_id=user_id)
            .values('registered_at__date').distinct().count())


def _whole_wage(company, member, date_from, date_to):
    """整件计件：期间有登记且已完成的菲票 × 单价 ÷ 参与人数（合作均摊）。"""
    from apps.records.models import Record

    rate = q2(member.piece_rate or 0)
    if rate <= 0:
        return Decimal('0'), {'whole_tickets': 0, 'piece_rate': str(rate)}
    # 候选菲票：本人在期间内有登记的菲票（settlement 过滤不影响「参与」判定，
    # 但已完成判定与均摊只看未结算记录，见模块头防重复计酬规则）
    my_tickets = list(_period_records(company, date_from, date_to,
                                      user_id=member.user_id, **_UNSETTLED)
                      .values_list('ticket_id', flat=True).distinct())
    if not my_tickets:
        return Decimal('0'), {'whole_tickets': 0, 'piece_rate': str(rate)}
    # 完成判定：菲票全量（不限期间/不限本人）distinct 工序数 ≥ 床次配置工序数
    rows = list(Record.objects.filter(company=company, ticket_id__in=my_tickets,
                                      **_UNSETTLED).order_by()
                .values('ticket_id')
                .annotate(pn=Count('process_id', distinct=True),
                          users=Count('user_id', distinct=True)))
    pc_map = production_pc_map(Record.objects.filter(
        company=company, ticket_id__in=my_tickets)
        .values_list('ticket__production_id', flat=True).distinct())
    prod_of = dict(Record.objects.filter(company=company, ticket_id__in=my_tickets)
                   .values_list('ticket_id', 'ticket__production_id'))
    amount = Decimal('0')
    done_cnt = 0
    for row in rows:
        pc = pc_map.get(prod_of.get(row['ticket_id'])) or 0
        if pc and row['pn'] >= pc:
            # 合作均摊：1/参与人数 件（本人必在参与者内）
            amount += rate / Decimal(str(max(row['users'], 1)))
            done_cnt += 1
    return q2(amount), {'whole_tickets': done_cnt, 'piece_rate': str(rate)}


def _commission_wage(company, member, date_from, date_to, group_cache):
    """计件提成：本组成员期间未结算计件总额 × 比例%（组基数全员共享，按组缓存）。"""
    gno = member.group_no or 0
    rate = q2(member.commission_rate or 0)
    if gno <= 0 or rate <= 0:
        return Decimal('0'), {'commission_base': '0.00', 'commission_rate': str(rate)}
    # 缓存键带期间：同一 cache dict 可能被"全历史"与"本期"两个窗口复用
    key = (gno, date_from, date_to)
    if key not in group_cache:
        member_ids = list(UserCompany.objects.filter(
            company=company, group_no=gno, is_deleted=False,
            status__in=[UserCompany.Status.ACTIVE, UserCompany.Status.RESIGNED])
            .values_list('user_id', flat=True))
        base = q2(_period_records(company, date_from, date_to, **_UNSETTLED)
                  .filter(user_id__in=member_ids)
                  .aggregate(total=Sum(F('qty') * F('price'),
                                       output_field=_DEC2))['total'] or 0)
        group_cache[key] = base
    base = group_cache[key]
    return q2(base * rate / Decimal('100')), {
        'commission_base': str(base), 'commission_rate': str(rate)}


def compute_wage(company, member, date_from, date_to, group_cache=None):
    """按成员工资方式计算期间工资。

    返回 {salary_type, salary_type_display, amount, detail}；
    member 为 UserCompany（含 salary_type/piece_rate/commission_rate/fixed_salary）。
    group_cache：批量场景传同一 dict 缓存组基数（提成口径），避免同组重复聚合。
    """
    months = month_count(date_from, date_to)
    st = member.salary_type or UserCompany.SalaryType.PROCESS
    detail = {}
    if st == UserCompany.SalaryType.FIXED:
        days = worked_days(company, member.user_id, date_from, date_to)
        base = q2(member.fixed_salary or 0)
        amount = base * days if member.fixed_type == UserCompany.FixedType.DAY \
            else base * months
        detail = {'worked_days': days, 'months': months, 'fixed_salary': str(base)}
    elif st == UserCompany.SalaryType.WHOLE:
        amount, detail = _whole_wage(company, member, date_from, date_to)
    elif st == UserCompany.SalaryType.COMMISSION:
        amount, detail = _commission_wage(
            company, member, date_from, date_to,
            group_cache if group_cache is not None else {})
    else:  # process（存量默认）
        amount = q2(_period_records(company, date_from, date_to,
                                    user_id=member.user_id, **_UNSETTLED)
                    .aggregate(total=Sum(F('qty') * F('price'),
                                         output_field=_DEC2))['total'] or 0)
    return {
        'salary_type': st,
        'salary_type_display': member.get_salary_type_display(),
        'amount': q2(amount),
        'detail': detail,
    }
