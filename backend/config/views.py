from django.shortcuts import render

from apps.accounts.middleware import supabase_login_required


@supabase_login_required
def sandbox(request):
    return render(request, "sandbox/sandbox.html")
