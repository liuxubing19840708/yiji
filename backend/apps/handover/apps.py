"""通用交接应用配置。"""
from django.apps import AppConfig


class HandoverConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.handover'
    verbose_name = '通用交接'
