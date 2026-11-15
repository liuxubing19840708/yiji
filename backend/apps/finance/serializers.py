"""应收域序列化器（P2 finance 域，契约 §四 端点）。

金额字段 DRF 默认输出字符串（两位/三位小数原样），与全项目报表口径一致；
outstanding=amount-received_amount 为计算字段（未收余额）。
"""
from rest_framework import serializers

from apps.finance.models import Receivable, ReceiptLine


class ReceivableSerializer(serializers.ModelSerializer):
    customer_name = serializers.CharField(source='customer.name', read_only=True,
                                          default='')
    order_no = serializers.CharField(source='order.order_no', read_only=True,
                                     default='')
    outstanding = serializers.SerializerMethodField('get_outstanding')
    status_display = serializers.CharField(source='get_status_display',
                                           read_only=True)

    class Meta:
        model = Receivable
        fields = ['id', 'receivable_no', 'customer', 'customer_name',
                  'order', 'order_no', 'shipment_id',
                  'amount', 'received_amount', 'outstanding',
                  'status', 'status_display', 'remark', 'created_at']
        read_only_fields = fields

    def get_outstanding(self, obj) -> str:
        return str(obj.amount - obj.received_amount)


class ReceivableCreateSerializer(serializers.Serializer):
    """POST /finance/receivables 入参（手工建应收；出货域走 services.create_receivable）。

    shipment_id 为出货域预留透传列（手工端一般不传）；customer/order 归属
    校验在视图层做（公司隔离 + 404 语义），serializer 只管形态。
    """

    customer = serializers.IntegerField(label='客户ID',
                                        error_messages={'required': '请选择客户',
                                                        'null': '请选择客户',
                                                        'invalid': '客户ID不合法'})
    order = serializers.IntegerField(label='订单ID', required=False, allow_null=True)
    shipment_id = serializers.IntegerField(label='出货单ID', required=False,
                                           allow_null=True)
    amount = serializers.DecimalField(
        max_digits=12, decimal_places=2, label='应收金额',
        error_messages={'invalid': '应收金额不合法', 'required': '请填写应收金额',
                        'null': '请填写应收金额'})
    remark = serializers.CharField(label='备注', required=False, allow_blank=True,
                                   max_length=255)

    def validate_amount(self, value):
        if value <= 0:
            raise serializers.ValidationError('应收金额必须大于 0')
        return value


class ReceiptLineSerializer(serializers.ModelSerializer):
    method_display = serializers.CharField(source='get_method_display',
                                           read_only=True)
    handler_name = serializers.CharField(source='handler', read_only=True)

    class Meta:
        model = ReceiptLine
        fields = ['id', 'receivable', 'amount', 'method', 'method_display',
                  'received_at', 'handler', 'handler_name', 'remark', 'created_at']
        read_only_fields = fields


class ReceiptCreateSerializer(serializers.Serializer):
    """POST /finance/receivables/{id}/receipts 入参：核销（收款）。

    amount>0；超额收款 400 拦截在 services.add_receipt（金额级硬拦）。
    """

    METHOD_CHOICES = ReceiptLine.Method.choices

    amount = serializers.DecimalField(
        max_digits=12, decimal_places=2, label='核销金额',
        error_messages={'invalid': '核销金额不合法', 'required': '请填写核销金额',
                        'null': '请填写核销金额'})
    method = serializers.ChoiceField(choices=METHOD_CHOICES, default=ReceiptLine.Method.CASH,
                                     label='收款方式',
                                     error_messages={'invalid_choice': '收款方式不合法'})
    received_at = serializers.DateTimeField(label='收款时间', required=False,
                                            allow_null=True)
    handler = serializers.CharField(label='经手人', required=False, allow_blank=True,
                                    allow_null=True, max_length=32)
    remark = serializers.CharField(label='备注', required=False, allow_blank=True,
                                   max_length=255)

    def validate_amount(self, value):
        if value <= 0:
            raise serializers.ValidationError('核销金额必须大于 0')
        return value


class BatchReceiptSerializer(serializers.Serializer):
    """POST /finance/customers/{customer_id}/receipts-batch 入参（批量核销）。

    total=客户一笔到账总额；FIFO 分配给该客户未结清应收（services.batch_receipt_by_customer，
    溢收款 400 硬拦）。method/handler/remark 语义同单笔核销。
    """

    METHOD_CHOICES = ReceiptLine.Method.choices

    total = serializers.DecimalField(
        max_digits=12, decimal_places=2, label='收款总额',
        error_messages={'invalid': '收款金额不合法', 'required': '请填写收款总额',
                        'null': '请填写收款总额'})
    method = serializers.ChoiceField(choices=METHOD_CHOICES,
                                     default=ReceiptLine.Method.CASH)
    received_at = serializers.DateTimeField(label='收款时间', required=False,
                                            allow_null=True)
    handler = serializers.CharField(label='经手人', required=False, allow_blank=True,
                                    allow_null=True, max_length=32)
    remark = serializers.CharField(label='备注', required=False, allow_blank=True,
                                   max_length=255)

    def validate_total(self, value):
        if value <= 0:
            raise serializers.ValidationError('收款金额必须大于 0')
        return value
