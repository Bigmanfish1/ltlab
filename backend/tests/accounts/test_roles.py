import uuid
from unittest.mock import patch

from django.http import HttpResponse
from django.test import RequestFactory, TestCase
from django.urls import reverse

from apps.accounts.constants import VIEW_AS_COOKIE
from apps.accounts.models import Profile
from apps.accounts.view_as import is_previewing, set_view_as_student
from apps.exercises.models import Attempt, Exercise, Topic
from apps.exercises.views.submit import record_attempt

from .test_auth import fake_claims


def _signed_view_as_cookie():
    """A genuinely signed 'view as student' cookie value (as the app would set)."""
    response = HttpResponse()
    set_view_as_student(response, is_secure=False)
    return response.cookies[VIEW_AS_COOKIE].value


class PreviewTests(TestCase):
    """A. is_previewing: downgrade-only, tamper-proof."""

    def setUp(self):
        self.factory = RequestFactory()

    def _request(self, role, cookie_value=None):
        request = self.factory.get("/")
        request.profile = Profile(email="x@uni.edu", role=role, id=uuid.uuid4())
        if cookie_value is not None:
            request.COOKIES[VIEW_AS_COOKIE] = cookie_value
        return request

    def test_teacher_with_valid_cookie_is_previewing(self):
        request = self._request(Profile.ROLE_TEACHER, _signed_view_as_cookie())
        self.assertTrue(is_previewing(request))

    def test_student_with_valid_cookie_cannot_preview(self):
        # Same valid cookie, but a student can never downgrade/escalate.
        request = self._request(Profile.ROLE_STUDENT, _signed_view_as_cookie())
        self.assertFalse(is_previewing(request))

    def test_teacher_with_tampered_cookie_is_not_previewing(self):
        request = self._request(Profile.ROLE_TEACHER, "garbage-not-a-signature")
        self.assertFalse(is_previewing(request))

    def test_teacher_with_no_cookie_is_not_previewing(self):
        request = self._request(Profile.ROLE_TEACHER)
        self.assertFalse(is_previewing(request))


class _AuthedClientTests(TestCase):
    """Drives real requests through the middleware stack + URL routing, faking a
    verified Supabase session (verify_token mocked) joined to a Profile by email."""

    def setUp(self):
        patcher = patch("apps.accounts.middleware.verify_token")
        self.mock_verify = patcher.start()
        self.addCleanup(patcher.stop)

    def login_as(self, profile, previewing=False, session_id="sess-1"):
        self.mock_verify.return_value = fake_claims(
            email=profile.email, session_id=session_id
        )
        self.client.cookies["sb-access-token"] = "tok"
        if previewing:
            self.client.cookies[VIEW_AS_COOKIE] = _signed_view_as_cookie()


class RoleManagementTests(_AuthedClientTests):
    """B. set_user_role endpoint."""

    def setUp(self):
        super().setUp()
        self.teacher = Profile.objects.create(
            email="teacher@uni.edu", role=Profile.ROLE_TEACHER, id=uuid.uuid4()
        )
        self.student = Profile.objects.create(
            email="student@uni.edu", role=Profile.ROLE_STUDENT, id=uuid.uuid4()
        )

    def _url(self, profile):
        return reverse("set_user_role", args=[profile.id])

    def test_student_cannot_change_roles(self):
        self.login_as(self.student)
        victim = Profile.objects.create(
            email="victim@uni.edu", role=Profile.ROLE_STUDENT, id=uuid.uuid4()
        )
        response = self.client.post(self._url(victim), {"role": Profile.ROLE_TEACHER})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/")
        victim.refresh_from_db()
        self.assertEqual(victim.role, Profile.ROLE_STUDENT)

    def test_invalid_role_is_rejected_without_error(self):
        self.login_as(self.teacher)
        response = self.client.post(self._url(self.student), {"role": "admin"})
        self.assertEqual(response.status_code, 302)
        self.student.refresh_from_db()
        self.assertEqual(self.student.role, Profile.ROLE_STUDENT)

    def test_missing_role_is_rejected_without_error(self):
        self.login_as(self.teacher)
        response = self.client.post(self._url(self.student), {})
        self.assertEqual(response.status_code, 302)
        self.student.refresh_from_db()
        self.assertEqual(self.student.role, Profile.ROLE_STUDENT)

    def test_teacher_cannot_change_own_role(self):
        self.login_as(self.teacher)
        response = self.client.post(
            self._url(self.teacher), {"role": Profile.ROLE_STUDENT}
        )
        self.assertEqual(response.status_code, 302)
        self.teacher.refresh_from_db()
        self.assertEqual(self.teacher.role, Profile.ROLE_TEACHER)

    def test_teacher_promotes_student(self):
        self.login_as(self.teacher)
        response = self.client.post(
            self._url(self.student), {"role": Profile.ROLE_TEACHER}
        )
        self.assertEqual(response.status_code, 302)
        self.student.refresh_from_db()
        self.assertEqual(self.student.role, Profile.ROLE_TEACHER)

    def test_teacher_demotes_teacher(self):
        other = Profile.objects.create(
            email="other@uni.edu", role=Profile.ROLE_TEACHER, id=uuid.uuid4()
        )
        self.login_as(self.teacher)
        response = self.client.post(self._url(other), {"role": Profile.ROLE_STUDENT})
        self.assertEqual(response.status_code, 302)
        other.refresh_from_db()
        self.assertEqual(other.role, Profile.ROLE_STUDENT)

    def test_get_is_not_allowed(self):
        self.login_as(self.teacher)
        response = self.client.get(self._url(self.student))
        self.assertEqual(response.status_code, 405)


