"""采购应付、供应商付款和其他实际收支的事务服务。"""
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.core.numbering import next_no
from apps.core.utils import q2
from apps.finance.models import CashEntry, Payable, SupplierPayment
from apps.ops.models import AuditLog
from apps.ops.services import audit


def _positive(amount, label='金额'):
    value = q2(amount)
    if value <= 0:
        raise ValidationError({'amount': f'{label}必须大于 0'})
    return value


def _payable_status(payable):
    if payable.paid_amount <= 0:
        return Payable.Status.UNPAID
    if payable.paid_amount >= payable.amount:
        return Payable.Status.SETTLED
    return Payable.Status.PARTIAL


def create_payable(company, *, supplier, supplier_name, amount, payable_at,
                   purchase=None, due_at=None, remark='', user=None):
    amount = _positive(amount, '应付金额')
    if supplier is not None and supplier.company_id != company.id:
        raise ValidationError({'supplier': '供应商不属于本公司'})
    if purchase is not None:
        if purchase.company_id != company.id:
            raise ValidationError({'purchase': '采购单不属于本公司'})
        if supplier is not None and purchase.supplier_id not in (None, supplier.id):
            raise ValidationError({'supplier': '供应商与采购单不一致'})
        supplier = supplier or purchase.supplier
    name = (supplier.name if supplier else supplier_name or '').strip()
    if not name:
        raise ValidationError({'supplier_name': '请选择供应商或填写供应商名称'})
    with transaction.atomic():
        payable = Payable.objects.create(
            company=company, payable_no=next_no(company, Payable, 'payable_no', 'AP'),
            supplier=supplier, supplier_name=name[:128], purchase=purchase,
            amount=amount, payable_at=payable_at, due_at=due_at, remark=remark or '')
    audit(company, AuditLog.Type.OTHER, operator=user,
          after={'payable_id': payable.id, 'payable_no': payable.payable_no,
                 'amount': str(amount), 'supplier': name},
          message=f'采购应付建立：【{payable.payable_no}】{name} {amount} 元')
    return payable


def create_supplier_payment(company, *, amount, paid_at=None, payable=None,
                            supplier=None, supplier_name='', method='bank',
                            transaction_ref='', remark='', user=None):
    amount = _positive(amount, '付款金额')
    if supplier is not None and supplier.company_id != company.id:
        raise ValidationError({'supplier': '供应商不属于本公司'})
    with transaction.atomic():
        locked = None
        if payable is not None:
            locked = Payable.all_objects.select_for_update().filter(
                pk=payable.pk, company=company, is_deleted=False).first()
            if locked is None:
                raise ValidationError({'payable': '应付单不存在'})
            new_total = q2(locked.paid_amount + amount)
            if new_total > locked.amount:
                raise ValidationError({'amount': (
                    f'超额付款：应付 {locked.amount} 元，已付 {locked.paid_amount} 元，'
                    f'本笔 {amount} 元')})
            if supplier is not None and locked.supplier_id not in (None, supplier.id):
                raise ValidationError({'supplier': '供应商与应付单不一致'})
            supplier = locked.supplier or supplier
            name = locked.supplier_name
        else:
            name = (supplier.name if supplier else supplier_name or '').strip()
        if not name:
            raise ValidationError({'supplier_name': '请选择供应商或填写供应商名称'})
        try:
            payment = SupplierPayment.objects.create(
                company=company, payable=locked, supplier=supplier,
                supplier_name=name[:128], amount=amount,
                paid_at=paid_at or timezone.now(), method=method,
                transaction_ref=(transaction_ref or '').strip(), operator=user,
                remark=remark or '')
        except IntegrityError:
            raise ValidationError({'transaction_ref': '外部流水号已存在，请勿重复付款'})
        if locked is not None:
            locked.paid_amount = q2(locked.paid_amount + amount)
            locked.status = _payable_status(locked)
            locked.save(update_fields=['paid_amount', 'status', 'updated_at'])
    audit(company, AuditLog.Type.OTHER, operator=user,
          after={'payment_id': payment.id, 'amount': str(amount),
                 'payable_no': locked.payable_no if locked else '',
                 'transaction_ref': payment.transaction_ref},
          message=f'供应商付款：{name} {amount} 元'
                  + (f'，核销【{locked.payable_no}】' if locked else ''))
    return payment


