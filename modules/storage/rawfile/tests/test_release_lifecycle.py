import hashlib
import json
import subprocess
from types import SimpleNamespace

import pytest
import yaml

from gpu_platform_storage_rawfile import install as installation


@pytest.fixture
def release(tmp_path, monkeypatch):
    state = tmp_path / "state" / "installer" / "rawfile"
    state.mkdir(parents=True)
    chart = b"portable-chart-fixture"
    (state / "rawfile.tgz").write_bytes(chart)
    monkeypatch.setattr(installation, "CHART_SHA256", hashlib.sha256(chart).hexdigest())
    renderer = tmp_path / "module" / "host" / "rawfile_permissions.py"
    renderer.parent.mkdir(parents=True)
    renderer.write_text("#!/usr/bin/env python3\n")
    monkeypatch.setattr(installation, "__file__", str(renderer.parent.parent / "install.py"))
    pool = tmp_path / "pool"
    pool.mkdir()
    payload = pool / "user-data"
    payload.write_bytes(b"durable-volume-payload")
    observed = SimpleNamespace(present=False, fail_list=False, fail_install=False, fail_uninstall=False)
    calls = []

    def runner(arguments, **kwargs):
        calls.append((arguments, kwargs))
        if arguments[:2] == ["helm", "list"]:
            if observed.fail_list:
                raise subprocess.CalledProcessError(1, arguments)
            return SimpleNamespace(stdout=json.dumps([{"name": "rawfile"}] if observed.present else []))
        if arguments[:2] == ["helm", "upgrade"]:
            observed.present = True
            if observed.fail_install:
                raise subprocess.CalledProcessError(1, arguments)
        if arguments[:2] == ["helm", "uninstall"]:
            if observed.fail_uninstall:
                raise subprocess.CalledProcessError(1, arguments)
            observed.present = False
        return SimpleNamespace(stdout="", returncode=0)

    context = SimpleNamespace(state_directory=tmp_path / "state", runner=runner, options={"pools": [
        {"name": "disk", "path": str(pool), "storage_class": "volumes", "buffer_percent": 5.0}
    ]})
    return context, observed, calls, state, payload


def test_preexisting_release_survives_repeated_install_and_uninstall(release):
    context, observed, calls, state, payload = release
    observed.present = True
    installation.install(context)
    installation.install(context)
    installation.uninstall(context)
    assert observed.present
    assert not any(args[:2] == ["helm", "uninstall"] for args, _ in calls)
    assert sum(args[:2] == ["helm", "list"] for args, _ in calls) == 1
    assert payload.read_bytes() == b"durable-volume-payload"


def test_created_release_is_removed_once_and_pool_data_remains(release):
    context, observed, calls, state, payload = release
    installation.install(context)
    installation.install(context)
    installation.uninstall(context)
    installation.uninstall(context)
    assert not observed.present
    assert sum(args[:2] == ["helm", "uninstall"] for args, _ in calls) == 1
    assert not (state / "release.json").exists()
    assert payload.read_bytes() == b"durable-volume-payload"


def test_helm_targets_execution_cluster_independently_of_user_environment(release, monkeypatch):
    context, _, calls, _, _ = release
    monkeypatch.setenv("KUBECONFIG", "/unrelated/config")
    installation.install(context)
    installation.uninstall(context)
    commands = [args for args, _ in calls if args[0] == "helm"]
    assert {args[1] for args in commands} == {"list", "upgrade", "uninstall"}
    assert all(args[args.index("--kubeconfig") + 1] == "/etc/rancher/k3s/k3s.yaml" for args in commands)


def test_failed_release_deletion_can_be_retried(release):
    context, observed, calls, state, payload = release
    installation.install(context)
    observed.fail_uninstall = True
    with pytest.raises(subprocess.CalledProcessError):
        installation.uninstall(context)
    assert (state / "release.json").is_file()
    observed.fail_uninstall = False
    installation.uninstall(context)
    assert not observed.present
    assert not (state / "release.json").exists()
    assert payload.is_file()


def test_interrupted_install_keeps_ownership_for_cleanup(release):
    context, observed, calls, state, payload = release
    observed.fail_install = True
    with pytest.raises(subprocess.CalledProcessError):
        installation.install(context)
    assert observed.present
    installation.uninstall(context)
    assert not observed.present
    assert payload.is_file()


def test_untracked_release_is_not_removed(release):
    context, observed, calls, state, payload = release
    observed.present = True
    installation.uninstall(context)
    assert observed.present
    assert calls == []


def test_failed_release_inspection_does_not_install_or_claim_ownership(release):
    context, observed, calls, state, payload = release
    observed.fail_list = True
    with pytest.raises(subprocess.CalledProcessError):
        installation.install(context)
    assert not any(args[:2] == ["helm", "upgrade"] for args, _ in calls)
    assert not (state / "release.json").exists()
    assert calls[0][1]["check"] is True


@pytest.mark.parametrize("buffer_percent,expected", [(5.0, "5%"), (2.5, "2.5%")])
def test_pool_reserved_capacity_uses_valid_percentage_values(release, buffer_percent, expected):
    context, observed, calls, state, payload = release
    context.options["pools"][0]["buffer_percent"] = buffer_percent
    installation.install(context)
    values = yaml.safe_load((state / "values.yaml").read_text())
    assert values["node"]["storagePools"]["disk"]["reservedCapacity"] == expected
