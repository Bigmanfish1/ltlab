from urllib.parse import urlencode, urlparse

from django.contrib import messages
from django.shortcuts import redirect
from django.urls import reverse
from django.views.decorators.http import require_http_methods
from gotrue.errors import AuthApiError

from config.supabase_client import get_supabase_client

from .models import Profile

ACCESS_TOKEN_MAX_AGE = 60 * 60
REFRESH_TOKEN_MAX_AGE = 60 * 60 * 24 * 7
PKCE_VERIFIER_MAX_AGE = 60 * 10
PKCE_VERIFIER_COOKIE = "sb-pkce-verifier"

AUTH_ERROR_MESSAGES = {
    "bad_oauth_callback": "Google sign-in failed. Please try again.",
    "bad_oauth_state": "Your sign-in session expired. Please try again.",
    "provider_email_needs_verification": "Please verify your email with Google before continuing.",
}


def _is_safe_redirect(url: str) -> bool:
    parsed = urlparse(url)
    return not parsed.netloc and not parsed.scheme


def _set_auth_cookies(response, session, is_secure: bool) -> None:
    common = {"httponly": True, "secure": is_secure, "samesite": "Lax"}
    response.set_cookie("sb-access-token", session.access_token, max_age=ACCESS_TOKEN_MAX_AGE, **common)
    response.set_cookie("sb-refresh-token", session.refresh_token, max_age=REFRESH_TOKEN_MAX_AGE, **common)


def _clear_auth_cookies(response) -> None:
    response.delete_cookie("sb-access-token")
    response.delete_cookie("sb-refresh-token")


def _auth_error_message(e: AuthApiError) -> str:
    return AUTH_ERROR_MESSAGES.get(e.code, str(e.message))


def _pop_pkce_verifier(supabase) -> str | None:
    key = f"{supabase.auth._storage_key}-code-verifier"
    verifier = supabase.auth._storage.get_item(key)
    supabase.auth._storage.remove_item(key)
    return verifier


def _get_or_create_profile(user) -> Profile:
    profile, _ = Profile.objects.get_or_create(
        supabase_user_id=user.id,
        defaults={"email": user.email},
    )
    if profile.email != user.email:
        profile.email = user.email
        profile.save(update_fields=["email"])
    return profile


@require_http_methods(["GET"])
def login_view(request):
    if request.supabase_user:
        return redirect("/")

    next_url = request.GET.get("next", "")
    if next_url and not _is_safe_redirect(next_url):
        next_url = ""

    redirect_to = request.build_absolute_uri(reverse("accounts:callback"))
    if next_url:
        redirect_to = f"{redirect_to}?{urlencode({'next': next_url})}"

    try:
        supabase = get_supabase_client()
        result = supabase.auth.sign_in_with_oauth(
            {
                "provider": "google",
                "options": {"redirect_to": redirect_to},
            }
        )
        verifier = _pop_pkce_verifier(supabase)
        response = redirect(result.url)
        if verifier:
            response.set_cookie(
                PKCE_VERIFIER_COOKIE,
                verifier,
                max_age=PKCE_VERIFIER_MAX_AGE,
                httponly=True,
                secure=request.is_secure(),
                samesite="Lax",
            )
        return response
    except Exception:
        messages.error(request, "Couldn't reach Google sign-in. Please try again.")
        return redirect("/")


@require_http_methods(["GET"])
def oauth_callback_view(request):
    code = request.GET.get("code")
    if not code:
        messages.error(request, "Google sign-in failed. Please try again.")
        return redirect("accounts:login")

    verifier = request.COOKIES.get(PKCE_VERIFIER_COOKIE)

    try:
        supabase = get_supabase_client()
        result = supabase.auth.exchange_code_for_session(
            {"auth_code": code, "code_verifier": verifier}
        )
    except AuthApiError as e:
        messages.error(request, _auth_error_message(e))
        return redirect("accounts:login")
    except Exception:
        messages.error(request, "Something went wrong. Please try again.")
        return redirect("accounts:login")

    _get_or_create_profile(result.user)

    next_url = request.GET.get("next", "/")
    if not _is_safe_redirect(next_url):
        next_url = "/"

    response = redirect(next_url)
    _set_auth_cookies(response, result.session, request.is_secure())
    response.delete_cookie(PKCE_VERIFIER_COOKIE)
    return response


@require_http_methods(["POST"])
def logout_view(request):
    token = request.COOKIES.get("sb-access-token")
    if token:
        try:
            supabase = get_supabase_client()
            supabase.auth.sign_out()
        except Exception:
            pass

    response = redirect("/accounts/login/")
    _clear_auth_cookies(response)
    return response
