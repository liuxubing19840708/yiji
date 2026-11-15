from rest_framework import serializers

from apps.manufacturing.models import QuantityLedger
from apps.manufacturing.serializers import LedgerSerializer

from .models import (FinishedInbound, FinishedInboundLine, PackingBox, PackingBoxLine,
                     PackingTask)


class RequestSerializer(serializers.Serializer):
    request_id = serializers.CharField(max_length=64, allow_blank=False, trim_whitespace=True)


class ReverseRequestSerializer(RequestSerializer):
    reason = serializers.CharField(max_length=255, allow_blank=False, trim_whitespace=True)


class PackingBoxLineSerializer(serializers.ModelSerializer):
    bundle_no = serializers.CharField(source='ticket.bundle_no', read_only=True)
    bed_no = serializers.CharField(source='ticket.bed_no', read_only=True)

    class Meta:
        model = PackingBoxLine
        fields = ['id', 'ticket', 'bed_no', 'bundle_no', 'color', 'size', 'quantity']


class PackingBoxSerializer(serializers.ModelSerializer):
    lines = PackingBoxLineSerializer(many=True, read_only=True)
    total_quantity = serializers.SerializerMethodField()

    class Meta:
        model = PackingBox
        fields = ['id', 'box_no', 'total_quantity', 'lines']

    def get_total_quantity(self, obj):
        return sum(line.quantity for line in obj.lines.all())


class DocumentLedgerSerializerMixin:
    """列表保持轻量；仅单证详情读取原流水及其反向流水。"""

    def get_reverser_name(self, obj):
        return str(obj.reverser) if obj.reverser else ''

    def get_ledger_entries(self, obj):
        if not self.context.get('include_ledger'):
            return []
        source_prefix = ('finished_goods.packing_task' if hasattr(obj, 'task_no')
                         else 'finished_goods.inbound')
        document_no = getattr(obj, 'task_no', None) or obj.document_no
        queryset = (QuantityLedger.objects.filter(
            company=obj.company, source=f'{source_prefix}:{document_no}')
            .select_related('node__stage', 'ticket', 'operator', 'reversal_of')
            .order_by('id'))
        return LedgerSerializer(queryset, many=True).data


class PackingTaskSerializer(DocumentLedgerSerializerMixin, serializers.ModelSerializer):
    ledger_entries = serializers.SerializerMethodField()
    reverser_name = serializers.SerializerMethodField()
    boxes = PackingBoxSerializer(many=True, read_only=True)
    route_code = serializers.CharField(source='route.code', read_only=True)
    total_quantity = serializers.SerializerMethodField()
    status_display = serializers.CharField(source='get_status_display', read_only=True)

    class Meta:
        model = PackingTask
        fields = ['id', 'task_no', 'route', 'route_code', 'packing_node', 'inbound_node',
                  'status', 'status_display', 'total_quantity', 'boxes', 'remark', 'request_id',
                  'approval_request_id', 'creator', 'approver', 'approved_at',
                  'reversal_request_id', 'reversal_reason', 'reverser', 'reverser_name',
                  'reversed_at', 'ledger_entries', 'created_at']

    def get_total_quantity(self, obj):
        return sum(line.quantity for box in obj.boxes.all() for line in box.lines.all())


class PackingTaskCreateSerializer(RequestSerializer):
    route_id = serializers.IntegerField(min_value=1)
    task_no = serializers.CharField(required=False, allow_blank=True, max_length=64)
    boxes = serializers.ListField(child=serializers.DictField(), allow_empty=False)
    remark = serializers.CharField(required=False, allow_blank=True, max_length=255)


class InboundLineSerializer(serializers.ModelSerializer):
    box_no = serializers.CharField(source='box.box_no', read_only=True)
    bed_no = serializers.CharField(source='ticket.bed_no', read_only=True)
    bundle_no = serializers.CharField(source='ticket.bundle_no', read_only=True)

    class Meta:
        model = FinishedInboundLine
        fields = ['id', 'box', 'box_no', 'ticket', 'bed_no', 'bundle_no', 'color', 'size',
                  'quantity']


class FinishedInboundSerializer(DocumentLedgerSerializerMixin, serializers.ModelSerializer):
    ledger_entries = serializers.SerializerMethodField()
    reverser_name = serializers.SerializerMethodField()
    lines = InboundLineSerializer(many=True, read_only=True)
    route_code = serializers.CharField(source='route.code', read_only=True)
    total_quantity = serializers.SerializerMethodField()
    status_display = serializers.CharField(source='get_status_display', read_only=True)

    class Meta:
        model = FinishedInbound
        fields = ['id', 'document_no', 'route', 'route_code', 'packing_node', 'inbound_node',
                  'warehouse_name', 'location_name', 'status', 'status_display', 'total_quantity',
                  'lines', 'remark', 'request_id', 'approval_request_id', 'creator', 'approver',
                  'approved_at', 'reversal_request_id', 'reversal_reason', 'reverser',
                  'reverser_name', 'reversed_at', 'ledger_entries', 'created_at']

    def get_total_quantity(self, obj):
        return sum(line.quantity for line in obj.lines.all())


class FinishedInboundCreateSerializer(RequestSerializer):
    route_id = serializers.IntegerField(min_value=1)
    document_no = serializers.CharField(required=False, allow_blank=True, max_length=64)
    lines = serializers.ListField(child=serializers.DictField(), allow_empty=False)
    warehouse_name = serializers.CharField(required=False, allow_blank=True, max_length=128)
    location_name = serializers.CharField(required=False, allow_blank=True, max_length=128)
    remark = serializers.CharField(required=False, allow_blank=True, max_length=255)
