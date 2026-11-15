"""报价域视图（P2 报价域 §4.8；公司隔离 + 2 段式权限点）。

权限：GET 类（列表/详情/回填对比）挂 quotation.view；
建/改/删/状态流转挂 quotation.manage。两点 legacy=False——旧 coarse 岗位
零回归、默认零授权（决策表「P1 权限点」同款）；岗位映射建议：
老板/厂长全开、跟单+quotation.view。

D3 回填对比软提醒语义：backfill-compare 纯只读，不做任何拦截，
完成/出货后的提醒属前端展示（见 services.backfill_compare docstring）。
"""
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.http import Http404
from django.utils import timezone
from django.utils.dateparse import parse_date
from rest_framework import generics
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.catalog.models import Customer, Style
from apps.core.mixins import CompanyContextMixin
from apps.core.pagination import DefaultPagination
from apps.ops.models import AuditLog
from apps.ops.services import audit
from apps.orders.models import Order
from apps.quotation.models import Quotation
from apps.quotation.serializers import (QuotationDetailSerializer,
                                        QuotationListSerializer)
from apps.quotation.services import (RATE_DEFAULT, RATE_MAX, RATE_MIN,
                                     auto_small_lot_addon, backfill_compare,
                                     build_addon_rule, next_quote_no,
                                     save_children, snapshot_labor_base)
from apps.tech.models import BomItem, Material

# 状态流转矩阵：lost/won 为终态（重开须复制新报价，不提供回退）
_STATUS_FLOW = {
    Quotation.Status.DRAFT: {Quotation.Status.SENT, Quotation.Status.WON,
                             Quotation.Status.LOST},
    Quotation.Status.SENT: {Quotation.Status.WON, Quotation.Status.LOST},
    Quotation.Status.WON: set(),
    Quotation.Status.LOST: set(),
}


def _to_decimal(raw):
    """宽容取数：''/None → None；非法数字抛 InvalidOperation。"""
    if raw is None or raw == '':
        return None
    try:
        return Decimal(str(raw).strip())
    except (InvalidOperation, ValueError):
        raise InvalidOperation(str(raw))


def _get_quote(company, pk) -> Quotation:
    """软删报价一律 404（列表不显/详情 404/流转 404，cost 域同款边界）。"""
    q = Quotation.all_objects.filter(company=company, pk=pk).first()
    if q is None or q.is_deleted:
        raise Http404('报价单不存在')
    return q


def _validated_style(company, raw):
    try:
        sid = int(raw)
    except (TypeError, ValueError):
        raise ValidationError({'style': '款式参数不合法'})
    style = Style.objects.filter(company=company, pk=sid).first()
    if style is None:
        raise ValidationError({'style': '款式不存在（须为本公司款式）'})
    return style


def _validated_customer(company, raw):
    if raw in (None, ''):
        return None
    try:
        cid = int(raw)
    except (TypeError, ValueError):
        raise ValidationError({'customer': '客户参数不合法'})
    c = Customer.objects.filter(company=company, pk=cid).first()
    if c is None:
        raise ValidationError({'customer': '客户不存在（须为本公司客户）'})
    return c


def _validated_qty(raw):
    if raw in (None, ''):
        raise ValidationError({'qty': '请填写报价数量'})
    try:
        qty = int(raw)
    except (TypeError, ValueError):
        raise ValidationError({'qty': '报价数量不合法'})
    if qty <= 0:
        raise ValidationError({'qty': '报价数量必须大于0'})
    return qty


