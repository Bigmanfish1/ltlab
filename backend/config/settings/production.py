from .base import *
 
DEBUG = False
 
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=[".run.app"])
 
# Security settings
USE_X_FORWARDED_HOST = True  # Cloud Run proxies requests; trust X-Forwarded-Host for build_absolute_uri
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

# Transaction-mode pooler (6543) can't keep server-side cursors across connections.
# This is a per-connection option, so it must live inside DATABASES, not as a
# module-level setting (Django reads it from the connection's settings_dict).
DATABASES["default"]["DISABLE_SERVER_SIDE_CURSORS"] = True

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