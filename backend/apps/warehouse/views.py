"""轻量仓库与质量追溯接口。

权限口径（用户拍板 2026-10-18）：查询与操作均为「管理员 + production 权限岗」，
统一走 check_perm('production')（has_perm 对管理员岗位恒 True）。
追溯只读血缘表（ProductionLotLink），与仓库单据解耦。
"""
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.http import Http404
from django.utils import timezone
from rest_framework import generics, status
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.mixins import CompanyContextMixin
from apps.core.pagination import DefaultPagination
from apps.warehouse.models import (
    IssueItem,
    Lot,
    MaterialIssue,
    ProductionLotLink,
    PurchaseOrder,
    StockIn,
)
from apps.warehouse.serializers import (
    IssueItemInputSerializer,
    LotLinkItemSerializer,
    LotSerializer,
    MaterialIssueSerializer,
    ProductionLotLinkSerializer,
    PurchaseItemInputSerializer,
    PurchaseOrderSerializer,
    StockInItemInputSerializer,
    StockInSerializer,
)


def _purge_deleted(model, **filters):
    """同业务键存在软删行时会撞唯一约束，先物理清理（项目既有约定）。"""
    model.all_objects.filter(is_deleted=True, **filters).delete()


def _next_doc_no(company, prefix, model, field) -> str:
    """单号自动生成：前缀+日期+当日序号，撞号顺延（含软删行）。"""
    day = timezone.localdate().strftime('%Y%m%d')
    n = 1
    while True:
        no = f'{prefix}{day}{n:02d}'
        if not model.all_objects.filter(company=company, **{field: no}).exists():
            return no
        n += 1


# ============ 面料批次（Lot） ============

class LotListCreateView(CompanyContextMixin, generics.ListCreateAPIView):
    """GET /lots 批次列表（keyword 模糊批次号/面料/颜色）；POST 新增批次。"""

    serializer_class = LotSerializer
    pagination_class = DefaultPagination

    def get_queryset(self):
        qs = Lot.objects.filter(company=self.company)
        keyword = self.request.query_params.get('keyword', '').strip()
        if keyword:
            qs = qs.filter(Q(lot_no__icontains=keyword) | Q(fabric_name__icontains=keyword)
                           | Q(color__icontains=keyword))
        return qs

    def perform_create(self, serializer):
        self.check_perm('warehouse.manage')
        lot_no = serializer.validated_data.get('lot_no', '').strip()
        if not lot_no:
            raise ValidationError({'lot_no': '批次号不能为空'})
        _purge_deleted(Lot, company=self.company, lot_no=lot_no)
        if Lot.objects.filter(company=self.company, lot_no=lot_no).exists():
            raise ValidationError({'lot_no': '批次号已存在'})
        serializer.save(company=self.company, lot_no=lot_no)


class LotDetailView(CompanyContextMixin, APIView):
    """GET/PUT/DELETE /lots/<pk>（删除前校验无血缘/领料引用）。"""

    def _get(self, pk) -> Lot:
        try:
            return Lot.objects.get(company=self.company, pk=pk)
        except Lot.DoesNotExist:
            raise Http404('批次不存在')

    def get(self, request, pk):
        return Response(LotSerializer(self._get(pk)).data)

    def put(self, request, pk):
        self.check_perm('warehouse.manage')
        lot = self._get(pk)
        new_no = str(request.data.get('lot_no') or '').strip()
        if new_no and new_no != lot.lot_no:
            _purge_deleted(Lot, company=self.company, lot_no=new_no)
            if Lot.objects.filter(company=self.company, lot_no=new_no).exclude(pk=pk).exists():
                raise ValidationError({'lot_no': '批次号已存在'})
            lot.lot_no = new_no
        for field in ('fabric_name', 'color', 'unit', 'remark'):
            if field in request.data:
                setattr(lot, field, str(request.data.get(field) or '')[:255])
        if 'qty' in request.data:
            lot.qty = request.data.get('qty') or 0
        if 'batch_date' in request.data:
            lot.batch_date = request.data.get('batch_date') or None
        lot.save()
        return Response(LotSerializer(lot).data)

    def delete(self, request, pk):
        self.check_perm('warehouse.manage')
        lot = self._get(pk)
        if ProductionLotLink.objects.filter(lot=lot).exists():
            raise ValidationError('该批次已有床次血缘，无法删除')
        if IssueItem.objects.filter(lot=lot).exists():
            raise ValidationError('该批次已有领料记录，无法删除')
        lot.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


