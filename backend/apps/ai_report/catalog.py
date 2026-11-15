# -*- coding: utf-8 -*-
"""受治理的动态报表目录。

字段 key 是公开契约，ORM path 不外传；数据集必须显式注册，不能把模型生成
的字符串直接交给 ORM。跨一对多关系的指标在执行器中按主单行预聚合，禁止
直接 JOIN 后求和造成订单量、裁货量或登记量膨胀。
"""
from dataclasses import dataclass
from functools import cached_property

from django.db.models import (
    CharField, DateField, F, IntegerField, OuterRef, Subquery, Value,
)
from django.db.models.functions import (
    Coalesce, NullIf, TruncDate, TruncMonth, TruncWeek,
)
from django.utils import timezone

from apps.accounts.models import UserCompany
from apps.core.permissions import has_perm
from apps.finance.models import CashEntry, Payable, Receivable, ReceiptLine, SupplierPayment
from apps.orders.models import Order
from apps.production.models import Production
from apps.records.models import Record
from apps.salary.models import SalaryPayment, SettleApply, Settlement
from apps.shipment.models import Shipment
from apps.tech.models import Material
from apps.warehouse.models import Lot, PurchaseItem, PurchaseOrder

CATALOG_VERSION = '2026-11-11.v7'
TEXT_OPERATORS = ('eq', 'neq', 'in', 'contains', 'isnull')
NUMBER_OPERATORS = ('eq', 'neq', 'in', 'gte', 'lte', 'isnull')
DATE_OPERATORS = ('between', 'gte', 'lte')
DATE_NULL_OPERATORS = DATE_OPERATORS + ('isnull',)
BOOLEAN_OPERATORS = ('eq', 'neq')
CHOICE_OPERATORS = ('eq', 'neq', 'in', 'isnull')


@dataclass(frozen=True)
class FieldSpec:
    key: str
    label: str
    type: str
    path: str = ''
    permission: str | None = None
    can_dimension: bool = True
    can_filter: bool = True
    operators: tuple = TEXT_OPERATORS
    trunc: str = ''
    special: str = ''
    choices: tuple = ()

    def expression(self, company):
        """每次新建表达式，避免跨查询复用带状态的 Django expression。"""
        if self.trunc:
            trunc_class = {
                'day': TruncDate, 'week': TruncWeek, 'month': TruncMonth,
            }[self.trunc]
            return trunc_class(
                self.path, tzinfo=timezone.get_current_timezone(),
                output_field=DateField())
        if self.special == 'group_no':
            membership = UserCompany.objects.filter(
                company=company, user_id=OuterRef('user_id')).order_by('id')
            return Coalesce(
                Subquery(membership.values('group_no')[:1]), Value(0),
                output_field=IntegerField())
        if self.special == 'post_name':
            membership = UserCompany.objects.filter(
                company=company, user_id=OuterRef('user_id')).order_by('id')
            return Coalesce(
                Subquery(membership.values('post__name')[:1]), Value('未设岗位'),
                output_field=CharField())
        if self.special == 'employee_name':
            return Coalesce(
                NullIf(F('user__real_name'), Value('')), F('user__username'),
                output_field=CharField())
        if self.special == 'settlement_employee':
            return Coalesce(
                NullIf(F('user__real_name'), Value('')), F('user__username'),
                Value('全员结算'), output_field=CharField())
        if self.special == 'salary_payment_employee':
            return Coalesce(
                NullIf(F('employee__real_name'), Value('')), F('employee__username'),
                output_field=CharField())
        if self.special == 'purchase_supplier':
            return Coalesce(
                NullIf(F('order__supplier__name'), Value('')),
                NullIf(F('order__supplier_name'), Value('')),
                Value('未指定供应商'), output_field=CharField())
        return F(self.path)


@dataclass(frozen=True)
class MetricSpec:
    key: str
    label: str
    type: str
    aggregations: tuple
    default_aggregation: str
    permission: str | None = None
    aggregation_labels: dict | None = None
    path: str = ''
    special: str = ''
    requires_unit: bool = False
    ratio_numerator: str = ''
    ratio_denominator: str = ''

    def label_for(self, aggregation):
        return (self.aggregation_labels or {}).get(aggregation, self.label)


@dataclass(frozen=True)
class DatasetSpec:
    key: str
    label: str
    description: str
    model: object
    note: str
    fields: tuple
    metrics: tuple
    permission: str | None = None
    default_dimensions: tuple = ()
    default_metrics: tuple = ()
    time_field: str = 'created_at'
    time_label: str = '创建时间'
    deleted_label: str = '已删除数据'
    unit_dimension: str = ''
    company_path: str = 'company'
    active_related_paths: tuple = ()
    supports_as_of: bool = False
    supports_include_deleted: bool = True

    @cached_property
    def field_map(self):
        return {field.key: field for field in self.fields}

    @cached_property
    def metric_map(self):
        return {metric.key: metric for metric in self.metrics}


def calendar_fields(path='created_at', prefix='created', label='创建'):
    return (
        FieldSpec(f'{prefix}_at', f'{label}时间', 'datetime', path,
                  can_dimension=False, operators=DATE_OPERATORS),
        FieldSpec(f'{prefix}_day', f'{label}日', 'date', path,
                  can_filter=False, operators=(), trunc='day'),
        FieldSpec(f'{prefix}_week', f'{label}周', 'date', path,
                  can_filter=False, operators=(), trunc='week'),
        FieldSpec(f'{prefix}_month', f'{label}月', 'date', path,
                  can_filter=False, operators=(), trunc='month'),
    )


