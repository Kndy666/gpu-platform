"""Rewrite snapshot metadata without retaining superseded credential-bearing blobs."""

import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import tarfile

METADATA_LIMIT = 16 * 1024 * 1024


def transient_key(key):
    return key.startswith(("PLATFORM_GPU_", "CUDA_MPS_")) or key in {
        "NVIDIA_VISIBLE_DEVICES", "CUDA_VISIBLE_DEVICES"
    }


def sanitized_config(configuration):
    removed = set()
    for name in ("config", "container_config"):
        section = configuration.get(name)
        if not isinstance(section, dict):
            continue
        environment = section.get("Env") or []
        retained = []
        for entry in environment:
            key, _, value = entry.partition("=")
            if transient_key(key):
                if len(value) >= 8:
                    removed.add(value)
            else:
                retained.append(entry)
        section["Env"] = [*retained, "NVIDIA_VISIBLE_DEVICES=void"]
    for history in configuration.get("history") or []:
        for field in ("created_by", "comment"):
            text = history.get(field, "")
            if any(value in text for value in removed) or any(
                key in text for key in ("PLATFORM_GPU_", "CUDA_MPS_", "CUDA_VISIBLE_DEVICES", "NVIDIA_VISIBLE_DEVICES")
            ):
                history.pop(field, None)
    return configuration


def sanitize_snapshot(source: Path, destination: Path):
    """Copy verified layers and rewritten reachable OCI metadata into a private archive."""
    temporary = destination.with_name(destination.name + ".part")
    try:
        with tarfile.open(source) as incoming, temporary.open("xb") as output:
            temporary.chmod(0o600)
            with tarfile.open(fileobj=output, mode="w") as outgoing:
                members: dict[str, tarfile.TarInfo] = {}
                for member in incoming:
                    path = PurePosixPath(member.name)
                    if path.is_absolute() or ".." in path.parts or member.issym() or member.islnk() or member.isdev():
                        raise ValueError("runtime.invalid_snapshot_archive")
                    if member.isdir():
                        continue
                    if not member.isfile() or member.name in members or len(members) >= 100000:
                        raise ValueError("runtime.invalid_snapshot_archive")
                    members[member.name] = member
                emitted = set()

                def read_json(name, descriptor=None):
                    member = members[name]
                    if member.size > METADATA_LIMIT:
                        raise ValueError("runtime.snapshot_metadata_too_large")
                    stream = incoming.extractfile(member)
                    if stream is None:
                        raise ValueError("runtime.invalid_snapshot_archive")
                    with stream:
                        content = stream.read(METADATA_LIMIT + 1)
                    if descriptor is not None and (
                        len(content) != descriptor["size"]
                        or "sha256:" + hashlib.sha256(content).hexdigest() != descriptor["digest"]
                    ):
                        raise ValueError("runtime.snapshot_digest_mismatch")
                    return json.loads(content)

                def blob_name(descriptor):
                    digest = descriptor["digest"]
                    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
                        raise ValueError("runtime.invalid_snapshot_digest")
                    return "blobs/sha256/" + digest.split(":", 1)[1]

                def write_bytes(name, data):
                    if name in emitted:
                        return
                    member = tarfile.TarInfo(name)
                    member.size, member.mode = len(data), 0o600
                    outgoing.addfile(member, io.BytesIO(data))
                    emitted.add(name)

                def write_json(name, value):
                    data = json.dumps(value, separators=(",", ":")).encode()
                    write_bytes(name, data)

                def rewritten(descriptor, value):
                    data = json.dumps(value, separators=(",", ":")).encode()
                    digest = "sha256:" + hashlib.sha256(data).hexdigest()
                    result = {key: value for key, value in descriptor.items() if key not in {"data", "urls"}}
                    result.update(digest=digest, size=len(data))
                    write_bytes(blob_name(result), data)
                    return result

                def copy_layer(descriptor):
                    name = blob_name(descriptor)
                    member = members[name]
                    if member.size != descriptor["size"]:
                        raise ValueError("runtime.snapshot_digest_mismatch")
                    if name in emitted:
                        return
                    stream = incoming.extractfile(member)
                    if stream is None:
                        raise ValueError("runtime.invalid_snapshot_archive")
                    checksum = hashlib.sha256()
                    with stream:
                        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                            checksum.update(chunk)
                        if "sha256:" + checksum.hexdigest() != descriptor["digest"]:
                            raise ValueError("runtime.snapshot_digest_mismatch")
                        stream.seek(0)
                        copied = tarfile.TarInfo(name)
                        copied.size, copied.mode = member.size, 0o600
                        outgoing.addfile(copied, stream)
                    emitted.add(name)

                def rewrite(descriptor, depth=0):
                    if depth > 8:
                        raise ValueError("runtime.snapshot_index_depth_exceeded")
                    value = read_json(blob_name(descriptor), descriptor)
                    if "manifests" in value:
                        value["manifests"] = [rewrite(child, depth + 1) for child in value["manifests"]]
                    else:
                        configuration = value["config"]
                        value["config"] = rewritten(
                            configuration, sanitized_config(read_json(blob_name(configuration), configuration))
                        )
                        value["layers"] = value.get("layers") or []
                        for layer in value["layers"]:
                            copy_layer(layer)
                    return rewritten(descriptor, value)

                layout = read_json("oci-layout")
                if layout.get("imageLayoutVersion") != "1.0.0":
                    raise ValueError("runtime.invalid_snapshot_layout")
                index = read_json("index.json")
                index["manifests"] = [rewrite(descriptor) for descriptor in index["manifests"]]
                write_json("oci-layout", {"imageLayoutVersion": "1.0.0"})
                write_json("index.json", index)
        temporary.replace(destination)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
