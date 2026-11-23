"""Generate execution objects from node-local, versioned handoffs."""

import hashlib
import base64
import json
import re
import shlex
from pathlib import Path
from typing import Any

from gpu_platform_contracts.handoffs import TaskInputs
from gpu_platform_contracts.messages import TaskSpec
from gpu_platform_contracts.peer_ssh import FEATURE, PeerIdentity


def name_for(task_id):
    return "task-" + hashlib.sha256(task_id.encode()).hexdigest()[:32]


def task_objects(task: TaskSpec, inputs: TaskInputs, options: dict, *, state_directory: Path | None = None):
    name = name_for(task.id)
    labels = {"platform.task-key": name, "app.kubernetes.io/managed-by": "gpu-platform"}
    annotations = {"platform.task": task.id, "platform.owner": task.owner}
    metadata = {"name": name, "labels": labels, "annotations": annotations}
    if any(volume.node_id != task.node_id or volume.owner != task.owner for volume in inputs.volumes):
        raise PermissionError("runtime.invalid_volume_owner")
    if inputs.image and inputs.image.node_id != task.node_id:
        raise ValueError("runtime.image_node_mismatch")
    if inputs.gpu and (inputs.gpu.node_id != task.node_id or inputs.gpu.task_id != task.id):
        raise PermissionError("runtime.invalid_gpu_owner")
    cpu = task.resources.get("cpu", 0)
    memory = task.resources.get("memory", 0)
    system_storage = task.resources.get("system_storage", 5)
    if any(value < 1 or value != int(value) for value in (cpu, memory, system_storage)):
        raise ValueError("runtime.invalid_resource_request")
    home = next(
        (volume.mount_path for volume in inputs.volumes if volume.mount_path.startswith("/home/")), None
    )
    if home is None or not any(volume.mount_path == "/workspace" for volume in inputs.volumes):
        raise ValueError("runtime.environment_directories_required")
    env = {
        "TASK_MODE": task.kind,
        "TASK_DIRECTORY": task.extensions.get("working_directory", "/workspace"),
        "TASK_USER": task.identity.username,
        "TASK_HOME": home,
        "TASK_CPU": str(cpu),
        "TASK_ENV_NAMES": " ".join(task.environment),
    }
    for key in task.environment:
        if not key.isascii() or not key.replace("_", "a").isalnum() or key[0].isdigit():
            raise ValueError("runtime.invalid_environment_key")
    secret: dict[str, Any] = {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": metadata,
        "type": "Opaque",
        "stringData": {
            "authorized_keys": "\n".join(task.identity.ssh_keys) + "\n",
            "task.env": "\n".join(f"{key}={shlex.quote(value)}" for key, value in env.items())
            + "\n"
            + "\n".join(f"export {key}={shlex.quote(value)}" for key, value in task.environment.items()),
            "command.sh": "#!/bin/bash\n"
            + ("exec " + shlex.join(task.command) if task.command else "exit 0")
            + "\n",
            "runner.sh": (Path(__file__).parent / "images" / "runner.sh").read_text(),
        },
    }
    limits = {"cpu": str(cpu), "memory": f"{memory}Gi", "ephemeral-storage": f"{int(system_storage)}Gi"}
    mounts, volumes = [], []
    for index, volume in enumerate(inputs.volumes):
        if volume.form != "kubernetes-pvc":
            raise ValueError("runtime.unsupported_volume_form")
        key = f"volume-{index}"
        volumes.append(
            {
                "name": key,
                "persistentVolumeClaim": {"claimName": volume.location, "readOnly": volume.read_only},
            }
        )
        mounts.append({"name": key, "mountPath": volume.mount_path, "readOnly": volume.read_only})
    for index, directory in enumerate(inputs.datasets):
        if directory.node_id != task.node_id:
            raise ValueError("runtime.dataset_node_mismatch")
        key = f"dataset-{index}"
        volumes.append({"name": key, "hostPath": {"path": directory.path, "type": "Directory"}})
        mounts.append({"name": key, "mountPath": directory.mount_path, "readOnly": True})
    volumes.extend(
        [
            {"name": "config", "secret": {"secretName": name, "defaultMode": 0o440}},
            {"name": "run", "emptyDir": {"medium": "Memory", "sizeLimit": "64Mi"}},
            {"name": "shm", "emptyDir": {"medium": "Memory", "sizeLimit": f"{max(1, int(memory) // 4)}Gi"}},
            {
                "name": "resource-view",
                "hostPath": {
                    "path": options.get("resource_view", "/var/lib/lxcfs"),
                    "type": "Directory",
                },
            },
        ]
    )
    mounts.extend(
        [
            {"name": "config", "mountPath": "/run/platform", "readOnly": True},
            {"name": "run", "mountPath": "/run/user"},
            {"name": "shm", "mountPath": "/dev/shm"},
        ]
    )
    if value := task.extensions.get(FEATURE):
        from uuid import UUID

        identity = PeerIdentity.model_validate(value)
        if state_directory is None:
            raise ValueError("runtime.peer_state_directory_required")
        peer_directory = state_directory / "peer-ssh" / str(UUID(task.id)) / "mount"
        volumes.append({"name": "peer-ssh", "hostPath": {"path": str(peer_directory), "type": "Directory"}})
        mounts.append({"name": "peer-ssh", "mountPath": "/run/platform-peer", "readOnly": True})
        secret["stringData"].update({
            "peer_proxy": (Path(__file__).parent / "images" / "peer-proxy.sh").read_text(),
            "peer_host_certificate": identity.host_certificate + "\n",
            "peer_user_authority": identity.user_authority + "\n",
            "peer_principal": f"account-{task.owner}\n",
            "peer_known_hosts": f"@cert-authority *.workspaces.internal {identity.host_authority}\n",
        })
    mounts.extend(
        {"name": "resource-view", "mountPath": "/" + path, "subPath": path, "readOnly": True}
        for path in [
            "proc/cpuinfo",
            "proc/meminfo",
            "proc/stat",
            "proc/uptime",
            "proc/swaps",
            "proc/diskstats",
            "proc/loadavg",
            "sys/devices/system/cpu/online",
        ]
    )
    environment = {
        "HOME": home,
        "USER": task.identity.username,
        "LOGNAME": task.identity.username,
        "NVIDIA_VISIBLE_DEVICES": "void",
    }
    if inputs.gpu:
        environment.update(inputs.gpu.environment)
        for index, mount in enumerate(inputs.gpu.mounts):
            key = f"gpu-{index}"
            volumes.append({"name": key, "hostPath": {"path": mount["source"], "type": "Directory"}})
            mounts.append(
                {"name": key, "mountPath": mount["target"], "readOnly": mount.get("read_only", True)}
            )
    container: dict[str, Any] = {
        "name": "workspace",
        "image": inputs.image.reference if inputs.image else task.image,
        "imagePullPolicy": "IfNotPresent",
        "command": ["/bin/bash", "/run/platform/runner.sh", "/run/platform/task.env"],
        "env": [{"name": key, "value": value} for key, value in environment.items()],
        "resources": {"requests": limits.copy(), "limits": limits.copy()},
        "ports": [{"name": "ssh", "containerPort": 2222}],
        "volumeMounts": mounts,
        "securityContext": {
            "allowPrivilegeEscalation": True,
            "appArmorProfile": {"type": "Localhost", "localhostProfile": "gpu-platform-workspace"},
        },
        "readinessProbe": {"tcpSocket": {"port": 2222}, "initialDelaySeconds": 3, "periodSeconds": 5},
    }
    spec: dict[str, Any] = {
        "restartPolicy": "Never",
        "runtimeClassName": options.get(
            "gpu_runtime_class" if inputs.gpu else "runtime_class",
            "workspace-gpu" if inputs.gpu else "workspace",
        ),
        "automountServiceAccountToken": False,
        "serviceAccountName": "workspace",
        "enableServiceLinks": False,
        "terminationGracePeriodSeconds": 70,
        "securityContext": {
            "runAsNonRoot": True,
            "runAsUser": task.identity.uid,
            "runAsGroup": task.identity.gid,
            "fsGroup": task.identity.gid,
            "fsGroupChangePolicy": "OnRootMismatch",
            "seccompProfile": {"type": "Localhost", "localhostProfile": "gpu-platform-workspace.json"},
        },
        "containers": [container],
        "volumes": volumes,
    }
    if inputs.image and inputs.image.credentials:
        credentials = inputs.image.credentials
        if not re.fullmatch(r"[A-Za-z0-9.:[\]-]+", credentials["server"]):
            raise ValueError("runtime.invalid_registry_server")
        secret["stringData"][".dockerconfigjson"] = json.dumps(
            {
                "auths": {
                    credentials["server"]: {
                        "auth": base64.b64encode(
                            f"{credentials['username']}:{credentials['password']}".encode()
                        ).decode(),
                    }
                }
            }
        )
        spec["imagePullSecrets"] = [{"name": name + "-image"}]
        spec["volumes"].append(
            {
                "name": "registry-auth",
                "secret": {
                    "secretName": name + "-image",
                    "items": [{"key": ".dockerconfigjson", "path": "config.json"}],
                    "defaultMode": 0o440,
                },
            }
        )
        container["volumeMounts"].append(
            {"name": "registry-auth", "mountPath": "/run/platform-registry", "readOnly": True}
        )
        certificate = inputs.image.metadata.get("ca_certificate")
        if certificate:
            spec["volumes"].append(
                {
                    "name": "registry-ca",
                    "secret": {
                        "secretName": name + "-image",
                        "items": [{"key": "ca.crt", "path": "ca.crt"}],
                        "defaultMode": 0o444,
                    },
                }
            )
            container["volumeMounts"].append(
                {
                    "name": "registry-ca",
                    "mountPath": "/etc/docker/certs.d/" + credentials["server"],
                    "readOnly": True,
                }
            )
    annotations["platform.expires-at"] = task.expires_at.isoformat() if task.expires_at else ""
    metadata = {
        **metadata,
        "labels": {
            **labels,
            "kueue.x-k8s.io/queue-name": options.get("queue", "platform"),
            "kueue.x-k8s.io/priority-class": "platform-high" if task.priority > 0 else "platform-normal",
        },
    }
    if task.kind == "batch":
        workload = {
            "apiVersion": "batch/v1",
            "kind": "Job",
            "metadata": metadata,
            "spec": {
                "suspend": True,
                "parallelism": 1,
                "completions": 1,
                "backoffLimit": 0,
                "template": {"metadata": {"labels": labels, "annotations": annotations}, "spec": spec},
            },
        }
    else:
        spec["schedulingGates"] = [{"name": "kueue.x-k8s.io/admission"}]
        workload = {"apiVersion": "v1", "kind": "Pod", "metadata": metadata, "spec": spec}
    service = {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": metadata,
        "spec": {
            "type": "NodePort",
            "selector": labels,
            "ports": [
                {
                    "name": "ssh",
                    "port": 2222,
                    "targetPort": 2222,
                    **({"nodePort": task.login_port} if task.login_port else {}),
                }
            ],
        },
    }
    objects = [secret, service, workload]
    if inputs.image and inputs.image.credentials:
        image_secret = {".dockerconfigjson": secret["stringData"].pop(".dockerconfigjson")}
        if inputs.image.metadata.get("ca_certificate"):
            image_secret["ca.crt"] = inputs.image.metadata["ca_certificate"]
        objects.insert(
            1,
            {
                "apiVersion": "v1",
                "kind": "Secret",
                "type": "kubernetes.io/dockerconfigjson",
                "metadata": {**metadata, "name": name + "-image"},
                "stringData": image_secret,
            },
        )
    return objects
