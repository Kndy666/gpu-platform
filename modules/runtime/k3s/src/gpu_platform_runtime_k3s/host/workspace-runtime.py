#!/usr/bin/env python3
"""Install OCI settings for standard development environments."""

import argparse
from copy import deepcopy
import json
from pathlib import Path
import subprocess

import yaml

SPEC_PATH = Path("/etc/rancher/k3s/platform-workspace-oci.json")
TEMPLATE_PATH = Path("/var/lib/rancher/k3s/agent/etc/containerd/config-v3.toml.tmpl")
HEADER = "# Managed by gpu-platform runtime module\n"


def workspace_spec(default, gpu_hook=None):
    spec = deepcopy(default)
    spec["mounts"] += [
        {
            "destination": "/run/platform-proc",
            "type": "proc",
            "source": "proc",
            "options": ["nosuid", "noexec", "nodev"],
        },
        {
            "destination": "/run/platform-sys",
            "type": "sysfs",
            "source": "sysfs",
            "options": ["nosuid", "noexec", "nodev", "ro"],
        },
    ]
    spec["linux"].setdefault("devices", []).append(
        {
            "path": "/dev/net/tun",
            "type": "c",
            "major": 10,
            "minor": 200,
            "fileMode": 0o666,
            "uid": 0,
            "gid": 0,
        }
    )
    spec["linux"]["resources"].setdefault("devices", []).append(
        {"allow": True, "type": "c", "major": 10, "minor": 200, "access": "rwm"}
    )
    if gpu_hook:
        spec.setdefault("hooks", {}).setdefault("prestart", []).append(
            {"path": gpu_hook, "args": [gpu_hook], "timeout": 30}
        )
    return spec


def runtime_template(classes, gpu_binary=None):
    text = HEADER + '{{ template "base" . }}\n'
    for kind, name in classes.items():
        binary = gpu_binary if kind == "gpu" else "/usr/bin/runc"
        if not binary:
            raise ValueError("GPU runtime provider did not supply an OCI executable")
        table = "plugins.'io.containerd.cri.v1.runtime'.containerd.runtimes.'" + name + "'"
        text += (
            f'\n[{table}]\n  runtime_type = "io.containerd.runc.v2"\n'
            f'  base_runtime_spec = "{SPEC_PATH}"\n[{table}.options]\n'
            f'  BinaryName = "{binary}"\n  SystemdCgroup = true\n'
        )
    return text


def write_changed(path, content):
    if path.exists() and path.read_text() == content:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return True


def install(cfg, run=subprocess.run):
    def command(args, **kwargs):
        return run(args, check=True, text=True, **kwargs)

    if TEMPLATE_PATH.exists() and not TEMPLATE_PATH.read_text().startswith(
        HEADER
    ):
        raise RuntimeError("Merge the existing containerd template before installing workspace runtimes")
    default = json.loads(command(["k3s", "ctr", "oci", "spec"], capture_output=True).stdout)
    changed = write_changed(
        SPEC_PATH, json.dumps(workspace_spec(default, cfg.get("gpu_hook")), indent=2) + "\n"
    )
    changed = write_changed(TEMPLATE_PATH, runtime_template(cfg["platform"]["runtime_classes"], cfg.get("gpu_binary"))) or changed
    from workspace_security import install_security

    install_security(cfg["platform"]["architecture"])
    if changed:
        command(["systemctl", "restart", "k3s"])
    command(["k3s", "kubectl", "wait", "--for=condition=Ready", "nodes", "--all", "--timeout=180s"])
    for name in cfg["platform"]["runtime_classes"].values():
        command(
            ["k3s", "kubectl", "apply", "-f", "-"],
            input=json.dumps(
                {
                    "apiVersion": "node.k8s.io/v1",
                    "kind": "RuntimeClass",
                    "metadata": {"name": name, "labels": {"app.kubernetes.io/managed-by": "gpu-platform"}},
                    "handler": name,
                }
            ),
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    install(yaml.safe_load(parser.parse_args().config.read_text()))
