"""Local verification of Supabase access tokens (ES256 JWTs).

Supabase signs access tokens with an asymmetric key (ES256) and publishes the
public key at the project's JWKS endpoint. Verifying the signature + claims
locally lets the middleware authenticate every request with zero network calls
to the Auth server — the recommended pattern once a project is on asymmetric
signing keys (calling auth.get_user() per request is the anti-pattern it
replaces).

Trade-off: a JWT stays valid until its `exp`, so a server-side logout is not
visible to local verification on its own. We close that gap with an in-process
session denylist: logout adds the token's `session_id` until it would have
expired anyway. Correct for a single-process deployment (our gunicorn runs one
worker); the denylist is also lost on restart/redeploy. Swap SessionDenylist for
a shared/persistent implementation (same interface) to survive that or scale to
multiple workers.
"""

import logging
import time

import jwt
from django.conf import settings
from jwt import PyJWKClient

logger = logging.getLogger(__name__)

# Clock-skew tolerance for exp/nbf, shared between verification and the denylist
# so a revoked-but-within-leeway token can't slip through after logout.
LEEWAY = 10  # seconds

_jwks_client: PyJWKClient | None = None


def auth_base_url() -> str:
    """The Supabase Auth base URL, e.g. https://<proj>.supabase.co/auth/v1.

    Single source for the JWKS URL, the JWT `iss` claim, and the logout endpoint.
    `rstrip` guards against a trailing slash on SUPABASE_URL producing a
    double-slash issuer that would mismatch every token's `iss`.
    """
    return f"{settings.SUPABASE_URL.rstrip('/')}/auth/v1"


def _client() -> PyJWKClient:
    # PyJWKClient caches the JWK set on the instance (default 5 min), so it must
    # be a singleton — a per-call client would refetch JWKS every request.
    # cache_keys is left at its default False (True can serve a rotated-out key).
    global _jwks_client
    if _jwks_client is None:
        _jwks_client = PyJWKClient(f"{auth_base_url()}/.well-known/jwks.json")
    return _jwks_client


class SupabaseUser:
    """Minimal view over verified JWT claims, matching the attributes the rest
    of the code reads off the gotrue User object (id / email / user_metadata)."""

    __slots__ = ("id", "email", "user_metadata", "session_id")

    def __init__(self, claims: dict):
        self.id = claims.get("sub")
        self.email = claims.get("email")
        self.user_metadata = claims.get("user_metadata") or {}
        self.session_id = claims.get("session_id")


def verify_token(token: str, verify_exp: bool = True) -> dict:
    """Return the verified claims for a Supabase access token.

    Raises jwt.ExpiredSignatureError when the token is expired (caller should try
    a refresh) and other jwt.InvalidTokenError subclasses on a bad
    signature/audience/issuer. Pass verify_exp=False to read claims off an
    already-expired token while still checking the signature/aud/iss.
    """
    signing_key = _client().get_signing_key_from_jwt(token)
    return jwt.decode(
        token,
        signing_key.key,
        algorithms=["ES256"],
        audience="authenticated",
        issuer=auth_base_url(),
        leeway=LEEWAY,
        options={"verify_exp": verify_exp},
    )


def _is_live(exp: float) -> bool:
    """True while a stored expiry is still in the future."""
    return exp > time.time()


class SessionDenylist:
    """Sessions revoked (via logout) before their token's natural expiry.

    In-memory and per-process — correct for the single gunicorn worker, lost on
    restart. Replace with a shared/persistent backend exposing the same methods
    to survive restarts or scale to multiple workers.
    """

    def __init__(self):
        self._revoked: dict[str, float] = {}

    def revoke(self, session_id: str | None, exp: float) -> None:
        if not session_id:
            return
        self.prune()
        # Hold past the token's exp + leeway, so verify_token's leeway window
        # cannot re-admit a just-logged-out token.
        self._revoked[session_id] = exp + LEEWAY

    def is_revoked(self, session_id: str | None) -> bool:
        if not session_id:
            return False
        exp = self._revoked.get(session_id)
        if exp is None:
            return False
        if not _is_live(exp):
            self._revoked.pop(session_id, None)
            return False
        return True

    def prune(self) -> None:
        for sid in [s for s, exp in self._revoked.items() if not _is_live(exp)]:
            self._revoked.pop(sid, None)

    def clear(self) -> None:
        self._revoked.clear()


_denylist = SessionDenylist()


def revoke_session(session_id: str | None, exp: float) -> None:
    _denylist.revoke(session_id, exp)


def is_session_revoked(session_id: str | None) -> bool:
    return _denylist.is_revoked(session_id)