# ============ 床次 × 批次血缘 ============

class ProductionLotLinkView(CompanyContextMixin, APIView):
    """GET/PUT /productions/<production_id>/lot-links 床次血缘（P0 手工挂批次入口）。

    PUT 整表重建：items=[{lot_id, qty}]；建床/编菲页与床次详情补填共用。
    """

    def get(self, request, production_id):
        self.check_perm('warehouse.manage')
        from apps.production.models import Production
        try:
            production = Production.objects.get(company=self.company, pk=production_id)
        except Production.DoesNotExist:
            raise Http404('床次订单不存在')
        links = (ProductionLotLink.objects.filter(production=production)
                 .select_related('lot'))
        return Response(ProductionLotLinkSerializer(links, many=True).data)

    def put(self, request, production_id):
        self.check_perm('warehouse.manage')
        from apps.production.models import Production
        try:
            production = Production.objects.get(company=self.company, pk=production_id)
        except Production.DoesNotExist:
            raise Http404('床次订单不存在')
        serializer = LotLinkItemSerializer(data=request.data.get('items'), many=True)
        serializer.is_valid(raise_exception=True)
        items = serializer.validated_data

        lot_ids = [item['lot_id'] for item in items]
        lots = {l.id: l for l in Lot.objects.filter(company=self.company, pk__in=lot_ids)}
        missing = [lid for lid in lot_ids if lid not in lots]
        if missing:
            raise ValidationError({'items': f'批次不存在：{missing[:5]}'})
        seen = set()
        for item in items:
            if item['lot_id'] in seen:
                raise ValidationError({'items': '存在重复批次'})
            seen.add(item['lot_id'])

        with transaction.atomic():
            # 整表重建（项目约定）：软删行先硬删，旧血缘软删留痕
            _purge_deleted(ProductionLotLink, production=production)
            ProductionLotLink.objects.filter(production=production).delete()
            ProductionLotLink.objects.bulk_create([
                ProductionLotLink(company=self.company, production=production,
                                  lot=lots[item['lot_id']], qty=item['qty'])
                for item in items
            ])
        links = ProductionLotLink.objects.filter(production=production).select_related('lot')
        return Response(ProductionLotLinkSerializer(links, many=True).data)


# ============ 追溯查询（正向 / 反向 / 终端） ============

def _lot_impact(company, lot_ids):
    """批次受影响面：{lot_id: (production_count, ticket_count, qty_total)}。"""
    if not lot_ids:
        return {}
    rows = (ProductionLotLink.objects
            .filter(company=company, lot_id__in=lot_ids)
            .values('lot_id')
            .annotate(pc=Count('production', distinct=True),
                      tc=Count('production__tickets', distinct=True),
                      qt=Sum('production__tickets__qty')))
    return {r['lot_id']: (r['pc'], r['tc'], int(r['qt'] or 0)) for r in rows}


class TraceLotSearchView(CompanyContextMixin, APIView):
    """GET /trace/lot?keyword= 批次搜索（含受影响面统计，列表页用）。"""

    def get(self, request):
        self.check_perm('trace.view')
        qs = Lot.objects.filter(company=self.company)
        keyword = self.request.query_params.get('keyword', '').strip()
        if keyword:
            qs = qs.filter(Q(lot_no__icontains=keyword) | Q(fabric_name__icontains=keyword)
                           | Q(color__icontains=keyword))
        qs = qs[:100]
        impact = _lot_impact(self.company, [l.id for l in qs])
        results = []
        for lot in qs:
            pc, tc, qt = impact.get(lot.id, (0, 0, 0))
            results.append({
                'id': lot.id, 'lot_no': lot.lot_no, 'fabric_name': lot.fabric_name,
                'color': lot.color, 'qty': str(lot.qty), 'unit': lot.unit,
                'batch_date': lot.batch_date,
                'production_count': pc, 'ticket_count': tc, 'qty_total': qt,
            })
        return Response({'results': results})


