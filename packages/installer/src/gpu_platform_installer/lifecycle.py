"""Installation actions; host effects pass through an injected command runner."""
from __future__ import annotations

import json
import hashlib
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import yaml

from gpu_platform_contracts.config import PlatformConfig
from gpu_platform_contracts.messages import ACTIVE_STATES, Command, NodeReport, Operation
from gpu_platform_contracts.module import ModuleContext
from gpu_platform_contracts.host import HostOwnership

from .checks import module_context, validate
from .compose import render_compose
from .config import effective_config, resolve_host
from .host.system import HostContext, configure_firewall, inspect_host, prepare_docker, remove_firewall, run
from .modules import discover
from .order import installation_order


class Installer:
    def __init__(self, config: PlatformConfig, host: str, directory: Path, *,
                 loader: Callable[[str, str], Any] = discover, runner: Callable[..., Any] = run,
                 facts: dict[str, Any] | None = None, state_probe: Callable[[str], dict] | None = None):
        self.config, self.host, self.directory = config, host, directory.resolve()
        self.loader, self.runner = loader, runner
        self.facts = facts
        self.state_probe = state_probe or self.probe
        self.maintenance_reports: Path | None = None
        self.local_state_probe: Callable[[], dict] = self.probe_local

    def plan(self, *, machine_checks: bool = False) -> dict[str, Any]:
        modules = validate(self.config, self.host, loader=self.loader, facts=self.facts,
                           runner=self.runner, machine_checks=machine_checks)
        order = installation_order({key: item.descriptor for key, item in modules.items()})
        return {
            "host": self.host,
            "modules": order,
            "uninstall_modules": list(reversed(order)),
            "compose": render_compose(self.config, self.host, modules, self.directory / "platform.yaml"),
        }

    def write_configuration(self, plan: dict[str, Any]) -> Path:
        self.directory.mkdir(parents=True, exist_ok=True)
        os.chmod(self.directory, 0o750)
        if os.geteuid() == 0:
            os.chown(self.directory, 0, 10001)
        for name, value in (("platform.yaml", effective_config(self.config)), ("compose.yaml", plan["compose"])):
            path = self.directory / name
            temporary = path.with_suffix(".tmp")
            temporary.write_text(yaml.safe_dump(value, sort_keys=False))
            os.chmod(temporary, 0o640)
            if os.geteuid() == 0:
                os.chown(temporary, 0, 10001)
            temporary.replace(path)
        if "control" in resolve_host(self.config, self.host).roles:
            canonical = Path(self.config.cluster.state_directory) / "cluster.yaml"
            canonical.parent.mkdir(parents=True, exist_ok=True)
            temporary = canonical.with_suffix(".tmp")
            temporary.write_text(yaml.safe_dump(effective_config(self.config), sort_keys=False))
            temporary.chmod(0o640)
            if os.geteuid() == 0:
                os.chown(temporary, 10001, 10001)
            temporary.replace(canonical)
        secret_path = self.directory / ".env"
        if not secret_path.exists():
            with secret_path.open("x") as stream:
                stream.write(f"DJANGO_SECRET_KEY={secrets.token_urlsafe(64)}\n")
                stream.write(f"PLATFORM_INITIAL_ADMIN_PASSWORD={secrets.token_urlsafe(24)}\n")
            os.chmod(secret_path, 0o600)
        return self.directory / "compose.yaml"

    def compose(self, *arguments: str, **kwargs: Any) -> Any:
        return self.runner(["docker", "compose", "--project-directory", str(self.directory),
                            "-f", str(self.directory / "compose.yaml"), *arguments], **kwargs)

    def prepare_images(self, plan: dict[str, Any], *, upgrade: bool = False) -> None:
        parameters = resolve_host(self.config, self.host).parameters
        archives = parameters.get("image_archives", [])
        for archive in archives:
            path = Path(archive).expanduser().resolve()
            if not path.is_file():
                raise FileNotFoundError(path)
            self.runner(["docker", "load", "--input", str(path)])
        offline = parameters.get("offline", False)
        images = {service["image"] for service in plan["compose"]["services"].values() if service.get("image")}
        for image in sorted(images):
            result = self.runner(["docker", "image", "inspect", image], check=False, capture_output=True)
            if result.returncode and offline:
                raise RuntimeError(f"Offline image is missing: {image}")
            if (result.returncode or (upgrade and not archives)) and not offline:
                self.runner(["docker", "pull", image])

    def setup_fingerprint(self, modules: dict) -> str:
        descriptors = {}
        for key, module in modules.items():
            descriptor = module.descriptor.model_dump(mode="json")
            for field in ("provides", "accepts", "requires"):
                descriptor[field] = {kind: sorted(forms) for kind, forms in descriptor[field].items()}
            descriptors[key] = {"descriptor": descriptor, "selection": module.selection.model_dump(mode="json")}
        payload = {"host": resolve_host(self.config, self.host).model_dump(mode="json"),
                   "cluster": self.config.cluster.model_dump(mode="json"), "modules": descriptors}
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

    def record_setup(self, modules: dict) -> None:
        path = self.directory / "installation.json"
        path.write_text(json.dumps({"host_setup": self.setup_fingerprint(modules)}))
        path.chmod(0o600)

    def prepare_host(self, modules: dict, order: list[str], facts: dict) -> None:
        host = resolve_host(self.config, self.host)
        context = HostContext(self.host, {}, Path(self.config.cluster.state_directory),
                              effective_config(self.config), host.model_dump(mode="json"), self.runner, facts)
        prepare_docker(context)
        configure_firewall(context)
        state = Path(self.config.cluster.state_directory)
        if not state.exists():
            self.runner(["install", "-d", "-o", "10001", "-g", "10001", "-m", "0750", str(state)])
        if "node" in host.roles and host.features.public_datasets:
            datasets = Path(host.parameters.get("datasets", "/data/datasets"))
            if not datasets.exists():
                self.runner(["install", "-d", "-m", "0755", str(datasets)])
        installation_handoffs: dict[str, list[dict[str, Any]]] = {}
        for phase in ("install_host", "install"):
            for key in order:
                module = modules[key]
                getattr(module.implementation, phase)(module_context(
                    self.config, self.host, module, self.runner, facts, installation_handoffs))

    def install(self) -> dict[str, Any]:
        facts = self.facts if self.facts is not None else inspect_host()
        modules = validate(self.config, self.host, loader=self.loader, facts=facts,
                           runner=self.runner, machine_checks=True)
        plan = self.plan()
        host = resolve_host(self.config, self.host)
        state = Path(self.config.cluster.state_directory)
        self.prepare_host(modules, plan["modules"], facts)
        self.write_configuration(plan)
        self.compose("config", "--quiet")
        self.prepare_images(plan)
        if "control" in host.roles:
            self.compose("run", "--rm", "web", "python", "-m", "gpu_platform_control.manage", "initdb")
            self.compose("run", "--rm", "web", "python", "-m", "gpu_platform_control.manage", "bootstrap")
        self.compose("up", "-d", "--wait")
        self.record_setup(modules)
        if "control" in host.roles and "node" in host.roles and host.slots.get("build") and host.slots.get("registry"):
            recipe = host.parameters.get("bootstrap_recipe")
            if recipe:
                recipe_path = Path(recipe)
                if not recipe_path.is_absolute():
                    recipe_path = state / recipe_path
                self.compose("run", "--rm", "web", "python", "-m", "gpu_platform_control.manage", "bootstrap_workspace",
                             "--node-name", self.host, "--recipe", str(recipe_path),
                             "--context", host.parameters.get("bootstrap_context", "trusted:environment"))
        return plan

    def probe(self, name: str) -> dict[str, Any]:
        host = resolve_host(self.config, name)
        selection = host.slots["transport"]
        if selection is None:
            raise ValueError(f"Node transport is not configured: {name}")
        implementation = self.loader("transport", selection.module)
        options = dict(selection.options)
        if selection.module == "local":
            options.setdefault("socket_path", str(Path(self.config.cluster.state_directory) / "node.sock"))
        transport = implementation(ModuleContext(node_id=name, options=options,
                                                  state_directory=Path(self.config.cluster.state_directory)))
        reply = transport.request(host.address, Command(operation=Operation.STATUS))
        if not reply.ok:
            raise RuntimeError(f"Node status query failed: {name}")
        return reply.data

    def probe_local(self) -> dict[str, Any]:
        implementation = self.loader("transport", "local")
        transport = implementation(ModuleContext(node_id=self.host,
            options={"socket_path": str(Path(self.config.cluster.state_directory) / "node.sock")},
            state_directory=Path(self.config.cluster.state_directory)))
        reply = transport.request("local", Command(operation=Operation.STATUS))
        if not reply.ok:
            raise RuntimeError(f"Local node status query failed: {self.host}")
        return reply.data

    def config_digest(self) -> str:
        return hashlib.sha256(json.dumps(effective_config(self.config), sort_keys=True).encode()).hexdigest()

    def validate_report(self, name: str, data: dict) -> NodeReport:
        report = NodeReport.model_validate(data)
        age = (datetime.now(timezone.utc) - report.observed_at).total_seconds()
        if report.node_id != name or not -5 <= age <= 60:
            raise RuntimeError(f"A current node report is required: {name}")
        if not report.maintenance:
            raise RuntimeError(f"Node must be in maintenance mode: {name}")
        if any(task.state in ACTIVE_STATES for task in report.tasks):
            raise RuntimeError(f"Node still has active tasks: {name}")
        return report

    def check_maintenance(self) -> dict[str, dict]:
        reports = {}
        if self.maintenance_reports is not None:
            bundle = json.loads(self.maintenance_reports.read_text())
            expected = {name for name, host in self.config.hosts.items() if "node" in host.roles}
            if bundle.get("configuration") != self.config_digest() or set(bundle.get("reports", {})) != expected:
                raise RuntimeError("Maintenance reports do not match the configured cluster")
            reports = bundle["reports"]
        elif "control" not in self.config.hosts[self.host].roles:
            raise RuntimeError("A worker upgrade requires fresh maintenance reports exported on the control host")
        for name, host in self.config.hosts.items():
            if "node" not in host.roles:
                continue
            data = reports[name] if self.maintenance_reports is not None else self.state_probe(name)
            reports[name] = self.validate_report(name, data).model_dump(mode="json")
        if "node" in self.config.hosts[self.host].roles and "control" not in self.config.hosts[self.host].roles:
            self.validate_report(self.host, self.local_state_probe())
        return reports

    def export_maintenance_reports(self, path: Path) -> dict:
        if "control" not in self.config.hosts[self.host].roles:
            raise RuntimeError("Maintenance reports must be exported on the control host")
        reports = self.check_maintenance()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps({"version": 1, "configuration": self.config_digest(), "reports": reports}))
        temporary.chmod(0o600)
        temporary.replace(path)
        return {"path": str(path), "nodes": list(reports), "maximum_age_seconds": 60}

    def upgrade(self) -> dict[str, Any]:
        self.check_maintenance()
        plan = self.plan(machine_checks=True)
        modules = validate(self.config, self.host, loader=self.loader)
        record = self.directory / "installation.json"
        setup_needed = not record.exists() or json.loads(record.read_text()).get("host_setup") != self.setup_fingerprint(modules)
        self.write_configuration(plan)
        self.compose("config", "--quiet")
        self.prepare_images(plan, upgrade=True)
        # Refresh the admission gate after image downloads and before stopping services.
        self.check_maintenance()
        self.compose("stop")
        if setup_needed:
            facts = self.facts if self.facts is not None else inspect_host()
            self.prepare_host(modules, plan["modules"], facts)
        if "control" in resolve_host(self.config, self.host).roles:
            self.compose("run", "--rm", "web", "python", "-m", "gpu_platform_control.manage", "initdb")
        self.compose("up", "-d", "--wait", "--remove-orphans")
        self.record_setup(modules)
        return plan

    def uninstall(self) -> None:
        self.check_maintenance()
        modules = validate(self.config, self.host, loader=self.loader)
        order = installation_order({key: item.descriptor for key, item in modules.items()})
        self.compose("down", "--remove-orphans")
        for key in reversed(order):
            module = modules[key]
            module.implementation.uninstall(module_context(self.config, self.host, module, self.runner, self.facts))
        for key in reversed(order):
            module = modules[key]
            module.implementation.uninstall_host(module_context(self.config, self.host, module, self.runner, self.facts))
        host = resolve_host(self.config, self.host)
        context = HostContext(self.host, {}, Path(self.config.cluster.state_directory),
            effective_config(self.config), host.model_dump(mode="json"), self.runner, self.facts or {})
        if HostOwnership(context, "docker-builder-cache").cleanup():
            self.runner(["systemctl", "daemon-reload"])
        remove_firewall(context)
        (self.directory / "uninstalled.json").write_text(json.dumps({"host": self.host, "data_preserved": True}))
