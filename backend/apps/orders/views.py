"""订单管理视图：列表/建单/详情/修改/删除/状态/完成度（7 端点，契约 §四）。

口径约定（实施方案 §2.5）：
  - 分子 = 挂单床次菲票登记按色码聚合（Record 按 ticket.color/size 汇总），
    分母 = OrderItem.qty；落后格 = 完成率 < 80%（已决策）；
  - 单号留空自动 SO+日期+3 位顺延（all_objects 扫描防软删撞段，事务内重试防并发撞号）；
  - 同单同色码唯一、总数量 > 0；DELETE 已挂床次 400（只可取消）；
  - 全部端点走 check_perm('orders.manage')（legacy_key='production'，旧裁床岗零回归）。
统计聚合函数集中在本模块（_aggregate_stats），列表与详情共用，进度端点复用同一
Record 聚合口径；后续 stats.py 口径唯一化（SA2 P0）时迁移消费，不改公式。
"""
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q, Sum
from django.http import Http404
from django.utils import timezone
from rest_framework import generics
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.mixins import CompanyContextMixin
from apps.core.pagination import DefaultPagination
from apps.core.utils import q3
from apps.ops.models import AuditLog
from apps.ops.services import audit
from apps.orders.models import Order, OrderItem
from apps.orders.serializers import OrderSerializer

# 落后格阈值（已决策：完成率 < 80% 红底）；交期黄灯天数（≤7 天）
LAGGING_THRESHOLD = 80.0
DELIVERY_WARN_DAYS = 7


def _purge_deleted(model, **filters):
    """同业务键存在软删行时防历史遗留干扰，先物理清理（沿用生产模块惯例）。"""
    model.all_objects.filter(is_deleted=True, **filters).delete()


def _gen_order_no(company) -> str:
    """单号留空自动生成：SO + 日期(YYYYMMDD) + 当日 3 位顺延（2026-10-21 需求 #3）。

    seq = 当日本公司最大序号 + 1；扫描 all_objects（含软删行）防软删段复用混乱；
    并发撞唯一约束由调用方事务内重试一次（候选号 +1）。
    """
    prefix = f'SO{timezone.localdate():%Y%m%d}'
    max_seq = 0
    for no in Order.all_objects.filter(company=company, order_no__startswith=prefix) \
            .values_list('order_no', flat=True):
        suffix = no[len(prefix):]
        if suffix.isdigit():
            max_seq = max(max_seq, int(suffix))
    return f'{prefix}{max_seq + 1:03d}'


def _validated_style(company, raw):
    """款式归属校验：传了就必须本公司存在（防跨厂挂他厂款式）。

    存量兼容：POST/PUT 不传 style 保持空（存量单无款式；改单不传不清空）。
    """
    if raw in (None, ''):
        return None
    from apps.catalog.models import Style
    style = Style.objects.filter(company=company, pk=raw).first()
    if style is None:
        raise ValidationError({'style': '款式不存在'})
    return style


def _validated_image_paths(raw):
    """备注图片（2026-10-21 需求 #3）：相对路径字符串数组，最多 9 张。"""
    if raw in (None, ''):
        return []
    if (not isinstance(raw, list)
            or any(not isinstance(p, str) or not p.strip() for p in raw)):
        raise ValidationError({'images': '备注图片须为路径数组'})
    if len(raw) > 9:
        raise ValidationError({'images': '备注图片最多 9 张'})
    return [p.strip() for p in raw]


def _validated_order_process_items(company, raw):
    """订单关联工序（2026-10-21 需求 #3）：[{process_id, price}]，工序须本公司存在。"""
    if raw in (None, ''):
        return []
    if not isinstance(raw, list):
        raise ValidationError({'process_items': '订单工序须为列表'})
    from apps.catalog.models import Process
    prepared = []
    for idx, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValidationError({'process_items': f'第{idx + 1}项格式不正确'})
        try:
            process_id = int(item.get('process_id'))
        except (TypeError, ValueError):
            raise ValidationError({'process_items': f'第{idx + 1}项缺少有效工序'})
        price = item.get('price', 0)
        if price is None:
            price = 0
        try:
            price_v = q3(price)
        except Exception:
            raise ValidationError({'process_items': f'第{idx + 1}项单价不合法'})
        if price_v < 0:
            raise ValidationError({'process_items': f'第{idx + 1}项工价不能为负数'})
        prepared.append({'process_id': process_id, 'price': str(price_v)})
    found = set(Process.objects.filter(
        company=company, pk__in=[p['process_id'] for p in prepared]
    ).values_list('id', flat=True))
    for p in prepared:
        if p['process_id'] not in found:
            raise ValidationError({'process_items': f"工序 {p['process_id']} 不存在"})
    return prepared


