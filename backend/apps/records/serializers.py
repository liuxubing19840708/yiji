"""扫菲登记序列化器。"""
from rest_framework import serializers

from apps.records.models import Record


class RecordSerializer(serializers.ModelSerializer):
    """登记记录只读序列化：带菲票/款式/工序/人员展示字段。"""

    bed_no = serializers.CharField(source='ticket.production.bed_no', read_only=True)
    bundle_no = serializers.CharField(source='ticket.bundle_no', read_only=True)
    color = serializers.CharField(source='ticket.color', read_only=True)
    size = serializers.CharField(source='ticket.size', read_only=True)
    style_no = serializers.CharField(
        source='ticket.production.style.style_no', read_only=True)
    style_name = serializers.CharField(
        source='ticket.production.style.name', read_only=True)
    process_name = serializers.CharField(source='process.name', read_only=True)
    user_name = serializers.CharField(source='user.real_name', read_only=True)
    operator_name = serializers.CharField(source='operator.real_name', read_only=True)
    amount = serializers.SerializerMethodField()

    class Meta:
        model = Record
        fields = ('id', 'ticket', 'bed_no', 'bundle_no', 'color', 'size',
                  'style_no', 'style_name', 'process', 'process_name',
                  'user', 'user_name', 'operator', 'operator_name',
                  'qty', 'price', 'amount', 'registered_at', 'settlement')

    def get_amount(self, obj):
        from apps.core.utils import q2
        return q2(obj.qty * obj.price)


class RecordCreateSerializer(serializers.Serializer):
    """登记入参：ticket/process 必填，user 缺省为本人，qty 缺省为菲票扎数。"""

    ticket_id = serializers.IntegerField()
    process_id = serializers.IntegerField()
    user_id = serializers.IntegerField(required=False)
    qty = serializers.IntegerField(required=False, min_value=1)