class TraceLotDetailView(CompanyContextMixin, APIView):
    """GET /trace/lot/<pk> 正向追溯：批次 → 受影响床次/菲票 → 客户/来源单号。"""

    def get(self, request, pk):
        self.check_perm('trace.view')
        try:
            lot = Lot.objects.get(company=self.company, pk=pk)
        except Lot.DoesNotExist:
            raise Http404('批次不存在')
        links = (ProductionLotLink.objects.filter(lot=lot)
                 .select_related('production', 'production__style',
                                 'production__customer'))
        productions = []
        ticket_total = 0
        qty_total = 0
        for link in links:
            p = link.production
            tickets = p.tickets.all()
            tc = tickets.count()
            tq = int(tickets.aggregate(s=Sum('qty'))['s'] or 0)
            ticket_total += tc
            qty_total += tq
            productions.append({
                'id': p.id, 'bed_no': p.bed_no,
                'style_no': p.style.style_no, 'style_name': p.style.name,
                'customer_name': p.customer.name if p.customer else '',
                'source_order_no': p.source_order_no,
                'order_type': p.order_type,
                'total_qty': p.total_qty, 'issue_qty': str(link.qty),
                'ticket_count': tc,
            })
        return Response({
            'lot': LotSerializer(lot).data,
            'productions': productions,
            'impact': {'production_count': len(productions),
                       'ticket_count': ticket_total, 'qty_total': qty_total},
        })


class TraceReverseView(CompanyContextMixin, APIView):
    """GET /trace/reverse 反向追溯：客户投诉某款某色 → 定位床次 + 批次。

    参数：style_no / color / customer_id / date_from / date_to（均可选，组合过滤）。
    """

    def get(self, request):
        self.check_perm('trace.view')
        from apps.production.models import Production
        from django.utils.dateparse import parse_date

        qs = (Production.objects.filter(company=self.company)
              .select_related('style', 'customer'))
        style_no = request.query_params.get('style_no', '').strip()
        if style_no:
            qs = qs.filter(Q(style__style_no__icontains=style_no)
                           | Q(style__name__icontains=style_no))
        color = request.query_params.get('color', '').strip()
        if color:
            # 颜色在菲票上（自由文本），经菲票反查床次
            qs = qs.filter(tickets__color__icontains=color)
        customer_id = request.query_params.get('customer_id', '').strip()
        if customer_id:
            qs = qs.filter(customer_id=customer_id)
        date_from = parse_date(request.query_params.get('date_from') or '')
        if date_from:
            qs = qs.filter(created_at__date__gte=date_from)
        date_to = parse_date(request.query_params.get('date_to') or '')
        if date_to:
            qs = qs.filter(created_at__date__lte=date_to)
        qs = qs.distinct()[:100]

        impact = _lot_impact(self.company, [])  # 占位避免误用；逐床次取批次
        matches = []
        for p in qs:
            links = p.lot_links.select_related('lot')
            matches.append({
                'production_id': p.id, 'bed_no': p.bed_no,
                'style_no': p.style.style_no, 'style_name': p.style.name,
                'customer_name': p.customer.name if p.customer else '',
                'source_order_no': p.source_order_no,
                'order_type': p.order_type,
                'total_qty': p.total_qty,
                'created_at': p.created_at,
                'lots': [{'id': l.lot.id, 'lot_no': l.lot.lot_no,
                          'color': l.lot.color, 'qty': str(l.qty)} for l in links],
            })
        return Response({'matches': matches})


