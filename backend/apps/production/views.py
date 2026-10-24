"""生产管理视图：床次订单、锁定/裁完、订单工序、按扎号查看、裁货明细、扫码解析、生产计划看板聚合。"""
from datetime import datetime, timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, DecimalField, F, Min, Q, Sum
from django.http import Http404
from django.utils import timezone
from rest_framework import generics
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.catalog.models import Ticket
from apps.accounts.models import UserCompany
from apps.core.mixins import CompanyContextMixin
from apps.core.pagination import DefaultPagination
from apps.core.utils import q3
from apps.ops.models import AuditLog, CompanySetting
from apps.ops.services import audit
from apps.orders.models import Order
from apps.production.models import Production, ProductionProcess
from apps.production.plan_rows import build_plan_rows, plan_window
from apps.production.serializers import ProductionSerializer
from apps.production.stats import (
    bed_progress_stats,
    bundle_ticket_stats,
    done_working_qty,
    production_pc_map,
    production_process_count,
    ticket_record_rows,
)


def _purge_deleted(model, **filters):
    """同业务键存在软删行时会撞唯一约束，先物理清理。"""
    model.all_objects.filter(is_deleted=True, **filters).delete()


def _recalc_total(production):
    """按菲票汇总重算裁货总数量。"""
    from apps.catalog.models import Ticket as _Ticket
    total = _Ticket.objects.filter(production=production).aggregate(s=Sum('qty'))['s'] or 0
    production.total_qty = int(total)
    production.save(update_fields=['total_qty', 'updated_at'])
    return production.total_qty


def _save_order_processes(production, request_data):
    """保存订单工序（可覆盖款式默认单价），全量替换。"""
    items = request_data.get('process_items')
    if items is None:
        return
    if not isinstance(items, list):
        raise ValidationError({'process_items': '工序配置格式不正确'})

    from apps.catalog.models import Process
    rows = []
    seen = set()
    for idx, item in enumerate(items):
        try:
            process_id = int(item.get('process_id'))
        except (TypeError, ValueError):
            raise ValidationError({'process_items': f'第{idx + 1}项缺少有效工序'})
        if process_id in seen:
            raise ValidationError({'process_items': '工序配置中存在重复工序'})
        seen.add(process_id)
        process = Process.objects.filter(company=production.company_id, pk=process_id).first()
        if not process:
            raise ValidationError({'process_items': f'工序 {process_id} 不存在'})
        price = item.get('price')
        if price in (None, ''):
            price = process.default_price
        try:
            price = q3(price)
        except Exception:
            raise ValidationError({'process_items': f'第{idx + 1}项单价不合法'})
        if price < 0:
            raise ValidationError({'process_items': f'第{idx + 1}项单价不合法'})
        rows.append(ProductionProcess(production=production, process=process, price=price))

    with transaction.atomic():
        ProductionProcess.all_objects.filter(production=production).delete()
        if rows:
            ProductionProcess.objects.bulk_create(rows)


def _inherit_order_processes(production, order):
    """挂单开床继承订单工序（2026-10-22 用户反馈）：向导工序环节已去掉、建床请求
    不带 process_items 时，把订单上模板填充的关联工序 [{process_id, price}]
    继承为床次订单工序，避免「订单配了工序、床次却显示未配置」。"""
    items = order.process_items or []
    if not items or ProductionProcess.all_objects.filter(production=production).exists():
        return
    from apps.catalog.models import Process
    rows = []
    for item in items:
        try:
            pid = int(item.get('process_id'))
        except (TypeError, ValueError):
            continue
        process = Process.objects.filter(company=production.company_id, pk=pid).first()
        if not process:
            continue
        price = item.get('price')
        if price in (None, ''):
            price = process.default_price
        try:
            price = q3(price)
        except Exception:
            price = q3(process.default_price)
        if price < 0:
            price = q3(process.default_price)
        rows.append(ProductionProcess(production=production, process=process, price=price))
    if rows:
        ProductionProcess.objects.bulk_create(rows)


def _process_count(production) -> int:
    """该床次配置的工序总数（订单工序优先，退回款式工序）。

    口径唯一（§2.9 stats.py）：实现收编至 stats.production_process_count。
    """
    return production_process_count(production)


def _production_record_stats(production_ids):
    """按床次聚合登记：{production_id: (登记件数, 已完成扎数, 扎总数, 进度%)}。

    口径唯一（§2.9 stats.py）：实现收编至 stats.bed_progress_stats。
    进度% = 各扎「已登记工序数/床次配置工序数」的平均（0~100）——
    登记件数是全部工序累加（一扎过 N 道工序记 N 次），直接除裁货数会超 100%（看板 630% bug）。
    【2026-10-20 收编修复】原实现 Record 过滤走 company__productions__id__in 跨表
    JOIN，厂内每张床都会把登记行放大 N 倍（N=厂内床次数），列表页 registered_qty
    虚高；统一后与工作台同口径（company+production_id 过滤），golden 快照对照见
    backend/test_stats_refactor.py 头注。
    """
    return bed_progress_stats(None, production_ids)


def _attach_stats(company, items):
    """为序列化后的床次列表填充统计字段。"""
    ids = [item['id'] for item in items]
    if not ids:
        return
    stats = _production_record_stats(ids)
    for item in items:
        registered, done_tickets, ticket_count, progress = stats.get(item['id'], (0, 0, 0, 0.0))
        item['registered_qty'] = registered
        item['done_ticket_count'] = done_tickets
        item['ticket_count'] = ticket_count
        item['cut_total'] = item.get('total_qty', 0)


