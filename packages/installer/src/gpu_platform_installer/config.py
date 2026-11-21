"""Resolve project defaults, cluster overrides, and host overrides."""
from __future__ import annotations

from copy import deepcopy
from importlib.resources import files
from pathlib import Path
from typing import Any

import yaml

from gpu_platform_contracts.config import HostConfig, PlatformConfig


class ConfigurationLoader(yaml.SafeLoader):
    def construct_mapping(self, node, deep=False):
        keys = set()
        for key_node, _ in node.value:
            if key_node.tag == "tag:yaml.org,2002:merge":
                continue
            key = self.construct_object(key_node, deep=deep)
            if key in keys:
                raise ValueError(f"Duplicate configuration key: {key}")
            keys.add(key)
        return super().construct_mapping(node, deep=deep)


def parse_yaml(text: str) -> dict[str, Any]:
    value = yaml.load(text, Loader=ConfigurationLoader)
    if not isinstance(value, dict):
        raise ValueError("Configuration must be a mapping")
    return value


def merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def read_yaml(path: Path) -> dict[str, Any]:
    if path.stat().st_size > 8 * 1024 * 1024:
        raise ValueError("Configuration exceeds 8 MiB")
    return parse_yaml(path.read_text())


def load_config(path: Path | str, defaults_path: Path | None = None) -> PlatformConfig:
    defaults = read_yaml(defaults_path) if defaults_path else parse_yaml(
        files("gpu_platform_installer").joinpath("platform.default.yaml").read_text()
    )
    override = read_yaml(Path(path))
    # A supplied topology describes the whole cluster.
    if "hosts" in override:
        defaults.pop("hosts", None)
    return PlatformConfig.model_validate(merge(defaults, override))


def resolve_host(config: PlatformConfig, name: str) -> HostConfig:
    if name not in config.hosts:
        raise ValueError(f"Unknown host: {name}")
    host = HostConfig.model_validate(merge(
        config.defaults.model_dump(mode="python"),
        config.hosts[name].model_dump(mode="python", exclude_unset=True),
    ))
    if "node" in host.roles and "control" in host.roles:
        from gpu_platform_contracts.config import ModuleSelection

        host.slots["transport"] = ModuleSelection(module="local")
        host.slots["entry"] = ModuleSelection(module="local")
    if sum("node" in item.roles for item in config.hosts.values()) < 2:
        host.features.image_sync = False
        host.features.dataset_sync = False
    return host


def effective_config(config: PlatformConfig) -> dict[str, Any]:
    result = config.model_dump(mode="json")
    result["hosts"] = {
        name: resolve_host(config, name).model_dump(mode="json") for name in config.hosts
    }
    return result
