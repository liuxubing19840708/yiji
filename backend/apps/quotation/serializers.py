"""报价域序列化器：读侧扁平/详情两套，写侧校验在视图层（口径集中在 services）。"""
from rest_framework import serializers

from apps.quotation.models import QuoteLabor, QuoteMaterial, Quotation


class QuoteMaterialSerializer(serializers.ModelSerializer):
    """报价用料行（读）：amount=单耗×单价（计算属性，随行下发）。"""

    material_code = serializers.CharField(source='material.code', read_only=True)
    material_name = serializers.CharField(source='material.name', read_only=True)
    material_unit = serializers.CharField(source='material.unit', read_only=True)
    bom_item_id = serializers.IntegerField(source='bom_item.id', read_only=True,
                                           allow_null=True, default=None)
    amount = serializers.SerializerMethodField()

    class Meta:
        model = QuoteMaterial
        fields = ['id', 'material', 'material_code', 'material_name',
                  'material_unit', 'bom_item', 'bom_item_id',
                  'quote_unit_usage', 'price', 'amount']
        read_only_fields = fields

    def get_amount(self, obj):
        return str(obj.amount)


class QuoteLaborSerializer(serializers.ModelSerializer):
    """报价人工（读）：rate_warning 随行下发（D2 超界只提示不拦截）。"""

    rate_warning_msg = serializers.SerializerMethodField()

    class Meta:
        model = QuoteLabor
        fields = ['id', 'labor_base', 'rate', 'rate_warning', 'rate_warning_msg',
                  'small_lot_addon', 'addon_rule', 'addon_amount', 'labor_total']
        read_only_fields = fields

    def get_rate_warning_msg(self, obj):
        from apps.quotation.services import RATE_MAX, RATE_MIN
        if not obj.rate_warning:
            return ''
        if obj.rate < RATE_MIN:
            return f'倍率 {obj.rate} 低于常规区间下限 {RATE_MIN}（小厂淡季口径，请确认）'
        return f'倍率 {obj.rate} 高于常规区间上限 {RATE_MAX}（请确认）'


class QuotationListSerializer(serializers.ModelSerializer):
    """列表（读，扁平不嵌套，分页性能）。"""

    customer_name = serializers.CharField(source='customer.name', read_only=True,
                                          default='')
    style_no = serializers.CharField(source='style.style_no', read_only=True)
    style_name = serializers.CharField(source='style.name', read_only=True,
                                       default='')
    order_no = serializers.CharField(source='order.order_no', read_only=True,
                                     allow_null=True, default='')
    status_display = serializers.CharField(source='get_status_display',
                                           read_only=True)

    class Meta:
        model = Quotation
        fields = ['id', 'quote_no', 'customer', 'customer_name',
                  'style', 'style_no', 'style_name', 'qty', 'quote_date',
                  'material_total', 'total_price', 'profit_rate',
                  'status', 'status_display', 'order', 'order_no',
                  'remark', 'created_at']
        read_only_fields = fields


class QuotationDetailSerializer(QuotationListSerializer):
    """详情（读）：嵌套用料行与人工行。"""

    materials = QuoteMaterialSerializer(many=True, read_only=True)
    labor = QuoteLaborSerializer(read_only=True)

    class Meta(QuotationListSerializer.Meta):
        fields = QuotationListSerializer.Meta.fields + ['materials', 'labor']
