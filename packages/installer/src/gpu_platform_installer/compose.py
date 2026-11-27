"""Compose documents assembled from host roles and module fragments."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from gpu_platform_contracts.config import PlatformConfig

from .checks import module_context
from .config import resolve_host
from .modules import SelectedModule


def merge_fragment(base: dict[str, Any], fragment: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(base)
    for key, value in fragment.items():
        existing = result.get(key)
        if isinstance(value, dict) and isinstance(existing, dict):
            result[key] = merge_fragment(existing, value)
        elif isinstance(value, list) and isinstance(existing, list) and key not in {"command", "entrypoint", "test"}:
            result[key] = existing + [item for item in value if item not in existing]
        else:
            result[key] = deepcopy(value)
    return result


def render_compose(config: PlatformConfig, name: str, modules: dict[str, SelectedModule],
                   config_path: Path) -> dict[str, Any]:
    host = resolve_host(config, name)
    state = config.cluster.state_directory
    https = bool(host.parameters.get("https", False))
    scheme = "https" if https else "http"
    port = int(host.parameters.get("web_port", 8000))
    public_origin = host.parameters.get("public_origin") or host.parameters.get("https_origin") or (
        f"{scheme}://{config.cluster.public_host}" + (f":{port}" if port not in {80, 443} else "")
    )
    common: dict[str, Any] = {
        "restart": "unless-stopped",
        "init": True,
        "environment": {
            "PLATFORM_CONFIG": "/etc/gpu-platform/platform.yaml",
            "PLATFORM_HOST": name,
            "PLATFORM_DATA": state,
            "DJANGO_SETTINGS_MODULE": "gpu_platform_control.settings.base",
            "DJANGO_SECRET_KEY": "${DJANGO_SECRET_KEY:?Set DJANGO_SECRET_KEY}",
            "DJANGO_ALLOWED_HOSTS": f"{config.cluster.public_host},localhost,127.0.0.1,web",
            "PLATFORM_HTTPS": "1" if https else "0",
            "CSRF_TRUSTED_ORIGINS": public_origin,
        },
        "volumes": [f"{state}:{state}", f"{config_path.resolve()}:/etc/gpu-platform/platform.yaml:ro"],
    }
    common["environment"].update({key: str(value) for key, value in host.parameters.get("environment", {}).items()})
    services: dict[str, Any] = {}
    if "control" in host.roles:
        control = {**deepcopy(common), "image": config.cluster.control_image}
        control["environment"]["PLATFORM_CONFIG"] = f"{state}/cluster.yaml"
        control["environment"]["PLATFORM_INITIAL_ADMIN_PASSWORD"] = "${PLATFORM_INITIAL_ADMIN_PASSWORD:-}"
        services["web"] = {
            **deepcopy(control),
            "command": ["gunicorn", "gpu_platform_control.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "2"],
            "healthcheck": {"test": ["CMD", "python", "-c",
                "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz/', timeout=3)"],
                "interval": "30s", "timeout": "5s", "retries": 3},
        }
        services["panel"] = {
            "image": config.cluster.panel_image,
            "restart": "unless-stopped",
            "init": True,
            "depends_on": ["web"],
            "ports": [f"{port}:80"],
            "healthcheck": {
                "test": ["CMD", "wget", "-q", "-O", "/dev/null", "http://127.0.0.1/"],
                "interval": "30s", "timeout": "5s", "retries": 3,
            },
        }
        services["loop"] = {**deepcopy(control), "command": ["python", "-m", "gpu_platform_control.manage", "reconcile"]}
        services["links"] = {
            **deepcopy(control),
            "command": ["python", "-m", "gpu_platform_control.manage", "links"],
            "network_mode": "host",
        }
        if config.control.notify:
            services["notifier"] = {**deepcopy(control), "command": ["python", "-m", "gpu_platform_control.manage", "notify"]}
    if "node" in host.roles:
        node = deepcopy(common)
        # The node daemon receives machine configuration and never needs account secrets.
        node["environment"].pop("DJANGO_SECRET_KEY")
        node["environment"].pop("DJANGO_SETTINGS_MODULE")
        node["environment"].pop("DJANGO_ALLOWED_HOSTS")
        if host.features.public_datasets:
            datasets = host.parameters.get("datasets", "/data/datasets")
            node["volumes"].append(f"{datasets}:{datasets}")
        services["node"] = {
            **node, "image": config.cluster.node_image,
            "privileged": True, "network_mode": "host", "pid": "host",
            "command": ["python", "-m", "gpu_platform_node.daemon", "serve", "--host", name,
                        "--config", "/etc/gpu-platform/platform.yaml"],
            "healthcheck": {"test": ["CMD", "python", "-m", "gpu_platform_node.daemon", "status", "--json",
                                     "--socket", f"{state}/node.sock"],
                            "interval": "30s", "timeout": "5s", "retries": 3},
        }
    document: dict[str, Any] = {"name": f"gpu-platform-{name}", "services": services}
    for module in modules.values():
        document = merge_fragment(document, module.implementation.compose(module_context(config, name, module)))
    for service_name, service in document["services"].items():
        if not service.get("image") and not service.get("build"):
            raise ValueError(f"Compose service has no image: {service_name}")
        targets: set[str] = set()
        for volume in service.get("volumes", []):
            target = volume.get("target") if isinstance(volume, dict) else volume.split(":")[1] if ":" in volume else volume
            if not isinstance(target, str) or not target.startswith("/"):
                raise ValueError(f"Invalid volume target in {service_name}")
            if target in targets:
                raise ValueError(f"Conflicting volume target in {service_name}: {target}")
            targets.add(target)
    return document