class ProductionListCreateView(CompanyContextMixin, generics.ListCreateAPIView):
    """床次订单列表 / 新建。"""
    serializer_class = ProductionSerializer
    pagination_class = DefaultPagination

    def get_queryset(self):
        qs = Production.objects.filter(company=self.company).select_related(
            'style', 'customer')
        keyword = self.request.query_params.get('keyword')
        if keyword:
            qs = qs.filter(Q(style__style_no__icontains=keyword)
                           | Q(style__name__icontains=keyword)
                           | Q(bed_no__icontains=keyword))
        bed_no = self.request.query_params.get('bed_no')
        if bed_no:
            qs = qs.filter(bed_no__iexact=bed_no)
        style_id = self.request.query_params.get('style_id')
        if style_id:
            qs = qs.filter(style_id=style_id)
        order_type = self.request.query_params.get('order_type')
        if order_type:
            qs = qs.filter(order_type=order_type)
        is_locked = self.request.query_params.get('is_locked')
        if is_locked in ('0', '1'):
            qs = qs.filter(is_locked=(is_locked == '1'))
        is_cut_done = self.request.query_params.get('is_cut_done')
        if is_cut_done in ('0', '1'):
            qs = qs.filter(is_cut_done=(is_cut_done == '1'))
        # 挂单过滤（2026-10-20 P1）：按客户订单筛床次（订单详情关联床次列表用）
        order_id = self.request.query_params.get('order_id')
        if order_id:
            qs = qs.filter(order_id=order_id)
        return qs

    def list(self, request, *args, **kwargs):
        page = self.paginate_queryset(self.get_queryset())
        serializer = self.get_serializer(page, many=True)
        items = serializer.data
        _attach_stats(self.company, items)
        return self.get_paginated_response(items)

    def perform_create(self, serializer):
        self.check_perm('production.manage')
        bed_no = (serializer.validated_data.get('bed_no') or '').strip()
        if not bed_no:
            raise ValidationError({'bed_no': '床次号不能为空'})
        _purge_deleted(Production, company=self.company, bed_no=bed_no)
        if Production.objects.filter(company=self.company, bed_no=bed_no).exists():
            raise ValidationError({'bed_no': '床次号已存在'})
        try:
            total_qty = int(self.request.data.get('total_qty') or 0)
        except (TypeError, ValueError):
            total_qty = 0
        # plan_qty（2026-10-18 拍板新增）：计划裁货数，可空；与实裁 total_qty 分账，
        # 不做强制校验（已裁≠计划先只做数据可查，看板暂不消费）。
        try:
            plan_qty = int(self.request.data.get('plan_qty') or 0)
        except (TypeError, ValueError):
            plan_qty = 0
        # 「默认裁完」开关不再于建床时置 is_cut_done（2026-10-18 拍板修正）：
        # 旧实现「建床即裁完」会反向拦截编菲（TicketBatchView）与登记，开关形自锁；
        # 新语义：auto_cut_done = 「编菲成功后自动置裁完」，在 TicketBatchView 落地
        # （系统无裁剪类工序标识，无法按工序登记自动判定裁完，故采用编菲完成后
        # 自动标记 + 人工可在详情取消/重标的兜底设计）。
        # ---- 挂单（2026-10-20 P1 契约 §四）：可选 order_id ----
        # 校验：订单不存在 404 / 跨公司 400 / 非 producing 400；
        # source_order_no 自动带出订单号（可被显式传入覆盖）；不带 order_id 走存量路径不变。
        order = None
        order_id = self.request.data.get('order_id')
        if order_id not in (None, ''):
            order = Order.objects.filter(pk=order_id).first()
            if order is None:
                raise Http404('订单不存在')
            if order.company_id != self.company.id:
                raise ValidationError({'order_id': '订单不属于本公司'})
            if order.status != Order.Status.PRODUCING:
                raise ValidationError({'order_id': '订单已完结或已取消，不可挂单'})
        with transaction.atomic():
            # process_items 由 _save_order_processes 从 request.data 落库，
            # 不能留在 validated_data 里（save 会当模型字段传给 Production → TypeError）
            serializer.validated_data.pop('process_items', None)
            if order is not None:
                src = (serializer.validated_data.get('source_order_no') or '').strip()
                # 挂单款式继承（2026-10-21 需求 #3）：带 order_id 时 style 可不传，
                # 兜底取订单款式（订单未挂款式则仍须显式传 style）
                style = (serializer.validated_data.get('style')
                         or (order.style if order.style_id else None))
                if style is None:
                    raise ValidationError({'style': '请选择款式'})
                if src:
                    instance = serializer.save(company=self.company,
                                               total_qty=max(total_qty, 0),
                                               plan_qty=plan_qty or None, order=order,
                                               style=style)
                else:
                    instance = serializer.save(company=self.company,
                                               total_qty=max(total_qty, 0),
                                               plan_qty=plan_qty or None, order=order,
                                               style=style,
                                               source_order_no=order.order_no)
            else:
                instance = serializer.save(company=self.company,
                                           total_qty=max(total_qty, 0),
                                           plan_qty=plan_qty or None)
            _save_order_processes(instance, self.request.data)
            # 挂单继承订单工序（2026-10-22）：显式传了 process_items（含空表）以请求为准，
            # 未传时订单有关联工序则自动落为床次订单工序
            if order is not None and self.request.data.get('process_items') is None:
                _inherit_order_processes(instance, order)
        audit(self.company, AuditLog.Type.OTHER, operator=self.request.user,
              bed_no=instance.bed_no,
              style_no=instance.style.style_no,
              message=f'新建床次订单：【床次{instance.bed_no}】'
                      +(f'（挂单 {order.order_no}）' if order is not None else ''))


