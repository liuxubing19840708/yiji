# -*- coding: utf-8 -*-
"""已校验 QueryPlan → Django ORM；公司范围在此强制附加。"""
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.db.models import (
    Avg, Case, Count, DecimalField, ExpressionWrapper, F, IntegerField, Max, Min,
    OuterRef, Q, Subquery, Sum, Value, When,
)
from django.db.models.functions import Coalesce, Greatest, NullIf, TruncDate, TruncMonth, TruncWeek
from django.utils import timezone

from apps.catalog.models import Ticket
from apps.orders.models import OrderItem
from apps.records.models import Record
from apps.salary.models import SalaryPayment, SettleApply, Settlement
from apps.shipment.models import Shipment
from apps.finance.models import CashEntry, ReceiptLine, SupplierPayment
from apps.warehouse.models import (
    IssueItem, MaterialIssue, Receipt, ReceiptItem, StockInItem,
)

AUDITED_SHIPMENT_STATUSES = (
    Shipment.Status.CONFIRMED, Shipment.Status.PARTIAL,
    Shipment.Status.SHIPPED, Shipment.Status.SIGNED,
)

MONEY_FIELD = DecimalField(max_digits=18, decimal_places=3)
QUANTITY_FIELD = DecimalField(max_digits=18, decimal_places=2)


@dataclass
class QueryResult:
    title: str
    summary: str
    note: str
    columns: list
    rows: list
    total_count: int
    truncated: bool
    scope: list
    comparison: dict | None = None

    @property
    def row_count(self):
        return len(self.rows)


def _local_day(value, offset=0):
    parsed = date_from_iso(value) + timedelta(days=offset)
    return timezone.make_aware(
        datetime.combine(parsed, time.min), timezone.get_current_timezone())


def date_from_iso(value):
    return datetime.strptime(value, '%Y-%m-%d').date()


def _apply_filter(qs, company, field, operator, value):
    alias = f'filter_{field.key}'
    qs = qs.annotate(**{alias: field.expression(company)})
    if field.type == 'datetime':
        if operator == 'between':
            return qs.filter(**{
                f'{alias}__gte': _local_day(value[0]),
                f'{alias}__lt': _local_day(value[1], 1),
            })
        if operator == 'gte':
            return qs.filter(**{f'{alias}__gte': _local_day(value)})
        if operator == 'lte':
            return qs.filter(**{f'{alias}__lt': _local_day(value, 1)})
    if field.type == 'date' and operator == 'between':
        return qs.filter(**{
            f'{alias}__gte': value[0], f'{alias}__lte': value[1],
        })
    if operator == 'eq':
        return qs.filter(**{alias: value})
    if operator == 'neq':
        return qs.filter(~Q(**{alias: value}))
    if operator == 'in':
        return qs.filter(**{f'{alias}__in': value})
    if operator == 'contains':
        return qs.filter(**{f'{alias}__icontains': value})
    if operator == 'isnull':
        return qs.filter(**{f'{alias}__isnull': value})
    if operator in ('gte', 'lte'):
        return qs.filter(**{f'{alias}__{operator}': value})
    raise ValueError(f'未处理的操作符：{operator}')


def _related_scalar(queryset, group_key, expression, output_field=None):
    """一对多关系先按主单行聚合，避免多个指标 JOIN 后相互放大。"""
    rows = (queryset.order_by().values(group_key)
            .annotate(_report_value=expression).values('_report_value'))
    return Coalesce(
        Subquery(rows[:1]), Value(0), output_field=output_field or IntegerField())


def _zero_sum(expression):
    return Coalesce(Sum(expression), Value(0), output_field=IntegerField())


def _decimal_total(expression):
    return Coalesce(
        Sum(expression), Value(0, output_field=QUANTITY_FIELD),
        output_field=QUANTITY_FIELD)


def _money_total(expression):
    return Coalesce(
        Sum(expression), Value(0, output_field=MONEY_FIELD),
        output_field=MONEY_FIELD)


_KG_UNITS = ('kg', 'KG', '公斤', '千克')
_M_UNITS = ('米', 'm', 'M')


