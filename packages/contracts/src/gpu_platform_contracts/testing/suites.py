from datetime import timedelta
from math import isfinite

from ..messages import ACTIVE_STATES, Command, Operation, TERMINAL_STATES


class AuthContract:
    def test_stable_verified_identity(self, auth, valid_credentials):
        first = auth.authenticate(valid_credentials)
        second = auth.authenticate(valid_credentials)
        assert first is not None and second is not None
        assert (first.provider, first.subject) == (second.provider, second.subject)

    def test_rejects_invalid_credentials(self, auth, invalid_credentials):
        assert auth.authenticate(invalid_credentials) is None


class TransportContract:
    def test_correlated_command(self, transport, endpoint):
        command = Command(operation=Operation.CAPABILITIES)
        reply = transport.request(endpoint, command)
        assert reply.id == command.id and reply.ok
        assert transport.online(endpoint)

    def test_binary_stream_roundtrip(self, transport, endpoint, stream_id):
        data = bytes(range(256)) * 8193
        assert transport.send_stream(endpoint, stream_id, (data[:1000], data[1000:])) == len(data)
        assert b"".join(transport.receive_stream(endpoint, stream_id)) == data

    def test_port_connection(self, transport, endpoint, echo_port, connect_echo):
        with transport.open_port(endpoint, echo_port) as address:
            assert connect_echo(address, b"\x00\xff\x01") == b"\x00\xff\x01"


class EntryContract:
    def test_entry_lifecycle(self, entry, task_id, username, connection, public_port):
        address = entry.open(task_id, username, connection, public_port)
        assert entry.address(task_id) == address
        assert entry.open(task_id, username, connection, public_port) == address
        entry.close(task_id)
        entry.close(task_id)
        assert entry.address(task_id) is None


class RuntimeContract:
    def test_task_lifecycle(self, runtime, task_spec, task_inputs, await_task):
        first = runtime.start(task_spec, task_inputs)
        assert first.id == task_spec.id and first.state in ACTIVE_STATES
        assert runtime.start(task_spec, task_inputs).id == first.id
        assert any(task.id == first.id for task in runtime.list_tasks())
        running = await_task(runtime, first.id, {"running"})
        assert running.usage == task_spec.resources
        runtime.stop(first.id)
        stopped = await_task(runtime, first.id, TERMINAL_STATES)
        assert runtime.status(first.id).state == stopped.state
        assert runtime.stop(first.id).state in TERMINAL_STATES

    def test_local_expiry(self, runtime, task_spec, task_inputs, await_task):
        assert task_spec.expires_at is not None
        runtime.start(task_spec, task_inputs)
        runtime.expire(task_spec.expires_at + timedelta(seconds=1))
        assert await_task(runtime, task_spec.id, TERMINAL_STATES).state in TERMINAL_STATES

    def test_standard_environment(self, runtime, task_spec, task_inputs, environment_probe):
        runtime.start(task_spec, task_inputs)
        report = environment_probe(runtime, task_spec.id)
        assert all(report[field] for field in type(runtime.environment).model_fields)


class StorageContract:
    def test_volume_lifecycle(
        self, storage, owner, volume_request, grown_size, write_volume, read_volume, await_operation
    ):
        volume = await_operation(lambda: storage.create(owner, volume_request))
        assert storage.get(volume.id, owner).size_gib == volume_request.size_gib
        write_volume(volume, b"\x00\xff" * 50)
        grown = await_operation(lambda: storage.resize(volume.id, owner, grown_size))
        assert grown.size_gib == grown_size
        assert read_volume(grown) == b"\x00\xff" * 50
        await_operation(lambda: storage.delete(volume.id, owner))
        await_operation(lambda: storage.delete(volume.id, owner))

    def test_independent_copy(
        self, storage, owner, volume_request, copy_request, write_volume, read_volume, await_operation
    ):
        source = await_operation(lambda: storage.create(owner, volume_request))
        write_volume(source, b"\x00\x01")
        copied = await_operation(lambda: storage.copy(source.id, owner, copy_request))
        write_volume(source, b"\x02\x03")
        assert read_volume(copied) == b"\x00\x01"


class RegistryContract:
    def test_export_import(self, registry, owner, image_source, image_id, second_image_id):
        image = registry.store(image_source, owner, image_id)
        exported = list(registry.export_image(image.id, owner))
        copied = registry.import_image(second_image_id, owner, image.architecture, iter(exported))
        assert copied.id == second_image_id
        assert copied.owner == owner and copied.digest == image.digest
        assert registry.get(copied.id, owner).digest == image.digest
        registry.delete(copied.id, owner)
        registry.delete(image.id, owner)


class BuildContract:
    def test_bounded_build(self, builder, build_spec, await_build):
        result = builder.build(build_spec)
        assert result.id == build_spec.id
        complete = await_build(builder, build_spec.id)
        assert complete.state == "completed" and complete.image is not None
        assert complete.image.owner == build_spec.owner
        assert complete.image.architecture == build_spec.architecture

    def test_cancel(self, builder, build_spec):
        builder.build(build_spec)
        builder.cancel(build_spec.id)
        assert builder.status(build_spec.id).state == "failed"


class GPUContract:
    def test_lease_accounting(self, gpu, task_id, gpu_requests):
        before = gpu.resources()
        lease = gpu.allocate(task_id, gpu_requests)
        assert lease.devices == gpu_requests
        assert gpu.allocate(task_id, gpu_requests).id == lease.id
        during = gpu.resources()
        assert all(during.used.get(key, 0) >= value for key, value in before.used.items())
        gpu.release(lease.id)
        gpu.release(lease.id)
        assert gpu.resources() == before


class MetricsContract:
    def test_finite_measurements(self, metrics):
        sample = metrics.sample()
        assert sample and all(value is None or isinstance(value, str) or isfinite(value) for value in sample.values())


class NotifyContract:
    def test_recipient_delivery(self, notifier, recipient, event, language, assert_delivery):
        notifier.send(recipient, event, language)
        assert_delivery(recipient, event, language)
