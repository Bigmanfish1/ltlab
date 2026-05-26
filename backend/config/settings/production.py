from .base import *
import os
 
DEBUG = False
 
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=["ltlab.onrender.com"])
 
# Security settings
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True