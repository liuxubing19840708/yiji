"""应收域视图（P2 finance 域，契约 §四 端点；公司隔离 + 2 段式权限点）。

权限（2 段式，legacy=False，决策表惯例）：
  - finance.view：应收列表（GET /finance/receivables）；
  - finance.manage：建应收 / 核销 / 撤销 / 作废 / 移动加权报表
    （设计分工原文「建应收/核销/作废/报表」——报表归 manage，
    跟单查看岗 +finance.view 只看应收账）。
岗位映射建议：老板/财务全开；跟单+finance.view。

revenue 口径衔接：revenue=理论收入（cost 域），与实际收款差异属预期
（折扣/尾数/坏账）；本 app 核销后以收款实绩复核（§3.3 后半句落点）。
"""
from django.http import Http404
from django.utils.dateparse import parse_date
from rest_framework import generics
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.catalog.models import Customer
from apps.core.mixins import CompanyContextMixin
from apps.core.pagination import DefaultPagination
from apps.finance.models import Receivable
from apps.finance.serializers import (BatchReceiptSerializer,
                                      ReceivableCreateSerializer,
                                      ReceivableSerializer,
                                      ReceiptCreateSerializer,
                                      ReceiptLineSerializer)
from apps.finance.services import (add_receipt, batch_receipt_by_customer,
                                   create_receivable, revoke_last_receipt,
                                   stock_valuation, void_receivable)
from apps.orders.models import Order


def _get_active_receivable(company, pk) -> Receivable:
    """软删（已作废）应收一律 404：列表不显/核销 404/撤销 404（软删边界惯例）。"""
    rec = Receivable.all_objects.filter(company=company, pk=pk).first()
    if rec is None or rec.is_deleted:
        raise Http404('应收不存在')
    return rec


