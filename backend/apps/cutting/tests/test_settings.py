"""隔离 SQLite 测试配置；裁片 app 已由共享 settings 注册。"""
from config.settings import *  # noqa: F401,F403

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': ':memory:',
        'TEST': {'NAME': ':memory:'},
    }
}
ROOT_URLCONF = 'apps.cutting.tests.urls'
PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
LOGGING = {}
