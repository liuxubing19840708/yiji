"""生产路线、受控节点执行与只读流水 API。

写接口只暴露 receive/complete/transfer/return/scrap 包装服务，不开放任意
post_quantity，避免调用方绕过路线方向和数量守恒规则。
"""
from django.db import IntegrityError, models, transaction
from django.http import Http404
from rest_framework import generics, status
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.catalog.models import Ticket
from apps.core.mixins import CompanyContextMixin
from apps.core.pagination import DefaultPagination
from apps.orders.models import Order
from apps.production.models import Production

from . import services as svc
from .exceptions import IdempotencyError, NotFound, ValidationError
from .models import ProductionRoute, ProductionStage, QuantityLedger, RouteNode
from .serializers import (
    ActiveStatusSerializer, InstantiateRouteSerializer, LedgerSerializer, ReverseSerializer,
    RouteCopySerializer, RouteInputSerializer, RouteSerializer, RouteUpdateSerializer,
    StageActionSerializer, StageInputSerializer, StageSerializer,
)


class DomainView(CompanyContextMixin, APIView):
    def handle_exception(self, exc):
        # 在 DRF dispatch 内映射领域异常，finalize_response 会正确设置渲染器。
        if isinstance(exc, NotFound):
            raise Http404(str(exc)) from None
        if isinstance(exc, IdempotencyError):
            return Response({'detail': str(exc)}, status=status.HTTP_409_CONFLICT)
        if isinstance(exc, ValidationError):
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return super().handle_exception(exc)

    def stage(self, pk):
        obj = ProductionStage.objects.filter(pk=pk, company=self.company).first()
        if obj is None:
            raise Http404('生产节点不存在')
        return obj

    def route(self, pk):
        obj = (ProductionRoute.objects.filter(pk=pk, company=self.company)
               .select_related('order', 'production', 'source_template')
               .annotate(instance_count=models.Count('instances', distinct=True))
               .prefetch_related('nodes__stage').first())
        if obj is None:
            raise Http404('生产路线不存在')
        return obj

    def ticket(self, pk):
        obj = Ticket.objects.filter(pk=pk, company=self.company).first()
        if obj is None:
            raise Http404('菲票不存在')
        return obj

    def node(self, pk):
        obj = RouteNode.objects.filter(pk=pk, company=self.company).first()
        if obj is None:
            raise Http404('路线节点不存在')
        return obj


class StageListCreateView(DomainView):
    def get(self, request):
        self.check_perm('manufacturing.view')
        stages = ProductionStage.objects.filter(company=self.company).annotate(
            in_use=models.Exists(RouteNode.objects.filter(stage=models.OuterRef('pk'))))
        if request.query_params.get('is_active') in ('0', '1'):
            stages = stages.filter(is_active=request.query_params['is_active'] == '1')
        return Response(StageSerializer(stages, many=True).data)

    def post(self, request):
        self.check_perm('manufacturing.manage')
        form = StageInputSerializer(data=request.data)
        form.is_valid(raise_exception=True)
        try:
            stage = ProductionStage.objects.create(company=self.company, **form.validated_data)
        except IntegrityError:
            raise DRFValidationError({'code': '节点编码已存在'})
        return Response(StageSerializer(stage).data, status=status.HTTP_201_CREATED)


class StageDetailView(DomainView):
    def put(self, request, pk):
        self.check_perm('manufacturing.manage')
        stage = self.stage(pk)
        form = StageInputSerializer(data=request.data)
        form.is_valid(raise_exception=True)
        if RouteNode.objects.filter(stage=stage).exists() and any(
                form.validated_data[key] != getattr(stage, key) for key in ('code', 'stage_type')):
            raise DRFValidationError('已使用阶段的编码和类型不可修改，请新建标准阶段；历史路线保留快照')
        for key, value in form.validated_data.items():
            setattr(stage, key, value)
        try:
            stage.save()
        except IntegrityError:
            raise DRFValidationError({'code': '节点编码已存在'})
        return Response(StageSerializer(stage).data)

    def delete(self, request, pk):
        self.check_perm('manufacturing.manage')
        stage = self.stage(pk)
        if RouteNode.objects.filter(stage=stage).exists():
            raise DRFValidationError('节点已被路线使用，不能删除')
        stage.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class StageStatusView(DomainView):
    def post(self, request, pk):
        self.check_perm('manufacturing.manage')
        stage = self.stage(pk)
        form = ActiveStatusSerializer(data=request.data)
        form.is_valid(raise_exception=True)
        stage.is_active = form.validated_data['is_active']
        stage.save(update_fields=['is_active', 'updated_at'])
        return Response(StageSerializer(stage).data)


