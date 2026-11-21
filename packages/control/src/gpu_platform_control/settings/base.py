import yaml
from .database import database_settings
from django.utils.translation import gettext_lazy as tr
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("PLATFORM_DATA", BASE_DIR.parent / "runtime"))
PLATFORM_DATASET_ROOT = Path(os.environ.get("PLATFORM_DATASET_ROOT", "/srv/platform/datasets"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "")
DEBUG = os.environ.get("DJANGO_DEBUG", "0") == "1"
if not SECRET_KEY:
    if not DEBUG and "test" not in __import__("sys").argv:
        raise RuntimeError("DJANGO_SECRET_KEY must be configured")
    SECRET_KEY = "development-only-do-not-deploy"
ALLOWED_HOSTS = os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,testserver").split(",")
INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "gpu_platform_control.core.apps.ControlConfig",
    "gpu_platform_control.core.accounts.apps.AccountsConfig",
    "gpu_platform_control.core.quotas.apps.QuotasConfig",
    "gpu_platform_control.core.requests.apps.RequestsConfig",
    "gpu_platform_control.core.tasks.apps.TasksConfig",
    "gpu_platform_control.core.nodes.apps.NodesConfig",
    "gpu_platform_control.core.retention.apps.RetentionConfig",
    "gpu_platform_control.core.sync.apps.SyncConfig",
    "gpu_platform_control.core.audit.apps.AuditConfig",
    "gpu_platform_control.core.events.apps.EventsConfig",
    "gpu_platform_panel.apps.PanelConfig",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "gpu_platform_control.core.accounts.middleware.UserLocaleMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
ROOT_URLCONF = "gpu_platform_control.urls"
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
            ]
        },
    }
]
WSGI_APPLICATION = "gpu_platform_control.wsgi.application"
_configuration_path = os.environ.get("PLATFORM_CONFIG")
_configuration = yaml.safe_load(Path(_configuration_path).read_text()) if _configuration_path else {}
DATABASES = {"default": database_settings(_configuration.get("control", {}).get("database", {}), DATA_DIR)}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
]
LANGUAGE_CODE = _configuration.get("cluster", {}).get("default_language", "en")
LANGUAGES = [("en", "English"), ("zh-hans", tr("Simplified Chinese"))]
USE_I18N = True
TIME_ZONE = "Asia/Shanghai"
USE_TZ = True
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
LOGIN_URL = "/accounts/login/"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/accounts/login/"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SECURE = os.environ.get("PLATFORM_HTTPS", "1") == "1"
CSRF_COOKIE_SECURE = SESSION_COOKIE_SECURE
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
CSRF_TRUSTED_ORIGINS = (
    os.environ.get("CSRF_TRUSTED_ORIGINS", "").split(",") if os.environ.get("CSRF_TRUSTED_ORIGINS") else []
)
X_FRAME_OPTIONS = "DENY"
FILE_UPLOAD_MAX_MEMORY_SIZE = 1024 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = 8 * 1024 * 1024
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "INFO"},
}
AUTHENTICATION_BACKENDS = ["gpu_platform_control.core.accounts.backend.SlotBackend"]

LOCALE_PATHS = [BASE_DIR / "locale"]

MIGRATION_MODULES = {"platform_" + domain: None for domain in ["core", "accounts", "quotas", "requests", "tasks", "nodes", "retention", "sync", "audit", "events"]}
PLATFORM_SKILL_DIRECTORY = os.environ.get(
    "PLATFORM_SKILL_DIRECTORY", str(BASE_DIR / "agent-skill/gpu-platform")
)
