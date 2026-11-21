import ipaddress
from urllib.parse import urlsplit

from gpu_platform_contracts.handoffs import RegistryTrust

from .accounts import Accounts


def registry_url(options):
    return options.get("url", f"https://127.0.0.1:{options.get('port', 5000)}")


def build_mirrors(options):
    return options.get("build_mirrors", [f"registry:{options.get('port', 5000)}"])


def install(context):
    root = context.state_directory / "registry"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    (root / "data").mkdir(mode=0o700, exist_ok=True)
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
    Accounts(root, context.options, lambda: None).identity("__service__")
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
    return {
        "services": {
            "node": {"volumes": ["/var/run/docker.sock:/var/run/docker.sock"]},
            "registry": {
                "image": image,
                "container_name": context.options.get("container_name", "gpu-platform-registry"),
                "restart": "unless-stopped",
                "ports": [{"target": port, "published": str(endpoint.port or 443),
                           "host_ip": address, "protocol": "tcp"}],
                "command": ["serve", "/etc/zot/config.json"],
                "volumes": [f"{root}:/etc/zot:ro", f"{root}:/var/lib/registry"],
            }
        }
    }
