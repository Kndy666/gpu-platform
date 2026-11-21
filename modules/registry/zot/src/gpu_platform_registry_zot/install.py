import ipaddress
import json
from urllib.parse import urlsplit

from gpu_platform_contracts.handoffs import RegistryTrust
from gpu_platform_contracts.module import CheckResult
from gpu_platform_contracts.texts import Message

from .accounts import Accounts
from .options import Options
from .pools import POOL_MOUNTS, existing_pool


def registry_url(options):
    return options.get("url", f"https://127.0.0.1:{options.get('port', 5000)}")


def build_mirrors(options):
    return options.get("build_mirrors", [f"registry:{options.get('port', 5000)}"])


def device(path):
    while not path.exists():
        path = path.parent
    return path.stat().st_dev


def validate_pool_devices(root, options):
    devices = {device(root)}
    for pool in options.pools:
        disk = device(pool.path)
        if disk in devices:
            raise ValueError(f"Image pool {pool.name} requires its own mounted data filesystem: {pool.path}")
        devices.add(disk)


def check_host(context):
    try:
        validate_storage(context.state_directory / "registry", Options.model_validate(context.options))
    except (OSError, ValueError) as error:
        return [CheckResult(ok=False, message=Message(code="registry.image_pool_unavailable", parameters={"detail": str(error)}))]
    return []


def validate_storage(root, options):
    validate_pool_devices(root, options)
    configured = {entry.name for entry in options.pools}
    for directory in (root / "images", root / "placements"):
        for record in directory.glob("*.json"):
            data = json.loads(record.read_text())
            assigned = data.get("pool") or data.get("metadata", {}).get("pool")
            if assigned and assigned not in configured:
                raise ValueError(f"Image pool {assigned} still contains assigned images")
    source = root / "data"
    if options.pools and source.is_dir() and any(source.iterdir()):
        raise ValueError(f"Registry data exists outside the configured image pools: {source}")


def prepare_storage(root, options):
    validate_storage(root, options)
    pool = existing_pool(options)
    if not pool:
        (root / "data").mkdir(mode=0o700, exist_ok=True)
        return
    for entry in options.pools:
        (entry.path / "images").mkdir(mode=0o700, parents=True, exist_ok=True)
    (pool.path / "existing").mkdir(mode=0o700, parents=True, exist_ok=True)


def install(context):
    root = context.state_directory / "registry"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    options = Options.model_validate(context.options)
    prepare_storage(root, options)
    hostname = urlsplit(registry_url(context.options)).hostname
    hostnames = [hostname, *(urlsplit("https://" + mirror).hostname for mirror in build_mirrors(context.options))]
    alternatives = []
    for name in dict.fromkeys(hostnames):
        try:
            ipaddress.ip_address(name)
            alternatives.append("IP:" + name)
        except ValueError:
            alternatives.append("DNS:" + name)
    if not (root / "tls.crt").exists():
        context.runner(
            [
                "openssl",
                "req",
                "-x509",
                "-newkey",
                "rsa:3072",
                "-nodes",
                "-days",
                "3650",
                "-keyout",
                str(root / "tls.key"),
                "-out",
                str(root / "tls.crt"),
                "-subj",
                "/CN=" + hostname,
                "-addext",
                "subjectAltName=" + ",".join(alternatives),
            ],
            check=True,
        )
        (root / "tls.key").chmod(0o600)
    accounts = Accounts(root, context.options, lambda: None)
    accounts.identity("__service__")
    identities = root / "accounts.json"
    if identities.exists():
        accounts.publish(json.loads(identities.read_text()))
    context.installation_handoffs.setdefault("image", []).append(
        RegistryTrust(node_id=context.node_id, endpoint=urlsplit(registry_url(context.options)).netloc,
                      ca_file=root / "tls.crt").model_dump(mode="json")
    )


def compose(context):
    root = context.state_directory / "registry"
    architecture = getattr(context, "host", {}).get("architecture", "amd64")
    image = context.options.get("image", f"ghcr.io/project-zot/zot-linux-{architecture}:v2.1.22")
    endpoint = urlsplit(registry_url(context.options))
    address = context.options.get("publish_address")
    if address is None:
        try:
            address = str(ipaddress.ip_address(endpoint.hostname))
        except ValueError:
            address = "127.0.0.1"
    port = int(context.options.get("port", 5000))
    pools = [{"type": "bind", "source": str(pool.path), "target": str(POOL_MOUNTS / pool.name),
              "bind": {"create_host_path": False}}
             for pool in Options.model_validate(context.options).pools]
    return {
        "services": {
            "node": {"volumes": ["/var/run/docker.sock:/var/run/docker.sock", *pools]},
            "registry": {
                "image": image,
                "container_name": context.options.get("container_name", "gpu-platform-registry"),
                "restart": "unless-stopped",
                "ports": [{"target": port, "published": str(endpoint.port or 443),
                           "host_ip": address, "protocol": "tcp"}],
                "command": ["serve", "/etc/zot/config.json"],
                "volumes": [f"{root}:/etc/zot:ro", f"{root}:/var/lib/registry", *pools],
            }
        }
    }
