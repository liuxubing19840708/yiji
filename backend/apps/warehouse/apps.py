from django.apps import AppConfig


class WarehouseConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.warehouse'
    verbose_name = '轻量仓库与质量追溯（批次/血缘/追溯查询）'