class ProductionDetailView(CompanyContextMixin, generics.RetrieveUpdateDestroyAPIView):
    """床次订单详情 / 修改 / 删除。"""
    serializer_class = ProductionSerializer

    def get_object(self):
        try:
            return Production.objects.select_related('style', 'customer').get(
                company=self.company, pk=self.kwargs['pk'])
        except Production.DoesNotExist:
            raise Http404('床次订单不存在')

    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        data = self.get_serializer(instance).data
        _attach_stats(self.company, [data])
        return Response(data)

    def perform_update(self, serializer):
        self.check_perm('production.manage')
        instance = self.get_object()
        bed_no = (serializer.validated_data.get('bed_no') or instance.bed_no).strip()
        if bed_no != instance.bed_no:
            _purge_deleted(Production, company=self.company, bed_no=bed_no)
            if Production.objects.filter(company=self.company, bed_no=bed_no) \
                    .exclude(pk=instance.pk).exists():
                raise ValidationError({'bed_no': '床次号已存在'})
        with transaction.atomic():
            # 同 perform_create：process_items 走 _save_order_processes，不进模型 save
            serializer.validated_data.pop('process_items', None)
            updated = serializer.save()
            _save_order_processes(updated, self.request.data)

    def perform_destroy(self, instance):
        # Plan A：删除床次为高危点（不获旧 'production' 覆盖，需显式授权）
        self.check_perm('production.delete')
        from apps.records.models import Record
        from apps.catalog.models import Ticket as _Ticket
        if Record.objects.filter(company=self.company,
                                 ticket__production=instance).exists():
            raise ValidationError('该床次已有登记记录，需先撤销全部登记')
        audit(self.company, AuditLog.Type.OTHER, operator=self.request.user,
              bed_no=instance.bed_no,
              message=f'删除床次订单：【床次{instance.bed_no}】')
        # A4 最小修复（2026-10-18）：BaseModel.delete 只软删自身，其下菲票若保持
        # 活跃会继续进入票查询/看板 order_total/款式统计等口径。选择「同事务级联
        # 软删菲票」而非各查询加过滤——默认管理器（is_deleted=False）在所有消费方
        # 自动生效，改动面最小且不易漏。前置守卫已保证此时床上无任何登记记录。
        with transaction.atomic():
            _Ticket.objects.filter(production=instance).update(is_deleted=True)
            instance.is_deleted = True
            instance.save(update_fields=['is_deleted', 'updated_at'])


class ProductionLockView(CompanyContextMixin, APIView):
    """锁定 / 解锁床次（防错登、已结款订单）。锁定与解锁均需管理员权限，写日志。"""

    def put(self, request, pk):
        # Plan A：锁定/解锁床次由「仅管理员」放开为权限点 production.lock
        self.check_perm('production.lock')
        production = self._get(pk)
        locked = request.data.get('locked')
        locked = (not production.is_locked) if locked is None else bool(locked)
        if locked != production.is_locked:
            production.is_locked = locked
            production.save(update_fields=['is_locked', 'updated_at'])
            action = '锁定' if locked else '解锁'
            audit(self.company, AuditLog.Type.OTHER, operator=request.user,
                  bed_no=production.bed_no,
                  style_no=production.style.style_no,
                  message=f'{action}床次：【床次{production.bed_no}】操作人：{request.user}')
        return Response({'id': production.id, 'is_locked': production.is_locked})

    def _get(self, pk):
        try:
            return Production.objects.select_related('style').get(
                company=self.company, pk=pk)
        except Production.DoesNotExist:
            raise Http404('床次订单不存在')


class ProductionCutDoneView(CompanyContextMixin, APIView):
    """标记裁完 / 取消裁完（= 裁剪完成标记，2026-10-18 拍板）。

    裁完仅作状态：不拦截登记、不拦截编菲加扎；auto_cut_done 开启时编菲成功
    后会自动置裁完（见 TicketBatchView），人工可在此取消/重标。
    """

    def put(self, request, pk):
        self.check_perm('production.manage')
        try:
            production = Production.objects.select_related('style').get(
                company=self.company, pk=pk)
        except Production.DoesNotExist:
            raise Http404('床次订单不存在')
        is_cut_done = request.data.get('is_cut_done')
        is_cut_done = (not production.is_cut_done) if is_cut_done is None else bool(is_cut_done)
        if is_cut_done != production.is_cut_done:
            production.is_cut_done = is_cut_done
            production.save(update_fields=['is_cut_done', 'updated_at'])
            action = '标记裁完' if is_cut_done else '取消裁完'
            audit(self.company, AuditLog.Type.OTHER, operator=request.user,
                  bed_no=production.bed_no,
                  style_no=production.style.style_no,
                  message=f'{action}：【床次{production.bed_no}】')
        return Response({'id': production.id, 'is_cut_done': production.is_cut_done})


class ProductionColorStatsView(CompanyContextMixin, APIView):
    """厂内常用颜色（编菲点选，痛点：颜色手写繁琐）：按历史菲票颜色用量倒序取 top 20。"""

    def get(self, request):
        self.check_perm('production.manage')
        rows = (Ticket.objects.filter(company=self.company).exclude(color='')
                .values('color').annotate(cnt=Count('id'))
                .order_by('-cnt')[:20])
        return Response({'results': [{'name': r['color'], 'count': r['cnt']} for r in rows]})


