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
    'apps.tech',
    'apps.cost',
    'apps.quotation',
    'apps.finance',
    'apps.shipment',
    'apps.ai_report',
    'apps.quality',
    'apps.preproduction',
    'apps.manufacturing',
    'apps.cutting',
    'apps.handover',
    'apps.finished_goods',
    'apps.subcontract',
]

# ---- AI 报表问答（智谱 GLM；key 走环境变量，不进库不进 git）----
GLM_API_KEY = env('GLM_API_KEY', '')
GLM_MODEL = env('GLM_MODEL', 'glm-4-flash')
GLM_BASE_URL = env('GLM_BASE_URL', 'https://open.bigmodel.cn/api/paas/v4')
GLM_TIMEOUT_S = int(env('GLM_TIMEOUT_S', '8'))
# 测试阶段自由问答：白名单外问题把系统实时统计数据作为上下文交给 GLM 作答
# （数字仍全部来自系统快照，模型只负责组织语言）。上线前改 .env AI_FREE_MODE=0 收回白名单模式。
AI_FREE_MODE = env_bool('AI_FREE_MODE', True)

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
            'NAME': env('DB_NAME', str(BASE_DIR / 'db.sqlite3')),
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

# 跨域：开发缺省放行全部；生产（DEBUG=0）缺省白名单——deploy/.env.production 显式配置
CORS_ALLOW_ALL_ORIGINS = env_bool('CORS_ALLOW_ALL', DEBUG)
CORS_ALLOWED_ORIGINS = [o.strip() for o in env('CORS_ALLOWED_ORIGINS', '').split(',') if o.strip()]

# ---------------------------------------------------------------- 生产安全（deploy/.env.production 驱动，DEBUG=0 时生效）
if not DEBUG:
    # nginx 终结 TLS，Django 据此识别 https
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
    SECURE_SSL_REDIRECT = env_bool('SECURE_SSL_REDIRECT', False)   # nginx 已做 80→443 跳转，此开关按需
    SECURE_HSTS_SECONDS = int(env('SECURE_HSTS_SECONDS', '0') or 0)
    SESSION_COOKIE_SECURE = env_bool('SECURE_COOKIES', True)
    CSRF_COOKIE_SECURE = env_bool('SECURE_COOKIES', True)

SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = 'DENY'

# ---------------------------------------------------------------- 日志（console + 滚动文件）
LOG_DIR = BASE_DIR / 'logs'
LOG_DIR.mkdir(exist_ok=True)
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'std': {'format': '{asctime} {levelname} {name} {message}', 'style': '{'},
    },
    'handlers': {
        'console': {'class': 'logging.StreamHandler', 'formatter': 'std'},
        'file': {
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': LOG_DIR / 'saofei.log',
            'maxBytes': 10 * 1024 * 1024,
            'backupCount': 10,
            'encoding': 'utf-8',
            'formatter': 'std',
        },
    },
    'root': {'handlers': ['console', 'file'], 'level': 'INFO'},
    'loggers': {
        'django.request': {'handlers': ['console', 'file'], 'level': 'WARNING', 'propagate': False},
    },
}

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
# AI 异步导出私有目录：不得由 nginx/media 直接暴露，只能经鉴权下载端点读取。
AI_EXPORT_ROOT = env('AI_EXPORT_ROOT', str(BASE_DIR / 'private_ai_exports'))
AI_EXPORT_RETENTION_HOURS = int(env('AI_EXPORT_RETENTION_HOURS', '24') or 24)
AI_REPORT_SLOW_MS = int(env('AI_REPORT_SLOW_MS', '1000') or 1000)

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# ---------------------------------------------------------------- 业务
WECHAT_APPID = env('WECHAT_APPID', '')
WECHAT_SECRET = env('WECHAT_SECRET', '')
# 微信开放平台「网站应用」网页扫码登录（与小程序 appid/secret 相互独立）
WECHAT_OPEN_APPID = env('WECHAT_OPEN_APPID', '')
WECHAT_OPEN_SECRET = env('WECHAT_OPEN_SECRET', '')
WECHAT_WEB_STATE_MAX_AGE = 600
# 小程序码目标版本：release/trial/develop（体验版/开发版扫码需对应权限）
WECHAT_WXACODE_ENV = env('WECHAT_WXACODE_ENV', 'release')

# 公司成员容量默认上限（可在 Company.capacity 单公司覆盖）
EMPLOYEE_CAPACITY_DEFAULT = 50
