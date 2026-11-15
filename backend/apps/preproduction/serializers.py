"""产前域只读输出；请求数据由服务层校验，防止序列化器绕开业务规则。"""
from rest_framework import serializers

from .models import (
    BriefingConfirmation, BriefingReceipt, BriefingTarget, CraftTemplate,
    PreproductionApproval, PreproductionAuditEvent, ReviewIssue, SampleRecord,
    SampleTask, TechPackVersion,
)


class SampleRecordSerializer(serializers.ModelSerializer):
    operator_name = serializers.CharField(source='operator.real_name', read_only=True, default='')

    class Meta:
        model = SampleRecord
        fields = ['id', 'task', 'result', 'note', 'attachments', 'operator',
                  'operator_name', 'occurred_at', 'created_at']


class SampleTaskSerializer(serializers.ModelSerializer):
    records = SampleRecordSerializer(many=True, read_only=True)
    assignee_name = serializers.CharField(source='assignee.real_name', read_only=True, default='')

    class Meta:
        model = SampleTask
        fields = ['id', 'tech_pack', 'title', 'assignee', 'assignee_name', 'status',
                  'records', 'created_at', 'updated_at']


class ReviewIssueSerializer(serializers.ModelSerializer):
    raised_by_name = serializers.CharField(source='raised_by.real_name', read_only=True, default='')
    handled_by_name = serializers.CharField(source='reviewed_by.real_name', read_only=True, default='')

    class Meta:
        model = ReviewIssue
        fields = ['id', 'tech_pack', 'title', 'detail', 'status', 'rectification',
                  'review_note', 'raised_by', 'raised_by_name', 'reviewed_by',
                  'handled_by_name', 'reviewed_at', 'created_at', 'updated_at']


class BriefingReceiptSerializer(serializers.ModelSerializer):
    class Meta:
        model = BriefingReceipt
        fields = ['id', 'target', 'employee', 'signed_at', 'remark', 'created_at']


class BriefingTargetSerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(source='employee.real_name', read_only=True, default='')
    receipt = BriefingReceiptSerializer(read_only=True)

    class Meta:
        model = BriefingTarget
        fields = ['id', 'tech_pack', 'employee', 'employee_name', 'note', 'receipt', 'created_at']


class BriefingConfirmationSerializer(serializers.ModelSerializer):
    confirmed_by_name = serializers.CharField(source='confirmed_by.real_name', read_only=True, default='')

    class Meta:
        model = BriefingConfirmation
        fields = ['id', 'tech_pack', 'content_hash', 'note', 'confirmed_by',
                  'confirmed_by_name', 'confirmed_at', 'created_at']


class CraftTemplateSerializer(serializers.ModelSerializer):
    is_system = serializers.BooleanField(read_only=True)
    created_by_name = serializers.CharField(source='created_by.real_name', read_only=True, default='')

    class Meta:
        model = CraftTemplate
        fields = ['id', 'kind', 'name', 'content', 'is_system', 'created_by',
                  'created_by_name', 'created_at', 'updated_at']


class ApprovalSerializer(serializers.ModelSerializer):
    decided_by_name = serializers.CharField(source='decided_by.real_name', read_only=True, default='')

    class Meta:
        model = PreproductionApproval
        fields = ['id', 'order', 'tech_pack', 'content_hash', 'status', 'reason',
                  'decided_by', 'decided_by_name', 'decided_at', 'created_at']


class TechPackSerializer(serializers.ModelSerializer):
    order_no = serializers.CharField(source='order.order_no', read_only=True)
    style_no = serializers.CharField(source='style.style_no', read_only=True)
    sample_tasks = SampleTaskSerializer(many=True, read_only=True)
    review_issues = ReviewIssueSerializer(many=True, read_only=True)
    briefing_targets = BriefingTargetSerializer(many=True, read_only=True)
    briefing_confirmations = BriefingConfirmationSerializer(many=True, read_only=True)
    approvals = ApprovalSerializer(many=True, read_only=True)

    class Meta:
        model = TechPackVersion
        fields = ['id', 'order', 'order_no', 'style', 'style_no', 'version_no',
                  'pattern_no', 'pattern_files', 'measurements', 'tolerances',
                  'attachments', 'requirements', 'challenges', 'common_mistakes',
                  'quality_standards', 'content_hash', 'revision', 'is_current',
                  'sample_tasks', 'review_issues', 'briefing_targets',
                  'briefing_confirmations', 'approvals',
                  'created_by', 'created_at', 'updated_at']


class AuditEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = PreproductionAuditEvent
        fields = ['id', 'action', 'entity_type', 'entity_id', 'tech_pack', 'actor',
                  'actor_name', 'before', 'after', 'message', 'created_at']
