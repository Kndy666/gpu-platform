from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import yaml

from gpu_platform_contracts.config import PlatformConfig
from gpu_platform_contracts.module import Module, ModuleDescriptor
from gpu_platform_installer.checks import CompatibilityError, validate
from gpu_platform_installer.compose import merge_fragment
from gpu_platform_installer.compose import render_compose
from gpu_platform_installer.config import load_config, resolve_host
from gpu_platform_installer.lifecycle import Installer
from gpu_platform_installer.order import installation_order


def implementation(slot, *, provides=None, accepts=None, requires=None, dependencies=None):
    return type(f"Example{slot.title()}", (Module,), {"descriptor": ModuleDescriptor(
        name="example", slot=slot, version="1.0", side="both",
        provides=provides or {}, accepts=accepts or {}, requires=requires or {}, dependencies=dependencies or [],
    )})


@pytest.fixture
def implementations():
    return {
        "auth": implementation("auth"),
        "runtime": implementation("runtime", provides={"snapshot": {"layer"}},
                                   accepts={"volume": {"directory"}, "image": {"archive"}, "readonly_directory": {"local"}}),
        "storage": implementation("storage", provides={"volume": {"directory"}}),
        "registry": implementation("registry", provides={"image": {"archive"}}, accepts={"snapshot": {"layer"}}),
        "transport": implementation("transport", provides={"port": {"tcp"}, "stream": {"bytes"}}),
        "entry": implementation("entry", accepts={"port": {"tcp"}}),
    }


@pytest.fixture
def config(tmp_path):
    return PlatformConfig.model_validate({
        "cluster": {"state_directory": str(tmp_path / "state")},
        "control": {"auth": {"module": "example"}},
        "defaults": {"slots": {slot: {"module": "example"} for slot in ("runtime", "storage", "registry")}},
        "hosts": {"primary": {"roles": ["control", "node"]}},
    })


@pytest.fixture
def loader(implementations):
    def load(slot, name):
        cls = implementations[slot]
        # Local transport and entry are selected automatically for a colocated node.
        if name == "local":
            return type(f"Local{slot}", (cls,), {"descriptor": cls.descriptor.model_copy(update={"name": "local"})})
        return cls
    return load


def test_overrides_merge_parameters_and_explicitly_disable_modules(tmp_path):
    base = tmp_path / "base.yaml"
    base.write_text(yaml.safe_dump({
        "defaults": {"slots": {"gpu": {"module": "example", "options": {"limit": 8}}},
                     "parameters": {"cache": {"limit": 8, "mode": "disk"}}},
        "hosts": {"unused": {"roles": ["control", "node"]}},
    }))
    path = tmp_path / "cluster.yaml"
    path.write_text(yaml.safe_dump({
        "defaults": {"parameters": {"cache": {"limit": 16}}},
        "hosts": {"primary": {"roles": ["control", "node"], "slots": {"gpu": None},
                              "parameters": {"cache": {"limit": 32}}}},
    }))
    parsed = load_config(path, base)
    resolved = resolve_host(parsed, "primary")
    assert set(parsed.hosts) == {"primary"}
    assert resolved.slots["gpu"] is None
    assert resolved.parameters["cache"] == {"limit": 32, "mode": "disk"}
    assert resolved.features.image_sync is False
    assert resolved.features.dataset_sync is False


def test_providers_install_before_consumers_with_snapshot_feedback(implementations):
    order = installation_order({slot: cls.descriptor for slot, cls in implementations.items()})
    assert order.index("storage") < order.index("runtime")
    assert order.index("registry") < order.index("runtime")
    assert order.index("transport") < order.index("entry")


def test_cycles_are_rejected():
    descriptors = {
        "runtime": implementation("runtime", dependencies=["storage"]).descriptor,
        "storage": implementation("storage", dependencies=["runtime"]).descriptor,
    }
    with pytest.raises(ValueError):
        installation_order(descriptors)


