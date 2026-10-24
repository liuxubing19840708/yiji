"""薪酬结算视图：公司/员工工资汇总、结算与撤销、工资调整、结算申请流。

工资口径：四类工资计算唯一入口在 apps/salary/wage.py（compute_wage），
本模块只负责结转（快照/圈占/通知），不重复实现口径。
"""
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Count, DecimalField, F, Sum
from django.http import Http404
from django.utils import timezone
from django.utils.dateparse import parse_date
from rest_framework import generics
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import UserCompany
from apps.core.mixins import CompanyContextMixin
from apps.core.pagination import DefaultPagination
from apps.core.permissions import has_perm
from apps.core.utils import q2
from apps.ops.models import AuditLog
from apps.ops.services import audit, notify
from apps.records.models import Record
from apps.salary.models import SalaryAdjust, SettleApply, Settlement
from apps.salary.wage import (
    compute_wage,
    month_count as _month_count,
    month_end as _month_end,
)
from apps.salary.serializers import (
    AdjustCreateSerializer,
    SalaryAdjustSerializer,
    SettleApplyCreateSerializer,
    SettleApplySerializer,
    SettlementCreateSerializer,
    SettlementSerializer,
)

_DEC2 = DecimalField(max_digits=12, decimal_places=2)


# ---- 期间解析助手 ----

def _range(request):
    """解析 date_from / date_to / quick（today/yesterday/month），未传返回 (None, None)。"""
    date_from = request.query_params.get('date_from') or None
    date_to = request.query_params.get('date_to') or None
    quick = request.query_params.get('quick') or None
    today = timezone.localdate()
    if quick == 'today':
        date_from = date_to = today
    elif quick == 'yesterday':
        date_from = date_to = today - timedelta(days=1)
    elif quick == 'month':
        date_from = today.replace(day=1)
        date_to = _month_end(today)
    if date_from and isinstance(date_from, str):
        date_from = parse_date(date_from) or None
    if date_to and isinstance(date_to, str):
        date_to = parse_date(date_to) or None
    return date_from, date_to


def _record_period_qs(company, date_from, date_to, **extra):
    """按 registered_at 日期过滤登记记录。"""
    qs = Record.objects.filter(company=company, **extra)
    if date_from:
        qs = qs.filter(registered_at__date__gte=date_from)
    if date_to:
        qs = qs.filter(registered_at__date__lte=date_to)
    return qs


def _is_admin(user_company):
    return bool(user_company and user_company.post and user_company.post.is_admin)


def _covered_amount(company, uid, date_from, date_to):
    """员工在期间内已被已结算单覆盖的金额（防重复计酬扣减用）。

    只统计带 wage_detail 的结算单（四类工资制后创建的）：process 用户的记录
    靠圈占防重复，不走本函数；非 process 用户（fixed/whole/commission）的工资
    基数不随结算单圈占消失，靠扣减已覆盖金额避免同期间重复发薪。
    """
    total = Decimal('0')
    sts = Settlement.objects.filter(
        company=company, status=Settlement.Status.SETTLED)
    if date_to:
        sts = sts.filter(period_start__lte=date_to)
    if date_from:
        sts = sts.filter(period_end__gte=date_from)
    for s in sts:
        detail = s.wage_detail or {}
        v = detail.get(str(uid), detail.get(uid))
        if v is not None:
            total += Decimal(str(v))
    return q2(total)


def _outstanding_wage(company, member, group_cache):
    """非 process 成员（fixed/whole/commission）的未结工资。

    口径：全历史工资 − 全历史已结快照（wage_detail），下限 0。这三类工资
    基数不随结算单圈占消失（整件完成判定、组提成基数都依赖登记记录保持
    未结算状态），只按"期间重叠扣减"无法防止跨期重复发薪，故统一按
    全历史累计扣减；结算单的期间参数对非 process 用户只是快照时点。
    返回 (compute_wage 结果, 未结金额)。
    """
    covered = _covered_amount(company, member.user_id, None, None)
    w = compute_wage(company, member, None, timezone.localdate(), group_cache)
    return w, max(w['amount'] - covered, Decimal('0'))


class CompanySummaryView(CompanyContextMixin, APIView):
    """公司工资汇总（GET salary/company，管理员）：已结算/未结算、无单价工序数、固定工资预发。

    期间参数：date_from/date_to/quick，缺省为当前自然月。
    """

    def get(self, request):
        # Plan A：薪酬汇总查询走 salary.view 点（管理员岗位恒通过）
        self.check_perm('salary.view')
        date_from, date_to = _range(request)
        if not date_from and not date_to:
            today = timezone.localdate()
            date_from, date_to = today.replace(day=1), _month_end(today)
        elif not date_to:
            date_to = _month_end(date_from)
        elif not date_from:
            date_from = date_to.replace(day=1)
        period = _record_period_qs(self.company, date_from, date_to)

        settled = Settlement.objects.filter(
            company=self.company,
            status=Settlement.Status.SETTLED,
            period_start__lte=date_to,
            period_end__gte=date_from,
        ).aggregate(total=Sum('amount'))['total'] or Decimal('0')

        agg = period.filter(settlement__isnull=True).aggregate(
            # 别名不能用 qty：同调用内 amount 的 F('qty') 会解析到该聚合别名而非字段
            total_qty=Sum('qty'),
            amount=Sum(F('qty') * F('price'), output_field=_DEC2),
            users=Count('user', distinct=True),
            tickets=Count('ticket', distinct=True),
            processes=Count('process', distinct=True),
        )
        no_price = (period.filter(price=0).order_by()
                    .values('process').distinct().count())

        # 四类工资制：期间工资按人算（fixed/whole/commission 直接计薪；
        # process 金额已在上面登记聚合里，不重复计入 total）
        group_cache = {}
        wage_totals = {'fixed': Decimal('0'), 'whole': Decimal('0'),
                       'commission': Decimal('0')}
        for member in UserCompany.objects.filter(
                company=self.company, status=UserCompany.Status.ACTIVE,
                is_suspended=False,
                salary_type__in=[UserCompany.SalaryType.FIXED,
                                 UserCompany.SalaryType.WHOLE,
                                 UserCompany.SalaryType.COMMISSION]):
            w = compute_wage(self.company, member, date_from, date_to,
                             group_cache)
            wage_totals[w['salary_type']] += w['amount']
        fixed_total = wage_totals['fixed']

        return Response({
            'period': {'date_from': date_from, 'date_to': date_to},
            'settled': str(q2(settled)),
            'unsettled': {
                'qty': agg['total_qty'] or 0,
                'amount': str(q2(agg['amount'] or 0)),
                'users': agg['users'] or 0,
                'tickets': agg['tickets'] or 0,
                'processes': agg['processes'] or 0,
                'no_price_processes': no_price,
            },
            'fixed_total': str(q2(fixed_total)),
            'whole_total': str(q2(wage_totals['whole'])),
            'commission_total': str(q2(wage_totals['commission'])),
            'total': str(q2(q2(agg['amount'] or 0) + q2(fixed_total)
                            + wage_totals['whole'] + wage_totals['commission'])),
        })


