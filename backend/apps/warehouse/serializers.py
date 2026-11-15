"""轻量仓库序列化器（批次 / 采购 / 入库 / 领料 / 血缘）。"""
from django.utils import timezone
from rest_framework import serializers

from apps.core.serializers import validate_same_company
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


class LotSerializer(serializers.ModelSerializer):
    source_in_no = serializers.CharField(source='source_in.stock_in.in_no',
                                         read_only=True, default='')

    class Meta:
        model = Lot
        fields = ['id', 'lot_no', 'fabric_name', 'color', 'qty', 'unit', 'unit_cost',
                  'batch_date', 'source_in', 'source_in_no', 'remark', 'created_at']
        read_only_fields = ['source_in']


class LotLinkItemSerializer(serializers.Serializer):
    """血缘写入格式：{lot_id, qty}。"""
    lot_id = serializers.IntegerField()
    qty = serializers.DecimalField(max_digits=12, decimal_places=2, required=False, default=0)


class ProductionLotLinkSerializer(serializers.ModelSerializer):
    lot_no = serializers.CharField(source='lot.lot_no', read_only=True)
    fabric_name = serializers.CharField(source='lot.fabric_name', read_only=True, default='')
    color = serializers.CharField(source='lot.color', read_only=True, default='')

    class Meta:
        model = ProductionLotLink
        fields = ['id', 'production', 'lot', 'lot_no', 'fabric_name', 'color', 'qty']


class SupplierSerializer(serializers.ModelSerializer):
    class Meta:
        model = Supplier
        fields = ['id', 'code', 'name', 'contact_person', 'phone', 'settle_method',
                  'rating', 'remark', 'created_at']


class PurchaseItemSerializer(serializers.ModelSerializer):
    material_name = serializers.CharField(source='material.name', read_only=True, default='')
    material_unit = serializers.CharField(source='material.unit', read_only=True, default='')

    class Meta:
        model = PurchaseItem
        fields = ['id', 'fabric_name', 'color', 'qty', 'unit', 'price', 'material',
                  'material_name', 'material_unit', 'price_unit', 'demand_source',
                  'arrived_qty', 'qualified_qty']


class PurchaseOrderSerializer(serializers.ModelSerializer):
    items = PurchaseItemSerializer(many=True, read_only=True)
    total_qty = serializers.SerializerMethodField()

    def get_total_qty(self, obj):
        # 列表与详情共用；不能依赖列表查询额外 annotate，否则详情和创建响应恒为 0。
        return sum((item.qty for item in obj.items.all()), 0)
    # 单号留空 → perform_create 的 _next_doc_no('CG') 自动取号（前端从不传单号）
    order_no = serializers.CharField(required=False, allow_blank=True)
    # 缺省=今天（最小前端不必传日期）；模型列是 DateField，这里必须 DateField——
    # 用 DateTimeField 会把 datetime 存进日期列，回读 date 与声明不符在响应序列化时崩
    ordered_at = serializers.DateField(required=False)

    class Meta:
        model = PurchaseOrder
        fields = ['id', 'order_no', 'supplier_name', 'ordered_at', 'status',
                  'remark', 'items', 'total_qty', 'created_at']


class PurchaseItemInputSerializer(serializers.Serializer):
    fabric_name = serializers.CharField(max_length=64)
    color = serializers.CharField(max_length=64, required=False, allow_blank=True, default='')
    qty = serializers.DecimalField(max_digits=12, decimal_places=2, required=False, default=0)
    unit = serializers.CharField(max_length=16, required=False, allow_blank=True, default='米')
    price = serializers.DecimalField(max_digits=10, decimal_places=3, required=False, default=0)
    # D19 单价单位：用户原报单位（空=按主单位）；price 存储为主单位规范化值
    price_unit = serializers.ChoiceField(choices=['', 'kg', 'm'], required=False,
                                         allow_blank=True, default='')
    # P2：物料锚点（需求汇总/在途按物料聚合用，可空）+ 需求来源
    material_id = serializers.IntegerField(required=False, allow_null=True)
    demand_source = serializers.ChoiceField(choices=['manual', 'summary_import'],
                                            required=False, default='manual')


