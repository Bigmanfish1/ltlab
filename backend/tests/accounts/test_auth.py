import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import jwt
from cryptography.hazmat.primitives.asymmetric import ec
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError
from django.shortcuts import redirect
from django.test import RequestFactory, TestCase

from apps.accounts.constants import LOGIN_URL
from apps.accounts.jwt_auth import (
    SupabaseUser,
    _denylist,
    is_session_revoked,
    revoke_session,
    verify_token,
)
from apps.accounts.middleware import (
    HtmxAuthRedirectMiddleware,
    SupabaseAuthMiddleware,
    supabase_login_required,
    teacher_required,
)
from apps.accounts.models import Profile
from apps.accounts.views import _get_or_create_profile, logout_view


def fake_user(email="alice@uni.edu", name="Alice Example", uid="uuid-123"):
    """Stand-in for a Supabase gotrue User object (refresh path)."""
    return SimpleNamespace(
        id=uid,
        email=email,
        user_metadata={"full_name": name} if name else {},
    )


def fake_claims(email="alice@uni.edu", name="Alice Example", sub="uuid-123", session_id="sess-1"):
    """Stand-in for verified Supabase JWT claims."""
    return {
        "sub": sub,
        "email": email,
        "user_metadata": {"full_name": name} if name else {},
        "session_id": session_id,
    }


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
        request.profile = None
        response = supabase_login_required(self._view())(request)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, LOGIN_URL)

    def test_login_required_allows_authenticated_with_profile(self):
        request = self.factory.get("/protected/")
        request.supabase_user = fake_user()
        request.profile = Profile.objects.create(email="alice@uni.edu")
        self.assertEqual(supabase_login_required(self._view())(request), "OK")

    def test_login_required_bounces_authenticated_without_profile(self):
        # Orphaned session (Profile deleted or email changed): re-login + clear.
        request = self.factory.get("/protected/")
        request.supabase_user = fake_user()
        request.profile = None
        response = supabase_login_required(self._view())(request)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, LOGIN_URL)
        self.assertEqual(response.cookies["sb-access-token"].value, "")
        self.assertEqual(response.cookies["sb-refresh-token"].value, "")

    def test_teacher_required_redirects_anonymous_to_login(self):
        request = self.factory.get("/teacher/")
        request.supabase_user = None
        request.profile = None
        response = teacher_required(self._view())(request)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, LOGIN_URL)

    def test_teacher_required_bounces_authenticated_without_profile(self):
        request = self.factory.get("/teacher/")
        request.supabase_user = fake_user()
        request.profile = None
        response = teacher_required(self._view())(request)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, LOGIN_URL)
        self.assertEqual(response.cookies["sb-access-token"].value, "")

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


class JwtAuthTests(TestCase):
    def setUp(self):
        _denylist.clear()

    def tearDown(self):
        _denylist.clear()

    def test_supabase_user_reads_claims(self):
        u = SupabaseUser(fake_claims())
        self.assertEqual(u.id, "uuid-123")
        self.assertEqual(u.email, "alice@uni.edu")
        self.assertEqual(u.user_metadata["full_name"], "Alice Example")
        self.assertEqual(u.session_id, "sess-1")

    def test_revoked_session_until_exp(self):
        revoke_session("sess-1", time.time() + 100)
        self.assertTrue(is_session_revoked("sess-1"))

    def test_revocation_outlives_leeway(self):
        # Stored exp is exp + LEEWAY, so a token expiring "now" stays denied past
        # verify_token's accept-leeway window.
        revoke_session("sess-1", time.time())
        self.assertTrue(is_session_revoked("sess-1"))

    def test_revocation_lapses_and_prunes_at_exp(self):
        revoke_session("sess-1", time.time() - 100)  # well past exp + leeway
        self.assertFalse(is_session_revoked("sess-1"))
        self.assertNotIn("sess-1", _denylist._revoked)

    def test_unknown_session_not_revoked(self):
        self.assertFalse(is_session_revoked("nope"))

    def test_none_session_id_is_noop(self):
        revoke_session(None, time.time() + 100)
        self.assertFalse(is_session_revoked(None))