class EmployeeSalaryView(CompanyContextMixin, APIView):
    """员工工资明细（GET salary/employee/<uid>）：本人或管理员可查。

    默认返回按日/按床次/按工序三维汇总（含金额）；tab=count 只返回数量
    （不含金额，规格 §10.2 计数 Tab）；tab=fixed 返回固定工资概览。
    响应均携带 settled（历史已结算总额）/ unsettled（当前未结总额），
    供员工首页 5 卡与「我的薪酬」头部使用（record.settlement 快照口径）。
    """

    def get(self, request, uid):
        is_admin = _is_admin(self.user_company)
        if uid != request.user.id and not is_admin and not has_perm(self.user_company, 'salary.view'):
            raise PermissionDenied('只能查看本人工资')
        # 2026-10-18 拍板：离职成员薪酬盲区补齐——不再限定在职，离职员工本人
        # 与管理员均可查其历史薪酬与未结余额（无本公司成员关系仍 404）
        try:
            target_uc = UserCompany.objects.select_related('user').get(
                company=self.company, user_id=uid,
                status__in=[UserCompany.Status.ACTIVE,
                            UserCompany.Status.RESIGNED])
        except UserCompany.DoesNotExist:
            raise Http404('员工不存在')

        date_from, date_to = _range(request)
        if not date_from and not date_to:
            today = timezone.localdate()
            date_from, date_to = today.replace(day=1), _month_end(today)

        # 全历史已结/未结总额（与期间无关，供头部卡片）
        salary_type = target_uc.salary_type or UserCompany.SalaryType.PROCESS
        if salary_type == UserCompany.SalaryType.PROCESS:
            # 存量口径：登记记录快照（record.settlement 外键）——行为零变化
            own = Record.objects.filter(company=self.company,
                                        user_id=target_uc.user_id)
            settled = q2(own.filter(settlement__isnull=False).aggregate(
                total=Sum(F('qty') * F('price'), output_field=_DEC2))['total'])
            unsettled = q2(own.filter(settlement__isnull=True).aggregate(
                total=Sum(F('qty') * F('price'), output_field=_DEC2))['total'])
        else:
            # 四类工资制（fixed/whole/commission）：已结 = 结算单分员工快照
            # 累计；未结 = 全历史工资 − 已结（下限 0），口径见 _outstanding_wage
            settled = _covered_amount(self.company, target_uc.user_id,
                                      None, None)
            _, unsettled = _outstanding_wage(self.company, target_uc, {})
        head = {'settled': str(settled), 'unsettled': str(unsettled),
                'salary_type': salary_type,
                'salary_type_display': target_uc.get_salary_type_display()}

        if request.query_params.get('tab') == 'fixed':
            return Response({
                'period': {'date_from': date_from, 'date_to': date_to},
                'fixed': self._fixed_block(target_uc, date_from, date_to),
                **head,
            })

        tab = request.query_params.get('tab') == 'count'
        period = _record_period_qs(self.company, date_from, date_to,
                                   user_id=target_uc.user_id)
        by_date, by_bed, by_process, by_style = {}, {}, {}, {}
        bed_settled = {}
        date_tickets = {}
        total_qty = 0
        total_amount = Decimal('0')
        ticket_ids = set()
        zero_price_processes = set()
        for rec in period.select_related('ticket', 'ticket__production',
                                         'ticket__production__style', 'process'):
            day = timezone.localtime(rec.registered_at).date()
            self._add(by_date, day, rec, tab)
            date_tickets.setdefault(day, set()).add(rec.ticket_id)
            prod = rec.ticket.production
            style = prod.style
            bed_key = (prod.id, prod.bed_no, style.style_no, style.name,
                       style.image or '', prod.total_qty or 0)
            self._add(by_bed, bed_key, rec, tab)
            if rec.settlement_id:
                bed_settled[bed_key] = (bed_settled.get(bed_key, Decimal('0'))
                                        + rec.price * rec.qty)
            self._add(by_process, (rec.process_id, rec.process.name),
                      rec, tab)
            self._add(by_style, (style.id, style.style_no, style.name,
                                 style.image or ''), rec, tab)
            total_qty += rec.qty
            if not tab:
                total_amount += rec.price * rec.qty
            if rec.price == 0:
                zero_price_processes.add(rec.process_id)
            ticket_ids.add(rec.ticket_id)

        def rows(bucket, key_fn, sort_desc=True):
            out = []
            for k, v in (sorted(bucket.items(), key=key_fn,
                                reverse=sort_desc) if sort_desc
                         else sorted(bucket.items())):
                item = {'qty': v['qty']}
                if not tab:
                    item['amount'] = str(q2(v['amount']))
                out.append((k, item))
            return out

        sort_key = (lambda kv: kv[1]['qty']) if tab else \
                   (lambda kv: kv[1]['amount'])
        by_date_rows = [{'date': str(k), 'tickets': len(date_tickets.get(k, ())),
                         **v}
                        for k, v in rows(by_date,
                                         lambda kv: kv[0], sort_desc=False)]
        by_bed_rows = [{'production_id': k[0], 'bed_no': k[1],
                        'style_no': k[2], 'style_name': k[3], 'image': k[4],
                        'bed_total': k[5],
                        'settled': str(q2(bed_settled.get(k, Decimal('0')))),
                        **v}
                       for k, v in rows(by_bed, sort_key)]
        by_process_rows = [{'process_id': k[0], 'name': k[1], **v}
                           for k, v in rows(by_process, sort_key)]
        # 汇总查看（2026-10-22 参考竞品）：按款式聚合 完成件数/单价/金额
        by_style_rows = []
        for k, v in rows(by_style, sort_key):
            qty = v['qty']
            item = {'style_id': k[0], 'style_no': k[1], 'style_name': k[2],
                    'image': k[3], 'qty': qty}
            if not tab:
                amount = q2(v['amount'])
                item['amount'] = str(amount)
                item['avg_price'] = str(q2(amount / qty)) if qty else '0.00'
            else:
                item['amount'] = '0.00'
                item['avg_price'] = '0.00'
            by_style_rows.append(item)
        summary = {'qty': total_qty, 'ticket_count': len(ticket_ids),
                   'processes': len(by_process),
                   'no_price_count': len(zero_price_processes)}
        if not tab:
            summary['amount'] = str(q2(total_amount))
        return Response({
            'period': {'date_from': date_from, 'date_to': date_to},
            'by_date': by_date_rows,
            'by_bed': by_bed_rows,
            'by_process': by_process_rows,
            'by_style': by_style_rows,
            'summary': summary,
            'fixed': self._fixed_block(target_uc, date_from, date_to),
            **head,
        })

    @staticmethod
    def _add(bucket, key, rec, count_only=False):
        item = bucket.setdefault(key, {'qty': 0, 'amount': Decimal('0')})
        item['qty'] += rec.qty
        if not count_only:
            item['amount'] += rec.price * rec.qty

    def _fixed_block(self, target_uc, date_from, date_to):
        months = _month_count(date_from, date_to)
        worked_days = (_record_period_qs(self.company, date_from, date_to,
                                         user_id=target_uc.user_id)
                       .order_by().values('registered_at__date')
                       .distinct().count())
        salary = q2(target_uc.fixed_salary or 0)
        if target_uc.fixed_type == UserCompany.FixedType.DAY:
            amount = salary * worked_days
        else:
            amount = salary * months
        return {
            'fixed_salary': str(salary),
            'fixed_type': target_uc.fixed_type,
            'fixed_type_display': target_uc.get_fixed_type_display(),
            'worked_days': worked_days,
            'months': months,
            'amount': str(q2(amount)),
        }


