import hashlib
import hmac

from django.conf import settings

CODE_PREFIX = "P-"
CODE_HEX_CHARS = 6


def participant_code(profile) -> str:
    # Derived rather than stored so research data can be keyed without a schema change on the
    # shared Users table. Keyed with SECRET_KEY so the code cannot be reversed to an account
    # by anyone who only sees the exported data.
    digest = hmac.new(
        settings.SECRET_KEY.encode(), str(profile.id).encode(), hashlib.sha256
    ).hexdigest()
    return CODE_PREFIX + digest[:CODE_HEX_CHARS].upper()
