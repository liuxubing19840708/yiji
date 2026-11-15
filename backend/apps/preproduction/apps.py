from django.apps import AppConfig


class PreproductionConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.preproduction'
    verbose_name = '产前管理'
