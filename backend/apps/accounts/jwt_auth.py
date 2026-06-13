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
worker); a multi-worker / multi-host deploy would need a shared store.
"""

import logging
import time

import jwt
from django.conf import settings
from jwt import PyJWKClient

logger = logging.getLogger(__name__)

# PyJWKClient caches the JWK set on the instance (default 5 min), so it must be a
# singleton — a per-call client would refetch JWKS every request. cache_keys is
# left at its default False (True can serve a rotated-out key indefinitely).
_jwks_client: PyJWKClient | None = None

# session_id -> token exp (unix seconds). Only holds sessions revoked before
# their natural expiry; entries are pruned lazily once past exp.
_revoked_sessions: dict[str, float] = {}


def _client() -> PyJWKClient:
    global _jwks_client
    if _jwks_client is None:
        _jwks_client = PyJWKClient(f"{settings.SUPABASE_URL}/auth/v1/.well-known/jwks.json")
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


def verify_token(token: str) -> dict:
    """Return the verified claims for a Supabase access token.

    Raises jwt.ExpiredSignatureError when the token is expired (caller should try
    a refresh) and other jwt.InvalidTokenError subclasses on a bad
    signature/audience/issuer.
    """
    signing_key = _client().get_signing_key_from_jwt(token)
    return jwt.decode(
        token,
        signing_key.key,
        algorithms=["ES256"],
        audience="authenticated",
        issuer=f"{settings.SUPABASE_URL}/auth/v1",
        leeway=10,  # tolerate minor client/server clock skew
    )


def revoke_session(session_id: str | None, exp: float) -> None:
    """Deny a session (logout) until its access token would expire anyway."""
    if not session_id:
        return
    _prune()
    _revoked_sessions[session_id] = exp


def is_session_revoked(session_id: str | None) -> bool:
    if not session_id:
        return False
    exp = _revoked_sessions.get(session_id)
    if exp is None:
        return False
    if exp <= time.time():
        _revoked_sessions.pop(session_id, None)
        return False
    return True


def _prune() -> None:
    now = time.time()
    for sid in [s for s, exp in _revoked_sessions.items() if exp <= now]:
        _revoked_sessions.pop(sid, None)
