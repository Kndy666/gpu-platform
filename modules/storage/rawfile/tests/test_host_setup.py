import json
from types import SimpleNamespace

import pytest

from gpu_platform_storage_rawfile.module import Module


def context(tmp_path, mount):
    calls = []

    def runner(arguments, **kwargs):
        calls.append(arguments)
        return SimpleNamespace(stdout=json.dumps({"filesystems": [mount]}))

    return SimpleNamespace(options={"pools": [{
        "name": "data", "path": str(tmp_path / "new" / "volumes"), "storage_class": "volumes",
    }]}, runner=runner), calls


def test_pool_check_uses_existing_parent_without_creating_directories(tmp_path):
    value, calls = context(tmp_path, {"target": str(tmp_path), "fstype": "ext4", "options": "rw,relatime"})
    assert all(result.ok for result in Module.check_host(value))
    assert calls[0][calls[0].index("--target") + 1] == str(tmp_path)
    assert not (tmp_path / "new").exists()


@pytest.mark.parametrize("mount,code", [
    ({"target": "/", "fstype": "ext4", "options": "rw"}, "storage.pool_not_mounted"),
    ({"target": "/data", "fstype": "tmpfs", "options": "rw"}, "storage.pool_not_mounted"),
    ({"target": "/data", "fstype": "ext4", "options": "ro"}, "storage.pool_read_only"),
])
def test_pool_check_rejects_unusable_filesystems(tmp_path, mount, code):
    value, _ = context(tmp_path, mount)
    result, = Module.check_host(value)
    assert not result.ok
    assert result.message.code == code


def test_pool_names_classes_and_paths_must_be_unique(tmp_path):
    value, _ = context(tmp_path, {"target": str(tmp_path), "fstype": "ext4", "options": "rw"})
    value.options["pools"] *= 2
    assert [result.ok for result in Module.check_host(value)] == [True, False]
