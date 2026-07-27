"""Signed, downgrade-only "view as student" preview cookie.

A real teacher can render the student UI as themselves without editing their own
role. The cookie only ever downgrades the rendered view: it is honoured solely
when the real role is teacher, so it can never escalate a student.
"""

from django.core import signing

from .constants import VIEW_AS_COOKIE, VIEW_AS_MAX_AGE
from .models import Profile


def set_view_as_student(response, is_secure: bool) -> None:
    response.set_signed_cookie(
        VIEW_AS_COOKIE,
        Profile.ROLE_STUDENT,
        max_age=VIEW_AS_MAX_AGE,
        httponly=True,
        secure=is_secure,
        samesite="Lax",
    )


def clear_view_as(response) -> None:
    response.delete_cookie(VIEW_AS_COOKIE, samesite="Lax")


def is_previewing(request) -> bool:
    profile = getattr(request, "profile", None)
    if profile is None or profile.role != Profile.ROLE_TEACHER:
        return False
    try:
        value = request.get_signed_cookie(
            VIEW_AS_COOKIE, default=None, max_age=VIEW_AS_MAX_AGE
        )
    except (signing.BadSignature, signing.SignatureExpired):
        return False
    return value == Profile.ROLE_STUDENT