class CompanyEmployeesView(CompanyContextMixin, APIView):
    """公司员工薪酬卡列表（GET salary/company/employees，管理员）。

    规格 §10.1「选择员工 → 员工卡片列表」，2026-10-22 参考竞品改版补字段：
    settled（全历史已结）/ piece_amount（期间未结计件）/ fixed_amount（期间固定）
    / total_amount（计件+固定）/ process_count / no_price_count（期间）。
    unsettled 为全历史未结总额；done_qty / period_amount 为期间内完成件数与
    未结工资额（工资结算确认页用）。按未结降序。
    """

    def get(self, request):
        # Plan A：员工薪酬卡列表走 salary.view 点
        self.check_perm('salary.view')
        date_from, date_to = _range(request)
        if not date_from and not date_to:
            today = timezone.localdate()
            date_from, date_to = today.replace(day=1), _month_end(today)
        # 全历史已结/未结（员工卡两栏，record.settlement 快照口径）
        settled_map, unsettled_map = {}, {}
        for flag in (False, True):
            m = settled_map if flag else unsettled_map
            for row in (Record.objects.filter(
                    company=self.company,
                    settlement__isnull=flag)
                    .values('user')
                    .annotate(amount=Sum(F('qty') * F('price'),
                                         output_field=_DEC2))):
                m[row['user']] = q2(row['amount'] or 0)
        # 期间未结计件 + 完成工序数
        piece_map, process_map = {}, {}
        for row in (_record_period_qs(self.company, date_from, date_to,
                                      settlement__isnull=True)
                    .values('user')
                    .annotate(amount=Sum(F('qty') * F('price'),
                                         output_field=_DEC2),
                              processes=Count('process', distinct=True))):
            piece_map[row['user']] = q2(row['amount'] or 0)
            process_map[row['user']] = row['processes'] or 0
        # 无单价工序数（期间出现过 0 价登记的工序去重数）
        no_price_map = {
            row['user']: row['processes']
            for row in (_record_period_qs(self.company, date_from, date_to,
                                          price=0)
                        .order_by().values('user')
                        .annotate(processes=Count('process', distinct=True)))
        }
        period_rows = (_record_period_qs(self.company, date_from, date_to)
                       .values('user')
                       .annotate(total_qty=Sum('qty'),
                                 amount=Sum(F('qty') * F('price'),
                                            output_field=_DEC2)))
        qty_map, period_map = {}, {}
        for row in period_rows:
            # 别名不能用 qty：同调用内 amount 的 F('qty') 会解析到聚合别名
            qty_map[row['user']] = row['total_qty'] or 0
            period_map[row['user']] = q2(row['amount'] or 0)

        rows = []
        # 2026-10-18 拍板：薪酬卡并入离职成员（status 字段供前端区分），
        # 离职员工的未结余额在管理端可见、可先结算再离职
        members = (UserCompany.objects.filter(
            company=self.company,
            status__in=[UserCompany.Status.ACTIVE, UserCompany.Status.RESIGNED],
            is_suspended=False).select_related('user', 'post'))
        # 四类工资制（非 process 成员）：已结 = 结算单分员工快照累计；
        # 全历史未结 = 全历史工资 − 已结（下限 0），口径见 _outstanding_wage
        today = timezone.localdate()
        settled_detail_map = {}
        for s in Settlement.objects.filter(
                company=self.company, status=Settlement.Status.SETTLED):
            for k, v in (s.wage_detail or {}).items():
                try:
                    uid = int(k)
                except (TypeError, ValueError):
                    continue
                settled_detail_map[uid] = (settled_detail_map.get(
                    uid, Decimal('0')) + Decimal(str(v)))
        group_cache = {}
        for member in members:
            salary_type = member.salary_type or UserCompany.SalaryType.PROCESS
            if salary_type == UserCompany.SalaryType.PROCESS:
                # 存量口径（行为零变化）：计件=期间未结登记额；已结/未结走记录快照
                piece = compute_wage(self.company, member, date_from,
                                     date_to, group_cache)['amount']
                fixed = whole = commission = Decimal('0')
                unsettled = unsettled_map.get(member.user_id, Decimal('0'))
                settled = settled_map.get(member.user_id, Decimal('0'))
            else:
                w = compute_wage(self.company, member, date_from, date_to,
                                 group_cache)
                piece = Decimal('0')
                fixed = w['amount'] if salary_type == UserCompany.SalaryType.FIXED \
                    else Decimal('0')
                whole = w['amount'] if salary_type == UserCompany.SalaryType.WHOLE \
                    else Decimal('0')
                commission = (w['amount']
                              if salary_type == UserCompany.SalaryType.COMMISSION
                              else Decimal('0'))
                settled = q2(settled_detail_map.get(member.user_id,
                                                    Decimal('0')))
                w_all = compute_wage(self.company, member, None, today,
                                     group_cache)
                unsettled = max(w_all['amount'] - settled, Decimal('0'))
            rows.append({
                'user_id': member.user_id,
                'real_name': member.user.real_name,
                'post_name': member.post.name if member.post else '',
                'status': member.status,
                'status_display': member.get_status_display(),
                'salary_type': salary_type,
                'salary_type_display': member.get_salary_type_display(),
                'piece_rate': (str(q2(member.piece_rate))
                               if member.piece_rate is not None else None),
                'commission_rate': (str(q2(member.commission_rate))
                                    if member.commission_rate is not None
                                    else None),
                'unsettled': str(q2(unsettled)),
                'settled': str(q2(settled)),
                'piece_amount': str(q2(piece)),
                'fixed_amount': str(q2(fixed)),
                'whole_amount': str(q2(whole)),
                'commission_amount': str(q2(commission)),
                'total_amount': str(q2(piece + fixed + whole + commission)),
                'process_count': process_map.get(member.user_id, 0),
                'no_price_count': no_price_map.get(member.user_id, 0),
                'done_qty': qty_map.get(member.user_id, 0),
                'period_amount': str(period_map.get(member.user_id,
                                                    Decimal('0'))),
            })
        rows.sort(key=lambda r: (Decimal(r['unsettled']), r['done_qty']),
                  reverse=True)
        return Response(rows)


