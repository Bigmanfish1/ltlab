"""Single source of truth for Supabase auth-cookie policy.

Both the OAuth views (fresh login / logout) and the middleware (silent refresh /
forced re-login) write and clear these cookies. Keeping the names, max-ages, and
security attributes in one place stops the set/clear pairs from drifting apart.
"""

from .constants import ACCESS_TOKEN_MAX_AGE, REFRESH_TOKEN_MAX_AGE

ACCESS_TOKEN_COOKIE = "sb-access-token"
REFRESH_TOKEN_COOKIE = "sb-refresh-token"
# SameSite must match between set and clear or some browsers refuse to delete
# the cookie — keep it in one place.
COOKIE_SAMESITE = "Lax"


def set_auth_cookies(response, session, is_secure: bool) -> None:
    common = {"httponly": True, "secure": is_secure, "samesite": COOKIE_SAMESITE}
    response.set_cookie(
        ACCESS_TOKEN_COOKIE, session.access_token, max_age=ACCESS_TOKEN_MAX_AGE, **common
    )
    response.set_cookie(
        REFRESH_TOKEN_COOKIE, session.refresh_token, max_age=REFRESH_TOKEN_MAX_AGE, **common
    )


def clear_auth_cookies(response) -> None:
    response.delete_cookie(ACCESS_TOKEN_COOKIE, samesite=COOKIE_SAMESITE)
    response.delete_cookie(REFRESH_TOKEN_COOKIE, samesite=COOKIE_SAMESITE)
