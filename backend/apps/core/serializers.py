"""核心模块序列化器。"""
from rest_framework import serializers

from .models import ClientLog


def validate_same_company(serializer, obj, label):
    """DRF 全局外键查询集的公司边界；新建与更新共用同一校验。

    视图在 initial 阶段已经解析 company。非视图直用序列化器时不猜测公司，
    业务服务仍须自行做归属校验。
    """
    company = getattr(serializer.context.get('view'), 'company', None)
    if obj is not None and company is not None and obj.company_id != company.id:
        raise serializers.ValidationError(f'{label}不存在')
    return obj


class ClientLogSerializer(serializers.ModelSerializer):
    """客户端日志（管理员读取）：带用户姓名/手机号便于定位。"""

    user_name = serializers.SerializerMethodField()
    user_phone = serializers.SerializerMethodField()

    class Meta:
        model = ClientLog
        fields = ['id', 'company', 'user', 'user_name', 'user_phone',
                  'level', 'page', 'message', 'detail', 'created_at']

    def get_user_name(self, obj):
        u = obj.user
        if not u:
            return ''
        return u.real_name or u.username or ''

    def get_user_phone(self, obj):
        return (obj.user.phone or '') if obj.user else ''