def _purchase_receipt_qty(company, field):
    """按采购行预聚合已确认暂收，避免多行 ReceiptItem 放大采购数量。"""
    rows = ReceiptItem.objects.filter(
        receipt__company=company, receipt__is_deleted=False,
        receipt__status=Receipt.Status.CONFIRMED,
        purchase_item_id=OuterRef('pk'))
    return _related_scalar(rows, 'purchase_item_id', Sum(field), QUANTITY_FIELD)


def _purchase_conversion_condition():
    same_unit = (
        Q(unit__in=_KG_UNITS, material__unit__in=_KG_UNITS)
        | Q(unit__in=_M_UNITS, material__unit__in=_M_UNITS)
    )
    conversion_ready = Q(material__weight_gsm__gt=0, material__width_cm__gt=0)
    cross_unit = (
        Q(unit__in=_KG_UNITS, material__unit__in=_M_UNITS)
        | Q(unit__in=_M_UNITS, material__unit__in=_KG_UNITS)
    )
    return same_unit | (cross_unit & conversion_ready)


def _purchase_main_qty():
    """当前物料参数下折算主单位；无法换算时返回 NULL，绝不按零处理。"""
    factor = ExpressionWrapper(
        F('material__weight_gsm') * F('material__width_cm') / Value(100000.0),
        output_field=DecimalField(max_digits=24, decimal_places=8))
    return Case(
        When(_purchase_conversion_condition() & (
            Q(unit__in=_KG_UNITS, material__unit__in=_KG_UNITS)
            | Q(unit__in=_M_UNITS, material__unit__in=_M_UNITS)
        ), then=F('qty')),
        When(Q(unit__in=_KG_UNITS, material__unit__in=_M_UNITS,
               material__weight_gsm__gt=0, material__width_cm__gt=0),
             then=ExpressionWrapper(F('qty') / factor, output_field=QUANTITY_FIELD)),
        When(Q(unit__in=_M_UNITS, material__unit__in=_KG_UNITS,
               material__weight_gsm__gt=0, material__width_cm__gt=0),
             then=ExpressionWrapper(F('qty') * factor, output_field=QUANTITY_FIELD)),
        default=Value(None), output_field=QUANTITY_FIELD)


def _material_conversion_condition(unit_path):
    """同单位直接覆盖；公斤/米同族及跨族换算与仓库服务口径一致。"""
    same_exact = Q(**{unit_path: F('material__unit')})
    same_family = (
        Q(**{f'{unit_path}__in': _KG_UNITS, 'material__unit__in': _KG_UNITS})
        | Q(**{f'{unit_path}__in': _M_UNITS, 'material__unit__in': _M_UNITS})
    )
    cross_unit = (
        Q(**{f'{unit_path}__in': _KG_UNITS, 'material__unit__in': _M_UNITS})
        | Q(**{f'{unit_path}__in': _M_UNITS, 'material__unit__in': _KG_UNITS})
    )
    conversion_ready = Q(material__weight_gsm__gt=0, material__width_cm__gt=0)
    return same_exact | same_family | (cross_unit & conversion_ready)


def _material_main_qty(unit_path):
    """按当前物料参数折算；不能换算返回 NULL，聚合时不得当成零库存。"""
    factor = ExpressionWrapper(
        F('material__weight_gsm') * F('material__width_cm') / Value(100000.0),
        output_field=DecimalField(max_digits=24, decimal_places=8))
    return Case(
        When(_material_conversion_condition(unit_path) & ~(
            Q(**{f'{unit_path}__in': _KG_UNITS, 'material__unit__in': _M_UNITS})
            | Q(**{f'{unit_path}__in': _M_UNITS, 'material__unit__in': _KG_UNITS})
        ), then=F('qty')),
        When(Q(**{f'{unit_path}__in': _KG_UNITS, 'material__unit__in': _M_UNITS,
                  'material__weight_gsm__gt': 0, 'material__width_cm__gt': 0}),
             then=ExpressionWrapper(F('qty') / factor, output_field=QUANTITY_FIELD)),
        When(Q(**{f'{unit_path}__in': _M_UNITS, 'material__unit__in': _KG_UNITS,
                  'material__weight_gsm__gt': 0, 'material__width_cm__gt': 0}),
             then=ExpressionWrapper(F('qty') * factor, output_field=QUANTITY_FIELD)),
        default=Value(None), output_field=QUANTITY_FIELD)


