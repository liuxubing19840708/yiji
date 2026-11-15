"""出货域视图（P2 §4.2；公司隔离 + 2 段式权限点 shipment.view/manage）。

权限（legacy=False，决策表惯例；岗位映射建议：老板/厂长/跟单全开，
跟单查看岗 +shipment.view）：
  - shipment.view：出货列表/详情/未交矩阵/打印历史；
  - shipment.manage：建单/改单/删单/审核/作废/装箱行/打印上报
    （print.manage 岗亦可上报打印——同 TicketPrintLog 打印岗惯例）。

审核/作废/未交量口径全部在 services（audit_shipment/void_shipment/
undelivered_matrix），视图只做公司隔离取数与形状校验。
"""
from django.db import IntegrityError, OperationalError, transaction
from django.db.models import Q, Sum
from django.http import FileResponse, Http404
from django.utils import timezone
from rest_framework import generics
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.catalog.models import Customer
from apps.core.mixins import CompanyContextMixin
from apps.core.pagination import DefaultPagination
from apps.orders.models import Order
from apps.shipment import services
from apps.shipment.models import (PackingBox, Shipment, ShipmentItem,
                                  ShipmentPrintLog)
from apps.shipment.serializers import (PackingBoxSerializer,
                                       ShipmentPrintLogSerializer,
                                       ShipmentSerializer)


def _get_shipment(company, pk) -> Shipment:
    """软删（已作废）出货单一律 404（软删边界惯例，同应收域）。"""
    shp = Shipment.all_objects.filter(company=company, pk=pk).first()
    if shp is None or shp.is_deleted:
        raise Http404('出货单不存在')
    return shp


def _validated_order(company, raw) -> Order:
    order = Order.objects.filter(company=company, pk=raw).first()
    if order is None or order.is_deleted:
        raise ValidationError({'order': '订单不存在'})
    return order


def _validated_customer(company, raw) -> Customer:
    """客户必传（出货单必挂客户——应收联动 create_receivable 依赖）。"""
    if raw in (None, ''):
        raise ValidationError({'customer': '请选择客户'})
    cust = Customer.objects.filter(company=company, pk=raw).first()
    if cust is None or cust.is_deleted:
        raise ValidationError({'customer': '客户不存在'})
    return cust


def _validated_items(order, raw):
    """出货明细校验：非空列表、色码齐全、qty>0、同单同色码唯一。"""
    if not isinstance(raw, list) or not raw:
        raise ValidationError({'items': '出货明细不能为空'})
    rows, seen = [], set()
    for idx, it in enumerate(raw):
        if not isinstance(it, dict):
            raise ValidationError({'items': f'第{idx + 1}项格式不正确'})
        color = str(it.get('color') or '').strip()
        size = str(it.get('size') or '').strip()
        try:
            qty = int(it.get('qty'))
        except (TypeError, ValueError):
            qty = 0
        if not color:
            raise ValidationError({'items': f'第{idx + 1}项缺少颜色'})
        if not size:
            raise ValidationError({'items': f'第{idx + 1}项缺少尺码'})
        if qty <= 0:
            raise ValidationError({'items': f'第{idx + 1}项数量必须大于0'})
        key = (color, size)
        if key in seen:
            raise ValidationError({'items': f'同单同色码重复：{color}/{size}'})
        seen.add(key)
        rows.append(ShipmentItem(color=color, size=size, qty=qty))
    return rows


def _check_shipment_no_free(company, shipment_no):
    if Shipment.objects.filter(company=company, shipment_no=shipment_no).exists():
        raise ValidationError({'shipment_no': '出货单号已存在'})


def _refresh_totals(shp):
    """boxes/total_qty 自动汇总（装箱行增删与明细变更后调用）。"""
    shp.total_qty = shp.items.aggregate(s=Sum('qty'))['s'] or 0
    shp.boxes = shp.packing_boxes.count()
    shp.save(update_fields=['total_qty', 'boxes', 'updated_at'])


