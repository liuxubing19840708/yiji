"""基础档案接口：款式（含复制/同步工序）、工序（含选择器）、客户、菲票查询与编菲。"""
from django.db import IntegrityError, transaction
from django.db.models import Count, F, Q, Sum
from django.http import Http404
from django.utils import timezone
from rest_framework import generics, status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.catalog.models import (
    Customer,
    CustomerGroup,
    Process,
    ProcessTemplate,
    Style,
    StyleProcess,
    Ticket,
    TicketPrintLog,
)
from apps.catalog.serializers import (
    CustomerGroupSerializer,
    CustomerSerializer,
    ProcessSerializer,
    ProcessTemplateSerializer,
    StyleSerializer,
    TicketSerializer,
)
from apps.core.mixins import CompanyContextMixin
from apps.core.pagination import DefaultPagination
from apps.core.utils import build_ticket_qr, q3
from apps.ops.models import AuditLog, CompanySetting
from apps.ops.services import audit


def _purge_deleted(model, **filters):
    """硬删除同键软删除行：软删除行仍占用数据库唯一约束，须先清掉才能重建。"""
    model.all_objects.filter(is_deleted=True, **filters).delete()


def save_style_processes(company, request_data, style):
    """保存款式工序配置（§五）：整表重建 + 自动汇总款式总单价。"""
    items = request_data.get('process_items')
    if items is None:
        return
    if not isinstance(items, list):
        raise ValidationError({'process_items': '工序配置须为列表'})
    prepared, seen = [], set()
    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            raise ValidationError({'process_items': f'第{idx + 1}项格式不正确'})
        try:
            process_id = int(item.get('process_id'))
        except (TypeError, ValueError):
            raise ValidationError({'process_items': f'第{idx + 1}项缺少有效工序'})
        if process_id in seen:
            raise ValidationError({'process_items': '工序配置中存在重复工序'})
        seen.add(process_id)
        try:
            process = Process.objects.get(company=company, pk=process_id)
        except Process.DoesNotExist:
            raise ValidationError({'process_items': f'工序 {process_id} 不存在'})
        try:
            price = q3(item['price']) if item.get('price') is not None else process.default_price
        except Exception:
            raise ValidationError({'process_items': f'第{idx + 1}项单价不合法'})
        try:
            sort = int(item.get('sort', 0) or 0)
        except (TypeError, ValueError):
            sort = 0
        # allow_skip（2026-10-18 拍板）：缺省 True 兼容旧入参（全部允许跳序）
        allow_skip = bool(item.get('allow_skip', True))
        prepared.append(StyleProcess(style=style, process=process, price=price, sort=sort,
                                     allow_skip=allow_skip))
    with transaction.atomic():
        # 配置行整表重建：含软删除行硬删除，避免唯一约束冲突
        StyleProcess.all_objects.filter(style=style).delete()
        StyleProcess.objects.bulk_create(prepared)
    style.recalc_price()


def attach_style_stats(company, items):
    """给款式列表项补统计行（§五）：裁货数/菲票数/工序数/已完成/未完成。

    口径唯一（§2.9 stats.py）：四项聚合收编至 stats.style_ticket_stats
    （款式工序数分母口径不变，逐位等价搬迁）。
    """
    style_ids = [item['id'] for item in items if item.get('id')]
    if not style_ids:
        return
    from apps.production.stats import style_ticket_stats
    cut_map, ticket_map, pc_map, done_per_style = style_ticket_stats(company, style_ids)
    for item in items:
        sid = item['id']
        cnt = ticket_map.get(sid, 0)
        done = done_per_style.get(sid, 0)
        item['cut_total'] = int(cut_map.get(sid, 0) or 0)
        item['ticket_count'] = cnt
        item['process_count'] = pc_map.get(sid, 0)
        item['done_ticket_count'] = done
        item['unfinished_ticket_count'] = cnt - done