def _validated_nodes(company, rows):
    sequences = [row['sequence'] for row in rows]
    if len(sequences) != len(set(sequences)):
        raise DRFValidationError({'nodes': '节点顺序不能重复'})
    stage_ids = {row['stage_id'] for row in rows}
    stages = ProductionStage.objects.filter(company=company, pk__in=stage_ids)
    by_id = {stage.pk: stage for stage in stages}
    if len(by_id) != len(stage_ids):
        raise DRFValidationError({'nodes': '包含不存在或不属于本公司的生产节点'})
    return [
        RouteNode(company=company, stage=by_id[row['stage_id']],
                  stage_code=by_id[row['stage_id']].code,
                  stage_name=by_id[row['stage_id']].name,
                  stage_type=by_id[row['stage_id']].stage_type,
                  sequence=row['sequence'], required=row['required'],
                  allow_parallel=row['allow_parallel'], allow_skip=row['allow_skip'])
        for row in sorted(rows, key=lambda item: item['sequence'])
    ]


class RouteListCreateView(DomainView):
    def get(self, request):
        self.check_perm('manufacturing.view')
        routes = (ProductionRoute.objects.filter(company=self.company)
                  .select_related('order', 'production', 'source_template')
                  .annotate(instance_count=models.Count('instances', distinct=True))
                  .prefetch_related('nodes__stage'))
        if 'is_template' in request.query_params:
            value = request.query_params['is_template']
            if value not in ('0', '1'):
                raise DRFValidationError({'is_template': '仅支持 0/1'})
            routes = routes.filter(is_template=value == '1')
        if request.query_params.get('is_active') in ('0', '1'):
            routes = routes.filter(is_active=request.query_params['is_active'] == '1')
        if request.query_params.get('order_id'):
            routes = routes.filter(order_id=request.query_params['order_id'])
        if request.query_params.get('production_id'):
            routes = routes.filter(production_id=request.query_params['production_id'])
        return Response(RouteSerializer(routes, many=True).data)

    def post(self, request):
        self.check_perm('manufacturing.manage')
        form = RouteInputSerializer(data=request.data)
        form.is_valid(raise_exception=True)
        nodes = _validated_nodes(self.company, form.validated_data['nodes'])
        with transaction.atomic():
            try:
                route = ProductionRoute.objects.create(
                    company=self.company, code=form.validated_data['code'],
                    name=form.validated_data['name'], is_template=True)
                for node in nodes:
                    node.route = route
                RouteNode.objects.bulk_create(nodes)
            except IntegrityError:
                raise DRFValidationError({'code': '路线编码已存在或节点配置冲突'})
        return Response(RouteSerializer(self.route(route.pk)).data, status=status.HTTP_201_CREATED)


class RouteDetailView(DomainView):
    def get(self, request, pk):
        self.check_perm('manufacturing.view')
        return Response(RouteSerializer(self.route(pk)).data)

    def put(self, request, pk):
        self.check_perm('manufacturing.manage')
        route = self.route(pk)
        if not route.is_template:
            raise DRFValidationError('实例路线是数量快照，不能直接修改；请新建路线实例')
        form = RouteUpdateSerializer(data=request.data)
        form.is_valid(raise_exception=True)
        nodes = _validated_nodes(self.company, form.validated_data['nodes'])
        with transaction.atomic():
            route.name = form.validated_data['name']
            route.save(update_fields=['name', 'updated_at'])
            route.nodes.all().delete()
            for node in nodes:
                node.route = route
            RouteNode.objects.bulk_create(nodes)
        return Response(RouteSerializer(self.route(route.pk)).data)

    def delete(self, request, pk):
        self.check_perm('manufacturing.manage')
        route = self.route(pk)
        if QuantityLedger.objects.filter(route=route).exists():
            raise DRFValidationError('路线已有数量流水，不能删除')
        if route.instances.exists() or route.next_versions.exists():
            raise DRFValidationError('模板已有实例或后续版本，不能删除；可停用保留历史')
        route.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class RouteStatusView(DomainView):
    def post(self, request, pk):
        self.check_perm('manufacturing.manage')
        route = self.route(pk)
        if not route.is_template:
            raise DRFValidationError('实例路线不提供启停状态')
        form = ActiveStatusSerializer(data=request.data)
        form.is_valid(raise_exception=True)
        route.is_active = form.validated_data['is_active']
        route.save(update_fields=['is_active', 'updated_at'])
        return Response(RouteSerializer(self.route(route.pk)).data)