def _material_inbound(company, as_of=None):
    rows = StockInItem.objects.filter(
        stock_in__company=company, stock_in__is_deleted=False,
        material_id=OuterRef('pk'))
    if as_of:
        rows = rows.filter(stock_in__in_at__lt=_local_day(as_of, 1))
    return _related_scalar(
        rows, 'material_id', Sum(_material_main_qty('unit')), QUANTITY_FIELD)


def _material_issue(company, issue_type, as_of=None):
    rows = IssueItem.objects.filter(
        issue__company=company, issue__is_deleted=False,
        issue__status=MaterialIssue.Status.CONFIRMED,
        issue__type=issue_type, material_id=OuterRef('pk'))
    if as_of:
        rows = rows.filter(issue__issue_at__lt=_local_day(as_of, 1))
    return _related_scalar(
        rows, 'material_id', Sum(_material_main_qty('lot__unit')), QUANTITY_FIELD)


def _material_uncovered_flows(company, as_of=None):
    """不可换算的当前留存流水行数；不按零库存掩盖，也不混入可换算数量。"""
    inbound = StockInItem.objects.filter(
        stock_in__company=company, stock_in__is_deleted=False,
        material_id=OuterRef('pk'))
    issues = IssueItem.objects.filter(
        issue__company=company, issue__is_deleted=False,
        issue__status=MaterialIssue.Status.CONFIRMED,
        material_id=OuterRef('pk'))
    if as_of:
        inbound = inbound.filter(stock_in__in_at__lt=_local_day(as_of, 1))
        issues = issues.filter(issue__issue_at__lt=_local_day(as_of, 1))
    inbound_pending = Case(
        When(_material_conversion_condition('unit'), then=Value(0)),
        default=Value(1), output_field=IntegerField())
    issue_pending = Case(
        When(_material_conversion_condition('lot__unit'), then=Value(0)),
        default=Value(1), output_field=IntegerField())
    return (
        _related_scalar(inbound, 'material_id', Sum(inbound_pending), IntegerField())
        + _related_scalar(issues, 'material_id', Sum(issue_pending), IntegerField())
    )


def _lot_inbound(company):
    """沿用需求汇总的入库流水口径：含草稿主单，排除软删主单和明细。"""
    rows = StockInItem.objects.filter(
        stock_in__company=company, stock_in__is_deleted=False,
        lot_id=OuterRef('pk'))
    return _related_scalar(rows, 'lot_id', Sum('qty'), QUANTITY_FIELD)


def _lot_issue(company, issue_type):
    rows = IssueItem.objects.filter(
        issue__company=company, issue__is_deleted=False,
        issue__status=MaterialIssue.Status.CONFIRMED,
        issue__type=issue_type, lot_id=OuterRef('pk'))
    return _related_scalar(rows, 'lot_id', Sum('qty'), QUANTITY_FIELD)


