from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env(
    DEBUG=(bool, False),
    ALLOWED_HOSTS=(list, ["*"]),
)

# Read .env file when running outside Docker (file won't exist inside container
# because docker-compose injects vars directly into the environment).
_env_file = BASE_DIR.parent / ".env"
if _env_file.exists():
    environ.Env.read_env(_env_file, overwrite=False)

SECRET_KEY = env("SECRET_KEY")
DEBUG = env("DEBUG")
ALLOWED_HOSTS = env("ALLOWED_HOSTS")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Local apps
    "apps.accounts",
    "apps.exercises",
    "apps.kripke",
    "apps.checker",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "apps.accounts.middleware.SupabaseAuthMiddleware",
    "apps.accounts.middleware.HtmxAuthRedirectMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": env.db("DATABASE_URL"),
}

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Africa/Johannesburg"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

SUPABASE_URL = env("SUPABASE_URL", default="")
SUPABASE_ANON_KEY = env("SUPABASE_ANON_KEY", default="")

# Django cache — use Redis so web and worker processes share the same store.
# Falls back to the built-in local-memory backend in environments without Redis
# (e.g. plain manage.py runserver for quick local hacking).
_REDIS_URL = env("REDIS_URL", default="")
if _REDIS_URL:
    CACHES = {
        "default": {
            "BACKEND":  "django.core.cache.backends.redis.RedisCache",
            "LOCATION": _REDIS_URL,
        }
    }

# TTL for cached LTL results (seconds).  Students running the same example
# repeatedly (classroom bursts) will get instant responses on cache hits.
RESULT_CACHE_TTL = env.int("RESULT_CACHE_TTL", default=3600)

# Celery
CELERY_BROKER_URL        = env("REDIS_URL", default="redis://redis:6379/0")
CELERY_RESULT_BACKEND    = env("REDIS_URL", default="redis://redis:6379/0")
CELERY_ACCEPT_CONTENT    = ["json"]
CELERY_TASK_SERIALIZER   = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE          = TIME_ZONE

# Time limits — soft limit is a backstop for Python-side loops; it cannot
# interrupt SPOT's C++ translate() (signal delivered at bytecode boundary only).
# The structural caps in engine.validate_request are the primary DoS guard.
CELERY_TASK_SOFT_TIME_LIMIT = env.int("CELERY_SOFT_TIME_LIMIT", default=10)
CELERY_TASK_TIME_LIMIT      = env.int("CELERY_TIME_LIMIT",      default=30)

# OOM mitigation: recycle workers after N tasks or M MB of memory used.
# SPOT can leave fragmented BDD memory after heavy runs; recycling prevents
# gradual RSS growth from accumulating into an OOM kill.
CELERY_WORKER_MAX_TASKS_PER_CHILD  = env.int("CELERY_MAX_TASKS_PER_CHILD",  default=50)
CELERY_WORKER_MAX_MEMORY_PER_CHILD = env.int("CELERY_MAX_MEMORY_PER_CHILD", default=300_000)  # KB

# Prevent Redis result accumulation — results expire after 1 hour.
CELERY_RESULT_EXPIRES = env.int("CELERY_RESULT_EXPIRES", default=3600)