class RouteCopyView(DomainView):
    new_version = False

    def post(self, request, pk):
        self.check_perm('manufacturing.manage')
        source = self.route(pk)
        if not source.is_template:
            raise DRFValidationError('只能复制路线模板')
        form = RouteCopySerializer(data=request.data)
        form.is_valid(raise_exception=True)
        data = form.validated_data
        nodes = (_validated_nodes(self.company, data['nodes']) if 'nodes' in data else [
            RouteNode(company=self.company, stage=node.stage,
                      stage_code=node.stage_code, stage_name=node.stage_name,
                      stage_type=node.stage_type, sequence=node.sequence,
                      required=node.required, allow_parallel=node.allow_parallel,
                      allow_skip=node.allow_skip)
            for node in source.nodes.select_related('stage').all()
        ])
        with transaction.atomic():
            try:
                route = ProductionRoute.objects.create(
                    company=self.company, code=data['code'], name=data['name'], is_template=True,
                    version=source.version + 1 if self.new_version else 1,
                    previous_version=source if self.new_version else None)
                for node in nodes:
                    node.route = route
                RouteNode.objects.bulk_create(nodes)
            except IntegrityError:
                raise DRFValidationError({'code': '路线编码已存在'}) from None
        return Response(RouteSerializer(self.route(route.pk)).data, status=status.HTTP_201_CREATED)


class RouteNewVersionView(RouteCopyView):
    new_version = True


class RouteInstantiateView(DomainView):
    def post(self, request, pk):
        self.check_perm('manufacturing.manage')
        template = self.route(pk)
        if not template.is_template:
            raise DRFValidationError('只能从路线模板创建实例')
        if not template.is_active:
            raise DRFValidationError('路线模板已停用，不能创建新实例；历史实例不受影响')
        form = InstantiateRouteSerializer(data=request.data)
        form.is_valid(raise_exception=True)
        data = form.validated_data
        order = production = None
        if data.get('order_id'):
            order = Order.objects.filter(pk=data['order_id'], company=self.company).first()
            if order is None:
                raise Http404('订单不存在')
        else:
            production = Production.objects.filter(
                pk=data['production_id'], company=self.company).first()
            if production is None:
                raise Http404('床次不存在')
        try:
            route = svc.instantiate_route(template, company=self.company, order=order,
                                          production=production, code=data.get('code'),
                                          name=data.get('name'))
        except IntegrityError:
            raise DRFValidationError({'code': '路线编码已存在或目标已有实例路线'})
        return Response(RouteSerializer(self.route(route.pk)).data, status=status.HTTP_201_CREATED)


class RouteBalancesView(DomainView):
    def get(self, request, pk):
        self.check_perm('manufacturing.view')
        route = self.route(pk)
        ticket = self.ticket(request.query_params['ticket_id']) \
            if request.query_params.get('ticket_id') else None
        rows = svc.get_ticket_node_balances(self.company, route, ticket=ticket)
        return Response(rows)


class RouteTimelineView(DomainView):
    """统一路线时间线：生产、质检、裁片、包装入库和出货共用同一事实流水。"""

    def get(self, request, pk):
        self.check_perm('manufacturing.view')
        route = self.route(pk)
        entries = QuantityLedger.objects.filter(company=self.company, route=route).select_related(
            'node__stage', 'ticket', 'operator', 'related_entry', 'reversal_of')
        ticket = self.ticket(request.query_params['ticket_id']) \
            if request.query_params.get('ticket_id') else None
        if ticket is not None:
            entries = entries.filter(ticket=ticket)
        entries = entries.order_by('created_at', 'id')
        total = entries.count()
        rows = entries[:500]
        return Response({'route': route.pk, 'ticket': ticket.pk if ticket else None,
                         'events': LedgerSerializer(rows, many=True).data,
                         'total': total, 'truncated': total > 500})


