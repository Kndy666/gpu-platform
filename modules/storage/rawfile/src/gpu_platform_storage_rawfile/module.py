"""Node-owned Rawfile volumes and resumable copies between PVCs."""

import hashlib
import json
import os
import subprocess
from decimal import Decimal

from gpu_platform_contracts.assessment import ResourceAssessment
from gpu_platform_contracts.handoffs import Volume, VolumeRequest
from gpu_platform_contracts.module import ModuleDescriptor, SlotPending
from gpu_platform_contracts.resources import ResourceDefinition, ResourceUsage, StoragePoolUsage
from gpu_platform_contracts.slots.storage import Storage
from gpu_platform_contracts.texts import LocalizedText, Message
from .options import Options

COPY_SCRIPT = """set -eu
source=${1:-/source}
destination=${2:-/destination}
needed=$(du -sx --block-size=1 "$source" | cut -f1)
available=$(df -B1 --output=avail "$destination" | tail -n 1 | tr -d ' ')
[ "$needed" -lt "$available" ] || exit 72
cp -a --sparse=always "$source/." "$destination/"
sync -f "$destination"
"""



class Module(Storage):
    @classmethod
    def check_host(cls, context):
        from .install import check_host

        return check_host(context)

    @classmethod
    def install(cls, context):
        from .install import install

        install(context)

    @classmethod
    def uninstall(cls, context):
        from .install import uninstall

        uninstall(context)

    @classmethod
    def compose(cls, context):
        from .install import compose

        return compose(context)

    options_model = Options
    descriptor = ModuleDescriptor(
        parameters=Options.model_json_schema(),
        name="rawfile",
        slot="storage",
        version="0.4.0",
        side="node",
        requires={"runtime": {"k3s"}},
        provides={"volume": {"kubernetes-pvc"}},
        resources=[
            ResourceDefinition(key="storage", unit="GiB", accounting="volume", requestable=False,
                               label=LocalizedText(en="Storage", zh_hans="存储")),
            ResourceDefinition(key="home_storage", unit="GiB", accounting="volume", volume_kind="home",
                               integral=True, minimum=Decimal(1), label=LocalizedText(en="Home volume", zh_hans="个人卷")),
            ResourceDefinition(key="work_storage", unit="GiB", accounting="volume", volume_kind="work",
                               integral=True, minimum=Decimal(1), label=LocalizedText(en="Work volume", zh_hans="工作卷")),
        ],
    )

    def __init__(self, context, run=subprocess.run):
        super().__init__(context)
        self.run = run
        self.root = context.state_directory / "volumes"
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.namespace = context.options.get("namespace", "gpu-workloads")

    def command(self, *args, data=None):
        result = self.run(
            ["k3s", "kubectl", "-n", self.namespace, *args],
            input=json.dumps(data) if data is not None else None,
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        )
        return json.loads(result.stdout) if result.stdout.strip() else None

    def path(self, volume_id):
        return self.root / (hashlib.sha256(volume_id.encode()).hexdigest() + ".json")

    def persist(self, volume):
        path = self.path(volume.id)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(volume.model_dump_json())
        temporary.chmod(0o600)
        temporary.replace(path)

    def get(self, volume_id, owner):
        volume = Volume.model_validate_json(self.path(volume_id).read_text())
        if volume.owner != owner:
            raise PermissionError("storage.owner_mismatch")
        return volume

    def claim(self, volume):
        claim = self.command("get", "pvc", volume.location, "--ignore-not-found", "-o", "json")
        if claim and (
            claim["metadata"].get("annotations", {}).get("platform.owner") != volume.owner
            or claim["metadata"].get("annotations", {}).get("platform.volume") != volume.id
        ):
            raise PermissionError("storage.claim_owner_mismatch")
        return claim

    def mounted(self, location):
        pods = self.command("get", "pods", "-o", "json")["items"]
        return any(
            v.get("persistentVolumeClaim", {}).get("claimName") == location
            for pod in pods
            if pod.get("status", {}).get("phase") not in {"Succeeded", "Failed"}
            for v in pod.get("spec", {}).get("volumes", [])
        )

    def accounted_volumes(self):
        volumes = [Volume.model_validate_json(path.read_text()) for path in self.root.glob("*.json")]
        transitions = [volume.metadata["resize_transition"] for volume in volumes if volume.metadata.get("resize_transition")]
        duplicates = {transition["destination_id"] for transition in transitions}
        return [volume for volume in volumes if volume.id not in duplicates] + [
            Volume.model_validate(transition["source"]) for transition in transitions]

    def pool_availability(self, existing):
        pools = self.context.options.get("pools", [])
        available = {}
        for pool in pools:
            stats = os.statvfs(pool["path"])
            capacity = Decimal(stats.f_blocks * stats.f_frsize) / 2**30
            free = Decimal(stats.f_bavail * stats.f_frsize) / 2**30
            reserve = capacity * Decimal(str(pool.get("buffer_percent", 5))) / 100
            allocated = sum((volume.size_gib for volume in existing
                             if volume.metadata.get("storage_class") == pool["storage_class"]), Decimal(0))
            available[pool["storage_class"]] = max(Decimal(0), min(free, capacity - allocated) - reserve)
        return available

    def assess(self, intent):
        existing = self.accounted_volumes()
        available = self.pool_availability(existing)
        quantities = {"storage": sum((volume.size_gib for volume in intent.volumes), Decimal(0))} if intent.volumes else {}
        for kind in ("home", "work"):
            if intent.volumes:
                quantities[kind + "_storage"] = sum((volume.size_gib for volume in intent.volumes if volume.kind == kind), Decimal(0))
        by_id = {volume.id: volume for volume in (
            Volume.model_validate_json(path.read_text()) for path in self.root.glob("*.json"))}
        seen = set()
        for requested in sorted(intent.volumes, key=lambda volume: volume.size_gib, reverse=True):
            if requested.id in seen or requested.size_gib <= 0:
                return ResourceAssessment(feasible=False, quantities=quantities, reason=Message(code="storage.invalid_request"))
            seen.add(requested.id)
            prior = by_id.get(requested.id)
            if prior and prior.owner != intent.owner:
                return ResourceAssessment(feasible=False, reason=Message(code="storage.owner_mismatch"))
            if prior and prior.metadata.get("resize_transition"):
                return ResourceAssessment(feasible=False, reason=Message(code="storage.resize_pending"))
            if prior and requested.size_gib >= prior.size_gib:
                required = requested.size_gib - prior.size_gib
                eligible = [prior.metadata.get("storage_class")]
            else:
                required = requested.size_gib
                eligible = list(available)
            if required == 0:
                continue
            candidates = [key for key in eligible if available.get(key, 0) >= required]
            if not candidates:
                return ResourceAssessment(feasible=False, quantities=quantities, reason=Message(code="storage.capacity_unavailable"))
            selected = max(candidates, key=lambda key: available[key])
            available[selected] -= required
        return ResourceAssessment(feasible=True, quantities=quantities)

    def create(self, owner, request):
        if request.size_gib <= 0:
            raise ValueError("storage.invalid_size")
        path = self.path(request.id)
        if path.exists():
            volume = self.get(request.id, owner)
            if volume.size_gib != request.size_gib or volume.mount_path != request.mount_path:
                raise ValueError("storage.volume_conflict")
        else:
            pools = self.context.options.get("pools", [])
            storage_class = self.context.options.get("storage_class", "rawfile")
            if pools:
                eligible = [(capacity, name) for name, capacity in self.pool_availability(self.accounted_volumes()).items()
                            if capacity >= request.size_gib]
                if not eligible:
                    raise OSError("storage.capacity_unavailable")
                storage_class = max(eligible)[1]
            volume = Volume(
                id=request.id,
                owner=owner,
                node_id=self.context.node_id,
                size_gib=request.size_gib,
                location="volume-" + path.stem[:32],
                mount_path=request.mount_path,
                form="kubernetes-pvc",
                metadata={"storage_class": storage_class, "kind": request.kind},
            )
        claim = self.claim(volume)
        if claim is None and path.exists():
            raise FileNotFoundError("storage.claim_missing")
        if claim is None:
            self.command(
                "create",
                "-f",
                "-",
                "-o",
                "json",
                data={
                    "apiVersion": "v1",
                    "kind": "PersistentVolumeClaim",
                    "metadata": {
                        "name": volume.location,
                        "annotations": {"platform.owner": owner, "platform.volume": request.id},
                    },
                    "spec": {
                        "accessModes": ["ReadWriteOnce"],
                        "storageClassName": volume.metadata["storage_class"],
                        "resources": {"requests": {"storage": f"{request.size_gib}Gi"}},
                    },
                },
            )
        self.persist(volume)
        return volume

    def delete_claim(self, volume, before_delete):
        claim = self.claim(volume)
        if claim is None:
            return
        if self.mounted(volume.location):
            raise SlotPending(reason="storage.volume_busy")
        persistent_volume = claim.get("spec", {}).get("volumeName")
        if persistent_volume:
            volume.metadata.update(deleting_pv=persistent_volume, deleting_claim_uid=claim["metadata"]["uid"])
            before_delete(volume)
        self.command(
            "delete",
            "--raw",
            f"/api/v1/namespaces/{self.namespace}/persistentvolumeclaims/{volume.location}",
            "-f",
            "-",
            data={
                "apiVersion": "v1",
                "kind": "DeleteOptions",
                "preconditions": {"uid": claim["metadata"]["uid"]},
            },
        )

    def released(self, volume):
        if self.claim(volume) is not None:
            return False
        name = volume.metadata.get("deleting_pv")
        if name:
            pv = self.command("get", "pv", name, "--ignore-not-found", "-o", "json")
            if pv and pv.get("spec", {}).get("claimRef", {}).get("uid") == volume.metadata.get("deleting_claim_uid"):
                return False
        return True

    def delete(self, volume_id, owner):
        if not self.path(volume_id).exists():
            return
        volume = self.get(volume_id, owner)
        self.delete_claim(volume, self.persist)
        if not self.released(volume):
            raise SlotPending(reason="storage.deletion_pending")
        self.path(volume_id).unlink()

    def resize(self, volume_id, owner, size_gib):
        volume = self.get(volume_id, owner)
        if volume.metadata.get("resize_transition"):
            volume = self.finish_resize(volume)
        if size_gib <= 0:
            raise ValueError("storage.invalid_size")
        if size_gib == volume.size_gib:
            return volume
        if size_gib > volume.size_gib:
            claim = self.claim(volume)
            if claim is None:
                raise FileNotFoundError("storage.claim_missing")
            self.command(
                "patch",
                "pvc",
                volume.location,
                "--type=merge",
                "-p",
                json.dumps(
                    {
                        "metadata": {"resourceVersion": claim["metadata"]["resourceVersion"]},
                        "spec": {"resources": {"requests": {"storage": f"{size_gib}Gi"}}},
                    }
                ),
                "-o",
                "json",
            )
            volume.size_gib = size_gib
            self.persist(volume)
            return volume
        if self.mounted(volume.location):
            raise SlotPending(reason="storage.volume_busy")
        destination = VolumeRequest(
            id=volume.id + ":resize:" + str(size_gib),
            size_gib=size_gib,
            kind=volume.metadata["kind"],
            mount_path=volume.mount_path,
        )
        copied = self.copy(volume_id, owner, destination)
        replacement = copied.model_copy(update={"id": volume.id, "metadata": {
            **copied.metadata, "resize_transition": {"source": volume.model_dump(mode="json"), "destination_id": copied.id}
        }})
        self.persist(replacement)
        return self.finish_resize(replacement)

    def finish_resize(self, replacement):
        transition = replacement.metadata["resize_transition"]
        source = Volume.model_validate(transition["source"])
        claim = self.command("get", "pvc", replacement.location, "--ignore-not-found", "-o", "json")
        if claim is None:
            raise FileNotFoundError("storage.resize_target_missing")
        annotations = claim["metadata"].get("annotations", {})
        if annotations.get("platform.owner") != replacement.owner or annotations.get("platform.volume") not in {replacement.id, transition["destination_id"]}:
            raise PermissionError("storage.claim_owner_mismatch")
        self.command("annotate", "pvc", replacement.location, "platform.volume=" + replacement.id, "--overwrite", "-o", "json")
        def before_delete(volume):
            transition["source"] = volume.model_dump(mode="json")
            self.persist(replacement)

        self.delete_claim(source, before_delete)
        if not self.released(source):
            raise SlotPending(reason="storage.resize_cleanup_pending")
        self.path(transition["destination_id"]).unlink(missing_ok=True)
        replacement.metadata.pop("resize_transition")
        self.persist(replacement)
        return replacement

    def copy(self, volume_id, owner, destination):
        source = self.get(volume_id, owner)
        if source.id == destination.id:
            raise ValueError("storage.copy_same_volume")
        if self.mounted(source.location):
            raise SlotPending(reason="storage.volume_busy")
        target = self.create(owner, destination)
        if target.metadata.get("copy_complete"):
            if target.metadata.get("copy_source") != source.id:
                raise ValueError("storage.copy_conflict")
            return target
        name = "copy-" + hashlib.sha256((source.id + "\0" + destination.id).encode()).hexdigest()[:32]
        job = self.command("get", "job", name, "--ignore-not-found", "-o", "json")
        if job:
            if job["metadata"].get("annotations", {}).get("platform.owner") != owner:
                raise PermissionError("storage.copy_owner_mismatch")
            if job.get("status", {}).get("succeeded"):
                target.metadata.update(copy_source=source.id, copy_complete=True)
                self.persist(target)
                return target
            if job.get("status", {}).get("failed"):
                raise OSError("storage.copy_failed")
        else:
            self.command(
                "create",
                "-f",
                "-",
                "-o",
                "json",
                data={
                    "apiVersion": "batch/v1",
                    "kind": "Job",
                    "metadata": {"name": name, "annotations": {"platform.owner": owner}},
                    "spec": {
                        "backoffLimit": 0,
                        "ttlSecondsAfterFinished": 86400,
                        "activeDeadlineSeconds": 86400,
                        "template": {
                            "spec": {
                                "automountServiceAccountToken": False,
                                "restartPolicy": "Never",
                                "securityContext": {
                                    "runAsUser": 0,
                                    "runAsGroup": 0,
                                    "seccompProfile": {"type": "RuntimeDefault"},
                                },
                                "containers": [
                                    {
                                        "name": "copy",
                                        "image": self.context.options.get(
                                            "copy_image", "debian:bookworm-slim"
                                        ),
                                        "command": ["/bin/sh", "-c", COPY_SCRIPT],
                                        "resources": {
                                            "requests": {"cpu": "100m", "memory": "128Mi"},
                                            "limits": {"cpu": "1", "memory": "512Mi"},
                                        },
                                        "volumeMounts": [
                                            {"name": "source", "mountPath": "/source", "readOnly": True},
                                            {"name": "destination", "mountPath": "/destination"},
                                        ],
                                    }
                                ],
                                "volumes": [
                                    {
                                        "name": "source",
                                        "persistentVolumeClaim": {
                                            "claimName": source.location,
                                            "readOnly": True,
                                        },
                                    },
                                    {
                                        "name": "destination",
                                        "persistentVolumeClaim": {"claimName": target.location},
                                    },
                                ],
                            }
                        },
                    },
                },
            )
        raise SlotPending(reason="storage.copy_pending")

    def resources(self):
        volumes = self.accounted_volumes()
        used = {"storage": sum((volume.size_gib for volume in volumes), Decimal(0))}
        for kind in ("home", "work"):
            used[kind + "_storage"] = sum((volume.size_gib for volume in volumes if volume.metadata.get("kind") == kind), Decimal(0))
        available = sum(self.pool_availability(volumes).values(), Decimal(0))
        total = used["storage"] + available
        pools = []
        for pool in self.context.options.get("pools", []):
            stats = os.statvfs(pool["path"])
            reserved = sum((volume.size_gib for volume in volumes
                            if volume.metadata.get("storage_class") == pool["storage_class"]), Decimal(0))
            pools.append(StoragePoolUsage(
                name=pool["name"], capacity_gib=Decimal(stats.f_blocks * stats.f_frsize) / 2**30,
                free_gib=Decimal(stats.f_bavail * stats.f_frsize) / 2**30, reserved_gib=reserved,
                buffer_percent=pool.get("buffer_percent", 5), metadata={"storage_class": pool["storage_class"]}))
        return ResourceUsage(total={key: total if key == "storage" else used[key] + available for key in used}, used=used, pools=pools)