def _metric_expression(metric, aggregation, company, as_of=None):
    key = metric.key
    special = metric.special
    if metric.ratio_numerator:
        from dataclasses import replace
        numerator = _metric_expression(
            replace(metric, special=metric.ratio_numerator, ratio_numerator=''),
            'sum', company, as_of)
        denominator = _metric_expression(
            replace(metric, special=metric.ratio_denominator, ratio_numerator=''),
            'sum', company, as_of)
        return ExpressionWrapper(
            numerator * Value(1.0) / NullIf(denominator, Value(0)),
            output_field=DecimalField(max_digits=18, decimal_places=6))
    if special == 'count_id':
        return Count('id')
    if special == 'audited_shipment_count':
        return Count('id', filter=Q(status__in=AUDITED_SHIPMENT_STATUSES))
    if special == 'shipped_qty':
        return Coalesce(
            Sum('total_qty', filter=Q(status__in=AUDITED_SHIPMENT_STATUSES)),
            Value(0), output_field=IntegerField())
    if special == 'lot_inbound_qty':
        return _decimal_total(_lot_inbound(company))
    if special == 'lot_issued_qty':
        return _decimal_total(_lot_issue(company, MaterialIssue.Type.ISSUE))
    if special == 'lot_returned_qty':
        return _decimal_total(_lot_issue(company, MaterialIssue.Type.RETURN))
    if special == 'lot_current_stock_qty':
        net = (_lot_inbound(company)
               - _lot_issue(company, MaterialIssue.Type.ISSUE)
               + _lot_issue(company, MaterialIssue.Type.RETURN))
        return _decimal_total(net)
    if special == 'material_inbound_qty':
        return _decimal_total(_material_inbound(company, as_of))
    if special == 'material_issued_qty':
        return _decimal_total(_material_issue(
            company, MaterialIssue.Type.ISSUE, as_of))
    if special == 'material_returned_qty':
        return _decimal_total(_material_issue(
            company, MaterialIssue.Type.RETURN, as_of))
    if special == 'material_current_stock_qty':
        net = (_material_inbound(company, as_of)
               - _material_issue(company, MaterialIssue.Type.ISSUE, as_of)
               + _material_issue(company, MaterialIssue.Type.RETURN, as_of))
        return _decimal_total(net)
    if special == 'material_uncovered_flow_count':
        return _zero_sum(_material_uncovered_flows(company, as_of))
    if special == 'purchase_order_count':
        return Count('order', distinct=True)
    if special in {
        'purchase_confirmed_arrived_qty', 'purchase_confirmed_qualified_qty',
        'purchase_confirmed_rejected_qty',
    }:
        source_field = {
            'purchase_confirmed_arrived_qty': 'arrived_qty',
            'purchase_confirmed_qualified_qty': 'qualified_qty',
            'purchase_confirmed_rejected_qty': 'rejected_qty',
        }[special]
        return _decimal_total(_purchase_receipt_qty(company, source_field))
    if special == 'purchase_pending_receipt_qty':
        pending = Greatest(
            F('qty') - _purchase_receipt_qty(company, 'arrived_qty'),
            Value(0, output_field=QUANTITY_FIELD), output_field=QUANTITY_FIELD)
        return _decimal_total(pending)
    if special == 'purchase_convertible_amount':
        amount = ExpressionWrapper(
            _purchase_main_qty() * F('price'), output_field=MONEY_FIELD)
        return _money_total(amount)
    if special == 'purchase_amount_pending_count':
        pending = Case(
            When(_purchase_conversion_condition(), then=Value(0)),
            default=Value(1), output_field=IntegerField())
        return _zero_sum(pending)
    if special == 'salary_settled_count':
        return Count('id', filter=Q(status=Settlement.Status.SETTLED))
    if special == 'salary_settled_amount':
        return Coalesce(
            Sum('amount', filter=Q(status=Settlement.Status.SETTLED)),
            Value(0, output_field=MONEY_FIELD), output_field=MONEY_FIELD)
    if special == 'salary_revoked_amount':
        return Coalesce(
            Sum('amount', filter=Q(status=Settlement.Status.REVOKED)),
            Value(0, output_field=MONEY_FIELD), output_field=MONEY_FIELD)
    if special == 'salary_settled_employee_count':
        return Coalesce(
            Sum('employee_count', filter=Q(status=Settlement.Status.SETTLED)),
            Value(0), output_field=IntegerField())
    if special.startswith('salary_apply_'):
        status = {
            'salary_apply_pending_count': SettleApply.Status.PENDING,
            'salary_apply_approved_count': SettleApply.Status.APPROVED,
            'salary_apply_paid_count': SettleApply.Status.PAID,
            'salary_apply_rejected_count': SettleApply.Status.REJECTED,
            'salary_apply_voided_count': SettleApply.Status.VOIDED,
        }[special]
        return Count('id', filter=Q(status=status))
    if special == 'salary_payment_count':
        return Count('id', filter=Q(status=SalaryPayment.Status.ACTIVE))
    if special == 'salary_payment_amount':
        return Coalesce(Sum('amount', filter=Q(status=SalaryPayment.Status.ACTIVE)),
                        Value(0, output_field=MONEY_FIELD), output_field=MONEY_FIELD)
    if special == 'salary_payment_employee_count':
        return Count('employee', distinct=True,
                     filter=Q(status=SalaryPayment.Status.ACTIVE))
    if special == 'finance_receivable_amount':
        return _money_total(F('amount'))
    if special == 'finance_received_amount':
        return _money_total(F('received_amount'))
    if special == 'finance_outstanding_amount':
        return _money_total(ExpressionWrapper(
            F('amount') - F('received_amount'), output_field=MONEY_FIELD))
    if special == 'finance_payable_amount':
        return _money_total(F('amount'))
    if special == 'finance_payable_paid':
        return _money_total(F('paid_amount'))
    if special == 'finance_payable_outstanding':
        return _money_total(ExpressionWrapper(
            F('amount') - F('paid_amount'), output_field=MONEY_FIELD))
    if special == 'supplier_payment_count':
        return Count('id', filter=Q(status=SupplierPayment.Status.ACTIVE))
    if special == 'supplier_payment_amount':
        return Coalesce(Sum('amount', filter=Q(status=SupplierPayment.Status.ACTIVE)),
                        Value(0, output_field=MONEY_FIELD), output_field=MONEY_FIELD)
    if special in ('cash_expense_amount', 'cash_income_amount'):
        direction = (CashEntry.Direction.EXPENSE if special == 'cash_expense_amount'
                     else CashEntry.Direction.INCOME)
        return Coalesce(Sum('amount', filter=Q(
            status=CashEntry.Status.ACTIVE, direction=direction)),
            Value(0, output_field=MONEY_FIELD), output_field=MONEY_FIELD)
    if special == 'cash_net_amount':
        return _money_total(Case(
            When(Q(status=CashEntry.Status.ACTIVE,
                   direction=CashEntry.Direction.INCOME), then=F('amount')),
            When(Q(status=CashEntry.Status.ACTIVE,
                   direction=CashEntry.Direction.EXPENSE), then=-F('amount')),
            default=Value(0, output_field=MONEY_FIELD), output_field=MONEY_FIELD))
    if special == 'order_item_qty':
        rows = OrderItem.objects.filter(
            order__company=company, order_id=OuterRef('pk'))
        return _zero_sum(_related_scalar(rows, 'order_id', Sum('qty')))
    if special == 'order_cut_qty':
        rows = Ticket.objects.filter(
            company=company, production__is_deleted=False,
            production__order_id=OuterRef('pk'))
        return _zero_sum(_related_scalar(rows, 'production__order_id', Sum('qty')))
    if special == 'order_registered_qty':
        rows = Record.objects.filter(
            company=company, ticket__is_deleted=False,
            ticket__production__is_deleted=False,
            ticket__production__order_id=OuterRef('pk'))
        return _zero_sum(_related_scalar(
            rows, 'ticket__production__order_id', Sum('qty')))
    if special == 'production_registered_qty':
        rows = Record.objects.filter(
            company=company, ticket__is_deleted=False,
            ticket__production_id=OuterRef('pk'))
        return _zero_sum(_related_scalar(rows, 'ticket__production_id', Sum('qty')))
    if special == 'production_ticket_count':
        rows = Ticket.objects.filter(
            company=company, production_id=OuterRef('pk'))
        return Coalesce(Sum(_related_scalar(rows, 'production_id', Count('id'))),
                        Value(0), output_field=IntegerField())
    if metric.path:
        aggregate = {'sum': Sum, 'avg': Avg, 'min': Min, 'max': Max}[aggregation]
        expression = aggregate(metric.path)
        if aggregation == 'sum':
            output_field = (
                MONEY_FIELD if metric.type == 'money'
                else QUANTITY_FIELD if metric.type == 'decimal'
                else IntegerField())
            return Coalesce(
                expression, Value(0, output_field=output_field),
                output_field=output_field)
        return expression
    if key == 'registered_qty':
        aggregate = {'sum': Sum, 'avg': Avg, 'min': Min, 'max': Max}[aggregation]
        expression = aggregate('qty')
        if aggregation == 'sum':
            expression = Coalesce(expression, Value(0), output_field=IntegerField())
        return expression
    if key == 'estimated_wage':
        amount = ExpressionWrapper(F('qty') * F('price'), output_field=MONEY_FIELD)
        return Coalesce(Sum(amount), Value(0, output_field=MONEY_FIELD),
                        output_field=MONEY_FIELD)
    if key == 'record_count':
        return Count('id')
    if key == 'employee_count':
        return Count('user', distinct=True)
    if key == 'ticket_count':
        return Count('ticket', distinct=True)
    raise ValueError(f'未处理的指标：{key}')


