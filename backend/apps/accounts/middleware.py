from urllib.parse import parse_qs, urlencode, urlparse

from django.shortcuts import redirect

from config.supabase_client import get_supabase_client

from .models import Profile


class SupabaseAuthMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        token = request.COOKIES.get("sb-access-token")
        request.supabase_user = None
        request.profile = None

        if token:
            try:
                supabase = get_supabase_client()
                response = supabase.auth.get_user(token)
                request.supabase_user = response.user
                if request.supabase_user:
                    request.profile = Profile.objects.filter(
                        supabase_user_id=request.supabase_user.id
                    ).first()
            except Exception:
                pass

        return self.get_response(request)


class HtmxAuthRedirectMiddleware:
    """
    When HTMX makes a partial request and gets a 302, it tries to load the
    login page into a small div instead of navigating the whole browser.
    This middleware detects that and sends HX-Redirect instead.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        if request.headers.get("HX-Request") == "true" and response.status_code == 302:
            ref_header = request.headers.get("Referer", "")
            next_path = urlparse(ref_header).path if ref_header else request.path

            redirect_url = urlparse(response["Location"])
            query_params = parse_qs(redirect_url.query)
            query_params["next"] = [next_path]

            response.status_code = 204
            response.headers["HX-Redirect"] = (
                f"{redirect_url.path}?{urlencode(query_params, doseq=True)}"
            )

        return response


def supabase_login_required(view_func):
    def wrapper(request, *args, **kwargs):
        if request.supabase_user is None:
            return redirect("/accounts/login/")
        return view_func(request, *args, **kwargs)
    return wrapper


def teacher_required(view_func):
    def wrapper(request, *args, **kwargs):
        if request.supabase_user is None:
            return redirect("/accounts/login/")
        if request.profile is None or request.profile.role != Profile.ROLE_TEACHER:
            return redirect("/")
        return view_func(request, *args, **kwargs)
    return wrapper