class ShipmentListCreateView(CompanyContextMixin, generics.ListCreateAPIView):
    """GET /shipments?status=&order_id=&customer_id=&keyword=
    + POST /shipments（建单，单号留空自动 SH+日期+3 位顺延）。"""

    serializer_class = ShipmentSerializer
    pagination_class = DefaultPagination

    def get_queryset(self):
        qs = (Shipment.objects.filter(company=self.company)
              .select_related('order', 'customer', 'audited_by'))
        p = self.request.query_params
        status = (p.get('status') or '').strip()
        if status:
            if status not in Shipment.Status.values:
                raise ValidationError({'status': '状态不合法（draft/confirmed/'
                                                'partial/shipped/signed）'})
            qs = qs.filter(status=status)
        order_id = p.get('order_id')
        if order_id:
            qs = qs.filter(order_id=order_id)
        customer_id = p.get('customer_id')
        if customer_id:
            qs = qs.filter(customer_id=customer_id)
        keyword = (p.get('keyword') or '').strip()
        if keyword:
            from django.db.models import Q
            qs = qs.filter(
                Q(shipment_no__icontains=keyword)
                | Q(customer__name__icontains=keyword)
                | Q(order__order_no__icontains=keyword)
                | Q(logistics_no__icontains=keyword))
        return qs

    def get(self, request, *args, **kwargs):
        self.check_perm('shipment.view')
        return super().get(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        self.check_perm('shipment.manage')
        order = _validated_order(self.company, request.data.get('order'))
        customer = _validated_customer(self.company, request.data.get('customer'))
        rows = _validated_items(order, request.data.get('items'))
        shipment_no = (request.data.get('shipment_no') or '').strip()
        if shipment_no:
            _check_shipment_no_free(self.company, shipment_no)
        shp = Shipment(company=self.company, order=order, customer=customer,
                       shipment_no=shipment_no,
                       delivery_date=request.data.get('delivery_date') or None,
                       ship_date=request.data.get('ship_date') or None,
                       logistics_no=(request.data.get('logistics_no') or '').strip(),
                       remark=(request.data.get('remark') or '').strip(),
                       total_qty=sum(r.qty for r in rows))
        import random
        import time as _time
        for attempt in range(5):
            if not shp.shipment_no:
                shp.shipment_no = services.gen_shipment_no(self.company)
            try:
                with transaction.atomic():
                    shp.save()
                    for r in rows:
                        r.shipment = shp
                    ShipmentItem.objects.bulk_create(rows)
                break
            except (IntegrityError, OperationalError):
                # 显式单号撞 UNIQUE 转 400（预查已放行=并发插入同号），不裸抛 500；
                # 自动号重取号重试（同订单域惯例）
                if shipment_no:
                    _check_shipment_no_free(self.company, shipment_no)
                    raise ValidationError({'shipment_no': '出货单号已存在'})
                shp.pk = None
                shp.shipment_no = ''   # 清掉撞号候选，下轮重取（防死循环重提同号）
                _time.sleep(random.uniform(0.05, 0.25))
        else:
            # for-else：5 轮重取仍撞号（极端并发）→ 400 上抛不静默
            raise ValidationError({'shipment_no': '出货单号生成冲突，请重试'})
        from apps.ops.models import AuditLog
        from apps.ops.services import audit
        audit(self.company, AuditLog.Type.OTHER, operator=request.user,
              message=f'新建出货单：【{shp.shipment_no}】（{order.order_no} '
                      f'{shp.total_qty} 件）')
        return Response(ShipmentSerializer(shp).data, status=201)


class ShipmentDetailView(CompanyContextMixin, generics.RetrieveUpdateDestroyAPIView):
    """GET / PUT / DELETE /shipments/<id>。

    PUT：草稿可全量改（order/customer/items 全量替换）；
    已审核仅允许 logistics_no/ship_date/remark（明细与未交量已锁定）。
    DELETE：仅草稿（已审核走作废，保留应收联动）。
    """

    serializer_class = ShipmentSerializer

    def get_object(self):
        return _get_shipment(self.company, self.kwargs['pk'])

    def get(self, request, *args, **kwargs):
        self.check_perm('shipment.view')
        return super().get(request, *args, **kwargs)

    def put(self, request, *args, **kwargs):
        self.check_perm('shipment.manage')
        shp = self.get_object()
        editable = {'logistics_no', 'ship_date', 'remark'}
        if shp.status != Shipment.Status.DRAFT:
            illegal = set(request.data) - editable - {'shipment_no'}
            if illegal:
                raise ValidationError({
                    'detail': f'已审核出货单仅可修改物流单号/发货日期/备注'
                              f'（非法字段：{("、".join(sorted(illegal)))}）'})
        if 'logistics_no' in request.data:
            shp.logistics_no = (request.data.get('logistics_no') or '').strip()
        if 'ship_date' in request.data:
            shp.ship_date = request.data.get('ship_date') or None
        if 'remark' in request.data:
            shp.remark = (request.data.get('remark') or '').strip()
        if 'delivery_date' in request.data and shp.status == Shipment.Status.DRAFT:
            shp.delivery_date = request.data.get('delivery_date') or None
        if shp.status == Shipment.Status.DRAFT and (
                'order' in request.data or 'items' in request.data
                or 'customer' in request.data):
            if 'order' in request.data:
                shp.order = _validated_order(self.company, request.data.get('order'))
            if 'customer' in request.data:
                shp.customer = _validated_customer(
                    self.company, request.data.get('customer'))
            if 'items' in request.data:
                rows = _validated_items(shp.order, request.data.get('items'))
                ShipmentItem.all_objects.filter(shipment=shp).delete()
                for r in rows:
                    r.shipment = shp
                ShipmentItem.objects.bulk_create(rows)
        shp.total_qty = shp.items.aggregate(s=Sum('qty'))['s'] or 0
        shp.save()
        from apps.ops.models import AuditLog
        from apps.ops.services import audit
        audit(self.company, AuditLog.Type.OTHER, operator=request.user,
              message=f'修改出货单：【{shp.shipment_no}】')
        return Response(ShipmentSerializer(shp).data)

    def perform_destroy(self, instance):
        self.check_perm('shipment.manage')
        shp = self.get_object()
        if shp.status != Shipment.Status.DRAFT:
            raise ValidationError({'detail': '已审核出货单不可删除，请作废'
                                             '（作废会联动提示应收处理）'})
        from apps.ops.models import AuditLog
        from apps.ops.services import audit
        audit(self.company, AuditLog.Type.OTHER, operator=self.request.user,
              message=f'删除出货单：【{shp.shipment_no}】')
        ShipmentItem.all_objects.filter(shipment=shp).delete()
        PackingBox.all_objects.filter(shipment=shp).delete()
        shp.is_deleted = True
        shp.save(update_fields=['is_deleted', 'updated_at'])


class ShipmentAuditView(CompanyContextMixin, APIView):
    """POST /shipments/{id}/audit 审核（挂 shipment.manage）。

    事务内三道校验（未交量/负库存/缺价，400 硬拦）→ 自动建应收 →
    status 流转 confirmed/partial。响应 = {'shipment', 'receivable'} 双块。
    """

    def post(self, request, pk):
        self.check_perm('shipment.manage')
        shp = _get_shipment(self.company, pk)
        shp, receivable = services.audit_shipment(self.company, shp, request.user)
        return Response({
            'shipment': ShipmentSerializer(shp).data,
            'receivable': {
                'id': receivable.pk,
                'receivable_no': receivable.receivable_no,
                'amount': str(receivable.amount),
                'status': receivable.status,
                'shipment_id': shp.pk,
            }}, status=200)


class ShipmentVoidView(CompanyContextMixin, APIView):
    """POST /shipments/{id}/void 作废（挂 shipment.manage）。

    撤单不自动撤应收：响应 warning 字段说明资金联动（挂应收清单 +
    已收金额提示），人工到应收模块处理。作废单回到未交池（已出量不计）。
    """

    def post(self, request, pk):
        self.check_perm('shipment.manage')
        shp = _get_shipment(self.company, pk)
        shp, receivables, warning = services.void_shipment(
            self.company, shp, request.user)
        return Response({
            'id': shp.pk, 'shipment_no': shp.shipment_no, 'voided': True,
            'warning': warning,
            'receivables': [{'id': r.pk, 'receivable_no': r.receivable_no,
                             'amount': str(r.amount),
                             'received_amount': str(r.received_amount),
                             'status': r.status} for r in receivables]})


class ShipmentUndeliveredView(CompanyContextMixin, APIView):
    """GET /shipments/{id}/undelivered 该订单未交量矩阵（色码×尺码 qty−已出）。

    真口径：OrderItem.qty − Σ已审核出货明细（作废单不计）；本单草稿量不计入
    已出（审核前未交量不预占）。
    """

    def get(self, request, pk):
        self.check_perm('shipment.view')
        shp = _get_shipment(self.company, pk)
        return Response(services.undelivered_matrix(self.company, shp.order))


class PackingBoxListCreateView(CompanyContextMixin, APIView):
    """GET/POST /shipments/{id}/boxes 装箱行（挂 shipment.manage 写 / view 读）。"""

    def get(self, request, pk):
        self.check_perm('shipment.view')
        shp = _get_shipment(self.company, pk)
        boxes = shp.packing_boxes.order_by('box_no', 'id')
        return Response(PackingBoxSerializer(boxes, many=True).data)

    def post(self, request, pk):
        self.check_perm('shipment.manage')
        shp = _get_shipment(self.company, pk)
        s = PackingBoxSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        box_no = (s.validated_data.get('box_no') or '').strip()
        if not box_no:
            raise ValidationError({'box_no': '请填写箱号'})
        box = PackingBox.objects.create(shipment=shp, **s.validated_data)
        _refresh_totals(shp)
        return Response(PackingBoxSerializer(box).data, status=201)


class PackingBoxDeleteView(CompanyContextMixin, APIView):
    """DELETE /shipments/{id}/boxes/{box_id} 删装箱行（挂 shipment.manage）。"""

    def delete(self, request, pk, box_id):
        self.check_perm('shipment.manage')
        shp = _get_shipment(self.company, pk)
        box = PackingBox.objects.filter(shipment=shp, pk=box_id).first()
        if box is None:
            raise Http404('装箱行不存在')
        box.delete()
        _refresh_totals(shp)
        return Response(status=204)


class ShipmentPrintFileView(CompanyContextMixin, APIView):
    """GET /shipments/{id}/print-file?template=shipment|packing 返回真实中文 PDF。"""

    def get(self, request, pk):
        if not (self.has_perm('shipment.manage') or self.has_perm('print.manage')):
            raise PermissionDenied('没有该模块的操作权限')
        shp = _get_shipment(self.company, pk)
        template = request.query_params.get('template') or ShipmentPrintLog.Template.SHIPMENT
        if template not in ShipmentPrintLog.Template.values:
            raise ValidationError({'template': '模板不合法（shipment/packing）'})
        from io import BytesIO
        from urllib.parse import quote
        from apps.shipment.printing import render_shipment_pdf
        content = render_shipment_pdf(shp, template)
        suffix = 'packing' if template == ShipmentPrintLog.Template.PACKING else 'shipment'
        filename = f'{shp.shipment_no}-{suffix}.pdf'
        response = FileResponse(BytesIO(content), content_type='application/pdf')
        response['Content-Length'] = str(len(content))
        response['Content-Disposition'] = (
            f'attachment; filename="{shp.shipment_no}-{suffix}.pdf"; '
            f"filename*=UTF-8''{quote(filename)}")
        return response


class ShipmentPrintLogView(CompanyContextMixin, APIView):
    """GET/POST /shipments/{id}/print-logs 打印记录（TicketPrintLog 模式复刻）。

    POST 入参 {template: shipment|packing, print_key: 'shipment:{id}:{tpl}'}：
    print_key 幂等（命中 reported=0 + duplicated），新 key 且该单该模板已有
    打印记录 → is_reprint=True（补打）。权限：上报 shipment.manage 或
    print.manage；历史 shipment.view。
    """

    def get(self, request, pk):
        self.check_perm('shipment.view')
        shp = _get_shipment(self.company, pk)
        logs = shp.print_logs.all()
        template = (request.query_params.get('template') or '').strip()
        if template:
            if template not in ShipmentPrintLog.Template.values:
                raise ValidationError({'template': '模板不合法（shipment/packing）'})
            logs = logs.filter(template=template)
        return Response(ShipmentPrintLogSerializer(logs, many=True).data)

    def post(self, request, pk):
        if not (self.has_perm('shipment.manage') or self.has_perm('print.manage')):
            raise PermissionDenied('没有该模块的操作权限')
        shp = _get_shipment(self.company, pk)
        template = request.data.get('template') or ShipmentPrintLog.Template.SHIPMENT
        if template not in ShipmentPrintLog.Template.values:
            raise ValidationError({'template': '模板不合法（shipment/packing）'})
        print_key = str(request.data.get('print_key') or '')[:64] or None
        is_reprint = ShipmentPrintLog.objects.filter(
            shipment=shp, template=template).exists()
        duplicated = []
        with transaction.atomic():
            if print_key:
                if ShipmentPrintLog.objects.filter(
                        shipment=shp, print_key=print_key).exists():
                    duplicated = [print_key]
                    return Response({'reported': 0, 'duplicated': duplicated})
            try:
                ShipmentPrintLog.objects.create(
                    company=self.company, shipment=shp, template=template,
                    operator=request.user, print_key=print_key,
                    is_reprint=is_reprint)
            except IntegrityError:  # 并发同 key：唯一约束兜底幂等
                duplicated = [print_key]
                return Response({'reported': 0, 'duplicated': duplicated})
        from apps.ops.models import AuditLog
        from apps.ops.services import audit
        audit(self.company, AuditLog.Type.OTHER, operator=request.user,
              message=('打印' + ('装箱单' if template == ShipmentPrintLog.Template.PACKING
                          else '出货单')
                      + f'：【{shp.shipment_no}】'
                      + ('（补打）' if is_reprint else '')))
        return Response({'reported': 1, 'duplicated': duplicated,
                         'is_reprint': is_reprint}, status=201)