class ReceivableView(CompanyContextMixin, generics.ListCreateAPIView):
    """GET /finance/receivables?status=&customer_id=&keyword=&date_from=&date_to=

    过滤：status=unpaid/partial/settled；keyword=应收单号/客户名/订单号；
    date_from/date_to=建档日期。已作废（软删）不显。
    POST 同路径手工建应收（挂 finance.manage；出货域自动建走
    services.create_receivable 同一入口）。
    """

    serializer_class = ReceivableSerializer
    pagination_class = DefaultPagination

    def get_queryset(self):
        qs = (Receivable.objects.filter(company=self.company)
              .select_related('customer', 'order'))
        p = self.request.query_params
        status = (p.get('status') or '').strip()
        if status:
            if status not in Receivable.Status.values:
                raise ValidationError({'status': '状态不合法（unpaid/partial/settled）'})
            qs = qs.filter(status=status)
        customer_id = p.get('customer_id')
        if customer_id:
            qs = qs.filter(customer_id=customer_id)
        keyword = (p.get('keyword') or '').strip()
        if keyword:
            from django.db.models import Q
            qs = qs.filter(
                Q(receivable_no__icontains=keyword)
                | Q(customer__name__icontains=keyword)
                | Q(order__order_no__icontains=keyword))
        date_from = parse_date(p.get('date_from') or '')
        if date_from:
            qs = qs.filter(created_at__date__gte=date_from)
        date_to = parse_date(p.get('date_to') or '')
        if date_to:
            qs = qs.filter(created_at__date__lte=date_to)
        return qs

    def get(self, request, *args, **kwargs):
        self.check_perm('finance.view')
        return super().get(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        self.check_perm('finance.manage')
        s = ReceivableCreateSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        vd = s.validated_data
        customer = (Customer.objects.filter(company=self.company,
                                            pk=vd['customer']).first())
        if customer is None or customer.is_deleted:
            raise ValidationError({'customer': '客户不存在'})
        order = None
        if vd.get('order'):
            order = Order.objects.filter(company=self.company,
                                         pk=vd['order']).first()
            if order is None or order.is_deleted:
                raise ValidationError({'order': '订单不存在'})
        rec = create_receivable(
            self.company, customer=customer, amount=vd['amount'],
            order=order, shipment_id=vd.get('shipment_id'),
            remark=vd.get('remark', ''), user=request.user)
        return Response(ReceivableSerializer(rec).data, status=201)


class ReceivableDetailView(CompanyContextMixin, APIView):
    """GET /finance/receivables/{id} 应收详情（挂 finance.view，公司隔离）。

    契约缺口 A（2026-10-24 INT 补齐）：此前前端 receivable-detail 以列表
    keyword=应收单号 回查兜底，现直接取单；软删（作废）应收 404（同列表口径）。
    """

    def get(self, request, receivable_id):
        self.check_perm('finance.view')
        rec = _get_active_receivable(self.company, receivable_id)
        return Response(ReceivableSerializer(rec).data)


class ReceiptLinesView(CompanyContextMixin, APIView):
    """GET /finance/receivables/{id}/receipt-lines 收款核销流水列表（挂 finance.view）。

    契约缺口 B（2026-10-24 INT 补齐）：详情页「收款时间线」数据源；
    倒序（最近一笔在前）；已撤销（软删）行不显（撤销留痕走 ops 审计日志）。
    单张应收的核销笔数有限，暂不分页（分页可后补）。
    """

    def get(self, request, receivable_id):
        self.check_perm('finance.view')
        rec = _get_active_receivable(self.company, receivable_id)
        lines = rec.receipt_lines.all().order_by('-id')
        return Response(ReceiptLineSerializer(lines, many=True).data)


class ReceiptCreateView(CompanyContextMixin, APIView):
    """POST /finance/receivables/{id}/receipts 核销（收款，挂 finance.manage）。

    超额收款 400 硬拦（金额级——多收的钱要退，与领料超领零拦截口径不同，
    见 services.add_receipt）；响应 = {receivable, receipt} 双块。
    """

    def post(self, request, receivable_id):
        self.check_perm('finance.manage')
        rec = _get_active_receivable(self.company, receivable_id)
        s = ReceiptCreateSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        vd = s.validated_data
        rec, line = add_receipt(
            self.company, rec, amount=vd['amount'], method=vd['method'],
            received_at=vd.get('received_at'), handler=vd.get('handler') or '',
            remark=vd.get('remark', ''), user=request.user)
        return Response({'receivable': ReceivableSerializer(rec).data,
                         'receipt': ReceiptLineSerializer(line).data}, status=201)


class CustomerBatchReceiptView(CompanyContextMixin, APIView):
    """POST /finance/customers/<customer_id>/receipts-batch 按客户批量核销（finance.manage）。

    2026-11-02 评审 Top10 #5：客户一笔款付多单，FIFO 分配（created_at 升序）
    逐行复用 add_receipt（行级锁+audit 逐行），溢收款 400 硬拦（同单笔红线）。
    响应 = {customer, allocated[], total_allocated, leftover}。
    """

    def post(self, request, customer_id):
        self.check_perm('finance.manage')
        try:
            Customer.objects.get(company=self.company, pk=customer_id, is_deleted=False)
        except Customer.DoesNotExist:
            raise Http404('客户不存在')
        s = BatchReceiptSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        vd = s.validated_data
        result = batch_receipt_by_customer(
            self.company, customer_id, total=vd['total'], method=vd['method'],
            received_at=vd.get('received_at'), handler=vd.get('handler') or '',
            remark=vd.get('remark', ''), user=request.user)
        return Response({'customer': customer_id, **result}, status=201)


class ReceiptRevokeView(CompanyContextMixin, APIView):
    """POST /finance/receivables/{id}/receipts/revoke 撤销最近一条核销（挂 finance.manage）。

    反向回退 received_amount 并重算 status；流水行软删保留 + audit 留痕。
    响应 = {receivable, revoked_receipt_id}。
    """

    def post(self, request, receivable_id):
        self.check_perm('finance.manage')
        rec = _get_active_receivable(self.company, receivable_id)
        rec, line = revoke_last_receipt(self.company, rec, user=request.user)
        return Response({'receivable': ReceivableSerializer(rec).data,
                         'revoked_receipt_id': line.pk})


class ReceivableVoidView(CompanyContextMixin, APIView):
    """POST /finance/receivables/{id}/void 作废应收（挂 finance.manage）。

    有活动核销须先全部撤销（400）；作废=软删 + audit 留痕，列表即下架。
    """

    def post(self, request, receivable_id):
        self.check_perm('finance.manage')
        rec = _get_active_receivable(self.company, receivable_id)
        rec = void_receivable(self.company, rec, user=request.user)
        return Response({'id': rec.pk, 'receivable_no': rec.receivable_no,
                         'voided': True})


class StockValuationView(CompanyContextMixin, APIView):
    """GET /finance/stock-valuation?material_id=&date_to= 移动加权库存估值（挂 finance.manage）。

    D4 双口径的第二口径：车间用批次价（Lot.unit_cost）、财务对账用移动加权
    （本表，基于 Lot 流水 Σ入库额/Σ入库量 逐笔递推），双口径并存。
    """

    def get(self, request):
        self.check_perm('finance.manage')
        date_to = parse_date(request.query_params.get('date_to') or '')
        material_id = request.query_params.get('material_id')
        if material_id:
            try:
                material_id = int(material_id)
            except (TypeError, ValueError):
                raise ValidationError({'material_id': 'material_id 须为整数'})
        return Response(stock_valuation(self.company,
                                        material_id=material_id, date_to=date_to))
