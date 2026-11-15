"""轻量仓库与质量追溯接口。

权限口径（用户拍板 2026-10-18）：查询与操作均为「管理员 + production 权限岗」，
统一走 check_perm('production')（has_perm 对管理员岗位恒 True）。
追溯只读血缘表（ProductionLotLink），与仓库单据解耦。
"""
from decimal import Decimal

from django.db import transaction
from django.db.models import Count, DecimalField, F, Q, Sum
from django.http import Http404
from django.utils import timezone
from rest_framework import generics, status
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.mixins import CompanyContextMixin
from apps.core.pagination import DefaultPagination
from apps.ops.models import AuditLog
from apps.ops.services import audit
# D1 期初导入复用 tech 导入公共件（xlsx/JSON 行归一 + all-or-nothing 语义同款）
from apps.tech.views import _ImportMixin  # noqa: E402  (apps.tech.views 不反向依赖本模块，无环)
from apps.warehouse.services import convert_price_to_main_unit
from apps.warehouse.models import (
    IssueItem,
    Lot,
    MaterialIssue,
    ProductionLotLink,
    PurchaseItem,
    PurchaseOrder,
    Receipt,
    ReceiptItem,
    StockIn,
    StockInItem,
    Supplier,
)
from apps.warehouse.serializers import (
    IssueItemInputSerializer,
    LotLinkItemSerializer,
    LotSerializer,
    MaterialIssueSerializer,
    ProductionLotLinkSerializer,
    PurchaseItemInputSerializer,
    PurchaseOrderSerializer,
    ReceiptItemInputSerializer,
    ReceiptSerializer,
    StockInItemInputSerializer,
    StockInSerializer,
    SupplierSerializer,
)


def _purge_deleted(model, **filters):
    """同业务键存在软删行时会撞唯一约束，先物理清理（项目既有约定）。"""
    model.all_objects.filter(is_deleted=True, **filters).delete()


def _next_doc_no(company, prefix, model, field) -> str:
    """单号自动生成：前缀+日期+当日序号，撞号顺延（含软删行）。

    生成逻辑收编 apps.core.numbering（2026-10-29）：首个空号语义（跳号回填，
    2 位流水）由 first_free=True + digits=2 保留。
    """
    from apps.core.numbering import next_no
    return next_no(company, model, field, prefix, digits=2, first_free=True)


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
        if 'unit_cost' in request.data:
            # P1 库存价链：批次单价补录入口（挂 warehouse.manage），存量手工批次靠此收敛
            uc = request.data.get('unit_cost')
            lot.unit_cost = Decimal(str(uc)) if uc not in (None, '', 0, '0') else None
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
                rows = []
                for item in items:
                    # P2：物料锚点解析（需求汇总/在途按物料聚合，可空）
                    mat = None
                    if item.get('material_id'):
                        from apps.tech.models import Material
                        mat = (Material.objects
                               .filter(pk=item['material_id'], company=self.company).first())
                        if mat is None:
                            raise ValidationError({'items': f"物料 {item['material_id']} 不存在"})
                    # D19 单价单位规范化：price_unit 非空 → 按 material 折算主单位价，
                    # price 存储恒为主单位规范化值（成本侧语义零改动）
                    price = item['price']
                    price_unit = item.get('price_unit') or ''
                    if price_unit:
                        if mat is None:
                            raise ValidationError({'items': '单价单位换算需先选择物料'})
                        converted = convert_price_to_main_unit(price, price_unit, mat)
                        if converted is None:
                            raise ValidationError(
                                {'items': f'物料 {mat.code} 缺克重/幅宽，无法换算单价'})
                        price = converted
                    rows.append(PurchaseItem(order=order, material=mat,
                                             demand_source=item.get('demand_source', 'manual'),
                                             fabric_name=item['fabric_name'],
                                             color=item['color'], qty=item['qty'],
                                             unit=item['unit'], price=price,
                                             price_unit=price_unit))
                PurchaseItem.objects.bulk_create(rows)


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
        from apps.warehouse.models import PurchaseItem, StockInItem
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
        # P1 库存价链：行级 purchase_item 校验归属；单价缺省带出 PO 行价
        for item in items:
            if item.get('purchase_item'):
                pi = (PurchaseItem.objects.filter(pk=item['purchase_item'])
                      .select_related('order').first())
                if pi is None or pi.order.company_id != self.company.id:
                    raise ValidationError({'items': f"采购行 {item['purchase_item']} 不存在"})
                item['_pi'] = pi
                if item.get('unit_price') is None:
                    item['unit_price'] = pi.price
            else:
                item['_pi'] = None
            # P2 需求汇总库存口径的物料锚点（可空，手工入库可选填）
            item['_mat'] = None
            if item.get('material_id'):
                from apps.tech.models import Material
                item['_mat'] = (Material.objects
                                .filter(pk=item['material_id'], company=self.company).first())
                if item['_mat'] is None:
                    raise ValidationError({'items': f"物料 {item['material_id']} 不存在"})
            # D19 单价单位规范化：手录单价带 price_unit → 折算主单位价（存储恒规范化）；
            # 无手录价（随采购行带出）时采购价已规范化，price_unit 仅随行留痕
            item['_price_unit'] = item.get('price_unit') or ''
            if item['_price_unit'] and item.get('unit_price') is not None:
                if item['_mat'] is None:
                    raise ValidationError({'items': '单价单位换算需先选择物料'})
                converted = convert_price_to_main_unit(item['unit_price'],
                                                       item['_price_unit'], item['_mat'])
                if converted is None:
                    raise ValidationError(
                        {'items': f"物料 {item['_mat'].code} 缺克重/幅宽，无法换算单价"})
                item['unit_price'] = converted
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
                                  qty=item['qty'], unit=item['unit'] or '米', lot=lot,
                                  unit_price=item.get('unit_price'),
                                  price_unit=item.get('_price_unit') or '',
                                  purchase_item=item['_pi'],
                                  material=item.get('_mat'))
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
    """POST /stock-ins/<pk>/confirm 草稿 → 完成。

    P1 库存价链（D4）：确认时行带 purchase_item 而 unit_price 为空 → 默认带出
    PO 行价；批次（Lot）无单价且行有价 → 批次单价由入库价折算落定
    （存量手工批次价保持不动，补录走 Lot PUT）。
    """

    def post(self, request, pk):
        self.check_perm('warehouse.manage')
        with transaction.atomic():
            # 状态检查必须在行锁内；否则两个请求会同时看到 draft 并重复确认。
            doc = (StockIn.objects.select_for_update()
                   .filter(company=self.company, pk=pk)
                   .prefetch_related('items__purchase_item__order').first())
            if not doc:
                raise Http404('入库单不存在')
            if doc.status != StockIn.Status.DRAFT:
                raise ValidationError('该入库单已是完成状态')
            for item in doc.items.all():
                if item.purchase_item_id and item.unit_price is None:
                    item.unit_price = item.purchase_item.price
                    item.save(update_fields=['unit_price', 'updated_at'])
                if (item.lot_id and item.unit_price is not None
                        and item.lot.unit_cost is None):
                    Lot.objects.filter(pk=item.lot_id, unit_cost__isnull=True) \
                        .update(unit_cost=item.unit_price)
            doc.status = StockIn.Status.DONE
            doc.save(update_fields=['status', 'updated_at'])
        return Response(StockInSerializer(doc).data)


