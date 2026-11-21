import io
from decimal import Decimal

import pytest
from pydantic import ValidationError

from gpu_platform_contracts.config import PlatformConfig
from gpu_platform_contracts.environment import StandardEnvironment
from gpu_platform_contracts.messages import Command, MAX_MESSAGE_BYTES, Operation, Reply, encode_message, read_message
from gpu_platform_contracts.resources import GPURequest, ResourceDefinition, ResourceQuotaRule, ResourceUsage
from gpu_platform_contracts.texts import LocalizedText
from gpu_platform_contracts.module import ModuleDescriptor


def test_resource_quantities_reject_invalid_numbers():
    for number in (Decimal("NaN"), Decimal("Infinity"), -1):
        with pytest.raises(ValidationError):
            ResourceUsage(total={"custom.capacity": number})
    assert ResourceUsage(total={"custom.capacity": "1.25"}).total["custom.capacity"] == Decimal("1.25")


def test_gpu_share_bounds():
    with pytest.raises(ValidationError):
        GPURequest(device_id="device", compute_percent=101, memory_gib=1)


def test_environment_invariants():
    for field in StandardEnvironment.model_fields:
        with pytest.raises(ValidationError):
            StandardEnvironment(**{field: False})


def test_wire_frame_size_and_roundtrip():
    command = Command(operation=Operation.STATUS)
    assert Command.model_validate(read_message(io.BytesIO(encode_message(command)))) == command
    for data in (b"{}", b"[]\n", b"x" * (MAX_MESSAGE_BYTES + 1)):
        with pytest.raises(ValueError):
            read_message(io.BytesIO(data))
    with pytest.raises(ValueError):
        encode_message(Command(operation=Operation.STATUS, payload={"bytes": "x" * MAX_MESSAGE_BYTES}))


def test_reply_error_invariant():
    with pytest.raises(ValidationError):
        Reply(id="one", ok=False)


def test_cluster_topology_validation():
    with pytest.raises(ValidationError):
        PlatformConfig(hosts={"one": {"roles": ["node"]}})
    config = PlatformConfig(hosts={"one": {"roles": ["control", "node"]}})
    assert config.hosts["one"].roles == ["control", "node"]


def test_quota_conversion_metadata_requires_a_valid_source_and_target():
    source = ResourceDefinition(key="capacity", unit="percent", label=LocalizedText(en="Capacity"))
    for divisor in [0, -1, "Infinity", "NaN"]:
        with pytest.raises(ValidationError):
            ResourceQuotaRule(source=source, divisor=divisor)
    with pytest.raises(ValidationError):
        ResourceQuotaRule(source=source, basis="per_allocation")
    rule = ResourceQuotaRule(source=source, divisor=100)
    for definitions in [[], [source], [ResourceDefinition(key="count", unit="count", label=LocalizedText(en="Count"))]]:
        with pytest.raises(ValidationError):
            ModuleDescriptor(name="fixture", slot="gpu", side="node", version="1",
                             resources=definitions, quota_rules={"count": rule})
    with pytest.raises(ValidationError):
        ModuleDescriptor(name="fixture", slot="gpu", side="node", version="1",
                         resources=[source.model_copy(update={"integral": True})], quota_rules={"capacity": rule})
    descriptor = ModuleDescriptor(name="fixture", slot="gpu", side="node", version="1",
        resources=[ResourceDefinition(key="count", unit="count", label=LocalizedText(en="Count"), integral=True)],
        quota_rules={"count": rule})
    assert ModuleDescriptor.model_validate_json(descriptor.model_dump_json()) == descriptor