def _route_dashboard(company, route):
    balances = svc.get_ticket_node_balances(company, route)
    tickets = {ticket.pk: ticket for ticket in Ticket.objects.filter(
        company=company,
        pk__in={row['ticket_id'] for row in balances}).order_by('bed_no', 'bundle_no', 'id')}
    nodes = list(route.nodes.select_related('stage').order_by('sequence', 'id'))
    node_types = {node.pk: node.stage.stage_type for node in nodes}
    rows = []
    for ticket_id, ticket in tickets.items():
        by_node = {row['node_id']: row['balance'] for row in balances
                   if row['ticket_id'] == ticket_id}
        rows.append({
            'ticket_id': ticket_id, 'bed_no': ticket.bed_no,
            'style_no': ticket.production.style.style_no if ticket.production.style else '',
            'bundle_no': ticket.bundle_no, 'color': ticket.color, 'size': ticket.size,
            'ticket_qty': ticket.qty,
            'wip': sum(balance['available'] for node_id, balance in by_node.items()
                       if node_types[node_id] != 'finished_inbound'),
            'scrapped': sum(balance['scrapped'] for balance in by_node.values()),
            'finished_available': sum(
                balance['available'] for node_id, balance in by_node.items()
                if node_types[node_id] == 'finished_inbound'),
            'nodes': [{'node_id': node.pk, 'sequence': node.sequence,
                       'stage_name': node.stage.name, 'stage_type': node.stage.stage_type,
                       **by_node[node.pk]} for node in nodes],
        })
    from apps.cutting.models import CuttingHandover
    from apps.finished_goods.models import DocumentStatus, FinishedInbound, PackingTask
    from apps.quality.models import QualityInspection
    pending = {
        'quality': QualityInspection.objects.filter(
            company=company, node__route=route, status='pending').count(),
        'cutting': CuttingHandover.objects.filter(
            company=company, source_node__route=route, status='pending').count(),
        'packing': PackingTask.objects.filter(
            company=company, route=route, status=DocumentStatus.DRAFT).count(),
        'inbound': FinishedInbound.objects.filter(
            company=company, route=route, status=DocumentStatus.DRAFT).count(),
    }
    delivery = None
    order = route.order or (route.production.order if route.production else None)
    if order is not None:
        from django.db.models import Sum
        from apps.orders.models import OrderItem
        from apps.shipment.services import shipped_qty_map
        ordered = OrderItem.objects.filter(order=order).aggregate(
            total=Sum('qty'))['total'] or 0
        shipped = sum(shipped_qty_map(company, order).values())
        delivery = {'ordered': ordered, 'shipped': shipped,
                    'remaining': max(0, ordered - shipped)}
    return {
        'route': route.pk, 'route_code': route.code, 'rows': rows,
        'summary': {'tickets': len(rows), 'ticket_qty': sum(row['ticket_qty'] for row in rows),
                    'wip': sum(row['wip'] for row in rows),
                    'scrapped': sum(row['scrapped'] for row in rows),
                    'finished_available': sum(row['finished_available'] for row in rows)},
        'pending': pending, 'delivery': delivery,
    }


class RouteDashboardView(DomainView):
    """单路线流程看板；所有数量只取统一流水，单证只提供待办计数。"""

    def get(self, request, pk):
        self.check_perm('manufacturing.view')
        return Response(_route_dashboard(self.company, self.route(pk)))


