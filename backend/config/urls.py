from django.contrib import admin
from django.urls import path

from apps.checker.views import counterexample, verify_ltl
from config.api import api
from config.views import home, sandbox

urlpatterns = [
    path("", home, name="home"),
    path("sandbox/", sandbox, name="sandbox"),
    path("sandbox/verify/", verify_ltl, name="sandbox_verify"),
    path("sandbox/counterexample/", counterexample, name="sandbox_counterexample"),
    path("admin/", admin.site.urls),
    path("api/", api.urls),
]