def _metric_type(metric, aggregation):
    if metric.key == 'registered_qty' and aggregation == 'avg':
        return 'decimal'
    return metric.type


def _cash_flow_sources(company):
    """五类事实分别在 ORM 内聚合，再按公开维度合并，避免制造重复账务表。"""
    return (
        ('customer_receipts', 'income', 'received_at', ReceiptLine.objects.filter(
            company=company, receivable__is_deleted=False, is_deleted=False)),
        ('other_income', 'income', 'occurred_at', CashEntry.objects.filter(
            company=company, status=CashEntry.Status.ACTIVE,
            direction=CashEntry.Direction.INCOME)),
        ('salary_payments', 'expense', 'paid_at', SalaryPayment.objects.filter(
            company=company, status=SalaryPayment.Status.ACTIVE)),
        ('supplier_payments', 'expense', 'paid_at', SupplierPayment.objects.filter(
            company=company, status=SupplierPayment.Status.ACTIVE)),
        ('expenses', 'expense', 'occurred_at', CashEntry.objects.filter(
            company=company, status=CashEntry.Status.ACTIVE,
            direction=CashEntry.Direction.EXPENSE)),
    )


def _cash_choice_match(actual, operator, expected):
    if operator == 'eq':
        return actual == expected
    if operator == 'neq':
        return actual != expected
    return actual in expected