class OperationsDashboardView(DomainView):
    """跨订单/床次运营看板；节点余额与最后活动均来自统一数量流水。"""

    def get(self, request):
        self.check_perm('manufacturing.view')
        try:
            stalled_hours = int(request.query_params.get('stalled_hours', 24))
            page = int(request.query_params.get('page', 1))
            page_size = int(request.query_params.get(
                'page_size', request.query_params.get('limit', 20)))
        except ValueError:
            raise DRFValidationError('stalled_hours、page、page_size 和 limit 必须为整数')
        if not 1 <= stalled_hours <= 24 * 365 or page < 1 or not 1 <= page_size <= 200:
            raise DRFValidationError('stalled_hours 须为 1~8760，page 须大于 0，page_size 须为 1~200')
        routes = ProductionRoute.objects.filter(
            company=self.company, is_template=False).select_related(
            'order', 'production__order').prefetch_related('nodes__stage')
        if request.query_params.get('order_id'):
            order_id = request.query_params['order_id']
            routes = routes.filter(models.Q(order_id=order_id) | models.Q(production__order_id=order_id))
        if request.query_params.get('production_id'):
            routes = routes.filter(production_id=request.query_params['production_id'])
        if request.query_params.get('style_no'):
            style_no = request.query_params['style_no'].strip()
            routes = routes.filter(models.Q(order__style__style_no__icontains=style_no) | models.Q(
                production__style__style_no__icontains=style_no))
        if request.query_params.get('customer_id'):
            customer_id = request.query_params['customer_id']
            routes = routes.filter(models.Q(order__customer_id=customer_id) | models.Q(
                production__order__customer_id=customer_id))
        if request.query_params.get('q'):
            keyword = request.query_params['q'].strip()
            routes = routes.filter(models.Q(code__icontains=keyword) | models.Q(name__icontains=keyword) | models.Q(
                production__bed_no__icontains=keyword) | models.Q(order__order_no__icontains=keyword) | models.Q(
                production__order__order_no__icontains=keyword))
        total = routes.count()
        from django.utils import timezone
        threshold = timezone.now() - timezone.timedelta(hours=stalled_hours)

        # 全局指标在数据库中按节点聚合，不能退化为只汇总当前页；冲销反向计入原事件。
        def signed_event_sum(event_type):
            quantity = models.F('quantity')
            return models.Sum(models.Case(
                models.When(models.Q(event_type=event_type), then=quantity),
                models.When(models.Q(event_type=QuantityLedger.Event.REVERSAL,
                                     reversal_of__event_type=event_type), then=-quantity),
                default=0, output_field=models.IntegerField()), default=0)

        aggregated = QuantityLedger.objects.filter(company=self.company, route__in=routes).values(
            'node', 'node__route_id', 'node__stage_type').annotate(**{
                event_type: signed_event_sum(event_type)
                for event_type in ('received', 'rework', 'transferred', 'scrapped')
            })
        totals = {}
        global_summary = {'wip': 0, 'scrapped': 0, 'finished_available': 0}
        for row in aggregated:
            key = (row['node__route_id'], row['node'])
            totals[key] = {event_type: row[event_type]
                           for event_type in ('received', 'rework', 'transferred', 'scrapped')}
            totals[key]['stage_type'] = row['node__stage_type']
            available = row['received'] + row['rework'] - row['transferred'] - row['scrapped']
            global_summary['scrapped'] += row['scrapped']
            if row['node__stage_type'] == 'finished_inbound':
                global_summary['finished_available'] += available
            else:
                global_summary['wip'] += available
        latest = {row['node']: row['last_at'] for row in QuantityLedger.objects.filter(
            company=self.company, route__in=routes).values('node').annotate(
            last_at=models.Max('created_at'))}
        stalled_route_ids = set()
        for (route_id, node_id), row in totals.items():
            available = row['received'] + row['rework'] - row['transferred'] - row['scrapped']
            if (available > 0 and node_id in latest and latest[node_id] < threshold
                    and row['stage_type'] != 'finished_inbound'):
                stalled_route_ids.add(route_id)

        start = (page - 1) * page_size
        routes = list(routes.order_by('id')[start:start + page_size])
        route_ids = [route.pk for route in routes]
        nodes = RouteNode.objects.filter(route_id__in=route_ids).select_related('stage')
        node_types = {node.pk: node.stage_type for node in nodes}
        node_names = {node.pk: node.stage_name for node in nodes}

        # 菲票数量和汇总均按各路线自身业务锚点计算，不把模板或别的公司混入。
        ticket_anchor = models.Q(pk__in=[])
        for route in routes:
            ticket_anchor |= (models.Q(production_id=route.production_id) if route.production_id
                              else models.Q(production__order_id=route.order_id))
        route_tickets = {route_id: [] for route_id in route_ids}
        if route_ids:
            for ticket in Ticket.objects.filter(ticket_anchor, company=self.company).select_related('production'):
                for route in routes:
                    if ((route.production_id and ticket.production_id == route.production_id)
                            or (not route.production_id and ticket.production.order_id == route.order_id)):
                        route_tickets[route.pk].append(ticket)

        from apps.cutting.models import CuttingHandover
        from apps.finished_goods.models import DocumentStatus, FinishedInbound, PackingTask
        from apps.quality.models import QualityInspection
        def pending_counts(queryset, field):
            return {row[field]: row['total'] for row in queryset.values(field).annotate(
                total=models.Count('id'))}
        pending_by_route = {route_id: dict.fromkeys(
            ('quality', 'cutting', 'packing', 'inbound'), 0) for route_id in route_ids}
        for key, counts in {
            'quality': pending_counts(QualityInspection.objects.filter(
                company=self.company, node__route_id__in=route_ids, status='pending'), 'node__route_id'),
            'cutting': pending_counts(CuttingHandover.objects.filter(
                company=self.company, source_node__route_id__in=route_ids, status='pending'), 'source_node__route_id'),
            'packing': pending_counts(PackingTask.objects.filter(
                company=self.company, route_id__in=route_ids, status=DocumentStatus.DRAFT), 'route_id'),
            'inbound': pending_counts(FinishedInbound.objects.filter(
                company=self.company, route_id__in=route_ids, status=DocumentStatus.DRAFT), 'route_id'),
        }.items():
            for route_id, count in counts.items():
                pending_by_route[route_id][key] = count

        # 同一订单的多个床次只汇总一次订单交付，返回时分别展示相同订单口径。
        order_by_route = {route.pk: route.order or (
            route.production.order if route.production else None) for route in routes}
        order_ids = {order.pk for order in order_by_route.values() if order is not None}
        from apps.orders.models import OrderItem
        from apps.shipment.models import Shipment, ShipmentItem
        ordered = {row['order_id']: row['total'] for row in OrderItem.objects.filter(
            order_id__in=order_ids).values('order_id').annotate(total=models.Sum('qty'))}
        shipped = {row['shipment__order_id']: row['total'] or 0 for row in ShipmentItem.objects.filter(
            shipment__company=self.company, shipment__order_id__in=order_ids,
            shipment__is_deleted=False).exclude(shipment__status=Shipment.Status.DRAFT).order_by().values(
            'shipment__order_id').annotate(total=models.Sum('qty'))}

        results = []
        for route in routes:
            summary = {'tickets': len(route_tickets[route.pk]),
                       'ticket_qty': sum(ticket.qty for ticket in route_tickets[route.pk]),
                       'wip': 0, 'scrapped': 0, 'finished_available': 0}
            stalled_nodes = []
            for node in nodes:
                if node.route_id != route.pk:
                    continue
                row = totals.get((route.pk, node.pk), {})
                available = row.get('received', 0) + row.get('rework', 0) - row.get(
                    'transferred', 0) - row.get('scrapped', 0)
                scrapped = row.get('scrapped', 0)
                summary['scrapped'] += scrapped
                if node_types[node.pk] == 'finished_inbound':
                    summary['finished_available'] += available
                else:
                    summary['wip'] += available
                    if available > 0 and node.pk in latest and latest[node.pk] < threshold:
                        stalled_nodes.append({'node_id': node.pk, 'stage_name': node_names[node.pk],
                                              'available': available, 'last_activity_at': latest[node.pk]})
            order = order_by_route[route.pk]
            delivery = None
            if order is not None:
                order_qty = ordered.get(order.pk, 0) or 0
                shipped_qty = shipped.get(order.pk, 0)
                delivery = {'ordered': order_qty, 'shipped': shipped_qty,
                            'remaining': max(0, order_qty - shipped_qty)}
            results.append({
                'route': route.pk, 'route_code': route.code, 'route_name': route.name,
                'order': order.pk if order else None, 'production': route.production_id,
                'summary': summary, 'pending': pending_by_route[route.pk],
                'delivery': delivery, 'stalled_nodes': stalled_nodes,
            })
        return Response({
            'results': results, 'total': total, 'page': page, 'page_size': page_size,
            'pages': (total + page_size - 1) // page_size,
            'has_next': start + len(results) < total, 'has_previous': page > 1,
            'truncated': start + len(results) < total,
            'stalled_hours': stalled_hours,
            'summary': {'routes': total, **global_summary,
                        'stalled_routes': len(stalled_route_ids)},
        })