class ProductionInputHintsView(CompanyContextMixin, APIView):
    """编菲录入提示（录入减负）：sizes=厂内历史尺码用量倒序 top 20；
    fabrics=厂内历史面料用量倒序 top 20（床次批次级 fabric 字段聚合）。"""

    def get(self, request):
        self.check_perm('production.manage')
        sizes = (Ticket.objects.filter(company=self.company).exclude(size='')
                 .values('size').annotate(cnt=Count('id')).order_by('-cnt')[:20])
        fabrics = (Production.objects.filter(company=self.company).exclude(fabric='')
                   .values('fabric').annotate(cnt=Count('id')).order_by('-cnt')[:20])
        return Response({
            'sizes': [{'name': r['size'], 'count': r['cnt']} for r in sizes],
            'fabrics': [{'name': r['fabric'], 'count': r['cnt']} for r in fabrics],
        })


class ProductionOrderProcessView(CompanyContextMixin, APIView):
    """订单工序：查看 / 全量替换该床次的工序及单价（可覆盖款式默认工序）。"""

    def get(self, request, pk):
        try:
            production = Production.objects.get(company=self.company, pk=pk)
        except Production.DoesNotExist:
            raise Http404('床次订单不存在')
        rows = ProductionProcess.objects.filter(production=production) \
            .select_related('process')
        data = [{'id': row.id, 'process': row.process_id,
                 'process_name': row.process.name,
                 'is_fixed': row.process.is_fixed, 'price': row.price}
                for row in rows]
        return Response(data)

    def put(self, request, pk):
        # Plan A：订单工序覆盖=本床次改价，走 styles.price 点；
        # 历史上该接口走 'production' 点——两把钥匙任一可开（零回归）
        if not (self.has_perm('styles.price') or self.has_perm('production')):
            raise PermissionDenied('没有该模块的操作权限')
        try:
            production = Production.objects.select_related('style').get(
                company=self.company, pk=pk)
        except Production.DoesNotExist:
            raise Http404('床次订单不存在')
        _save_order_processes(production, request.data)
        audit(self.company, AuditLog.Type.PROCESS, operator=request.user,
              bed_no=production.bed_no,
              style_no=production.style.style_no,
              message=f'更新订单工序：【床次{production.bed_no}】')
        rows = ProductionProcess.objects.filter(production=production) \
            .select_related('process')
        data = [{'id': row.id, 'process': row.process_id,
                 'process_name': row.process.name,
                 'is_fixed': row.process.is_fixed, 'price': row.price}
                for row in rows]
        return Response(data)


class ProductionBundleListView(CompanyContextMixin, APIView):
    """按扎号查看：展开该床次所有菲票扎，每扎显示状态（未登记/部分登记/完成）。"""

    def get(self, request, pk):
        try:
            production = Production.objects.select_related('style').get(
                company=self.company, pk=pk)
        except Production.DoesNotExist:
            raise Http404('床次订单不存在')

        tickets = Ticket.objects.filter(company=self.company, production=production) \
            .order_by('bed_no', 'id')
        # 每扎已登记的工序数与件数（口径唯一：stats.bundle_ticket_stats）
        rec_map = bundle_ticket_stats(self.company, production)
        process_count = _process_count(production)

        keyword = request.query_params.get('keyword')
        state = request.query_params.get('state')  # none/partial/done
        items = []
        for ticket in tickets:
            row = rec_map.get(ticket.id)
            pn = int(row['pn']) if row else 0
            qty = int(row['qty'] or 0) if row else 0
            if process_count and pn >= process_count:
                ticket_state = 'done'
            elif pn:
                ticket_state = 'partial'
            else:
                ticket_state = 'none'
            if keyword and keyword not in ticket.bundle_no:
                continue
            if state and ticket_state != state:
                continue
            items.append({
                'id': ticket.id,
                'bundle_no': ticket.bundle_no,
                'color': ticket.color,
                'size': ticket.size,
                'qty': ticket.qty,
                'qr_code': ticket.qr_code,
                'is_printed': ticket.is_printed,
                'registered_process_count': pn,
                'process_count': process_count,
                'registered_qty': qty,
                'state': ticket_state,
                'state_display': {'none': '未登记', 'partial': '部分登记',
                                  'done': '完成'}[ticket_state],
            })
        return Response({'count': len(items), 'process_count': process_count,
                         'results': items})


class ProductionCutDetailView(CompanyContextMixin, APIView):
    """裁货明细：颜色 × 尺码矩阵汇总视图。"""

    def get(self, request, pk):
        try:
            production = Production.objects.get(company=self.company, pk=pk)
        except Production.DoesNotExist:
            raise Http404('床次订单不存在')
        rows = (Ticket.objects.filter(company=self.company, production=production)
                .order_by()
                .values('color', 'size')
                .annotate(qty=Sum('qty'), bundle_count=Count('id')))
        colors, sizes = [], []
        cells = {}
        for row in rows:
            color = row['color'] or ''
            size = row['size'] or ''
            if color not in colors:
                colors.append(color)
            if size not in sizes:
                sizes.append(size)
            cells[f'{color}|{size}'] = {'qty': int(row['qty'] or 0),
                                        'bundle_count': row['bundle_count']}
        return Response({
            'bed_no': production.bed_no,
            'total_qty': production.total_qty,
            'colors': colors,
            'sizes': sizes,
            'cells': cells,
        })


