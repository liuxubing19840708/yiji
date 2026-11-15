"""质检管理（竞品对齐 GAP-1，2026-11-04）。"""
from django.apps import AppConfig


class QualityConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.quality'
    verbose_name = '质检管理'