def _validated_materials(company, raw):
    """用料行校验 → [{'material','bom_item','quote_unit_usage','price'}]。

    bom_item 选了定额行带出参照：material 缺省取 bom_item.material；
    两者都传时必须同物料（防行内口径矛盾）。usage>0、price>=0。
    """
    if raw is None:
        return None
    if not isinstance(raw, list):
        raise ValidationError({'materials': '用料行须为数组'})
    rows = []
    for i, item in enumerate(raw, start=1):
        label = f'materials[{i}]'
        if not isinstance(item, dict):
            raise ValidationError({label: '格式不正确'})
        mat_id, bom_id = item.get('material'), item.get('bom_item')
        material = bom_item = None
        if mat_id not in (None, ''):
            try:
                material = Material.objects.get(company=company, pk=int(mat_id))
            except (Material.DoesNotExist, TypeError, ValueError):
                raise ValidationError({label: '物料不存在（须为本公司物料）'})
        if bom_id not in (None, ''):
            try:
                bom_item = BomItem.objects.select_related('bom').get(
                    bom__company=company, pk=int(bom_id))
            except (BomItem.DoesNotExist, TypeError, ValueError):
                raise ValidationError({label: 'BOM定额行不存在（须为本公司BOM行）'})
            if material is None:
                material = bom_item.material
            elif material.pk != bom_item.material_id:
                raise ValidationError({label: '物料与BOM定额行不一致'})
        if material is None:
            raise ValidationError({label: '缺少物料（手选物料或选BOM定额行二选一）'})
        try:
            usage = _to_decimal(item.get('quote_unit_usage'))
        except InvalidOperation:
            raise ValidationError({label: '报价单耗不合法'})
        if usage is None or usage <= 0:
            raise ValidationError({label: '报价单耗必须大于0'})
        try:
            price = _to_decimal(item.get('price'))
        except InvalidOperation:
            raise ValidationError({label: '单价不合法'})
        if price is None or price < 0:
            raise ValidationError({label: '单价不能为负数'})
        rows.append({'material': material, 'bom_item': bom_item,
                     'quote_unit_usage': usage, 'price': price})
    return rows


def _validated_labor(company, style, qty, raw):
    """人工行校验 → labor_data dict（缺省全带出：工价快照/默认倍率/小单规则）。

    - labor_base 缺省 = 款式工序工价快照合计（D2「手录或带出」）；
    - rate 缺省 2.3，超 1.8-3.0 置 warning 不拦截（D2 拍板）；
    - small_lot_addon 缺省按 qty 规则（<300 → +30%），显式传入则 auto_applied=False；
    - addon_amount（上机费/制版费）缺省 0，>=0。
    """
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValidationError({'labor': '人工行须为对象'})
    try:
        labor_base = _to_decimal(raw.get('labor_base'))
        if labor_base is None:
            labor_base = snapshot_labor_base(style)
        elif labor_base < 0:
            raise ValidationError({'labor': '工序工价总和不能为负数'})
        rate = _to_decimal(raw.get('rate'))
        if rate is None:
            rate = RATE_DEFAULT
        elif rate <= 0:
            raise ValidationError({'labor': '倍率必须大于0'})
        addon_amount = _to_decimal(raw.get('addon_amount'))
        if addon_amount is None:
            addon_amount = Decimal('0')
        elif addon_amount < 0:
            raise ValidationError({'labor': '附加费不能为负数'})
        addon = _to_decimal(raw.get('small_lot_addon'))
        if addon is None:
            addon = auto_small_lot_addon(qty)
            rule = build_addon_rule(auto_applied=True)
        elif addon < 0 or addon > 100:
            raise ValidationError({'labor': '小单溢价须在 0-100 之间'})
        else:
            rule = build_addon_rule(auto_applied=False)
    except InvalidOperation:
        raise ValidationError({'labor': '人工行数值不合法'})
    custom_rule = raw.get('addon_rule')
    if custom_rule is not None:
        if not isinstance(custom_rule, dict):
            raise ValidationError({'labor': 'addon_rule 须为对象'})
        rule = custom_rule
    return {'labor_base': labor_base, 'rate': rate,
            'small_lot_addon': addon, 'addon_rule': rule,
            'addon_amount': addon_amount,
            'rate_warning': not (RATE_MIN <= rate <= RATE_MAX)}


