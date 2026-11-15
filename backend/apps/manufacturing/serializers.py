"""生产路线与数量流水的 API 输入/输出。"""
from rest_framework import serializers

from .models import ProductionRoute, ProductionStage, QuantityLedger, RouteNode


class StageSerializer(serializers.ModelSerializer):
    in_use = serializers.SerializerMethodField()

    def get_in_use(self, obj):
        if hasattr(obj, 'in_use'):
            return obj.in_use
        return obj.route_nodes.exists()

    class Meta:
        model = ProductionStage
        fields = ['id', 'code', 'name', 'stage_type', 'is_active', 'in_use',
                  'created_at', 'updated_at']


class StageInputSerializer(serializers.Serializer):
    code = serializers.CharField(max_length=32, trim_whitespace=True)
    name = serializers.CharField(max_length=64, trim_whitespace=True)
    stage_type = serializers.ChoiceField(choices=ProductionStage.StageType.choices)


class RouteNodeSerializer(serializers.ModelSerializer):
    class Meta:
        model = RouteNode
        fields = ['id', 'stage', 'stage_code', 'stage_name', 'stage_type', 'sequence',
                  'required', 'allow_parallel', 'allow_skip']


class RouteNodeInputSerializer(serializers.Serializer):
    stage_id = serializers.IntegerField(min_value=1)
    sequence = serializers.IntegerField(min_value=1)
    required = serializers.BooleanField(default=True)
    allow_parallel = serializers.BooleanField(default=False)
    allow_skip = serializers.BooleanField(default=False)


class RouteSerializer(serializers.ModelSerializer):
    nodes = RouteNodeSerializer(many=True, read_only=True)
    instance_count = serializers.IntegerField(read_only=True, default=0)
    configuration_warnings = serializers.SerializerMethodField()

    def get_configuration_warnings(self, obj):
        if not obj.is_template:
            return []
        nodes = list(obj.nodes.all())
        types = [node.stage_type for node in nodes]
        labels = {'final_inspection': '尾查/成品检验', 'packing': '包装',
                  'finished_inbound': '成品入库'}
        warnings = [f'缺少{label}节点，不能形成完整成品闭环'
                    for key, label in labels.items() if key not in types]
        inactive = [node.stage_name for node in nodes if not node.stage.is_active]
        if inactive:
            warnings.append(f'包含已停用阶段：{"、".join(inactive)}')
        positions = {key: types.index(key) for key in labels if key in types}
        if ('final_inspection' in positions and 'packing' in positions
                and positions['final_inspection'] > positions['packing']):
            warnings.append('尾查/成品检验应排在包装之前')
        if ('packing' in positions and 'finished_inbound' in positions
                and positions['packing'] > positions['finished_inbound']):
            warnings.append('包装应排在成品入库之前')
        return warnings

    class Meta:
        model = ProductionRoute
        fields = ['id', 'code', 'name', 'is_template', 'is_active', 'version',
                  'previous_version', 'order', 'production', 'source_template', 'nodes',
                  'instance_count', 'configuration_warnings', 'created_at', 'updated_at']


class RouteInputSerializer(serializers.Serializer):
    code = serializers.CharField(max_length=64, trim_whitespace=True)
    name = serializers.CharField(max_length=64, trim_whitespace=True)
    nodes = RouteNodeInputSerializer(many=True, allow_empty=False)


class RouteUpdateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=64, trim_whitespace=True)
    nodes = RouteNodeInputSerializer(many=True, allow_empty=False)


class ActiveStatusSerializer(serializers.Serializer):
    is_active = serializers.BooleanField()


class RouteCopySerializer(serializers.Serializer):
    code = serializers.CharField(max_length=64, trim_whitespace=True)
    name = serializers.CharField(max_length=64, trim_whitespace=True)
    nodes = RouteNodeInputSerializer(many=True, allow_empty=False, required=False)


class InstantiateRouteSerializer(serializers.Serializer):
    order_id = serializers.IntegerField(min_value=1, required=False)
    production_id = serializers.IntegerField(min_value=1, required=False)
    code = serializers.CharField(max_length=64, trim_whitespace=True, required=False)
    name = serializers.CharField(max_length=64, trim_whitespace=True, required=False)

    def validate(self, attrs):
        if bool(attrs.get('order_id')) == bool(attrs.get('production_id')):
            raise serializers.ValidationError('order_id 与 production_id 必须且只能提供一个')
        return attrs


class StageActionSerializer(serializers.Serializer):
    ticket_id = serializers.IntegerField(min_value=1)
    node_id = serializers.IntegerField(min_value=1)
    to_node_id = serializers.IntegerField(min_value=1, required=False)
    quantity = serializers.IntegerField(min_value=1)
    request_id = serializers.CharField(max_length=60, trim_whitespace=True)
    source = serializers.CharField(max_length=128, required=False, allow_blank=True)
    reason = serializers.CharField(max_length=255, required=False, allow_blank=True)


class ReverseSerializer(serializers.Serializer):
    request_id = serializers.CharField(max_length=64, trim_whitespace=True)
    reason = serializers.CharField(max_length=255, required=False, allow_blank=True)


class LedgerSerializer(serializers.ModelSerializer):
    node_name = serializers.CharField(source='node.stage.name', read_only=True)
    node_sequence = serializers.IntegerField(source='node.sequence', read_only=True)
    bed_no = serializers.CharField(source='ticket.bed_no', read_only=True)
    bundle_no = serializers.CharField(source='ticket.bundle_no', read_only=True)
    event_type_display = serializers.CharField(source='get_event_type_display', read_only=True)
    operator_name = serializers.SerializerMethodField()

    def get_operator_name(self, obj):
        return str(obj.operator) if obj.operator else ''

    class Meta:
        model = QuantityLedger
        fields = ['id', 'route', 'node', 'node_name', 'node_sequence', 'order', 'production',
                  'ticket', 'bed_no', 'bundle_no', 'color', 'size', 'event_type',
                  'event_type_display', 'quantity', 'source', 'operator', 'operator_name',
                  'related_entry',
                  'reversal_of', 'request_id', 'reason', 'created_at']