def _assert_document_managed_action(node, action, target=None):
    """专用单证节点不能经通用动作绕过检验、包装、入库或出货事实。"""
    stage_type = node.stage.stage_type
    target_type = target.stage.stage_type if target is not None else None
    if stage_type in ('semi_inspection', 'final_inspection') and action in ('complete', 'return', 'scrap'):
        raise DRFValidationError('检验节点的合格、返修和报废请通过质量检验单确认')
    if stage_type == 'packing' and action == 'complete':
        raise DRFValidationError('包装合格数量请通过包装任务审核登记')
    if stage_type == 'finished_inbound':
        raise DRFValidationError('成品现货只能由成品入库、出货审核或原单证冲销变更')
    if action == 'transfer' and (stage_type == 'packing' or target_type == 'finished_inbound'):
        raise DRFValidationError('包装转成品入库请通过成品入库单审核登记')


class StageActionView(DomainView):
    """统一节点动作入口；action 为 receive/complete/transfer/return/scrap。"""

    def post(self, request, action):
        self.check_perm('manufacturing.manage')
        if action not in ('receive', 'complete', 'transfer', 'return', 'scrap'):
            raise DRFValidationError('不支持的生产动作')
        form = StageActionSerializer(data=request.data)
        form.is_valid(raise_exception=True)
        data = form.validated_data
        ticket = self.ticket(data['ticket_id'])
        node = self.node(data['node_id'])
        target = None
        if action in ('transfer', 'return'):
            if not data.get('to_node_id'):
                raise DRFValidationError({'to_node_id': '转出/退回必须提供目标节点'})
            target = self.node(data['to_node_id'])
        _assert_document_managed_action(node, action, target)
        kwargs = {'operator': request.user, 'request_id': data['request_id'],
                  'source': data.get('source', ''), 'reason': data.get('reason', '')}
        if action == 'receive':
            result = svc.receive_stage(self.company, node, ticket, data['quantity'], **kwargs)
        elif action == 'complete':
            result = svc.complete_stage(self.company, node, ticket, data['quantity'], **kwargs)
        elif action == 'scrap':
            result = svc.scrap_stage(self.company, node, ticket, data['quantity'], **kwargs)
        else:
            function = svc.transfer_stage if action == 'transfer' else svc.return_stage
            result = function(self.company, node, target, ticket, data['quantity'], **kwargs)
        many = isinstance(result, tuple)
        payload = LedgerSerializer(result, many=many).data
        return Response(payload, status=status.HTTP_201_CREATED)


