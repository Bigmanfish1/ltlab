import functools
import logging
from urllib.parse import parse_qs, urlencode, urlparse

from django.conf import settings
from django.shortcuts import redirect
from gotrue.errors import AuthApiError
from supabase import create_client

from config.supabase_client import get_cached_user

from .auth_cookies import clear_auth_cookies, set_auth_cookies
from .constants import LOGIN_URL
from .models import Profile

logger = logging.getLogger(__name__)


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
                # Cached per token for a few seconds; only a cache miss hits the
                # network (see config.supabase_client.get_cached_user).
                request.supabase_user = get_cached_user(token)
                if request.supabase_user:
                    request.profile = Profile.objects.filter(
                        email=request.supabase_user.email
                    ).first()
            except AuthApiError:
                # Access token expired — attempt silent refresh
                refresh_token = request.COOKIES.get("sb-refresh-token")
                if refresh_token:
                    try:
                        client = create_client(settings.SUPABASE_URL, settings.SUPABASE_ANON_KEY)
                        result = client.auth.refresh_session(refresh_token)
                        request.supabase_user = result.user
                        request._refreshed_session = result.session
                        if request.supabase_user:
                            request.profile = Profile.objects.filter(
                                email=request.supabase_user.email
                            ).first()
                    except Exception:
                        # Refresh failed (revoked/expired refresh token) — stay
                        # anonymous, but record it so a broken Supabase is visible.
                        logger.warning("Supabase token refresh failed", exc_info=True)
            except Exception:
                # Unexpected error validating the token (e.g. Supabase down or
                # misconfigured). Fail closed to anonymous, but don't go silent.
                logger.exception("Supabase auth check failed")

        response = self.get_response(request)

        if request._refreshed_session:
            set_auth_cookies(response, request._refreshed_session, request.is_secure())

        return response


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
