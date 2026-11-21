import os
import stat
from types import SimpleNamespace

import pytest

from gpu_platform_contracts.module import HostContext
from gpu_platform_build_buildkit import host
from gpu_platform_build_buildkit.module import Module


def context(tmp_path, monkeypatch, *, modern=True, restricted=True):
    policy = tmp_path / "apparmor.d" / host.PROFILE_NAME
    restriction = tmp_path / "userns_restriction"
    restriction.write_text("1" if restricted else "0")
    monkeypatch.setattr(host, "PROFILE_PATH", policy)
    monkeypatch.setattr(host, "USERNS_RESTRICTION", restriction)
    monkeypatch.setattr(host.shutil, "which", lambda name: "/usr/sbin/" + name)
    calls = []

    def runner(argv, **kwargs):
        calls.append((argv, kwargs))
        if "--skip-kernel-load" in argv:
            userns = "userns," in kwargs["input"]
            return SimpleNamespace(returncode=0 if modern or not userns else 1)
        return SimpleNamespace(returncode=0)

    return HostContext("worker", {}, tmp_path / "state", {}, {}, runner, {}), calls, policy, restriction


def test_builder_installs_owned_userns_profile_and_removes_it(tmp_path, monkeypatch):
    value, calls, policy, restriction = context(tmp_path, monkeypatch)
    Module.install_host(value)
    assert policy.is_file()
    assert calls[-1][0] == ["apparmor_parser", "--replace", str(policy)]
    assert restriction.read_text() == "1"
    compose = Module.compose(value)
    assert f"apparmor={host.PROFILE_NAME}" in compose["services"]["builder"]["security_opt"]
    Module.uninstall_host(value)
    assert not policy.exists()
    assert calls[-1][0] == ["apparmor_parser", "--remove", str(policy)]


def test_older_parser_uses_supported_profile_without_changing_host_policy(tmp_path, monkeypatch):
    value, calls, policy, restriction = context(tmp_path, monkeypatch, modern=False, restricted=False)
    Module.install_host(value)
    assert policy.is_file()
    assert sum("--skip-kernel-load" in argv for argv, _ in calls) == 2
    assert restriction.read_text() == "0"


def test_restricted_host_requires_parser_support_before_installing(tmp_path, monkeypatch):
    value, calls, policy, _ = context(tmp_path, monkeypatch, modern=False)
    with pytest.raises(RuntimeError):
        Module.install_host(value)
    assert not policy.exists()
    assert not any("--replace" in argv for argv, _ in calls)


def test_preexisting_profile_is_not_replaced_or_removed(tmp_path, monkeypatch):
    value, _, policy, _ = context(tmp_path, monkeypatch)
    policy.parent.mkdir()
    policy.write_text("operator policy")
    with pytest.raises(FileExistsError):
        Module.install_host(value)
    Module.uninstall_host(value)
    assert policy.read_text() == "operator policy"


def test_rootless_builder_can_read_configuration_with_private_installer_umask(tmp_path, monkeypatch):
    value, _, _, _ = context(tmp_path, monkeypatch)
    previous = os.umask(0o077)
    try:
        Module.install(value)
    finally:
        os.umask(previous)
    settings = value.state_directory / "buildkit" / "config" / "buildkitd.toml"
    assert stat.S_IMODE(settings.stat().st_mode) == 0o644
    settings.write_text('debug = true\n')
    settings.chmod(0o600)
    Module.install(value)
    assert settings.read_text() == 'debug = true\n'
    assert stat.S_IMODE(settings.stat().st_mode) == 0o644
