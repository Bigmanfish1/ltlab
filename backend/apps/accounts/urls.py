from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("login/", views.login_view, name="login"),
    path("callback/", views.oauth_callback_view, name="callback"),
    path("logout/", views.logout_view, name="logout"),
]
