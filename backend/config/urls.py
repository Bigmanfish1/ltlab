from django.contrib import admin
from django.urls import include, path

from apps.checker.views import counterexample, verify_ltl
from apps.exercises.views import exercise_canvas, exercises, get_hint, submit_formula, teacher_exercises
from apps.home.views import home, teacher_results
from config.api import api
from config.views import sandbox

urlpatterns = [
    path("accounts/", include("apps.accounts.urls", namespace="accounts")),
    path("", home, name="home"),
    path("exercises/", exercises, name="exercises"),
    path("exercises/<uuid:exercise_id>/", exercise_canvas, name="exercise_canvas"),
    path('exercises/<uuid:exercise_id>/submit/', submit_formula, name='submit_formula'),
    path('exercises/<uuid:exercise_id>/hint/', get_hint, name='get_hint'),
    path("sandbox/", sandbox, name="sandbox"),
    path("sandbox/verify/", verify_ltl, name="sandbox_verify"),
    path("sandbox/counterexample/", counterexample, name="sandbox_counterexample"),
    path("teacher/exercises/", teacher_exercises, name="teacher_exercises"),
    path("results/", teacher_results, name="results"),
    path("admin/", admin.site.urls),
    path("api/", api.urls),
]