class VerifyTokenTests(TestCase):
    """Exercise the real ES256 signature/claim checks with a locally-generated
    key (the JWKS lookup is patched to return our public key)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.priv = ec.generate_private_key(ec.SECP256R1())
        cls.iss = f"{settings.SUPABASE_URL.rstrip('/')}/auth/v1"

    def _token(self, **overrides):
        claims = {
            "sub": "uuid-1",
            "email": "alice@uni.edu",
            "session_id": "s1",
            "aud": "authenticated",
            "iss": self.iss,
            "exp": int(time.time()) + 600,
        }
        claims.update(overrides)
        return jwt.encode(claims, self.priv, algorithm="ES256")

    def _patch_key(self):
        signing = SimpleNamespace(key=self.priv.public_key())
        client = SimpleNamespace(get_signing_key_from_jwt=lambda t: signing)
        return patch("apps.accounts.jwt_auth._client", return_value=client)

    def test_valid_token_returns_claims(self):
        with self._patch_key():
            claims = verify_token(self._token())
        self.assertEqual(claims["email"], "alice@uni.edu")
        self.assertEqual(claims["session_id"], "s1")

    def test_expired_token_raises(self):
        with self._patch_key(), self.assertRaises(jwt.ExpiredSignatureError):
            verify_token(self._token(exp=int(time.time()) - 30))

    def test_wrong_audience_raises(self):
        with self._patch_key(), self.assertRaises(jwt.InvalidAudienceError):
            verify_token(self._token(aud="anon"))

    def test_wrong_issuer_raises(self):
        with self._patch_key(), self.assertRaises(jwt.InvalidIssuerError):
            verify_token(self._token(iss="https://evil.example/auth/v1"))

    def test_tampered_signature_raises(self):
        other_key = ec.generate_private_key(ec.SECP256R1())
        signing = SimpleNamespace(key=other_key.public_key())
        client = SimpleNamespace(get_signing_key_from_jwt=lambda t: signing)
        with patch("apps.accounts.jwt_auth._client", return_value=client):
            with self.assertRaises(jwt.InvalidSignatureError):
                verify_token(self._token())

    def test_expired_token_decodes_when_exp_check_disabled(self):
        # Used by the refresh path to read session_id off an expired token.
        with self._patch_key():
            claims = verify_token(
                self._token(exp=int(time.time()) - 30), verify_exp=False
            )
        self.assertEqual(claims["session_id"], "s1")


class SupabaseAuthMiddlewareTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        _denylist.clear()

    def tearDown(self):
        _denylist.clear()

    def _run(self, request):
        middleware = SupabaseAuthMiddleware(lambda r: MagicMock(status_code=200))
        return middleware(request)

    def test_no_token_is_anonymous(self):
        request = self.factory.get("/")
        self._run(request)
        self.assertIsNone(request.supabase_user)
        self.assertIsNone(request.profile)

    @patch("apps.accounts.middleware.verify_token")
    def test_valid_token_attaches_profile_by_email(self, mock_verify):
        profile = Profile.objects.create(email="alice@uni.edu")
        mock_verify.return_value = fake_claims()

        request = self.factory.get("/")
        request.COOKIES["sb-access-token"] = "tok"
        self._run(request)

        self.assertEqual(request.supabase_user.email, "alice@uni.edu")
        self.assertEqual(request.profile.pk, profile.pk)

    @patch("apps.accounts.middleware.verify_token")
    def test_valid_token_unknown_email_leaves_profile_none(self, mock_verify):
        mock_verify.return_value = fake_claims()

        request = self.factory.get("/")
        request.COOKIES["sb-access-token"] = "tok"
        self._run(request)

        self.assertIsNotNone(request.supabase_user)
        self.assertIsNone(request.profile)

    @patch("apps.accounts.middleware.verify_token")
    def test_revoked_session_is_anonymous(self, mock_verify):
        Profile.objects.create(email="alice@uni.edu")
        mock_verify.return_value = fake_claims(session_id="sess-x")
        revoke_session("sess-x", time.time() + 100)

        request = self.factory.get("/")
        request.COOKIES["sb-access-token"] = "tok"
        self._run(request)

        self.assertIsNone(request.supabase_user)
        self.assertIsNone(request.profile)

    @patch("apps.accounts.middleware.create_client")
    @patch("apps.accounts.middleware.verify_token")
    def test_expired_token_triggers_refresh(self, mock_verify, mock_create):
        profile = Profile.objects.create(email="alice@uni.edu")
        mock_verify.side_effect = jwt.ExpiredSignatureError()
        new_session = SimpleNamespace(access_token="new-a", refresh_token="new-r")
        mock_create.return_value.auth.refresh_session.return_value = SimpleNamespace(
            session=new_session, user=fake_user()
        )

        request = self.factory.get("/")
        request.COOKIES["sb-access-token"] = "old"
        request.COOKIES["sb-refresh-token"] = "refresh"
        self._run(request)

        self.assertEqual(request.profile.pk, profile.pk)
        self.assertIs(request._refreshed_session, new_session)

    @patch("apps.accounts.middleware.create_client")
    @patch("apps.accounts.middleware.verify_token")
    def test_refresh_skipped_for_revoked_session(self, mock_verify, mock_create):
        # Expired token whose session was logged out: must NOT refresh back in.
        def verify_side(token, verify_exp=True):
            if verify_exp:
                raise jwt.ExpiredSignatureError()
            return fake_claims(session_id="sess-rev")

        mock_verify.side_effect = verify_side
        revoke_session("sess-rev", time.time() + 100)

        request = self.factory.get("/")
        request.COOKIES["sb-access-token"] = "old"
        request.COOKIES["sb-refresh-token"] = "refresh"
        self._run(request)

        self.assertIsNone(request.supabase_user)
        mock_create.assert_not_called()

    @patch("apps.accounts.middleware.verify_token")
    def test_emailless_token_is_anonymous(self, mock_verify):
        # A token with no email claim can't be joined to a Profile; treat it as
        # unauthenticated instead of an authenticated-but-profileless re-login loop.
        mock_verify.return_value = fake_claims(email=None)

        request = self.factory.get("/")
        request.COOKIES["sb-access-token"] = "tok"
        self._run(request)

        self.assertIsNone(request.supabase_user)
        self.assertIsNone(request.profile)

    @patch("apps.accounts.middleware.create_client")
    @patch("apps.accounts.middleware.verify_token")
    def test_refresh_does_not_overwrite_no_profile_cookie_clear(
        self, mock_verify, mock_create
    ):
        # Silent refresh + orphaned (no-Profile) session: the decorator clears the
        # cookies; the refresh cookie write must NOT resurrect them on the response.
        def verify_side(token, verify_exp=True):
            if verify_exp:
                raise jwt.ExpiredSignatureError()
            return fake_claims(session_id="sess-live")

        mock_verify.side_effect = verify_side
        new_session = SimpleNamespace(access_token="new-a", refresh_token="new-r")
        mock_create.return_value.auth.refresh_session.return_value = SimpleNamespace(
            session=new_session, user=fake_user(email="ghost@uni.edu")
        )

        guarded = supabase_login_required(lambda r: "unreachable")
        middleware = SupabaseAuthMiddleware(guarded)

        request = self.factory.get("/protected/")
        request.COOKIES["sb-access-token"] = "old"
        request.COOKIES["sb-refresh-token"] = "refresh"
        response = middleware(request)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.cookies["sb-access-token"].value, "")
        self.assertEqual(response.cookies["sb-refresh-token"].value, "")


class HtmxAuthRedirectMiddlewareTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def _run(self, response_factory, **req_kwargs):
        mw = HtmxAuthRedirectMiddleware(response_factory)
        request = self.factory.get("/page/", **req_kwargs)
        return mw(request)

    def test_login_bounce_converted_to_hx_redirect(self):
        resp = self._run(lambda r: redirect(LOGIN_URL), HTTP_HX_REQUEST="true")
        self.assertEqual(resp.status_code, 204)
        self.assertTrue(resp.headers["HX-Redirect"].startswith(LOGIN_URL))

    def test_non_login_internal_redirect_passes_through(self):
        # Only auth bounces are rewritten; ordinary same-origin 302s stay 302
        # without a spurious ?next= injected.
        resp = self._run(lambda r: redirect("/dashboard/"), HTTP_HX_REQUEST="true")
        self.assertEqual(resp.status_code, 302)
        self.assertNotIn("HX-Redirect", resp.headers)

    def test_external_redirect_passes_through(self):
        resp = self._run(
            lambda r: redirect("https://accounts.google.com/o"), HTTP_HX_REQUEST="true"
        )
        self.assertEqual(resp.status_code, 302)
        self.assertNotIn("HX-Redirect", resp.headers)

    def test_non_htmx_request_untouched(self):
        resp = self._run(lambda r: redirect(LOGIN_URL))
        self.assertEqual(resp.status_code, 302)
        self.assertNotIn("HX-Redirect", resp.headers)


class LogoutViewTests(TestCase):
    def setUp(self):
        _denylist.clear()

    def tearDown(self):
        _denylist.clear()

    @patch("apps.accounts.views.httpx.post")
    @patch("apps.accounts.views.verify_token")
    def test_logout_revokes_session_and_clears_cookies(self, mock_verify, mock_post):
        mock_verify.return_value = {"session_id": "sess-9", "exp": time.time() + 100}

        request = RequestFactory().post("/accounts/logout/")
        request.COOKIES["sb-access-token"] = "tok"
        response = logout_view(request)

        self.assertTrue(is_session_revoked("sess-9"))
        self.assertEqual(response.cookies["sb-access-token"].value, "")
        self.assertEqual(response.cookies["sb-refresh-token"].value, "")
        mock_post.assert_called_once()


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