def _execute_cash_flow_plan(plan, dataset, company):
    from dataclasses import replace

    sources = list(_cash_flow_sources(company))
    for item in plan['filters']:
        key, operator, value = item['field'], item['operator'], item['value']
        if key in ('flow_type', 'direction'):
            index = 0 if key == 'flow_type' else 1
            sources = [source for source in sources
                       if _cash_choice_match(source[index], operator, value)]

    calendar = {
        'occurred_day': TruncDate, 'occurred_week': TruncWeek,
        'occurred_month': TruncMonth,
    }
    groups = {}
    for flow_type, direction, time_path, original_qs in sources:
        qs = original_qs.order_by()
        for item in plan['filters']:
            if item['field'] == 'occurred_at':
                field = replace(dataset.field_map['occurred_at'], path=time_path)
                qs = _apply_filter(qs, company, field, item['operator'], item['value'])
        aliases = {}
        annotations = {}
        for key in plan['dimensions']:
            if key in calendar:
                aliases[key] = f'report_{key}'
                annotations[aliases[key]] = calendar[key](
                    time_path, tzinfo=timezone.get_current_timezone())
        aggregated = (qs.annotate(**annotations).values(*aliases.values())
                      .annotate(amount=Sum('amount')).order_by())
        for aggregate_row in aggregated:
            row_key = tuple(
                flow_type if key == 'flow_type'
                else direction if key == 'direction'
                else aggregate_row[aliases[key]]
                for key in plan['dimensions']
            )
            amounts = groups.setdefault(row_key, {
                key: Decimal('0.00') for key in (
                    'customer_receipts', 'other_income', 'salary_payments',
                    'supplier_payments', 'expenses')
            })
            amounts[flow_type] += aggregate_row['amount'] or Decimal('0.00')

    # 无分组仍返回一行全公司零值汇总，与其他受治理数据集契约一致。
    if not plan['dimensions'] and not groups:
        groups[()] = {key: Decimal('0.00') for key in (
            'customer_receipts', 'other_income', 'salary_payments',
            'supplier_payments', 'expenses')}
    rows = []
    for row_key, amounts in groups.items():
        income = amounts['customer_receipts'] + amounts['other_income']
        outflow = (amounts['salary_payments'] + amounts['supplier_payments']
                   + amounts['expenses'])
        row = dict(zip(plan['dimensions'], row_key))
        row.update(amounts)
        row.update(income=income, outflow=outflow, net=income - outflow)
        for item in plan['metrics']:
            row[item['key']] = row[item['key']].normalize()
        rows.append(row)

    ordering = plan['order_by'] or [
        {'field': plan['metrics'][0]['key'], 'direction': 'desc'},
        *[{'field': key, 'direction': 'asc'} for key in plan['dimensions']],
    ]
    for item in reversed(ordering):
        rows.sort(key=lambda row: (row[item['field']] is None, row[item['field']]),
                  reverse=item['direction'] == 'desc')
    total_count = len(rows)
    truncated = total_count > plan['limit']
    rows = rows[:plan['limit']]

    columns = [{
        'key': key, 'label': dataset.field_map[key].label,
        'type': dataset.field_map[key].type,
        'choices': [{'value': value, 'label': label}
                    for value, label in dataset.field_map[key].choices],
    } for key in plan['dimensions']]
    columns += [{
        'key': item['key'], 'label': dataset.metric_map[item['key']].label,
        'type': 'money',
    } for item in plan['metrics']]
    summary = (f'共 {total_count} 个分组，返回 {len(rows)} 行' if plan['dimensions']
               else '已按所选条件生成全公司汇总')
    note = dataset.note
    if truncated:
        summary += f'（结果已截断，最多返回 {plan["limit"]} 行）'
        note += ' 导出仍受相同行数限制。'
    return QueryResult(
        title='统一经营现金流动态报表', summary=summary, note=note,
        columns=columns, rows=rows, total_count=total_count, truncated=truncated,
        scope=[f'公司：{company.name}', '时间口径：实际业务时间（按本地自然日筛选）',
               '数据范围：只包含有效实际流水，永久排除作废或撤销流水'])