class QuotationListCreateView(CompanyContextMixin, generics.ListCreateAPIView):
    """GET /quotation/quotations?status=&customer_id=&style_id=&keyword=
       POST /quotation/quotations

    过滤：status=draft/sent/won/lost；customer_id/style_id 精确；
    keyword=报价单号/客户名/款号/款式名模糊。软删不显。
    POST：quote_no 自动号（Q+日期+流水）；materials/labor 缺省自动带出
    （工价快照、默认倍率 2.3、小单溢价按 qty 规则）；算定字段服务端重算。
    """

    pagination_class = DefaultPagination

    def get_serializer_class(self):
        return QuotationListSerializer if self.request.method == 'GET' \
            else QuotationDetailSerializer

    def get_queryset(self):
        qs = (Quotation.objects.filter(company=self.company)
              .select_related('customer', 'style', 'order'))
        p = self.request.query_params
        status = (p.get('status') or '').strip()
        if status:
            if status not in Quotation.Status.values:
                raise ValidationError({'status': '状态须为 draft/sent/won/lost'})
            qs = qs.filter(status=status)
        if p.get('customer_id'):
            qs = qs.filter(customer_id=p.get('customer_id'))
        if p.get('style_id'):
            qs = qs.filter(style_id=p.get('style_id'))
        keyword = (p.get('keyword') or '').strip()
        if keyword:
            from django.db.models import Q
            qs = qs.filter(
                Q(quote_no__icontains=keyword)
                | Q(customer__name__icontains=keyword)
                | Q(style__style_no__icontains=keyword)
                | Q(style__name__icontains=keyword))
        return qs

    def list(self, request, *args, **kwargs):
        self.check_perm('quotation.view')
        return super().list(request, *args, **kwargs)

    def create(self, request, *args, **kwargs):
        self.check_perm('quotation.manage')
        style = _validated_style(self.company, request.data.get('style'))
        customer = _validated_customer(self.company, request.data.get('customer'))
        qty = _validated_qty(request.data.get('qty'))
        quote_date = parse_date(request.data.get('quote_date') or '') \
            or timezone.localdate()
        materials = _validated_materials(self.company, request.data.get('materials'))
        labor = _validated_labor(self.company, style, qty, request.data.get('labor'))
        remark = str(request.data.get('remark') or '').strip()[:255]
        with transaction.atomic():
            q = Quotation.objects.create(
                company=self.company,
                quote_no=next_quote_no(self.company, quote_date),
                customer=customer, style=style, qty=qty,
                quote_date=quote_date,
                status=Quotation.Status.DRAFT, remark=remark)
            if materials is None:
                materials = []
            save_children(q, materials, labor)
        audit(self.company, AuditLog.Type.OTHER, operator=request.user,
              style_no=style.style_no,
              message=f'新建报价单：【{q.quote_no}】款【{style.style_no}】'
                      f'x{qty} 总价 {q.total_price}')
        return Response(QuotationDetailSerializer(q).data, status=201)


class QuotationDetailView(CompanyContextMixin,
                          generics.RetrieveUpdateDestroyAPIView):
    """GET / PUT / DELETE /quotation/quotations/<id>。

    PUT：qty/style/customer/remark 全量可改；materials 传则全量替换
    （BOM 明细同款惯例，不传保持原明细）；labor 传则覆盖（labor_base 缺省
    仍按快照带出——传 null 即重带）；算定字段一律重算。DELETE=软删。
    """

    serializer_class = QuotationDetailSerializer

    def get_object(self):
        return _get_quote(self.company, self.kwargs['pk'])

    def retrieve(self, request, *args, **kwargs):
        self.check_perm('quotation.view')
        return Response(self.get_serializer(self.get_object()).data)

    def update(self, request, *args, **kwargs):
        self.check_perm('quotation.manage')
        q = self.get_object()
        style = (_validated_style(self.company, request.data.get('style'))
                 if request.data.get('style') is not None else q.style)
        customer = (_validated_customer(self.company, request.data.get('customer'))
                    if 'customer' in request.data else q.customer)
        qty = (_validated_qty(request.data.get('qty'))
               if request.data.get('qty') is not None else q.qty)
        quote_date = (parse_date(request.data.get('quote_date') or '')
                      or q.quote_date)
        remark = (str(request.data.get('remark') or '').strip()[:255]
                  if 'remark' in request.data else q.remark)
        materials = _validated_materials(self.company,
                                         request.data.get('materials'))
        labor = (_validated_labor(self.company, style, qty, request.data.get('labor'))
                 if request.data.get('labor') is not None else None)
        with transaction.atomic():
            q.style, q.customer, q.qty = style, customer, qty
            q.quote_date, q.remark = quote_date, remark
            q.save(update_fields=['style', 'customer', 'qty', 'quote_date',
                                  'remark', 'updated_at'])
            if materials is not None or labor is not None:
                if materials is None:
                    materials = [{'material': m.material, 'bom_item': m.bom_item,
                                  'quote_unit_usage': m.quote_unit_usage,
                                  'price': m.price}
                                 for m in q.materials.all()]
                if labor is None:
                    lb = q.labor
                    labor = {'labor_base': lb.labor_base, 'rate': lb.rate,
                             'small_lot_addon': lb.small_lot_addon,
                             'addon_rule': lb.addon_rule,
                             'addon_amount': lb.addon_amount,
                             'rate_warning': lb.rate_warning}
                save_children(q, materials, labor)
        audit(self.company, AuditLog.Type.OTHER, operator=request.user,
              style_no=style.style_no,
              message=f'修改报价单：【{q.quote_no}】总价 {q.total_price}')
        return Response(QuotationDetailSerializer(q).data)

    def perform_destroy(self, instance):
        self.check_perm('quotation.manage')
        instance.delete()  # BaseModel.delete = 软删
        audit(self.company, AuditLog.Type.OTHER, operator=self.request.user,
              message=f'删除报价单：【{instance.quote_no}】')


