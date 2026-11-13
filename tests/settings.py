"""Isolated settings for the ordinary test suite, independent of deployment env."""
import atexit
import os
from pathlib import Path
import tempfile

_directory = tempfile.TemporaryDirectory(prefix='gpu-platform-tests-')
atexit.register(_directory.cleanup)
os.environ.update(
    PLATFORM_DATA=_directory.name,
    PLATFORM_DATASET_ROOT=str(Path(_directory.name) / 'datasets'),
    PLATFORM_CONFIG=str(Path(__file__).with_name('platform.yaml')),
    DJANGO_SECRET_KEY='isolated-test-secret',
    DJANGO_DEBUG='1',
    DJANGO_ALLOWED_HOSTS='localhost,127.0.0.1,testserver',
    PLATFORM_HTTPS='0',
    CSRF_TRUSTED_ORIGINS='',
)

from config.settings import *  # noqa: E402,F403
