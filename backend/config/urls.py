from django.contrib import admin
from django.urls import path

from config.api import api
from config.views import home

urlpatterns = [
    path("", home, name="home"),
    path("admin/", admin.site.urls),
    path("api/", api.urls),
]