PIECEWORK_FIELDS = (
    FieldSpec('registered_at', '登记时间', 'datetime', 'registered_at',
              can_dimension=False, operators=DATE_OPERATORS),
    FieldSpec('registered_day', '登记日', 'date', 'registered_at',
              can_filter=False, operators=(), trunc='day'),
    FieldSpec('registered_week', '登记周', 'date', 'registered_at',
              can_filter=False, operators=(), trunc='week'),
    FieldSpec('registered_month', '登记月', 'date', 'registered_at',
              can_filter=False, operators=(), trunc='month'),
    FieldSpec('process_id', '工序ID', 'integer', 'process_id',
              can_dimension=False, operators=NUMBER_OPERATORS),
    FieldSpec('process_name', '工序', 'text', 'process__name'),
    FieldSpec('employee_id', '员工ID', 'integer', 'user_id',
              permission='salary.view', can_dimension=False,
              operators=NUMBER_OPERATORS),
    FieldSpec('employee_name', '员工', 'text', permission='salary.view',
              special='employee_name'),
    FieldSpec('group_no', '生产组', 'group', permission=None,
              operators=NUMBER_OPERATORS, special='group_no'),
    FieldSpec('post_name', '岗位', 'text', permission='salary.view',
              special='post_name'),
    FieldSpec('style_no', '款号', 'text', 'ticket__production__style__style_no'),
    FieldSpec('style_name', '款式名称', 'text', 'ticket__production__style__name'),
    FieldSpec('customer_name', '客户', 'text', 'ticket__production__customer__name'),
    FieldSpec('order_no', '订单号', 'text', 'ticket__production__order__order_no'),
    FieldSpec('bed_no', '床次号', 'text', 'ticket__bed_no'),
    FieldSpec('bundle_no', '扎号', 'text', 'ticket__bundle_no'),
    FieldSpec('color', '颜色', 'text', 'ticket__color'),
    FieldSpec('size', '尺码', 'text', 'ticket__size'),
)

PIECEWORK_METRICS = (
    MetricSpec(
        'registered_qty', '登记件数', 'integer', ('sum', 'avg', 'min', 'max'), 'sum',
        aggregation_labels={
            'sum': '登记件数', 'avg': '平均每笔件数',
            'min': '最小单笔件数', 'max': '最大单笔件数',
        }),
    MetricSpec('estimated_wage', '预估工资', 'money', ('sum',), 'sum',
               permission='salary.view'),
    MetricSpec('record_count', '登记笔数', 'integer', ('count',), 'count'),
    MetricSpec('employee_count', '参与人数', 'integer', ('count_distinct',), 'count_distinct'),
    MetricSpec('ticket_count', '涉及菲票数', 'integer', ('count_distinct',), 'count_distinct'),
)

PIECEWORK_DATASET = DatasetSpec(
    key='piecework_records',
    label='计件登记',
    description='按登记时间统计计件数量、登记笔数、参与人数、菲票数和预估工资。',
    model=Record,
    note=(
        '登记件数不等于成衣产量；预估工资=Σ登记件数×登记单价，非最终结算金额。'
        '生产组与岗位取员工在当前公司的有效成员关系。'
    ),
    fields=PIECEWORK_FIELDS,
    metrics=PIECEWORK_METRICS,
    default_dimensions=('process_name',),
    default_metrics=('registered_qty', 'estimated_wage', 'record_count'),
    time_field='registered_at',
    time_label='登记时间',
    deleted_label='已撤销登记',
)

ORDER_DATASET = DatasetSpec(
    key='orders',
    label='客户订单',
    description='按创建时间或交期统计订单数、订单数量、已裁数量和计件登记量。',
    model=Order,
    permission='orders.manage',
    note=(
        '订单数量=有效色码明细合计；已裁数量=有效床次的有效菲票合计；'
        '登记件数为挂单床次各工序登记累加，不等于成衣完成数。'
        '已删除主单只有全域权限用户显式选择后才纳入。'
    ),
    fields=calendar_fields() + (
        FieldSpec('order_id', '订单ID', 'integer', 'id',
                  can_dimension=False, operators=NUMBER_OPERATORS),
        FieldSpec('delivery_date', '交期', 'date', 'delivery_date',
                  operators=DATE_NULL_OPERATORS),
        FieldSpec('order_no', '订单号', 'text', 'order_no'),
        FieldSpec('customer_name', '客户', 'text', 'customer__name'),
        FieldSpec('style_no', '款号', 'text', 'style__style_no'),
        FieldSpec('style_name', '款式名称', 'text', 'style__name'),
        FieldSpec('status', '订单状态', 'text', 'status',
                  operators=CHOICE_OPERATORS, choices=tuple(Order.Status.choices)),
        FieldSpec('order_type', '订单类型', 'text', 'order_type',
                  operators=CHOICE_OPERATORS, choices=tuple(Order.OrderType.choices)),
        FieldSpec('remark', '备注', 'text', 'remark', can_dimension=False),
    ),
    metrics=(
        MetricSpec('order_count', '订单数', 'integer', ('count',), 'count',
                   special='count_id'),
        MetricSpec('order_qty', '订单数量', 'integer', ('sum',), 'sum',
                   special='order_item_qty'),
        MetricSpec('cut_qty', '已裁数量', 'integer', ('sum',), 'sum',
                   special='order_cut_qty'),
        MetricSpec('registered_qty', '登记件数', 'integer', ('sum',), 'sum',
                   special='order_registered_qty'),
    ),
    default_dimensions=('status',),
    default_metrics=('order_count', 'order_qty', 'cut_qty', 'registered_qty'),
    deleted_label='已删除订单',
)