class TraceTicketView(CompanyContextMixin, APIView):
    """GET /trace/ticket/<pk> 追溯终端：一张菲票的全链路。

    返回菲票 + 床次（款式/客户/来源单号）+ 批次血缘 + 登记时间线（工序/员工/时间）+ 打印痕迹。
    旧票（七段码）扫码后按 bed_no+bundle_no 命中菲票 id 即可进本接口。
    """

    def get(self, request, pk):
        self.check_perm('trace.view')
        from apps.catalog.models import Ticket
        from apps.records.models import Record

        try:
            ticket = (Ticket.objects.filter(company=self.company, pk=pk)
                      .select_related('production', 'production__style',
                                      'production__customer').first())
        except (ValueError, TypeError):
            ticket = None
        if not ticket:
            raise Http404('菲票不存在')
        p = ticket.production
        links = p.lot_links.select_related('lot')
        records = (Record.objects.filter(company=self.company, ticket=ticket)
                   .select_related('process', 'user', 'operator'))
        timeline = [{
            'id': r.id,
            'process': r.process.name,
            'user': r.user.real_name or str(r.user),
            'operator': (r.operator.real_name or str(r.operator)) if r.operator else '',
            'qty': r.qty, 'price': str(r.price),
            'registered_at': r.registered_at,
        } for r in records]
        return Response({
            'ticket': {
                'id': ticket.id, 'bed_no': ticket.bed_no, 'bundle_no': ticket.bundle_no,
                'color': ticket.color, 'size': ticket.size, 'qty': ticket.qty,
                'fabric': ticket.fabric, 'piece_no': ticket.piece_no,
                'qr_code': ticket.qr_code,
            },
            'production': {
                'id': p.id, 'bed_no': p.bed_no,
                'style_no': p.style.style_no, 'style_name': p.style.name,
                'customer_name': p.customer.name if p.customer else '',
                'source_order_no': p.source_order_no,
                'order_type': p.order_type, 'total_qty': p.total_qty,
                'fabric': p.fabric,
            },
            'lots': [{'id': l.lot.id, 'lot_no': l.lot.lot_no,
                      'fabric_name': l.lot.fabric_name, 'color': l.lot.color,
                      'qty': str(l.qty), 'batch_date': l.lot.batch_date}
                     for l in links],
            'records': timeline,
            'print': {'is_printed': ticket.is_printed,
                      'print_count': ticket.print_count, 'print_at': ticket.print_at},
        })


class TraceProductionView(CompanyContextMixin, APIView):
    """GET /trace/production/<pk> 床次追溯视角：批次血缘 + 菲票汇总 + 领料流水。"""

    def get(self, request, pk):
        self.check_perm('trace.view')
        from apps.production.models import Production

        try:
            p = (Production.objects.filter(company=self.company, pk=pk)
                 .select_related('style', 'customer').get())
        except (Production.DoesNotExist, ValueError, TypeError):
            raise Http404('床次订单不存在')
        links = p.lot_links.select_related('lot')
        tickets = p.tickets.all()
        issues = (IssueItem.objects.filter(production=p)
                  .select_related('lot', 'issue'))
        return Response({
            'production': {
                'id': p.id, 'bed_no': p.bed_no,
                'style_no': p.style.style_no, 'style_name': p.style.name,
                'customer_name': p.customer.name if p.customer else '',
                'source_order_no': p.source_order_no,
                'order_type': p.order_type, 'total_qty': p.total_qty,
                'fabric': p.fabric,
            },
            'lots': [{'id': l.lot.id, 'lot_no': l.lot.lot_no,
                      'fabric_name': l.lot.fabric_name, 'color': l.lot.color,
                      'qty': str(l.qty)} for l in links],
            'ticket_count': tickets.count(),
            'qty_total': int(tickets.aggregate(s=Sum('qty'))['s'] or 0),
            'issues': [{'issue_no': i.issue.issue_no, 'lot_no': i.lot.lot_no,
                        'qty': str(i.qty), 'issue_at': i.issue.issue_at,
                        'status': i.issue.status} for i in issues],
        })


# ============ 采购单 ============

