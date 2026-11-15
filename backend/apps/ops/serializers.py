"""运营支撑序列化器。"""
from rest_framework import serializers

from apps.ops.models import AuditLog, Notification


class AuditLogSerializer(serializers.ModelSerializer):
    type_display = serializers.CharField(source='get_type_display', read_only=True)
    operator_name = serializers.CharField(source='operator.real_name', read_only=True, default='')
    target_user_name = serializers.CharField(source='target_user.real_name', read_only=True, default='')

    class Meta:
        model = AuditLog
        fields = [
            'id', 'type', 'type_display', 'bed_no', 'style_no', 'bundle_no',
            'operator', 'operator_name', 'target_user', 'target_user_name',
            'before', 'after', 'message', 'created_at',
        ]


class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification
        fields = ['id', 'code', 'title', 'message', 'is_read', 'created_at']
        read_only_fields = fields


class SettingSerializer(serializers.Serializer):
    """批量读写设置：请求体即 {key: value} 字典。"""

    def validate(self, attrs):
        from apps.ops.models import CompanySetting
        data = self.initial_data
        if not isinstance(data, dict):
            raise serializers.ValidationError('请求体应为设置键值对象')
        unknown = [k for k in data if k not in CompanySetting.DEFAULTS]
        if unknown:
            raise serializers.ValidationError(f'未知设置项：{", ".join(unknown)}')
        for key in ('preproduction_gate_mode', 'manufacturing_gate_mode'):
            if key in data and data[key] not in ('off', 'warn', 'strict'):
                raise serializers.ValidationError({key: '仅支持 off/warn/strict'})
        return data

    def to_representation(self, instance):
        return instance