PRODUCTION_DATASET = DatasetSpec(
    key='productions',
    label='生产床次',
    description='按创建时间统计床次数、计划裁货、实际裁货、菲票和计件登记量。',
    model=Production,
    permission='production.manage',
    note=(
        '实际裁货取床次裁货总数量；登记件数为各工序登记累加，不能直接当作床次完成率。'
        '床次完成率采用工序扎数口径，本批不开放该派生指标。'
    ),
    fields=calendar_fields() + (
        FieldSpec('production_id', '床次ID', 'integer', 'id',
                  can_dimension=False, operators=NUMBER_OPERATORS),
        FieldSpec('bed_no', '床次号', 'text', 'bed_no'),
        FieldSpec('order_no', '订单号', 'text', 'order__order_no'),
        FieldSpec('customer_name', '客户', 'text', 'customer__name'),
        FieldSpec('style_no', '款号', 'text', 'style__style_no'),
        FieldSpec('style_name', '款式名称', 'text', 'style__name'),
        FieldSpec('order_type', '订单类型', 'text', 'order_type',
                  operators=CHOICE_OPERATORS, choices=tuple(Production.OrderType.choices)),
        FieldSpec('fabric', '面料', 'text', 'fabric'),
        FieldSpec('source_order_no', '来源单号', 'text', 'source_order_no'),
        FieldSpec('is_locked', '锁定状态', 'boolean', 'is_locked',
                  operators=BOOLEAN_OPERATORS,
                  choices=((True, '已锁定'), (False, '未锁定'))),
        FieldSpec('is_cut_done', '裁完状态', 'boolean', 'is_cut_done',
                  operators=BOOLEAN_OPERATORS,
                  choices=((True, '已裁完'), (False, '未裁完'))),
    ),
    metrics=(
        MetricSpec('production_count', '床次数', 'integer', ('count',), 'count',
                   special='count_id'),
        MetricSpec('cut_qty', '实际裁货', 'integer', ('sum',), 'sum', path='total_qty'),
        MetricSpec('plan_qty', '计划裁货', 'integer', ('sum',), 'sum', path='plan_qty'),
        MetricSpec('registered_qty', '登记件数', 'integer', ('sum',), 'sum',
                   special='production_registered_qty'),
        MetricSpec('ticket_count', '菲票数', 'integer', ('count',), 'count',
                   special='production_ticket_count'),
    ),
    default_dimensions=('is_cut_done',),
    default_metrics=('production_count', 'cut_qty', 'registered_qty', 'ticket_count'),
    deleted_label='已删除床次',
)

SHIPMENT_DATASET = DatasetSpec(
    key='shipments',
    label='出货管理',
    description='按发货日期统计出货单、实际出货数量、单据数量和箱数。',
    model=Shipment,
    permission='shipment.view',
    note=(
        '实际出货数量只统计已审核的 confirmed/partial/shipped/signed 单据，草稿不计；'
        '单据数量和箱数包含筛选范围内的草稿，需结合实际出货状态解读。'
        '数量取出货单汇总字段，不连接色码明细或装箱行，避免一对多连接放大。'
    ),
    fields=calendar_fields() + (
        FieldSpec('shipment_id', '出货单ID', 'integer', 'id',
                  can_dimension=False, operators=NUMBER_OPERATORS),
        FieldSpec('ship_date', '发货日期', 'date', 'ship_date',
                  operators=DATE_NULL_OPERATORS),
        FieldSpec('delivery_date', '交货日期', 'date', 'delivery_date',
                  operators=DATE_NULL_OPERATORS),
        FieldSpec('shipment_no', '出货单号', 'text', 'shipment_no'),
        FieldSpec('order_no', '订单号', 'text', 'order__order_no'),
        FieldSpec('customer_name', '客户', 'text', 'customer__name'),
        FieldSpec('style_no', '款号', 'text', 'order__style__style_no'),
        FieldSpec('style_name', '款式名称', 'text', 'order__style__name'),
        FieldSpec('status', '出货状态', 'text', 'status',
                  operators=CHOICE_OPERATORS, choices=tuple(Shipment.Status.choices)),
        FieldSpec('logistics_no', '物流单号', 'text', 'logistics_no'),
        FieldSpec('document_qty_field', '单据数量值', 'integer', 'total_qty',
                  can_dimension=False, operators=NUMBER_OPERATORS),
        FieldSpec('boxes_field', '箱数值', 'integer', 'boxes',
                  can_dimension=False, operators=NUMBER_OPERATORS),
        FieldSpec('remark', '备注', 'text', 'remark', can_dimension=False),
    ),
    metrics=(
        MetricSpec('shipment_count', '出货单数', 'integer', ('count',), 'count',
                   special='count_id'),
        MetricSpec('audited_shipment_count', '已审核出货单数', 'integer', ('count',), 'count',
                   special='audited_shipment_count'),
        MetricSpec('shipped_qty', '实际出货数量', 'integer', ('sum',), 'sum',
                   special='shipped_qty'),
        MetricSpec('document_qty', '单据数量', 'integer', ('sum',), 'sum', path='total_qty'),
        MetricSpec('boxes', '箱数', 'integer', ('sum',), 'sum', path='boxes'),
    ),
    default_dimensions=('status',),
    default_metrics=('shipment_count', 'audited_shipment_count', 'shipped_qty',
                     'document_qty', 'boxes'),
    time_field='ship_date',
    time_label='发货日期',
    deleted_label='已删除/已作废出货单',
)


WAREHOUSE_LOT_DATASET = DatasetSpec(
    key='warehouse_lots',
    label='仓库批次库存',
    description='按批次、面料和单位统计入库流水、已确认领退料及当前流水库存。',
    model=Lot,
    permission='warehouse.manage',
    note=(
        '当前流水库存=入库流水（含草稿入库、排除已删除行）−已确认领料+已确认退料；'
        '不是独立库存快照，不使用批次档案的原始数量字段代替结存。'
        '数量必须按单位分组，米、公斤等不同单位不得混加；跨单位换算与历史时点库存未开放。'
        '批次创建日期仅用于筛选批次，不代表库存截至日期。'
    ),
    fields=calendar_fields() + (
        FieldSpec('lot_id', '批次ID', 'integer', 'id',
                  can_dimension=False, operators=NUMBER_OPERATORS),
        FieldSpec('lot_no', '批次号', 'text', 'lot_no'),
        FieldSpec('fabric_name', '面料名称', 'text', 'fabric_name'),
        FieldSpec('color', '颜色', 'text', 'color'),
        FieldSpec('unit', '单位', 'text', 'unit'),
        FieldSpec('batch_date', '批次日期', 'date', 'batch_date',
                  operators=DATE_NULL_OPERATORS),
        FieldSpec('source_in_no', '来源入库单号', 'text', 'source_in__stock_in__in_no'),
        FieldSpec('remark', '备注', 'text', 'remark', can_dimension=False),
    ),
    metrics=(
        MetricSpec('lot_count', '批次数', 'integer', ('count',), 'count',
                   special='count_id'),
        MetricSpec('inbound_qty', '入库流水量', 'decimal', ('sum',), 'sum',
                   special='lot_inbound_qty', requires_unit=True),
        MetricSpec('issued_qty', '已确认领料量', 'decimal', ('sum',), 'sum',
                   special='lot_issued_qty', requires_unit=True),
        MetricSpec('returned_qty', '已确认退料量', 'decimal', ('sum',), 'sum',
                   special='lot_returned_qty', requires_unit=True),
        MetricSpec('current_stock_qty', '当前流水库存', 'decimal', ('sum',), 'sum',
                   special='lot_current_stock_qty', requires_unit=True),
    ),
    default_dimensions=('fabric_name', 'unit'),
    default_metrics=('current_stock_qty', 'inbound_qty', 'issued_qty', 'returned_qty'),
    time_field='created_at',
    time_label='批次创建时间（不是库存截至时间）',
    deleted_label='已删除批次',
    unit_dimension='unit',
)