class StyleListCreateView(CompanyContextMixin, generics.ListCreateAPIView):
    """GET /styles 列表（keyword/customer_id 筛选 + 统计行）；POST 新增款式+工序配置。"""
    serializer_class = StyleSerializer
    pagination_class = DefaultPagination

    def get_queryset(self):
        queryset = Style.objects.filter(company=self.company)
        keyword = self.request.query_params.get('keyword', '').strip()
        if keyword:
            queryset = queryset.filter(
                Q(style_no__icontains=keyword) | Q(name__icontains=keyword))
        customer_id = self.request.query_params.get('customer_id')
        if customer_id:
            queryset = queryset.filter(customer_id=customer_id)
        return queryset

    def list(self, request, *args, **kwargs):
        response = super().list(request, *args, **kwargs)
        attach_style_stats(self.company, response.data.get('results', response.data))
        return response

    def perform_create(self, serializer):
        self.check_perm('styles.manage')
        style_no = serializer.validated_data.get('style_no')
        _purge_deleted(Style, company=self.company, style_no=style_no)
        if Style.objects.filter(company=self.company, style_no=style_no).exists():
            raise ValidationError({'style_no': '款号已存在'})
        with transaction.atomic():
            # process_items 是 write_only 输入字段，不能透传给 Style.objects.create()
            serializer.validated_data.pop('process_items', None)
            style = serializer.save(company=self.company)
            save_style_processes(self.company, self.request.data, style)


class StyleDetailView(CompanyContextMixin, generics.RetrieveUpdateDestroyAPIView):
    """GET/PUT/DELETE /styles/<pk>（删除前校验无菲票）。"""
    serializer_class = StyleSerializer
    pagination_class = DefaultPagination

    def get_queryset(self):
        return Style.objects.filter(company=self.company)

    def update(self, request, *args, **kwargs):
        # 工序单价页契约：仅传 process_items 时按部分更新处理（其余字段沿用原值）。
        # PUT 必须可用（wx.request 无 PATCH），否则 DRF 全量校验会把只配价钱的合法
        # 请求 400 掉（style_no/name 必填）。
        # 2026-10-21：工艺说明/图片/视频（craft_note/craft_images/video）单字段
        # 编辑页同样按部分更新处理，避免全量校验 400。
        if not ({'style_no', 'name'} & set(request.data.keys())):
            if {'process_items', 'craft_note', 'craft_images', 'video'} & set(
                    request.data.keys()):
                kwargs['partial'] = True
        return super().update(request, *args, **kwargs)

    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        data = self.get_serializer(instance).data
        attach_style_stats(self.company, [data])
        return Response(data)

    def perform_update(self, serializer):
        # Plan A：带 process_items 的保存=工序上价/改价，走独立权限点（旧 'styles' 仍覆盖）
        if 'process_items' in self.request.data:
            self.check_perm('styles.price')
        self.check_perm('styles.manage')
        new_no = serializer.validated_data.get('style_no')
        if new_no and new_no != serializer.instance.style_no:
            _purge_deleted(Style, company=self.company, style_no=new_no)
            if Style.objects.filter(company=self.company, style_no=new_no).exclude(
                    pk=serializer.instance.pk).exists():
                raise ValidationError({'style_no': '款号已存在'})
        with transaction.atomic():
            style = serializer.save()
            if 'process_items' in self.request.data:
                save_style_processes(self.company, self.request.data, style)

    def perform_destroy(self, instance):
        # Plan A：删除款式为高危点（不获旧 'styles' 覆盖，需显式授权）
        self.check_perm('styles.delete')
        if Ticket.objects.filter(company=self.company, production__style=instance).exists():
            raise ValidationError('该款式已有菲票，无法删除')
        instance.delete()


class StyleCopyView(CompanyContextMixin, APIView):
    """POST /styles/<pk>/copy 深拷贝款式（§五）：新款号 = 原款号-1/-2… 冲突递增，复制工序配置。"""

    def post(self, request, pk):
        self.check_perm('styles.manage')
        try:
            style = Style.objects.get(company=self.company, pk=pk)
        except Style.DoesNotExist:
            raise Http404('款式不存在')
        suffix = 1
        while Style.objects.filter(
                company=self.company, style_no=f'{style.style_no}-{suffix}').exists():
            suffix += 1
        with transaction.atomic():
            new_style = Style.objects.create(
                company=self.company,
                style_no=f'{style.style_no}-{suffix}',
                name=f'{style.name}(副本)',
                price=style.price,
                customer=style.customer,
                image=style.image,
                remark=style.remark)
            StyleProcess.objects.bulk_create([
                StyleProcess(style=new_style, process=sp.process, price=sp.price, sort=sp.sort)
                for sp in style.processes.all()])
        return Response(StyleSerializer(new_style).data, status=status.HTTP_201_CREATED)


