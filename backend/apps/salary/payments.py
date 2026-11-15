"""实付工资：支付事实、结算归属及原子余额校验。"""
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.accounts.models import UserCompany
from apps.salary.models import SalaryPayment, SalaryPaymentAllocation, Settlement


def settlement_paid_amount(settlement, employee_id=None):
    """有效实付对结算快照的抵扣；作废支付不影响此口径。"""
    qs = SalaryPaymentAllocation.objects.filter(
        settlement=settlement,
        payment__status=SalaryPayment.Status.ACTIVE,
        payment__is_deleted=False,
    )
    if employee_id is not None:
        qs = qs.filter(payment__employee_id=employee_id)
    return qs.aggregate(total=Sum('amount'))['total'] or Decimal('0.00')


def settlement_outstanding(settlement, employee_id=None):
    """员工快照（无分员工快照的单人单回退结算总额）减有效实付。"""
    if employee_id is None:
        cap = settlement.amount
    elif settlement.user_id is not None:
        cap = settlement.amount if settlement.user_id == employee_id else Decimal('0')
    else:
        raw = (settlement.wage_detail or {}).get(str(employee_id))
        cap = Decimal(str(raw)) if raw is not None else Decimal('0')
    return max(Decimal('0.00'), cap - settlement_paid_amount(
        settlement, employee_id))


def create_salary_payment(*, company, employee, paid_at, method, transaction_ref,
                          note, operator, amount, allocations=()):
    """在事务与结算行锁内创建支付；分摊总额必须等于实付金额。"""
    transaction_ref = str(transaction_ref or '').strip()
    if not transaction_ref:
        raise ValidationError({'transaction_ref': '外部流水号不能为空'})
    if SalaryPayment.objects.filter(
            company=company, transaction_ref=transaction_ref,
            status=SalaryPayment.Status.ACTIVE).exists():
        raise ValidationError({'transaction_ref': '该公司有效支付的外部流水号已存在'})
    if not UserCompany.objects.filter(
            company=company, user=employee,
            status__in=[UserCompany.Status.ACTIVE, UserCompany.Status.RESIGNED]).exists():
        raise ValidationError({'employee_id': '员工不属于当前公司'})

    rows = list(allocations or ())
    seen = set()
    for row in rows:
        sid = row['settlement_id']
        if sid in seen:
            raise ValidationError({'allocations': '同一结算单不能重复分摊'})
        seen.add(sid)
    allocated = sum((row['amount'] for row in rows), Decimal('0.00'))
    if rows and allocated != amount:
        raise ValidationError({'allocations': '分摊金额合计必须等于实付金额'})

    try:
        with transaction.atomic():
            # PostgreSQL 等支持行锁的库上，余额读取与写入由同一结算行锁串行化。
            locked = list(Settlement.objects.select_for_update().filter(
                company=company, status=Settlement.Status.SETTLED,
                id__in=seen).order_by('id'))
            by_id = {s.id: s for s in locked}
            missing = seen - set(by_id)
            if missing:
                raise ValidationError({
                    'allocations': f'结算单不存在、已撤销或不属于当前公司：{sorted(missing)}'})
            for row in rows:
                settlement = by_id[row['settlement_id']]
                if settlement.user_id is not None:
                    if settlement.user_id != employee.id:
                        raise ValidationError({'allocations': '不能把支付分摊到其他员工的结算单'})
                else:
                    detail = settlement.wage_detail or {}
                    if not detail:
                        raise ValidationError({
                            'allocations': '全员结算单没有 wage_detail，无法确定员工归属'})
                    if str(employee.id) not in detail:
                        raise ValidationError({
                            'allocations': '该员工不在全员结算单的 wage_detail 中'})
                    try:
                        Decimal(str(detail[str(employee.id)]))
                    except Exception:
                        raise ValidationError({'allocations': '结算单员工金额快照无效'})
                remaining = settlement_outstanding(settlement, employee.id)
                if row['amount'] > remaining:
                    raise ValidationError({
                        'allocations': f'结算单#{settlement.id}该员工未付余额为{remaining}，不能超付'})
            payment = SalaryPayment.objects.create(
                company=company, employee=employee, paid_at=paid_at,
                method=method, transaction_ref=transaction_ref, note=note,
                operator=operator, amount=amount)
            SalaryPaymentAllocation.objects.bulk_create([
                SalaryPaymentAllocation(
                    payment=payment, settlement=by_id[row['settlement_id']],
                    amount=row['amount'])
                for row in rows
            ])
    except IntegrityError:
        # 并发同流水号最终由条件唯一约束兜底。
        raise ValidationError({'transaction_ref': '该公司有效支付的外部流水号已存在'})
    return payment


def create_apply_payment(apply, settlement, operator, method='other', note=''):
    """旧申请发放入口的事实落库；自动流水号保证可追踪且无需改原响应。"""
    return create_salary_payment(
        company=apply.company, employee=apply.user,
        paid_at=apply.paid_at or timezone.now(), method=method or 'other',
        transaction_ref=f'settle-apply-{apply.id}', note=note,
        operator=operator, amount=settlement.amount,
        allocations=[{'settlement_id': settlement.id, 'amount': settlement.amount}],
    )
