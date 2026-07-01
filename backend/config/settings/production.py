from .base import *
import os
 
DEBUG = False
 
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=["ltlab.onrender.com"])
 
# Security settings
USE_X_FORWARDED_HOST = True  # Render proxies requests; trust X-Forwarded-Host for build_absolute_uri
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

# Transaction-mode pooler (6543) can't keep server-side cursors across connections.
DISABLE_SERVER_SIDE_CURSORS = True