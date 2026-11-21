"""Materialized view data: no queryset or model instance crosses the service boundary."""

from dataclasses import dataclass
from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID
from django.core.paginator import Page
from django.db.models import Model, QuerySet
from django.utils.functional import Promise

SECRET_FIELDS = {"password", "notification_receivers", "receivers"}
COMPUTED_FIELDS = {
    "ResourceRequest": [
        "events",
        "login_user",
        "home_dir",
        "can_stop",
        "stop_label",
        "stop_pending_label",
        "workspace",
    ],
    "User": ["profile"],
    "UserGroup": ["member_count"],
    "VolumeAllocation": ["busy", "user_size_gib"],
    "StoragePool": ["available_gib", "percent"],
    "ImageArtifact": ["used_by", "owner_name"],
    "AuditLog": ["label", "target_label", "details"],
    "SyncJob": ["phase"],
}


@dataclass(frozen=True)
class Pagination:
    count: int
    num_pages: int


@dataclass(frozen=True)
class Collection:
    items: list
    number: int
    paginator: Pagination
    previous_url: str | None = None
    next_url: str | None = None
    start: int = 0
    end: int = 0

    def __iter__(self):
        return iter(self.items)

    def __len__(self):
        return len(self.items)

    def __bool__(self):
        return bool(self.items)

    def has_other_pages(self):
        return self.paginator.num_pages > 1

    def has_previous(self):
        return self.number > 1

    def has_next(self):
        return self.number < self.paginator.num_pages

    def previous_page_number(self):
        return self.number - 1

    def next_page_number(self):
        return self.number + 1

    def start_index(self):
        return self.start

    def end_index(self):
        return self.end


def materialize(value, *, depth=3, seen=frozenset()):
    if value is None or isinstance(value, (str, int, float, bool, Decimal, UUID, datetime, date)):
        return value
    if isinstance(value, Promise):
        return str(value)
    if isinstance(value, Page):
        return Collection(
            [materialize(row, depth=depth) for row in value],
            value.number,
            Pagination(value.paginator.count, value.paginator.num_pages),
            getattr(value, "previous_url", None),
            getattr(value, "next_url", None),
            value.start_index(),
            value.end_index(),
        )
    if isinstance(value, Model):
        identity = (value._meta.label, value.pk)
        result = {"id": value.pk, "pk": value.pk}
        if identity in seen:
            return result
        seen = seen | {identity}
        for field in value._meta.concrete_fields:
            if field.name in SECRET_FIELDS:
                continue
            if field.is_relation:
                result[field.attname] = getattr(value, field.attname)
                if depth > 0:
                    result[field.name] = materialize(getattr(value, field.name), depth=depth - 1, seen=seen)
            else:
                result[field.name] = materialize(getattr(value, field.name), depth=depth, seen=seen)
            if field.choices:
                result["get_" + field.name + "_display"] = str(
                    getattr(value, "get_" + field.name + "_display")()
                )
        if type(value).__name__ == "ResourceRequest":
            from .projections import task_fields
            result.update(task_fields(value))
            from gpu_platform_control.core.nodes.capabilities import accelerator_module, accelerator_texts
            result["accelerator_label"] = accelerator_texts(value.node)["device_label"]
            result["accelerator_count"] = result["gpu"] or sum(Decimal(str(value.resource_quantities.get(row["key"], 0)))
                for row in accelerator_module(value.node).get("resources", []) if row["unit"] == "count")
            import shlex
            result["command"] = value.argv[2] if len(value.argv) == 3 and value.argv[:2] == ["/bin/bash", "-c"] else shlex.join(value.argv)
            workspace = getattr(value, "workspace", None)
            allocations = workspace.allocations.get("gpu", []) if workspace else []
            result["allocated_devices"] = allocations
            result["gpu_rows"] = [
                {"number": index + 1, **request,
                 "allocation": allocations[index] if index < len(allocations) else None}
                for index, request in enumerate(result["gpu_requests"])
            ]
        if type(value).__name__ == "UserGroup":
            from .catalog import resource_options
            from gpu_platform_control.core.policies import group_policy
            policy = group_policy(value)
            result["quota_rows"] = [
                {**row, "limit": policy["resources"].get(row["key"]),
                 "per_allocation_limit": policy["per_allocation"].get(row["key"])}
                for row in resource_options() if row["key"] in policy["resources"]
                and row["has_member_quota"]
            ]
        if type(value).__name__ in {"Workspace", "ImageBuild", "EnvironmentSnapshot"} and result.get("error"):
            from .errors import execution_error

            related = (
                value.request
                if type(value).__name__ == "Workspace"
                else value.source
                if type(value).__name__ == "EnvironmentSnapshot"
                else value
            )
            result["error_code"] = result["error"]
            result["error"] = execution_error(result["error"], related.node)
        if type(value).__name__ == "ComputeNode" and result.get("error"):
            from .errors import execution_error

            result["error"] = execution_error(result["error"], value)
        for name in COMPUTED_FIELDS.get(type(value).__name__, []):
            if depth > 0 and hasattr(value, name):
                result[name] = materialize(getattr(value, name), depth=depth - 1, seen=seen)
        return result
    if isinstance(value, Mapping):
        return {key: materialize(item, depth=depth, seen=seen) for key, item in value.items()}
    from django.db.models.manager import BaseManager

    if isinstance(value, BaseManager):
        return {"all": materialize(list(value.all()), depth=depth, seen=seen)}
    if isinstance(value, (list, tuple, set, QuerySet)):
        return [materialize(item, depth=depth, seen=seen) for item in value]
    raise TypeError(f"Unsupported read model: {type(value).__name__}")
