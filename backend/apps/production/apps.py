"""生产管理（床次订单）应用配置。"""
from django.apps import AppConfig


class ProductionConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.production'
    verbose_name = '生产管理（床次订单）'
