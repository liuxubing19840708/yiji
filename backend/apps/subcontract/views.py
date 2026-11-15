"""外发视图（GAP-5）：建单/列表、收回、结算、删除、导出。

权限（2 段式）：GET/导出 = outsource.view；建单/收回/结算/删除 = outsource.manage。
状态机：sent →（收回，量足）returned /（量不足）partial →（再收回量足）returned
       →（结算）settled；settled 终态禁改禁删。
"""
from decimal import Decimal
from django.db import transaction
from django.db.models import Count
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import generics
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.export import xlsx_response
from apps.core.mixins import CompanyContextMixin
from apps.core.numbering import next_no
from apps.core.pagination import DefaultPagination
from apps.core.permissions import has_perm
from apps.ops.services import audit
from apps.subcontract.models import OutsourceOrder, OutsourceReceive
from apps.subcontract.serializers import (
    OutsourceOrderSerializer, OutsourceReceiveSerializer,
)


def _require_manage(self):
    if not has_perm(self.user_company, 'outsource.manage'):
        raise PermissionDenied('无外发管理权限')


def _require_view(self):
    if not has_perm(self.user_company, 'outsource.view'):
        raise PermissionDenied('无外发查看权限')


class OutsourceOrderListCreateView(CompanyContextMixin, generics.ListCreateAPIView):
    """GET 外发单列表（outsource.view）；POST 建单（outsource.manage）。"""

    serializer_class = OutsourceOrderSerializer
    pagination_class = DefaultPagination

    def get_queryset(self):
        _require_view(self)
        qs = (OutsourceOrder.objects.filter(company=self.company)
              .select_related('supplier', 'order', 'style', 'creator')
              .annotate(receive_count=Count('receives')))
        params = self.request.query_params
        if params.get('status'):
            qs = qs.filter(status=params['status'])
        if params.get('supplier_id'):
            qs = qs.filter(supplier_id=params['supplier_id'])
        if params.get('order_id'):
            qs = qs.filter(order_id=params['order_id'])
        return qs

    def perform_create(self, serializer):
        _require_manage(self)
        for field, label in (('supplier', '供应商'), ('order', '订单'), ('style', '款式')):
            related = serializer.validated_data.get(field)
            if related is not None and related.company_id != self.company.id:
                raise ValidationError({field: f'{label}不属于当前公司'})
        with transaction.atomic():
            no = next_no(self.company, OutsourceOrder, 'no', 'OS')
            send_qty = serializer.validated_data['send_qty']
            price = serializer.validated_data.get('unit_price') or Decimal('0')
            supplier = serializer.validated_data.get('supplier')
            extra = {}
            if supplier and not serializer.validated_data.get('factory_name'):
                extra['factory_name'] = supplier.name
            instance = serializer.save(
                company=self.company, no=no, amount=send_qty * price,
                creator=self.user_company.user, **extra)
            audit(self.company, 'other', operator=self.user_company.user,
                  message=f'新建外发单 {instance.no}：{instance.factory_name or "—"} '
                          f'x{instance.send_qty} 件，加工费 {instance.amount} 元')


