from .base import *  # noqa: F401, F403

DEBUG = True
ALLOWED_HOSTS = ["*"]

# Guarded so dev still boots on an image built before the dep was added.
try:
    import django_migration_linter  # noqa: F401
    INSTALLED_APPS += ["django_migration_linter"]  # noqa: F405
except ImportError:
    pass

# Show emails in the console during development
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

# Dev and the test runner never run collectstatic, so the manifest storage from
# base.py has nothing to look names up in. DEBUG hides that behind the finders;
# tests run with DEBUG=False and hit it on any page using {% static %}.
STORAGES = {
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
    },
}
