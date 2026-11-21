"""Public deployment settings projected from the cluster configuration."""

from gpu_platform_control.slots.configuration import cluster_config


def base_config():
    configuration = cluster_config()
    control = next(host for host in configuration.hosts.values() if "control" in host.roles)
    return {
        "platform": {
            "public_host": configuration.cluster.public_host,
            "https_origin": control.parameters.get(
                "https_origin", "https://" + configuration.cluster.public_host
            ),
            "ssh_ports": configuration.cluster.ssh_ports,
            "architecture": control.architecture,
            "reconcile_seconds": control.parameters.get("reconcile_seconds", 10),
            "batch_reminder_hours": control.parameters.get("batch_reminder_hours", 168),
        },
    }


def config():
    return base_config()
