"""订单成本视图（P1 cost 域，契约 §四 端点；公司隔离 + 2 段式权限点）。

权限：GET 类挂 cost.view；recompute/overhead/batch 挂 cost.manage。
两点 legacy=False（决策表「P1 权限点」）——legacy 岗位默认零授权、零回归；
岗位映射建议：老板/厂长全开、跟单+cost.view。

revenue 口径缓冲：revenue=Σ OrderItem.qty×price 是理论收入，
与实际收款（折扣/尾数/坏账）差异属预期，P2 应收核销后以收款实绩复核。
"""
from django.http import Http404
from django.utils import timezone
from django.utils.dateparse import parse_date
from rest_framework import generics
from rest_framework.exceptions import ValidationError
from django.db.models import Q
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.mixins import CompanyContextMixin
from apps.core.pagination import DefaultPagination
from apps.cost.models import OrderCost
from apps.cost.serializers import OrderCostSerializer, OrderPnlSerializer, OverheadSerializer
from apps.cost.services import recompute, recompute_batch, wage_reconciliation
from apps.ops.models import AuditLog
from apps.ops.services import audit
from apps.orders.models import Order


def _get_active_order(company, pk) -> Order:
    """软删订单一律 404（列表不显/重算 404/制费 404，§3.3 软删边界）。"""
    order = Order.all_objects.filter(company=company, pk=pk).first()
    if order is None or order.is_deleted:
        raise Http404('订单不存在')
    return order


class OrderCostListView(CompanyContextMixin, generics.ListAPIView):
    """GET /cost/orders?date_from=&date_to=&keyword=&computed_at_from=

    过滤：date_from/date_to=订单创建日期；keyword=订单号/客户/款号/款名；
    computed_at_from=计算时间（支撑「月底一键重算后看增量」）。
    软删订单不显（join 过滤 is_deleted=False）。
    """

    serializer_class = OrderCostSerializer
    pagination_class = DefaultPagination

    def get_queryset(self):
        qs = (OrderCost.objects.filter(company=self.company, order__is_deleted=False)
              .select_related('order', 'order__customer', 'order__style',
                              'computed_by'))
        p = self.request.query_params
        date_from = parse_date(p.get('date_from') or '')
        if date_from:
            qs = qs.filter(order__created_at__date__gte=date_from)
        date_to = parse_date(p.get('date_to') or '')
        if date_to:
            qs = qs.filter(order__created_at__date__lte=date_to)
        keyword = (p.get('keyword') or '').strip()
        if keyword:
            from django.db.models import Q
            qs = qs.filter(
                Q(order__order_no__icontains=keyword)
                | Q(order__customer__name__icontains=keyword)
                | Q(order__style__style_no__icontains=keyword)
                | Q(order__style__name__icontains=keyword))
        computed_at_from = parse_date(p.get('computed_at_from') or '')
        if computed_at_from:
            qs = qs.filter(computed_at__date__gte=computed_at_from)
        return qs

    def list(self, request, *args, **kwargs):
        self.check_perm('cost.view')
        return super().list(request, *args, **kwargs)


class OrderPnlListView(CompanyContextMixin, generics.ListAPIView):
    """GET /cost/orders/pnl?keyword=&date_from=&date_to=&status=

    单订单损益视图（评审 Top10 #2，2026-11-02）：成本域 OrderCost 全量行
    叠加应收实绩（Receivable 按 order 聚合）。挂 cost.view（只读）。
    口径：
      - revenue/gross_profit = 理论口径（Σ OrderItem.qty×price − total_cost）
      - ar_received/real_profit = 实收口径（已核销 − total_cost）
      - 不挂单应收不计入（与成本「挂单才算」同口径，防毛利虚高口径混乱）
      - is_stale=True 行表示上游领料/登记/制费变动未重算，前端须提示
    status 过滤为订单状态（producing/done/cancelled），keyword 同成本列表口径。
    """

    serializer_class = OrderPnlSerializer
    pagination_class = DefaultPagination

    def get_queryset(self):
        from django.db.models import Sum
        from apps.finance.models import Receivable
        qs = (OrderCost.objects.filter(company=self.company, order__is_deleted=False)
              .select_related('order', 'order__customer', 'order__style')
              .annotate(
                  ar_amount=Sum('order__finance_receivables__amount',
                                filter=Q(order__finance_receivables__is_deleted=False)),
                  ar_received=Sum('order__finance_receivables__received_amount',
                                  filter=Q(order__finance_receivables__is_deleted=False))))
        p = self.request.query_params
        date_from = parse_date(p.get('date_from') or '')
        if date_from:
            qs = qs.filter(order__created_at__date__gte=date_from)
        date_to = parse_date(p.get('date_to') or '')
        if date_to:
            qs = qs.filter(order__created_at__date__lte=date_to)
        order_status = (p.get('status') or '').strip()
        if order_status:
            qs = qs.filter(order__status=order_status)
        keyword = (p.get('keyword') or '').strip()
        if keyword:
            qs = qs.filter(
                Q(order__order_no__icontains=keyword)
                | Q(order__customer__name__icontains=keyword)
                | Q(order__style__style_no__icontains=keyword)
                | Q(order__style__name__icontains=keyword))
        return qs

    def list(self, request, *args, **kwargs):
        self.check_perm('cost.view')
        return super().list(request, *args, **kwargs)


