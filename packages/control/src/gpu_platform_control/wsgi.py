import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "gpu_platform_control.settings.base")
from django.core.wsgi import get_wsgi_application

application = get_wsgi_application()
