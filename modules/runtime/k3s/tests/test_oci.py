import hashlib
import io
import json
import tarfile

import pytest

from gpu_platform_runtime_k3s.oci import sanitize_snapshot


def archive_fixture(path, *, nested=False, corrupt=False):
    blobs = {}

    def blob(value, media_type):
        data = value if isinstance(value, bytes) else json.dumps(value).encode()
        digest = "sha256:" + hashlib.sha256(data).hexdigest()
        blobs["blobs/sha256/" + digest.split(":")[1]] = data
        return {"mediaType": media_type, "digest": digest, "size": len(data)}

    layer = blob(b"unchanged-layer-payload", "application/vnd.oci.image.layer.v1.tar")
    config = blob({
        "architecture": "amd64", "os": "linux",
        "config": {"Env": ["PATH=/usr/bin", "CUDA_HOME=/usr/local/cuda", "NVIDIA_VISIBLE_DEVICES=GPU-fixture",
                           "CUDA_VISIBLE_DEVICES=GPU-fixture", "CUDA_MPS_PIPE_DIRECTORY=/run/platform-mps",
                           "PLATFORM_GPU_LEASE=private-lease-token", "PLATFORM_GPU_TASK=fixture-task-id"]},
        "rootfs": {"type": "layers", "diff_ids": [layer["digest"]]},
        "history": [{"created_by": "base image creation"},
                    {"created_by": "ENV PLATFORM_GPU_LEASE=private-lease-token", "empty_layer": True}],
    }, "application/vnd.oci.image.config.v1+json")
    manifest = blob({"schemaVersion": 2, "mediaType": "application/vnd.oci.image.manifest.v1+json",
                     "config": config, "layers": [layer]}, "application/vnd.oci.image.manifest.v1+json")
    top = manifest
    if nested:
        top = blob({"schemaVersion": 2, "manifests": [manifest]}, "application/vnd.oci.image.index.v1+json")
    index = {"schemaVersion": 2, "manifests": [top]}
    blob(b"unreferenced private-lease-token", "application/octet-stream")
    if corrupt:
        blobs["blobs/sha256/" + layer["digest"].split(":")[1]] = b"X" * layer["size"]
    with tarfile.open(path, "w") as archive:
        for name, data in {"oci-layout": b'{"imageLayoutVersion":"1.0.0"}',
                           "index.json": json.dumps(index).encode(), "manifest.json": b"discarded-export-metadata", **blobs}.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return config, layer


@pytest.mark.parametrize("nested", [False, True])
def test_rewrites_reachable_metadata_and_omits_original_secret_blobs(tmp_path, nested):
    source, destination = tmp_path / "source.tar", tmp_path / "snapshot.tar"
    old_config, layer = archive_fixture(source, nested=nested)
    sanitize_snapshot(source, destination)
    with tarfile.open(destination) as archive:
        contents = {member.name: archive.extractfile(member).read() for member in archive.getmembers()}
    assert all(b"private-lease-token" not in data for data in contents.values())
    assert "blobs/sha256/" + old_config["digest"].split(":")[1] not in contents
    assert "manifest.json" not in contents
    reachable = {"index.json", "oci-layout"}

    def read(descriptor):
        path = "blobs/sha256/" + descriptor["digest"].split(":")[1]
        reachable.add(path)
        data = contents[path]
        assert len(data) == descriptor["size"]
        assert "sha256:" + hashlib.sha256(data).hexdigest() == descriptor["digest"]
        return data

    manifest = json.loads(read(json.loads(contents["index.json"])["manifests"][0]))
    if nested:
        manifest = json.loads(read(manifest["manifests"][0]))
    configuration = json.loads(read(manifest["config"]))
    assert configuration["config"]["Env"] == ["PATH=/usr/bin", "CUDA_HOME=/usr/local/cuda", "NVIDIA_VISIBLE_DEVICES=void"]
    assert configuration["rootfs"]["diff_ids"] == [layer["digest"]]
    assert manifest["layers"] == [layer]
    assert read(layer) == b"unchanged-layer-payload"
    assert set(contents) == reachable
    assert destination.stat().st_mode & 0o777 == 0o600


def test_corrupt_reachable_blob_never_commits_archive(tmp_path):
    source, destination = tmp_path / "source.tar", tmp_path / "snapshot.tar"
    archive_fixture(source, corrupt=True)
    with pytest.raises(ValueError):
        sanitize_snapshot(source, destination)
    assert not destination.exists()
    assert not destination.with_name(destination.name + ".part").exists()


def test_runtime_commits_supported_options_and_returns_only_sanitized_archive(tmp_path):
    from pathlib import Path
    import shutil
    from types import SimpleNamespace

    from gpu_platform_contracts.module import ModuleContext
    from gpu_platform_runtime_k3s.module import Module

    source = tmp_path / "committed.tar"
    archive_fixture(source)
    pod = {"spec": {"containers": [{"name": "workspace", "volumeMounts": []}]},
           "status": {"phase": "Running", "containerStatuses": [{"name": "workspace",
                      "containerID": "containerd://" + "a" * 64}]}}
    client = SimpleNamespace(list=lambda *args: [pod], check_owner=lambda *args: None)
    commands = []

    def run(args, **kwargs):
        commands.append(args)
        if "commit" in args:
            assert "--change" not in args
        if args[:6] == ["k3s", "ctr", "--namespace", "k8s.io", "images", "export"]:
            shutil.copyfile(source, args[6])
        return SimpleNamespace(stdout="", returncode=0)

    runtime = Module(ModuleContext(node_id="test", state_directory=tmp_path), client=client, run=run)
    runtime.save("task", {"task": {"owner": "owner"}})
    result = runtime.snapshot("task", "owner")
    assert Path(result.location).is_file() and result.size_bytes == Path(result.location).stat().st_size
    assert b"private-lease-token" not in Path(result.location).read_bytes()
    assert not list(runtime.root.glob("*.raw.tar"))
    assert commands[-1][:6] == ["k3s", "ctr", "--namespace", "k8s.io", "images", "rm"]
