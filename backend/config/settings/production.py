from .base import *
 
DEBUG = False
 
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=[".run.app"])
 
# Security settings
USE_X_FORWARDED_HOST = True  # Cloud Run proxies requests; trust X-Forwarded-Host for build_absolute_uri
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
# HSTS (1yr). *.run.app is already preloaded; this also covers a custom domain.
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True

# Transaction-mode pooler (6543) can't keep server-side cursors across connections.
DATABASES["default"]["DISABLE_SERVER_SIDE_CURSORS"] = True
DATABASES["default"]["CONN_MAX_AGE"] = 60
DATABASES["default"]["CONN_HEALTH_CHECKS"] = True
# setdefault so a connect timeout doesn't clobber options parsed from DATABASE_URL.
DATABASES["default"].setdefault("OPTIONS", {})["connect_timeout"] = 5

# Emit 500 tracebacks to stderr; DEBUG stays False (Django default only mails admins).
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {"format": "{levelname} {asctime} {name} {message}", "style": "{"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "verbose"},
    },
    "root": {"handlers": ["console"], "level": "WARNING"},
    "loggers": {
        "django.request": {"handlers": ["console"], "level": "ERROR", "propagate": False},
    },
}