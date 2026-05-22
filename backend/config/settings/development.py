from .base import *  # noqa: F401, F403

DEBUG = True
ALLOWED_HOSTS = ["*"]

# Show emails in the console during development
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
