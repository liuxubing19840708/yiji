"""统一经营现金流：只合并各域真实、有效、已发生资金事实，不代表会计利润。"""
from collections import defaultdict
from decimal import Decimal

from django.db.models import Sum
from django.db.models.functions import TruncDate, TruncMonth

from apps.finance.models import CashEntry, ReceiptLine, SupplierPayment
from apps.salary.models import SalaryPayment


def cash_flow(company, *, date_from, date_to, group_by='month'):
    trunc = TruncMonth if group_by == 'month' else TruncDate
    sources = {
        'customer_receipts': (ReceiptLine.objects.filter(
            company=company, receivable__is_deleted=False, is_deleted=False,
            received_at__date__gte=date_from, received_at__date__lte=date_to),
            'received_at', 1),
        'other_income': (CashEntry.objects.filter(
            company=company, status=CashEntry.Status.ACTIVE,
            direction=CashEntry.Direction.INCOME,
            occurred_at__date__gte=date_from, occurred_at__date__lte=date_to),
            'occurred_at', 1),
        'salary_payments': (SalaryPayment.objects.filter(
            company=company, status=SalaryPayment.Status.ACTIVE,
            paid_at__date__gte=date_from, paid_at__date__lte=date_to), 'paid_at', -1),
        'supplier_payments': (SupplierPayment.objects.filter(
            company=company, status=SupplierPayment.Status.ACTIVE,
            paid_at__date__gte=date_from, paid_at__date__lte=date_to), 'paid_at', -1),
        'expenses': (CashEntry.objects.filter(
            company=company, status=CashEntry.Status.ACTIVE,
            direction=CashEntry.Direction.EXPENSE,
            occurred_at__date__gte=date_from, occurred_at__date__lte=date_to),
            'occurred_at', -1),
    }
    rows = defaultdict(lambda: {key: Decimal('0.00') for key in sources})
    for key, (queryset, field, sign) in sources.items():
        values = (queryset.annotate(period=trunc(field)).values('period')
                  .annotate(amount=Sum('amount')).order_by('period'))
        for item in values:
            rows[item['period']][key] += item['amount'] * sign
    output = []
    totals = {key: Decimal('0.00') for key in sources}
    for period in sorted(rows):
        amounts = rows[period]
        for key in totals:
            totals[key] += amounts[key]
        income = amounts['customer_receipts'] + amounts['other_income']
        outflow = -(amounts['salary_payments'] + amounts['supplier_payments']
                    + amounts['expenses'])
        output.append(_row(period, amounts, income, outflow))
    total_income = totals['customer_receipts'] + totals['other_income']
    total_outflow = -(totals['salary_payments'] + totals['supplier_payments']
                      + totals['expenses'])
    return {
        'date_from': date_from.isoformat(), 'date_to': date_to.isoformat(),
        'group_by': group_by, 'rows': output, 'totals': _row(None, totals, total_income, total_outflow),
        'note': '经营现金流按实际业务时间合并有效回款、其他收入、实付工资、供应商付款和费用；'
                '作废或撤销流水不计。净流出为负，本报表不是利润表。',
    }


def _row(period, amounts, income, outflow):
    return {
        'period': period.isoformat() if period else None,
        **{key: str(value.quantize(Decimal('0.01'))) for key, value in amounts.items()},
        'income': str(income.quantize(Decimal('0.01'))),
        'outflow': str(outflow.quantize(Decimal('0.01'))),
        'net': str((income - outflow).quantize(Decimal('0.01'))),
    }