class LedgerListView(CompanyContextMixin, generics.ListAPIView):
    serializer_class = LedgerSerializer
    pagination_class = DefaultPagination

    def get_queryset(self):
        self.check_perm('manufacturing.view')
        rows = QuantityLedger.objects.filter(company=self.company).select_related(
            'route', 'node', 'ticket', 'operator', 'related_entry', 'reversal_of')
        for key in ('ticket_id', 'node_id', 'route_id', 'event_type'):
            if self.request.query_params.get(key):
                rows = rows.filter(**{key: self.request.query_params[key]})
        return rows


class LedgerReverseView(DomainView):
    def post(self, request, pk):
        self.check_perm('manufacturing.reverse')
        ledger = QuantityLedger.objects.filter(pk=pk, company=self.company).first()
        if ledger is None:
            raise Http404('数量流水不存在')
        protected_prefixes = ('quality_inspection:', 'cutting_handover:',
                              'finished_goods.', 'shipment:')
        if ledger.source.startswith(protected_prefixes):
            raise DRFValidationError('该流水由业务单证管理，请使用原单证的作废或冲销流程')
        form = ReverseSerializer(data=request.data)
        form.is_valid(raise_exception=True)
        reversal = svc.reverse_quantity(
            self.company, ledger, operator=request.user,
            request_id=form.validated_data['request_id'],
            reason=form.validated_data.get('reason', ''))
        return Response(LedgerSerializer(reversal).data, status=status.HTTP_201_CREATED)
