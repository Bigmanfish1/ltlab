import functools
import logging
from urllib.parse import parse_qs, urlencode, urlparse

import jwt
from django.conf import settings
from django.shortcuts import redirect
from supabase import create_client

from .auth_cookies import clear_auth_cookies, set_auth_cookies
from .constants import LOGIN_URL
from .jwt_auth import SupabaseUser, is_session_revoked, verify_token
from .models import Profile

logger = logging.getLogger(__name__)


def _attach_profile(request, user):
    """Attach the authenticated user and its Profile (joined by email).

    Email is the only join key to a Profile, so a token/user with no email can't
    be matched to one. Treat that as unauthenticated rather than attaching an
    emailless user that every protected view would bounce — that would be an
    authenticated-but-profileless re-login loop.
    """
    if user is None or not getattr(user, "email", None):
        request.supabase_user = None
        request.profile = None
        return
    request.supabase_user = user
    request.profile = Profile.objects.filter(email=user.email).first()


class SupabaseAuthMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        token = request.COOKIES.get("sb-access-token")
        request.supabase_user = None
        request.profile = None
        request._refreshed_session = None

        if token:
            try:
                # Verified locally (signature + exp + aud + iss) — no network.
                claims = verify_token(token)
            except jwt.ExpiredSignatureError:
                # Access token expired — attempt silent refresh.
                self._refresh(request)
            except Exception:
                # Bad signature/aud/iss, or JWKS fetch failure. Fail closed to
                # anonymous, but don't go silent.
                logger.warning("Supabase JWT verification failed", exc_info=True)
            else:
                # Kept out of the try so a bug here (or a DB error in the profile
                # lookup) surfaces instead of being mislabelled a JWT failure.
                if not is_session_revoked(claims.get("session_id")):
                    _attach_profile(request, SupabaseUser(claims))

        response = self.get_response(request)

        if request._refreshed_session:
            set_auth_cookies(response, request._refreshed_session, request.is_secure())

        return response

    def _refresh(self, request):
        refresh_token = request.COOKIES.get("sb-refresh-token")
        if not refresh_token:
            return
        # Don't silently refresh a session that was explicitly logged out. The
        # expired access token still carries a verifiable session_id (read it
        # without the exp check) to consult the denylist.
        access_token = request.COOKIES.get("sb-access-token")
        try:
            claims = verify_token(access_token, verify_exp=False)
            if is_session_revoked(claims.get("session_id")):
                return
        except Exception:
            pass
        try:
            client = create_client(settings.SUPABASE_URL, settings.SUPABASE_ANON_KEY)
            result = client.auth.refresh_session(refresh_token)
            request._refreshed_session = result.session
            _attach_profile(request, result.user)
        except Exception:
            # Refresh failed (revoked/expired refresh token, e.g. after a global
            # logout) — stay anonymous, but record it so a broken Supabase shows.
            logger.warning("Supabase token refresh failed", exc_info=True)


class HtmxAuthRedirectMiddleware:
    """
    When HTMX makes a partial request and gets a same-origin 302, it tries to
    load the login page into a small div instead of navigating the whole browser.
    This middleware detects that and sends HX-Redirect instead.
    External redirects (e.g. OAuth provider URLs) are passed through unchanged.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        if request.headers.get("HX-Request") == "true" and response.status_code == 302:
            location = response.get("Location", "")
            redirect_url = urlparse(location)

            # External URLs (OAuth, etc.) must pass through as a plain 302 —
            # stripping the host would send the browser to a same-origin 404.
            if redirect_url.netloc:
                return response

            # Only convert auth bounces (redirects to the login page). Other
            # same-origin 302s — e.g. a post-action redirect — must stay real
            # redirects HTMX follows, not be rewritten with a spurious ?next=.
            if redirect_url.path != LOGIN_URL:
                return response

            ref_header = request.headers.get("Referer", "")
            next_path = urlparse(ref_header).path if ref_header else request.path

            query_params = parse_qs(redirect_url.query)
            query_params["next"] = [next_path]

            response.status_code = 204
            response.headers["HX-Redirect"] = (
                f"{redirect_url.path}?{urlencode(query_params, doseq=True)}"
            )

        return response


def _redirect_if_no_profile(request):
    """Shared gate for protected views.

    Returns a redirect response when the request must be bounced, else None.
    An authenticated Supabase user with no Profile row is a broken state (row
    deleted, or the user's email changed in Supabase so the email join key no
    longer matches — see Profile / _get_or_create_profile). Clear the session so
    a clean re-login recreates the Profile, rather than letting each view invent
    its own recovery.
    """
    if request.supabase_user is None:
        return redirect(LOGIN_URL)
    if request.profile is None:
        logger.warning(
            "Authenticated Supabase user %s has no Profile (deleted or email "
            "changed); forcing re-login.",
            getattr(request.supabase_user, "email", "?"),
        )
        response = redirect(LOGIN_URL)
        clear_auth_cookies(response)
        # Disarm any pending silent-refresh cookie write: SupabaseAuthMiddleware
        # re-sets auth cookies after the view returns, which would otherwise
        # overwrite this clear when a refresh and a missing Profile coincide.
        request._refreshed_session = None
        return response
    return None


def supabase_login_required(view_func):
    @functools.wraps(view_func)
    def wrapper(request, *args, **kwargs):
        bounce = _redirect_if_no_profile(request)
        if bounce is not None:
            return bounce
        return view_func(request, *args, **kwargs)
    return wrapper


def teacher_required(view_func):
    @functools.wraps(view_func)
    def wrapper(request, *args, **kwargs):
        bounce = _redirect_if_no_profile(request)
        if bounce is not None:
            return bounce
        if request.profile.role != Profile.ROLE_TEACHER:
            return redirect("/")
        return view_func(request, *args, **kwargs)
    return wrapper
