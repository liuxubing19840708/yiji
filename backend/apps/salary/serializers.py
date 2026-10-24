"""薪酬结算序列化器。"""
from rest_framework import serializers

from apps.accounts.models import UserCompany
from apps.salary.models import SalaryAdjust, SettleApply, Settlement


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
