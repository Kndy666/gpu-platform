"""Install the single-node execution cluster and development environment prerequisites."""

import hashlib
import ipaddress
import json
from pathlib import Path
import re
import shutil
import sys
import tarfile
import time
from typing import Any
from urllib.parse import quote

import yaml

from gpu_platform_contracts.module import CheckResult
from gpu_platform_contracts.handoffs import RegistryTrust, GPURuntime
from gpu_platform_contracts.host import HostOwnership
from gpu_platform_contracts.texts import Message

ASSETS = Path(__file__).parent / "host"
KUBECONFIG = "/etc/rancher/k3s/k3s.yaml"


def versions():
    return dict(
        line.split("=", 1)
        for line in (ASSETS / "vendor-versions.lock").read_text().splitlines()
        if line and not line.startswith("#")
    )


def download(context, url, path, digest=None):
    path = Path(path)
    if path.exists() and (digest is None or hashlib.sha256(path.read_bytes()).hexdigest() == digest):
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".part")
    context.runner(["curl", "--fail", "--location", "--retry", "3", "--output", str(temporary), url])
    if digest and hashlib.sha256(temporary.read_bytes()).hexdigest() != digest:
        temporary.unlink()
        raise ValueError("Downloaded infrastructure checksum does not match")
    temporary.replace(path)
    return path


def binary(context, name, version, architecture, cache):
    if shutil.which(name):
        return
    if name == "helm":
        filename = f"helm-{version}-linux-{architecture}.tar.gz"
        url = f"https://get.helm.sh/{filename}"
        checksum = (
            download(context, url + ".sha256sum", cache / (filename + ".sha256sum")).read_text().split()[0]
        )
        member = f"linux-{architecture}/helm"
    else:
        filename = f"nerdctl-{version}-linux-{architecture}.tar.gz"
        url = f"https://github.com/containerd/nerdctl/releases/download/v{version}/{filename}"
        checksum = versions()[f"nerdctl_{architecture}_sha256"]
        member = "nerdctl"
    archive = download(context, url, cache / filename, checksum)
    with tarfile.open(archive) as stream:
        source = stream.extractfile(member)
        if source is None:
            raise ValueError("Infrastructure archive lacks executable")
        target = cache / name
        with target.open("wb") as output:
            shutil.copyfileobj(source, output)
    context.runner(["install", "-m", "0755", str(target), f"/usr/local/bin/{name}"])


def check_host(context):
    results = []
    controllers = Path("/sys/fs/cgroup/cgroup.controllers")
    available = set(controllers.read_text().split()) if controllers.exists() else set()
    if not {"cpu", "memory", "pids"} <= available:
        results.append(CheckResult(ok=False, message=Message(code="runtime.cgroup_controllers_required")))
    return results


