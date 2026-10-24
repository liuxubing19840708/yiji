"""订单管理（Order/OrderItem）应用配置。

2026-10-20 系统重设计 P1 新增（实施方案 §2.5 / 第三部分 DDL 已批准）。
"""
from django.apps import AppConfig


class OrdersConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.orders'
    verbose_name = '订单管理'
