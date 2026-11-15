"""质检序列化器（GAP-1）。"""
from rest_framework import serializers

from apps.quality.models import QualityInspection, QualityRecord


class QualityRecordSerializer(serializers.ModelSerializer):
    """列表/详情：携带菲票与工序展示字段。"""

    ticket_bundle_no = serializers.CharField(source='ticket.bundle_no', read_only=True)
    ticket_bed_no = serializers.CharField(source='ticket.bed_no', read_only=True)
    ticket_color = serializers.CharField(source='ticket.color', read_only=True)
    ticket_size = serializers.CharField(source='ticket.size', read_only=True)
    ticket_qty = serializers.IntegerField(source='ticket.qty', read_only=True)
    style_no = serializers.SerializerMethodField()
    process_name = serializers.CharField(source='process.name', read_only=True, default=None)
    producer_name = serializers.SerializerMethodField()
    inspector_name = serializers.SerializerMethodField()
    status_display = serializers.CharField(source='get_status_display', read_only=True)

    class Meta:
        model = QualityRecord
        fields = [
            'id', 'ticket', 'ticket_bundle_no', 'ticket_bed_no', 'ticket_color',
            'ticket_size', 'ticket_qty', 'style_no',
            'process', 'process_name', 'defect_qty', 'reason', 'status', 'status_display',
            'received_qty', 'received_at', 'producer', 'producer_name',
            'inspector', 'inspector_name', 'inspected_at', 'remark', 'created_at',
        ]
        read_only_fields = ['status', 'received_qty', 'received_at', 'inspector', 'inspected_at']

    def get_style_no(self, obj):
        prod = obj.ticket.production
        return getattr(getattr(prod, 'style', None), 'style_no', '') or ''

    def get_producer_name(self, obj):
        return (obj.producer.real_name or obj.producer.username) if obj.producer else None

    def get_inspector_name(self, obj):
        return (obj.inspector.real_name or obj.inspector.username) if obj.inspector else None

    def validate_defect_qty(self, value):
        if value <= 0:
            raise serializers.ValidationError('返修数量必须大于 0')
        return value

    def validate(self, attrs):
        ticket = attrs.get('ticket') or (self.instance and self.instance.ticket)
        if ticket is not None:
            defect = attrs.get('defect_qty',
                               self.instance.defect_qty if self.instance else 0)
            if defect > ticket.qty:
                raise serializers.ValidationError(
                    {'defect_qty': f'返修数量不能超过扎数 {ticket.qty}'})
        reason = attrs.get('reason', '')
        if self.instance is None and not str(reason).strip():
            raise serializers.ValidationError({'reason': '返修原因必填'})
        return attrs


class QualityReceiveSerializer(serializers.Serializer):
    """收货：收回数量 + 可选返修工人补充指定。"""

    received_qty = serializers.IntegerField(min_value=1)
    producer = serializers.IntegerField(required=False, allow_null=True)


class QualityFinishSerializer(serializers.Serializer):
    """完成：可附处理结果备注。"""

    remark = serializers.CharField(required=False, allow_blank=True, max_length=200)


class QualityInspectionSerializer(serializers.ModelSerializer):
    ticket_bundle_no = serializers.CharField(source='ticket.bundle_no', read_only=True)
    ticket_bed_no = serializers.CharField(source='ticket.bed_no', read_only=True)
    node_name = serializers.CharField(source='node.stage.name', read_only=True)
    node_sequence = serializers.IntegerField(source='node.sequence', read_only=True)
    rework_node_name = serializers.CharField(
        source='rework_node.stage.name', read_only=True, default=None)
    inspector_name = serializers.SerializerMethodField()
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    inspection_type_display = serializers.CharField(
        source='get_inspection_type_display', read_only=True)

    class Meta:
        model = QualityInspection
        fields = [
            'id', 'ticket', 'ticket_bed_no', 'ticket_bundle_no', 'route', 'node',
            'node_name', 'node_sequence', 'inspection_type', 'inspection_type_display',
            'status', 'status_display', 'inspected', 'qualified', 'rework', 'scrapped',
            'rework_node', 'rework_node_name', 'reinspection_of', 'reason',
            'inspector', 'inspector_name', 'inspected_at', 'confirmed_by', 'confirmed_at',
            'request_id', 'create_request_id', 'created_at',
        ]
        read_only_fields = [
            'route', 'status', 'inspector', 'inspected_at', 'confirmed_by', 'confirmed_at',
            'request_id',
        ]

    def get_inspector_name(self, obj):
        return (obj.inspector.real_name or obj.inspector.username) if obj.inspector else None


class QualityInspectionConfirmSerializer(serializers.Serializer):
    request_id = serializers.CharField(max_length=60, trim_whitespace=False)

    def validate_request_id(self, value):
        if not value.strip():
            raise serializers.ValidationError('request_id 不能为空')
        return value