class EmployeeConfigView(CompanyContextMixin, APIView):
    """员工工资配置（PUT salary/employee/<uid>/config，管理员）。

    四类工资制（2026-10-23 拍板）：salary_type 四选一互斥——
    fixed 固定（配 fixed_salary + fixed_type）/ whole 整件计件（配 piece_rate>0）
    / process 工序计件（无额外参数）/ commission 计件提成（配 0<commission_rate≤100）。
    未传字段保持原值（部分更新语义与存量一致）。
    """

    def put(self, request, uid):
        # Plan A：工资配置走 salary.config 点
        self.check_perm('salary.config')
        try:
            target_uc = UserCompany.objects.get(
                company=self.company, user_id=uid,
                status=UserCompany.Status.ACTIVE)
        except UserCompany.DoesNotExist:
            raise Http404('员工不存在')
        fixed_salary = request.data.get('fixed_salary')
        fixed_type = request.data.get('fixed_type')
        salary_type = request.data.get('salary_type')
        piece_rate = request.data.get('piece_rate')
        commission_rate = request.data.get('commission_rate')
        if fixed_salary is not None:
            try:
                target_uc.fixed_salary = Decimal(str(fixed_salary))
            except Exception:
                raise ValidationError({'fixed_salary': '金额格式不正确'})
            if target_uc.fixed_salary < 0:
                raise ValidationError({'fixed_salary': '固定工资不能为负'})
        if fixed_type is not None:
            if fixed_type not in UserCompany.FixedType.values:
                raise ValidationError({'fixed_type': '固定工资类型不合法'})
            target_uc.fixed_type = fixed_type
        if salary_type is not None:
            if salary_type not in UserCompany.SalaryType.values:
                raise ValidationError({
                    'salary_type': '工资计算方式必须为 fixed/whole/process/commission'})
            target_uc.salary_type = salary_type
        if piece_rate is not None:
            try:
                target_uc.piece_rate = Decimal(str(piece_rate))
            except Exception:
                raise ValidationError({'piece_rate': '单价格式不正确'})
            if target_uc.piece_rate < 0:
                raise ValidationError({'piece_rate': '整件单价不能为负'})
        if commission_rate is not None:
            try:
                target_uc.commission_rate = Decimal(str(commission_rate))
            except Exception:
                raise ValidationError({'commission_rate': '比例格式不正确'})
            if not (Decimal('0') < target_uc.commission_rate <= Decimal('100')):
                raise ValidationError(
                    {'commission_rate': '提成比例须大于 0 且不超过 100'})
        # 四选一联动校验：切换后目标口径缺参数则拒绝（piece_rate 可与本请求同传）
        if target_uc.salary_type == UserCompany.SalaryType.WHOLE and \
                not (target_uc.piece_rate and target_uc.piece_rate > 0):
            raise ValidationError(
                {'piece_rate': '整件计件需先设置大于 0 的整件单价'})
        if target_uc.salary_type == UserCompany.SalaryType.COMMISSION and \
                not (target_uc.commission_rate
                     and Decimal('0') < target_uc.commission_rate <= Decimal('100')):
            raise ValidationError(
                {'commission_rate': '计件提成需先设置大于 0 的提成比例'})
        target_uc.save(update_fields=[
            'fixed_salary', 'fixed_type', 'salary_type', 'piece_rate',
            'commission_rate', 'updated_at'])
        return Response({
            'user_id': target_uc.user_id,
            'fixed_salary': str(q2(target_uc.fixed_salary or 0)),
            'fixed_type': target_uc.fixed_type,
            'salary_type': target_uc.salary_type,
            'salary_type_display': target_uc.get_salary_type_display(),
            'piece_rate': (str(q2(target_uc.piece_rate))
                           if target_uc.piece_rate is not None else None),
            'commission_rate': (str(q2(target_uc.commission_rate))
                                if target_uc.commission_rate is not None
                                else None),
        })