class ScanParseView(CompanyContextMixin, APIView):
    """扫菲码解析：格式 companyNo|bedNo|styleNo|bundleNo|color|size|qty。

    返回菲票与床次状态，供扫菲登记页直接使用；跨公司菲票直接拦截。
    """

    def post(self, request):
        qr = (request.data.get('qr') or '').strip()
        if not qr:
            raise ValidationError({'qr': '扫码内容不能为空'})
        parts = qr.split('|')
        if len(parts) != 7:
            raise ValidationError({'qr': '菲票码格式不正确'})
        company_no, bed_no, style_no, bundle_no, color, size, qty = parts
        if company_no != self.company.company_no:
            raise ValidationError({'qr': '非本公司的菲票'})
        try:
            int(qty)
        except ValueError:
            raise ValidationError({'qr': '菲票码数量不合法'})
        ticket = (Ticket.objects.filter(company=self.company, bed_no=bed_no,
                                        bundle_no=bundle_no)
                  .select_related('production', 'production__style').first())
        if not ticket:
            raise ValidationError({'qr': '菲票不存在'})
        production = ticket.production
        from apps.records.models import Record
        # 单票登记聚合（口径唯一：stats.bundle_ticket_stats；无登记时 row=None）
        row = bundle_ticket_stats(self.company, production).get(ticket.id)
        return Response({
            'ticket': {
                'id': ticket.id,
                'production': production.id,
                'bed_no': ticket.bed_no,
                'bundle_no': ticket.bundle_no,
                'color': ticket.color,
                'size': ticket.size,
                'qty': ticket.qty,
                'qr_code': ticket.qr_code,
            },
            'style': {'id': production.style_id,
                      'style_no': production.style.style_no,
                      'name': production.style.name},
            'production': {
                'id': production.id,
                'is_locked': production.is_locked,
                'is_cut_done': production.is_cut_done,
                'process_count': _process_count(production),
                'registered_process_count': int(row['pn']) if row else 0,
                'registered_qty': int(row['qty'] or 0) if row else 0,
            },
        })


def _range_dates(range_key):
    """看板时间范围 key → (起日期, 止日期, 中文标签)。非法 key 回落本月。"""
    today = timezone.localdate()
    if range_key == 'today':
        return today, today, '今日'
    if range_key == 'yesterday':
        d = today - timedelta(days=1)
        return d, d, '昨日'
    if range_key == 'week':
        monday = today - timedelta(days=today.weekday())
        return monday, today, '本周'
    if range_key == 'last_month':
        last_day = today.replace(day=1) - timedelta(days=1)
        return last_day.replace(day=1), last_day, '上月'
    # 默认本月（含非法值回落）：1 号起至今
    return today.replace(day=1), today, '本月'


