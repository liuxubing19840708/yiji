"""外发管理（竞品对齐 GAP-5，2026-11-04；P3 §4.5 委外全量的轻量先行版）。"""
from django.apps import AppConfig


class SubcontractConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.subcontract'
    verbose_name = '外发管理'