def void_supplier_payment(company, payment, *, reason, user=None):
    if not (reason or '').strip():
        raise ValidationError({'reason': '作废必须填写原因'})
    with transaction.atomic():
        payment = SupplierPayment.all_objects.select_for_update().get(
            pk=payment.pk, company=company, is_deleted=False)
        if payment.status == SupplierPayment.Status.VOIDED:
            raise ValidationError({'status': '付款已作废，不能重复操作'})
        payable = None
        if payment.payable_id:
            payable = Payable.all_objects.select_for_update().get(
                pk=payment.payable_id, company=company)
        payment.status = SupplierPayment.Status.VOIDED
        payment.voided_by = user
        payment.voided_at = timezone.now()
        payment.void_reason = reason.strip()[:200]
        payment.save(update_fields=['status', 'voided_by', 'voided_at',
                                    'void_reason', 'updated_at'])
        if payable is not None:
            payable.paid_amount = q2(max(Decimal('0'), payable.paid_amount - payment.amount))
            payable.status = _payable_status(payable)
            payable.save(update_fields=['paid_amount', 'status', 'updated_at'])
    audit(company, AuditLog.Type.OTHER, operator=user,
          before={'payment_id': payment.id, 'amount': str(payment.amount),
                  'status': 'active'},
          after={'status': 'voided', 'reason': payment.void_reason},
          message=f'供应商付款作废：{payment.supplier_name} {payment.amount} 元；'
                  f'{payment.void_reason}')
    return payment


def void_payable(company, payable, *, user=None):
    with transaction.atomic():
        payable = Payable.all_objects.select_for_update().get(
            pk=payable.pk, company=company, is_deleted=False)
        if payable.payments.filter(status=SupplierPayment.Status.ACTIVE).exists():
            raise ValidationError({'status': '存在有效付款，须先作废付款'})
        payable.is_deleted = True
        payable.save(update_fields=['is_deleted', 'updated_at'])
    audit(company, AuditLog.Type.OTHER, operator=user,
          before={'payable_no': payable.payable_no, 'amount': str(payable.amount)},
          message=f'采购应付作废：【{payable.payable_no}】')
    return payable


def create_cash_entry(company, *, direction, category, amount, occurred_at=None,
                      counterparty='', method='', reference_no='', remark='', user=None):
    amount = _positive(amount)
    if direction not in CashEntry.Direction.values:
        raise ValidationError({'direction': '收支方向不合法'})
    if not (category or '').strip():
        raise ValidationError({'category': '费用或收入类别不能为空'})
    with transaction.atomic():
        try:
            entry = CashEntry.objects.create(
                company=company, entry_no=next_no(company, CashEntry, 'entry_no', 'CE'),
                direction=direction, category=category.strip()[:64], amount=amount,
                occurred_at=occurred_at or timezone.now(),
                counterparty=(counterparty or '').strip()[:128],
                method=(method or '').strip()[:32],
                reference_no=(reference_no or '').strip()[:64], operator=user,
                remark=remark or '')
        except IntegrityError:
            raise ValidationError({'reference_no': '外部凭证号已存在，请勿重复登记'})
    audit(company, AuditLog.Type.OTHER, operator=user,
          after={'entry_id': entry.id, 'entry_no': entry.entry_no,
                 'direction': direction, 'category': entry.category,
                 'amount': str(amount)},
          message=f'{entry.get_direction_display()}登记：【{entry.entry_no}】'
                  f'{entry.category} {amount} 元')
    return entry


def void_cash_entry(company, entry, *, reason, user=None):
    if not (reason or '').strip():
        raise ValidationError({'reason': '作废必须填写原因'})
    with transaction.atomic():
        entry = CashEntry.all_objects.select_for_update().get(
            pk=entry.pk, company=company, is_deleted=False)
        if entry.status == CashEntry.Status.VOIDED:
            raise ValidationError({'status': '收支已作废，不能重复操作'})
        entry.status = CashEntry.Status.VOIDED
        entry.voided_by = user
        entry.voided_at = timezone.now()
        entry.void_reason = reason.strip()[:200]
        entry.save(update_fields=['status', 'voided_by', 'voided_at',
                                  'void_reason', 'updated_at'])
    audit(company, AuditLog.Type.OTHER, operator=user,
          before={'entry_no': entry.entry_no, 'amount': str(entry.amount),
                  'status': 'active'},
          after={'status': 'voided', 'reason': entry.void_reason},
          message=f'{entry.get_direction_display()}作废：【{entry.entry_no}】；'
                  f'{entry.void_reason}')
    return entry