MATERIAL_STOCK_DATASET = DatasetSpec(
    key='material_stock',
    label='物料主单位库存',
    description='按物料及当前主单位汇总可换算的入库、已确认领退料和流水库存。',
    model=Material,
    permission='warehouse.manage',
    note=(
        '可换算库存按流水显式关联的物料汇总，当前物料主单位相同的数量直接计入；'
        '公斤↔米使用当前克重和幅宽，每米公斤=克重×幅宽÷100000。'
        '未知单位、跨单位缺参数及未关联物料的流水不伪造数量；未关联物料的流水不在本集出现，'
        '其余不可换算流水计入未覆盖流水行数，必须结合该指标解读。'
        '入库含草稿，领退料只计已确认；排除当前软删流水。'
        'as_of 是截至业务日期按当前留存流水重算，不是审计快照，不能恢复历史软删、确认状态或物料参数；'
        '不得用物料或批次创建日期代替库存截至日期。'
    ),
    fields=calendar_fields() + (
        FieldSpec('material_id', '物料ID', 'integer', 'id',
                  can_dimension=False, operators=NUMBER_OPERATORS),
        FieldSpec('material_code', '物料编码', 'text', 'code'),
        FieldSpec('material_name', '物料名称', 'text', 'name'),
        FieldSpec('category', '物料分类', 'text', 'category',
                  operators=CHOICE_OPERATORS, choices=tuple(Material.Category.choices)),
        FieldSpec('main_unit', '物料主单位', 'text', 'unit'),
        FieldSpec('weight_gsm', '当前克重', 'decimal', 'weight_gsm',
                  can_dimension=False, operators=NUMBER_OPERATORS + ('isnull',)),
        FieldSpec('width_cm', '当前幅宽', 'decimal', 'width_cm',
                  can_dimension=False, operators=NUMBER_OPERATORS + ('isnull',)),
    ),
    metrics=(
        MetricSpec('material_count', '物料数', 'integer', ('count',), 'count',
                   special='count_id'),
        MetricSpec('inbound_qty', '可换算入库量', 'decimal', ('sum',), 'sum',
                   special='material_inbound_qty', requires_unit=True),
        MetricSpec('issued_qty', '可换算已确认领料量', 'decimal', ('sum',), 'sum',
                   special='material_issued_qty', requires_unit=True),
        MetricSpec('returned_qty', '可换算已确认退料量', 'decimal', ('sum',), 'sum',
                   special='material_returned_qty', requires_unit=True),
        MetricSpec('current_stock_qty', '可换算主单位库存', 'decimal', ('sum',), 'sum',
                   special='material_current_stock_qty', requires_unit=True),
        MetricSpec('uncovered_flow_count', '未覆盖流水行数', 'integer', ('count',), 'count',
                   special='material_uncovered_flow_count'),
    ),
    default_dimensions=('material_code', 'material_name', 'main_unit'),
    default_metrics=('current_stock_qty', 'uncovered_flow_count'),
    time_label='物料创建时间（不是库存截至时间）',
    deleted_label='已删除物料',
    unit_dimension='main_unit',
    supports_as_of=True,
)