class DashboardView(CompanyContextMixin, APIView):
    """生产看板聚合（只读，「生产计划表」形态页面的数据源）。

    GET /api/dashboard?range=today|yesterday|week|month|last_month|custom（默认 month；
    custom 需另传 start_date/end_date=YYYY-MM-DD 闭区间，非法回落本月）。
    管理员与员工均可查看（CompanyContextMixin 已保证在职成员身份）；
    全部为聚合查询，不落库、不改模型。返回结构：
      range      当前时间范围 key（回显）
      summary    汇总卡（完成件数/工作中/裁货数/登记件数随 range 变；
                 在产床次/裁货总量/订单总量为存量不随 range 变）
      plans      生产计划表（行粒度 = 款号+下单日期+颜色：每个款式每个颜色一行，
                 同一 (款号,下单日期) 的颜色行连续排在一起，组内仅首行显示款号/
                 下单日期——前端做「合并单元格」观感。只含 order_date（该款最早
                 床次日期）落在所选 range 区间内的款式。每行含订单数量（该色菲票
                 合计）、床次（该色涉及床次「床号×该色菲票数」，一床混多色时该床在
                 各色行都出现）、已裁数量（该色涉及床次裁货合计）、已裁合计（款号级：
                 该款全部床次去重裁货合计）；排单/下线/出货/尾部成品暂无数据源，
                 由前端占位，不下发）
      daily      近 7 天每日登记件数（柱状图，固定近 7 天不随 range 变）
      processes  范围内登记件数 Top 工序及占比（随 range 变）
      rank       范围内登记件数 Top 员工（随 range 变）
      group_rank 范围内登记件数按组编号聚合（1组/2组…随 range 变；无组成员归 0 组）
    """

    # 支持的时间范围 key（与前端顶部 tab 一一对应；custom=自定义区间另传起止日期）
    RANGE_KEYS = ('today', 'yesterday', 'week', 'month', 'last_month', 'custom')

    def get(self, request):
        from apps.records.models import Record

        company = self.company
        today = timezone.localdate()
        week_start = today - timedelta(days=6)

        range_key = request.query_params.get('range') or 'month'
        if range_key not in self.RANGE_KEYS:
            range_key = 'month'
        start, end, _label = _range_dates(range_key)
        if range_key == 'custom':
            # 自定义区间：start_date/end_date（YYYY-MM-DD，闭区间）；缺省或非法回落本月
            try:
                cs = datetime.strptime(request.query_params.get('start_date') or '',
                                       '%Y-%m-%d').date()
                ce = datetime.strptime(request.query_params.get('end_date') or '',
                                       '%Y-%m-%d').date()
                if cs > ce:
                    cs, ce = ce, cs
                start, end = cs, ce
            except ValueError:
                start, end = _range_dates('month')[:2]

        # ---- 计划表过滤窗口（与 range tab 对应）：抽取至 plan_rows 共用 ----
        # ----（dashboard 与看板模板渲染两个入口同口径）              ----
        plan_start, plan_end = plan_window(range_key, start, end)

        # ---- 统一的时间范围记录集：下面所有按时间统计的块必须从这里派生， ----
        # ---- 保证切 tab 时每个统计块同步刷新（防「部分块不变」回归）      ----
        range_records = (Record.objects.filter(company=company)
                         .filter(registered_at__date__gte=start,
                                 registered_at__date__lte=end)
                         .order_by())  # order_by() 必须前置：Record.Meta.ordering 引用菲票字段会混入 GROUP BY

        # ---- 全量床次（含款式）：计划表布局 + 床次→款号映射（存量）----
        prods = list(Production.objects.filter(company=company)
                     .select_related('style').order_by('style_id', 'id'))
        prod_style = {p.id: p.style_id for p in prods}

        # 每床次配置工序数（订单工序优先，逐床次独立退回款式工序）：口径唯一（§2.9
        # stats.py），批量查询避免逐床次 N+1。完成/工作拆分与登记进度共用同一口径
        # （distinct 已登记工序数 ≥ 配置工序数 = 完成）。
        # 【2026-10-20 收编修复】原实现缺失回落循环把「有订单工序床次」的 pc 一并
        # 覆盖成款式工序数（厂内混入 0 工序床次时全厂 pc 被改写，与工作台口径分叉）；
        # golden 前快照 done/working=120/125，统一后 170/75，对照见
        # backend/test_stats_refactor.py 头注。
        pc_map = production_pc_map(prod_style)

        # ---- 范围内按扎聚合：完成件数 / 工作中件数（随 range 变，仅汇总卡用）----
        # 完成口径：范围内有登记的菲票中，distinct 已登记工序数 ≥ 配置工序数视为完成，
        # 否则工作中；完成 + 工作中 = 范围内已登记件数（登记件数为各工序累加口径）。
        total_done, total_working = done_working_qty(
            ticket_record_rows(company, records_qs=range_records), pc_map)

        # ---- 范围内裁货数：新建床次（Production.created_at 落在 range）的裁货合计 ----
        range_cut_total = (Production.objects.filter(
            company=company,
            created_at__date__gte=start,
            created_at__date__lte=end)
            .aggregate(s=Sum('total_qty'))['s'] or 0)

        # ---- summary：汇总卡（完成/工作/裁货/登记随 range，存量类不随 range）----
        range_qty = range_records.aggregate(s=Sum('qty'))['s'] or 0
        range_users = range_records.values('user_id').distinct().count()
        range_tickets = range_records.values('ticket_id').distinct().count()
        active_beds = (Production.objects.filter(
            company=company, is_locked=False, is_cut_done=False).count())
        cut_total = (Production.objects.filter(company=company)
                     .aggregate(s=Sum('total_qty'))['s'] or 0)
        order_total = Ticket.objects.filter(company=company) \
            .aggregate(s=Sum('qty'))['s'] or 0
        summary = {
            'done_qty': total_done,                  # 范围内完成件数
            'working_qty': total_working,            # 范围内工作中件数
            'cut_qty': int(range_cut_total),         # 范围内新建床次裁货数
            'registered_qty': int(range_qty),        # 范围内登记件数（= 完成 + 工作中）
            'user_count': range_users,
            'ticket_count': range_tickets,
            'active_beds': active_beds,
            'cut_total': int(cut_total),
            'order_total': int(order_total),
        }

        # ---- plans：生产计划表（行粒度 = 款号+下单日期+颜色；按 range 过滤下单期间）----
        # 聚合逻辑抽取至 plan_rows.build_plan_rows（与看板模板渲染入口共用，防口径分叉）
        plans = build_plan_rows(company, plan_start, plan_end, prods=prods)

        # ---- daily：近 7 天每日登记件数（柱状图固定近 7 天，缺数日补 0）----
        day_rows = (Record.objects.filter(company=company,
                                          registered_at__date__gte=week_start)
                    .order_by()  # order_by() 必须前置：Meta.ordering 会混入 GROUP BY
                    .values('registered_at__date').annotate(qty=Sum('qty')))
        qty_by_date = {row['registered_at__date']: int(row['qty'] or 0) for row in day_rows}
        weekdays = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']
        daily = []
        for i in range(7):
            d = week_start + timedelta(days=i)
            daily.append({
                'date': d.strftime('%m-%d'),
                'weekday': weekdays[d.weekday()],
                'qty': qty_by_date.get(d, 0),
            })

        # ---- processes：范围内工序占比 Top 6（随 range 变）----
        proc_rows = (range_records
                     .values('process_id', 'process__name')
                     .annotate(qty=Sum('qty')).order_by('-qty')[:6])
        processes = [{
            'name': row['process__name'],
            'qty': int(row['qty'] or 0),
            'percent': round(int(row['qty'] or 0) / range_qty * 100, 1) if range_qty else 0,
        } for row in proc_rows]

        # ---- rank：范围内登记件数 Top 员工（随 range 变）----
        rank_rows = (range_records
                     .values('user_id', 'user__real_name', 'user__phone', 'user__username')
                     .annotate(qty=Sum('qty'), ticket_count=Count('ticket_id', distinct=True))
                     .order_by('-qty')[:10])
        rank = [{
            'user_id': row['user_id'],
            'name': row['user__real_name'] or row['user__phone'] or row['user__username'],
            'qty': int(row['qty'] or 0),
            'ticket_count': row['ticket_count'],
        } for row in rank_rows]

        # ---- 组排行：范围内登记件数按员工组编号聚合（1组/2组…，qty 降序）----
        user_ids = set(range_records.values_list('user_id', flat=True))
        user_group = dict(UserCompany.objects.filter(
            company=company, user_id__in=user_ids, is_deleted=False)
            .values_list('user_id', 'group_no'))
        group_acc = {}  # group_no -> [qty, users, tickets]
        for row in (range_records.values('user_id')
                    .annotate(qty=Sum('qty'), ticket_count=Count('ticket_id', distinct=True))):
            gno = user_group.get(row['user_id']) or 0  # 无组成员归 0（展示「未分组」）
            acc = group_acc.setdefault(gno, [0, 0, 0])
            acc[0] += int(row['qty'] or 0)
            acc[1] += 1
            acc[2] += row['ticket_count']
        group_rank = [{
            'group_no': gno,
            'qty': acc[0],
            'user_count': acc[1],
            'ticket_count': acc[2],
        } for gno, acc in sorted(group_acc.items(), key=lambda kv: -kv[1][0])]

        return Response({
            'range': range_key,
            'summary': summary,
            'plans': plans,
            'daily': daily,
            'processes': processes,
            'rank': rank,
            'group_rank': group_rank,
        })


