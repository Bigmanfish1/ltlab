from django.contrib import admin
from django.urls import include, path

from apps.checker.views import counterexample, verify_ltl
from apps.exercises.views import exercise_canvas, exercises, get_hint, submit_formula
from apps.home.views import home
from config.api import api
from config.views import sandbox

urlpatterns = [
    path("accounts/", include("apps.accounts.urls", namespace="accounts")),
    path("", home, name="home"),
    path("exercises/", exercises, name="exercises"),
    path("exercises/<int:exercise_id>/", exercise_canvas, name="exercise_canvas"),
    path('exercises/<int:exercise_id>/submit/', submit_formula, name='submit_formula'),
    path('exercises/<int:exercise_id>/hint/', get_hint, name='get_hint'),
    path("sandbox/", sandbox, name="sandbox"),
    path("sandbox/verify/", verify_ltl, name="sandbox_verify"),
    path("sandbox/counterexample/", counterexample, name="sandbox_counterexample"),
    path("admin/", admin.site.urls),
    path("api/", api.urls),
]
