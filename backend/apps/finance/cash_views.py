from django.http import Http404
from django.utils.dateparse import parse_date
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.mixins import CompanyContextMixin
from apps.core.pagination import DefaultPagination
from apps.finance.cashflow_services import cash_flow
from apps.finance.cash_serializers import (
    CashEntryCreateSerializer, CashEntrySerializer, PayableCreateSerializer,
    PayableSerializer, SupplierPaymentCreateSerializer, SupplierPaymentSerializer,
    VoidSerializer,
)
from apps.finance.cash_services import (
    create_cash_entry, create_payable, create_supplier_payment,
    void_cash_entry, void_payable, void_supplier_payment,
)
from apps.finance.models import CashEntry, Payable, SupplierPayment
from apps.warehouse.models import PurchaseOrder, Supplier


def _supplier(company, pk):
    if pk is None:
        return None
    supplier = Supplier.objects.filter(company=company, pk=pk).first()
    if supplier is None:
        raise ValidationError({'supplier': '供应商不存在'})
    return supplier


def _purchase(company, pk):
    if pk is None:
        return None
    purchase = PurchaseOrder.objects.filter(company=company, pk=pk).first()
    if purchase is None:
        raise ValidationError({'purchase': '采购单不存在'})
    return purchase


def _date_filter(request, qs, field):
    for key, lookup in (('date_from', 'gte'), ('date_to', 'lte')):
        raw = request.query_params.get(key)
        if raw:
            value = parse_date(raw)
            if value is None:
                raise ValidationError({key: '日期必须为 YYYY-MM-DD'})
            qs = qs.filter(**{f'{field}__date__{lookup}': value})
    return qs


class CashFlowView(CompanyContextMixin, APIView):
    def get(self, request):
        # 汇总包含工资金额，不能因持有 finance.view 而绕过薪酬权限。
        self.check_perm('finance.manage')
        self.check_perm('salary.view')
        params = request.query_params
        start, end = parse_date(params.get('date_from', '')), parse_date(params.get('date_to', ''))
        if not start or not end:
            raise ValidationError({'detail': 'date_from 和 date_to 必须为 YYYY-MM-DD'})
        if start > end:
            raise ValidationError({'detail': '开始日期不得晚于结束日期'})
        if (end - start).days > 3660:
            raise ValidationError({'detail': '单次现金流汇总范围不得超过 10 年'})
        group_by = params.get('group_by', 'month')
        if group_by not in ('day', 'month'):
            raise ValidationError({'group_by': '只支持 day 或 month'})
        return Response(cash_flow(self.company, date_from=start, date_to=end, group_by=group_by))


class PayableListCreateView(CompanyContextMixin, APIView):
    def get(self, request):
        self.check_perm('finance.view')
        qs = Payable.objects.filter(company=self.company).select_related('supplier', 'purchase')
        p = request.query_params
        if p.get('status'):
            if p['status'] not in Payable.Status.values:
                raise ValidationError({'status': '状态不合法'})
            qs = qs.filter(status=p['status'])
        if p.get('supplier'):
            qs = qs.filter(supplier_id=p['supplier'])
        if p.get('keyword'):
            qs = qs.filter(payable_no__icontains=p['keyword'].strip())
        qs = _date_filter(request, qs, 'payable_at')
        paginator = DefaultPagination()
        page = paginator.paginate_queryset(qs, request, self)
        return paginator.get_paginated_response(PayableSerializer(page, many=True).data)

    def post(self, request):
        self.check_perm('finance.manage')
        s = PayableCreateSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        data = s.validated_data
        payable = create_payable(
            self.company, supplier=_supplier(self.company, data.get('supplier')),
            supplier_name=data.get('supplier_name', ''),
            purchase=_purchase(self.company, data.get('purchase')),
            amount=data['amount'], payable_at=data['payable_at'],
            due_at=data.get('due_at'), remark=data.get('remark', ''), user=request.user)
        return Response(PayableSerializer(payable).data, status=201)


class PayableDetailView(CompanyContextMixin, APIView):
    def get_object(self, pk):
        try:
            return Payable.objects.get(company=self.company, pk=pk)
        except Payable.DoesNotExist:
            raise Http404('应付单不存在')

    def get(self, request, pk):
        self.check_perm('finance.view')
        return Response(PayableSerializer(self.get_object(pk)).data)

    def post(self, request, pk):
        raise ValidationError({'detail': '请使用专用作废或付款接口'})


