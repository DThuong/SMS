from django.http import JsonResponse
from django.urls import path

from .api import api


def index(request):
    return JsonResponse({"service": "SMS API", "docs": "/api/docs", "health": "/api/health"})


urlpatterns = [
    path("", index),
    path("api/", api.urls),
]
