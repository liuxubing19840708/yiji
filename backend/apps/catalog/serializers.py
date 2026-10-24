"""基础档案序列化器（款式 / 工序 / 客户 / 菲票）。"""
from rest_framework import serializers

from apps.catalog.models import (
    Customer,
    CustomerGroup,
    Process,
    ProcessTemplate,
    Style,
    StyleProcess,
    Ticket,
)


class CustomerGroupSerializer(serializers.ModelSerializer):
    customer_count = serializers.IntegerField(read_only=True, default=0)

    class Meta:
        model = CustomerGroup
        fields = ['id', 'name', 'customer_count', 'created_at']


class CustomerSerializer(serializers.ModelSerializer):
    group_name = serializers.CharField(source='group.name', read_only=True, default='')
    # 留空自动生成（CustomerListCreateView._next_customer_no），前端不必手编编号
    customer_no = serializers.CharField(required=False, allow_blank=True, max_length=32)

    class Meta:
        model = Customer
        fields = ['id', 'customer_no', 'group', 'group_name', 'name', 'phone',
                  'remark', 'created_at']


class ProcessSerializer(serializers.ModelSerializer):
    class Meta:
        model = Process
        fields = ['id', 'name', 'default_price', 'is_fixed', 'sort', 'created_at']


class StyleProcessSerializer(serializers.ModelSerializer):
    """款式工序配置（只读展示）。"""
    process_name = serializers.CharField(source='process.name', read_only=True)
    is_fixed = serializers.BooleanField(source='process.is_fixed', read_only=True)

    class Meta:
        model = StyleProcess
        fields = ['id', 'process', 'process_name', 'is_fixed', 'price', 'sort', 'allow_skip']


class StyleProcessInputSerializer(serializers.Serializer):
    """款式工序配置（写入格式）。"""
    process_id = serializers.IntegerField()
    price = serializers.DecimalField(max_digits=10, decimal_places=3, required=False)
    sort = serializers.IntegerField(required=False, default=0)
    # 跳序开关：缺省 True 兼容旧调用方（全部允许跳序）
    allow_skip = serializers.BooleanField(required=False, default=True)


class StyleSerializer(serializers.ModelSerializer):
    customer_name = serializers.CharField(source='customer.name', read_only=True, default='')
    processes = StyleProcessSerializer(many=True, read_only=True)
    process_items = StyleProcessInputSerializer(many=True, write_only=True, required=False)

    # 统计行（列表/详情时由视图聚合填充，默认 0）
    cut_total = serializers.IntegerField(read_only=True, default=0)
    ticket_count = serializers.IntegerField(read_only=True, default=0)
    process_count = serializers.IntegerField(read_only=True, default=0)
    done_ticket_count = serializers.IntegerField(read_only=True, default=0)
    unfinished_ticket_count = serializers.IntegerField(read_only=True, default=0)

    class Meta:
        model = Style
        fields = ['id', 'style_no', 'name', 'price', 'customer', 'customer_name',
                  'image', 'images', 'craft_note', 'craft_images', 'video', 'fabric', 'remark',
                  'processes', 'process_items',
                  'cut_total', 'ticket_count', 'process_count',
                  'done_ticket_count', 'unfinished_ticket_count', 'created_at']
        read_only_fields = ['id', 'price', 'created_at']

    def validate_images(self, value):
        """款式图片多图：≤9 张、元素必须是相对路径字符串（/uploads 返回形态）。"""
        if value in (None, ''):
            return []
        if not isinstance(value, list):
            raise serializers.ValidationError('款式图片须为路径数组')
        if len(value) > 9:
            raise serializers.ValidationError('款式图片最多 9 张')
        out = [str(u) for u in value if str(u).strip()]
        return out

    def _sync_image(self, instance):
        """image 恒同步 images 首张（旧读方只认 image 字段，多图无感）。"""
        instance.image = (instance.images or [''])[0] if isinstance(instance.images, list) else ''

    def create(self, validated_data):
        instance = super().create(validated_data)
        self._sync_image(instance)
        instance.save(update_fields=['image'])
        return instance

    def update(self, instance, validated_data):
        instance = super().update(instance, validated_data)
        self._sync_image(instance)
        instance.save(update_fields=['image'])
        return instance


class ProcessTemplateSerializer(serializers.ModelSerializer):
    """工序模板（§工序模板）：items=[{process_id, price}]，校验在序列化器内完成。"""

    class Meta:
        model = ProcessTemplate
        fields = ['id', 'name', 'items', 'created_at']
        read_only_fields = ['id', 'created_at']

    def validate_items(self, value):
        # 传 None 视为未传（保持默认 []），避免 PUT 全量校验误伤
        if value is None:
            return []
        if not isinstance(value, list):
            raise serializers.ValidationError('工序配置须为列表')
        for idx, item in enumerate(value):
            if not isinstance(item, dict):
                raise serializers.ValidationError(f'第{idx + 1}项格式不正确')
            try:
                process_id = int(item.get('process_id'))
            except (TypeError, ValueError):
                raise serializers.ValidationError(f'第{idx + 1}项缺少有效工序')
            price = item.get('price', 0)
            try:
                if price is not None:
                    price = float(price)
            except (TypeError, ValueError):
                raise serializers.ValidationError(f'第{idx + 1}项单价不合法')
            if price is not None and price < 0:
                raise serializers.ValidationError(f'第{idx + 1}项工价不能为负数')
        return value


class TicketSerializer(serializers.ModelSerializer):
    style_no = serializers.CharField(source='production.style.style_no', read_only=True)
    style_name = serializers.CharField(source='production.style.name', read_only=True)

    class Meta:
        model = Ticket
        fields = ['id', 'production', 'style_no', 'style_name', 'bed_no', 'bundle_no',
                  'color', 'size', 'qty', 'fabric', 'piece_no', 'qr_code',
                  'is_printed', 'print_count', 'print_at', 'created_at']
        read_only_fields = ['id', 'qr_code', 'is_printed', 'print_count', 'print_at',
                            'created_at']