class StyleSyncProcessView(CompanyContextMixin, APIView):
    """POST /styles/<pk>/sync-process 同步工序配置到存量菲票（§五）。

    扫菲登记实时读取款式工序配置，本身即为最新；此接口用于涉及
    已有登记记录的菲票时二次确认并写操作日志。
    Plan A 二期：原为全公司无门槛写端点（写操作日志），补 styles.manage 关卡；
    旧 coarse 'styles' 岗位经 legacy 覆盖天然通过，零回归。
    """

    def post(self, request, pk):
        self.check_perm('styles.manage')
        try:
            style = Style.objects.get(company=self.company, pk=pk)
        except Style.DoesNotExist:
            raise Http404('款式不存在')
        from apps.records.models import Record
        ticket_count = (Record.objects.filter(
            company=self.company, ticket__production__style=style)
            .values('ticket_id').distinct().count())
        if not request.data.get('confirm') and ticket_count:
            return Response({'need_confirm': True, 'ticket_count': ticket_count})
        audit(self.company, AuditLog.Type.PROCESS, operator=request.user,
              style_no=style.style_no,
              message=f'{request.user.real_name} 同步款式 {style.style_no} 工序配置'
                      f'（涉及 {ticket_count} 张已登记菲票）')
        return Response({'synced': True, 'ticket_count': ticket_count})


def _validate_template_processes(company, items):
    """模板/订单工序归属校验：process 必须本公司存在（防跨厂挂他人工序）。"""
    if not items:
        return
    process_ids = []
    for item in items:
        try:
            process_ids.append(int(item.get('process_id')))
        except (TypeError, ValueError, AttributeError):
            raise ValidationError({'items': '工序配置缺少有效工序'})
    found = set(Process.objects.filter(
        company=company, pk__in=process_ids).values_list('id', flat=True))
    missing = [pid for pid in process_ids if pid not in found]
    if missing:
        raise ValidationError({'items': f'工序 {missing[0]} 不存在'})


class ProcessTemplateListCreateView(CompanyContextMixin, generics.ListCreateAPIView):
    """GET /process-templates 工序模板列表（keyword 筛选）；POST 新增模板（§工序模板）。

    items=[{process_id, price}]：process 必须本公司存在、price≥0（序列化器校验）；
    name 公司内唯一（软删防撞，400「模板名称已存在」）。
    """

    serializer_class = ProcessTemplateSerializer
    pagination_class = DefaultPagination

    def get_queryset(self):
        queryset = ProcessTemplate.objects.filter(company=self.company)
        keyword = self.request.query_params.get('keyword', '').strip()
        if keyword:
            queryset = queryset.filter(name__icontains=keyword)
        return queryset

    def perform_create(self, serializer):
        self.check_perm('styles.manage')
        _validate_template_processes(self.company, serializer.validated_data.get('items'))
        name = (serializer.validated_data.get('name') or '').strip()
        _purge_deleted(ProcessTemplate, company=self.company, name=name)
        if ProcessTemplate.objects.filter(company=self.company, name=name).exists():
            raise ValidationError({'name': '模板名称已存在'})
        serializer.save(company=self.company)


