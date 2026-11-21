"""Localized resource read models driven by registered definitions."""

from django.utils.translation import get_language
from gpu_platform_contracts.texts import LocalizedText
from gpu_platform_control.core.quotas.models import NodeResource


def node_resources(node_id):
    return [
        {
            "key": row.resource_id,
            "label": LocalizedText.model_validate(row.resource.label).resolve(get_language() or "en"),
            "unit": row.resource.unit,
            "accounting": row.resource.accounting,
            "volume_kind": row.resource.volume_kind,
            "allocation_group": row.resource.allocation_group,
            "total": row.total,
            "used": row.used,
            "available": max(0, row.total - row.used),
        }
        for row in NodeResource.objects.filter(node_id=node_id).select_related("resource")
    ]


def task_resources(request):
    return [
        {**row, "value": request.resource_quantities[row["key"]]}
        for row in node_resources(request.node_id)
        if row["key"] in request.resource_quantities
    ]
