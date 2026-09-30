"""Django settings for the Voice CRM agent. Everything secret or environment-specific comes from env vars."""
import os
import sys
from datetime import timedelta
from pathlib import Path

import dj_database_url
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent


# ── env helpers ────────────────────────────────────
def env(name, default=""):
    return os.environ.get(name, default)


def env_bool(name, default=False):
    return env(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


def env_int(name, default):
    return int(env(name, str(default)))


def env_float(name, default):
    return float(env(name, str(default)))


def env_list(name, default=""):
    return [x.strip() for x in env(name, default).split(",") if x.strip()]


# ── core ───────────────────────────────────────────
DEBUG = env_bool("DEBUG", False)
SECRET_KEY = env("DJANGO_SECRET_KEY")
if not SECRET_KEY:
    if not DEBUG:
        raise ImproperlyConfigured("DJANGO_SECRET_KEY must be set when DEBUG is off.")
    SECRET_KEY = "dev-only-insecure-key"

ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")
CSRF_TRUSTED_ORIGINS = env_list("CSRF_TRUSTED_ORIGINS")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.postgres",   # required by pgvector HnswIndex
    "rest_framework",
    "apps.accounts",
    "apps.crm",
    "apps.documents",
    "apps.agent",
    "apps.voice",
]

MIDDLEWARE = [
    "security.middleware.HealthCheckMiddleware",   # first: answers /health/ before host validation
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "security.middleware.RateLimitMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "security.middleware.InputSanitizationMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [BASE_DIR / "templates"],
    "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.request",
        "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages",
    ]},
}]

# ── database (Postgres + pgvector) ─────────────────
if env("DATABASE_URL"):
    DATABASES = {"default": dj_database_url.parse(env("DATABASE_URL"), conn_max_age=60)}
else:
    DATABASES = {"default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env("DB_NAME", "voice"),
        "USER": env("DB_USER", "postgres"),
        "PASSWORD": env("DB_PASSWORD"),
        "HOST": env("DB_HOST", "localhost"),
        "PORT": env("DB_PORT", "5432"),
        "CONN_MAX_AGE": 60,
    }}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ── cache (Redis in production, in-memory fallback without REDIS_URL) ──
# The in-memory fallback is per-process: fine for tests/dev, not for several containers.
if env("REDIS_URL"):
    CACHES = {"default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": env("REDIS_URL"),
    }}
else:
    CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}

# ── auth / API ─────────────────────────────────────
if "test" in sys.argv[:2]:      # fast password hashing while running the test suite only
    PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 10}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["rest_framework_simplejwt.authentication.JWTAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PARSER_CLASSES": ["rest_framework.parsers.JSONParser", "rest_framework.parsers.MultiPartParser"],
}
SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(hours=2),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=1),
}

REGISTRATION_ENABLED = env_bool("REGISTRATION_ENABLED", True)
REGISTRATION_INVITE_CODE = env("REGISTRATION_INVITE_CODE")   # empty = open registration

# ── static files ───────────────────────────────────
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
}

# ── i18n ───────────────────────────────────────────
LANGUAGE_CODE = "en-us"
TIME_ZONE = env("TIME_ZONE", "UTC")   # set to your business's zone, e.g. "Europe/London"
USE_TZ = True

# ── HTTPS / proxy ──────────────────────────────────
# Production path: browser -> CloudFront (https) -> ALB (http) -> container.
# CloudFront tells us the original scheme in CloudFront-Forwarded-Proto.
BEHIND_CLOUDFRONT = env_bool("BEHIND_CLOUDFRONT", not DEBUG)
TRUSTED_PROXY_COUNT = env_int("TRUSTED_PROXY_COUNT", 2 if BEHIND_CLOUDFRONT else 0)  # CloudFront + ALB
if BEHIND_CLOUDFRONT:
    SECURE_PROXY_SSL_HEADER = ("HTTP_CLOUDFRONT_FORWARDED_PROTO", "https")
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 31536000
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"

# ── logging ────────────────────────────────────────
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": env("LOG_LEVEL", "INFO")},
}

# ── Voice CRM behaviour ────────────────────────────
BUSINESS_HOURS = (9, 17)     # bookings allowed from 09:00 up to (not including) 17:00
SLOT_MINUTES = 30            # bookings must start on :00 or :30
EMERGENCY_MESSAGE = (
    "This sounds urgent. I can't help with emergencies. "
    "Please call your local emergency number right now, or a crisis helpline in your country."
)

# Model names change over time: override through env vars, no code change needed.
LLM_MODELS = env_list("LLM_MODELS", "llama-3.3-70b-versatile,llama-3.1-8b-instant")   # tried in order
GROQ_API_KEY = env("GROQ_API_KEY")
STT_MODEL = env("STT_MODEL", "whisper-large-v3-turbo")
ELEVENLABS_API_KEY = env("ELEVENLABS_API_KEY")
ELEVENLABS_VOICE_ID = env("ELEVENLABS_VOICE_ID")
ELEVENLABS_MODEL = env("ELEVENLABS_MODEL", "eleven_multilingual_v2")
METRICS_TOKEN = env("METRICS_TOKEN")   # empty = metrics endpoint disabled

# ── Knowledge base (RAG) ───────────────────────────
EMBEDDING_MODEL = env("EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5")   # 384 dimensions (see documents migration)
EMBEDDING_CACHE_DIR = env("EMBEDDING_CACHE_DIR")
RETRIEVAL_TOP_K = env_int("RETRIEVAL_TOP_K", 4)
RETRIEVAL_MIN_SIM = env_float("RETRIEVAL_MIN_SIM", 0.5)   # below this, nothing is "relevant": no LLM call
GROUNDING_MIN = env_float("GROUNDING_MIN", 0.55)          # below this, the answer is not backed by the documents
MAX_PDF_BYTES = env_int("MAX_PDF_BYTES", 10 * 1024 * 1024)
MAX_PDF_PAGES = env_int("MAX_PDF_PAGES", 50)
MAX_CHUNKS_PER_DOC = env_int("MAX_CHUNKS_PER_DOC", 400)
