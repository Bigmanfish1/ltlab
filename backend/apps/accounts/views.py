import httpx
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.db import IntegrityError, transaction
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_http_methods
from gotrue.errors import AuthApiError
from supabase import create_client

from .constants import (
    ACCESS_TOKEN_MAX_AGE,
    LOGIN_URL,
    PKCE_VERIFIER_COOKIE,
    PKCE_VERIFIER_MAX_AGE,
    REFRESH_TOKEN_MAX_AGE,
)
from .models import Profile

AUTH_ERROR_MESSAGES = {
    "bad_oauth_callback": "Google sign-in failed. Please try again.",
    "bad_oauth_state": "Your sign-in session expired. Please try again.",
    "provider_email_needs_verification": "Please verify your email with Google before continuing.",
}


def _safe_next(url: str, request) -> str:
    """Return url if it points within this site, else '/'.

    Uses Django's host/scheme check, which (unlike a hand-rolled urlparse)
    also rejects backslash tricks like '/\\evil.com' that browsers normalise
    into a protocol-relative redirect to an external host.
    """
    if url and url_has_allowed_host_and_scheme(
        url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return url
    return ""


def _set_auth_cookies(response, session, is_secure: bool) -> None:
    common = {"httponly": True, "secure": is_secure, "samesite": "Lax"}
    response.set_cookie("sb-access-token", session.access_token, max_age=ACCESS_TOKEN_MAX_AGE, **common)
    response.set_cookie("sb-refresh-token", session.refresh_token, max_age=REFRESH_TOKEN_MAX_AGE, **common)


def _clear_auth_cookies(response) -> None:
    response.delete_cookie("sb-access-token", samesite="Lax")
    response.delete_cookie("sb-refresh-token", samesite="Lax")


def _auth_error_message(e: AuthApiError) -> str:
    return AUTH_ERROR_MESSAGES.get(e.code, str(e.message))


def _pop_pkce_verifier(supabase) -> str | None:
    key = f"{supabase.auth._storage_key}-code-verifier"
    verifier = supabase.auth._storage.get_item(key)
    supabase.auth._storage.remove_item(key)
    return verifier


def _get_or_create_profile(user) -> Profile:
    try:
        # atomic() so a losing race on the unique supabase_user_id raises
        # IntegrityError cleanly instead of poisoning the request transaction.
        with transaction.atomic():
            profile, _ = Profile.objects.get_or_create(
                supabase_user_id=user.id,
                defaults={"email": user.email},
            )
    except IntegrityError:
        # Concurrent first login created the row first — fetch theirs.
        profile = Profile.objects.get(supabase_user_id=user.id)
    if profile.email != user.email:
        profile.email = user.email
        profile.save(update_fields=["email"])
    return profile


@require_http_methods(["GET"])
def login_view(request):
    """Render the login landing page. OAuth is initiated by google_oauth_view."""
    if request.supabase_user:
        return redirect("/")
    return render(request, "accounts/login.html")


@require_http_methods(["GET"])
def google_oauth_view(request):
    """Initiate the Google OAuth PKCE flow and redirect to the provider."""
    if request.supabase_user:
        return redirect("/")

    next_url = _safe_next(request.GET.get("next", ""), request)

    redirect_to = request.build_absolute_uri(reverse("accounts:callback"))
    if next_url:
        redirect_to = f"{redirect_to}?{urlencode({'next': next_url})}"

    try:
        # Fresh client per request — PKCE verifier lives in the client's
        # in-memory _storage, so a shared singleton would let concurrent
        # logins overwrite each other's verifier.
        supabase = create_client(settings.SUPABASE_URL, settings.SUPABASE_ANON_KEY)
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
        return redirect("accounts:login")


@require_http_methods(["GET"])
def oauth_callback_view(request):
    code = request.GET.get("code")
    if not code:
        messages.error(request, "Google sign-in failed. Please try again.")
        return redirect("accounts:login")

    verifier = request.COOKIES.get(PKCE_VERIFIER_COOKIE)

    try:
        supabase = create_client(settings.SUPABASE_URL, settings.SUPABASE_ANON_KEY)
        result = supabase.auth.exchange_code_for_session(
            {"auth_code": code, "code_verifier": verifier}
        )
    except AuthApiError as e:
        messages.error(request, _auth_error_message(e))
        return redirect("accounts:login")
    except Exception:
        messages.error(request, "Something went wrong. Please try again.")
        return redirect("accounts:login")

    if not result.session or not result.user:
        messages.error(request, "Google sign-in failed. Please try again.")
        return redirect("accounts:login")

    _get_or_create_profile(result.user)

    next_url = _safe_next(request.GET.get("next", ""), request) or "/"

    response = redirect(next_url)
    _set_auth_cookies(response, result.session, request.is_secure())
    response.delete_cookie(PKCE_VERIFIER_COOKIE)
    return response


@require_http_methods(["POST"])
def logout_view(request):
    token = request.COOKIES.get("sb-access-token")
    if token:
        try:
            # Call the Supabase logout endpoint directly with the user's token.
            # The shared singleton client has no session stored, so
            # supabase.auth.sign_out() would be a no-op against the server.
            httpx.post(
                f"{settings.SUPABASE_URL}/auth/v1/logout",
                headers={
                    "Authorization": f"Bearer {token}",
                    "apikey": settings.SUPABASE_ANON_KEY,
                },
                params={"scope": "global"},
                timeout=5.0,
            )
        except Exception:
            pass

    response = redirect(LOGIN_URL)
    _clear_auth_cookies(response)
    return response