def _validated_item_rows(order, raw):
    """建单/改单的 items 校验：非空列表、色码齐全、qty>0、同单同色码唯一。"""
    if not isinstance(raw, list) or not raw:
        raise ValidationError({'items': '订单明细不能为空'})
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
        rows.append(OrderItem(order=order, color=color, size=size, qty=qty))
    return rows


def _check_order_no_free(company, order_no, exclude_pk=None):
    """显式单号查重：仅拦未删除行（condition UK 的活跃分支），软删后同号可复用。"""
    qs = Order.objects.filter(company=company, order_no=order_no)
    if exclude_pk:
        qs = qs.exclude(pk=exclude_pk)
    if qs.exists():
        raise ValidationError({'order_no': '订单号已存在'})


def _aggregate_stats(company, order_ids):
    """按订单聚合：{id: (total_qty, registered_qty, progress%)}。

    registered = 挂单床次（production.order=单）活跃菲票的登记 Σqty；
    历史无单床次（order 为空）不进任何订单；进度封顶 100（防超登脏数据）。
    """
    from apps.records.models import Record
    total_map = {row['order_id']: int(row['qty'] or 0) for row in
                 OrderItem.objects.filter(order_id__in=order_ids)
                 .order_by().values('order_id').annotate(qty=Sum('qty'))}
    reg_map = {row['ticket__production__order_id']: int(row['qty'] or 0) for row in
               Record.objects.filter(company=company,
                                     ticket__is_deleted=False,
                                     ticket__production__is_deleted=False,
                                     ticket__production__order_id__in=order_ids)
               .order_by()  # order_by() 前置：防 Meta.ordering 混入 GROUP BY
               .values('ticket__production__order_id')
               .annotate(qty=Sum('qty'))}
    result = {}
    for oid in order_ids:
        total = total_map.get(oid, 0)
        registered = reg_map.get(oid, 0)
        progress = round(registered / total * 100, 1) if total else 0.0
        result[oid] = (total, registered, min(progress, 100.0))
    return result


def _attach_stats(company, rows, orders):
    """为序列化后的订单行填充统计字段（总量/登记/完成率/距交期天数/交期灯）。"""
    stats = _aggregate_stats(company, [o.id for o in orders])
    today = timezone.localdate()
    for order, row in zip(orders, rows):
        total, registered, progress = stats.get(order.id, (0, 0, 0.0))
        row['total_qty'] = total
        row['registered_qty'] = registered
        row['progress'] = progress
        if order.delivery_date:
            days_left = (order.delivery_date - today).days
            if order.status == Order.Status.PRODUCING and days_left < 0 and progress < 100:
                light = 'red'      # 过期未完成
            elif 0 <= days_left <= DELIVERY_WARN_DAYS:
                light = 'yellow'   # 距交期 ≤7 天
            else:
                light = 'green'
        else:
            days_left, light = None, 'green'
        row['days_left'] = days_left
        row['delivery_light'] = light


def _get_order(company, pk) -> Order:
    try:
        return Order.objects.get(company=company, pk=pk)
    except Order.DoesNotExist:
        raise Http404('订单不存在')


