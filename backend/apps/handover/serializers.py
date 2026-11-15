"""通用交接 API 输入输出。"""
from rest_framework import serializers

from .models import (Handover, HandoverEvent, HandoverItem, HandoverKind, HandoverPolicy)
from .timeout_services import timeout_info


def user_name(user, *, null_empty=False):
    if user is None:
        return None if null_empty else ''
    return user.real_name or user.username or user.phone or ''


def _request_id(value):
    if not value.strip():
        raise serializers.ValidationError('request_id 不能为空')
    return value


class RequestSerializer(serializers.Serializer):
    request_id = serializers.CharField(max_length=64, trim_whitespace=False)

    def validate_request_id(self, value):
        return _request_id(value)


class HandoverPolicySerializer(serializers.ModelSerializer):
    updated_by_name = serializers.SerializerMethodField()

    class Meta:
        model = HandoverPolicy
        fields = ['id', 'kind', 'mode', 'source_node', 'target_node', 'target_group_no',
                  'receiver', 'timeout_minutes', 'updated_by_name']

    def get_updated_by_name(self, obj):
        return user_name(obj.updated_by)


class HandoverPolicyInputSerializer(serializers.Serializer):
    kind = serializers.ChoiceField(choices=HandoverKind.choices, default=HandoverKind.CUTTING)
    mode = serializers.ChoiceField(choices=HandoverPolicy.Mode.choices,
                                   default=HandoverPolicy.Mode.OFF)
    source_node = serializers.IntegerField(min_value=1, required=False, allow_null=True)
    target_node = serializers.IntegerField(min_value=1, required=False, allow_null=True)
    target_group_no = serializers.IntegerField(min_value=1, required=False, allow_null=True)
    receiver = serializers.IntegerField(min_value=1, required=False, allow_null=True)
    timeout_minutes = serializers.IntegerField(min_value=1, default=120)


class HandoverEventSerializer(serializers.ModelSerializer):
    actor_name = serializers.SerializerMethodField()
    actual_user_name = serializers.SerializerMethodField()

    class Meta:
        model = HandoverEvent
        fields = ['id', 'action', 'actor', 'actor_name', 'actual_user', 'actual_user_name',
                  'reason', 'before', 'after', 'created_at']

    def get_actor_name(self, obj):
        return user_name(obj.actor)

    def get_actual_user_name(self, obj):
        return user_name(obj.actual_user, null_empty=True)


class HandoverItemSerializer(serializers.ModelSerializer):
    ticket_label = serializers.SerializerMethodField()

    class Meta:
        model = HandoverItem
        fields = ['id', 'ticket', 'ticket_label', 'quantity', 'received_quantity',
                  'difference_type', 'reason', 'out_ledger', 'in_ledger']

    def get_ticket_label(self, obj):
        ticket = obj.ticket
        return '/'.join(part for part in (ticket.bed_no, ticket.bundle_no, ticket.color) if part)


