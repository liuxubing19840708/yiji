"""Django 全局配置。"""
import os
from datetime import timedelta
from pathlib import Path

import dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

# .env 与环境变量均可生效（环境变量优先）
dotenv.load_dotenv(BASE_DIR / '.env')


def env(key, default=''):
    return os.environ.get(key, default)


def env_bool(key, default=False):
    return str(os.environ.get(key, default)).strip().lower() in ('1', 'true', 'yes', 'on')

SECRET_KEY = env('SECRET_KEY', 'dev-insecure-secret-key-change-me')
DEBUG = env_bool('DEBUG', True)
ALLOWED_HOSTS = [h.strip() for h in env('ALLOWED_HOSTS', '*').split(',') if h.strip()]

# ---------------------------------------------------------------- 应用
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'rest_framework',
    'corsheaders',
    # 业务应用
    'apps.core',
    'apps.accounts',
    'apps.catalog',
    'apps.production',
    'apps.orders',
    'apps.records',
    'apps.salary',
    'apps.ops',
    'apps.kanban_template',
    'apps.warehouse',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'
ASGI_APPLICATION = 'config.asgi.application'

# ---------------------------------------------------------------- 数据库
# 数据库：DB_ENGINE=postgres 走 PostgreSQL（生产）；缺省 SQLite（本地开发零配置）。
if env('DB_ENGINE', 'sqlite') == 'postgres':
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.postgresql',
            'NAME': env('DB_NAME', 'yisao'),
            'USER': env('DB_USER', 'postgres'),
            'PASSWORD': env('DB_PASSWORD', 'postgres'),
            'HOST': env('DB_HOST', '127.0.0.1'),
            'PORT': env('DB_PORT', '5432'),
            'OPTIONS': {
                'client_encoding': 'UTF8',
            },
            'CONN_MAX_AGE': 60,
        }
    }
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
        }
    }

# ---------------------------------------------------------------- 认证
AUTH_USER_MODEL = 'accounts.User'

REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': (
        'rest_framework_simplejwt.authentication.JWTAuthentication',
    ),
    'DEFAULT_PERMISSION_CLASSES': (
        'rest_framework.permissions.IsAuthenticated',
    ),
    'DEFAULT_PAGINATION_CLASS': 'apps.core.pagination.DefaultPagination',
    'PAGE_SIZE': 20,
    'DATETIME_FORMAT': '%Y-%m-%d %H:%M:%S',
    'EXCEPTION_HANDLER': 'apps.core.exceptions.api_exception_handler',
}

SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(days=7),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=30),
    'AUTH_HEADER_TYPES': ('Bearer',),
    'UPDATE_LAST_LOGIN': True,
}

# 跨域：开发环境放行全部；生产请改为具体域名
CORS_ALLOW_ALL_ORIGINS = env_bool('CORS_ALLOW_ALL', True)
CORS_ALLOWED_ORIGINS = [o.strip() for o in env('CORS_ALLOWED_ORIGINS', '').split(',') if o.strip()]

# ---------------------------------------------------------------- 国际化
LANGUAGE_CODE = 'zh-hans'
TIME_ZONE = 'Asia/Shanghai'
USE_I18N = True
USE_TZ = True

# ---------------------------------------------------------------- 静态/媒体
STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'static'
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# ---------------------------------------------------------------- 业务
WECHAT_APPID = env('WECHAT_APPID', '')
WECHAT_SECRET = env('WECHAT_SECRET', '')
# 小程序码目标版本：release/trial/develop（体验版/开发版扫码需对应权限）
WECHAT_WXACODE_ENV = env('WECHAT_WXACODE_ENV', 'release')

# 公司成员容量默认上限（可在 Company.capacity 单公司覆盖）
EMPLOYEE_CAPACITY_DEFAULT = 50
