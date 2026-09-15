from django import template

from apps.accounts.participant import participant_code as derive_code

register = template.Library()


@register.filter
def participant_code(profile):
    if profile is None:
        return ""
    return derive_code(profile)