class DashboardDimView(CompanyContextMixin, APIView):
    """看板维度查看（2026-10-23 用户需求）：按订单/裁床/部门(岗位)/组/人 聚合登记数据。

    GET /api/dashboard/dim?dim=order|bed|post|group|user&range=&start_date=&end_date=
    range 解析与 DashboardView 完全同口径（registered_at 落区间）；dim=style 的
    「按款式」视图走既有模板渲染接口（/api/kanban/render），不经过这里。
    返回扁平表：{dim, columns:[{key,title,width}], rows:[{key, nav, cells:[...]}]}，
    小程序看板「计划表」维度切换后直接渲染；nav 供行下钻（order→订单详情、
    bed→床次详情），其余维度不可点。
    金额 = Σ qty×price（计件工资口径，与薪酬 unsettled 一致，未结算不剔除）。
    """

    DIMS = ('order', 'bed', 'post', 'group', 'user')

    def get(self, request):
        from decimal import Decimal
        from apps.records.models import Record

        company = self.company
        dim = request.query_params.get('dim') or 'order'
        if dim not in self.DIMS:
            return Response(
                {'detail': f'dim 参数不合法，可选：{"、".join(self.DIMS)}'}, status=400)

        # ---- range 解析（照搬 DashboardView：非法回落本月，custom 收起止日）----
        range_key = request.query_params.get('range') or 'month'
        if range_key not in DashboardView.RANGE_KEYS:
            range_key = 'month'
        start, end, _label = _range_dates(range_key)
        if range_key == 'custom':
            try:
                cs = datetime.strptime(request.query_params.get('start_date') or '',
                                       '%Y-%m-%d').date()
                ce = datetime.strptime(request.query_params.get('end_date') or '',
                                       '%Y-%m-%d').date()
                if cs > ce:
                    cs, ce = ce, cs
                start, end = cs, ce
            except ValueError:
                start, end = _range_dates('month')[:2]

        records = (Record.objects.filter(company=company)
                   .filter(registered_at__date__gte=start,
                           registered_at__date__lte=end)
                   .order_by())  # order_by() 必须前置：Meta.ordering 会混入 GROUP BY

        def amount():
            # 聚合实例不可跨 QuerySet 复用（Django 会缓存解析状态），每次现建
            return Sum(F('qty') * F('price'), output_field=DecimalField())

        def money(v):
            return str((v or Decimal('0')).quantize(Decimal('0.01')))

        def user_name(row):
            return (row.get('user__real_name') or row.get('user__phone')
                    or row.get('user__username') or f"#{row['user_id']}")

        columns, rows = [], []

        if dim == 'order':
            # ---- 按订单：范围内登记（经 菲票→床次→订单 链路）按订单聚合 ----
            qs = (records.filter(ticket__production__order__isnull=False)
                  .values('ticket__production__order_id',
                          'ticket__production__order__order_no',
                          'ticket__production__order__customer__name',
                          'ticket__production__style__style_no')
                  .annotate(amount=amount(), qty=Sum('qty'),
                            tickets=Count('ticket_id', distinct=True),
                            users=Count('user', distinct=True))
                  .order_by('-qty'))
            columns = [
                {'key': 'order_no', 'title': '订单号', 'width': 190},
                {'key': 'style_no', 'title': '款号', 'width': 150},
                {'key': 'customer', 'title': '客户', 'width': 150},
                {'key': 'tickets', 'title': '菲票数', 'width': 100},
                {'key': 'qty', 'title': '登记件数', 'width': 120},
                {'key': 'users', 'title': '人数', 'width': 90},
                {'key': 'amount', 'title': '金额', 'width': 110},
            ]
            rows = [{
                'key': str(row['ticket__production__order_id']),
                'nav': 'order',
                'cells': [
                    row['ticket__production__order__order_no'] or '',
                    row['ticket__production__style__style_no'] or '',
                    row['ticket__production__order__customer__name'] or '未指定客户',
                    str(row['tickets']),
                    str(int(row['qty'] or 0)),
                    str(row['users']),
                    money(row['amount']),
                ],
            } for row in qs]

        elif dim == 'bed':
            # ---- 按裁床：范围内有登记的床次聚合，状态=裁完/锁定/生产中 ----
            qs = (records
                  .values('ticket__production_id', 'ticket__production__bed_no',
                          'ticket__production__style__style_no',
                          'ticket__production__plan_qty',
                          'ticket__production__is_cut_done',
                          'ticket__production__is_locked')
                  .annotate(amount=amount(), qty=Sum('qty'),
                            tickets=Count('ticket_id', distinct=True),
                            users=Count('user', distinct=True))
                  .order_by('-qty'))
            columns = [
                {'key': 'bed_no', 'title': '床号', 'width': 130},
                {'key': 'style_no', 'title': '款号', 'width': 150},
                {'key': 'plan_qty', 'title': '计划裁货', 'width': 110},
                {'key': 'tickets', 'title': '菲票数', 'width': 100},
                {'key': 'qty', 'title': '登记件数', 'width': 120},
                {'key': 'users', 'title': '人数', 'width': 90},
                {'key': 'amount', 'title': '金额', 'width': 110},
                {'key': 'state', 'title': '状态', 'width': 100},
            ]
            rows = [{
                'key': str(row['ticket__production_id']),
                'nav': 'bed',
                'cells': [
                    row['ticket__production__bed_no'] or '',
                    row['ticket__production__style__style_no'] or '',
                    str(int(row['ticket__production__plan_qty'] or 0)),
                    str(row['tickets']),
                    str(int(row['qty'] or 0)),
                    str(row['users']),
                    money(row['amount']),
                    ('已裁完' if row['ticket__production__is_cut_done']
                     else ('已锁定' if row['ticket__production__is_locked'] else '生产中')),
                ],
            } for row in qs]

        else:
            # ---- 部门(岗位) / 组 / 人：先按人聚合，再按 UserCompany 归维 ----
            per_user = list(records
                            .values('user_id', 'user__real_name', 'user__phone',
                                    'user__username')
                            .annotate(amount=amount(),
                                      qty=Sum('qty'),
                                      tickets=Count('ticket_id', distinct=True)))
            uc_map = {uc.user_id: uc for uc in UserCompany.objects.filter(
                company=company,
                user_id__in=[r['user_id'] for r in per_user],
                is_deleted=False).select_related('post')}

            if dim == 'post':
                # 部门维度 = 岗位（系统暂无独立部门表，按岗位归组；未设岗位归「未设岗位」）
                acc = {}  # post_name -> [qty, users, tickets, amount]
                for r in per_user:
                    uc = uc_map.get(r['user_id'])
                    name = (uc.post.name if uc and uc.post_id else '') or '未设岗位'
                    a = acc.setdefault(name, [0, 0, 0, Decimal('0')])
                    a[0] += int(r['qty'] or 0)
                    a[1] += 1
                    a[2] += r['tickets']
                    a[3] += (r['amount'] or Decimal('0'))
                columns = [
                    {'key': 'post', 'title': '部门（岗位）', 'width': 170},
                    {'key': 'users', 'title': '人数', 'width': 90},
                    {'key': 'tickets', 'title': '菲票数', 'width': 100},
                    {'key': 'qty', 'title': '登记件数', 'width': 120},
                    {'key': 'amount', 'title': '金额', 'width': 110},
                ]
                rows = [{
                    'key': name, 'nav': '',
                    'cells': [name, str(a[1]), str(a[2]), str(a[0]), money(a[3])]
                } for name, a in sorted(acc.items(), key=lambda kv: -kv[1][0])]

            elif dim == 'group':
                # 组维度：group_no（1组/2组…），无组成员归 0=未分组（与 group_rank 同口径）
                acc = {}  # group_no -> [qty, users, tickets, amount]
                for r in per_user:
                    uc = uc_map.get(r['user_id'])
                    gno = (uc.group_no if uc else None) or 0
                    a = acc.setdefault(gno, [0, 0, 0, Decimal('0')])
                    a[0] += int(r['qty'] or 0)
                    a[1] += 1
                    a[2] += r['tickets']
                    a[3] += (r['amount'] or Decimal('0'))
                columns = [
                    {'key': 'group', 'title': '组', 'width': 120},
                    {'key': 'users', 'title': '人数', 'width': 90},
                    {'key': 'tickets', 'title': '菲票数', 'width': 100},
                    {'key': 'qty', 'title': '登记件数', 'width': 120},
                    {'key': 'amount', 'title': '金额', 'width': 110},
                ]
                rows = [{
                    'key': str(gno), 'nav': '',
                    'cells': [('未分组' if gno == 0 else f'{gno}组'),
                              str(a[1]), str(a[2]), str(a[0]), money(a[3])]
                } for gno, a in sorted(acc.items(), key=lambda kv: -kv[1][0])]

            else:  # user
                columns = [
                    {'key': 'name', 'title': '员工', 'width': 130},
                    {'key': 'post', 'title': '岗位', 'width': 130},
                    {'key': 'group', 'title': '组', 'width': 90},
                    {'key': 'tickets', 'title': '菲票数', 'width': 100},
                    {'key': 'qty', 'title': '登记件数', 'width': 120},
                    {'key': 'amount', 'title': '金额', 'width': 110},
                ]
                ranked = sorted(per_user, key=lambda r: -(int(r['qty'] or 0)))

                def post_of(uid):
                    uc = uc_map.get(uid)
                    return (uc.post.name if uc and uc.post_id else '') or '未设岗位'

                def group_of(uid):
                    uc = uc_map.get(uid)
                    return (f'{uc.group_no}组' if uc and uc.group_no else '未分组')

                rows = [{
                    'key': str(r['user_id']), 'nav': '',
                    'cells': [
                        user_name(r),
                        post_of(r['user_id']),
                        group_of(r['user_id']),
                        str(r['tickets']),
                        str(int(r['qty'] or 0)),
                        money(r['amount']),
                    ],
                } for r in ranked]

        return Response({'dim': dim, 'range': range_key,
                         'columns': columns, 'rows': rows})