PURCHASE_ITEM_DATASET = DatasetSpec(
    key='purchase_items',
    label='采购明细',
    description='按采购日期、供应商、物料和单位统计采购、确认到料及待到料数量。',
    model=PurchaseItem,
    permission='warehouse.manage',
    note=(
        '采购与待到料数量必须按原采购单位分组。已确认到料/合格/拒收取已确认暂收流水，'
        '草稿暂收不计；待到料量逐采购行计算 max(0,采购数量−已确认到料)，不允许行间冲抵。'
        '可换算采购金额按当前物料主单位、克重和幅宽换算数量后乘规范化单价；'
        '缺物料、缺参数或未知单位的行不计入该金额，必须同时查看金额待补参数行数。'
        '采购完成不等于已入库或已付款，本数据集不提供应付和实付。'
    ),
    fields=(
        FieldSpec('ordered_at', '采购日期', 'date', 'order__ordered_at',
                  can_dimension=False, operators=DATE_OPERATORS),
        FieldSpec('ordered_day', '采购日', 'date', 'order__ordered_at',
                  can_filter=False, operators=(), trunc='day'),
        FieldSpec('ordered_week', '采购周', 'date', 'order__ordered_at',
                  can_filter=False, operators=(), trunc='week'),
        FieldSpec('ordered_month', '采购月', 'date', 'order__ordered_at',
                  can_filter=False, operators=(), trunc='month'),
        FieldSpec('purchase_item_id', '采购行ID', 'integer', 'id',
                  can_dimension=False, operators=NUMBER_OPERATORS),
        FieldSpec('order_no', '采购单号', 'text', 'order__order_no'),
        FieldSpec('supplier_name', '供应商', 'text', special='purchase_supplier'),
        FieldSpec('status', '采购状态', 'text', 'order__status',
                  operators=CHOICE_OPERATORS, choices=tuple(PurchaseOrder.Status.choices)),
        FieldSpec('fabric_name', '面料名称', 'text', 'fabric_name'),
        FieldSpec('color', '颜色', 'text', 'color'),
        FieldSpec('material_code', '物料编码', 'text', 'material__code'),
        FieldSpec('material_name', '物料名称', 'text', 'material__name'),
        FieldSpec('unit', '采购单位', 'text', 'unit'),
        FieldSpec('main_unit', '物料主单位', 'text', 'material__unit'),
        FieldSpec('demand_source', '需求来源', 'text', 'demand_source',
                  operators=CHOICE_OPERATORS,
                  choices=(('manual', '手工'), ('summary_import', '汇总导入'))),
        FieldSpec('remark', '采购备注', 'text', 'order__remark', can_dimension=False),
    ),
    metrics=(
        MetricSpec('purchase_order_count', '采购单数', 'integer', ('count_distinct',),
                   'count_distinct', special='purchase_order_count'),
        MetricSpec('purchase_line_count', '采购行数', 'integer', ('count',), 'count',
                   special='count_id'),
        MetricSpec('purchase_qty', '采购数量', 'decimal', ('sum',), 'sum',
                   path='qty', requires_unit=True),
        MetricSpec('confirmed_arrived_qty', '已确认到料量', 'decimal', ('sum',), 'sum',
                   special='purchase_confirmed_arrived_qty', requires_unit=True),
        MetricSpec('confirmed_qualified_qty', '已确认合格量', 'decimal', ('sum',), 'sum',
                   special='purchase_confirmed_qualified_qty', requires_unit=True),
        MetricSpec('confirmed_rejected_qty', '已确认拒收量', 'decimal', ('sum',), 'sum',
                   special='purchase_confirmed_rejected_qty', requires_unit=True),
        MetricSpec('pending_receipt_qty', '待到料量', 'decimal', ('sum',), 'sum',
                   special='purchase_pending_receipt_qty', requires_unit=True),
        MetricSpec('convertible_purchase_amount', '可换算采购金额', 'money', ('sum',), 'sum',
                   special='purchase_convertible_amount'),
        MetricSpec('amount_pending_line_count', '金额待补参数行数', 'integer', ('count',),
                   'count', special='purchase_amount_pending_count'),
    ),
    default_dimensions=('supplier_name', 'unit'),
    default_metrics=('purchase_order_count', 'purchase_qty', 'confirmed_arrived_qty',
                     'pending_receipt_qty'),
    time_field='ordered_at',
    time_label='采购日期',
    deleted_label='已删除采购行及其采购主单',
    unit_dimension='unit',
    company_path='order__company',
    active_related_paths=('order',),
)


SALARY_SETTLEMENT_DATASET = DatasetSpec(
    key='salary_settlements',
    label='薪酬结算',
    description='按结算期间、员工和状态统计工资结算快照及撤销情况。',
    model=Settlement,
    permission='salary.view',
    note=(
        '有效结算金额只统计已结算状态的金额快照，不包含已撤销结算。'
        '全员结算按分员工快照归属需另查明细，本数据集主行“全员结算”不拆人。'
        '结算金额不是银行或现金实付金额；实付请使用实付工资数据集。全员结算未付余额需按工资快照和员工支付分摊计算。'
    ),
    fields=calendar_fields() + (
        FieldSpec('settlement_id', '结算单ID', 'integer', 'id',
                  can_dimension=False, operators=NUMBER_OPERATORS),
        FieldSpec('period_start', '结算起始', 'date', 'period_start', operators=DATE_OPERATORS),
        FieldSpec('period_end', '结算截止', 'date', 'period_end', operators=DATE_OPERATORS),
        FieldSpec('employee_name', '结算员工', 'text', special='settlement_employee'),
        FieldSpec('status', '结算状态', 'text', 'status',
                  operators=CHOICE_OPERATORS, choices=tuple(Settlement.Status.choices)),
        FieldSpec('created_by_name', '创建人', 'text', 'created_by__real_name'),
    ),
    metrics=(
        MetricSpec('settlement_count', '结算单数', 'integer', ('count',), 'count',
                   special='count_id'),
        MetricSpec('settled_count', '有效结算单数', 'integer', ('count',), 'count',
                   special='salary_settled_count'),
        MetricSpec('settled_amount', '有效结算金额', 'money', ('sum',), 'sum',
                   special='salary_settled_amount'),
        MetricSpec('revoked_amount', '已撤销结算金额', 'money', ('sum',), 'sum',
                   special='salary_revoked_amount'),
        MetricSpec('settled_employee_count', '有效结算覆盖人次', 'integer', ('sum',), 'sum',
                   special='salary_settled_employee_count'),
    ),
    default_dimensions=('period_start', 'employee_name'),
    default_metrics=('settlement_count', 'settled_count', 'settled_amount'),
    deleted_label='已删除结算单',
)