@pytest.mark.parametrize("forms", [set(), {"block"}])
def test_storage_requires_a_compatible_volume_handoff(config, loader, implementations, forms):
    implementations["storage"] = implementation("storage", provides={"volume": forms})
    with pytest.raises(CompatibilityError) as result:
        validate(config, "primary", loader=loader)
    assert any(issue.code == "handoff.required_volume_unavailable" for issue in result.value.issues)


def test_optional_provider_is_checked_when_present(config, loader, implementations):
    implementations["registry"] = implementation("registry", provides={"image": {"other"}}, accepts={"snapshot": {"layer"}})
    with pytest.raises(CompatibilityError) as result:
        validate(config, "primary", loader=loader)
    assert any(issue.code == "handoff.incompatible" for issue in result.value.issues)


def test_feature_prerequisites_and_runtime_requirements(config, loader, implementations):
    config.defaults.slots["registry"] = None
    implementations["storage"] = implementation("storage", provides={"volume": {"directory"}}, requires={"runtime": {"different"}})
    with pytest.raises(CompatibilityError) as result:
        validate(config, "primary", loader=loader)
    assert {issue.code for issue in result.value.issues} >= {
        "feature.save_environment_unavailable", "module.requirement_unavailable",
    }


def test_command_lists_replace_and_mount_lists_extend():
    result = merge_fragment({"command": ["a"], "volumes": ["a:/a"]}, {"command": ["b"], "volumes": ["b:/b", "a:/a"]})
    assert result == {"command": ["b"], "volumes": ["a:/a", "b:/b"]}


def test_single_host_always_runs_login_service_with_host_network(config, loader, tmp_path):
    modules = validate(config, "primary", loader=loader)
    services = render_compose(config, "primary", modules, tmp_path / "cluster.yaml")["services"]
    links = services["links"]
    assert links["network_mode"] == "host"
    assert links["command"][-1] == "links"
    assert links["image"] == services["web"]["image"]
    assert links["volumes"] == services["loop"]["volumes"]
    assert links["environment"]["PLATFORM_CONFIG"] == services["loop"]["environment"]["PLATFORM_CONFIG"]


@pytest.mark.parametrize("roles", [["control"], ["control", "node"], ["node"]])
def test_control_hosts_publish_the_panel_on_the_configured_port(config, tmp_path, roles):
    config.cluster.panel_image = "registry.example/panel:custom"
    config.hosts["primary"].roles = roles
    config.hosts["primary"].parameters = {"web_port": 9012}
    services = render_compose(config, "primary", {}, tmp_path / "cluster.yaml")["services"]
    if "control" not in roles:
        assert "panel" not in services and "web" not in services
        return
    panel = services["panel"]
    assert panel["image"] == config.cluster.panel_image
    assert panel["ports"] == ["9012:80"] and "ports" not in services["web"]
    assert panel["depends_on"] == ["web"] and panel["init"] and panel["restart"] == "unless-stopped"
    assert panel["healthcheck"]["test"][-1] == "http://127.0.0.1/"
    allowed_hosts = services["web"]["environment"]["DJANGO_ALLOWED_HOSTS"].split(",")
    assert {config.cluster.public_host, "localhost", "127.0.0.1", "web"} <= set(allowed_hosts)


def node_report(node_id="primary", maintenance=True, tasks=None, age=0):
    return {"node_id": node_id, "observed_at": datetime.now(timezone.utc) - timedelta(seconds=age),
            "maintenance": maintenance, "tasks": tasks or []}


@pytest.mark.parametrize("report", [
    node_report(maintenance=False), node_report(tasks=[{"id": "work", "state": "running"}]),
    node_report(tasks=[{"id": "work", "state": "queued"}]), node_report(age=120), node_report(node_id="other"),
])
def test_upgrade_rejects_busy_unmaintained_or_stale_nodes_before_side_effects(config, loader, tmp_path, report):
    calls = []
    instance = Installer(config, "primary", tmp_path / "deploy", loader=loader,
                         runner=lambda *args, **kwargs: calls.append(args), state_probe=lambda name: report)
    with pytest.raises(RuntimeError):
        instance.upgrade()
    assert not calls
    assert not instance.directory.exists()