class PayableVoidView(PayableDetailView):
    def post(self, request, pk):
        self.check_perm('finance.manage')
        payable = void_payable(self.company, self.get_object(pk), user=request.user)
        return Response({'id': payable.id, 'voided': True})


class SupplierPaymentListCreateView(CompanyContextMixin, APIView):
    def get(self, request):
        self.check_perm('finance.view')
        qs = SupplierPayment.objects.filter(company=self.company).select_related('payable', 'supplier')
        if request.query_params.get('status') == 'active':
            qs = qs.filter(status=SupplierPayment.Status.ACTIVE)
        elif request.query_params.get('status') == 'voided':
            qs = qs.filter(status=SupplierPayment.Status.VOIDED)
        elif request.query_params.get('status'):
            raise ValidationError({'status': '状态不合法'})
        supplier_id = request.query_params.get('supplier')
        if supplier_id:
            if not Supplier.objects.filter(company=self.company, pk=supplier_id).exists():
                raise ValidationError({'supplier': '供应商不存在'})
            qs = qs.filter(supplier_id=supplier_id)
        qs = _date_filter(request, qs, 'paid_at')
        paginator = DefaultPagination()
        page = paginator.paginate_queryset(qs, request, self)
        return paginator.get_paginated_response(SupplierPaymentSerializer(page, many=True).data)

    def post(self, request):
        self.check_perm('finance.manage')
        s = SupplierPaymentCreateSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        data = s.validated_data
        payable = None
        if data.get('payable'):
            payable = Payable.objects.filter(company=self.company, pk=data['payable']).first()
            if payable is None:
                raise ValidationError({'payable': '应付单不存在'})
        payment = create_supplier_payment(
            self.company, payable=payable,
            supplier=_supplier(self.company, data.get('supplier')),
            supplier_name=data.get('supplier_name', ''), amount=data['amount'],
            paid_at=data.get('paid_at'), method=data['method'],
            transaction_ref=data.get('transaction_ref', ''),
            remark=data.get('remark', ''), user=request.user)
        return Response(SupplierPaymentSerializer(payment).data, status=201)


class SupplierPaymentVoidView(CompanyContextMixin, APIView):
    def post(self, request, pk):
        self.check_perm('finance.manage')
        s = VoidSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        try:
            payment = SupplierPayment.objects.get(company=self.company, pk=pk)
        except SupplierPayment.DoesNotExist:
            raise Http404('付款不存在')
        payment = void_supplier_payment(
            self.company, payment, reason=s.validated_data['reason'], user=request.user)
        return Response(SupplierPaymentSerializer(payment).data)


class CashEntryListCreateView(CompanyContextMixin, APIView):
    def get(self, request):
        self.check_perm('finance.view')
        qs = CashEntry.objects.filter(company=self.company)
        p = request.query_params
        if p.get('direction'):
            if p['direction'] not in CashEntry.Direction.values:
                raise ValidationError({'direction': '方向不合法'})
            qs = qs.filter(direction=p['direction'])
        if p.get('category'):
            qs = qs.filter(category=p['category'])
        if p.get('status'):
            if p['status'] not in CashEntry.Status.values:
                raise ValidationError({'status': '状态不合法'})
            qs = qs.filter(status=p['status'])
        qs = _date_filter(request, qs, 'occurred_at')
        paginator = DefaultPagination()
        page = paginator.paginate_queryset(qs, request, self)
        return paginator.get_paginated_response(CashEntrySerializer(page, many=True).data)

    def post(self, request):
        self.check_perm('finance.manage')
        s = CashEntryCreateSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        entry = create_cash_entry(self.company, **s.validated_data, user=request.user)
        return Response(CashEntrySerializer(entry).data, status=201)


class CashEntryVoidView(CompanyContextMixin, APIView):
    def post(self, request, pk):
        self.check_perm('finance.manage')
        s = VoidSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        try:
            entry = CashEntry.objects.get(company=self.company, pk=pk)
        except CashEntry.DoesNotExist:
            raise Http404('收支记录不存在')
        entry = void_cash_entry(self.company, entry,
                                reason=s.validated_data['reason'], user=request.user)
        return Response(CashEntrySerializer(entry).data)
