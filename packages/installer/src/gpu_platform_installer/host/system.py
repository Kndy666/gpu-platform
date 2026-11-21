from __future__ import annotations

import os
import platform
import shutil
import subprocess
from dataclasses import dataclass, field

from gpu_platform_contracts.module import HostContext as HostContext
from pathlib import Path
from typing import Any
from gpu_platform_contracts.host import HostOwnership


def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
    kwargs.setdefault("check", True)
    kwargs.setdefault("text", True)
    return subprocess.run(argv, **kwargs)



@dataclass(frozen=True)
class CheckIssue:
    code: str
    parameters: dict[str, Any] = field(default_factory=dict)
    severity: str = "error"


def inspect_host() -> dict[str, Any]:
    release: dict[str, str] = {}
    release_file = Path("/etc/os-release")
    if release_file.exists():
        for line in release_file.read_text().splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                release[key] = value.strip('"')
    return {
        "os": release.get("ID", "unknown"),
        "version": release.get("VERSION_ID", "unknown"),
        "architecture": {"x86_64": "amd64", "aarch64": "arm64"}.get(
            platform.machine(), platform.machine()
        ),
        "docker": shutil.which("docker"),
        "uid": os.geteuid(),
    }


def check_system(facts: dict[str, Any]) -> list[CheckIssue]:
    issues = []
    if facts.get("os") != "ubuntu" or facts.get("version") not in {"22.04", "24.04"}:
        issues.append(CheckIssue("host.unsupported_os", {"os": facts.get("os"), "version": facts.get("version")}))
    if facts.get("architecture") not in {"amd64", "arm64"}:
        issues.append(CheckIssue("host.unsupported_architecture", {"architecture": facts.get("architecture")}))
    return issues


def prepare_docker(context: HostContext) -> None:
    """Install pinned Docker Engine and its Compose plugin on a supported host."""
    if context.facts.get("uid", os.geteuid()) != 0:
        raise PermissionError("Host installation requires root")
    if check_system(context.facts):
        raise RuntimeError("Host requirements are not satisfied")
    if not context.facts.get("docker"):
        context.runner(["apt-get", "update"])
        context.runner(["apt-get", "install", "-y", "ca-certificates", "curl"])
        context.runner(["install", "-m", "0755", "-d", "/etc/apt/keyrings"])
        context.runner(["curl", "-fsSL", "https://download.docker.com/linux/ubuntu/gpg", "-o", "/etc/apt/keyrings/docker.asc"])
        context.runner(["chmod", "a+r", "/etc/apt/keyrings/docker.asc"])
        codename = {"22.04": "jammy", "24.04": "noble"}[context.facts["version"]]
        architecture = context.facts["architecture"]
        repository = f"deb [arch={architecture} signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu {codename} stable\n"
        context.runner(["tee", "/etc/apt/sources.list.d/docker.list"], input=repository, capture_output=True)
        context.runner(["apt-get", "update"])
        version = f"5:29.1.3-1~ubuntu.{context.facts['version']}~{codename}"
        context.runner(["apt-get", "install", "-y", "docker-ce=" + version, "docker-ce-cli=" + version,
                        "containerd.io", "docker-buildx-plugin", "docker-compose-plugin"])
    else:
        result = context.runner(["docker", "version", "--format", "{{.Server.APIVersion}}"], capture_output=True)
        api = tuple(int(part) for part in result.stdout.strip().split("."))
        if api < (1, 44):
            raise RuntimeError("The existing Docker Engine must support API 1.44 or later")
        result = context.runner(["docker", "compose", "version"], check=False, capture_output=True)
        if result.returncode:
            raise RuntimeError("Install the Docker Compose plugin for the existing Docker Engine")
    context.runner(["systemctl", "enable", "--now", "docker"])
    configure_builder_cache(context)


def configure_builder_cache(context: HostContext, systemd: Path = Path("/etc/systemd/system")) -> None:
    service = systemd / "gpu-platform-docker-cache.service"
    timer = systemd / "gpu-platform-docker-cache.timer"
    ownership = HostOwnership(context, "docker-builder-cache")
    with ownership.files([service, timer], before_remove={
        str(timer): [["systemctl", "disable", "--now", timer.name]],
    }):
        service.write_text(
            "[Unit]\nDescription=Reclaim unused Docker build cache\nAfter=docker.service\n"
            "[Service]\nType=oneshot\n"
            "ExecStart=/usr/bin/docker builder prune --force --all --filter until=4h\n"
            "ExecStart=/usr/bin/docker builder prune --force --all --keep-storage 4294967296\n"
        )
        timer.write_text(
            "[Unit]\nDescription=Reclaim Docker build cache periodically\n"
            "[Timer]\nOnBootSec=5min\nOnUnitActiveSec=15min\n"
            "[Install]\nWantedBy=timers.target\n"
        )
        context.runner(["systemctl", "daemon-reload"])
        context.runner(["systemctl", "enable", "--now", timer.name])