def test_upgrade_checks_all_nodes(config, loader, tmp_path):
    config.hosts["secondary"] = config.hosts["primary"].model_copy(update={"roles": ["node"]})
    queried = []
    def probe(name):
        queried.append(name)
        return node_report(name, maintenance=name == "primary")
    instance = Installer(config, "primary", tmp_path, loader=loader, state_probe=probe)
    with pytest.raises(RuntimeError):
        instance.check_maintenance()
    assert queried == ["primary", "secondary"]


def test_installed_setup_fingerprint_survives_interpreter_restart(config, tmp_path):
    import os
    import subprocess
    import sys

    path = tmp_path / "config.json"
    path.write_text(config.model_dump_json())
    script = """
import sys
from pathlib import Path
from types import SimpleNamespace
from gpu_platform_contracts.config import PlatformConfig, ModuleSelection
from gpu_platform_contracts.module import ModuleDescriptor
from gpu_platform_installer.lifecycle import Installer

config = PlatformConfig.model_validate_json(Path(sys.argv[1]).read_text())
forms = {"image": {"archive", "directory", "reference", "layer"}}
descriptor = ModuleDescriptor(name="example", slot="runtime", version="1.0", side="both",
                              provides=forms, accepts=forms, requires=forms)
module = SimpleNamespace(descriptor=descriptor, selection=ModuleSelection(module="example"))
print(Installer(config, "primary", Path.cwd()).setup_fingerprint({"runtime": module}))
"""
    values = [subprocess.check_output([sys.executable, "-c", script, str(path)], text=True,
                                      env={**os.environ, "PYTHONHASHSEED": seed}) for seed in ("1", "2")]
    assert values[0] == values[1]


def test_upgrade_stops_services_before_schema_validation(config, loader, tmp_path):
    calls = []
    instance = Installer(config, "primary", tmp_path / "deploy", loader=loader,
                         runner=lambda args, **kwargs: calls.append(args) or SimpleNamespace(returncode=0),
                         facts={"os": "ubuntu", "version": "24.04", "architecture": "amd64"},
                         state_probe=lambda name: node_report(name))
    instance.directory.mkdir()
    instance.record_setup(validate(config, "primary", loader=loader))
    instance.upgrade()
    stop = next(index for index, call in enumerate(calls) if call[-1] == "stop")
    initialize = next(index for index, call in enumerate(calls) if "initdb" in call)
    up = next(index for index, call in enumerate(calls) if "up" in call)
    assert stop < initialize < up
    assert (instance.directory / ".env").stat().st_mode & 0o777 == 0o600


def test_offline_image_preparation_requires_every_image(config, loader, tmp_path):
    config.hosts["primary"].parameters = {"offline": True}
    calls = []
    instance = Installer(config, "primary", tmp_path, loader=loader,
                         runner=lambda args, **kwargs: calls.append(args) or SimpleNamespace(returncode=1))
    with pytest.raises(RuntimeError):
        instance.prepare_images({"compose": {"services": {"job": {"image": "example:1"}}}})
    assert all("pull" not in call for call in calls)


def test_image_archives_are_loaded_before_presence_checks(config, loader, tmp_path):
    archive = tmp_path / "images.tar"
    archive.write_bytes(b"archive-fixture")
    config.hosts["primary"].parameters = {"offline": True, "image_archives": [str(archive)]}
    calls = []
    instance = Installer(config, "primary", tmp_path, loader=loader,
                         runner=lambda args, **kwargs: calls.append(args) or SimpleNamespace(returncode=0))
    instance.prepare_images({"compose": {"services": {"job": {"image": "example:1"}}}})
    assert calls[0][:2] == ["docker", "load"]
    assert calls[1][:3] == ["docker", "image", "inspect"]


