import hashlib
import json
import os
from pathlib import Path
import subprocess

import yaml

from gpu_platform_contracts.module import CheckResult
from gpu_platform_contracts.texts import Message

CHART_SHA256 = "57da44022c61e8e6b08650c34308997fb06f48bc66f37afacf0fdcc61ee08bcb"


def record_release_ownership(context, state):
    journal = state / "release.json"
    if journal.exists():
        release_ownership(journal)
        return
    result = context.runner(
        ["helm", "list", "--namespace", "openebs", "--all", "--filter", "^rawfile$", "--output", "json",
         "--kubeconfig", "/etc/rancher/k3s/k3s.yaml"],
        check=True, capture_output=True, text=True,
    )
    releases = json.loads(result.stdout)
    if not isinstance(releases, list) or any(not isinstance(item, dict) for item in releases):
        raise ValueError("storage.release_inspection_invalid")
    record = {"preexisting": any(item.get("name") == "rawfile" for item in releases)}
    temporary = journal.with_suffix(".tmp")
    with temporary.open("w") as stream:
        json.dump(record, stream)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.chmod(0o600)
    temporary.replace(journal)


def release_ownership(journal):
    record = json.loads(journal.read_text())
    if not isinstance(record, dict) or type(record.get("preexisting")) is not bool:
        raise ValueError("storage.release_ownership_invalid")
    return record["preexisting"]


def check_host(context):
    pools = context.options.get("pools", [])
    if not pools:
        return [CheckResult(ok=False, message=Message(code="storage.pools_required"))]
    results = []
    paths, names, classes = set(), set(), set()
    runner = getattr(context, "runner", subprocess.run)
    for pool in pools:
        path = Path(pool.get("path", ""))
        code = None
        if (not path.is_absolute() or not pool.get("name") or not pool.get("storage_class")
                or not isinstance(pool.get("buffer_percent", 5), (int, float))
                or not 0 <= pool.get("buffer_percent", 5) < 100):
            code = "storage.pool_invalid"
        elif str(path.resolve()) in paths or pool["name"] in names or pool["storage_class"] in classes:
            code = "storage.pool_duplicate"
        else:
            paths.add(str(path.resolve()))
            names.add(pool["name"])
            classes.add(pool["storage_class"])
            ancestor = path
            while not ancestor.exists() and ancestor != ancestor.parent:
                ancestor = ancestor.parent
            if not ancestor.is_dir():
                code = "storage.pool_invalid"
            else:
                try:
                    result = runner(
                        ["findmnt", "--json", "--target", str(ancestor),
                         "--output", "TARGET,FSTYPE,OPTIONS"],
                        check=True, capture_output=True, text=True, timeout=10,
                    )
                    mount = json.loads(result.stdout)["filesystems"][0]
                    if mount["target"] == "/" or mount["fstype"] in {"tmpfs", "ramfs", "overlay"}:
                        code = "storage.pool_not_mounted"
                    elif "rw" not in mount["options"].split(","):
                        code = "storage.pool_read_only"
                except (OSError, subprocess.SubprocessError, ValueError, KeyError, IndexError):
                    code = "storage.pool_inspection_failed"
        results.append(CheckResult(ok=code is None, message=Message(
            code=code or "storage.pool_ready", parameters={"path": str(path)},
        )))
    return results


def install(context):
    pools = context.options.get("pools", [])
    if not pools:
        raise ValueError("storage.pools_required")
    state = context.state_directory / "installer" / "rawfile"
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    record_release_ownership(context, state)
    for pool in pools:
        directory = Path(pool["path"])
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        directory.chmod(0o700)
    chart = state / "rawfile.tgz"
    if not chart.exists():
        context.runner(
            [
                "curl",
                "-fL",
                "--retry",
                "3",
                "-o",
                str(chart),
                "https://github.com/openebs/rawfile-localpv/releases/download/v0.15.3/rawfile-localpv-0.15.3.tgz",
            ]
        )
    if (
        hashlib.sha256(chart.read_bytes()).hexdigest()
        != CHART_SHA256
    ):
        raise ValueError("storage.chart_checksum_mismatch")
    classes = [
        {
            "name": pool["storage_class"],
            "enabled": True,
            "volumeBindingMode": "WaitForFirstConsumer",
            "isDefault": False,
            "reclaimPolicy": "Delete",
            "allowVolumeExpansion": True,
            "fsType": "ext4",
            "storagePool": pool["name"],
            "thinProvision": "false",
            "formatOptions": ["-E", "nodiscard,assume_storage_prezeroed=1"],
            "mountOptions": ["nodiscard"],
            "copyOnWrite": "false",
        }
        for pool in pools
    ]
    values = state / "values.yaml"
    values.write_text(
        yaml.safe_dump(
            {
                "node": {
                    "defaultPool": pools[0]["name"],
                    "storagePools": {
                        pool["name"]: {
                            "path": pool["path"],
                            "reservedCapacity": f"{float(pool.get('buffer_percent', 5)):g}%",
                            "reservedCapacityMode": "plain",
                        }
                        for pool in pools
                    },
                },
                "storageClasses": classes,
                "analytics": {"enabled": False},
                "global": {"analytics": {"enabled": False}},
                "metrics": {"enabled": True},
            }
        )
    )
    renderer = Path(__file__).parent / "host" / "rawfile_permissions.py"
    renderer.chmod(0o755)
    context.runner(
        [
            "helm",
            "upgrade",
            "--install",
            "rawfile",
            str(chart),
            "--namespace",
            "openebs",
            "--create-namespace",
            "--values",
            str(values),
            "--post-renderer",
            str(renderer),
            "--wait",
            "--timeout",
            "10m",
            "--kubeconfig",
            "/etc/rancher/k3s/k3s.yaml",
        ]
    )


def uninstall(context):
    journal = context.state_directory / "installer" / "rawfile" / "release.json"
    if not journal.exists():
        return
    if not release_ownership(journal):
        context.runner(["helm", "uninstall", "rawfile", "--namespace", "openebs", "--ignore-not-found",
                        "--kubeconfig", "/etc/rancher/k3s/k3s.yaml"], check=True)
    journal.unlink()


def compose(context):
    return {
        "services": {
            "node": {
                "volumes": [f"{pool['path']}:{pool['path']}" for pool in context.options.get("pools", [])]
            }
        }
    }
