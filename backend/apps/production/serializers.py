"""生产管理序列化器（床次订单 / 订单工序）。"""
from rest_framework import serializers

from apps.production.models import Production, ProductionProcess


class ProductionProcessSerializer(serializers.ModelSerializer):
    """订单工序（只读展示）。"""
    process_name = serializers.CharField(source='process.name', read_only=True)
    is_fixed = serializers.BooleanField(source='process.is_fixed', read_only=True)

    class Meta:
        model = ProductionProcess
        fields = ['id', 'process', 'process_name', 'is_fixed', 'price']


class ProductionProcessInputSerializer(serializers.Serializer):
    """订单工序（写入格式）。"""
    process_id = serializers.IntegerField()
    price = serializers.DecimalField(max_digits=10, decimal_places=3, required=False)


class ProductionSerializer(serializers.ModelSerializer):
    style_no = serializers.CharField(source='style.style_no', read_only=True)
    style_name = serializers.CharField(source='style.name', read_only=True)
    style_image = serializers.CharField(source='style.image', read_only=True, default='')
    customer_name = serializers.CharField(source='customer.name', read_only=True, default='')
    # 挂单（2026-10-20 P1）：order 可写（建床时传 order_id）；order_no 只读带出
    order_no = serializers.CharField(source='order.order_no', read_only=True, default='')
    order_processes = ProductionProcessSerializer(many=True, read_only=True)
    process_items = ProductionProcessInputSerializer(many=True, write_only=True, required=False)

    # 统计行（列表/详情时由视图聚合填充，默认 0）
    ticket_count = serializers.IntegerField(read_only=True, default=0)
    cut_total = serializers.IntegerField(read_only=True, default=0)
    registered_qty = serializers.IntegerField(read_only=True, default=0)
    done_ticket_count = serializers.IntegerField(read_only=True, default=0)

    class Meta:
        model = Production
        fields = ['id', 'style', 'style_no', 'style_name', 'style_image', 'bed_no', 'total_qty',
                  'plan_qty', 'order_type', 'customer', 'customer_name', 'fabric',
                  'source_order_no', 'order', 'order_no',
                  'is_locked', 'is_cut_done', 'order_processes', 'process_items',
                  'ticket_count', 'cut_total', 'registered_qty', 'done_ticket_count',
                  'created_at']
        read_only_fields = ['id', 'total_qty', 'is_locked', 'is_cut_done', 'created_at']
        # 挂单建床（2026-10-21 需求 #3）：带 order_id 时 style 可不传（从订单继承），
        # 由视图 perform_create 校验兜底（无单挂且无款式 400）
        extra_kwargs = {'style': {'required': False,
                                  'error_messages': {'invalid': '款式不存在'}}}
