from django.urls import path

from . import views, views_manage

app_name = "accounts"

urlpatterns = [
    path("login/", views.login_view, name="login"),
    path("login/google/", views.google_oauth_view, name="google_oauth"),
    path("callback/", views.oauth_callback_view, name="callback"),
    path("logout/", views.logout_view, name="logout"),
    path("view-as/student/", views_manage.enter_view_as_student, name="view_as_student"),
    path("view-as/teacher/", views_manage.exit_view_as, name="view_as_teacher"),
]
