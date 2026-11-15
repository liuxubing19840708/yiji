"""出货域序列化器（P2 §4.2，契约端点响应口径）。

金额不落出货侧（应收金额由审核服务按 OrderItem.price 现算）；
items 读侧内联输出，写侧由视图层 _validated_item_rows 校验（公司隔离与
订单色码核对在审核时把关，草稿态允许先录后对）。
"""
from rest_framework import serializers

from apps.shipment.models import (PackingBox, Shipment, ShipmentItem,
                                  ShipmentPrintLog)


class ShipmentItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = ShipmentItem
        fields = ['id', 'color', 'size', 'qty']
        read_only_fields = fields


class PackingBoxSerializer(serializers.ModelSerializer):
    # box_no 缺空校验在视图层做（错误文案统一「请填写箱号」）
    box_no = serializers.CharField(required=False, allow_blank=True)

    class Meta:
        model = PackingBox
        fields = ['id', 'box_no', 'color', 'size', 'qty', 'gross_wt',
                  'net_wt', 'box_spec', 'created_at']
        read_only_fields = ['id', 'created_at']


class ShipmentSerializer(serializers.ModelSerializer):
    customer_name = serializers.CharField(source='customer.name', read_only=True,
                                          default='')
    order_no = serializers.CharField(source='order.order_no', read_only=True,
                                     default='')
    status_display = serializers.CharField(source='get_status_display',
                                           read_only=True)
    audited_by_name = serializers.CharField(source='audited_by.real_name',
                                            read_only=True, default='')
    items = ShipmentItemSerializer(many=True, read_only=True)

    class Meta:
        model = Shipment
        fields = ['id', 'shipment_no', 'order', 'order_no', 'customer',
                  'customer_name', 'delivery_date', 'ship_date', 'status',
                  'status_display', 'boxes', 'total_qty', 'logistics_no',
                  'audited_by', 'audited_by_name', 'audited_at', 'remark',
                  'items', 'created_at']
        read_only_fields = fields


class ShipmentPrintLogSerializer(serializers.ModelSerializer):
    template_display = serializers.CharField(source='get_template_display',
                                             read_only=True)
    operator_name = serializers.CharField(source='operator.real_name',
                                          read_only=True, default='')

    class Meta:
        model = ShipmentPrintLog
        fields = ['id', 'shipment', 'template', 'template_display',
                  'print_key', 'is_reprint', 'operator', 'operator_name',
                  'created_at']
        read_only_fields = fields