class ProcessTemplateDetailView(CompanyContextMixin, generics.RetrieveUpdateDestroyAPIView):
    """GET/PUT/DELETE /process-templates/<pk>。删除为软删，重建同名的软删行会被物理清理。"""

    serializer_class = ProcessTemplateSerializer
    pagination_class = DefaultPagination

    def get_queryset(self):
        return ProcessTemplate.objects.filter(company=self.company)

    def perform_update(self, serializer):
        self.check_perm('styles.manage')
        _validate_template_processes(self.company, serializer.validated_data.get('items'))
        name = (serializer.validated_data.get('name') or '').strip()
        if name and name != serializer.instance.name:
            _purge_deleted(ProcessTemplate, company=self.company, name=name)
            if ProcessTemplate.objects.filter(company=self.company, name=name).exclude(
                    pk=serializer.instance.pk).exists():
                raise ValidationError({'name': '模板名称已存在'})
        serializer.save()

    def perform_destroy(self, instance):
        self.check_perm('styles.manage')
        instance.delete()


class ProcessListCreateView(CompanyContextMixin, generics.ListCreateAPIView):
    """GET /processes 列表（keyword/is_fixed 筛选）；POST 新增工序。"""
    serializer_class = ProcessSerializer
    pagination_class = DefaultPagination

    def get_queryset(self):
        queryset = Process.objects.filter(company=self.company)
        keyword = self.request.query_params.get('keyword', '').strip()
        if keyword:
            queryset = queryset.filter(name__icontains=keyword)
        is_fixed = self.request.query_params.get('is_fixed')
        if is_fixed in ('0', '1'):
            queryset = queryset.filter(is_fixed=(is_fixed == '1'))
        return queryset

    def perform_create(self, serializer):
        self.check_perm('styles.manage')
        name = serializer.validated_data.get('name')
        _purge_deleted(Process, company=self.company, name=name)
        if Process.objects.filter(company=self.company, name=name).exists():
            raise ValidationError({'name': '工序名称已存在'})
        serializer.save(company=self.company)


class ProcessDetailView(CompanyContextMixin, generics.RetrieveUpdateDestroyAPIView):
    """GET/PUT/DELETE /processes/<pk>（删除前校验未被款式使用、无登记记录）。"""
    serializer_class = ProcessSerializer
    pagination_class = DefaultPagination

    def get_queryset(self):
        return Process.objects.filter(company=self.company)

    def perform_update(self, serializer):
        self.check_perm('styles.manage')
        name = serializer.validated_data.get('name')
        if name and name != serializer.instance.name:
            _purge_deleted(Process, company=self.company, name=name)
            if Process.objects.filter(company=self.company, name=name).exclude(
                    pk=serializer.instance.pk).exists():
                raise ValidationError({'name': '工序名称已存在'})
        serializer.save()

    def perform_destroy(self, instance):
        # Plan A：删除工序同属高危点（与删除款式一致）
        self.check_perm('styles.delete')
        if StyleProcess.objects.filter(process=instance).exists():
            raise ValidationError('该工序已被款式使用，无法删除')
        from apps.records.models import Record
        if Record.objects.filter(company=self.company, process=instance).exists():
            raise ValidationError('该工序已有登记记录，无法删除')
        instance.delete()


class ProcessSelectorView(CompanyContextMixin, APIView):
    """GET /process-selector 扫菲工序选择器（§九）：固定工序 ∪ 本人指定工序，单选免选。"""

    def get(self, request):
        queryset = Process.objects.filter(company=self.company).filter(
            Q(is_fixed=True) | Q(pk__in=self._assigned_ids())
        ).order_by('sort', 'id')
        return Response(ProcessSerializer(queryset, many=True).data)

    def _assigned_ids(self):
        ids = []
        for raw in (self.user_company.assigned_processes or []):
            try:
                ids.append(int(raw))
            except (TypeError, ValueError):
                continue
        return ids


class CustomerGroupListCreateView(CompanyContextMixin, generics.ListCreateAPIView):
    """GET /customer-groups 列表（含客户数）；POST 新增分组。"""
    serializer_class = CustomerGroupSerializer
    pagination_class = DefaultPagination

    def get_queryset(self):
        return (CustomerGroup.objects.filter(company=self.company)
                .annotate(customer_count=Count('customers')))

    def perform_create(self, serializer):
        self.check_perm('customers.manage')
        name = serializer.validated_data.get('name')
        _purge_deleted(CustomerGroup, company=self.company, name=name)
        if CustomerGroup.objects.filter(company=self.company, name=name).exists():
            raise ValidationError({'name': '分组名称已存在'})
        serializer.save(company=self.company)


