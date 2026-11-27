from django.http import JsonResponse
from django.urls import path

from gpu_platform_control.api import api
from gpu_platform_control.services import pages


def health(request):
    data = pages.health(request.user).data
    return JsonResponse(data, status=200 if data["reconciler"] == "ok" else 503)


urlpatterns = [
    path("healthz/", health, name="healthz"),
    path("api/v1/", api.urls),
]
