from datetime import datetime, timezone
from dataclasses import dataclass
from collections.abc import Iterator
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _
from gpu_platform_contracts.messages import Operation
from gpu_platform_control.slots.gateway import Gateway, NodeCommandError, wait_for_result


@dataclass(frozen=True)
class Download:
    filename: str
    size_bytes: int
    chunks: Iterator[bytes]


def dataset_download(relative, node, gateway=None):
    from .features import require
    require(node, "public_datasets")
    gateway = gateway or Gateway()
    result = wait_for_result(gateway, node, Operation.DATASETS_DOWNLOAD, path=relative)
    transport = gateway.module(node, "transport")

    def chunks():
        try:
            yield from transport.receive_stream(gateway.endpoint(node), result["stream_id"])
        finally:
            gateway.request(node, Operation.CLEANUP, streams=[result["stream_id"]])

    return Download(result["filename"], result["size_bytes"], chunks())


def dataset_content(relative, query="", node=None):
    from .nodes.selection import selected_node

    node = node or selected_node()
    from .features import require
    require(node, "public_datasets")
    gateway = Gateway()
    try:
        result = gateway.request(node, Operation.DATASETS_LIST, path=relative)
    except NodeCommandError as exc:
        if exc.message.code != "command.invalid":
            raise
        try:
            return dataset_download(relative, node, gateway)
        except NodeCommandError as error:
            raise ValidationError(_("The requested item is unavailable."), code="dataset_not_found") from error
    entries = []
    for entry in result["entries"]:
        if query.strip().casefold() not in entry["name"].casefold():
            continue
        entries.append(
            {
                **entry,
                "size": entry["size_bytes"],
                "modified": datetime.fromtimestamp(entry["modified"], timezone.utc),
                "container_path": "/datasets/" + entry["path"],
            }
        )
    return sorted(entries, key=lambda item: (item["kind"] != "directory", item["name"].casefold()))
