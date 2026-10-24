"""订单管理序列化器（订单 / 色码明细）。

total_qty / registered_qty / progress / days_left 为统计行，
由视图聚合后填充（ListCreate 与 Detail 共用同一填充函数，零分叉）。
style 带出（2026-10-21 用户 8 项需求 #3）：style_no/style_name/style_image 供前端展示，
style 可写（挂单/款式关联）；images=备注图片相对路径数组；
process_items=[{process_id, price}] 订单关联工序（视图/序列化器校验本公司归属）。
"""
from rest_framework import serializers

from apps.catalog.models import Style
from apps.orders.models import Order, OrderItem


class OrderItemSerializer(serializers.ModelSerializer):
    """订单色码明细（只读展示）。"""

    class Meta:
        model = OrderItem
        fields = ['id', 'color', 'size', 'qty']


class OrderSerializer(serializers.ModelSerializer):
    # 单号可空（留空由视图自动生成 SO+日期+3 位顺延）；手填单号存量兼容（改单走 PUT）
    order_no = serializers.CharField(required=False, allow_blank=True, label='订单号')
    items = OrderItemSerializer(many=True, read_only=True)
    customer_name = serializers.CharField(source='customer.name', read_only=True, default='')
    style_no = serializers.CharField(source='style.style_no', read_only=True, default='')
    style_name = serializers.CharField(source='style.name', read_only=True, default='')
    style_image = serializers.CharField(source='style.image', read_only=True, default='')
    # 款式必填（2026-10-21 用户 8 项需求 #3 + F1 契约对齐）：新建/修改均须关联款式，
    # 400「请关联款式」；款式归属本公司由视图校验。存量订单数据兼容 null（模型可空）。
    style = serializers.PrimaryKeyRelatedField(
        queryset=Style.objects.all(), label='款式',
        error_messages={'required': '请关联款式', 'null': '请关联款式',
                        'blank': '请关联款式', 'invalid': '款式不存在',
                        'incorrect_type': '款式不存在', 'does_not_exist': '款式不存在'})
    # 订单类型：前端 picker 恒传；非法值不 400 拒收，归一为「自产」（模型默认）
    order_type = serializers.CharField(required=False, allow_blank=True, label='订单类型')

    # 统计行（列表/详情时由视图聚合填充，默认 0）
    total_qty = serializers.IntegerField(read_only=True, default=0)
    registered_qty = serializers.IntegerField(read_only=True, default=0)
    progress = serializers.FloatField(read_only=True, default=0.0)
    days_left = serializers.IntegerField(read_only=True, allow_null=True, default=None)

    class Meta:
        model = Order
        fields = ['id', 'order_no', 'customer', 'customer_name', 'delivery_date',
                  'order_type', 'status', 'remark', 'items',
                  'style', 'style_no', 'style_name', 'style_image',
                  'images', 'process_items',
                  'total_qty', 'registered_qty', 'progress', 'days_left',
                  'created_at']
        read_only_fields = ['id', 'created_at']
        extra_kwargs = {
            'customer': {'label': '客户', 'error_messages': {
                'required': '请选择客户', 'null': '请选择客户', 'invalid': '客户不存在'}},
            'delivery_date': {'label': '交期', 'error_messages': {
                'invalid': '交期格式不正确'}},
            'images': {'label': '备注图片'},
            'process_items': {'label': '订单工序'},
            'remark': {'label': '备注'},
        }

    def validate_order_type(self, value):
        value = (value or '').strip()
        return value if value in Order.OrderType.values else Order.OrderType.SELF

    def validate_process_items(self, value):
        # 结构/工价轻校验放序列化器；工序归属本公司由视图校验（不传=[]）
        if value is None:
            return []
        if not isinstance(value, list):
            raise serializers.ValidationError('订单工序须为列表')
        for idx, item in enumerate(value):
            if not isinstance(item, dict) or not item.get('process_id'):
                raise serializers.ValidationError(f'第{idx + 1}项缺少有效工序')
            price = item.get('price', 0)
            try:
                if price is not None and float(price) < 0:
                    raise serializers.ValidationError(f'第{idx + 1}项工价不能为负数')
            except (TypeError, ValueError):
                raise serializers.ValidationError(f'第{idx + 1}项单价不合法')
        return value

    def validate_images(self, value):
        if value is None:
            return []
        if (not isinstance(value, list)
                or any(not isinstance(p, str) or not p.strip() for p in value)):
            raise serializers.ValidationError('备注图片须为路径数组')
        if len(value) > 9:
            raise serializers.ValidationError('备注图片最多 9 张')
        return [p.strip() for p in value]