class PurchaseOrderListCreateView(CompanyContextMixin, generics.ListCreateAPIView):
    """GET /purchase-orders 列表（status 筛选）；POST 新增（单号留空自动生成）。"""

    serializer_class = PurchaseOrderSerializer
    pagination_class = DefaultPagination

    def get_queryset(self):
        qs = PurchaseOrder.objects.filter(company=self.company).prefetch_related('items')
        st = self.request.query_params.get('status')
        if st:
            qs = qs.filter(status=st)
        return qs

    def perform_create(self, serializer):
        self.check_perm('warehouse.manage')
        # 日期缺省=今天（前端新建不传 ordered_at；模型列是 DateField）
        if not serializer.validated_data.get('ordered_at'):
            serializer.validated_data['ordered_at'] = timezone.localdate()
        order_no = (serializer.validated_data.get('order_no') or '').strip()
        if not order_no:
            order_no = _next_doc_no(self.company, 'CG', PurchaseOrder, 'order_no')
        _purge_deleted(PurchaseOrder, company=self.company, order_no=order_no)
        items = []
        if isinstance(self.request.data.get('items'), list):
            it = PurchaseItemInputSerializer(data=self.request.data['items'], many=True)
            it.is_valid(raise_exception=True)
            items = it.validated_data
        with transaction.atomic():
            order = serializer.save(company=self.company, order_no=order_no)
            if items:
                from apps.warehouse.models import PurchaseItem
                PurchaseItem.objects.bulk_create(
                    [PurchaseItem(order=order, **item) for item in items])


class PurchaseOrderDetailView(CompanyContextMixin, APIView):
    """GET/PUT/DELETE /purchase-orders/<pk>（仅草稿可改删）。"""

    def _get(self, pk) -> PurchaseOrder:
        try:
            return PurchaseOrder.objects.get(company=self.company, pk=pk)
        except PurchaseOrder.DoesNotExist:
            raise Http404('采购单不存在')

    def get(self, request, pk):
        return Response(PurchaseOrderSerializer(self._get(pk)).data)

    def put(self, request, pk):
        self.check_perm('warehouse.manage')
        order = self._get(pk)
        if order.status != PurchaseOrder.Status.DRAFT:
            raise ValidationError('已完成采购单不可修改')
        for field in ('supplier_name', 'remark'):
            if field in request.data:
                setattr(order, field, str(request.data.get(field) or '')[:255])
        if 'ordered_at' in request.data:
            order.ordered_at = request.data.get('ordered_at') or order.ordered_at
        order.save()
        return Response(PurchaseOrderSerializer(order).data)

    def delete(self, request, pk):
        self.check_perm('warehouse.manage')
        order = self._get(pk)
        if order.status != PurchaseOrder.Status.DRAFT:
            raise ValidationError('已完成采购单不可删除')
        order.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class PurchaseOrderConfirmView(CompanyContextMixin, APIView):
    """POST /purchase-orders/<pk>/confirm 草稿 → 完成（终态，不可再改）。"""

    def post(self, request, pk):
        self.check_perm('warehouse.manage')
        try:
            order = PurchaseOrder.objects.get(company=self.company, pk=pk)
        except PurchaseOrder.DoesNotExist:
            raise Http404('采购单不存在')
        if order.status != PurchaseOrder.Status.DRAFT:
            raise ValidationError('该采购单已是完成状态')
        order.status = PurchaseOrder.Status.DONE
        order.save(update_fields=['status', 'updated_at'])
        return Response(PurchaseOrderSerializer(order).data)


# ============ 入库单（批次在此诞生） ============