def _install_host(context):
    locked = versions()
    architecture = context.host["architecture"]
    cache = context.state_directory / "cache" / "runtime"
    cache.mkdir(parents=True, exist_ok=True)
    context.runner(
        [
            "apt-get",
            "install",
            "-y",
            "curl",
            "ca-certificates",
            "apparmor",
            "apparmor-utils",
            "fuse3",
            "uidmap",
        ]
    )
    if not shutil.which("runc"):
        context.runner(["apt-get", "install", "-y", "runc"])
    if not shutil.which("k3s"):
        version = locked["k3s"]
        asset = "k3s-arm64" if architecture == "arm64" else "k3s"
        origin = f"https://github.com/k3s-io/k3s/releases/download/{quote(version, safe='')}"
        sums = download(
            context, f"{origin}/sha256sum-{architecture}.txt", cache / f"k3s-{architecture}.sha256"
        )
        digest = next(
            line.split()[0] for line in sums.read_text().splitlines() if line.split()[-1].lstrip("*") == asset
        )
        executable = download(context, f"{origin}/{asset}", cache / asset, digest)
        context.runner(["install", "-m", "0755", str(executable), "/usr/local/bin/k3s"])
        directory = Path("/etc/rancher/k3s")
        directory.mkdir(parents=True, exist_ok=True)
        network = context.options.get("network", context.host.get("parameters", {}).get("network", {}))
        config = {
            "write-kubeconfig-mode": "0600",
            "bind-address": str(context.options.get("api_bind_address") or "127.0.0.1"),
            "advertise-address": str(context.options.get("api_bind_address") or "127.0.0.1"),
            "disable": ["local-storage", "traefik", "servicelb"],
            "secrets-encryption": True,
            "cluster-cidr": network.get("pod_cidr", "10.42.0.0/16"),
            "service-cidr": network.get("service_cidr", "10.43.0.0/16"),
            "kubelet-arg": [
                f"system-reserved=cpu={context.options.get('reserve_cpu', 4)},memory={context.options.get('reserve_memory_gib', 16)}Gi"
            ],
        }
        config_path = directory / "config.yaml"
        if config_path.exists():
            raise FileExistsError(config_path)
        config_path.write_text(yaml.safe_dump(config))
        config_path.chmod(0o600)
        registry_config = directory / "registries.yaml"
        if network.get("registry_mirrors") and not registry_config.exists():
            registry_config.write_text(
                yaml.safe_dump(
                    {
                        "mirrors": {
                            name: {"endpoint": endpoints}
                            for name, endpoints in network["registry_mirrors"].items()
                        }
                    }
                )
            )
        service = Path("/etc/systemd/system/k3s.service")
        if service.exists():
            raise FileExistsError(service)
        service.write_text(
            "[Unit]\nDescription=Compute node execution cluster\nAfter=network-online.target\nWants=network-online.target\n"
            "[Service]\nType=notify\nExecStart=/usr/local/bin/k3s server\nKillMode=process\nDelegate=yes\n"
            "LimitNOFILE=1048576\nLimitNPROC=infinity\nTasksMax=infinity\nRestart=always\nRestartSec=5\n"
            "[Install]\nWantedBy=multi-user.target\n"
        )
        context.runner(["systemctl", "daemon-reload"])
        context.runner(["systemctl", "enable", "--now", "k3s"])
    wait_for_node(context)
    binary(context, "helm", locked["helm"], architecture, cache)
    binary(context, "nerdctl", locked["nerdctl"], architecture, cache)
    chart = cache / f"kueue-{locked['kueue']}.tgz"
    if not chart.exists():
        context.runner(
            [
                "helm",
                "pull",
                "oci://registry.k8s.io/kueue/charts/kueue",
                "--version",
                locked["kueue"],
                "--destination",
                str(cache),
            ]
        )
    if hashlib.sha256(chart.read_bytes()).hexdigest() != locked["kueue_chart_sha256"]:
        raise ValueError("Kueue chart checksum does not match")
    values = cache / "kueue-values.yaml"
    values.write_text(
        yaml.safe_dump(
            {
                "managerConfig": {
                    "controllerManagerConfigYaml": yaml.safe_dump(
                        {
                            "apiVersion": "config.kueue.x-k8s.io/v1beta1",
                            "kind": "Configuration",
                            "integrations": {"frameworks": ["batch/job", "pod"]},
                            "managedJobsNamespaceSelector": {"matchLabels": {"platform.queue": "enabled"}},
                        }
                    )
                }
            }
        )
    )
    install_queue(context, chart, values)


def wait_for_node(context):
    deadline = time.monotonic() + 180
    while True:
        result = context.runner(
            ["k3s", "kubectl", "wait", "--for=condition=Ready", "nodes", "--all", "--timeout=10s"],
            check=False, capture_output=True,
        )
        if result.returncode == 0:
            return
        if time.monotonic() >= deadline:
            raise RuntimeError("The execution node did not become ready: " + result.stderr)
        time.sleep(1)


def install_queue(context, chart, values):
    releases = json.loads(context.runner(
        ["helm", "--kubeconfig", KUBECONFIG, "list", "--namespace", "kueue-system", "--filter", "^kueue$", "--output", "json"],
        capture_output=True,
    ).stdout)
    if not releases:
        HostOwnership(context, "runtime-cluster").action("helm:kueue", [
            ["helm", "--kubeconfig", KUBECONFIG, "uninstall", "kueue", "--namespace", "kueue-system", "--ignore-not-found"]
        ])
    context.runner(
        [
            "helm",
            "--kubeconfig",
            KUBECONFIG,
            "upgrade",
            "--install",
            "kueue",
            str(chart),
            "--namespace",
            "kueue-system",
            "--create-namespace",
            "--values",
            str(values),
            "--wait",
            "--timeout",
            "5m",
        ]
    )


