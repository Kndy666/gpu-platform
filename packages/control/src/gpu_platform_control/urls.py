from django.urls import include, path
from django.views.generic import RedirectView
from django.views.i18n import JavaScriptCatalog
from gpu_platform_control.api import api

urlpatterns = [
    path("admin/", RedirectView.as_view(pattern_name="management")),
    path("accounts/", include("django.contrib.auth.urls")),
    path("api/v1/", api.urls),
    path("jsi18n/", JavaScriptCatalog.as_view(), name="javascript-catalog"),
    path("", include("gpu_platform_panel.urls")),
]
