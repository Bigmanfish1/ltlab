from django.contrib import admin
from django.urls import include, path

from apps.checker.views import counterexample, equivalence_check, verify_ltl
from apps.exercises.views import (
    exercise_builder,
    exercise_canvas,
    exercise_delete,
    exercises,
    manage,
    submit_buchi,
    submit_buchi_word,
    submit_formula,
    submit_kripke,
    submit_part,
    teacher_exercises,
    test_formula,
    topic_create,
    topic_delete,
    topic_reorder,
    topic_update,
    topic_visibility,
)
from apps.home.views import home, teacher_results, teacher_student_detail
from config.api import api
from config.views import sandbox

urlpatterns = [
    path("accounts/", include("apps.accounts.urls", namespace="accounts")),
    path("", home, name="home"),
    path("exercises/", exercises, name="exercises"),
    path("exercises/<uuid:exercise_id>/", exercise_canvas, name="exercise_canvas"),
    path('exercises/<uuid:exercise_id>/submit/', submit_formula, name='submit_formula'),
    path('exercises/<uuid:exercise_id>/submit-model/', submit_kripke, name='submit_kripke'),
    path('exercises/<uuid:exercise_id>/submit-buchi/', submit_buchi, name='submit_buchi'),
    path('exercises/<uuid:exercise_id>/submit-word/', submit_buchi_word, name='submit_buchi_word'),
    path(
        "exercises/<uuid:exercise_id>/parts/<uuid:part_id>/submit/",
        submit_part,
        name="submit_part",
    ),
    path("sandbox/", sandbox, name="sandbox"),
    path("sandbox/verify/", verify_ltl, name="sandbox_verify"),
    path("sandbox/equivalence/", equivalence_check, name="sandbox_equivalence"),
    path("sandbox/counterexample/", counterexample, name="sandbox_counterexample"),
    path("teacher/exercises/", teacher_exercises, name="teacher_exercises"),
    path("teacher/exercises/new/", exercise_builder, name="exercise_builder"),
    path("teacher/exercises/test-formula/", test_formula, name="test_formula"),
    path("teacher/exercises/<uuid:exercise_id>/edit/", exercise_builder, name="exercise_edit"),
    path("teacher/manage/", manage, name="manage"),
    path("teacher/manage/topics/new/", topic_create, name="topic_create"),
    path("teacher/manage/topics/<uuid:topic_id>/edit/", topic_update, name="topic_update"),
    path("teacher/manage/topics/<uuid:topic_id>/delete/", topic_delete, name="topic_delete"),
    path("teacher/manage/topics/<uuid:topic_id>/visibility/", topic_visibility, name="topic_visibility"),
    path("teacher/manage/topics/reorder/", topic_reorder, name="topic_reorder"),
    path("teacher/exercises/<uuid:exercise_id>/delete/", exercise_delete, name="exercise_delete"),
    path("results/", teacher_results, name="results"),
    path("results/student/<uuid:student_id>/", teacher_student_detail, name="teacher_student_detail"),
    path("admin/", admin.site.urls),
    path("api/", api.urls),
]