def host_files():
    return [Path(path) for path in (
        "/usr/local/bin/k3s", "/usr/local/bin/helm", "/usr/local/bin/nerdctl",
        "/etc/rancher/k3s/config.yaml",
        "/etc/systemd/system/k3s.service",
        "/etc/systemd/system/lxcfs.service.d/gpu-platform.conf",
        "/etc/rancher/k3s/platform-workspace-oci.json",
        "/var/lib/rancher/k3s/agent/etc/containerd/config-v3.toml.tmpl",
        "/var/lib/kubelet/seccomp/gpu-platform-workspace.json",
        "/etc/apparmor.d/gpu-platform-workspace",
    )]


def tracked_host_files(context):
    commands = {
        "/etc/systemd/system/k3s.service": [["systemctl", "disable", "--now", "k3s"]],
        **{f"/etc/apparmor.d/{name}": [["apparmor_parser", "-R", f"/etc/apparmor.d/{name}"]]
           for name in ("gpu-platform-workspace",)},
    }
    return HostOwnership(context, "runtime-host").files(host_files(), before_remove=commands)


def install_host(context):
    registry_config = HostOwnership(context, "runtime-registry-trust")
    with tracked_host_files(context), registry_config.configuration(
        Path("/etc/rancher/k3s/registries.yaml"), load=yaml.safe_load, format="yaml"
    ):
        _install_host(context)


def own_object(context, kind, name, namespace=None):
    arguments = [kind, name, *(["--namespace", namespace] if namespace else [])]
    result = context.runner(["k3s", "kubectl", "get", *arguments, "--ignore-not-found", "-o", "name"],
                            capture_output=True)
    if not result.stdout.strip():
        HostOwnership(context, "runtime-cluster").action(
            ":".join([kind, namespace or "", name]),
            [["k3s", "kubectl", "delete", *arguments, "--ignore-not-found", "--wait=true", "--timeout=60s"]],
        )


def workspace_policy(namespace, node, network):
    blocked = ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10", "127.0.0.0/8",
               "169.254.0.0/16"]
    for key in ("pod_cidr", "service_cidr"):
        blocked.extend(filter(None, (network.get(key) or "").split(",")))
    blocked.extend(address["address"] for address in node["status"].get("addresses", [])
                   if address["type"] in {"InternalIP", "ExternalIP"})
    networks = [ipaddress.ip_network(value.strip(), strict=False) for value in blocked]
    exceptions = [str(item) for item in ipaddress.collapse_addresses(item for item in networks if item.version == 4)]
    dns = {"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "kube-system"}},
           "podSelector": {"matchLabels": {"k8s-app": "kube-dns"}}}
    return {
        "apiVersion": "networking.k8s.io/v1", "kind": "NetworkPolicy",
        "metadata": {"name": "workspace-isolation", "namespace": namespace},
        "spec": {
            "podSelector": {}, "policyTypes": ["Ingress", "Egress"],
            "ingress": [{"ports": [{"port": 2222, "protocol": "TCP"}]}],
            "egress": [{"to": [dns], "ports": [{"port": 53, "protocol": protocol} for protocol in ("TCP", "UDP")]},
                       {"to": [{"ipBlock": {"cidr": "0.0.0.0/0", "except": exceptions}}]}],
        },
    }


def queue_objects(namespace, queue, node, network=None):
    allocatable = node["status"]["allocatable"]
    resources = [name for name in ("cpu", "memory", "ephemeral-storage") if name in allocatable]
    return [
        {
            "apiVersion": "v1",
            "kind": "Namespace",
            "metadata": {"name": namespace, "labels": {"platform.queue": "enabled"}},
        },
        {
            "apiVersion": "v1",
            "kind": "ServiceAccount",
            "metadata": {"name": "workspace", "namespace": namespace},
            "automountServiceAccountToken": False,
        },
        workspace_policy(namespace, node, network or {}),
        {
            "apiVersion": "kueue.x-k8s.io/v1beta1",
            "kind": "ResourceFlavor",
            "metadata": {"name": "platform-node"},
            "spec": {
                "nodeLabels": {"kubernetes.io/hostname": node["metadata"]["labels"]["kubernetes.io/hostname"]}
            },
        },
        {
            "apiVersion": "kueue.x-k8s.io/v1beta1",
            "kind": "ClusterQueue",
            "metadata": {"name": queue},
            "spec": {
                "namespaceSelector": {"matchLabels": {"platform.queue": "enabled"}},
                "queueingStrategy": "BestEffortFIFO",
                "resourceGroups": [
                    {
                        "coveredResources": resources,
                        "flavors": [
                            {
                                "name": "platform-node",
                                "resources": [
                                    {"name": name, "nominalQuota": allocatable[name]} for name in resources
                                ],
                            }
                        ],
                    }
                ],
            },
        },
        {
            "apiVersion": "kueue.x-k8s.io/v1beta1",
            "kind": "LocalQueue",
            "metadata": {"name": queue, "namespace": namespace},
            "spec": {"clusterQueue": queue},
        },
        *[
            {
                "apiVersion": "kueue.x-k8s.io/v1beta1",
                "kind": "WorkloadPriorityClass",
                "metadata": {"name": name},
                "value": priority,
            }
            for name, priority in (("platform-normal", 0), ("platform-high", 100))
        ],
    ]