class OutsourceOrderDetailView(CompanyContextMixin, APIView):
    """GET 详情；PUT 动作收回/结算；DELETE 删除（均 outsource.manage）。"""

    def get_object(self, pk):
        return get_object_or_404(
            OutsourceOrder.objects.select_related('supplier', 'order', 'style', 'creator'),
            company=self.company, pk=pk)

    def get(self, request, pk):
        _require_view(self)
        return Response(OutsourceOrderSerializer(self.get_object(pk)).data)

    def put(self, request, pk):
        _require_manage(self)
        rec = self.get_object(pk)
        action = request.data.get('action')
        if action == 'receive':
            return self._receive(request, rec)
        if action == 'settle':
            return self._settle(request, rec)
        raise ValidationError({'action': '仅支持 receive（收回）/ settle（结算）'})

    def _receive(self, request, rec):
        if rec.status not in (OutsourceOrder.Status.SENT, OutsourceOrder.Status.PARTIAL):
            raise ValidationError(f'仅「已发出/部分收回」可收回，当前：{rec.get_status_display()}')
        ser = OutsourceReceiveSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        qty = ser.validated_data['qty']
        remaining = rec.send_qty - rec.received_qty
        if qty > remaining:
            raise ValidationError(f'收回数量不能超过未收回余量 {remaining}')
        with transaction.atomic():
            OutsourceReceive.objects.create(
                company=self.company, order=rec, qty=qty,
                receiver=self.user_company.user,
                remark=ser.validated_data.get('remark', ''))
            rec.received_qty = rec.received_qty + qty
            rec.return_date = timezone.localdate()
            before = rec.status
            rec.status = (OutsourceOrder.Status.RETURNED
                          if rec.received_qty >= rec.send_qty
                          else OutsourceOrder.Status.PARTIAL)
            rec.save(update_fields=['received_qty', 'return_date', 'status', 'updated_at'])
            audit(self.company, 'other', operator=self.user_company.user,
                  before={'status': before, 'received_qty': rec.received_qty - qty},
                  after={'status': rec.status, 'received_qty': rec.received_qty},
                  message=f'外发单 {rec.no} 收回 {qty} 件（累计 {rec.received_qty}/{rec.send_qty}）')
        return Response(OutsourceOrderSerializer(rec).data)

    def _settle(self, request, rec):
        if rec.status != OutsourceOrder.Status.RETURNED:
            raise ValidationError(f'仅「已收回」可结算，当前：{rec.get_status_display()}')
        rec.status = OutsourceOrder.Status.SETTLED
        rec.save(update_fields=['status', 'updated_at'])
        audit(self.company, 'other', operator=self.user_company.user,
              before={'status': OutsourceOrder.Status.RETURNED},
              after={'status': rec.status},
              message=f'外发单 {rec.no} 已结算，加工费 {rec.amount} 元')
        return Response(OutsourceOrderSerializer(rec).data)

    def delete(self, request, pk):
        _require_manage(self)
        rec = self.get_object(pk)
        if rec.status == OutsourceOrder.Status.SETTLED:
            raise PermissionDenied('已结算的外发单不可删除')
        if rec.received_qty > 0:
            raise ValidationError('已有收回记录的外发单不可删除（可走结算收口）')
        rec.delete()  # 软删
        audit(self.company, 'other', operator=self.user_company.user,
              message=f'删除外发单 {rec.no}（{rec.factory_name or "—"} x{rec.send_qty}）')
        return Response(status=204)


class OutsourceExportView(CompanyContextMixin, APIView):
    """GET 外发导出 xlsx（outsource.view）；行数上限 20000，与列表同过滤。"""

    MAX_ROWS = 20000
    HEADERS = ['外发单号', '外协厂', '订单号', '款号', '外发工序', '外发数量',
               '收回数量', '加工单价', '加工费', '发出日期', '交回日期', '收回日期',
               '状态', '备注']

    def get(self, request):
        _require_view(self)
        qs = (OutsourceOrder.objects.filter(company=self.company)
              .select_related('supplier', 'order', 'style'))
        params = request.query_params
        if params.get('status'):
            qs = qs.filter(status=params['status'])
        if params.get('supplier_id'):
            qs = qs.filter(supplier_id=params['supplier_id'])
        rows = []
        for o in qs[:self.MAX_ROWS]:
            rows.append([
                o.no,
                (o.supplier.name if o.supplier else '') or o.factory_name,
                o.order.order_no if o.order else '',
                o.style.style_no if o.style else '',
                o.process_desc, o.send_qty, o.received_qty,
                str(o.unit_price), str(o.amount),
                o.send_date.strftime('%Y-%m-%d') if o.send_date else '',
                o.due_date.strftime('%Y-%m-%d') if o.due_date else '',
                o.return_date.strftime('%Y-%m-%d') if o.return_date else '',
                o.get_status_display(), o.remark,
            ])
        return xlsx_response(self.HEADERS, rows, filename='外发单')