class StockInItemSerializer(serializers.ModelSerializer):
    lot_id = serializers.IntegerField(source='lot.id', read_only=True, default=0)
    purchase_item_no = serializers.CharField(source='purchase_item.order.order_no',
                                             read_only=True, default='')
    material_name = serializers.CharField(source='material.name', read_only=True, default='')
    material_unit = serializers.CharField(source='material.unit', read_only=True, default='')

    class Meta:
        model = StockInItem
        fields = ['id', 'fabric_name', 'color', 'lot_no', 'qty', 'unit', 'lot_id',
                  'unit_price', 'price_unit', 'material_unit',
                  'purchase_item', 'purchase_item_no',
                  'material', 'material_name']


class StockInSerializer(serializers.ModelSerializer):
    items = StockInItemSerializer(many=True, read_only=True)
    purchase_no = serializers.CharField(source='purchase.order_no', read_only=True, default='')
    # 缺省=现在（最小前端不必传时间）
    in_at = serializers.DateTimeField(required=False)
    # 单号留空 → perform_create 的 _next_doc_no('RK') 自动取号
    in_no = serializers.CharField(required=False, allow_blank=True)

    class Meta:
        model = StockIn
        fields = ['id', 'in_no', 'source_type', 'purchase', 'purchase_no', 'in_at',
                  'status', 'remark', 'items', 'created_at']

    def validate_purchase(self, value):
        return validate_same_company(self, value, '采购单')


class StockInItemInputSerializer(serializers.Serializer):
    fabric_name = serializers.CharField(max_length=64)
    color = serializers.CharField(max_length=64, required=False, allow_blank=True, default='')
    lot_no = serializers.CharField(max_length=64)
    qty = serializers.DecimalField(max_digits=12, decimal_places=2, required=False, default=0)
    unit = serializers.CharField(max_length=16, required=False, allow_blank=True, default='米')
    # P1 库存价链：带采购行则入库单价缺省取其行价；也可直接手录单价
    purchase_item = serializers.IntegerField(required=False, allow_null=True)
    unit_price = serializers.DecimalField(max_digits=10, decimal_places=3,
                                          required=False, allow_null=True)
    # D19 单价单位：用户原报单位（空=按主单位）；unit_price 存储为主单位规范化值
    price_unit = serializers.ChoiceField(choices=['', 'kg', 'm'], required=False,
                                         allow_blank=True, default='')
    # P2 需求汇总库存口径的物料锚点（可空；暂收确认生成的入库行自动带出）
    material_id = serializers.IntegerField(required=False, allow_null=True)


class IssueItemSerializer(serializers.ModelSerializer):
    lot_no = serializers.CharField(source='lot.lot_no', read_only=True)
    lot_unit = serializers.CharField(source='lot.unit', read_only=True, default='')
    bed_no = serializers.CharField(source='production.bed_no', read_only=True)
    material_name = serializers.CharField(source='material.name', read_only=True, default='')
    material_unit = serializers.CharField(source='material.unit', read_only=True, default='')
    over_state = serializers.SerializerMethodField()

    class Meta:
        model = IssueItem
        fields = ['id', 'production', 'bed_no', 'lot', 'lot_no', 'lot_unit', 'qty',
                  'material', 'material_name', 'material_unit', 'bom_item', 'qty_req',
                  'over', 'over_state', 'over_reason']

    def get_over_state(self, obj) -> str:
        # 延迟导入避免循环依赖
        from apps.warehouse.services import item_over_state
        return item_over_state(obj)[0]


