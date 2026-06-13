import functools
from urllib.parse import parse_qs, urlencode, urlparse

from django.conf import settings
from django.shortcuts import redirect
from gotrue.errors import AuthApiError
from supabase import create_client

from config.supabase_client import get_supabase_client

from .constants import ACCESS_TOKEN_MAX_AGE, LOGIN_URL, REFRESH_TOKEN_MAX_AGE
from .models import Profile


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
                supabase = get_supabase_client()
                user_response = supabase.auth.get_user(token)
                request.supabase_user = user_response.user
                if request.supabase_user:
                    request.profile = Profile.objects.filter(
                        supabase_user_id=request.supabase_user.id
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
                                supabase_user_id=request.supabase_user.id
                            ).first()
                    except Exception:
                        pass
            except Exception:
                pass

        response = self.get_response(request)

        if request._refreshed_session:
            is_secure = request.is_secure()
            common = {"httponly": True, "secure": is_secure, "samesite": "Lax"}
            sess = request._refreshed_session
            response.set_cookie("sb-access-token", sess.access_token, max_age=ACCESS_TOKEN_MAX_AGE, **common)
            response.set_cookie("sb-refresh-token", sess.refresh_token, max_age=REFRESH_TOKEN_MAX_AGE, **common)

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


def supabase_login_required(view_func):
    @functools.wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if request.supabase_user is None:
            return redirect(LOGIN_URL)
        return view_func(request, *args, **kwargs)
    return wrapper


def teacher_required(view_func):
    @functools.wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if request.supabase_user is None:
            return redirect(LOGIN_URL)
        if request.profile is None or request.profile.role != Profile.ROLE_TEACHER:
            return redirect("/")
        return view_func(request, *args, **kwargs)
    return wrapper
