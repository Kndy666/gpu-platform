"""Connection details are supplied by the entry implementation."""

from pydantic import ValidationError
from gpu_platform_contracts.handoffs import LoginAddress


def connection_for(request):
    workspace = getattr(request, "workspace", None)
    if request.status != "running" or not workspace or not workspace.connection:
        return None
    try:
        address = LoginAddress.model_validate(workspace.connection)
    except ValidationError:
        return None
    alias = "task-" + str(request.pk)
    return {
        "host": address.host,
        "port": address.port,
        "user": address.username,
        "alias": alias,
        "command": f"ssh -o HostKeyAlias={alias} -p {address.port} {address.username}@{address.host}",
        "config": f"Host {alias}\n    HostName {address.host}\n    Port {address.port}\n    User {address.username}\n    HostKeyAlias {alias}\n",
    }