@pytest.mark.parametrize("handoff", ["port", "stream"])
def test_transport_requires_complete_handoffs_even_without_sync(config, loader, implementations, handoff):
    provides = {"port": {"tcp"}, "stream": {"bytes"}}
    provides.pop(handoff)
    implementations["transport"] = implementation("transport", provides=provides)
    with pytest.raises(CompatibilityError) as result:
        validate(config, "primary", loader=loader)
    assert any(issue.code == "handoff.transport_required" and issue.parameters["handoff"] == handoff
               for issue in result.value.issues)


def test_remote_entry_cannot_use_another_hosts_transport(config, loader, implementations):
    from gpu_platform_contracts.config import HostConfig
    config.hosts["remote"] = HostConfig.model_validate({"roles": ["node"], "slots": {
        "transport": {"module": "example"}, "entry": {"module": "example"}}})
    def scoped_loader(slot, name):
        if slot == "transport" and name == "example":
            return implementation("transport", provides={"port": {"incompatible"}, "stream": {"bytes"}})
        return loader(slot, name)
    with pytest.raises(CompatibilityError) as result:
        validate(config, "primary", loader=scoped_loader)
    assert any(issue.code == "handoff.entry_port_unavailable" and issue.parameters["module"] == "entry:remote"
               for issue in result.value.issues)


def worker_configuration(config):
    from gpu_platform_contracts.config import HostConfig
    config.hosts["worker"] = HostConfig.model_validate({"roles": ["node"], "slots": {
        "transport": {"module": "example"}, "entry": {"module": "example"}}})
    return config


def test_worker_bundle_requires_all_current_nodes_and_live_local_safety(config, loader, tmp_path):
    import json
    config = worker_configuration(config)
    control = Installer(config, "primary", tmp_path / "control", loader=loader,
                        state_probe=lambda name: node_report(name))
    report = tmp_path / "maintenance.json"
    control.export_maintenance_reports(report)
    worker = Installer(config, "worker", tmp_path / "worker", loader=loader,
                       state_probe=lambda _: pytest.fail("Workers must not probe controller-local endpoints"))
    with pytest.raises(RuntimeError):
        worker.check_maintenance()
    worker.maintenance_reports = report
    worker.local_state_probe = lambda: node_report("worker")
    assert set(worker.check_maintenance()) == {"primary", "worker"}
    worker.local_state_probe = lambda: node_report("worker", tasks=[{"id": "new", "state": "running"}])
    with pytest.raises(RuntimeError):
        worker.check_maintenance()
    worker.local_state_probe = lambda: node_report("worker")
    original = json.loads(report.read_text())
    stale = json.loads(report.read_text())
    stale["reports"]["primary"]["observed_at"] = (datetime.now(timezone.utc) - timedelta(seconds=90)).isoformat()
    report.write_text(json.dumps(stale))
    with pytest.raises(RuntimeError):
        worker.check_maintenance()
    del original["reports"]["primary"]
    report.write_text(json.dumps(original))
    with pytest.raises(RuntimeError):
        worker.check_maintenance()


def test_upgrade_rechecks_maintenance_after_image_preparation(config, loader, tmp_path):
    calls = []
    probes = 0
    def probe(name):
        nonlocal probes
        probes += 1
        return node_report(name, maintenance=probes == 1)
    instance = Installer(config, "primary", tmp_path / "deploy", loader=loader,
                         runner=lambda argv, **kwargs: calls.append(argv) or SimpleNamespace(returncode=0),
                         facts={"os": "ubuntu", "version": "24.04", "architecture": "amd64"}, state_probe=probe)
    with pytest.raises(RuntimeError):
        instance.upgrade()
    assert not any(call[-1] == "stop" for call in calls)


def test_upgrade_reapplies_changed_host_configuration_only_after_stop(config, loader, tmp_path):
    calls = []
    instance = Installer(config, "primary", tmp_path / "deploy", loader=loader,
                         runner=lambda argv, **kwargs: calls.append(argv) or SimpleNamespace(returncode=0),
                         facts={"os": "ubuntu", "version": "24.04", "architecture": "amd64"},
                         state_probe=lambda name: node_report(name))
    instance.prepare_host = lambda modules, order, facts: calls.append(["host-setup"])
    instance.upgrade()
    assert next(index for index, call in enumerate(calls) if call[-1] == "stop") < calls.index(["host-setup"])
    calls.clear()
    instance.upgrade()
    assert ["host-setup"] not in calls
    config.defaults.parameters["changed"] = True
    instance.upgrade()
    assert calls.count(["host-setup"]) == 1