SALARY_APPLICATION_DATASET = DatasetSpec(
    key='salary_applications',
    label='薪酬结算申请',
    description='按申请期间和状态统计待审核、待发放及已发放申请笔数。',
    model=SettleApply,
    permission='salary.view',
    note=(
        '申请金额是发起时的未结登记快照，发放时可能重新计算且申请未关联唯一实付快照，'
        '因此这里只提供申请笔数，不把申请金额解释为实际发放金额。'
    ),
    fields=calendar_fields() + (
        FieldSpec('application_id', '申请ID', 'integer', 'id',
                  can_dimension=False, operators=NUMBER_OPERATORS),
        FieldSpec('period_start', '结算起始', 'date', 'period_start', operators=DATE_OPERATORS),
        FieldSpec('period_end', '结算截止', 'date', 'period_end', operators=DATE_OPERATORS),
        FieldSpec('employee_name', '结算员工', 'text', special='employee_name'),
        FieldSpec('status', '申请状态', 'integer', 'status',
                  operators=CHOICE_OPERATORS, choices=tuple(SettleApply.Status.choices)),
        FieldSpec('paid_at', '发放时间', 'datetime', 'paid_at',
                  can_dimension=False, operators=DATE_NULL_OPERATORS),
        FieldSpec('paid_by_name', '发放人', 'text', 'paid_by__real_name'),
    ),
    metrics=(
        MetricSpec('application_count', '申请笔数', 'integer', ('count',), 'count',
                   special='count_id'),
        MetricSpec('pending_count', '待审核笔数', 'integer', ('count',), 'count',
                   special='salary_apply_pending_count'),
        MetricSpec('approved_count', '待发放笔数', 'integer', ('count',), 'count',
                   special='salary_apply_approved_count'),
        MetricSpec('paid_count', '已发放申请笔数', 'integer', ('count',), 'count',
                   special='salary_apply_paid_count'),
        MetricSpec('rejected_count', '已拒绝笔数', 'integer', ('count',), 'count',
                   special='salary_apply_rejected_count'),
        MetricSpec('voided_count', '已作废笔数', 'integer', ('count',), 'count',
                   special='salary_apply_voided_count'),
    ),
    default_dimensions=('status',),
    default_metrics=('application_count', 'pending_count', 'approved_count', 'paid_count'),
    deleted_label='已删除结算申请',
)


SALARY_PAYMENT_DATASET = DatasetSpec(
    key='salary_payments', label='实付工资',
    description='按实际支付时间、员工和支付方式统计有效实付工资。',
    model=SalaryPayment, permission='salary.view',
    note='实付金额只统计有效支付流水，作废支付保留审计但不计入；结算快照、申请金额和实付金额是不同口径。',
    fields=calendar_fields(path='paid_at', prefix='paid', label='实付') + (
        FieldSpec('payment_id', '支付ID', 'integer', 'id', can_dimension=False,
                  operators=NUMBER_OPERATORS),
        FieldSpec('employee_id', '员工ID', 'integer', 'employee_id', can_dimension=False,
                  operators=NUMBER_OPERATORS),
        FieldSpec('employee_name', '员工', 'text', special='salary_payment_employee'),
        FieldSpec('method', '支付方式', 'text', 'method'),
        FieldSpec('transaction_ref', '外部流水号', 'text', 'transaction_ref'),
        FieldSpec('status', '支付状态', 'text', 'status', operators=CHOICE_OPERATORS,
                  choices=tuple(SalaryPayment.Status.choices)),
    ),
    metrics=(
        MetricSpec('payment_count', '有效支付笔数', 'integer', ('count',), 'count',
                   special='salary_payment_count'),
        MetricSpec('paid_amount', '实付工资金额', 'money', ('sum',), 'sum',
                   special='salary_payment_amount'),
        MetricSpec('employee_count', '实付员工数', 'integer', ('count_distinct',),
                   'count_distinct', special='salary_payment_employee_count'),
    ),
    default_dimensions=('employee_name', 'method'),
    default_metrics=('payment_count', 'paid_amount'),
    time_field='paid_at', time_label='实付时间', deleted_label='已删除工资支付',
)


FINANCE_RECEIVABLE_DATASET = DatasetSpec(
    key='finance_receivables',
    label='财务应收',
    description='按客户、订单、出货单和状态统计应收、累计已收及未收余额。',
    model=Receivable,
    permission='finance.view',
    note=(
        '未收余额=应收金额−累计已收；累计已收取应收账行当前核销结果。'
        '应收没有到期日，不提供逾期天数或账龄；出货作废不自动撤销应收。'
        '当前没有采购应付、供应商付款或费用总账模型，不能用外发结算和订单制费冒充。'
    ),
    fields=calendar_fields() + (
        FieldSpec('receivable_id', '应收ID', 'integer', 'id',
                  can_dimension=False, operators=NUMBER_OPERATORS),
        FieldSpec('receivable_no', '应收单号', 'text', 'receivable_no'),
        FieldSpec('customer_name', '客户', 'text', 'customer__name'),
        FieldSpec('order_no', '订单号', 'text', 'order__order_no'),
        FieldSpec('shipment_no', '出货单号', 'text', 'shipment__shipment_no'),
        FieldSpec('status', '收款状态', 'text', 'status',
                  operators=CHOICE_OPERATORS, choices=tuple(Receivable.Status.choices)),
        FieldSpec('remark', '备注', 'text', 'remark', can_dimension=False),
    ),
    metrics=(
        MetricSpec('receivable_count', '应收笔数', 'integer', ('count',), 'count',
                   special='count_id'),
        MetricSpec('receivable_amount', '应收金额', 'money', ('sum',), 'sum', path='amount'),
        MetricSpec('received_amount', '累计已收金额', 'money', ('sum',), 'sum',
                   path='received_amount'),
        MetricSpec('outstanding_amount', '未收余额', 'money', ('sum',), 'sum',
                   special='finance_outstanding_amount'),
        MetricSpec('collection_rate', '应收核销率', 'percent', ('sum',), 'sum',
                   special='ratio', ratio_numerator='finance_received_amount',
                   ratio_denominator='finance_receivable_amount'),
    ),
    default_dimensions=('customer_name', 'status'),
    default_metrics=('receivable_count', 'receivable_amount', 'received_amount',
                     'outstanding_amount'),
    deleted_label='已作废应收',
)


