from django.apps import AppConfig


class OpsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.ops'
    verbose_name = '运营支撑（设置/通知/日志）'
