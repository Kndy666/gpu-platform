"""Portable settings with a fresh database and no deployment configuration."""

import os
import tempfile
from pathlib import Path

_test_directory = tempfile.TemporaryDirectory(prefix="control-tests-")
os.environ["PLATFORM_DATA"] = _test_directory.name
os.environ["DJANGO_SECRET_KEY"] = "test-key"
os.environ["DJANGO_DEBUG"] = "1"
os.environ["PLATFORM_HTTPS"] = "0"
from .base import *  # noqa: E402,F403

DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
PLATFORM_DATASET_ROOT = Path(_test_directory.name) / "datasets"
PLATFORM_DATASET_ROOT.mkdir()

PLATFORM_CLUSTER_CONFIG = {"hosts": {"main": {"roles": ["control", "node"]}}}

MIDDLEWARE = [item for item in MIDDLEWARE if "whitenoise" not in item]  # noqa: F405