class AdjustListCreateView(CompanyContextMixin, generics.ListCreateAPIView):
    """工资调整（salary/adjust）：GET 列表（管理员全部/员工本人），POST 管理员调整。"""

    pagination_class = DefaultPagination
    serializer_class = SalaryAdjustSerializer

    def get_queryset(self):
        qs = SalaryAdjust.objects.filter(company=self.company).select_related(
            'user', 'operator')
        if _is_admin(self.user_company):
            uid = self.request.query_params.get('user_id')
            if uid:
                qs = qs.filter(user_id=uid)
        else:
            qs = qs.filter(user=self.request.user)
        date_from, date_to = _range(self.request)
        if date_from:
            qs = qs.filter(created_at__date__gte=date_from)
        if date_to:
            qs = qs.filter(created_at__date__lte=date_to)
        return qs

    def create(self, request, *args, **kwargs):
        serializer = AdjustCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        out = SalaryAdjustSerializer(serializer.instance,
                                     context=self.get_serializer_context())
        return Response(out.data, status=201)

    def perform_create(self, serializer):
        # Plan A：调整件数/工资走 salary.config 点
        self.check_perm('salary.config')
        user_id = serializer.validated_data.get('user_id')
        try:
            target = UserCompany.objects.get(
                company=self.company, user_id=user_id,
                status=UserCompany.Status.ACTIVE)
        except UserCompany.DoesNotExist:
            raise ValidationError({'user_id': '员工不存在'})
        serializer.validated_data.pop('user_id', None)
        adjust = serializer.save(company=self.company, user=target.user,
                                 operator=self.request.user)
        audit(self.company, AuditLog.Type.OTHER, operator=self.request.user,
              target_user=adjust.user,
              message=(f'工资调整：【员工：{adjust.user}】【金额：{adjust.amount}】'
                       f'事由：{adjust.reason} 操作人：{self.request.user}'))


class SettlementListCreateView(CompanyContextMixin, generics.ListCreateAPIView):
    """结算单（salary/settlements）：GET 列表（管理员），POST 发起结算。"""

    pagination_class = DefaultPagination
    serializer_class = SettlementSerializer

    def get_queryset(self):
        # Plan A：结算单列表走 salary.view 点
        self.check_perm('salary.view')
        qs = Settlement.objects.filter(company=self.company).select_related(
            'user', 'created_by')
        uid = self.request.query_params.get('user_id')
        if uid:
            qs = qs.filter(user_id=uid)
        date_from, date_to = _range(self.request)
        if date_from:
            qs = qs.filter(period_end__gte=date_from)
        if date_to:
            qs = qs.filter(period_start__lte=date_to)
        return qs

    def create(self, request, *args, **kwargs):
        # Plan A：主动结算走 salary.settle 点（高危，不获旧 coarse 覆盖）
        self.check_perm('salary.settle')
        serializer = SettlementCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        settlement = self._settle(
            serializer.validated_data['period_start'],
            serializer.validated_data['period_end'],
            serializer.validated_data.get('user_id'))
        out = SettlementSerializer(settlement,
                                   context=self.get_serializer_context())
        return Response(out.data, status=201)

    def _settle(self, period_start, period_end, user_id=None):
        """结算期间工资（全员或指定员工）：按四类工资口径逐人计薪快照到结算单。

        process 员工：金额 = 期间未结登记额，结算时圈占登记记录（防之后改价、
        防同批记录被两张结算单覆盖）；fixed/whole/commission 员工：工资基数
        不随圈占消失，靠 wage_detail 扣减同期间已覆盖金额防重复发薪。
        结算单 wage_detail = {user_id: amount} 分员工快照，供员工卡已结归属。
        """
        if period_end < period_start:
            raise ValidationError({'period_end': '结束日期不能早于起始日期'})
        members = UserCompany.objects.filter(
            company=self.company, is_deleted=False,
            status__in=[UserCompany.Status.ACTIVE, UserCompany.Status.RESIGNED],
            is_suspended=False).select_related('user')
        target = None
        if user_id:
            target = members.filter(
                user_id=user_id, status=UserCompany.Status.ACTIVE).first()
            if target is None:
                raise ValidationError({'user_id': '员工不存在'})
            members = [target]
        group_cache = {}
        wages = {}
        for member in members:
            if member.salary_type == UserCompany.SalaryType.PROCESS:
                w = compute_wage(self.company, member, period_start,
                                 period_end, group_cache)
                amount = w['amount']
            else:
                w, amount = _outstanding_wage(self.company, member,
                                              group_cache)
            if amount > 0:
                wages[member.user_id] = {**w, 'amount': amount}
        if not wages:
            raise ValidationError('该期间没有可结算的工资')
        total = q2(sum(w['amount'] for w in wages.values()))
        process_uids = [uid for uid, w in wages.items()
                        if w['salary_type'] == UserCompany.SalaryType.PROCESS]
        with transaction.atomic():
            settlement = Settlement.objects.create(
                company=self.company, user=target.user if target else None,
                period_start=period_start, period_end=period_end,
                employee_count=len(wages), amount=total,
                wage_detail={str(uid): str(w['amount'])
                             for uid, w in wages.items()},
                created_by=self.request.user)
            # process 员工登记条件圈占（settlement__isnull=True）：并发结算
            # 同一期间只有一方抢到记录，另一方 claimed=0 → 抛错回滚刚建的结算单
            if process_uids:
                lock_qs = _record_period_qs(
                    self.company, period_start, period_end,
                    settlement__isnull=True).filter(user_id__in=process_uids)
                claimed = Record.objects.filter(
                    pk__in=lock_qs.values('pk'),
                    settlement__isnull=True).update(settlement=settlement)
                if not claimed:
                    raise ValidationError('该期间没有可结算的登记记录')
        for uid, w in wages.items():
            notify(self.company, [uid], 'settlement', '工资结算通知',
                   f'您的 {period_start} ~ {period_end} 期间工资已结算，'
                   f'金额：{w["amount"]} 元')
        audit(self.company, AuditLog.Type.OTHER, operator=self.request.user,
              target_user=target.user if target else None,
              message=(f'结算工资：【期间：{period_start}~{period_end}】'
                       f'【金额：{total}】操作人：{self.request.user}'))
        return settlement


