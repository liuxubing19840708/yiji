"""薪酬结算序列化器。"""
from decimal import Decimal

from django.utils import timezone
from rest_framework import serializers

from apps.accounts.models import UserCompany
from apps.salary.models import (SalaryAdjust, SalaryPayment,
                                SalaryPaymentAllocation, SettleApply, Settlement)
from apps.salary.payments import (settlement_outstanding,
                                  settlement_paid_amount)


class SettlementSerializer(serializers.ModelSerializer):
    user_name = serializers.CharField(source='user.real_name', default='')
    created_by_name = serializers.CharField(
        source='created_by.real_name', default='')
    status_display = serializers.CharField(
        source='get_status_display', read_only=True)

    class Meta:
        model = Settlement
        fields = ['id', 'company', 'user', 'user_name', 'period_start',
                  'period_end', 'employee_count', 'amount', 'wage_detail',
                  'status', 'status_display', 'created_by', 'created_by_name',
                  'created_at']


class SalaryAdjustSerializer(serializers.ModelSerializer):
    user_name = serializers.CharField(source='user.real_name', default='')
    operator_name = serializers.CharField(
        source='operator.real_name', default='')

    class Meta:
        model = SalaryAdjust
        fields = ['id', 'company', 'user', 'user_name', 'amount', 'reason',
                  'operator', 'operator_name', 'created_at']


class SettleApplySerializer(serializers.ModelSerializer):
    applicant_name = serializers.CharField(
        source='applicant.real_name', default='')
    user_name = serializers.CharField(source='user.real_name', default='')
    reviewer_name = serializers.CharField(
        source='reviewer.real_name', default='')
    paid_by_name = serializers.CharField(
        source='paid_by.real_name', default='')
    status_display = serializers.CharField(
        source='get_status_display', read_only=True)
    # 待谁审批（2026-10-21 用户需求）：待审核=待 管理员A、B 审批；待发放=已审核待 X 发放
    reviewer_hint = serializers.SerializerMethodField()

    class Meta:
        model = SettleApply
        fields = ['id', 'company', 'applicant', 'applicant_name', 'user',
                  'user_name', 'period_start', 'period_end', 'amount',
                  'status', 'status_display', 'reviewer_hint', 'note', 'reviewer',
                  'reviewer_name', 'reviewed_at', 'reject_reason', 'paid_by',
                  'paid_by_name', 'paid_at', 'payer', 'void_reason',
                  'created_at']

    def get_reviewer_hint(self, obj):
        if obj.status == SettleApply.Status.PENDING:
            names = self.context.get('admin_names')
            if names is None:  # 上下文未带（如直调序列化器）：现场查公司管理员
                names = list(
                    UserCompany.objects.filter(
                        company_id=obj.company_id, status=UserCompany.Status.ACTIVE,
                        post__is_admin=True, post__is_deleted=False)
                    .select_related('user')
                    .values_list('user__real_name', flat=True))
            names = [n for n in names if n][:3] or ['管理员']
            return '待 ' + '、'.join(names) + ' 审批'
        if obj.status == SettleApply.Status.APPROVED and obj.reviewer_id:
            return '已审核，待 ' + (obj.reviewer.real_name or '管理员') + ' 发放'
        return ''


class SettleApplyCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = SettleApply
        fields = ['period_start', 'period_end', 'note']


class AdjustCreateSerializer(serializers.ModelSerializer):
    user_id = serializers.IntegerField()

    class Meta:
        model = SalaryAdjust
        fields = ['user_id', 'amount', 'reason']


class SettlementCreateSerializer(serializers.Serializer):
    period_start = serializers.DateField()
    period_end = serializers.DateField()
    user_id = serializers.IntegerField(required=False, allow_null=True)


class SalaryPaymentAllocationSerializer(serializers.ModelSerializer):
    settlement_amount = serializers.DecimalField(
        source='settlement.amount', max_digits=12, decimal_places=2,
        read_only=True)
    paid_amount = serializers.SerializerMethodField()
    outstanding = serializers.SerializerMethodField()

    class Meta:
        model = SalaryPaymentAllocation
        fields = ['id', 'settlement', 'amount', 'settlement_amount',
                  'paid_amount', 'outstanding']

    def get_paid_amount(self, obj):
        return settlement_paid_amount(obj.settlement, obj.payment.employee_id)

    def get_outstanding(self, obj):
        return settlement_outstanding(obj.settlement, obj.payment.employee_id)


class SalaryPaymentSerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(
        source='employee.real_name', default='')
    operator_name = serializers.CharField(
        source='operator.real_name', default='')
    voided_by_name = serializers.CharField(
        source='voided_by.real_name', default='')
    status_display = serializers.CharField(
        source='get_status_display', read_only=True)
    allocations = SalaryPaymentAllocationSerializer(many=True, read_only=True)

    class Meta:
        model = SalaryPayment
        fields = ['id', 'company', 'employee', 'employee_name', 'paid_at',
                  'method', 'transaction_ref', 'note', 'operator',
                  'operator_name', 'amount', 'status', 'status_display',
                  'void_reason', 'voided_by', 'voided_by_name', 'voided_at',
                  'allocations', 'created_at']


class SalaryPaymentAllocationInputSerializer(serializers.Serializer):
    settlement_id = serializers.IntegerField(min_value=1)
    amount = serializers.DecimalField(
        max_digits=12, decimal_places=2, min_value=Decimal('0.01'))


class SalaryPaymentCreateSerializer(serializers.Serializer):
    employee_id = serializers.IntegerField()
    paid_at = serializers.DateTimeField(default=timezone.now)
    method = serializers.CharField(max_length=32, trim_whitespace=True)
    transaction_ref = serializers.CharField(max_length=100, trim_whitespace=True)
    note = serializers.CharField(
        max_length=500, required=False, allow_blank=True, default='')
    amount = serializers.DecimalField(
        max_digits=12, decimal_places=2, min_value=Decimal('0.01'))
    allocations = SalaryPaymentAllocationInputSerializer(
        many=True, required=False, default=list)


class SalaryPaymentVoidSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=500, trim_whitespace=True)

    def validate_reason(self, value):
        if not value:
            raise serializers.ValidationError('作废原因不能为空')
        return value