class HandoverSerializer(serializers.ModelSerializer):
    source_node_name = serializers.SerializerMethodField()
    target_node_name = serializers.SerializerMethodField()
    receiver_name = serializers.SerializerMethodField()
    created_by_name = serializers.SerializerMethodField()
    received_by_name = serializers.SerializerMethodField()
    total_quantity = serializers.IntegerField(read_only=True)
    received_quantity = serializers.IntegerField(read_only=True)
    has_discrepancy = serializers.BooleanField(read_only=True)
    policy_mode = serializers.SerializerMethodField()
    timeout_minutes = serializers.SerializerMethodField()
    due_at = serializers.SerializerMethodField()
    is_overdue = serializers.SerializerMethodField()
    overdue_minutes = serializers.SerializerMethodField()
    items = HandoverItemSerializer(many=True, read_only=True)
    events = HandoverEventSerializer(many=True, read_only=True)

    class Meta:
        model = Handover
        fields = ['id', 'handover_no', 'batch_no', 'kind', 'status',
                  'source_node', 'source_node_name', 'target_node', 'target_node_name',
                  'target_group_no', 'receiver', 'receiver_name', 'created_by', 'created_by_name',
                  'received_by', 'received_by_name', 'received_at', 'reject_reason',
                  'has_discrepancy', 'total_quantity', 'received_quantity', 'remark',
                  'policy_mode', 'timeout_minutes', 'due_at', 'is_overdue', 'overdue_minutes',
                  'created_at', 'items', 'events']

    def _timeout(self, obj):
        cached = getattr(obj, '_handover_timeout', None)
        if cached is None:
            cached = timeout_info(obj)
            obj._handover_timeout = cached
        return cached

    def get_policy_mode(self, obj):
        return self._timeout(obj)['policy_mode']

    def get_timeout_minutes(self, obj):
        return self._timeout(obj)['timeout_minutes']

    def get_due_at(self, obj):
        return self._timeout(obj)['due_at']

    def get_is_overdue(self, obj):
        return self._timeout(obj)['is_overdue']

    def get_overdue_minutes(self, obj):
        return self._timeout(obj)['overdue_minutes']

    def _node_name(self, node):
        return node.stage_name or node.stage.name

    def get_source_node_name(self, obj):
        return self._node_name(obj.source_node)

    def get_target_node_name(self, obj):
        return self._node_name(obj.target_node)

    def get_receiver_name(self, obj):
        return user_name(obj.receiver, null_empty=True)

    def get_created_by_name(self, obj):
        return user_name(obj.created_by)

    def get_received_by_name(self, obj):
        return user_name(obj.received_by, null_empty=True)


class HandoverItemInputSerializer(serializers.Serializer):
    ticket = serializers.IntegerField(min_value=1)
    quantity = serializers.IntegerField(min_value=1)


class HandoverTargetInputSerializer(serializers.Serializer):
    receiver = serializers.IntegerField(min_value=1, required=False, allow_null=True)
    target_group_no = serializers.IntegerField(min_value=1, required=False, allow_null=True)
    items = HandoverItemInputSerializer(many=True, allow_empty=False)

    def validate(self, attrs):
        if attrs.get('receiver') is None and attrs.get('target_group_no') is None:
            raise serializers.ValidationError('receiver 与 target_group_no 至少提供一项')
        return attrs


class HandoverCreateSerializer(RequestSerializer):
    kind = serializers.ChoiceField(choices=HandoverKind.choices)
    source_node = serializers.IntegerField(min_value=1)
    target_node = serializers.IntegerField(min_value=1)
    remark = serializers.CharField(max_length=255, required=False, allow_blank=True, default='')
    targets = HandoverTargetInputSerializer(many=True, allow_empty=False)

    def validate(self, attrs):
        seen = set()
        for target in attrs['targets']:
            for item in target['items']:
                ticket = item['ticket']
                if ticket in seen:
                    raise serializers.ValidationError(
                        {'targets': f'菲票 {ticket} 在一次请求中不能跨 target 重复分配'})
                seen.add(ticket)
        return attrs


class ReceiveItemInputSerializer(serializers.Serializer):
    id = serializers.IntegerField(min_value=1)
    received_quantity = serializers.IntegerField(min_value=0)
    difference_type = serializers.ChoiceField(choices=HandoverItem.DifferenceType.choices,
                                              required=False, allow_blank=True, default='')
    reason = serializers.CharField(max_length=255, required=False, allow_blank=True, default='')


class HandoverReceiveSerializer(RequestSerializer):
    reason = serializers.CharField(max_length=255, required=False, allow_blank=True, default='')
    items = ReceiveItemInputSerializer(many=True, required=False, default=list)

    def validate(self, attrs):
        ids = [item['id'] for item in attrs['items']]
        if len(ids) != len(set(ids)):
            raise serializers.ValidationError({'items': '差异明细不能重复'})
        return attrs


class HandoverRejectSerializer(RequestSerializer):
    reason = serializers.CharField(max_length=255, allow_blank=False, trim_whitespace=True)


class HandoverReassignSerializer(RequestSerializer):
    receiver = serializers.IntegerField(min_value=1)
    reason = serializers.CharField(max_length=255, allow_blank=False, trim_whitespace=True)
