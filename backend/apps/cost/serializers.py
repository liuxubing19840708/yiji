"""订单成本序列化器（cost 域，契约 §四 端点）。

金额字段 DRF 默认输出字符串（两位/三位小数原样），与全项目报表口径一致。
revenue 为理论收入（Σ OrderItem.qty×price），与实际收款（折扣/尾数/坏账）
差异属预期——P2 应收核销后以收款实绩复核。
"""
from rest_framework import serializers

from apps.cost.models import OrderCost


class OrderCostSerializer(serializers.ModelSerializer):
    order_no = serializers.CharField(source='order.order_no', read_only=True)
    customer_name = serializers.CharField(source='order.customer.name', read_only=True,
                                          default='')
    style_no = serializers.CharField(source='order.style.style_no', read_only=True,
                                     default='')
    style_name = serializers.CharField(source='order.style.name', read_only=True,
                                       default='')
    order_status = serializers.CharField(source='order.status', read_only=True)
    delivery_date = serializers.DateField(source='order.delivery_date', read_only=True,
                                          allow_null=True)
    computed_by_name = serializers.CharField(source='computed_by.real_name',
                                             read_only=True, default='')

    class Meta:
        model = OrderCost
        fields = ['id', 'order', 'order_no', 'customer_name',
                  'style_no', 'style_name', 'order_status', 'delivery_date',
                  'material_cost', 'labor_cost', 'outsourcing_cost', 'overhead_cost',
                  'total_cost', 'output_qty', 'unit_cost', 'revenue',
                  'no_order_cnt', 'missing_price_cnt',
                  'gross_profit', 'gross_margin',
                  'status', 'is_stale', 'computed_at', 'computed_by_name',
                  'created_at']
        read_only_fields = fields


class OrderPnlSerializer(OrderCostSerializer):
    """GET /cost/orders/pnl 单订单损益行（评审 Top10 #2，2026-11-02）。

    在 OrderCost 基础上叠加应收实绩：ar_amount/ar_received 来自 Receivable
    按 order 聚合（含手工应收；不挂单应收不计入——与成本同口径「挂单才算」）。
    ar_gap = 应收 − 已收（未回款）；real_profit = received − total_cost
    （实收口径毛利，负值=垫资中）。revenue/gross_profit 为理论口径（成本域注释），
    两口径并列展示，不做孰高孰低裁决。
    """

    ar_amount = serializers.SerializerMethodField()
    ar_received = serializers.SerializerMethodField()
    ar_gap = serializers.SerializerMethodField()
    real_profit = serializers.SerializerMethodField()

    class Meta(OrderCostSerializer.Meta):
        fields = OrderCostSerializer.Meta.fields + [
            'ar_amount', 'ar_received', 'ar_gap', 'real_profit']

    # 聚合注解无应收行时为 None（DRF default 不兜 None），统一兜 '0' 与报表口径一致
    def get_ar_amount(self, obj) -> str:
        return str(obj.ar_amount or 0)

    def get_ar_received(self, obj) -> str:
        return str(obj.ar_received or 0)

    def get_ar_gap(self, obj) -> str:
        return str((obj.ar_amount or 0) - (obj.ar_received or 0))

    def get_real_profit(self, obj) -> str:
        return str((obj.ar_received or 0) - (obj.total_cost or 0))


class OverheadSerializer(serializers.Serializer):
    """PUT /cost/orders/{order_id}/overhead 入参：制费/管理费总额（手工录入）。"""

    overhead_cost = serializers.DecimalField(
        max_digits=12, decimal_places=2, label='制费/管理费',
        error_messages={'invalid': '制费金额不合法', 'required': '请填写制费金额',
                        'null': '请填写制费金额'})

    def validate_overhead_cost(self, value):
        if value < 0:
            raise serializers.ValidationError('制费金额不能为负数')
        return value