class CustomerGroupDetailView(CompanyContextMixin, generics.RetrieveUpdateDestroyAPIView):
    """GET/PUT/DELETE /customer-groups/<pk>。"""
    serializer_class = CustomerGroupSerializer
    pagination_class = DefaultPagination

    def get_queryset(self):
        return (CustomerGroup.objects.filter(company=self.company)
                .annotate(customer_count=Count('customers')))

    def perform_update(self, serializer):
        self.check_perm('customers.manage')
        name = serializer.validated_data.get('name')
        if name and name != serializer.instance.name:
            _purge_deleted(CustomerGroup, company=self.company, name=name)
            if CustomerGroup.objects.filter(company=self.company, name=name).exclude(
                    pk=serializer.instance.pk).exists():
                raise ValidationError({'name': '分组名称已存在'})
        serializer.save()

    def perform_destroy(self, instance):
        self.check_perm('customers.manage')
        instance.delete()  # 客户分组置空由 customer.group SET_NULL 处理


class CustomerListCreateView(CompanyContextMixin, generics.ListCreateAPIView):
    """GET /customers 列表（keyword/group 筛选）；POST 新增客户。"""
    serializer_class = CustomerSerializer
    pagination_class = DefaultPagination

    def get_queryset(self):
        queryset = Customer.objects.filter(company=self.company)
        keyword = self.request.query_params.get('keyword', '').strip()
        if keyword:
            queryset = queryset.filter(
                Q(customer_no__icontains=keyword) | Q(name__icontains=keyword))
        group = self.request.query_params.get('group')
        if group:
            queryset = queryset.filter(group_id=group)
        return queryset

    def perform_create(self, serializer):
        self.check_perm('customers.manage')
        # 客户编号留空自动生成（KH0001 顺延）：编号手编繁琐且易重，前端留空即自动取号
        customer_no = (serializer.validated_data.get('customer_no') or '').strip()
        if not customer_no:
            customer_no = self._next_customer_no()
        _purge_deleted(Customer, company=self.company, customer_no=customer_no)
        if Customer.objects.filter(company=self.company, customer_no=customer_no).exists():
            raise ValidationError({'customer_no': '客户编号已存在'})
        serializer.save(company=self.company, customer_no=customer_no)

    def _next_customer_no(self) -> str:
        """KH + 4 位顺延：取现有最大号 +1，撞号（并发/脏数据）就继续找空位。"""
        import re
        nums = [int(m.group(1)) for m in (
            re.match(r'^KH(\d{1,6})$', c) for c in
            Customer.all_objects.filter(company=self.company)
            .values_list('customer_no', flat=True)) if m]
        n = (max(nums) + 1) if nums else 1
        while Customer.all_objects.filter(
                company=self.company, customer_no=f'KH{n:04d}').exists():
            n += 1
        return f'KH{n:04d}'


class CustomerDetailView(CompanyContextMixin, generics.RetrieveUpdateDestroyAPIView):
    """GET/PUT/DELETE /customers/<pk>。"""
    serializer_class = CustomerSerializer
    pagination_class = DefaultPagination

    def get_queryset(self):
        return Customer.objects.filter(company=self.company)

    def perform_update(self, serializer):
        self.check_perm('customers.manage')
        customer_no = serializer.validated_data.get('customer_no')
        if customer_no and customer_no != serializer.instance.customer_no:
            _purge_deleted(Customer, company=self.company, customer_no=customer_no)
            if Customer.objects.filter(company=self.company, customer_no=customer_no).exclude(
                    pk=serializer.instance.pk).exists():
                raise ValidationError({'customer_no': '客户编号已存在'})
        serializer.save()

    def perform_destroy(self, instance):
        self.check_perm('customers.manage')
        instance.delete()


