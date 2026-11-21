"""Route complete OCI repositories to per-node image filesystems."""

import hashlib
import json
import shutil
from decimal import Decimal
from pathlib import Path
from threading import Lock

from .accounts import atomic_write

POOL_MOUNTS = Path("/var/lib/gpu-platform-image-pools")


def existing_pool(options):
    return next((pool for pool in options.pools if pool.name == options.existing_images_pool),
                options.pools[0] if options.pools else None)


def storage_config(options):
    pool = existing_pool(options)
    root = POOL_MOUNTS / pool.name / "existing" if pool else Path("/var/lib/registry/data")
    return {
        "rootDirectory": str(root), "gc": True,
        "dedupe": True,
        **({"subPaths": {
            "/pool-" + entry.name: {
                "rootDirectory": str(POOL_MOUNTS / entry.name / "images"),
                "dedupe": True, "gc": True,
            } for entry in options.pools
        }} if options.pools else {}),
    }


class ImagePlacement:
    def __init__(self, root, options, records):
        self.root = root / "placements"
        self.options = options
        self.records = records
        self.lock = Lock()

    def path(self, image_id):
        return self.root / (hashlib.sha256(image_id.encode()).hexdigest() + ".json")

    def reserve(self, image_id, owner, digest, size):
        if not self.options.pools:
            return None
        with self.lock:
            self.root.mkdir(mode=0o700, exist_ok=True)
            path = self.path(image_id)
            if path.exists():
                placement = json.loads(path.read_text())
                if placement["owner"] != owner or placement["digest"] != digest:
                    raise ValueError("registry.image_placement_conflict")
                if (placement["pool"] not in {pool.name for pool in self.options.pools}
                        or not (POOL_MOUNTS / placement["pool"]).is_mount()):
                    raise OSError("registry.image_pool_unavailable")
                return placement["pool"]

            user_usage = dict.fromkeys((pool.name for pool in self.options.pools), 0)
            pending = user_usage.copy()
            for record in self.root.glob("*.json"):
                if (self.records / record.name).exists():
                    continue
                placement = json.loads(record.read_text())
                pool = placement["pool"]
                if pool in pending:
                    pending[pool] += placement["size"]
                    if placement["owner"] == owner:
                        user_usage[pool] += placement["size"]
            old_pool = existing_pool(self.options)
            for record in self.records.glob("*.json"):
                image = json.loads(record.read_text())
                pool = image.get("metadata", {}).get("pool", old_pool.name if old_pool else None)
                if image["owner"] == owner and pool in user_usage:
                    user_usage[pool] += image["size_bytes"]

            choices = []
            for pool in self.options.pools:
                location = POOL_MOUNTS / pool.name
                try:
                    if not location.is_mount():
                        continue
                    capacity, _, free = shutil.disk_usage(location)
                except OSError:
                    continue
                free -= pending[pool.name] + capacity * pool.buffer_percent // 100
                if capacity and free >= size:
                    choices.append((Decimal(user_usage[pool.name]) / capacity, -free, pool.name))
            if not choices:
                raise OSError("registry.image_pool_capacity_unavailable")
            pool_name = min(choices)[2]
            atomic_write(path, json.dumps({"pool": pool_name, "owner": owner, "digest": digest, "size": size}))
            return pool_name

    def release(self, image_id):
        with self.lock:
            self.path(image_id).unlink(missing_ok=True)
