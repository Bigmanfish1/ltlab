from django.shortcuts import render


def home(request):
    return render(request, "home.html")


def sandbox(request):
    return render(request, "sandbox/sandbox.html")