def registry_trust(context):
    trusted = {}
    for value in getattr(context, "installation_handoffs", {}).get("image", []):
        if value.get("form") != "oci-registry":
            continue
        handoff = RegistryTrust.model_validate(value)
        if handoff.node_id != context.node_id or handoff.version.split(".")[0] != "1":
            raise ValueError("runtime.registry_trust_scope_mismatch")
        trusted[handoff.endpoint] = handoff.ca_file
    trusted.update(context.options.get("registry_trust", {}))
    result = {}
    for endpoint, certificate in trusted.items():
        path = Path(certificate)
        if not path.is_absolute():
            path = context.state_directory / path
        if not path.is_file():
            raise FileNotFoundError(path)
        result[endpoint] = path
    return result


def _install(context):
    bootstrap = context.state_directory / context.options.get("bootstrap_directory", "bootstrap/environment")
    bootstrap.mkdir(parents=True, exist_ok=True)
    shutil.copytree(Path(__file__).parent / "images", bootstrap, dirs_exist_ok=True)
    if not context.host.get("slots", {}).get("registry") and not context.host.get("slots", {}).get("build"):
        reference = context.options.get("default_image", "gpu-platform/environment:0.4.0")
        context.runner(["docker", "build", "--tag", reference, str(bootstrap)])
        image = json.loads(
            context.runner(["docker", "image", "inspect", reference], capture_output=True).stdout
        )[0]
        digest = image["Id"]
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
            raise ValueError("The local environment image requires a valid content digest")
        archive = context.state_directory / "bootstrap" / "environment.tar"
        try:
            context.runner(["docker", "image", "save", "--output", str(archive), reference])
            context.runner(["k3s", "ctr", "--namespace", "k8s.io", "images", "import", str(archive)])
        finally:
            archive.unlink(missing_ok=True)
        catalog = context.state_directory / "bootstrap" / "catalog.json"
        catalog.parent.mkdir(parents=True, exist_ok=True)
        catalog.write_text(
            json.dumps(
                [
                    {
                        "id": "standard-environment",
                        "reference": reference,
                        "digest": digest,
                        "size_bytes": image["Size"],
                        "form": "node-local",
                        "metadata": {"standard_environment": True},
                    }
                ]
            )
        )
    namespace = context.options.get("namespace", "workloads")
    nodes = json.loads(
        context.runner(["k3s", "kubectl", "get", "nodes", "-o", "json"], capture_output=True).stdout
    )["items"]
    if len(nodes) != 1:
        raise ValueError("The execution cluster requires one node")
    network = context.options.get("network") or context.host.get("parameters", {}).get("network", {})
    for obj in queue_objects(namespace, context.options.get("queue", "platform"), nodes[0], network):
        if obj["kind"] != "Namespace":
            own_object(context, obj["kind"], obj["metadata"]["name"], obj["metadata"].get("namespace"))
        context.runner(["k3s", "kubectl", "apply", "-f", "-"], input=json.dumps(obj))
    classes = {"cpu": context.options.get("runtime_class", "workspace")}
    gpu = context.host["slots"].get("gpu")
    if gpu:
        classes["gpu"] = context.options.get("gpu_runtime_class", "workspace-gpu")
    for name in classes.values():
        own_object(context, "RuntimeClass", name)
    cfg: dict[str, Any] = {
        "platform": {
            "architecture": context.host["architecture"],
            "workload_namespace": namespace,
            "runtime_classes": classes,
        }
    }
    if gpu:
        providers = context.installation_handoffs.get("gpu_allocation", [])
        if len(providers) != 1:
            raise ValueError("A GPU module must supply exactly one OCI runtime")
        provider = GPURuntime.model_validate(providers[0])
        if provider.node_id != context.node_id or provider.version.split(".")[0] != "1":
            raise ValueError("GPU runtime handoff does not match the host")
        cfg["gpu_binary"] = provider.binary
        if provider.prestart_hook:
            cfg["gpu_hook"] = provider.prestart_hook
    config_path = context.state_directory / "runtime-install.yaml"
    config_path.write_text(yaml.safe_dump(cfg))
    trust = registry_trust(context)
    if trust:
        registries_path = Path("/etc/rancher/k3s/registries.yaml")
        registries = yaml.safe_load(registries_path.read_text()) if registries_path.exists() else {}
        registries = registries or {}
        for address, certificate in trust.items():
            certificate_path = Path(certificate)
            if not certificate_path.is_file():
                raise FileNotFoundError(certificate_path)
            registries.setdefault("configs", {}).setdefault(address, {})["tls"] = {
                "ca_file": str(certificate_path)
            }
            registries.setdefault("mirrors", {})[address] = {"endpoint": [f"https://{address}"]}
        registries_path.write_text(yaml.safe_dump(registries))
        context.runner(["systemctl", "restart", "k3s"])
        context.runner(
            ["k3s", "kubectl", "wait", "--for=condition=Ready", "nodes", "--all", "--timeout=180s"]
        )
    service = context.runner(["systemctl", "show", "lxcfs", "--property=LoadState", "--value"],
                             check=False, capture_output=True)
    if service.stdout.strip() == "not-found":
        HostOwnership(context, "runtime-services").action("service:lxcfs", [
            ["systemctl", "disable", "--now", "lxcfs"]
        ])
    context.runner([sys.executable, str(ASSETS / "resource-view.py"), "--config", str(config_path)])
    context.runner([sys.executable, str(ASSETS / "workspace-runtime.py"), "--config", str(config_path)])


