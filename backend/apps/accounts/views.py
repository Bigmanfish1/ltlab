import logging
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

from .auth_cookies import clear_auth_cookies, set_auth_cookies
from .constants import (
    LOGIN_URL,
    PKCE_VERIFIER_COOKIE,
    PKCE_VERIFIER_MAX_AGE,
)
from .jwt_auth import auth_base_url, revoke_session, verify_token
from .models import Profile

logger = logging.getLogger(__name__)

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


def _auth_error_message(e: AuthApiError) -> str:
    return AUTH_ERROR_MESSAGES.get(e.code, str(e.message))


def _pop_pkce_verifier(supabase) -> str | None:
    key = f"{supabase.auth._storage_key}-code-verifier"
    verifier = supabase.auth._storage.get_item(key)
    supabase.auth._storage.remove_item(key)
    return verifier


def _get_or_create_profile(user) -> Profile:
    # The Supabase auth id is a UUID; the Users table keys on an int8 identity
    # column, so email (unique on both sides, verified by Google) is the join key.
    # Caveat: email is the *only* link, so if a user's Supabase email changes the
    # old row is orphaned and a fresh one is created here (role/history reset).
    # Callers must guarantee user.email is set (the OAuth callback checks this).
    name = (user.user_metadata or {}).get("full_name", "")
    try:
        # atomic() so a losing race on the unique email raises IntegrityError
        # cleanly instead of poisoning the request transaction.
        with transaction.atomic():
            profile, _ = Profile.objects.get_or_create(
                email=user.email,
                defaults={"name": name},
            )
    except IntegrityError:
        # Concurrent first login created the row first — fetch theirs.
        profile = Profile.objects.get(email=user.email)
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
        # Includes breakage in _pop_pkce_verifier's use of supabase-py internals
        # on a dependency bump — log it so the cause isn't hidden behind the
        # generic message below.
        logger.exception("Google OAuth initiation failed")
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

    # email is the Profile join key, so refuse to proceed without one rather than
    # creating a NULL-email row (which would 500 on the get_or_create fallback).
    if not result.user.email:
        messages.error(request, AUTH_ERROR_MESSAGES["provider_email_needs_verification"])
        return redirect("accounts:login")

    _get_or_create_profile(result.user)

    next_url = _safe_next(request.GET.get("next", ""), request) or "/"

    response = redirect(next_url)
    set_auth_cookies(response, result.session, request.is_secure())
    response.delete_cookie(PKCE_VERIFIER_COOKIE)
    return response


@require_http_methods(["POST"])
def logout_view(request):
    token = request.COOKIES.get("sb-access-token")
    if token:
        # Deny this session locally until the token would expire anyway, so
        # logout takes effect immediately despite stateless JWT verification.
        try:
            claims = verify_token(token)
            revoke_session(claims.get("session_id"), claims.get("exp", 0))
        except Exception:
            pass  # already expired/invalid — nothing to revoke

        try:
            # Call the Supabase logout endpoint directly with the user's token.
            # The shared singleton client has no session stored, so
            # supabase.auth.sign_out() would be a no-op against the server.
            httpx.post(
                f"{auth_base_url()}/logout",
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
    clear_auth_cookies(response)
    return response
