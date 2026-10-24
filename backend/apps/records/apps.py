"""扫菲登记应用配置。"""
from django.apps import AppConfig


class RecordsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.records'
    verbose_name = '扫菲登记'
