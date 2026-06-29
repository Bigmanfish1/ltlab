"""Minimal settings for running the test suite locally (no Postgres, no Redis).

Use with:  python3 manage.py test --settings=config.settings.test
"""

from .base import *  # noqa: F401, F403

# Override DB to SQLite in-memory so the test runner needs no Postgres install.
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME":   ":memory:",
    }
}

# Disable Redis cache; fall back to LocMem so cache calls don't error.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
    }
}

# Silence migration output during test runs.
MIGRATION_MODULES = {app: None for app in [
    "accounts", "checker", "exercises", "home",
    "auth", "contenttypes", "sessions", "admin",
]}

# Supabase keys — not used by unit tests but required by settings import.
import os
os.environ.setdefault("SECRET_KEY", "test-secret-key-not-for-production")
os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("SUPABASE_JWT_SECRET", "test-jwt-secret")
