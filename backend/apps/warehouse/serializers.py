"""轻量仓库序列化器（批次 / 采购 / 入库 / 领料 / 血缘）。"""
from django.utils import timezone
from rest_framework import serializers

from apps.warehouse.models import (
    IssueItem,
    Lot,
    MaterialIssue,
    ProductionLotLink,
    PurchaseItem,
    PurchaseOrder,
    StockIn,
    StockInItem,
)


class LotSerializer(serializers.ModelSerializer):
    source_in_no = serializers.CharField(source='source_in.stock_in.in_no',
                                         read_only=True, default='')

    class Meta:
        model = Lot
        fields = ['id', 'lot_no', 'fabric_name', 'color', 'qty', 'unit',
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


class PurchaseItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = PurchaseItem
        fields = ['id', 'fabric_name', 'color', 'qty', 'unit', 'price']


class PurchaseOrderSerializer(serializers.ModelSerializer):
    items = PurchaseItemSerializer(many=True, read_only=True)
    total_qty = serializers.DecimalField(max_digits=13, decimal_places=2, read_only=True, default=0)
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


class StockInItemSerializer(serializers.ModelSerializer):
    lot_id = serializers.IntegerField(source='lot.id', read_only=True, default=0)

    class Meta:
        model = StockInItem
        fields = ['id', 'fabric_name', 'color', 'lot_no', 'qty', 'unit', 'lot_id']


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


class StockInItemInputSerializer(serializers.Serializer):
    fabric_name = serializers.CharField(max_length=64)
    color = serializers.CharField(max_length=64, required=False, allow_blank=True, default='')
    lot_no = serializers.CharField(max_length=64)
    qty = serializers.DecimalField(max_digits=12, decimal_places=2, required=False, default=0)
    unit = serializers.CharField(max_length=16, required=False, allow_blank=True, default='米')


class IssueItemSerializer(serializers.ModelSerializer):
    lot_no = serializers.CharField(source='lot.lot_no', read_only=True)
    bed_no = serializers.CharField(source='production.bed_no', read_only=True)

    class Meta:
        model = IssueItem
        fields = ['id', 'production', 'bed_no', 'lot', 'lot_no', 'qty']


class MaterialIssueSerializer(serializers.ModelSerializer):
    items = IssueItemSerializer(many=True, read_only=True)
    # 缺省=现在（最小前端不必传时间）
    issue_at = serializers.DateTimeField(required=False)
    # 单号留空 → perform_create 的 _next_doc_no('LL') 自动取号
    issue_no = serializers.CharField(required=False, allow_blank=True)

    class Meta:
        model = MaterialIssue
        fields = ['id', 'issue_no', 'issue_at', 'status', 'remark', 'items', 'created_at']


class IssueItemInputSerializer(serializers.Serializer):
    production_id = serializers.IntegerField()
    lot_id = serializers.IntegerField()
    qty = serializers.DecimalField(max_digits=12, decimal_places=2, required=False, default=0)