# ============ 领料单（确认钩子写血缘） ============

class MaterialIssueListCreateView(CompanyContextMixin, generics.ListCreateAPIView):
    """GET /material-issues 列表；POST 新增（单号留空自动生成）。

    P1 定额化：行可带 bom_item_id（选定额行，服务端算定 material/qty_req）或
    material_id（无 BOM 手选，qty_req=NULL=无定额）；qty 折算主单位 > qty_req
    时零拦截落 over 标记；type=return 为退料单（确认时冲减已领量）。
    """

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
        from apps.warehouse.services import convert_to_main_unit, quota_qty_for
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
        # P1：定额行/物料解析与超额判定（登记侧零拦截）
        rows = []
        for item in items:
            lot = lots[item['lot_id']]
            production = productions[item['production_id']]
            bom_item = None
            material = None
            qty_req = item.get('qty_req')
            if item.get('bom_item_id'):
                from apps.tech.models import BomItem
                bom_item = (BomItem.objects.select_related('material', 'bom')
                            .filter(pk=item['bom_item_id'], bom__company=self.company).first())
                if bom_item is None:
                    raise ValidationError({'items': f"定额行 {item['bom_item_id']} 不存在"})
                material = bom_item.material
                qty_req = quota_qty_for(bom_item, production)
            elif item.get('material_id'):
                from apps.tech.models import Material
                material = (Material.objects
                            .filter(pk=item['material_id'], company=self.company).first())
                if material is None:
                    raise ValidationError({'items': f"物料 {item['material_id']} 不存在"})
            over = False
            if material is not None and qty_req is not None:
                q_main = convert_to_main_unit(item['qty'], lot.unit, material)
                over = q_main is not None and q_main > qty_req   # 缺参跳过判定（pending）
            rows.append((item, lot, production, bom_item, material, qty_req, over))
        with transaction.atomic():
            doc = serializer.save(company=self.company, issue_no=issue_no,
                                  issue_at=serializer.validated_data.get('issue_at') or timezone.now(),
                                  operator=self.request.user)
            IssueItem.objects.bulk_create([
                IssueItem(issue=doc, production=production, lot=lot, qty=item['qty'],
                          bom_item=bom_item, material=material, qty_req=qty_req,
                          over=over, over_reason=(item.get('over_reason') or '')[:255])
                for item, lot, production, bom_item, material, qty_req, over in rows
            ])


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
    P1 退料（type=return）：qty 为正数=退回仓库量，确认时冲减血缘已领量（地板 0）。
    """

    def post(self, request, pk):
        self.check_perm('warehouse.manage')
        with transaction.atomic():
            # 锁同一领料单，第二个请求在锁释放后重读状态并拒绝重复累计血缘用量。
            doc = (MaterialIssue.objects.select_for_update()
                   .filter(company=self.company, pk=pk)
                   .prefetch_related('items').first())
            if not doc:
                raise Http404('领料单不存在')
            if doc.status != MaterialIssue.Status.DRAFT:
                raise ValidationError('该领料单已是确认状态')
            is_return = doc.type == MaterialIssue.Type.RETURN
            for item in doc.items.all():
                link, created = (ProductionLotLink.all_objects
                                 .get_or_create(company=self.company,
                                                production=item.production,
                                                lot=item.lot, is_deleted=False,
                                                defaults={'qty': 0 if is_return else item.qty}))
                if is_return:
                    # 退料冲减已领量，不为负
                    link.qty = max(Decimal('0'), (link.qty or Decimal('0')) - item.qty)
                    link.is_deleted = False
                    link.save(update_fields=['qty', 'is_deleted', 'updated_at'])
                elif not created:
                    link.qty = (link.qty or 0) + item.qty
                    link.is_deleted = False
                    link.save(update_fields=['qty', 'is_deleted', 'updated_at'])
            doc.status = MaterialIssue.Status.CONFIRMED
            doc.save(update_fields=['status', 'updated_at'])
            # P1 成本域 stale 埋点①（领料确认）：领料行床次的挂单订单成本翻 stale
            from apps.cost.services import mark_issue_confirmed_stale
            mark_issue_confirmed_stale(self.company, doc)
        return Response(MaterialIssueSerializer(doc).data)


def _s(v) -> str:
    """Decimal → 定长两位字符串（报表口径稳定）。"""
    return format(Decimal(v).quantize(Decimal('0.01')), 'f')


class IssueDivergenceView(CompanyContextMixin, APIView):
    """GET /issues/divergence 超额领料差异报表（定额 vs 实领 + 超领行 + 无定额行）。

    过滤键：date_from / date_to（领料日期）/ order_id / production_id / material_id。
    口径：只统计已确认单（与材料成本取数一致）；qty_issued 单位=Material 主单位
    （Lot 单位不同时按克重×幅宽折算，缺参行进 pending_units 待补清单，不入合计）。
    双口径：qty_req_snapshot=发料时快照 Σ IssueItem.qty_req；
            qty_req_current=按当前 active BOM 重算的应发（改版差额一目了然）。
    """

    def get(self, request):
        self.check_perm('warehouse.manage')
        from django.utils.dateparse import parse_date
        from apps.production.models import Production
        from apps.warehouse.services import (convert_to_main_unit, current_req_map,
                                             item_over_state)

        qs = (IssueItem.objects
              .filter(issue__company=self.company,
                      issue__status=MaterialIssue.Status.CONFIRMED)
              .select_related('issue', 'production', 'production__style',
                              'production__order', 'lot', 'material'))
        date_from = parse_date(request.query_params.get('date_from') or '')
        if date_from:
            qs = qs.filter(issue__issue_at__date__gte=date_from)
        date_to = parse_date(request.query_params.get('date_to') or '')
        if date_to:
            qs = qs.filter(issue__issue_at__date__lte=date_to)
        order_id = request.query_params.get('order_id', '').strip()
        if order_id:
            qs = qs.filter(production__order_id=order_id)
        production_id = request.query_params.get('production_id', '').strip()
        if production_id:
            qs = qs.filter(production_id=production_id)
        material_id = request.query_params.get('material_id', '').strip()
        if material_id:
            qs = qs.filter(material_id=material_id)
        items = list(qs)

        productions = {p.id: p for p in Production.objects.filter(
            company=self.company, pk__in={it.production_id for it in items})
            .select_related('style', 'order')}
        req_current = current_req_map(list(productions.values()))

        rows = {}
        pending_units = []
        for it in items:
            prod = productions.get(it.production_id)
            mat = it.material
            # 按床次×物料聚合（跨领料单求和）；无物料存量行按批次单列
            key = (it.production_id, mat.id if mat else 0, '' if mat else it.lot.lot_no)
            row = rows.get(key)
            if row is None:
                order = prod.order if prod else None
                row = rows[key] = {
                    'production_id': it.production_id,
                    'bed_no': prod.bed_no if prod else '',
                    'style_no': prod.style.style_no if prod and prod.style_id else '',
                    'order_id': order.id if order else None,
                    'order_no': order.order_no if order else '',
                    'material_id': mat.id if mat else None,
                    'material_code': (mat.code or '') if mat else '',
                    'material_name': mat.name if mat else '',
                    'material_unit': (mat.unit or '') if mat else '',
                    'lot_no': '' if mat else it.lot.lot_no,
                    'qty_issued': Decimal('0'),
                    'qty_returned': Decimal('0'),
                    'qty_req_snapshot': Decimal('0'),
                    'qty_req_current': req_current.get(
                        (it.production_id, mat.id if mat else None)),
                    'over_qty': Decimal('0'),
                    'no_req_qty': Decimal('0'),
                    'over': False,
                    'unit_pending': False,
                    'over_rows': [],
                }
            state, _ = item_over_state(it)
            # 退料行可能无 qty_req（item_over_state 会早退 no_req）——直接换算主单位量
            q_main = (convert_to_main_unit(it.qty, it.lot.unit, mat)
                      if mat is not None else None)
            if it.issue.type == MaterialIssue.Type.RETURN and mat is not None:
                # 退料冲减（按主单位折算；缺参进待补清单）
                if q_main is None:
                    row['unit_pending'] = True
                    pending_units.append(
                        {'issue_no': it.issue.issue_no, 'lot_no': it.lot.lot_no,
                         'lot_unit': it.lot.unit, 'material_unit': mat.unit,
                         'qty': str(it.qty)})
                else:
                    row['qty_returned'] += q_main
            elif mat is None or it.qty_req is None:
                # 无定额行（含存量行）：进「无定额领料占比」；换算缺参同时进待补清单
                if q_main is None:
                    row['no_req_qty'] += it.qty
                    if mat is not None:
                        row['unit_pending'] = True
                        pending_units.append(
                            {'issue_no': it.issue.issue_no, 'lot_no': it.lot.lot_no,
                             'lot_unit': it.lot.unit, 'material_unit': mat.unit,
                             'qty': str(it.qty)})
                else:
                    row['no_req_qty'] += q_main
            elif q_main is None:
                # 有定额但折算缺参：跳过 over 判定与合计，进待补清单
                row['unit_pending'] = True
                pending_units.append(
                    {'issue_no': it.issue.issue_no, 'lot_no': it.lot.lot_no,
                     'lot_unit': it.lot.unit, 'material_unit': mat.unit,
                     'qty': str(it.qty)})
            else:
                row['qty_issued'] += q_main
                # 发料快照：Σ IssueItem.qty_req（仅领料行；退料不冲快照定额）
                row['qty_req_snapshot'] += it.qty_req
                if q_main > it.qty_req:
                    row['over_qty'] += q_main - it.qty_req
            if state == 'over' or it.over:
                row['over'] = True
            if state in ('over', 'pending') or it.over:
                row['over_rows'].append({
                    'issue_id': it.issue_id, 'issue_no': it.issue.issue_no,
                    'issue_type': it.issue.type, 'lot_no': it.lot.lot_no,
                    'qty': str(it.qty),
                    'qty_req': str(it.qty_req) if it.qty_req is not None else None,
                    'state': state, 'over': it.over, 'over_reason': it.over_reason,
                })

        out_rows = []
        issued_total = Decimal('0')
        no_req_total = Decimal('0')
        over_total = Decimal('0')
        req_total = Decimal('0')
        for key in sorted(rows, key=lambda k: (k[0], k[1], k[2])):
            r = rows[key]
            issued_total += r['qty_issued']
            no_req_total += r['no_req_qty']
            over_total += r['over_qty']
            req_total += r['qty_req_snapshot']
            out_rows.append({
                'production_id': r['production_id'], 'bed_no': r['bed_no'],
                'style_no': r['style_no'], 'order_id': r['order_id'],
                'order_no': r['order_no'],
                'material_id': r['material_id'], 'material_code': r['material_code'],
                'material_name': r['material_name'], 'material_unit': r['material_unit'],
                'lot_no': r['lot_no'],
                'qty_issued': _s(r['qty_issued']),
                'qty_returned': _s(r['qty_returned']),
                'qty_net': _s(max(Decimal('0'), r['qty_issued'] - r['qty_returned'])),
                'qty_req_snapshot': _s(r['qty_req_snapshot']),
                'qty_req_current': (_s(r['qty_req_current'])
                                    if r['qty_req_current'] is not None else None),
                'over_qty': _s(r['over_qty']),
                'no_req_qty': _s(r['no_req_qty']),
                'over': r['over'], 'unit_pending': r['unit_pending'],
                'over_rows': r['over_rows'],
            })
        summary = {
            'over_ratio': (float((over_total / req_total * 100).quantize(
                Decimal('0.1'))) if req_total > 0 else None),
            'no_req_ratio': (float((no_req_total / (issued_total + no_req_total) * 100)
                                   .quantize(Decimal('0.1')))
                             if (issued_total + no_req_total) > 0 else None),
            'issued_total': _s(issued_total),
            'no_req_total': _s(no_req_total),
            'over_total': _s(over_total),
            'pending_count': len(pending_units),
        }
        return Response({'summary': summary, 'rows': out_rows,
                         'pending_units': pending_units})


# ============ P2 供应商主档（权限点 warehouse.supplier，legacy 默认零授权） ============

class SupplierListCreateView(CompanyContextMixin, generics.ListCreateAPIView):
    """GET /suppliers?keyword= 列表（模糊编码/名称/联系人/电话）；POST 新增。"""

    serializer_class = SupplierSerializer
    pagination_class = DefaultPagination

    def get_queryset(self):
        qs = Supplier.objects.filter(company=self.company)
        keyword = self.request.query_params.get('keyword', '').strip()
        if keyword:
            qs = qs.filter(Q(code__icontains=keyword) | Q(name__icontains=keyword)
                           | Q(contact_person__icontains=keyword)
                           | Q(phone__icontains=keyword))
        return qs

    def perform_create(self, serializer):
        self.check_perm('warehouse.supplier')
        code = (serializer.validated_data.get('code') or '').strip()
        if not code:
            raise ValidationError({'code': '供应商编码不能为空'})
        _purge_deleted(Supplier, company=self.company, code=code)
        if Supplier.objects.filter(company=self.company, code=code).exists():
            raise ValidationError({'code': '供应商编码已存在'})
        serializer.save(company=self.company, code=code)


class SupplierDetailView(CompanyContextMixin, APIView):
    """GET/PUT/DELETE /suppliers/<pk>。

    删除为软删（留痕）；仍被未删除采购单/暂收单引用时拒绝（PROTECT 语义，
    软删不阻断历史单据的供应商追溯）。
    """

    def _get(self, pk) -> Supplier:
        try:
            return Supplier.objects.get(company=self.company, pk=pk)
        except Supplier.DoesNotExist:
            raise Http404('供应商不存在')

    def get(self, request, pk):
        return Response(SupplierSerializer(self._get(pk)).data)

    def put(self, request, pk):
        self.check_perm('warehouse.supplier')
        sup = self._get(pk)
        new_code = str(request.data.get('code') or '').strip()
        if new_code and new_code != sup.code:
            _purge_deleted(Supplier, company=self.company, code=new_code)
            if Supplier.objects.filter(company=self.company, code=new_code).exclude(pk=pk).exists():
                raise ValidationError({'code': '供应商编码已存在'})
            sup.code = new_code
        for field in ('name', 'contact_person', 'phone', 'settle_method', 'rating', 'remark'):
            if field in request.data:
                setattr(sup, field, str(request.data.get(field) or '')[:128])
        sup.save()
        return Response(SupplierSerializer(sup).data)

    def delete(self, request, pk):
        self.check_perm('warehouse.supplier')
        sup = self._get(pk)
        if (PurchaseOrder.objects.filter(supplier=sup).exists()
                or Receipt.objects.filter(supplier=sup).exists()):
            raise ValidationError('该供应商已被采购单/暂收单引用，无法删除')
        sup.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


# ============ P2 到料暂收（暂收 → IQC 行级拆量 → 确认生成采购入库） ============

def _resolve_receipt_items(company, raw_items):
    """暂收行解析校验（POST/PUT 共用）：PO 行/物料归属校验 + 缺省带出。

    带 purchase_item 时 fabric_name/color/unit 缺省由 PO 行带出（前端少填）；
    无采购行（无单直收）fabric_name 必填。IQC 拆量（qualified/rejected）登记侧
    零拦截：合格+拒收可超到料量（让步收货场景），差异留痕不做加和校验。
    """
    if not isinstance(raw_items, list) or not raw_items:
        raise ValidationError({'items': '请至少提交一项暂收明细'})
    it = ReceiptItemInputSerializer(data=raw_items, many=True)
    it.is_valid(raise_exception=True)
    items = it.validated_data
    for item in items:
        pi = None
        if item.get('purchase_item'):
            pi = (PurchaseItem.objects.filter(pk=item['purchase_item'])
                  .select_related('order').first())
            if pi is None or pi.order.company_id != company.id:
                raise ValidationError({'items': f"采购行 {item['purchase_item']} 不存在"})
        item['_pi'] = pi
        if not item.get('fabric_name') and pi is not None:
            item['fabric_name'] = pi.fabric_name
        if not item.get('fabric_name'):
            raise ValidationError({'items': '面料名称不能为空（无采购行直收时必填）'})
        if not item.get('color') and pi is not None:
            item['color'] = pi.color
        if not item.get('unit') and pi is not None:
            item['unit'] = pi.unit
        item['unit'] = item.get('unit') or '米'
        mat = None
        if item.get('material_id'):
            from apps.tech.models import Material
            mat = Material.objects.filter(pk=item['material_id'], company=company).first()
            if mat is None:
                raise ValidationError({'items': f"物料 {item['material_id']} 不存在"})
        elif pi is not None:
            # 物料缺省由 PO 行带出（与价链带出同哲学，需求汇总库存锚点随单流转）
            mat = pi.material
        item['_mat'] = mat
    return items


def _create_receipt_items(doc, items):
    ReceiptItem.objects.bulk_create([
        ReceiptItem(receipt=doc, purchase_item=item['_pi'], material=item['_mat'],
                    fabric_name=item['fabric_name'], color=item['color'],
                    lot_no=item['lot_no'], unit=item['unit'],
                    arrived_qty=item.get('arrived_qty') or 0,
                    qualified_qty=item.get('qualified_qty') or 0,
                    rejected_qty=item.get('rejected_qty') or 0,
                    remark=(item.get('remark') or '')[:255])
        for item in items
    ])


class ReceiptListCreateView(CompanyContextMixin, generics.ListCreateAPIView):
    """GET /receipts?status=&keyword= 列表；POST 暂收（单号留空自动 ZS+日期+序号）。"""

    serializer_class = ReceiptSerializer
    pagination_class = DefaultPagination

    def get_queryset(self):
        qs = Receipt.objects.filter(company=self.company).prefetch_related('items')
        st = self.request.query_params.get('status')
        if st:
            qs = qs.filter(status=st)
        keyword = self.request.query_params.get('keyword', '').strip()
        if keyword:
            qs = qs.filter(Q(receipt_no__icontains=keyword)
                           | Q(supplier_name__icontains=keyword)
                           | Q(items__fabric_name__icontains=keyword)).distinct()
        return qs

    def perform_create(self, serializer):
        self.check_perm('warehouse.manage')
        receipt_no = (serializer.validated_data.get('receipt_no') or '').strip()
        if not receipt_no:
            receipt_no = _next_doc_no(self.company, 'ZS', Receipt, 'receipt_no')
        _purge_deleted(Receipt, company=self.company, receipt_no=receipt_no)
        supplier = serializer.validated_data.get('supplier')
        if supplier is not None and supplier.company_id != self.company.id:
            raise ValidationError({'supplier': '供应商不存在'})
        purchase = serializer.validated_data.get('purchase')
        if purchase is not None and purchase.company_id != self.company.id:
            raise ValidationError({'purchase': '采购单不存在'})
        items = _resolve_receipt_items(self.company, self.request.data.get('items'))
        with transaction.atomic():
            doc = serializer.save(company=self.company, receipt_no=receipt_no)
            _create_receipt_items(doc, items)


class ReceiptDetailView(CompanyContextMixin, APIView):
    """GET/PUT/DELETE /receipts/<pk>（仅 draft 可改删）。

    PUT = 暂存修改 + IQC 行级判定：items 整表重建（与血缘表同惯例，软删行先硬删），
    行级 qualified_qty/rejected_qty 拆量在此落定；确认后不可再改。
    """

    def _get(self, pk) -> Receipt:
        try:
            return Receipt.objects.get(company=self.company, pk=pk)
        except Receipt.DoesNotExist:
            raise Http404('暂收单不存在')

    def get(self, request, pk):
        return Response(ReceiptSerializer(self._get(pk)).data)

    def put(self, request, pk):
        self.check_perm('warehouse.manage')
        doc = self._get(pk)
        if doc.status != Receipt.Status.DRAFT:
            raise ValidationError('已确认暂收单不可修改')
        if 'supplier' in request.data:
            sid = request.data.get('supplier')
            if sid:
                sup = Supplier.objects.filter(company=self.company, pk=sid).first()
                if sup is None:
                    raise ValidationError({'supplier': '供应商不存在'})
                doc.supplier = sup
            else:
                doc.supplier = None
        if 'supplier_name' in request.data:
            doc.supplier_name = str(request.data.get('supplier_name') or '')[:128]
        if 'purchase' in request.data:
            pid = request.data.get('purchase')
            if pid:
                po = PurchaseOrder.objects.filter(company=self.company, pk=pid).first()
                if po is None:
                    raise ValidationError({'purchase': '采购单不存在'})
                doc.purchase = po
            else:
                doc.purchase = None
        if 'remark' in request.data:
            doc.remark = str(request.data.get('remark') or '')[:255]
        doc.save()
        if 'items' in request.data:
            items = _resolve_receipt_items(self.company, request.data.get('items'))
            with transaction.atomic():
                _purge_deleted(ReceiptItem, receipt=doc)
                ReceiptItem.objects.filter(receipt=doc).delete()
                _create_receipt_items(doc, items)
        return Response(ReceiptSerializer(doc).data)

    def delete(self, request, pk):
        self.check_perm('warehouse.manage')
        doc = self._get(pk)
        if doc.status != Receipt.Status.DRAFT:
            raise ValidationError('已确认暂收单不可删除')
        doc.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class ReceiptConfirmView(CompanyContextMixin, APIView):
    """POST /receipts/<pk>/confirm 暂收 → 已确认（IQC 结果落定，合格量自动入库）。

    确认动作（事务内）：
      1. 行级 qualified_qty>0 的行汇总生成一张 StockIn(type=purchase, status=done)：
         StockInItem 带 purchase_item + unit_price=PurchaseItem.price（复用 P1 价链，
         价格自动带出；无采购行 unit_price=NULL）+ material（需求汇总库存锚点）；
         批次 (company, lot_no) get_or_create（沿用供应商原始编号），新批次回填
         source_in，批次单价无值时由入库价落定（与 StockInConfirmView 同口径）；
      2. 拒收量留痕不进库存（不写 StockIn/Lot）；
      3. PurchaseItem.arrived_qty/qualified_qty 累计（在途=qty-arrived_qty 收敛）；
      4. 全部行拒收 → 不生成入库单，状态照常落 confirmed。

    响应 = 暂收单序列化 + in_no/in_id（确认自动生成入库单时回带，
    INT 2026-10-24 补缺口 E：前端确认 toast 直显入库单号；全拒收无入库单
    则不带这两个字段）。
    """

    def post(self, request, pk):
        self.check_perm('warehouse.manage')
        in_doc = None
        with transaction.atomic():
            # 入库单生成、采购到料累计和状态翻转共用同一单据锁，保证只执行一次。
            doc = (Receipt.objects.select_for_update()
                   .filter(company=self.company, pk=pk)
                   .prefetch_related('items__purchase_item__order', 'items__material').first())
            if not doc:
                raise Http404('暂收单不存在')
            if doc.status != Receipt.Status.DRAFT:
                raise ValidationError('该暂收单已是确认状态')
            qualified_items = [it for it in doc.items.all()
                               if (it.qualified_qty or Decimal('0')) > 0]
            if qualified_items:
                in_no = _next_doc_no(self.company, 'RK', StockIn, 'in_no')
                doc_head = StockIn.objects.create(
                    company=self.company, in_no=in_no,
                    source_type=StockIn.SourceType.PURCHASE, purchase=doc.purchase,
                    in_at=timezone.now(), status=StockIn.Status.DONE,
                    operator=self.request.user,
                    remark=f'暂收单 {doc.receipt_no} 确认自动生成')
                for it in qualified_items:
                    lot, created = Lot.all_objects.get_or_create(
                        company=self.company, lot_no=it.lot_no, is_deleted=False,
                        defaults={'fabric_name': it.fabric_name, 'color': it.color,
                                  'qty': it.qualified_qty, 'unit': it.unit or '米'})
                    price = it.purchase_item.price if it.purchase_item_id else None
                    row = StockInItem.objects.create(
                        stock_in=doc_head, fabric_name=it.fabric_name, color=it.color,
                        lot_no=it.lot_no, qty=it.qualified_qty, unit=it.unit or '米',
                        lot=lot, unit_price=price,
                        # D19：pi.price 已是主单位规范化值，直接传递；price_unit 随行留痕
                        price_unit=(it.purchase_item.price_unit or ''
                                    if it.purchase_item_id else ''),
                        purchase_item=it.purchase_item,
                        material=it.material)
                    if created:
                        # 新建批次回填来源入库行（追溯「批次 → 入库单」的锚点）
                        Lot.objects.filter(pk=lot.pk).update(source_in=row)
                    if price is not None:
                        Lot.objects.filter(pk=lot.pk, unit_cost__isnull=True)                             .update(unit_cost=price)
                    if it.purchase_item_id:
                        PurchaseItem.objects.filter(pk=it.purchase_item_id).update(
                            arrived_qty=F('arrived_qty') + it.arrived_qty,
                            qualified_qty=F('qualified_qty') + it.qualified_qty)
                in_doc = doc_head
            doc.status = Receipt.Status.CONFIRMED
            doc.confirmed_at = timezone.now()
            doc.confirmed_by = self.request.user
            doc.save(update_fields=['status', 'confirmed_at', 'confirmed_by', 'updated_at'])
        data = ReceiptSerializer(doc).data
        if in_doc is not None:
            data['in_no'] = in_doc.in_no
            data['in_id'] = in_doc.pk
        return Response(data)


class DemandSummaryView(CompanyContextMixin, APIView):
    """GET /demand-summary?style_id=&customer_id= 采购需求汇总（视图计算不落库）。

    口径声明（§4.1，逐项——前端与对账以此为准）：
      采购空差 qty_suggested = Σ(active BOM gross_usage × 订单未投产件数)
                              - 当前库存 - 在途
      1. 未投产件数 = Σ OrderItem.qty - Σ 关联 Production 计划件数
         （计划件数=plan_qty 回落 total_qty，与 P1 定额基数同源 bed_base_qty）。
         【近似口径】分色分码矩阵未与 BOM 色码维（lite 通码）匹配，按整单件数近似；
         未挂单床次（order=NULL）不计入投产。
      2. 订单范围：status != cancelled；未投产 <=0（已排完）跳过；无款式/无 active
         BOM 跳过——各计数进 summary（order_planned_full/order_no_style/order_no_bom）。
      3. 当前库存 = Σ StockInItem 主单位折算量 - Σ 已确认领料主单位折算量
         （退料 type=return 加回）。StockInItem 全量计入（含草稿入库单，流水口径）；
         主单位折算=克重x幅宽（公斤<->米），缺参/未知单位对行不入合计，
         计 conversion_pending_cnt。物料锚点=StockInItem.material /
         IssueItem.material（可空列，NULL 行不入物料维统计）。
      4. 在途 = Σ PO 行 max(0, qty - arrived_qty)：逐行截断防超收冲抵欠收行；
         只计挂 material 的行；arrived_qty 由暂收确认累计（草稿暂收不计）。
      5. 损耗已含在 gross_usage（unit_usage x (1+loss_rate/100)，B5 计算属性）。
      结果按物料聚合，qty_suggested 可为负（库存富余，前端自判是否显示）。
      6. P2.5 安全库存口径（2026-11-02 对齐华遨「自动采购建议+安全库存预警」）：
         qty_reorder = max(0, 需求-库存-在途+safety_stock)（补货点口径，含水位）；
         reorder_cnt=qty_reorder>0 行数（含纯水位触发行——订单空差为负但库存
         低于水位的物料也进来）；safety_alert_cnt=库存+在途<安全库存 的物料数
         （纯水位预警，与订单需求无关）。qty_suggested 语义不变（不含水位），
         两列并存由前端切换展示。
    """

    def get(self, request):
        self.check_perm('warehouse.manage')
        from apps.orders.models import Order, OrderItem
        from apps.production.models import Production
        from apps.tech.models import Bom, Material
        from apps.warehouse.services import bed_base_qty, convert_to_main_unit

        orders_qs = (Order.objects.filter(company=self.company)
                     .exclude(status=Order.Status.CANCELLED).select_related('style'))
        style_id = self.request.query_params.get('style_id', '').strip()
        if style_id:
            orders_qs = orders_qs.filter(style_id=style_id)
        customer_id = self.request.query_params.get('customer_id', '').strip()
        if customer_id:
            orders_qs = orders_qs.filter(customer_id=customer_id)
        orders = list(orders_qs)
        order_ids = [o.id for o in orders]

        # 未投产件数（近似口径，见 docstring 第 1 条）
        ordered = {}
        if order_ids:
            for r in (OrderItem.objects.filter(order_id__in=order_ids)
                      .values('order_id').annotate(q=Sum('qty'))):
                ordered[r['order_id']] = int(r['q'] or 0)
        planned = {}
        if order_ids:
            for p in Production.objects.filter(company=self.company,
                                               order_id__in=order_ids):
                # 查询已限定 order_id__in，此处 p.order_id 必有值（未挂单床次不入统计）
                planned[p.order_id] = planned.get(p.order_id, 0) + bed_base_qty(p)

        demand = {}
        skipped_full = skipped_no_style = skipped_no_bom = 0
        for o in orders:
            not_produced = ordered.get(o.id, 0) - planned.get(o.id, 0)
            if not_produced <= 0:
                skipped_full += 1
                continue
            if o.style_id is None:
                skipped_no_style += 1
                continue
            bom = (Bom.objects.filter(company=self.company, style=o.style, status='active')
                   .prefetch_related('items').first())
            if bom is None:
                skipped_no_bom += 1
                continue
            for bi in bom.items.all():
                demand[bi.material_id] = (demand.get(bi.material_id, Decimal('0'))
                                          + bi.gross_usage * not_produced)

        mats = {m.id: m for m in Material.objects.filter(pk__in=set(demand))}
        # P2.5：设了安全库存的物料也进池（纯水位预警行——无订单需求也看得到）
        for m in Material.objects.filter(company=self.company, status='active',
                                         safety_stock__gt=Decimal('0')):
            mats.setdefault(m.id, m)
        material_ids = set(mats)
        stock = {mid: Decimal('0') for mid in material_ids}
        transit = {mid: Decimal('0') for mid in material_ids}
        stock_pending = 0

        if material_ids:
            # 库存入：StockInItem 按 (物料, 单位) 分组折算主单位
            for g in (StockInItem.objects
                      .filter(stock_in__company=self.company,
                              material_id__in=material_ids)
                      .values('material_id', 'unit').annotate(q=Sum('qty'))):
                q_main = convert_to_main_unit(g['q'] or 0, g['unit'], mats[g['material_id']])
                if q_main is None:
                    stock_pending += 1
                else:
                    stock[g['material_id']] += q_main
            # 库存出：已确认领料冲减（退料加回），按 (物料, 批次单位) 分组
            for g in (IssueItem.objects
                      .filter(issue__company=self.company,
                              issue__status=MaterialIssue.Status.CONFIRMED,
                              material_id__in=material_ids)
                      .values('material_id', 'lot__unit', 'issue__type')
                      .annotate(q=Sum('qty'))):
                q_main = convert_to_main_unit(g['q'] or 0, g['lot__unit'],
                                              mats[g['material_id']])
                if q_main is None:
                    stock_pending += 1
                elif g['issue__type'] == MaterialIssue.Type.RETURN:
                    stock[g['material_id']] += q_main
                else:
                    stock[g['material_id']] -= q_main
            # 在途：PO 行逐行截断 max(0, qty-arrived_qty)，只计挂物料行
            for g in (PurchaseItem.objects
                      .filter(order__company=self.company,
                              material_id__in=material_ids, qty__gt=F('arrived_qty'))
                      .values('material_id')
                      .annotate(t=Sum(F('qty') - F('arrived_qty'),
                                      output_field=DecimalField(max_digits=14,
                                                                decimal_places=2)))):
                transit[g['material_id']] += g['t'] or Decimal('0')

        rows = []
        need_cnt = 0
        reorder_cnt = 0
        safety_alert_cnt = 0
        for mid in sorted(material_ids, key=lambda m: (mats[m].code, mats[m].name)):
            mat = mats[mid]
            d = demand.get(mid, Decimal('0')).quantize(Decimal('0.01'))
            s = stock[mid].quantize(Decimal('0.01'))
            t = transit[mid].quantize(Decimal('0.01'))
            sug = (d - s - t).quantize(Decimal('0.01'))
            safety = (mat.safety_stock or Decimal('0')).quantize(Decimal('0.01'))
            reorder = (d - s - t + safety).quantize(Decimal('0.01'))
            if reorder < 0:
                reorder = Decimal('0.00')
            if sug > 0:
                need_cnt += 1
            if reorder > 0:
                reorder_cnt += 1
            if s + t < safety:
                safety_alert_cnt += 1
            rows.append({
                'material_id': mid, 'material_code': mat.code,
                'material_name': mat.name, 'unit': mat.unit,
                'qty_demand': _s(d), 'qty_stock': _s(s),
                'qty_in_transit': _s(t), 'qty_suggested': _s(sug),
                'safety_stock': _s(safety), 'qty_reorder': _s(reorder),
            })
        summary = {
            'order_total': len(orders),
            'order_planned_full': skipped_full,
            'order_no_style': skipped_no_style,
            'order_no_bom': skipped_no_bom,
            'conversion_pending_cnt': stock_pending,
            'material_cnt': len(rows),
            'need_cnt': need_cnt,
            'reorder_cnt': reorder_cnt,
            'safety_alert_cnt': safety_alert_cnt,
        }
        return Response({'summary': summary, 'rows': rows})


# ============ D1 期初库存 Excel 导入（上线落地方案 §11-D1，SA-P2-5 2026-10-24） ============

# 标准模板列（列头须完全一致）。D19 拍板（2026-10-24）：批次价单位用户可选——
# 「单价单位」列填 公斤/米/空（空=按主单位，缺省可参照物料档案 price_unit），
# 导入时按行换算为主单位规范化值落库（缺克重/幅宽参数该行报错）
OPENING_IMPORT_COLUMNS = {
    '物料编码': 'code', '缸号': 'lot_no', '数量': 'qty', '单位': 'unit',
    '批次单价': 'unit_price', '单价单位': 'price_unit',
    '库位': 'location', '备注': 'remark',
}
# 缸号留空时的缺省批次号（辅料无缸号场景；Lot 按 (company, lot_no) get_or_create）
DEFAULT_OPENING_LOT_NO = '期初'


def _norm_price_unit(v) -> str:
    """单价单位列归一：公斤/千克→kg、米→m、其余（含空）→''（=按主单位）。"""
    u = str(v or '').strip()
    if u in ('kg', 'KG', '公斤', '千克'):
        return 'kg'
    if u in ('m', 'M', '米'):
        return 'm'
    return ''


class OpeningStockImportView(CompanyContextMixin, _ImportMixin, APIView):
    """POST /warehouse/opening-stock/import 期初库存 Excel 批量导入（D1）。

    模板列：物料编码*｜缸号｜数量*｜单位(空=主单位)｜批次单价｜单价单位(公斤/米/空=主单位)｜
    库位｜备注。
    行校验（all-or-nothing，任一行失败整批不入库，errors 带行号+中文原因）：
      - 物料编码须为本公司存在且未软删的物料；
      - 数量 > 0；
      - 单位≠主单位时走 services.convert_to_main_unit 折算（公斤↔米=克重×幅宽），
        缺克重/幅宽参数或单位对不识别 → 该行报错「缺换算参数」；
      - 批次单价可选 ≥0；单价单位非空（公斤/米）时按行折算主单位规范化价
        （D19，services.convert_price_to_main_unit），缺克重/幅宽 → 该行报错
        「该物料缺克重/幅宽，无法换算单价」；
      - 缸号不强制（辅料无缸号），留空用缺省批次号「期初」；
      - 幂等防重导：同物料+同缸号已存在非删除期初入库行（StockIn.source_type='opening'）
        → 该行报错「期初已导入过」整批拒（期初一次性动作，宁严勿松）。
    成功（事务内）：生成一张 StockIn(source_type='opening', status='done') + StockInItem
    （带 material、unit_price=批次单价列）+ Lot get_or_create（fabric_name=物料名）；
    批次价落定口径与暂收确认 ReceiptConfirmView 完全一致（价格非空且 Lot.unit_cost
    为空时 update unit_cost，不动已有批次价），新建 Lot 回填 source_in。
    库位/备注无独立列，随新建 Lot 落 Lot.remark（get_or_create 缺省仅新建生效，
    已有批次不覆盖）。响应 {created, stock_in_id, in_no, errors}。
    """

    def post(self, request):
        # 复用 tech 导入公共件（xlsx/JSON 行归一 + all-or-nothing 语义同款）
        self.check_perm('warehouse.manage')
        from apps.tech.models import Material
        from apps.tech.views import _to_decimal
        from apps.warehouse.services import convert_to_main_unit

        rows = self._collect_rows(OPENING_IMPORT_COLUMNS)
        empty = {'created': 0, 'stock_in_id': None, 'in_no': '', 'errors': []}
        if not rows:
            return Response({**empty,
                             'errors': [{'row': 0, 'code': '', 'reason': '无数据行'}]})

        errors, prepared = [], []
        seen_keys = set()
        for no, row in rows:
            code = str(row.get('code') or '').strip()
            lot_no = str(row.get('lot_no') or '').strip() or DEFAULT_OPENING_LOT_NO
            if not code:
                errors.append({'row': no, 'code': code, 'reason': '物料编码不能为空'})
                continue
            material = Material.objects.filter(company=self.company, code=code).first()
            if material is None:
                errors.append({'row': no, 'code': code, 'reason': '物料不存在'})
                continue
            try:
                qty = _to_decimal(row.get('qty'))
            except Exception:
                errors.append({'row': no, 'code': code, 'reason': '数量不合法'})
                continue
            if qty is None:
                errors.append({'row': no, 'code': code, 'reason': '数量不能为空'})
                continue
            if qty <= 0:
                errors.append({'row': no, 'code': code, 'reason': '数量必须大于0'})
                continue
            # 单位折算：空=主单位；非主单位走克重×幅宽换算（缺参/未知单位对 → 报错行）
            qty_main = qty
            if str(row.get('unit') or '').strip():
                qty_main = convert_to_main_unit(qty, str(row['unit']).strip(), material)
                if qty_main is None:
                    errors.append({'row': no, 'code': code, 'reason': '缺换算参数'})
                    continue
            price = None
            price_unit = _norm_price_unit(row.get('price_unit'))
            if row.get('unit_price') not in (None, ''):
                try:
                    price = _to_decimal(row.get('unit_price'))
                except Exception:
                    errors.append({'row': no, 'code': code, 'reason': '批次单价不合法'})
                    continue
                if price is None or price < 0:
                    errors.append({'row': no, 'code': code, 'reason': '批次单价不合法'})
                    continue
                if price_unit:
                    # D19：单价单位非空 → 按克重×幅宽折算主单位规范化价（缺参该行报错）
                    converted = convert_price_to_main_unit(price, price_unit, material)
                    if converted is None:
                        errors.append({'row': no, 'code': code,
                                       'reason': '该物料缺克重/幅宽，无法换算单价'})
                        continue
                    price = converted
            # 幂等防重导（期初一次性动作，宁严勿松）：批内重复 + 库内已有期初行
            key = (material.id, lot_no)
            if key in seen_keys:
                errors.append({'row': no, 'code': code,
                               'reason': '批内重复（同物料同缸号）'})
                continue
            seen_keys.add(key)
            if (StockInItem.objects
                    .filter(stock_in__company=self.company,
                            stock_in__source_type=StockIn.SourceType.OPENING,
                            stock_in__is_deleted=False,
                            material=material, lot_no=lot_no)
                    .exists()):
                errors.append({'row': no, 'code': code, 'reason': '期初已导入过'})
                continue
            prepared.append((no, material, lot_no, qty_main, price,
                             price_unit if price is not None else '',
                             str(row.get('location') or '').strip(),
                             str(row.get('remark') or '').strip()))

        if errors:
            # 不做部分成功静默：任一行失败 → 整批不入库，明确返回错误行
            return Response({**empty, 'errors': errors})

        with transaction.atomic():
            in_no = _next_doc_no(self.company, 'RK', StockIn, 'in_no')
            doc_head = StockIn.objects.create(
                company=self.company, in_no=in_no,
                source_type=StockIn.SourceType.OPENING,
                in_at=timezone.now(), status=StockIn.Status.DONE,
                operator=self.request.user, remark='期初库存导入')
            for _no, material, lot_no, qty_main, price, price_unit, location, remark in prepared:
                # 批次价/来源回填口径与暂收确认 ReceiptConfirmView 完全一致
                lot_remarks = [p for p in (f'库位：{location}' if location else '',
                                           remark) if p]
                lot, created = Lot.all_objects.get_or_create(
                    company=self.company, lot_no=lot_no, is_deleted=False,
                    defaults={'fabric_name': material.name[:64], 'color': '',
                              'qty': qty_main, 'unit': material.unit,
                              'remark': '；'.join(lot_remarks)[:255]})
                row = StockInItem.objects.create(
                    stock_in=doc_head, fabric_name=material.name[:64], color='',
                    lot_no=lot_no, qty=qty_main, unit=material.unit, lot=lot,
                    unit_price=price, price_unit=price_unit, material=material)
                if created:
                    # 新建批次回填来源入库行（追溯「批次 → 入库单」的锚点）
                    Lot.objects.filter(pk=lot.pk).update(source_in=row)
                if price is not None:
                    Lot.objects.filter(pk=lot.pk, unit_cost__isnull=True) \
                        .update(unit_cost=price)
        audit(self.company, AuditLog.Type.OTHER, operator=request.user,
              message=f'期初库存导入：{len(prepared)} 行（{in_no}）')
        return Response({'created': len(prepared), 'stock_in_id': doc_head.pk,
                         'in_no': in_no, 'errors': []})