class StockInListCreateView(CompanyContextMixin, generics.ListCreateAPIView):
    """GET /stock-ins 列表；POST 新增。

    行级 lot_no 自动 get_or_create 批次（沿用供应商原始编号）；新建批次回填来源入库行。
    """

    serializer_class = StockInSerializer
    pagination_class = DefaultPagination

    def get_queryset(self):
        qs = StockIn.objects.filter(company=self.company).prefetch_related('items')
        st = self.request.query_params.get('status')
        if st:
            qs = qs.filter(status=st)
        return qs

    def perform_create(self, serializer):
        self.check_perm('warehouse.manage')
        from apps.warehouse.models import StockInItem
        in_no = (serializer.validated_data.get('in_no') or '').strip()
        if not in_no:
            in_no = _next_doc_no(self.company, 'RK', StockIn, 'in_no')
        _purge_deleted(StockIn, company=self.company, in_no=in_no)
        raw_items = self.request.data.get('items')
        items = []
        if isinstance(raw_items, list) and raw_items:
            it = StockInItemInputSerializer(data=raw_items, many=True)
            it.is_valid(raise_exception=True)
            items = it.validated_data
        if not items:
            raise ValidationError({'items': '请至少提交一项入库明细'})
        with transaction.atomic():
            doc = serializer.save(company=self.company, in_no=in_no,
                                  in_at=serializer.validated_data.get('in_at') or timezone.now(),
                                  operator=self.request.user)
            for item in items:
                lot, created = Lot.all_objects.get_or_create(
                    company=self.company, lot_no=item['lot_no'], is_deleted=False,
                    defaults={'fabric_name': item['fabric_name'], 'color': item['color'],
                              'qty': item['qty'], 'unit': item['unit'] or '米',
                              'source_in_id': None})
                row = StockInItem(stock_in=doc, fabric_name=item['fabric_name'],
                                  color=item['color'], lot_no=item['lot_no'],
                                  qty=item['qty'], unit=item['unit'] or '米', lot=lot)
                row.save()
                if created:
                    # 新建批次回填来源入库行（追溯「批次 → 入库单」的锚点）
                    Lot.objects.filter(pk=lot.pk).update(source_in=row)


class StockInDetailView(CompanyContextMixin, APIView):
    """GET/PUT/DELETE /stock-ins/<pk>（仅草稿可改删）。"""

    def _get(self, pk) -> StockIn:
        try:
            return StockIn.objects.get(company=self.company, pk=pk)
        except StockIn.DoesNotExist:
            raise Http404('入库单不存在')

    def get(self, request, pk):
        return Response(StockInSerializer(self._get(pk)).data)

    def put(self, request, pk):
        self.check_perm('warehouse.manage')
        doc = self._get(pk)
        if doc.status != StockIn.Status.DRAFT:
            raise ValidationError('已完成入库单不可修改')
        if 'remark' in request.data:
            doc.remark = str(request.data.get('remark') or '')[:255]
        if 'in_at' in request.data and request.data.get('in_at'):
            doc.in_at = request.data.get('in_at')
        doc.save()
        return Response(StockInSerializer(doc).data)

    def delete(self, request, pk):
        self.check_perm('warehouse.manage')
        doc = self._get(pk)
        if doc.status != StockIn.Status.DRAFT:
            raise ValidationError('已完成入库单不可删除')
        doc.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class StockInConfirmView(CompanyContextMixin, APIView):
    """POST /stock-ins/<pk>/confirm 草稿 → 完成。"""

    def post(self, request, pk):
        self.check_perm('warehouse.manage')
        try:
            doc = StockIn.objects.get(company=self.company, pk=pk)
        except StockIn.DoesNotExist:
            raise Http404('入库单不存在')
        if doc.status != StockIn.Status.DRAFT:
            raise ValidationError('该入库单已是完成状态')
        doc.status = StockIn.Status.DONE
        doc.save(update_fields=['status', 'updated_at'])
        return Response(StockInSerializer(doc).data)


# ============ 领料单（确认钩子写血缘） ============

