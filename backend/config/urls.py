from django.urls import include, path
from django.views.generic import RedirectView
urlpatterns = [path('admin/', RedirectView.as_view(pattern_name='management')), path('accounts/', include('django.contrib.auth.urls')),
               path('api/v1/', include('portal.api_urls')),
               path('', include('portal.urls'))]