class OrderListCreateView(CompanyContextMixin, generics.ListCreateAPIView):
    """GET /orders（列表，status/keyword/overdue 过滤）+ POST /orders（建单）。"""
    serializer_class = OrderSerializer
    pagination_class = DefaultPagination

    def get_queryset(self):
        qs = Order.objects.filter(company=self.company).select_related('customer', 'style')
        status = self.request.query_params.get('status')
        if status in Order.Status.values:  # producing/done/cancelled；其余（all/空）不过滤
            qs = qs.filter(status=status)
        keyword = self.request.query_params.get('keyword')
        if keyword:
            qs = qs.filter(Q(order_no__icontains=keyword) | Q(customer__name__icontains=keyword)
                           | Q(style__style_no__icontains=keyword)
                           | Q(style__name__icontains=keyword))
        if self.request.query_params.get('overdue') == '1':
            qs = qs.filter(delivery_date__lt=timezone.localdate(),
                           status=Order.Status.PRODUCING)
        return qs

    def list(self, request, *args, **kwargs):
        # 全端点走 orders.manage（含查询：订单页签由持点岗位/管理员使用）
        self.check_perm('orders.manage')
        page = self.paginate_queryset(self.get_queryset())
        rows = self.get_serializer(page, many=True).data
        _attach_stats(self.company, rows, page)
        return self.get_paginated_response(rows)

    def perform_create(self, serializer):
        self.check_perm('orders.manage')
        order_no = (self.request.data.get('order_no') or '').strip()
        if order_no:
            # 软删行先物理清理（沿用 _purge_deleted 惯例），活跃行撞号 400
            _purge_deleted(Order, company=self.company, order_no=order_no)
            _check_order_no_free(self.company, order_no)
        style = _validated_style(self.company, self.request.data.get('style'))
        images = _validated_image_paths(self.request.data.get('images'))
        process_items = _validated_order_process_items(
            self.company, self.request.data.get('process_items'))
        rows = _validated_item_rows(None, self.request.data.get('items'))
        extra = {'style': style, 'images': images, 'process_items': process_items}
        if order_no:
            with transaction.atomic():
                instance = serializer.save(company=self.company, order_no=order_no, **extra)
                for r in rows:
                    r.order = instance
                OrderItem.objects.bulk_create(rows)
        else:
            # 自动取号（SO+日期+3位顺延）：候选号在 autocommit 下读取（不持共享锁，
            # 防两并发读-写事务互相升级锁死锁）；撞唯一约束或 SQLite 写锁冲突
            # （rollback journal 下并发写升级为立即 BUSY）都重取号重试
            import random
            import time as _time
            from django.db import OperationalError
            instance = None
            for attempt in range(5):
                candidate = _gen_order_no(self.company)
                try:
                    with transaction.atomic():
                        instance = serializer.save(
                            company=self.company, order_no=candidate, **extra)
                        for r in rows:
                            r.order = instance
                        OrderItem.objects.bulk_create(rows)
                    break
                except (IntegrityError, OperationalError):
                    if attempt == 4:
                        raise ValidationError({'order_no': '订单号生成冲突，请重试'})
                    _time.sleep(random.uniform(0.05, 0.25))
        audit(self.company, AuditLog.Type.OTHER, operator=self.request.user,
              message=f'新建订单：【{instance.order_no}】')


class OrderDetailView(CompanyContextMixin, generics.RetrieveUpdateDestroyAPIView):
    """GET / PUT / DELETE /orders/<id>。PUT 的 items 全量替换；DELETE 已挂床次 400。"""
    serializer_class = OrderSerializer

    def get_object(self):
        return _get_order(self.company, self.kwargs['pk'])

    def retrieve(self, request, *args, **kwargs):
        self.check_perm('orders.manage')
        order = self.get_object()
        row = self.get_serializer(order).data
        _attach_stats(self.company, [row], [order])
        return Response(row)

    def perform_update(self, serializer):
        self.check_perm('orders.manage')
        instance = self.get_object()
        order_no = (self.request.data.get('order_no') or '').strip()
        if order_no and order_no != instance.order_no:
            _purge_deleted(Order, company=self.company, order_no=order_no)
            _check_order_no_free(self.company, order_no, exclude_pk=instance.pk)
        extra = {}
        # 显式传 style 才动款式；显式清空（null/''）拒绝（新单必须挂款式）
        if 'style' in self.request.data:
            raw = self.request.data.get('style')
            if raw in (None, ''):
                raise ValidationError({'style': '请关联款式'})
            extra['style'] = _validated_style(self.company, raw)
        if 'images' in self.request.data:
            extra['images'] = _validated_image_paths(self.request.data.get('images'))
        if 'process_items' in self.request.data:
            extra['process_items'] = _validated_order_process_items(
                self.company, self.request.data.get('process_items'))
        with transaction.atomic():
            updated = serializer.save(
                order_no=order_no or instance.order_no, **extra)
            # items 全量替换：显式传 items 才动明细，缺省保持原明细
            if self.request.data.get('items') is not None:
                rows = _validated_item_rows(updated, self.request.data.get('items'))
                OrderItem.all_objects.filter(order=updated).delete()
                if rows:
                    OrderItem.objects.bulk_create(rows)
        audit(self.company, AuditLog.Type.OTHER, operator=self.request.user,
              message=f'修改订单：【{updated.order_no}】')

    def perform_destroy(self, instance):
        self.check_perm('orders.manage')
        from apps.production.models import Production
        # 已挂床次不可删只可取消（§2.5.2）
        if Production.objects.filter(company=self.company, order=instance).exists():
            raise ValidationError('该订单已挂床次，不可删除，只可取消')
        audit(self.company, AuditLog.Type.OTHER, operator=self.request.user,
              message=f'删除订单：【{instance.order_no}】')
        with transaction.atomic():
            instance.items.update(is_deleted=True)
            instance.is_deleted = True
            instance.save(update_fields=['is_deleted', 'updated_at'])