class SettlementRevokeView(CompanyContextMixin, APIView):
    """撤销结算单（POST salary/settlements/<pk>/revoke，管理员）：解锁期间登记。"""

    def post(self, request, pk):
        # Plan A：撤销结算单走 salary.settle_revoke 点
        self.check_perm('salary.settle_revoke')
        settlement = Settlement.objects.filter(
            company=self.company, pk=pk).first()
        if settlement is None:
            raise Http404('结算单不存在')
        if settlement.status == Settlement.Status.REVOKED:
            raise ValidationError('该结算单已撤销')
        paid = SettleApply.objects.filter(
            company=self.company, status=SettleApply.Status.PAID,
            period_start__lte=settlement.period_end,
            period_end__gte=settlement.period_start)
        if settlement.user_id:
            paid = paid.filter(user_id=settlement.user_id)
        if paid.exists():
            raise ValidationError('该期间工资已发放，不可撤销结算')
        records = Record.objects.filter(settlement=settlement)
        user_ids = list(records.values_list('user_id', flat=True).distinct())
        with transaction.atomic():
            records.update(settlement=None)
            settlement.status = Settlement.Status.REVOKED
            settlement.save(update_fields=['status', 'updated_at'])
        notify(self.company, user_ids, 'settlement_revoke', '结算撤销通知',
               f'您的 {settlement.period_start} ~ {settlement.period_end} '
               f'期间结算已撤销，登记记录恢复为未结算')
        audit(self.company, AuditLog.Type.OTHER, operator=self.request.user,
              target_user=settlement.user,
              message=(f'撤销结算单#{settlement.pk}：'
                       f'【期间：{settlement.period_start}~{settlement.period_end}】'
                       f'【金额：{settlement.amount}】操作人：{self.request.user}'))
        return Response({'id': settlement.pk, 'status': settlement.status})


class SettleApplyListCreateView(CompanyContextMixin, generics.ListCreateAPIView):
    """结算申请（salary/settle-applies）：GET 列表（管理员全部/员工本人），POST 发起申请。"""

    pagination_class = DefaultPagination
    serializer_class = SettleApplySerializer

    def get_serializer_context(self):
        """带上公司管理员姓名（待谁审批提示用，一次查询整页复用，避免逐行 N+1）。"""
        ctx = super().get_serializer_context()
        ctx['admin_names'] = list(
            UserCompany.objects.filter(
                company=self.company, status=UserCompany.Status.ACTIVE,
                post__is_admin=True, post__is_deleted=False)
            .select_related('user')
            .values_list('user__real_name', flat=True))
        return ctx

    def get_queryset(self):
        qs = SettleApply.objects.filter(company=self.company).select_related(
            'applicant', 'user', 'reviewer', 'paid_by')
        if _is_admin(self.user_company):
            uid = self.request.query_params.get('user_id')
            if uid:
                qs = qs.filter(user_id=uid)
            status_param = self.request.query_params.get('status')
            if status_param not in (None, ''):
                try:
                    qs = qs.filter(status=int(status_param))
                except (TypeError, ValueError):
                    raise ValidationError({'status': '状态参数不合法'})
        else:
            qs = qs.filter(user=self.request.user)
        date_from, date_to = _range(self.request)
        if date_from:
            qs = qs.filter(period_end__gte=date_from)
        if date_to:
            qs = qs.filter(period_start__lte=date_to)
        return qs

    def create(self, request, *args, **kwargs):
        serializer = SettleApplyCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        out = SettleApplySerializer(serializer.instance,
                                    context=self.get_serializer_context())
        return Response(out.data, status=201)

    def perform_create(self, serializer):
        period_start = serializer.validated_data['period_start']
        period_end = serializer.validated_data['period_end']
        if period_end < period_start:
            raise ValidationError({'period_end': '结束日期不能早于起始日期'})
        # 防重复申请：同一员工同一公司存在「待审核/待发放」的申请时不再收新单
        # （已发放/已拒绝/已作废为终态，可再次申请）
        active = SettleApply.objects.filter(
            company=self.company, user=self.request.user,
            status__in=(SettleApply.Status.PENDING, SettleApply.Status.APPROVED),
        ).order_by('-id').first()
        if active:
            raise ValidationError({
                'detail': f'您已有一笔结算申请处理中（当前状态：{active.get_status_display()}），'
                          '请等待审核发放完成后再申请'
            })
        amount = Decimal('0')
        if self.user_company is not None:
            # 四类工资口径：申请金额按本人工资方式计算
            # （非 process：全历史未结口径，见 _outstanding_wage）
            if self.user_company.salary_type == UserCompany.SalaryType.PROCESS:
                w = compute_wage(self.company, self.user_company,
                                 period_start, period_end, {})
                amount = w['amount']
            else:
                _, amount = _outstanding_wage(self.company,
                                              self.user_company, {})
        # 未结工资为 0 不允许申请（2026-10-23 用户需求）：结算单金额为 0 无意义
        if amount <= 0:
            raise ValidationError({'detail': '该期间未结工资为 0，无需申请结算'})
        serializer.save(company=self.company, applicant=self.request.user,
                        user=self.request.user, amount=amount)


