"""核心模块序列化器。"""
from rest_framework import serializers

from .models import ClientLog


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