def execute_plan(plan, dataset, company):
    if dataset.key == 'finance_cash_flow':
        return _execute_cash_flow_plan(plan, dataset, company)
    manager = dataset.model.all_objects if plan['include_deleted'] else dataset.model.objects
    qs = manager.filter(**{dataset.company_path: company}).order_by()
    if not plan['include_deleted']:
        for path in dataset.active_related_paths:
            qs = qs.filter(**{f'{path}__is_deleted': False})
    for item in plan['filters']:
        field = dataset.field_map[item['field']]
        qs = _apply_filter(qs, company, field, item['operator'], item['value'])

    # 内部别名不能与本模型字段同名（status/is_cut_done/boxes 等均合法业务字段）。
    # 公开 QueryPlan key 只在结果序列化前映射回来，排序也使用内部别名。
    dimension_aliases = {
        key: f'report_dimension_{index}' for index, key in enumerate(plan['dimensions'])
    }
    metric_aliases = {
        item['key']: f'report_metric_{index}' for index, item in enumerate(plan['metrics'])
    }
    dimension_annotations = {
        dimension_aliases[key]: dataset.field_map[key].expression(company)
        for key in plan['dimensions']
    }
    metric_annotations = {
        metric_aliases[item['key']]: _metric_expression(
            dataset.metric_map[item['key']], item['aggregation'], company,
            plan.get('as_of'))
        for item in plan['metrics']
    }

    if plan['dimensions']:
        grouped = (qs.annotate(**dimension_annotations)
                   .values(*dimension_aliases.values())
                   .annotate(**metric_annotations)
                   .order_by())
        total_count = grouped.count()
        ordering = [
            ('-' if item['direction'] == 'desc' else '')
            + dimension_aliases.get(item['field'], metric_aliases.get(item['field']))
            for item in plan['order_by']
        ]
        if not ordering:
            ordering = [f'-{metric_aliases[plan["metrics"][0]["key"]]}']
            ordering += list(dimension_aliases.values())
        selected = list(grouped.order_by(*ordering)[:plan['limit'] + 1])
        truncated = len(selected) > plan['limit']
        rows = [{
            **{key: row[alias] for key, alias in dimension_aliases.items()},
            **{key: row[alias] for key, alias in metric_aliases.items()},
        } for row in selected[:plan['limit']]]
    else:
        aggregated = qs.aggregate(**metric_annotations)
        rows = [{key: aggregated[alias] for key, alias in metric_aliases.items()}]
        total_count = 1
        truncated = False

    columns = []
    for key in plan['dimensions']:
        field = dataset.field_map[key]
        columns.append({
            'key': key,
            'label': field.label,
            'type': field.type,
            'choices': [
                {'value': value, 'label': label} for value, label in field.choices
            ],
        })
    for item in plan['metrics']:
        metric = dataset.metric_map[item['key']]
        columns.append({
            'key': item['key'],
            'label': metric.label_for(item['aggregation']),
            'type': _metric_type(metric, item['aggregation']),
        })

    if plan['dimensions']:
        summary = f'共 {total_count} 个分组，返回 {len(rows)} 行'
    else:
        summary = '已按所选条件生成全公司汇总'
    if truncated:
        summary += f'（结果已截断，最多返回 {plan["limit"]} 行）'
    note = dataset.note
    if truncated:
        note += ' 可缩小筛选范围、调整排序或提高行数上限；导出仍受相同计划限制。'
    scope = [
        f'公司：{company.name}',
        f'时间口径：{dataset.time_label}（按本地自然日筛选）',
        (f'数据范围：包含{dataset.deleted_label}' if plan['include_deleted']
         else f'数据范围：排除{dataset.deleted_label}'),
    ]
    if plan.get('as_of'):
        scope.append(
            f'库存截至业务日期：{plan["as_of"]}（含当日，按当前留存流水重算，非审计快照）')
    return QueryResult(
        title=f'{dataset.label}动态报表', summary=summary, note=note,
        columns=columns, rows=rows, total_count=total_count,
        truncated=truncated, scope=scope)