class OrderStatusView(CompanyContextMixin, APIView):
    """PUT /orders/<id>/status：done/cancelled；<100% 允许完成（管理员知情操作）。"""

    def put(self, request, pk):
        self.check_perm('orders.manage')
        order = _get_order(self.company, pk)
        status = request.data.get('status')
        if status not in (Order.Status.DONE, Order.Status.CANCELLED):
            raise ValidationError({'status': '状态不合法，仅允许 done/cancelled'})
        if status != order.status:
            order.status = status
            order.save(update_fields=['status', 'updated_at'])
            label = dict(Order.Status.choices)[status]
            audit(self.company, AuditLog.Type.OTHER, operator=request.user,
                  message=f'订单状态变更：【{order.order_no}】→ {label}')
        return Response({'id': order.id, 'status': order.status})


class OrderProgressView(CompanyContextMixin, APIView):
    """GET /orders/<id>/progress：色码矩阵完成度（分子=挂单床次登记按色码聚合）。

    2026-10-22 增补 cut_qty/cut_total：挂单床次菲票按色码聚合的「已裁」数——
    开床余量口径（订单数−已裁），供二次开床预填与超量校验使用；
    registered_qty 是扫菲进度（与还能开多少床无关），不可混用。
    """

    def get(self, request, pk):
        self.check_perm('orders.manage')
        order = _get_order(self.company, pk)
        from apps.records.models import Record
        from apps.production.models import Production
        from apps.catalog.models import Ticket
        reg_rows = (Record.objects.filter(
                        company=self.company,
                        ticket__is_deleted=False,
                        ticket__production__is_deleted=False,
                        ticket__production__order=order)
                    .order_by()  # order_by() 前置：防 Meta.ordering 混入 GROUP BY
                    .values('ticket__color', 'ticket__size')
                    .annotate(qty=Sum('qty')))
        reg_map = {(r['ticket__color'], r['ticket__size']): int(r['qty'] or 0)
                   for r in reg_rows}
        cut_rows = (Ticket.objects.filter(
                        company=self.company,
                        is_deleted=False,
                        production__is_deleted=False,
                        production__order=order)
                    .order_by()
                    .values('color', 'size')
                    .annotate(qty=Sum('qty')))
        cut_map = {(r['color'], r['size']): int(r['qty'] or 0) for r in cut_rows}
        items = []
        total_qty = total_reg = total_cut = 0
        for it in order.items.all():
            registered = reg_map.get((it.color, it.size), 0)
            cut = cut_map.get((it.color, it.size), 0)
            progress = round(registered / it.qty * 100, 1) if it.qty else 0.0
            progress = min(progress, 100.0)
            total_qty += it.qty
            total_reg += registered
            total_cut += cut
            items.append({
                'id': it.id, 'color': it.color, 'size': it.size, 'qty': it.qty,
                'registered_qty': registered, 'cut_qty': cut, 'progress': progress,
                'lagging': progress < LAGGING_THRESHOLD,  # 落后格（前端红底）
            })
        beds = (Production.objects.filter(company=self.company, order=order)
                .select_related('style'))
        return Response({
            'id': order.id,
            'order_no': order.order_no,
            'status': order.status,
            'total_qty': total_qty,
            'registered_qty': total_reg,
            'cut_qty': total_cut,
            # 上限 100 与订单列表/色码矩阵口径一致（多登记只封顶显示，不放大百分比）
            'progress': min(round(total_reg / total_qty * 100, 1), 100.0) if total_qty else 0.0,
            'items': items,
            'productions': [{'id': b.id, 'bed_no': b.bed_no,
                             'style_no': b.style.style_no,
                             'total_qty': b.total_qty} for b in beds],
        })
