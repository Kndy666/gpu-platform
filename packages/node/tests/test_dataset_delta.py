import io
import errno
import os
from pathlib import Path

import pytest

from gpu_platform_node.datasets import Datasets


def prepare(sender, receiver, identifier="transfer", paths=None):
    manifest = sender.manifest(paths or [])
    receiver.begin(identifier, io.BytesIO(sender.encode_manifest(manifest)), paths or [])
    return manifest


def copy_files(sender, receiver, manifest, identifier="transfer"):
    transferred = []
    for name, entry in manifest["entries"].items():
        if entry["kind"] != "file":
            continue
        status = receiver.prepare_file(identifier, name)
        if not status["complete"]:
            with sender.resolve(name).open("rb") as source:
                source.seek(status["offset"])
                receiver.receive_file(
                    identifier, name, status["offset"], iter(lambda: source.read(4096), b"")
                )
            assert receiver.verify_file(identifier, name)
            transferred.append(name)
    return transferred


def test_import_renames_within_dataset_mount(tmp_path, monkeypatch):
    source, mount = tmp_path / "source", tmp_path / "dataset-mount"
    source.mkdir()
    mount.mkdir()
    (source / "changed").write_bytes(b"new")
    (mount / "changed").write_bytes(b"old")
    sender, receiver = Datasets(source, "source"), Datasets(mount, "target")
    manifest = prepare(sender, receiver)
    replace = os.replace

    def mounted_replace(origin, destination):
        if Path(origin).is_relative_to(mount) != Path(destination).is_relative_to(mount):
            raise OSError(errno.EXDEV, "cross-device rename")
        return replace(origin, destination)

    monkeypatch.setattr(os, "replace", mounted_replace)
    copy_files(sender, receiver, manifest)
    receiver.finish("transfer")
    assert (mount / "changed").read_bytes() == b"new"
    assert [entry["name"] for entry in receiver.list()] == ["changed"]


def test_public_directory_excludes_active_transaction(tmp_path):
    source, target = tmp_path / "source", tmp_path / "target"
    source.mkdir()
    target.mkdir()
    sender, receiver = Datasets(source, "source"), Datasets(target, "target")
    prepare(sender, receiver)
    assert receiver.list() == []
    assert set(receiver.manifest([])["entries"]) == {"."}
    with pytest.raises(ValueError):
        receiver.resolve(str(receiver.session_path("transfer").relative_to(target)))


def test_delta_reuses_unchanged_files_without_copying_and_applies_deletions(tmp_path):
    source, target = tmp_path / "source", tmp_path / "target"
    source.mkdir()
    target.mkdir()
    for root in (source, target):
        (root / "same").write_bytes(bytes(range(256)) * 400)
    (source / "changed").write_bytes(b"new")
    (target / "changed").write_bytes(b"old")
    (target / "deleted").write_bytes(b"stale")
    inode = (target / "same").stat().st_ino
    sender, receiver = Datasets(source, "source"), Datasets(target, "target")
    manifest = prepare(sender, receiver)
    assert copy_files(sender, receiver, manifest) == ["changed"]
    receiver.finish("transfer")
    assert sorted(path.name for path in target.iterdir() if path != receiver.transactions) == [
        "changed",
        "same",
    ]
    assert (target / "same").stat().st_ino == inode
    assert (target / "same").read_bytes() == (source / "same").read_bytes()
    receiver.cleanup("transfer")
    assert not receiver.transactions.exists()


def test_concurrently_changed_reused_file_aborts_before_commit(tmp_path):
    source, target = tmp_path / "source", tmp_path / "target"
    source.mkdir()
    target.mkdir()
    for root in (source, target):
        (root / "same").write_bytes(b"expected")
    sender, receiver = Datasets(source, "source"), Datasets(target, "target")
    manifest = prepare(sender, receiver)
    copy_files(sender, receiver, manifest)
    (target / "same").write_bytes(b"concurrent-change")
    with pytest.raises(ValueError):
        receiver.finish("transfer")
    assert (target / "same").read_bytes() == b"concurrent-change"


def test_user_filenames_cannot_collide_with_session_metadata(tmp_path):
    source, target = tmp_path / "source", tmp_path / "target"
    (source / "payload").mkdir(parents=True)
    (source / "manifest.json").write_bytes(b"user-data")
    (source / "payload/file").write_bytes(b"nested-user-data")
    sender, receiver = Datasets(source, "source"), Datasets(target, "target")
    copy_files(sender, receiver, prepare(sender, receiver))
    receiver.finish("transfer")
    assert (target / "manifest.json").read_bytes() == b"user-data"
    assert (target / "payload/file").read_bytes() == b"nested-user-data"


def test_interrupted_commit_recovers_and_records_completion(tmp_path, monkeypatch):
    source, target = tmp_path / "source", tmp_path / "target"
    source.mkdir()
    target.mkdir()
    for name in ["first", "second"]:
        (source / name).write_bytes(b"new")
        (target / name).write_bytes(b"old")
    sender, receiver = Datasets(source, "source"), Datasets(target, "target")
    copy_files(sender, receiver, prepare(sender, receiver))
    original = os.replace
    interrupted = []

    def replace(origin, destination):
        original(origin, destination)
        if Path(origin).parent.name == "payload" and Path(destination).parent == target and not interrupted:
            interrupted.append(True)
            raise OSError("interrupted after rename")

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", replace)
        with pytest.raises(OSError):
            receiver.finish("transfer")
    recovered = Datasets(target, "target")
    assert not recovered.journal.exists()
    assert all((target / name).read_bytes() == b"new" for name in ["first", "second"])
    assert prepare(sender, recovered) == sender.manifest([])
    assert recovered.receipt_path("transfer").exists()


def test_interrupted_file_resumes_at_saved_byte_offset_after_restart(tmp_path):
    source, target = tmp_path / "source", tmp_path / "target"
    source.mkdir()
    target.mkdir()
    data = bytes(range(256)) * 100
    (source / "item").write_bytes(data)
    (target / "item").write_bytes(b"original")
    sender, receiver = Datasets(source, "source"), Datasets(target, "target")
    prepare(sender, receiver)

    def interrupted():
        yield data[:4096]
        raise ConnectionError("disconnected")

    with pytest.raises(ConnectionError):
        receiver.receive_file("transfer", "item", 0, interrupted())
    assert (target / "item").read_bytes() == b"original"
    receiver = Datasets(target, "target")
    assert receiver.prepare_file("transfer", "item")["offset"] == 4096
    receiver.receive_file("transfer", "item", 4096, [data[4096:]])
    assert receiver.verify_file("transfer", "item")
    receiver.finish("transfer")
    assert (target / "item").read_bytes() == data


def test_corrupt_payload_is_rejected_and_can_be_retransmitted(tmp_path):
    source, target = tmp_path / "source", tmp_path / "target"
    source.mkdir()
    target.mkdir()
    (source / "item").write_bytes(b"new")
    (target / "item").write_bytes(b"old")
    sender, receiver = Datasets(source, "source"), Datasets(target, "target")
    prepare(sender, receiver)
    receiver.receive_file("transfer", "item", 0, [b"bad"])
    assert not receiver.verify_file("transfer", "item")
    with pytest.raises(ValueError):
        receiver.finish("transfer")
    assert (target / "item").read_bytes() == b"old"
    assert receiver.prepare_file("transfer", "item")["offset"] == 0
    receiver.receive_file("transfer", "item", 0, [b"new"])
    assert receiver.verify_file("transfer", "item")
    receiver.finish("transfer")
    assert (target / "item").read_bytes() == b"new"