class MaterialIssueListCreateView(CompanyContextMixin, generics.ListCreateAPIView):
    """GET /material-issues 列表；POST 新增（单号留空自动生成）。"""

    serializer_class = MaterialIssueSerializer
    pagination_class = DefaultPagination

    def get_queryset(self):
        qs = MaterialIssue.objects.filter(company=self.company).prefetch_related('items')
        st = self.request.query_params.get('status')
        if st:
            qs = qs.filter(status=st)
        return qs

    def perform_create(self, serializer):
        self.check_perm('warehouse.manage')
        from apps.production.models import Production
        from apps.warehouse.models import IssueItem
        issue_no = (serializer.validated_data.get('issue_no') or '').strip()
        if not issue_no:
            issue_no = _next_doc_no(self.company, 'LL', MaterialIssue, 'issue_no')
        _purge_deleted(MaterialIssue, company=self.company, issue_no=issue_no)
        raw_items = self.request.data.get('items')
        items = []
        if isinstance(raw_items, list) and raw_items:
            it = IssueItemInputSerializer(data=raw_items, many=True)
            it.is_valid(raise_exception=True)
            items = it.validated_data
        if not items:
            raise ValidationError({'items': '请至少提交一项领料明细'})
        lots = {l.id: l for l in Lot.objects.filter(company=self.company,
                                                    pk__in=[i['lot_id'] for i in items])}
        productions = {p.id: p for p in Production.objects.filter(
            company=self.company, pk__in=[i['production_id'] for i in items])}
        for item in items:
            if item['lot_id'] not in lots:
                raise ValidationError({'items': f"批次 {item['lot_id']} 不存在"})
            if item['production_id'] not in productions:
                raise ValidationError({'items': f"床次 {item['production_id']} 不存在"})
        with transaction.atomic():
            doc = serializer.save(company=self.company, issue_no=issue_no,
                                  issue_at=serializer.validated_data.get('issue_at') or timezone.now(),
                                  operator=self.request.user)
            for item in items:
                IssueItem.objects.create(issue=doc,
                                         production=productions[item['production_id']],
                                         lot=lots[item['lot_id']], qty=item['qty'])


class MaterialIssueDetailView(CompanyContextMixin, APIView):
    """GET/PUT/DELETE /material-issues/<pk>（仅草稿可改删）。"""

    def _get(self, pk) -> MaterialIssue:
        try:
            return MaterialIssue.objects.get(company=self.company, pk=pk)
        except MaterialIssue.DoesNotExist:
            raise Http404('领料单不存在')

    def get(self, request, pk):
        return Response(MaterialIssueSerializer(self._get(pk)).data)

    def put(self, request, pk):
        self.check_perm('warehouse.manage')
        doc = self._get(pk)
        if doc.status != MaterialIssue.Status.DRAFT:
            raise ValidationError('已确认领料单不可修改')
        if 'remark' in request.data:
            doc.remark = str(request.data.get('remark') or '')[:255]
        if 'issue_at' in request.data and request.data.get('issue_at'):
            doc.issue_at = request.data.get('issue_at')
        doc.save()
        return Response(MaterialIssueSerializer(doc).data)

    def delete(self, request, pk):
        self.check_perm('warehouse.manage')
        doc = self._get(pk)
        if doc.status != MaterialIssue.Status.DRAFT:
            raise ValidationError('已确认领料单不可删除')
        doc.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class MaterialIssueConfirmView(CompanyContextMixin, APIView):
    """POST /material-issues/<pk>/confirm 草稿 → 已确认，并自动写床次×批次血缘。

    同床次同批次多次领料：血缘用量累加（流水口径）。
    """

    def post(self, request, pk):
        self.check_perm('warehouse.manage')
        try:
            doc = (MaterialIssue.objects.filter(company=self.company, pk=pk)
                   .prefetch_related('items').first())
        except (ValueError, TypeError):
            doc = None
        if not doc:
            raise Http404('领料单不存在')
        if doc.status != MaterialIssue.Status.DRAFT:
            raise ValidationError('该领料单已是确认状态')
        with transaction.atomic():
            for item in doc.items.all():
                link, created = (ProductionLotLink.all_objects
                                 .get_or_create(company=self.company,
                                                production=item.production,
                                                lot=item.lot, is_deleted=False,
                                                defaults={'qty': item.qty}))
                if not created:
                    link.qty = (link.qty or 0) + item.qty
                    link.is_deleted = False
                    link.save(update_fields=['qty', 'is_deleted', 'updated_at'])
            doc.status = MaterialIssue.Status.CONFIRMED
            doc.save(update_fields=['status', 'updated_at'])
        return Response(MaterialIssueSerializer(doc).data)