def install(context):
    registry_config = HostOwnership(context, "runtime-registry-trust")
    bootstrap = context.state_directory / context.options.get("bootstrap_directory", "bootstrap/environment")
    source = Path(__file__).parent / "images"
    generated = [bootstrap / path.relative_to(source) for path in source.rglob("*") if path.is_file()]
    generated.extend([context.state_directory / "runtime-install.yaml",
                      context.state_directory / "bootstrap" / "catalog.json"])
    with tracked_host_files(context), registry_config.configuration(
        Path("/etc/rancher/k3s/registries.yaml"), load=yaml.safe_load, format="yaml"
    ), HostOwnership(context, "runtime-bootstrap").files(generated):
        _install(context)


def uninstall(context):
    HostOwnership(context, "runtime-cluster").cleanup()


def uninstall_host(context):
    HostOwnership(context, "runtime-services").cleanup()
    trust_changed = HostOwnership(context, "runtime-registry-trust").cleanup(
        formats={"yaml": (yaml.safe_load, yaml.safe_dump)}
    )
    files_changed = HostOwnership(context, "runtime-host").cleanup()
    HostOwnership(context, "runtime-bootstrap").cleanup()
    context.runner(["systemctl", "daemon-reload"])
    if files_changed or trust_changed:
        for service in ("k3s", "lxcfs"):
            active = context.runner(["systemctl", "is-active", "--quiet", service], check=False)
            if active.returncode == 0:
                context.runner(["systemctl", "restart", service])


def compose(context):
    return {
        "services": {
            "node": {
                "volumes": [
                    "/usr/local/bin/k3s:/usr/local/bin/k3s:ro",
                    "/usr/local/bin/nerdctl:/usr/local/bin/nerdctl:ro",
                    "/etc/rancher/k3s:/etc/rancher/k3s:ro",
                    "/var/lib/rancher/k3s:/var/lib/rancher/k3s",
                    "/run/k3s:/run/k3s",
                    "/var/lib/lxcfs:/var/lib/lxcfs:ro",
                    "/var/lib/kubelet:/var/lib/kubelet",
                ],
                "environment": {"KUBECONFIG": "/etc/rancher/k3s/k3s.yaml"},
            }
        }
    }