FINANCE_RECEIPT_DATASET = DatasetSpec(
    key='finance_receipts',
    label='财务回款流水',
    description='按收款时间、方式、客户和经手人统计有效核销回款。',
    model=ReceiptLine,
    permission='finance.view',
    note=(
        '期间回款按有效核销流水的收款时间统计，撤销核销的软删流水不计；'
        '不能按应收创建时间代替收款时间。月结是收款方式枚举，不表示应付。'
    ),
    fields=calendar_fields(path='received_at', prefix='received', label='收款') + (
        FieldSpec('receipt_line_id', '回款流水ID', 'integer', 'id',
                  can_dimension=False, operators=NUMBER_OPERATORS),
        FieldSpec('receivable_no', '应收单号', 'text', 'receivable__receivable_no'),
        FieldSpec('customer_name', '客户', 'text', 'receivable__customer__name'),
        FieldSpec('order_no', '订单号', 'text', 'receivable__order__order_no'),
        FieldSpec('shipment_no', '出货单号', 'text', 'receivable__shipment__shipment_no'),
        FieldSpec('method', '收款方式', 'text', 'method',
                  operators=CHOICE_OPERATORS, choices=tuple(ReceiptLine.Method.choices)),
        FieldSpec('handler', '经手人', 'text', 'handler'),
        FieldSpec('remark', '备注', 'text', 'remark', can_dimension=False),
    ),
    metrics=(
        MetricSpec('receipt_count', '回款笔数', 'integer', ('count',), 'count',
                   special='count_id'),
        MetricSpec('received_amount', '回款金额', 'money', ('sum', 'avg', 'min', 'max'),
                   'sum', path='amount', aggregation_labels={
                       'sum': '回款金额', 'avg': '平均每笔回款',
                       'min': '最小单笔回款', 'max': '最大单笔回款',
                   }),
    ),
    default_dimensions=('method',),
    default_metrics=('receipt_count', 'received_amount'),
    time_field='received_at',
    time_label='收款时间',
    deleted_label='已撤销回款及其已作废应收',
    active_related_paths=('receivable',),
)


FINANCE_PAYABLE_DATASET = DatasetSpec(
    key='finance_payables', label='采购应付',
    description='按供应商、采购单、应付和到期日期统计应付、已付、未付及核销率。',
    model=Payable, permission='finance.view',
    note='应付金额由财务确认；已付只统计有效供应商付款核销，作废付款已回退。采购金额不等于应付或实付。',
    fields=calendar_fields(path='payable_at', prefix='payable', label='应付') + (
        FieldSpec('payable_id', '应付ID', 'integer', 'id', can_dimension=False,
                  operators=NUMBER_OPERATORS),
        FieldSpec('payable_no', '应付单号', 'text', 'payable_no'),
        FieldSpec('supplier_name', '供应商', 'text', 'supplier_name'),
        FieldSpec('purchase_no', '采购单号', 'text', 'purchase__order_no'),
        FieldSpec('due_at', '到期日', 'date', 'due_at', operators=DATE_NULL_OPERATORS),
        FieldSpec('status', '付款状态', 'text', 'status', operators=CHOICE_OPERATORS,
                  choices=tuple(Payable.Status.choices)),
    ),
    metrics=(
        MetricSpec('payable_count', '应付笔数', 'integer', ('count',), 'count', special='count_id'),
        MetricSpec('payable_amount', '应付金额', 'money', ('sum',), 'sum', path='amount'),
        MetricSpec('paid_amount', '累计已付金额', 'money', ('sum',), 'sum', path='paid_amount'),
        MetricSpec('outstanding_amount', '未付余额', 'money', ('sum',), 'sum',
                   special='finance_payable_outstanding'),
        MetricSpec('payment_rate', '应付核销率', 'percent', ('sum',), 'sum', special='ratio',
                   ratio_numerator='finance_payable_paid', ratio_denominator='finance_payable_amount'),
    ),
    default_dimensions=('supplier_name', 'status'),
    default_metrics=('payable_amount', 'paid_amount', 'outstanding_amount', 'payment_rate'),
    time_field='payable_at', time_label='应付日期', deleted_label='已作废应付',
)

SUPPLIER_PAYMENT_DATASET = DatasetSpec(
    key='supplier_payments', label='供应商付款流水',
    description='按实际付款时间、供应商和方式统计有效供应商付款。',
    model=SupplierPayment, permission='finance.view',
    note='付款金额只统计有效流水；作废流水保留审计但不计入付款。未关联应付的付款也计入实际付款。',
    fields=calendar_fields(path='paid_at', prefix='paid', label='付款') + (
        FieldSpec('payment_id', '付款ID', 'integer', 'id', can_dimension=False,
                  operators=NUMBER_OPERATORS),
        FieldSpec('supplier_name', '供应商', 'text', 'supplier_name'),
        FieldSpec('payable_no', '应付单号', 'text', 'payable__payable_no'),
        FieldSpec('method', '付款方式', 'text', 'method', operators=CHOICE_OPERATORS,
                  choices=tuple(SupplierPayment.Method.choices)),
        FieldSpec('status', '流水状态', 'text', 'status', operators=CHOICE_OPERATORS,
                  choices=tuple(SupplierPayment.Status.choices)),
        FieldSpec('transaction_ref', '外部流水号', 'text', 'transaction_ref'),
    ),
    metrics=(
        MetricSpec('payment_count', '有效付款笔数', 'integer', ('count',), 'count',
                   special='supplier_payment_count'),
        MetricSpec('paid_amount', '实际付款金额', 'money', ('sum',), 'sum',
                   special='supplier_payment_amount'),
    ),
    default_dimensions=('supplier_name', 'method'), default_metrics=('payment_count', 'paid_amount'),
    time_field='paid_at', time_label='付款时间', deleted_label='已删除付款',
)

