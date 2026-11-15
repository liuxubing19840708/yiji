"""隔离 SQLite 测试配置；仅在测试进程注册 handover，不修改共享 settings。"""
from config.settings import *  # noqa: F401,F403

INSTALLED_APPS = [*INSTALLED_APPS]  # noqa: F405
if 'apps.handover' not in INSTALLED_APPS:
    INSTALLED_APPS.append('apps.handover')
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': ':memory:',
        'TEST': {'NAME': ':memory:'},
    }
}
ROOT_URLCONF = 'apps.handover.tests.urls'
PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
LOGGING = {}
