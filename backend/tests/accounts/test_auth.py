from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError
from django.test import RequestFactory, TestCase

from apps.accounts.constants import LOGIN_URL
from apps.accounts.middleware import (
    SupabaseAuthMiddleware,
    supabase_login_required,
    teacher_required,
)
from apps.accounts.models import Profile
from apps.accounts.views import _get_or_create_profile


def fake_user(email="alice@uni.edu", name="Alice Example", uid="uuid-123"):
    """Stand-in for a Supabase gotrue User object."""
    return SimpleNamespace(
        id=uid,
        email=email,
        user_metadata={"full_name": name} if name else {},
    )


class ProfileModelTests(TestCase):
    def test_maps_to_users_table(self):
        self.assertEqual(Profile._meta.db_table, "Users")

    def test_email_is_unique(self):
        Profile.objects.create(email="a@uni.edu")
        with self.assertRaises(IntegrityError):
            Profile.objects.create(email="a@uni.edu")

    def test_role_defaults_to_student(self):
        p = Profile.objects.create(email="a@uni.edu")
        self.assertEqual(p.role, Profile.ROLE_STUDENT)


class GetOrCreateProfileTests(TestCase):
    def test_creates_row_for_new_email_with_name_from_metadata(self):
        profile = _get_or_create_profile(fake_user())
        self.assertEqual(profile.email, "alice@uni.edu")
        self.assertEqual(profile.name, "Alice Example")
        self.assertEqual(Profile.objects.count(), 1)

    def test_missing_metadata_leaves_name_blank(self):
        profile = _get_or_create_profile(fake_user(name=""))
        self.assertEqual(profile.name, "")

    def test_existing_email_returns_same_row_no_duplicate(self):
        first = _get_or_create_profile(fake_user())
        second = _get_or_create_profile(fake_user(name="Renamed"))
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(Profile.objects.count(), 1)
        # name is only set on creation, not overwritten on later logins
        self.assertEqual(second.name, "Alice Example")

    def test_concurrent_race_falls_back_to_fetch(self):
        # Pre-create the row the "winning" request would have made.
        existing = Profile.objects.create(email="alice@uni.edu")
        with patch.object(
            Profile.objects, "get_or_create", side_effect=IntegrityError
        ):
            profile = _get_or_create_profile(fake_user())
        self.assertEqual(profile.pk, existing.pk)


class DecoratorTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def _view(self):
        return lambda request: "OK"

    def test_login_required_redirects_anonymous(self):
        request = self.factory.get("/protected/")
        request.supabase_user = None
        response = supabase_login_required(self._view())(request)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, LOGIN_URL)

    def test_login_required_allows_authenticated(self):
        request = self.factory.get("/protected/")
        request.supabase_user = fake_user()
        self.assertEqual(supabase_login_required(self._view())(request), "OK")

    def test_teacher_required_redirects_anonymous_to_login(self):
        request = self.factory.get("/teacher/")
        request.supabase_user = None
        request.profile = None
        response = teacher_required(self._view())(request)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, LOGIN_URL)

    def test_teacher_required_redirects_student_home(self):
        request = self.factory.get("/teacher/")
        request.supabase_user = fake_user()
        request.profile = Profile.objects.create(
            email="s@uni.edu", role=Profile.ROLE_STUDENT
        )
        response = teacher_required(self._view())(request)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/")

    def test_teacher_required_allows_teacher(self):
        request = self.factory.get("/teacher/")
        request.supabase_user = fake_user()
        request.profile = Profile.objects.create(
            email="t@uni.edu", role=Profile.ROLE_TEACHER
        )
        self.assertEqual(teacher_required(self._view())(request), "OK")


class SupabaseAuthMiddlewareTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def _run(self, request):
        middleware = SupabaseAuthMiddleware(lambda r: MagicMock(status_code=200))
        return middleware(request)

    def test_no_token_is_anonymous(self):
        request = self.factory.get("/")
        self._run(request)
        self.assertIsNone(request.supabase_user)
        self.assertIsNone(request.profile)

    @patch("apps.accounts.middleware.get_supabase_client")
    def test_valid_token_attaches_profile_by_email(self, mock_get_client):
        profile = Profile.objects.create(email="alice@uni.edu")
        client = MagicMock()
        client.auth.get_user.return_value = SimpleNamespace(user=fake_user())
        mock_get_client.return_value = client

        request = self.factory.get("/")
        request.COOKIES["sb-access-token"] = "tok"
        self._run(request)

        self.assertEqual(request.supabase_user.email, "alice@uni.edu")
        self.assertEqual(request.profile.pk, profile.pk)

    @patch("apps.accounts.middleware.get_supabase_client")
    def test_valid_token_unknown_email_leaves_profile_none(self, mock_get_client):
        client = MagicMock()
        client.auth.get_user.return_value = SimpleNamespace(user=fake_user())
        mock_get_client.return_value = client

        request = self.factory.get("/")
        request.COOKIES["sb-access-token"] = "tok"
        self._run(request)

        self.assertIsNotNone(request.supabase_user)
        self.assertIsNone(request.profile)


class SetRoleCommandTests(TestCase):
    def test_promotes_existing_profile(self):
        Profile.objects.create(email="t@uni.edu", role=Profile.ROLE_STUDENT)
        call_command("set_role", "t@uni.edu", "teacher")
        self.assertEqual(
            Profile.objects.get(email="t@uni.edu").role, Profile.ROLE_TEACHER
        )

    def test_errors_on_unknown_email(self):
        with self.assertRaises(CommandError):
            call_command("set_role", "ghost@uni.edu", "teacher")
