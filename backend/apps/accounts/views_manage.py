import logging

from django.contrib import messages
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .middleware import supabase_login_required, teacher_page, teacher_required
from .models import Profile
from .view_as import clear_view_as, set_view_as_student

logger = logging.getLogger("ltlab.roles")


@teacher_page()
def manage_users(request):
    users = Profile.objects.all().order_by("role", "name", "email")
    page = Paginator(users, 50).get_page(request.GET.get("page"))
    return render(request, "manage/teacher_users.html", {"page": page, "users": page})


@teacher_required
@require_POST
def set_user_role(request, profile_id):
    role = request.POST.get("role")
    if role not in dict(Profile.ROLE_CHOICES):
        messages.error(request, "Unknown role.")
        return redirect("manage_users")

    target = get_object_or_404(Profile, pk=profile_id)

    if target.pk == request.profile.pk:
        messages.error(
            request,
            'You can’t change your own role — use "View as student" to preview.',
        )
        return redirect("manage_users")

    if target.role == role:
        messages.info(request, f"{target.email} is already {role}.")
        return redirect("manage_users")

    target.role = role
    target.save(update_fields=["role"])
    logger.info("role change: %s -> %s by %s", target.email, role, request.profile.email)
    messages.success(request, f"{target.email} is now {role}.")
    return redirect("manage_users")


@teacher_required
@require_POST
def enter_view_as_student(request):
    response = redirect("home")
    set_view_as_student(response, request.is_secure())
    return response


@supabase_login_required
@require_POST
def exit_view_as(request):
    response = redirect("home")
    clear_view_as(response)
    return response