class RolesPageTests(_AuthedClientTests):
    """C. manage_users list page."""

    def setUp(self):
        super().setUp()
        self.teacher = Profile.objects.create(
            email="teacher@uni.edu", role=Profile.ROLE_TEACHER, id=uuid.uuid4()
        )
        self.student = Profile.objects.create(
            email="student@uni.edu", role=Profile.ROLE_STUDENT, id=uuid.uuid4()
        )
        self.url = reverse("manage_users")

    def test_teacher_sees_page(self):
        self.login_as(self.teacher)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)

    def test_student_is_redirected(self):
        self.login_as(self.student)
        response = self.client.get(self.url)
        self.assertNotEqual(response.status_code, 200)
        self.assertEqual(response.status_code, 302)

    def test_previewing_teacher_steps_aside_to_dashboard(self):
        self.login_as(self.teacher, previewing=True)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("home"))


class ViewAsToggleTests(_AuthedClientTests):
    """D. enter / exit preview toggle endpoints."""

    def setUp(self):
        super().setUp()
        self.teacher = Profile.objects.create(
            email="teacher@uni.edu", role=Profile.ROLE_TEACHER, id=uuid.uuid4()
        )
        self.student = Profile.objects.create(
            email="student@uni.edu", role=Profile.ROLE_STUDENT, id=uuid.uuid4()
        )
        self.enter_url = reverse("accounts:view_as_student")
        self.exit_url = reverse("accounts:view_as_teacher")

    def test_teacher_enter_sets_cookie(self):
        self.login_as(self.teacher)
        response = self.client.post(self.enter_url)
        self.assertIn(VIEW_AS_COOKIE, response.cookies)
        self.assertTrue(response.cookies[VIEW_AS_COOKIE].value)

    def test_student_enter_is_blocked_and_sets_no_cookie(self):
        self.login_as(self.student)
        response = self.client.post(self.enter_url)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/")
        self.assertNotIn(VIEW_AS_COOKIE, response.cookies)

    def test_exit_clears_cookie(self):
        self.login_as(self.teacher, previewing=True)
        response = self.client.post(self.exit_url)
        self.assertIn(VIEW_AS_COOKIE, response.cookies)
        self.assertEqual(response.cookies[VIEW_AS_COOKIE].value, "")


class RecordAttemptTests(TestCase):
    """E. record_attempt persists only when not previewing."""

    def setUp(self):
        self.factory = RequestFactory()
        self.student = Profile.objects.create(
            email="student@uni.edu", role=Profile.ROLE_STUDENT, id=uuid.uuid4()
        )
        topic = Topic.objects.create(title="T", created_by=self.student)
        self.exercise = Exercise.objects.create(
            topic=topic,
            title="Ex",
            description="d",
            difficulty="beginner",
            hint="h",
        )

    def _fields(self):
        return dict(
            exercise=self.exercise,
            student=self.student,
            formula_input="F p",
            is_correct=True,
            hints_used=0,
        )

    def test_preview_records_nothing(self):
        request = self.factory.post("/submit/")
        request.is_previewing = True
        result = record_attempt(request, **self._fields())
        self.assertIsNone(result)
        self.assertEqual(Attempt.objects.count(), 0)

    def test_normal_submission_records_attempt(self):
        request = self.factory.post("/submit/")
        request.is_previewing = False
        result = record_attempt(request, **self._fields())
        self.assertIsNotNone(result)
        self.assertEqual(Attempt.objects.count(), 1)
        self.assertEqual(result.student_id, self.student.id)
