"""外发序列化器（GAP-5）。"""
from rest_framework import serializers

from apps.subcontract.models import OutsourceOrder


class OutsourceOrderSerializer(serializers.ModelSerializer):
    supplier_name = serializers.SerializerMethodField()
    order_no = serializers.CharField(source='order.order_no', read_only=True, default='')
    style_no = serializers.CharField(source='style.style_no', read_only=True, default='')
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    creator_name = serializers.SerializerMethodField()
    receive_count = serializers.IntegerField(read_only=True, default=0)

    class Meta:
        model = OutsourceOrder
        fields = [
            'id', 'no', 'supplier', 'supplier_name', 'factory_name',
            'order', 'order_no', 'style', 'style_no', 'process_desc',
            'send_qty', 'received_qty', 'unit_price', 'amount',
            'send_date', 'due_date', 'return_date', 'status', 'status_display',
            'remark', 'creator', 'creator_name', 'receive_count', 'created_at',
        ]
        read_only_fields = ['no', 'received_qty', 'return_date', 'status', 'creator']

    def get_supplier_name(self, obj):
        return (obj.supplier.name if obj.supplier else '') or obj.factory_name

    def get_creator_name(self, obj):
        return (obj.creator.real_name or obj.creator.username) if obj.creator else None

    def validate(self, attrs):
        send_qty = attrs.get('send_qty') or (self.instance and self.instance.send_qty)
        if not send_qty or send_qty <= 0:
            raise serializers.ValidationError({'send_qty': '外发数量必须大于 0'})
        price = attrs.get('unit_price') or (self.instance and self.instance.unit_price)
        if price is not None and price < 0:
            raise serializers.ValidationError({'unit_price': '加工单价不能为负'})
        return attrs


class OutsourceReceiveSerializer(serializers.Serializer):
    """收回：数量必填，1 ≤ qty ≤ 未收回余量。"""

    qty = serializers.IntegerField(min_value=1)
    remark = serializers.CharField(required=False, allow_blank=True, max_length=200)
