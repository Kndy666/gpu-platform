"""Translate registered allocation metadata into standard node handoffs."""

from gpu_platform_contracts.resources import GPURequest
from gpu_platform_control.core.quotas.accounting import definitions


def gpu_requests(request):
    declarations = definitions(request.node)
    slots = {module['slot']: module for module in request.node.capabilities.get('modules', [])}
    registered = slots.get('gpu', {}).get('resources', [])
    keys = {item['key'] for item in registered}
    requests = []
    for group, allocations in request.resource_allocations.items():
        selected = {key: row.resource.allocation_field for key, row in declarations.items()
                    if key in keys and row.resource.allocation_group == group and row.resource.allocation_field}
        if not selected:
            continue
        for index, allocation in enumerate(allocations):
            fields = {selected[key]: value for key, value in allocation.items() if key in selected}
            requests.append(GPURequest(device_id=f'auto:{index}', **fields))
    return requests
