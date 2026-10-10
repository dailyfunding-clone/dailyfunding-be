import os
from datetime import timedelta
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def env(key, default=""):
    return os.environ.get(key, default)


def env_bool(key, default=False):
    return env(key, "1" if default else "0").lower() in ("1", "true", "yes", "on")


def required_env(key):
    value = env(key)
    if not value:
        raise ImproperlyConfigured(f"{key} environment variable is required")
    return value


SECRET_KEY = required_env("SECRET_KEY")
DEBUG = env_bool("DEBUG", False)
ALLOWED_HOSTS = [h.strip() for h in env("ALLOWED_HOSTS").split(",") if h.strip()]
if not ALLOWED_HOSTS and not DEBUG:
    raise ImproperlyConfigured(
        "ALLOWED_HOSTS environment variable is required when DEBUG is off"
    )

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "rest_framework_simplejwt.token_blacklist",
    "drf_spectacular",
    "api.accounts",
    "api.products",
    "api.investments",
    "api.ledger",
    "api.loans",
    "api.contents",
    "api.mypage",
    "api.webhooks",
    "api.notifications",
    "api.adminpanel",
    "mockbank",
    "jobs",
    "ops",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "api.common.auth.CsrfDoubleSubmitMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env("DB_NAME", "dailyfunding"),
        "USER": env("DB_USER", "dailyfunding"),
        "PASSWORD": env("DB_PASSWORD", "dailyfunding"),
        "HOST": env("DB_HOST", "127.0.0.1"),
        "PORT": env("DB_PORT", "5432"),
        "CONN_MAX_AGE": int(env("CONN_MAX_AGE", "0")),
        "TEST": {"NAME": "dailyfunding_test"},
    }
}

AUTH_USER_MODEL = "accounts.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
]

LANGUAGE_CODE = "ko-kr"
TIME_ZONE = "UTC"
USE_TZ = True

STATIC_URL = "static/"
MEDIA_ROOT = BASE_DIR / "media"
MEDIA_URL = "media/"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "api.common.auth.CookieJWTAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": (
        "rest_framework.permissions.IsAuthenticated",
    ),
    "EXCEPTION_HANDLER": "api.common.exceptions.exception_handler",
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_PAGINATION_CLASS": "api.common.pagination.StandardPagination",
    "PAGE_SIZE": 20,
    "DEFAULT_THROTTLE_RATES": {
        "auth": "20/min",
        "pin": "10/min",
        "app_code": "20/min",
        "reset": "5/min",
        "vitals": "120/min",
    },
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=15),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=14),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "AUTH_HEADER_TYPES": ("Bearer",),
}

SPECTACULAR_SETTINGS = {
    "TITLE": "DailyFunding Clone API",
    "VERSION": "2.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "loggers": {"api.metrics": {"handlers": ["console"], "level": "INFO"}},
}

REDIS_URL = env("REDIS_URL", "redis://127.0.0.1:6379/0")
CELERY_BROKER_URL = REDIS_URL
CELERY_RESULT_BACKEND = REDIS_URL
CELERY_TASK_ALWAYS_EAGER = env_bool("CELERY_EAGER", False)
CELERY_TASK_EAGER_PROPAGATES = True
CELERY_BEAT_SCHEDULE = {
    "repay-daily": {
        "task": "jobs.tasks.repay_daily",
        "schedule": 60 * 60 * 24,
    },
    "points-expire-monthly": {
        "task": "jobs.tasks.expire_points",
        "schedule": 60 * 60 * 24,
    },
    "ledger-reconcile-daily": {
        "task": "jobs.tasks.reconcile_ledger",
        "schedule": 60 * 60 * 24,
    },
    "webhook-retry": {
        "task": "jobs.tasks.retry_webhooks",
        "schedule": 60,
    },
    "idempotency-purge-hourly": {
        "task": "jobs.tasks.purge_idempotency_records",
        "schedule": 60 * 60,
    },
    "product-progress-purge-hourly": {
        "task": "jobs.tasks.purge_product_progress",
        "schedule": 60 * 60,
    },
}

# mockbank -> API webhook channel
MOCKBANK_API_BASE = env("MOCKBANK_API_BASE", "http://127.0.0.1:8000")
BANK_WEBHOOK_SECRET = required_env("BANK_WEBHOOK_SECRET")
BANK_WEBHOOK_TOLERANCE_SEC = 300
MOCKBANK_CODE = "090"
MOCKBANK_NAME = "모의은행"

# Business constants
TAX_RATE = "0.154"  # 이자소득세 14% + 지방소득세 1.4%
WITHDRAW_FREE_COUNT = 20  # 월 무료 출금 횟수
WITHDRAW_FEE = 500
DAILY_WITHDRAW_LIMIT = 50_000_000
REAUTH_TOKEN_TTL_SEC = 600
PIN_MAX_FAILURES = 5
REFERRAL_MAX_REWARDS = 10
IDEMPOTENCY_TTL_HOURS = 24
