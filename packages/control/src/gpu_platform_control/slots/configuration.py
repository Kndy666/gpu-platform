from copy import deepcopy
from functools import lru_cache
import os
from pathlib import Path
import yaml
from django.conf import settings
from django.utils.translation import gettext_lazy as tr
from gpu_platform_contracts.config import PlatformConfig


def merge(base, override):
    result = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


@lru_cache(maxsize=4)
def _configuration(text):
    supplied = yaml.safe_load(text)
    raw = deepcopy(supplied)
    for name, host in raw.get("hosts", {}).items():
        raw["hosts"][name] = merge(raw.get("defaults", {}), host)
    return PlatformConfig.model_validate(raw)


def cluster_config():
    import json
    supplied = getattr(settings, "PLATFORM_CLUSTER_CONFIG", None)
    if supplied is not None:
        return _configuration(json.dumps(supplied))
    path = Path(os.environ.get("PLATFORM_CONFIG", "/etc/gpu-platform/platform.yaml"))
    return _configuration(path.read_text())


cluster_config.cache_clear = _configuration.cache_clear  # type: ignore[attr-defined]


def save_host(name, host):
    import fcntl
    import tempfile
    from django.core.exceptions import ValidationError
    path = Path(os.environ.get("PLATFORM_CONFIG", "/etc/gpu-platform/platform.yaml"))
    with path.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        data = yaml.safe_load(path.read_text())
        data["hosts"][name] = host
        resolved = deepcopy(data)
        resolved["hosts"] = {key: merge(data.get("defaults", {}), value)
                             for key, value in data["hosts"].items()}
        try:
            PlatformConfig.model_validate(resolved)
        except ValueError as error:
            raise ValidationError(tr("The server configuration is invalid. Check its address, architecture, and connection settings.")) from error
        descriptor, temporary = tempfile.mkstemp(prefix=".platform-", suffix=".yaml", dir=path.parent)
        try:
            with os.fdopen(descriptor, "w") as stream:
                os.fchmod(stream.fileno(), path.stat().st_mode & 0o777)
                stream.write(yaml.safe_dump(data, sort_keys=False))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)
    _configuration.cache_clear()