def _json_value(value):
    if isinstance(value, Decimal):
        return format(value, 'f')
    if isinstance(value, datetime):
        if timezone.is_aware(value):
            value = timezone.localtime(value)
        return value.isoformat()
    if hasattr(value, 'isoformat'):
        return value.isoformat()
    if isinstance(value, float):
        return format(Decimal(str(value)), 'f')
    return value


def serialize_report(result):
    return {
        'title': result.title,
        'summary': result.summary,
        'note': result.note,
        'columns': result.columns,
        'headers': [column['label'] for column in result.columns],
        'rows': [[
            _json_value(row.get(column['key'])) for column in result.columns
        ] for row in result.rows],
        'total_count': result.total_count,
        'returned_count': result.row_count,
        'truncated': result.truncated,
        'scope': result.scope,
        'comparison': result.comparison,
    }


def _choice_label(column, value):
    for choice in column.get('choices', []):
        if choice['value'] == value:
            return choice['label']
    return None


def export_rows(result):
    """保留数字为 Excel 数值，选项显示中文，并中和文本公式前缀。"""
    output = []
    for row in result.rows:
        values = []
        for column in result.columns:
            value = row.get(column['key'])
            if value is None:
                value = ''
            elif isinstance(value, datetime) or hasattr(value, 'isoformat') and not isinstance(value, Decimal):
                value = _json_value(value)
            else:
                value = _choice_label(column, value) or value
            if column['type'] == 'text' and isinstance(value, str) and value.startswith(('=', '+', '-', '@')):
                value = "'" + value
            values.append(value)
        output.append(values)
    return output