def test_duplicate_configuration_values_are_rejected(tmp_path):
    from gpu_platform_installer.config import load_config

    config = tmp_path / "cluster.yaml"
    config.write_text("hosts:\n  main:\n    roles: [control, node]\ncluster:\n  public_host: first.test\n  public_host: second.test\n")
    with pytest.raises(ValueError, match="Duplicate configuration key"):
        load_config(config)


def test_bundled_defaults_keep_sqlite_with_custom_state_directory(tmp_path):
    from gpu_platform_installer.config import load_config

    config = tmp_path / "cluster.yaml"
    config.write_text("cluster:\n  state_directory: /srv/cluster\n")
    resolved = load_config(config)
    assert resolved.control.database["name"] == "control.sqlite3"
    assert resolved.defaults.parameters["network"]["private_ports"] == [6443, 10250]


def test_installation_handoffs_reach_consumers_after_providers(config, loader, implementations, tmp_path, monkeypatch):
    import gpu_platform_installer.lifecycle as lifecycle

    monkeypatch.setattr(lifecycle, "prepare_docker", lambda _: None)
    monkeypatch.setattr(lifecycle, "configure_firewall", lambda _: None)
    handoff = {"form": "custom-image", "endpoint": "registry.test", "ca_file": str(tmp_path / "ca.crt")}
    seen = []
    implementations["registry"].install = classmethod(lambda cls, context: context.installation_handoffs.setdefault("image", []).append(handoff))
    implementations["runtime"].install = classmethod(lambda cls, context: seen.extend(context.installation_handoffs["image"]))
    instance = Installer(config, "primary", tmp_path, loader=loader, runner=lambda *args, **kwargs: None)
    modules = validate(config, "primary", loader=loader)
    order = installation_order({key: module.descriptor for key, module in modules.items()})
    instance.prepare_host(modules, order, {})
    assert seen == [handoff]


def test_yaml_anchor_overrides_remain_supported():
    from gpu_platform_installer.config import parse_yaml

    value = parse_yaml("base: &base\n  enabled: true\ncopy:\n  <<: *base\n  enabled: false\n")
    assert value['base']['enabled'] is True
    assert value['copy']['enabled'] is False


def test_module_option_model_rejects_unknown_values_before_install(config, loader, implementations):
    from gpu_platform_contracts.base import Contract
    from pydantic import ValidationError

    class Options(Contract):
        count: int = 1
        endpoint: str | None = None

    implementations['storage'].options_model = Options
    config.defaults.slots['storage'].options = {'count': '3', 'endpoint': None}
    selected = validate(config, 'primary', loader=loader)
    assert selected['storage'].selection.options == {'count': 3}
    config.defaults.slots['storage'].options = {'misspelled': True}
    with pytest.raises(ValidationError):
        validate(config, 'primary', loader=loader)


def test_public_dataset_mount_is_persistent_and_feature_scoped(config, loader, tmp_path):
    path = str(tmp_path / "public")
    config.defaults.parameters["datasets"] = path
    document = Installer(config, "primary", tmp_path, loader=loader).plan()["compose"]
    assert f"{path}:{path}" in document["services"]["node"]["volumes"]
    config.defaults.features.public_datasets = False
    document = Installer(config, "primary", tmp_path, loader=loader).plan()["compose"]
    assert f"{path}:{path}" not in document["services"]["node"]["volumes"]
    config.defaults.features.public_datasets = True
    config.defaults.parameters["datasets"] = "relative/path"
    with pytest.raises(CompatibilityError) as result:
        validate(config, "primary", loader=loader)
    assert any(issue.code == 'feature.datasets_path_invalid' for issue in result.value.issues)
