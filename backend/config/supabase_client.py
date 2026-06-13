import time

from django.conf import settings
from supabase import Client, create_client

_client: Client | None = None

# Short-lived cache of validated access tokens. SupabaseAuthMiddleware runs on
# every request; without this each page load (and every HTMX partial / poll)
# blocks on a network round-trip to supabase.auth.get_user(). Caching the
# validated user per token for a few seconds collapses bursts (multi-asset page
# loads, hint polling) down to a single upstream call. Only successful
# validations are cached — AuthApiError (expired/invalid token) propagates so the
# middleware's refresh path still fires.
_USER_CACHE_TTL = 30  # seconds
_USER_CACHE_MAX = 1000  # crude bound; cleared wholesale when exceeded
_user_cache: dict[str, tuple[float, object]] = {}


def get_supabase_client() -> Client:
    global _client
    if _client is None:
        _client = create_client(settings.SUPABASE_URL, settings.SUPABASE_ANON_KEY)
    return _client


def get_cached_user(token: str):
    """Return the Supabase user for ``token``, validating at most once per TTL.

    Raises whatever ``auth.get_user`` raises (e.g. AuthApiError) on a miss so the
    caller can handle expiry/refresh; errors are never cached.
    """
    now = time.monotonic()
    cached = _user_cache.get(token)
    if cached is not None and cached[0] > now:
        return cached[1]

    user = get_supabase_client().auth.get_user(token).user

    if len(_user_cache) >= _USER_CACHE_MAX:
        _user_cache.clear()
    _user_cache[token] = (now + _USER_CACHE_TTL, user)
    return user