class SettleApplyActionView(CompanyContextMixin, APIView):
    """结算申请动作基类：按 action 分派到 _review/_pay/_void。"""

    action = None

    def _get_apply(self, pk):
        apply = SettleApply.objects.filter(
            company=self.company, pk=pk).select_related(
            'applicant', 'user').first()
        if apply is None:
            raise Http404('结算申请不存在')
        return apply

    def put(self, request, pk):
        return getattr(self, '_' + self.action)(pk)

    def get_serializer_context(self):
        # APIView 没有 GenericAPIView 的上下文方法，这里补齐供序列化器使用
        return {'request': self.request, 'view': self, 'format': None}


class SettleApplyReviewView(SettleApplyActionView):
    """审核结算申请（PUT .../review，管理员）：action=approve/reject。"""

    action = 'review'

    def _review(self, pk):
        # Plan A：审核申请走 salary.review 点
        self.check_perm('salary.review')
        apply = self._get_apply(pk)
        if apply.status != SettleApply.Status.PENDING:
            raise ValidationError('仅待审核状态可审核')
        act = self.request.data.get('action')
        if act == 'approve':
            apply.status = SettleApply.Status.APPROVED
        elif act == 'reject':
            apply.status = SettleApply.Status.REJECTED
            apply.reject_reason = str(self.request.data.get('reason') or '')[:200]
        else:
            raise ValidationError({'action': 'action 必须为 approve/reject'})
        apply.reviewer = self.request.user
        apply.reviewed_at = timezone.now()
        apply.save(update_fields=['status', 'reviewer', 'reviewed_at',
                                  'reject_reason', 'updated_at'])
        if act == 'approve':
            message = (f'您 {apply.period_start} ~ {apply.period_end} '
                       f'期间的结算申请已通过，等待发放')
        else:
            message = (f'您 {apply.period_start} ~ {apply.period_end} '
                       f'期间的结算申请被拒绝：{apply.reject_reason or "无"}')
        notify(self.company, [apply.user_id], 'settle_review',
               '结算申请审核结果', message)
        return Response(SettleApplySerializer(
            apply, context=self.get_serializer_context()).data)


class SettleApplyPayView(SettleApplyActionView):
    """发放结算申请（PUT .../pay，管理员）：生成结算单并锁定登记。"""

    action = 'pay'

    def _pay(self, pk):
        # Plan A：发放申请走 salary.review 点
        self.check_perm('salary.review')
        apply = self._get_apply(pk)
        if apply.status != SettleApply.Status.APPROVED:
            raise ValidationError('仅待发放的申请可发放')
        # 四类工资口径：发放金额按本人工资方式重算（申请时快照仅供展示）；
        # 申请创建后记录可能已被全员结算圈占（重算为 0 → 拒绝发放）
        member = UserCompany.objects.filter(
            company=self.company, user_id=apply.user_id, is_deleted=False,
            status__in=[UserCompany.Status.ACTIVE, UserCompany.Status.RESIGNED],
            is_suspended=False).first()
        amount = Decimal('0')
        salary_type = (member.salary_type if member
                       else UserCompany.SalaryType.PROCESS)
        if member is not None:
            if salary_type == UserCompany.SalaryType.PROCESS:
                w = compute_wage(self.company, member, apply.period_start,
                                 apply.period_end, {})
                amount = w['amount']
            else:
                _, amount = _outstanding_wage(self.company, member, {})
        if amount <= 0:
            raise ValidationError('该期间已无可结算的工资')
        with transaction.atomic():
            settlement = Settlement.objects.create(
                company=self.company, user=apply.user,
                period_start=apply.period_start, period_end=apply.period_end,
                employee_count=1, amount=amount,
                wage_detail={str(apply.user_id): str(amount)},
                created_by=self.request.user)
            # process 员工圈占登记记录（防之后改价）；非 process 不圈占
            if salary_type == UserCompany.SalaryType.PROCESS:
                records = _record_period_qs(
                    self.company, apply.period_start, apply.period_end,
                    user_id=apply.user_id, settlement__isnull=True)
                claimed = Record.objects.filter(
                    pk__in=records.values('pk'),
                    settlement__isnull=True).update(settlement=settlement)
                if not claimed:
                    raise ValidationError('该期间已无可结算的登记记录')
            apply.status = SettleApply.Status.PAID
            apply.paid_by = self.request.user
            apply.paid_at = timezone.now()
            apply.payer = str(self.request.data.get('payer') or '')[:64]
            apply.save(update_fields=['status', 'paid_by', 'paid_at', 'payer',
                                      'updated_at'])
        notify(self.company, [apply.user_id], 'settle_paid', '工资发放通知',
               f'您 {apply.period_start} ~ {apply.period_end} 期间的工资'
               f'已发放，金额：{amount} 元')
        audit(self.company, AuditLog.Type.OTHER, operator=self.request.user,
              target_user=apply.user,
              message=(f'发放工资：【员工：{apply.user}】【金额：{amount}】'
                       f'操作人：{self.request.user}'))
        return Response(SettleApplySerializer(
            apply, context=self.get_serializer_context()).data)