CASH_ENTRY_DATASET = DatasetSpec(
    key='cash_entries', label='其他收支',
    description='按实际业务时间、类别和往来方统计手工登记的费用支出与其他收入。',
    model=CashEntry, permission='finance.view',
    note='仅统计有效手工收支，不含客户回款、工资付款和供应商付款；完整现金流需合并各事实表，本数据集不冒充利润表。',
    fields=calendar_fields(path='occurred_at', prefix='occurred', label='业务') + (
        FieldSpec('entry_id', '收支ID', 'integer', 'id', can_dimension=False,
                  operators=NUMBER_OPERATORS),
        FieldSpec('entry_no', '收支单号', 'text', 'entry_no'),
        FieldSpec('direction', '收支方向', 'text', 'direction', operators=CHOICE_OPERATORS,
                  choices=tuple(CashEntry.Direction.choices)),
        FieldSpec('category', '类别', 'text', 'category'),
        FieldSpec('counterparty', '往来方', 'text', 'counterparty'),
        FieldSpec('method', '收支方式', 'text', 'method'),
        FieldSpec('status', '状态', 'text', 'status', operators=CHOICE_OPERATORS,
                  choices=tuple(CashEntry.Status.choices)),
    ),
    metrics=(
        MetricSpec('expense_amount', '费用支出', 'money', ('sum',), 'sum',
                   special='cash_expense_amount'),
        MetricSpec('other_income_amount', '其他收入', 'money', ('sum',), 'sum',
                   special='cash_income_amount'),
        MetricSpec('other_net_amount', '其他收支净额', 'money', ('sum',), 'sum',
                   special='cash_net_amount'),
    ),
    default_dimensions=('direction', 'category'),
    default_metrics=('expense_amount', 'other_income_amount', 'other_net_amount'),
    time_field='occurred_at', time_label='业务时间', deleted_label='已删除收支',
)
CASH_FLOW_DATASET = DatasetSpec(
    key='finance_cash_flow', label='统一经营现金流',
    description='合并客户回款、其他收入、实付工资、供应商付款和费用，按实际业务时间统计。',
    model=CashEntry, permission='finance.manage',
    note='经营现金流只统计有效实际流水；流出以正数展示，净现金流=流入−流出，本报表不是利润表。',
    fields=calendar_fields(path='occurred_at', prefix='occurred', label='业务') + (
        FieldSpec('flow_type', '现金流来源', 'text', operators=('eq', 'neq', 'in'),
                  choices=(
                      ('customer_receipts', '客户回款'), ('other_income', '其他收入'),
                      ('salary_payments', '实付工资'),
                      ('supplier_payments', '供应商付款'), ('expenses', '费用支出'),
                  )),
        FieldSpec('direction', '收支方向', 'text', operators=('eq', 'neq', 'in'),
                  choices=(('income', '流入'), ('expense', '流出'))),
    ),
    metrics=tuple(
        MetricSpec(key, label, 'money', ('sum',), 'sum', permission='salary.view')
        for key, label in (
            ('customer_receipts', '客户回款'), ('other_income', '其他收入'),
            ('salary_payments', '实付工资'), ('supplier_payments', '供应商付款'),
            ('expenses', '费用支出'), ('income', '流入合计'),
            ('outflow', '流出合计'), ('net', '净现金流'),
        )
    ),
    default_dimensions=('occurred_month',),
    default_metrics=('income', 'outflow', 'net'),
    time_field='occurred_at', time_label='实际业务时间',
    deleted_label='作废或撤销流水', supports_include_deleted=False,
)


DATASETS = {
    dataset.key: dataset
    for dataset in (
        PIECEWORK_DATASET, ORDER_DATASET, PRODUCTION_DATASET,
        SHIPMENT_DATASET, WAREHOUSE_LOT_DATASET, MATERIAL_STOCK_DATASET,
        PURCHASE_ITEM_DATASET,
        SALARY_SETTLEMENT_DATASET, SALARY_APPLICATION_DATASET, SALARY_PAYMENT_DATASET,
        FINANCE_RECEIVABLE_DATASET, FINANCE_RECEIPT_DATASET,
        FINANCE_PAYABLE_DATASET, SUPPLIER_PAYMENT_DATASET, CASH_ENTRY_DATASET,
        CASH_FLOW_DATASET,
    )
}


def get_dataset(key):
    return DATASETS.get(key)


def can_use(user_company, permission, full_data=None):
    if not permission:
        return True
    if full_data is None:
        full_data = has_perm(user_company, 'ai.full_data')
    return full_data or has_perm(user_company, permission)


def catalog_for(user_company):
    """只下发当前岗位可使用的数据集、字段和指标。"""
    full_data = has_perm(user_company, 'ai.full_data')
    datasets = []
    for dataset in DATASETS.values():
        if not can_use(user_company, dataset.permission, full_data):
            continue
        if dataset.key == 'finance_cash_flow' and not can_use(
                user_company, 'salary.view', full_data):
            continue
        datasets.append({
            'key': dataset.key,
            'label': dataset.label,
            'description': dataset.description,
            'note': dataset.note,
            'time_field': dataset.time_field,
            'time_label': dataset.time_label,
            'deleted_label': dataset.deleted_label,
            'unit_dimension': dataset.unit_dimension,
            'supports_as_of': dataset.supports_as_of,
            'supports_include_deleted': dataset.supports_include_deleted,
            'fields': [{
                'key': field.key,
                'label': field.label,
                'type': field.type,
                'can_dimension': field.can_dimension,
                'can_filter': field.can_filter,
                'operators': list(field.operators),
                'choices': [
                    {'value': value, 'label': label}
                    for value, label in field.choices
                ],
            } for field in dataset.fields
                if can_use(user_company, field.permission, full_data)],
            'metrics': [{
                'key': metric.key,
                'label': metric.label,
                'type': metric.type,
                'aggregations': list(metric.aggregations),
                'default_aggregation': metric.default_aggregation,
                'requires_unit': metric.requires_unit,
            } for metric in dataset.metrics
                if can_use(user_company, metric.permission, full_data)],
            'default_dimensions': list(dataset.default_dimensions),
            'default_metrics': [
                key for key in dataset.default_metrics
                if can_use(user_company, dataset.metric_map[key].permission, full_data)
            ],
        })
    return {
        'version': CATALOG_VERSION,
        'datasets': datasets,
        'capabilities': {
            'full_data': full_data,
            'export': has_perm(user_company, 'ai.export'),
            'share': has_perm(user_company, 'ai.share'),
            'advanced_sql': False,
        },
        'limits': {'preview_rows': 500, 'export_rows': 5000},
    }
