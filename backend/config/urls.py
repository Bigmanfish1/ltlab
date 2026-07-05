from django.contrib import admin
from django.urls import include, path

from apps.checker.views import counterexample, verify_ltl
from apps.exercises.views import (
    exercise_builder,
    exercise_canvas,
    exercise_delete,
    exercises,
    get_hint,
    manage,
    submit_formula,
    teacher_exercises,
    topic_create,
    topic_delete,
    topic_reorder,
    topic_visibility,
)
from apps.home.views import home, teacher_results, teacher_student_detail
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
    path("teacher/exercises/", teacher_exercises, name="teacher_exercises"),
    path("teacher/exercises/new/", exercise_builder, name="exercise_builder"),
    path("teacher/exercises/<int:exercise_id>/edit/", exercise_builder, name="exercise_edit"),
    path("teacher/manage/", manage, name="manage"),
    path("teacher/manage/topics/new/", topic_create, name="topic_create"),
    path("teacher/manage/topics/<int:topic_id>/delete/", topic_delete, name="topic_delete"),
    path("teacher/manage/topics/<int:topic_id>/visibility/", topic_visibility, name="topic_visibility"),
    path("teacher/manage/topics/reorder/", topic_reorder, name="topic_reorder"),
    path("teacher/exercises/<int:exercise_id>/delete/", exercise_delete, name="exercise_delete"),
    path("results/", teacher_results, name="results"),
    path("results/student/<int:student_id>/", teacher_student_detail, name="teacher_student_detail"),
    path("admin/", admin.site.urls),
    path("api/", api.urls),
]
