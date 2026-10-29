"""Cấu hình Django cho SMS (Scan MES System)"""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent          # backend/
load_dotenv(BASE_DIR / ".env")

SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "dev-only-change-me")
DEBUG = os.getenv("DJANGO_DEBUG", "0") == "1"

def _csv(name: str, default: str = "") -> list[str]:
    return [x.strip() for x in os.getenv(name, default).split(",") if x.strip()]


ALLOWED_HOSTS = _csv("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")
# CORS: các trang web được phép gọi API này từ trình duyệt (DYS)
CORS_ALLOWED_ORIGINS = _csv("CORS_ALLOWED_ORIGINS", "https://dys.dongyang.site,http://localhost:3000")
CORS_ALLOW_HEADERS = ["accept", "content-type", "x-api-password", "x-requested-by"]
CORS_EXPOSE_HEADERS = ["content-disposition"]   # để DYS đọc được tên file khi tải

# Chạy sau Cloudflare (HTTPS ở Cloudflare, vào container là HTTP): tin header X-Forwarded-Proto
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_HOST = True

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",          # Django cần sẵn; SMS không dùng tài khoản người dùng
    "django.contrib.staticfiles",   # file tĩnh cho trang /api/docs
    "corsheaders",
    "apps.core",
    "apps.jobs",
    "apps.plans",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",   # phục vụ file tĩnh của /api/docs
    "corsheaders.middleware.CorsMiddleware",        # cho phép trang DYS gọi API từ trình duyệt
    "django.middleware.common.CommonMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [],
    "APP_DIRS": True,
    "OPTIONS": {"context_processors": ["django.template.context_processors.request"]},
}]

# ---------------- Database ----------------
if os.getenv("DB_ENGINE", "mysql") == "sqlite":          # chỉ dùng để thử nhanh
    DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "dev.sqlite3"}}
else:
    DATABASES = {"default": {
        "ENGINE": "django.db.backends.mysql",
        "HOST": os.getenv("DB_HOST", "127.0.0.1"),
        "PORT": os.getenv("DB_PORT", "3306"),
        "NAME": os.getenv("DB_NAME", "sms"),
        "USER": os.getenv("DB_USER", "sms_app"),
        "PASSWORD": os.getenv("DB_PASSWORD", ""),
        "OPTIONS": {
            "charset": "utf8mb4",
            "init_command": "SET sql_mode='STRICT_TRANS_TABLES'",
        },
    }}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# ---------------- Ngôn ngữ / giờ ----------------
LANGUAGE_CODE = "vi"
TIME_ZONE = "Asia/Ho_Chi_Minh"
USE_I18N = True
USE_TZ = True

# ---------------- Static (/admin) ----------------
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

# ---------------- Lưu trữ file kế hoạch ----------------
STORAGE_DIR = Path(os.getenv("STORAGE_DIR") or BASE_DIR / "storage")
PLAN_DIR = STORAGE_DIR / "KeHoach"
SCREENSHOT_DIR = STORAGE_DIR / "screenshots"
LOG_DIR = Path(os.getenv("LOG_DIR") or BASE_DIR / "logs")

# Ghi log ra console (Docker: xem bằng `docker compose logs -f api`)
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"simple": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "simple"}},
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {"django.request": {"handlers": ["console"], "level": "WARNING", "propagate": False}},
}