class TicketListView(CompanyContextMixin, generics.ListAPIView):
    """GET /tickets 菲票查询（production_id/bed_no/bundle_no/is_printed 筛选）。

    ordering=print_at：按最后打印时间倒序（打印历史视图用，白名单防注入）。
    """
    serializer_class = TicketSerializer
    pagination_class = DefaultPagination

    def get_queryset(self):
        queryset = Ticket.objects.filter(company=self.company).select_related(
            'production', 'production__style')
        params = self.request.query_params
        for key in ('production_id', 'bed_no', 'bundle_no'):
            value = params.get(key)
            if value:
                queryset = queryset.filter(**{key: value})
        is_printed = params.get('is_printed')
        if is_printed in ('0', '1'):
            queryset = queryset.filter(is_printed=(is_printed == '1'))
        if params.get('ordering') == 'print_at':
            queryset = queryset.order_by('-print_at', '-id')
        return queryset


class TicketBatchView(CompanyContextMixin, APIView):
    """POST /tickets/batch 编菲（§六）：按矩阵项展开生成菲票。

    items: [{color, size, qty, fabric?, piece_no?}]，qty 为该颜色尺码总数量；
    bundle_limit（每扎上限）时按上限拆成整扎+尾扎；扎号从 start_bundle_no 或
    该床次最大扎号+1 起递增，冲突自动顺延；二维码内容入 qr_code，供打印队列使用。
    """

    def post(self, request):
        self.check_perm('production.manage')
        production = self._get_production()
        # 2026-10-18 拍板：裁完=裁剪完成标记，不再拦截编菲加扎（旧拦截已删）；
        # is_cut_done 仅作为状态供工作台/看板区分「进行中」。
        # M2 二维码注入防护：床次与颜色/尺码会拼进二维码（| 分段），入库前校验。
        self._check_qr_field(str(production.bed_no), '床次')
        items = request.data.get('items')
        if not isinstance(items, list) or not items:
            raise ValidationError({'items': '请至少提交一项编菲数据'})
        bundle_limit = request.data.get('bundle_limit')
        if bundle_limit is not None:
            try:
                bundle_limit = int(bundle_limit)
            except (TypeError, ValueError):
                bundle_limit = 0
            if bundle_limit <= 0:
                raise ValidationError({'bundle_limit': '每扎上限须为正整数'})
        start_bundle_no = request.data.get('start_bundle_no')
        if start_bundle_no not in (None, ''):
            if not str(start_bundle_no).isdigit():
                raise ValidationError({'start_bundle_no': '起始扎号须为数字'})
            next_no = int(start_bundle_no)
        else:
            next_no = self._next_bundle_no(production.bed_no)

        prepared = []
        for item in items:
            if not isinstance(item, dict):
                raise ValidationError({'items': '编菲项格式不正确'})
            try:
                qty = int(item.get('qty'))
            except (TypeError, ValueError):
                raise ValidationError({'items': '数量须为正整数'})
            if qty <= 0:
                raise ValidationError({'items': '数量须为正整数'})
            color = str(item.get('color') or '')[:64]
            size = str(item.get('size') or '')[:32]
            self._check_qr_field(color, '颜色')
            self._check_qr_field(size, '尺码')
            fabric = str(item.get('fabric') or '')[:64] or production.fabric
            piece_no = str(item.get('piece_no') or '')[:32]
            chunks = [qty]
            if bundle_limit and qty > bundle_limit:
                chunks = [bundle_limit] * (qty // bundle_limit)
                if qty % bundle_limit:
                    chunks.append(qty % bundle_limit)
            for chunk in chunks:
                bundle_no = self._alloc_bundle_no(production.bed_no, next_no)
                next_no = int(bundle_no) + 1
                prepared.append(Ticket(
                    company=self.company, production=production,
                    bed_no=production.bed_no, bundle_no=bundle_no,
                    color=color, size=size, qty=chunk,
                    fabric=fabric, piece_no=piece_no,
                    qr_code=build_ticket_qr(self.company.company_no, production.bed_no,
                                            production.style.style_no, bundle_no,
                                            color, size, chunk)))
        try:
            created = self._create_tickets(production, prepared)
        except IntegrityError:
            # L4 并发兜底：另一请求同时占号撞 (床次,扎号) 唯一约束 →
            # 全量顺延扎号重试一次（沿用冲突自动顺延逻辑），仍冲突则抛出。
            next_no = self._next_bundle_no(production.bed_no)
            for t in prepared:
                bundle_no = self._alloc_bundle_no(production.bed_no, next_no)
                next_no = int(bundle_no) + 1
                t.bundle_no = bundle_no
                t.qr_code = build_ticket_qr(self.company.company_no, production.bed_no,
                                            production.style.style_no, bundle_no,
                                            t.color, t.size, t.qty)
            created = self._create_tickets(production, prepared)
        return Response(TicketSerializer(created, many=True).data,
                        status=status.HTTP_201_CREATED)

    def _check_qr_field(self, value, label):
        """M2 二维码注入防护：字段值会以 | 分段拼入菲票二维码，须保证扫码可解析。

        - 含 '|' 直接拒绝（破坏分段）；
        - GBK 编码后含 0x7C（'|' 的字节值）同样拒绝——某些汉字的 GBK 第二字节恰为
          0x7C，扫码枪按字节切段会错位；非 GBK 字符集也拒绝。
        """
        if '|' in value:
            raise ValidationError({label: f'{label}不能包含 | 字符'})
        try:
            if b'\x7c' in value.encode('gbk'):
                raise ValidationError({label: '含扫码无法解析的字符，请更换表述'})
        except UnicodeEncodeError:
            raise ValidationError({label: '含扫码无法解析的字符，请更换表述'})

    def _create_tickets(self, production, prepared):
        """批量入库 + 重算总数 + 「默认裁完」置位（编菲主链路，重试时复用）。"""
        with transaction.atomic():
            created = Ticket.objects.bulk_create(prepared)
            self._recalc_total(production)
            # 「默认裁完」auto_cut_done（2026-10-18 新语义）：编菲成功后自动置裁完。
            # 系统无裁剪类工序标识，无法按「菲票完成裁剪工序登记」自动判定，
            # 故按用户兜底方案落在编菲完成时点；裁完仅是状态（不再拦截加扎/登记），
            # 人工可随时在床次详情取消/重标。
            if CompanySetting.get_bool(self.company, 'auto_cut_done') \
                    and not production.is_cut_done:
                production.is_cut_done = True
                production.save(update_fields=['is_cut_done', 'updated_at'])
                audit(self.company, AuditLog.Type.OTHER, operator=self.request.user,
                      bed_no=production.bed_no,
                      style_no=production.style.style_no,
                      message=(f'默认裁完：编菲后自动置裁完'
                               f'【床次{production.bed_no}】'))
        return created

    def _get_production(self):
        from apps.production.models import Production
        pk = self.request.data.get('production_id') or self.request.query_params.get('production_id')
        try:
            return Production.objects.get(company=self.company, pk=pk)
        except (Production.DoesNotExist, ValueError, TypeError):
            raise Http404('床次订单不存在')

    def _next_bundle_no(self, bed_no):
        """该床次当前最大数字扎号 + 1；无菲票则从 1 起。"""
        nums = [int(b) for b in Ticket.all_objects.filter(
            company=self.company, bed_no=bed_no).values_list('bundle_no', flat=True)
            if b.isdigit()]
        return max(nums) + 1 if nums else 1

    def _alloc_bundle_no(self, bed_no, candidate):
        """扎号冲突自动顺延（§六）：唯一约束含软删除行，用 all_objects 校验。"""
        while Ticket.all_objects.filter(
                company=self.company, bed_no=bed_no, bundle_no=str(candidate)).exists():
            candidate += 1
        return str(candidate)

    def _recalc_total(self, production):
        production.total_qty = (Ticket.objects.filter(production=production)
                                .aggregate(s=Sum('qty'))['s'] or 0)
        production.save(update_fields=['total_qty', 'updated_at'])


class TicketPrintView(CompanyContextMixin, APIView):
    """POST /tickets/print 批量标记打印（§7）：print_count+1、is_printed=True、写操作日志。

    入参 {ticket_ids: [id...], print_key?: str}；权限：print.manage 或 production.manage。
    - H1：计数走单条 UPDATE(F 表达式) 原子自增，避免并发读改写丢失更新；
    - H2：print_key 逐票幂等（ticket_print_log 唯一约束兜底），命中则记 duplicated 不重复计数；
    - M1：缺失/已删票不拦截，进 skipped 返回（保持 200）；响应 {reported, skipped, duplicated}。
    实际蓝牙打印在小程序端完成，本接口只做打印结果上报与计数。
    """

    def post(self, request):
        # Plan A：打印计数=print.manage 或 production.manage（历史 'production' 一把钥匙，零回归）
        if not (self.has_perm('print.manage') or self.has_perm('production.manage')):
            raise PermissionDenied('没有该模块的操作权限')
        raw_ids = request.data.get('ticket_ids')
        if not isinstance(raw_ids, list) or not raw_ids:
            raise ValidationError({'ticket_ids': '请提交菲票 id 列表'})
        try:
            ids = [int(v) for v in raw_ids]
        except (TypeError, ValueError):
            raise ValidationError({'ticket_ids': '菲票 id 须为整数'})
        ids = list(dict.fromkeys(ids))  # 去重，避免重复计数
        print_key = str(request.data.get('print_key') or '')[:64] or None
        now = timezone.now()
        with transaction.atomic():
            tickets = list(Ticket.objects.select_related('production', 'production__style')
                           .filter(company=self.company, id__in=ids))
            by_id = {t.id: t for t in tickets}
            missing = [i for i in ids if i not in by_id]
            # H2 幂等快查：本 key 已上报过的票不再计数（唯一约束兜底并发）
            seen = set(TicketPrintLog.objects.filter(
                company=self.company, print_key=print_key,
                ticket_id__in=list(by_id)).values_list('ticket_id', flat=True)) \
                if print_key else set()
            to_count = [t for t in tickets if t.id not in seen]
            # H1 原子自增：单条 UPDATE 落库，无读改写竞态
            if to_count:
                Ticket.objects.filter(company=self.company,
                                      id__in=[t.id for t in to_count]).update(
                    print_count=F('print_count') + 1, is_printed=True,
                    print_at=now, updated_at=now)
                # M4 明细：is_reprint 取 update 前的 is_printed 快照（上面对象未回刷）
                TicketPrintLog.objects.bulk_create([
                    TicketPrintLog(company=self.company, ticket_id=t.id,
                                   operator=request.user, print_key=print_key,
                                   is_reprint=bool(t.is_printed))
                    for t in to_count])
        if to_count:
            self._audit(to_count)
        return Response({'reported': len(to_count), 'skipped': missing,
                         'duplicated': sorted(seen)})

    def _audit(self, tickets):
        """日志 message 服务端插值（§14 禁止存模板）；床次/款号字段冗余便于检索。"""
        bed = '、'.join(sorted({t.bed_no for t in tickets}))
        style_no = tickets[0].production.style.style_no if tickets else ''
        audit(self.company, AuditLog.Type.OTHER, operator=self.request.user,
              bed_no=bed, style_no=style_no,
              message=f'{self.request.user.real_name} 打印菲票 {len(tickets)} 张'
                      f'（床次 {bed}，款号 {style_no}）')


class TicketDetailView(CompanyContextMixin, APIView):
    """DELETE /tickets/<pk> 删除菲票（§八）：需先撤销全部登记。"""

    def delete(self, request, pk):
        self.check_perm('production.manage')
        try:
            ticket = Ticket.objects.get(company=self.company, pk=pk)
        except Ticket.DoesNotExist:
            raise Http404('菲票不存在')
        from apps.records.models import Record
        if Record.objects.filter(company=self.company, ticket=ticket).exists():
            raise ValidationError('该菲票已有登记记录，需先撤销全部登记')
        production = ticket.production
        ticket.delete()
        self._recalc_total(production)
        return Response(status=status.HTTP_204_NO_CONTENT)

    def _recalc_total(self, production):
        production.total_qty = (Ticket.objects.filter(production=production)
                                .aggregate(s=Sum('qty'))['s'] or 0)
        production.save(update_fields=['total_qty', 'updated_at'])
