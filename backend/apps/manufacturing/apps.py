"""统一生产数量底座。"""
from django.apps import AppConfig


class ManufacturingConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.manufacturing'
    verbose_name = '统一生产数量底座'
