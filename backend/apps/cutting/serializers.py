"""裁片交接 API 输入输出。"""
from rest_framework import serializers

from .models import CuttingHandover


class HandoverCreateSerializer(serializers.Serializer):
    ticket_id = serializers.IntegerField(min_value=1)
    source_node_id = serializers.IntegerField(min_value=1)
    target_node_id = serializers.IntegerField(min_value=1)
    receiver_id = serializers.IntegerField(min_value=1)
    quantity = serializers.IntegerField(min_value=1)
    request_id = serializers.CharField(max_length=64, trim_whitespace=False)
    remark = serializers.CharField(max_length=255, required=False, allow_blank=True, default='')


class HandoverSerializer(serializers.ModelSerializer):
    ticket_no = serializers.CharField(source='ticket.qr_code', read_only=True)
    bundle_no = serializers.CharField(source='ticket.bundle_no', read_only=True)
    bed_no = serializers.CharField(source='ticket.bed_no', read_only=True)
    source_node_name = serializers.CharField(source='source_node.stage.name', read_only=True)
    target_node_name = serializers.CharField(source='target_node.stage.name', read_only=True)
    receiver_name = serializers.CharField(source='receiver.real_name', read_only=True)
    created_by_name = serializers.CharField(source='created_by.real_name', read_only=True)
    signed_by_name = serializers.CharField(source='signed_by.real_name', read_only=True, default='')
    outgoing_ledger_id = serializers.IntegerField(read_only=True)
    incoming_ledger_id = serializers.IntegerField(read_only=True)

    class Meta:
        model = CuttingHandover
        fields = ['id', 'company', 'ticket', 'ticket_no', 'bed_no', 'bundle_no',
                  'source_node', 'source_node_name', 'target_node', 'target_node_name',
                  'quantity', 'receiver', 'receiver_name', 'status', 'request_id', 'remark',
                  'created_by', 'created_by_name', 'created_at', 'signed_by', 'signed_by_name',
                  'signed_at', 'cancelled_by', 'cancelled_at', 'outgoing_ledger_id',
                  'incoming_ledger_id']