class OrderCostRecomputeView(CompanyContextMixin, APIView):
    """POST /cost/orders/{order_id}/recompute 单订单重算（挂 cost.manage）。

    软删订单 404；无 items 订单算全 0 行照常落表；幂等可重复执行。
    """

    def post(self, request, order_id):
        self.check_perm('cost.manage')
        order = _get_active_order(self.company, order_id)
        oc = recompute(self.company, order, user=request.user)
        return Response(OrderCostSerializer(oc).data)


class OrderCostRecomputeBatchView(CompanyContextMixin, APIView):
    """POST /cost/orders/recompute-batch 批量重算（月底一键，挂 cost.manage）。

    body 二选一：{'order_ids': [...]} 或 {'date_from': 'YYYY-MM-DD',
    'date_to': 'YYYY-MM-DD'}（订单创建日期范围，可只传一端）。
    返回 {'success': [order_id...], 'failed': [{order_id, error}], 'count': n}。
    """

    def post(self, request):
        self.check_perm('cost.manage')
        order_ids = request.data.get('order_ids')
        qs = Order.objects.filter(company=self.company, is_deleted=False)
        if order_ids is not None:
            if not isinstance(order_ids, list) or not order_ids:
                raise ValidationError({'order_ids': 'order_ids 须为非空数组'})
            try:
                ids = [int(x) for x in order_ids]
            except (TypeError, ValueError):
                raise ValidationError({'order_ids': 'order_ids 须为整数数组'})
            # 软删/不属于本公司/不存在的 id 进 failed（不静默丢）
            found = {o.id: o for o in qs.filter(pk__in=ids)}
            orders = [found[i] for i in dict.fromkeys(ids) if i in found]
            failed = [{'order_id': i, 'error': '订单不存在'}
                      for i in dict.fromkeys(ids) if i not in found]
        else:
            date_from = parse_date(request.data.get('date_from') or '')
            date_to = parse_date(request.data.get('date_to') or '')
            if not date_from and not date_to:
                raise ValidationError(
                    {'order_ids': '请传 order_ids 数组，或 date_from/date_to 日期范围'})
            if date_from:
                qs = qs.filter(created_at__date__gte=date_from)
            if date_to:
                qs = qs.filter(created_at__date__lte=date_to)
            orders = list(qs.order_by('id'))
            failed = []
        success, batch_failed = recompute_batch(self.company, orders,
                                                user=request.user)
        return Response({'success': success, 'failed': failed + batch_failed,
                         'count': len(success)})


class OrderCostOverheadView(CompanyContextMixin, APIView):
    """PUT /cost/orders/{order_id}/overhead 录制费/管理费（挂 cost.manage）。

    改动走 ops.services.audit 留痕（before/after 快照）；成本行不存在时
    get_or_create 出草稿行承接手录值；改动即翻 is_stale（stale 埋点④，
    合计口径待重算收敛）。
    """

    def put(self, request, order_id):
        self.check_perm('cost.manage')
        order = _get_active_order(self.company, order_id)
        s = OverheadSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        new_val = s.validated_data['overhead_cost']
        from django.db import transaction
        from apps.cost.services import mark_orders_stale
        with transaction.atomic():
            oc, created = (OrderCost.all_objects.select_for_update()
                           .get_or_create(company=self.company, order=order,
                                          defaults={'overhead_cost': new_val,
                                                    'status': OrderCost.Status.DRAFT}))
            if not created:
                old_val = oc.overhead_cost
                oc.overhead_cost = new_val
                oc.save(update_fields=['overhead_cost', 'updated_at'])
            else:
                old_val = None
            mark_orders_stale(self.company, [order.pk])
            oc.refresh_from_db()  # 埋点走 queryset.update，不回刷内存对象；响应须反映最新 is_stale
        audit(self.company, AuditLog.Type.OTHER, operator=request.user,
              before={'order_id': order.pk, 'order_no': order.order_no,
                      'overhead_cost': str(old_val) if old_val is not None else None},
              after={'order_id': order.pk, 'order_no': order.order_no,
                     'overhead_cost': str(new_val)},
              message=(f'订单制费修改：【{order.order_no}】'
                       f'{old_val if old_val is not None else "（空）"}→{new_val}'))
        return Response(OrderCostSerializer(oc).data)


class WageReconciliationView(CompanyContextMixin, APIView):
    """GET /cost/wage-reconciliation?date_from=&date_to= 工资总额对账单（挂 cost.view）。

    Σ订单 labor_cost + whole/commission/fixed 与工资发放总额的逐项差异列表；
    日期缺省=本月 1 日至今天。口径差异属预期，逐项列来源（见 services）。
    """

    def get(self, request):
        self.check_perm('cost.view')
        today = timezone.localdate()
        date_from = parse_date(request.query_params.get('date_from') or '')
        date_to = parse_date(request.query_params.get('date_to') or '')
        if date_from and date_to and date_from > date_to:
            raise ValidationError({'date_from': '起始日期不能晚于截止日期'})
        data = wage_reconciliation(self.company,
                                   date_from or today.replace(day=1),
                                   date_to or today)
        return Response(data)