class SettleApplyVoidView(SettleApplyActionView):
    """作废结算申请（PUT .../void）：管理员可作废待审核/待发放，员工仅本人待审核。"""

    action = 'void'

    def _void(self, pk):
        apply = self._get_apply(pk)
        if not _is_admin(self.user_company) and not has_perm(self.user_company, 'salary.review'):
            if apply.user_id != self.request.user.id:
                raise PermissionDenied('只能作废本人申请')
            if apply.status != SettleApply.Status.PENDING:
                raise ValidationError('仅待审核状态可由本人作废')
        else:
            if apply.status not in (SettleApply.Status.PENDING,
                                    SettleApply.Status.APPROVED):
                raise ValidationError('仅待审核/待发放状态可作废')
        apply.status = SettleApply.Status.VOIDED
        apply.void_reason = str(self.request.data.get('reason') or '')[:200]
        apply.save(update_fields=['status', 'void_reason', 'updated_at'])
        return Response(SettleApplySerializer(
            apply, context=self.get_serializer_context()).data)


class SummaryCompletionView(CompanyContextMixin, APIView):
    """完成情况汇总（GET salary/summary/completion，管理员）：款式→工序→菲票三级钻取。

    层一：期间内全部款式汇总；传 style_id 得工序层；再传 process_id 得
    菲票明细 + 颜色/尺码矩阵 + 员工汇总。支持 user_id / customer_id 过滤。
    """

    def get(self, request):
        # Plan A：完成情况汇总走 salary.view 点
        self.check_perm('salary.view')
        date_from, date_to = _range(request)
        period = _record_period_qs(self.company, date_from, date_to)
        uid = request.query_params.get('user_id')
        if uid:
            period = period.filter(user_id=uid)
        customer_id = request.query_params.get('customer_id')
        if customer_id:
            period = period.filter(ticket__production__customer_id=customer_id)

        styles = (period.order_by()
                  .values('ticket__production__style_id',
                          'ticket__production__style__style_no',
                          'ticket__production__style__name')
                  .annotate(total_qty=Sum('qty'),
                            amount=Sum(F('qty') * F('price'), output_field=_DEC2),
                            ticket_count=Count('ticket', distinct=True),
                            process_count=Count('process', distinct=True))
                  .order_by('-total_qty'))
        style_rows = []
        for row in styles:
            qty = row['total_qty'] or 0
            amount = q2(row['amount'] or 0)
            style_rows.append({
                'style_id': row['ticket__production__style_id'],
                'style_no': row['ticket__production__style__style_no'],
                'style_name': row['ticket__production__style__name'],
                'qty': qty,
                'avg_price': str(q2(amount / qty)) if qty else '0.00',
                'amount': str(amount),
                'ticket_count': row['ticket_count'] or 0,
                'process_count': row['process_count'] or 0,
            })

        response = {
            'period': {'date_from': date_from, 'date_to': date_to},
            'styles': style_rows,
            'processes': [],
            'matrix': [],
            'employees': [],
        }
        style_id = request.query_params.get('style_id')
        if not style_id:
            return Response(response)

        base = period.filter(ticket__production__style_id=style_id)
        proc_rows = (base.order_by()
                     .values('process_id', 'process__name')
                     .annotate(total_qty=Sum('qty'),
                               amount=Sum(F('qty') * F('price'),
                                          output_field=_DEC2),
                               ticket_count=Count('ticket', distinct=True),
                               users=Count('user', distinct=True))
                     .order_by('-total_qty'))
        process_rows = []
        for row in proc_rows:
            qty = row['total_qty'] or 0
            amount = q2(row['amount'] or 0)
            process_rows.append({
                'process_id': row['process_id'],
                'name': row['process__name'],
                'qty': qty,
                'avg_price': str(q2(amount / qty)) if qty else '0.00',
                'amount': str(amount),
                'ticket_count': row['ticket_count'] or 0,
                'users': row['users'] or 0,
            })
        response['processes'] = process_rows

        process_id = request.query_params.get('process_id')
        if not process_id:
            return Response(response)

        records = base.filter(process_id=process_id).select_related(
            'ticket', 'user', 'process')
        matrix, employees, tickets = {}, {}, []
        for rec in records:
            color = rec.ticket.color or '-'
            size = rec.ticket.size or '-'
            sizes = matrix.setdefault(color, {})
            sizes[size] = sizes.get(size, 0) + rec.qty
            emp = employees.setdefault(rec.user_id, {
                'user_id': rec.user_id,
                'user_name': rec.user.real_name,
                'qty': 0, 'amount': Decimal('0')})
            emp['qty'] += rec.qty
            emp['amount'] += rec.price * rec.qty
            tickets.append({
                'ticket_id': rec.ticket_id,
                'bed_no': rec.ticket.bed_no,
                'bundle_no': rec.ticket.bundle_no,
                'color': rec.ticket.color,
                'size': rec.ticket.size,
                'qty': rec.qty,
                'price': str(rec.price),
                'amount': str(q2(rec.price * rec.qty)),
            })
        response['matrix'] = [
            {'color': color,
             'sizes': [{'size': size, 'qty': qty}
                       for size, qty in sorted(sizes.items())]}
            for color, sizes in matrix.items()]
        employee_rows = sorted(employees.values(), key=lambda e: -e['amount'])
        for emp in employee_rows:
            amount = q2(emp['amount'])
            emp['amount'] = str(amount)
            emp['avg_price'] = str(q2(amount / emp['qty'])) \
                if emp['qty'] else '0.00'
        response['employees'] = employee_rows
        response['tickets'] = tickets
        return Response(response)
