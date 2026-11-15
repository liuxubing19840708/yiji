from decimal import Decimal

from rest_framework import serializers

from apps.finance.models import CashEntry, Payable, SupplierPayment


class PayableSerializer(serializers.ModelSerializer):
    outstanding = serializers.SerializerMethodField()
    status_display = serializers.CharField(source='get_status_display', read_only=True)

    class Meta:
        model = Payable
        fields = ['id', 'payable_no', 'supplier', 'supplier_name', 'purchase',
                  'amount', 'paid_amount', 'outstanding', 'status', 'status_display',
                  'payable_at', 'due_at', 'remark', 'created_at']

    def get_outstanding(self, obj):
        return str(obj.amount - obj.paid_amount)


class PayableCreateSerializer(serializers.Serializer):
    supplier = serializers.IntegerField(required=False, allow_null=True)
    supplier_name = serializers.CharField(required=False, allow_blank=True, max_length=128)
    purchase = serializers.IntegerField(required=False, allow_null=True)
    amount = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=Decimal('0.01'))
    payable_at = serializers.DateField()
    due_at = serializers.DateField(required=False, allow_null=True)
    remark = serializers.CharField(required=False, allow_blank=True, max_length=255)

    def validate(self, attrs):
        if attrs.get('due_at') and attrs['due_at'] < attrs['payable_at']:
            raise serializers.ValidationError({'due_at': '到期日期不能早于应付日期'})
        return attrs


class SupplierPaymentSerializer(serializers.ModelSerializer):
    payable_no = serializers.CharField(source='payable.payable_no', read_only=True,
                                       default='')
    status_display = serializers.CharField(source='get_status_display', read_only=True)

    class Meta:
        model = SupplierPayment
        fields = ['id', 'payable', 'payable_no', 'supplier', 'supplier_name',
                  'amount', 'paid_at', 'method', 'transaction_ref', 'status',
                  'status_display', 'remark', 'voided_at', 'void_reason', 'created_at']


class SupplierPaymentCreateSerializer(serializers.Serializer):
    payable = serializers.IntegerField(required=False, allow_null=True)
    supplier = serializers.IntegerField(required=False, allow_null=True)
    supplier_name = serializers.CharField(required=False, allow_blank=True, max_length=128)
    amount = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=Decimal('0.01'))
    paid_at = serializers.DateTimeField(required=False, allow_null=True)
    method = serializers.ChoiceField(choices=SupplierPayment.Method.choices,
                                     default=SupplierPayment.Method.BANK)
    transaction_ref = serializers.CharField(required=False, allow_blank=True, max_length=64)
    remark = serializers.CharField(required=False, allow_blank=True, max_length=255)


class CashEntrySerializer(serializers.ModelSerializer):
    direction_display = serializers.CharField(source='get_direction_display', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)

    class Meta:
        model = CashEntry
        fields = ['id', 'entry_no', 'direction', 'direction_display', 'category',
                  'amount', 'occurred_at', 'counterparty', 'method', 'reference_no',
                  'status', 'status_display', 'remark', 'voided_at', 'void_reason',
                  'created_at']


class CashEntryCreateSerializer(serializers.Serializer):
    direction = serializers.ChoiceField(choices=CashEntry.Direction.choices)
    category = serializers.CharField(max_length=64, trim_whitespace=True)
    amount = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=Decimal('0.01'))
    occurred_at = serializers.DateTimeField(required=False, allow_null=True)
    counterparty = serializers.CharField(required=False, allow_blank=True, max_length=128)
    method = serializers.CharField(required=False, allow_blank=True, max_length=32)
    reference_no = serializers.CharField(required=False, allow_blank=True, max_length=64)
    remark = serializers.CharField(required=False, allow_blank=True, max_length=255)


class VoidSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=200, trim_whitespace=True)
