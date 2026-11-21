import json
from types import SimpleNamespace

import pytest

from gpu_platform_contracts.host import HostOwnership


def context(tmp_path, runner=lambda *a, **k: None):
    return SimpleNamespace(state_directory=tmp_path / "state", runner=runner)


def test_created_files_removed_existing_and_operator_changed_files_preserved(tmp_path):
    existing, created, modified = (tmp_path / name for name in ("existing", "created", "modified"))
    existing.write_bytes(b"operator-owned")
    ctx = context(tmp_path)
    with HostOwnership(ctx, "module").files([existing, created, modified]):
        created.write_bytes(b"generated")
        modified.write_bytes(b"generated")
    with HostOwnership(ctx, "module").files([created]):
        created.write_bytes(b"upgraded")
    modified.write_bytes(b"operator-change")
    HostOwnership(ctx, "module").cleanup()
    assert existing.read_bytes() == b"operator-owned"
    assert modified.read_bytes() == b"operator-change"
    assert not created.exists()


def test_configuration_restores_only_owned_entries_across_upgrade(tmp_path):
    path = tmp_path / "daemon.json"
    path.write_text(json.dumps({"log-driver": "local", "runtimes": {"other": {"path": "other"}}}))
    ctx = context(tmp_path)
    with HostOwnership(ctx, "gpu").configuration(path):
        data = json.loads(path.read_text())
        data["runtimes"]["nvidia"] = {"path": "nvidia-v1"}
        path.write_text(json.dumps(data))
    with HostOwnership(ctx, "gpu").configuration(path):
        data["runtimes"]["nvidia"]["path"] = "nvidia-v2"
        path.write_text(json.dumps(data))
    data["runtimes"]["new"] = {"path": "new"}
    data["log-driver"] = "json-file"
    path.write_text(json.dumps(data))
    HostOwnership(ctx, "gpu").cleanup()
    assert json.loads(path.read_text()) == {
        "log-driver": "json-file", "runtimes": {"other": {"path": "other"}, "new": {"path": "new"}}
    }


def test_new_configuration_file_removed_after_only_owned_entries_removed(tmp_path):
    path = tmp_path / "new.json"
    ctx = context(tmp_path)
    with HostOwnership(ctx, "module").configuration(path):
        path.write_text(json.dumps({"runtimes": {"nvidia": {"path": "generated"}}}))
    HostOwnership(ctx, "module").cleanup()
    assert not path.exists()


def test_failed_teardown_retries_remaining_actions_in_reverse_order(tmp_path):
    calls = []
    fail = True

    def runner(command):
        calls.append(command[0])
        if fail and command[0] == "second":
            raise RuntimeError("injected")

    ctx = context(tmp_path, runner)
    journal = HostOwnership(ctx, "module")
    for name in ("first", "second", "third"):
        journal.action(name, [[name]])
    with pytest.raises(RuntimeError):
        journal.cleanup()
    assert calls == ["third", "second"]
    fail = False
    HostOwnership(ctx, "module").cleanup()
    assert calls == ["third", "second", "second", "first"]