def configure_firewall(context: HostContext, directory: Path = Path("/etc/gpu-platform")) -> None:
    """Persist only installer-owned infrastructure rules through a systemd unit."""
    import json
    import re
    import shlex

    network = context.host.get("parameters", {}).get("network", {})
    interface = network.get("public_interface")
    ports = [(protocol, port) for protocol, key in (("tcp", "private_ports"), ("udp", "private_udp_ports"))
             for port in network.get(key, [])]
    record = directory / "firewall-rules.json"
    if not ports and not record.exists():
        return
    if any(isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535 for _, port in ports):
        raise ValueError("Invalid private port")
    if ports and (not interface or not re.fullmatch(r"[a-zA-Z0-9_.:-]+", interface)):
        raise ValueError("A public interface is required for private port rules")
    context.runner(["apt-get", "install", "-y", "iptables", "iproute2"])
    if ports and interface == "auto":
        result = context.runner(["ip", "-j", "route", "show", "default"], capture_output=True)
        routes = json.loads(result.stdout)
        if not routes:
            result = context.runner(["ip", "-6", "-j", "route", "show", "default"], capture_output=True)
            routes = json.loads(result.stdout)
        routes = [route for route in routes if route.get("dev") and route["dev"] != "lo"]
        if not routes:
            raise ValueError("No default-route interface is available for private port rules")
        interface = min(routes, key=lambda route: int(route.get("metric", 0)))["dev"]
    if ports:
        if not re.fullmatch(r"[a-zA-Z0-9_.:-]+", interface):
            raise ValueError("Invalid public interface")
        context.runner(["ip", "link", "show", "dev", interface], capture_output=True)
    desired = [
        [command, "INPUT", "-i", interface, "-p", protocol, "--dport", str(port),
         "-m", "comment", "--comment", "gpu-platform-private", "-j", "DROP"]
        for protocol, port in sorted(set(ports)) for command in ("iptables", "ip6tables")
    ]
    previous = json.loads(record.read_text()) if record.exists() else []
    removals = previous + [rule for rule in desired if rule not in previous]
    for rule in removals:
        if not isinstance(rule, list) or len(rule) != 14 or rule[0] not in {"iptables", "ip6tables"} or rule[1] != "INPUT" or rule[-4:] != ["--comment", "gpu-platform-private", "-j", "DROP"]:
            raise ValueError("Invalid persisted firewall rule")
    lines = ["#!/bin/sh", "set -eu"]
    for command, *rule in removals:
        check = shlex.join([command, "-C", *rule])
        remove = shlex.join([command, "-D", *rule])
        lines.append(f"while {check} 2>/dev/null; do {remove}; done")
    lines.append('if [ "${1:-apply}" = stop ]; then exit 0; fi')
    lines.extend(shlex.join([command, "-I", *rule]) for command, *rule in desired)
    directory.mkdir(mode=0o755, parents=True, exist_ok=True)
    script = directory / "firewall.sh"
    script.write_text("\n".join(lines) + "\n")
    script.chmod(0o700)
    unit = directory / "gpu-platform-firewall.service"
    unit.write_text(
        "[Unit]\nDescription=Platform infrastructure firewall\nAfter=network-online.target\n"
        "Wants=network-online.target\nBefore=docker.service k3s.service\n"
        "[Service]\nType=oneshot\nRemainAfterExit=yes\n"
        f"ExecStart={script} apply\nExecStop={script} stop\nExecReload={script} apply\n"
        "[Install]\nWantedBy=multi-user.target\n"
    )
    unit.chmod(0o644)
    context.runner(["install", "-m", "0644", str(unit), "/etc/systemd/system/gpu-platform-firewall.service"])
    context.runner(["systemctl", "daemon-reload"])
    context.runner(["systemctl", "enable", "gpu-platform-firewall.service"])
    context.runner(["systemctl", "restart", "gpu-platform-firewall.service"])
    record.write_text(json.dumps(desired))
    record.chmod(0o600)


def remove_firewall(context: HostContext, directory: Path = Path("/etc/gpu-platform")) -> None:
    """Remove the installer-owned firewall unit and rules, retaining other host state."""
    record = directory / "firewall-rules.json"
    if not record.exists():
        return
    script = directory / "firewall.sh"
    if not script.is_file():
        raise RuntimeError("The installed firewall cleanup script is missing")
    context.runner(["systemctl", "disable", "--now", "gpu-platform-firewall.service"])
    context.runner([str(script), "stop"])
    context.runner(["rm", "-f", "/etc/systemd/system/gpu-platform-firewall.service"])
    context.runner(["systemctl", "daemon-reload"])
    for path in (record, script, directory / "gpu-platform-firewall.service"):
        path.unlink(missing_ok=True)
    if not any(directory.iterdir()):
        directory.rmdir()