class QuotationMarkStatusView(CompanyContextMixin, APIView):
    """POST /quotation/quotations/<id>/mark-status 状态流转（挂 quotation.manage）。

    合法流转：draft→sent/won/lost；sent→won/lost；won/lost 为终态（非法回退 400）。
    won 可带 order_id 关联订单（won 后补关联也允许）；order_id 须为本公司
    未删订单，非 won 场景传 order_id 一律 400。
    """

    def post(self, request, pk):
        self.check_perm('quotation.manage')
        q = _get_quote(self.company, pk)
        target = request.data.get('status')
        if target not in Quotation.Status.values or target == Quotation.Status.DRAFT:
            raise ValidationError({'status': '目标状态须为 sent/won/lost'})
        order_id = request.data.get('order_id')
        # won 后补关联（docstring 意图，INT 2026-10-24 修）：单已 won 且 target=won
        # 带 order_id → 允许补挂/改挂订单（状态不变，不算「状态未变化」）；
        # 其余同状态/终态回退规则不动。
        won_relink = (target == Quotation.Status.WON
                      and q.status == Quotation.Status.WON
                      and order_id not in (None, ''))
        if target == q.status and not won_relink:
            raise ValidationError({'status': '状态未变化'})
        if not won_relink and target not in _STATUS_FLOW[q.status]:
            raise ValidationError(
                {'status': f'非法状态流转：{q.get_status_display()}'
                           f'→{dict(Quotation.Status.choices)[target]}'
                           '（won/lost 为终态，如需重报请新建报价单）'})
        order = None
        if order_id not in (None, ''):
            if target != Quotation.Status.WON and q.status != Quotation.Status.WON:
                raise ValidationError({'order_id': '仅 won（含 won 后补关联）可关联订单'})
            try:
                order = Order.objects.get(company=self.company, pk=int(order_id))
            except (Order.DoesNotExist, TypeError, ValueError):
                raise ValidationError({'order_id': '订单不存在（须为本公司未删订单）'})
        old_status = q.status
        q.status = target
        if order is not None:
            q.order = order
        q.save(update_fields=['status', 'order', 'updated_at'])
        if won_relink:
            message = (f'报价单补挂订单：【{q.quote_no}】'
                       f'关联订单 {order.order_no}')
        else:
            message = (f'报价单状态流转：【{q.quote_no}】'
                       f'{dict(Quotation.Status.choices)[old_status]}'
                       f'→{dict(Quotation.Status.choices)[target]}')
            if order:
                message += f'（关联订单 {order.order_no}）'
        audit(self.company, AuditLog.Type.OTHER, operator=request.user,
              before={'status': old_status}, after={'status': target},
              message=message)
        return Response(QuotationDetailSerializer(q).data)


class QuotationBackfillCompareView(CompanyContextMixin, APIView):
    """GET /quotation/quotations/<id>/backfill-compare 回填对比（D3，挂 quotation.view）。

    报价假设（材料/工价人工/合计）vs OrderCost 实际（材料/人工/总成本/收入）
    并排 + 逐项差额 + 差异率。软提醒语义：纯只读，不做任何拦截，不写任何数据；
    完成/出货后的提醒入口属前端展示职责（won 单角标/详情条幅等）。
    """

    def get(self, request, pk):
        self.check_perm('quotation.view')
        q = _get_quote(self.company, pk)
        return Response(backfill_compare(q))
