from .base import *  # noqa: F401, F403

DEBUG = True
ALLOWED_HOSTS = ["*"]

# Dev/CI only — provides the `lintmigrations` management command. Kept out of
# production settings so it never ships in the deployed image. Guarded so dev
# still boots on an image built before the dependency was added (rebuild to lint).
try:
    import django_migration_linter  # noqa: F401
    INSTALLED_APPS += ["django_migration_linter"]  # noqa: F405
except ImportError:
    pass

# Show emails in the console during development
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