class MaterialIssueSerializer(serializers.ModelSerializer):
    items = IssueItemSerializer(many=True, read_only=True)
    # 缺省=现在（最小前端不必传时间）
    issue_at = serializers.DateTimeField(required=False)
    # 单号留空 → perform_create 的 _next_doc_no('LL') 自动取号
    issue_no = serializers.CharField(required=False, allow_blank=True)

    class Meta:
        model = MaterialIssue
        fields = ['id', 'issue_no', 'type', 'issue_at', 'status', 'remark',
                  'items', 'created_at']


class IssueItemInputSerializer(serializers.Serializer):
    production_id = serializers.IntegerField()
    lot_id = serializers.IntegerField()
    qty = serializers.DecimalField(max_digits=12, decimal_places=2, required=False, default=0)
    # P1 定额化：选定额行（bom_item_id）自动带出 material/qty_req；
    # 无 BOM 可手选 material_id（qty_req 留空=无定额，进占比统计）
    bom_item_id = serializers.IntegerField(required=False, allow_null=True)
    material_id = serializers.IntegerField(required=False, allow_null=True)
    qty_req = serializers.DecimalField(max_digits=12, decimal_places=2,
                                       required=False, allow_null=True)
    over_reason = serializers.CharField(max_length=255, required=False,
                                        allow_blank=True, default='')


# ============ P2 到料暂收（Receipt） ============

class ReceiptItemSerializer(serializers.ModelSerializer):
    purchase_item_no = serializers.CharField(source='purchase_item.order.order_no',
                                             read_only=True, default='')
    material_name = serializers.CharField(source='material.name', read_only=True, default='')
    material_unit = serializers.CharField(source='material.unit', read_only=True, default='')

    class Meta:
        model = ReceiptItem
        fields = ['id', 'purchase_item', 'purchase_item_no', 'material', 'material_name',
                  'material_unit', 'fabric_name', 'color', 'lot_no', 'unit',
                  'arrived_qty', 'qualified_qty', 'rejected_qty', 'remark']


class ReceiptSerializer(serializers.ModelSerializer):
    items = ReceiptItemSerializer(many=True, read_only=True)
    supplier_code = serializers.CharField(source='supplier.code', read_only=True, default='')
    purchase_no = serializers.CharField(source='purchase.order_no', read_only=True, default='')
    confirmed_by_name = serializers.CharField(source='confirmed_by.real_name',
                                              read_only=True, default='')
    # 单号留空 → perform_create 的 _next_doc_no('ZS') 自动取号
    receipt_no = serializers.CharField(required=False, allow_blank=True)

    class Meta:
        model = Receipt
        fields = ['id', 'receipt_no', 'supplier', 'supplier_code', 'supplier_name',
                  'purchase', 'purchase_no', 'status', 'confirmed_at', 'confirmed_by',
                  'confirmed_by_name', 'remark', 'items', 'created_at']


class ReceiptItemInputSerializer(serializers.Serializer):
    """暂收行输入：带 purchase_item 时 fabric_name/color/unit 缺省由 PO 行带出。"""
    purchase_item = serializers.IntegerField(required=False, allow_null=True)
    material_id = serializers.IntegerField(required=False, allow_null=True)
    fabric_name = serializers.CharField(max_length=64, required=False, allow_blank=True,
                                        default='')
    color = serializers.CharField(max_length=64, required=False, allow_blank=True, default='')
    lot_no = serializers.CharField(max_length=64)
    unit = serializers.CharField(max_length=16, required=False, allow_blank=True, default='')
    arrived_qty = serializers.DecimalField(max_digits=12, decimal_places=2,
                                           required=False, default=0)
    qualified_qty = serializers.DecimalField(max_digits=12, decimal_places=2,
                                             required=False, default=0)
    rejected_qty = serializers.DecimalField(max_digits=12, decimal_places=2,
                                            required=False, default=0)
    remark = serializers.CharField(max_length=255, required=False, allow_blank=True, default='')
