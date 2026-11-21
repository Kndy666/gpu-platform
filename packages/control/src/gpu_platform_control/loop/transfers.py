import time
from django.core.exceptions import ValidationError
from gpu_platform_contracts.directories import DirectoryManifest
from gpu_platform_contracts.messages import Operation
from gpu_platform_control.services.synchronization import run_sync, finish_cleanup, UncertainImport
from gpu_platform_control.core.sync.models import SyncJob
from gpu_platform_control.slots.gateway import NodeCommandError, wait_for_result


class Transfers:
    def __init__(self, gateway, *, clock=time.monotonic, sleep=time.sleep):
        self.gateway, self.clock, self.sleep = gateway, clock, sleep

    def wait(self, node, operation, **payload):
        return wait_for_result(
            self.gateway, node, operation, clock=self.clock, sleep=self.sleep, **payload
        )

    def clean_image_streams(self, job):
        pending = job.cleanup_image.get("streams", {})
        if not pending:
            return
        remaining = pending.copy()
        for node in (job.source, job.target):
            streams = pending.get(str(node.pk))
            if streams:
                try:
                    self.gateway.request(node, Operation.CLEANUP, streams=streams)
                except (OSError, ConnectionError, NodeCommandError):
                    continue
                remaining.pop(str(node.pk))
        if remaining:
            job.cleanup_image["streams"] = remaining
        else:
            job.cleanup_image.pop("streams", None)
        job.save(update_fields=["cleanup_image"])

    def transfer(self, job, progress):
        if job.kind == "datasets":
            return self.transfer_files(job, progress)
        gateway = self.gateway
        source = gateway.module(job.source, "transport")
        target = gateway.module(job.target, "transport")
        uncertain = False
        stream_id = job.pk.hex
        pending = job.cleanup_image.setdefault("streams", {})
        for node in (job.source, job.target):
            pending.setdefault(str(node.pk), [stream_id])
        job.save(update_fields=["cleanup_image"])
        try:
            image = {"stream_id": stream_id, "image_id": f"{job.owner_id}/sync-{job.pk.hex}",
                     "owner": "shared" if job.publish_shared else str(job.owner_id),
                     "architecture": job.image.architecture}
            if job.error.get("code") == "sync_delivery_uncertain":
                result = self.wait(job.target, Operation.IMAGE_IMPORT, resume_only=True, **image)
                if not result.get("needs_archive"):
                    return result
            export = self.wait(job.source, Operation.IMAGE_EXPORT,
                image_id=job.image.node_image_id or job.image.reference,
                owner=job.image.node_image_owner or str(job.image.owner_id or "shared"),
                transfer_id=str(job.pk), stream_id=stream_id)
            if export["stream_id"] not in pending[str(job.source.pk)]:
                pending[str(job.source.pk)].append(export["stream_id"])
                job.save(update_fields=["cleanup_image"])
            total = export.get("size_bytes")
            started = self.clock()
            transferred = 0

            def chunks():
                nonlocal transferred
                for chunk in source.receive_stream(gateway.endpoint(job.source), export["stream_id"]):
                    transferred += len(chunk)
                    if job.bandwidth_bytes_per_second:
                        remaining = transferred / job.bandwidth_bytes_per_second - (self.clock() - started)
                        while remaining > 0:
                            self.sleep(min(1, remaining))
                            remaining = transferred / job.bandwidth_bytes_per_second - (self.clock() - started)
                    progress(transferred, total)
                    yield chunk

            target.send_stream(gateway.endpoint(job.target), stream_id, chunks())
            return self.wait(job.target, Operation.IMAGE_IMPORT, **image)
        except (OSError, ConnectionError) as exc:
            uncertain = True
            raise UncertainImport() from exc
        except NodeCommandError as exc:
            if exc.message.code == "command.failed":
                uncertain = True
                raise UncertainImport() from exc
            raise
        finally:
            if not uncertain:
                self.clean_image_streams(job)

    def transfer_files(self, job, progress):
        gateway = self.gateway
        source, target = [gateway.module(node, "transport") for node in [job.source, job.target]]
        session_id = job.pk.hex
        streams = []
        uncertain = False
        rate_deadline = self.clock()

        def wait(node, **payload):
            return wait_for_result(gateway, node, Operation.DATASETS_IMPORT, clock=self.clock,
                                   sleep=lambda delay: self.sleep(min(delay, 0.1)), session_id=session_id, **payload)

        try:
            descriptor = self.wait(job.source, Operation.DATASETS_MANIFEST,
                                   paths=job.paths, transfer_id=str(job.pk))
            streams.append((job.source, descriptor["stream_id"]))
            metadata = bytearray()
            for chunk in source.receive_stream(gateway.endpoint(job.source), descriptor["stream_id"]):
                if len(metadata) + len(chunk) > 64 * 1024 * 1024:
                    raise ValueError("Directory manifest is too large")
                metadata.extend(chunk)
            manifest = DirectoryManifest.model_validate_json(bytes(metadata))
            files = {name: entry for name, entry in manifest.entries.items() if entry.kind == "file"}
            total = sum(entry.size_bytes or 0 for entry in files.values())
            metadata_id = session_id + "-manifest"
            streams.append((job.target, metadata_id))
            target.send_stream(gateway.endpoint(job.target), metadata_id, [bytes(metadata)])
            prepared = wait(job.target, action="prepare", stream_id=metadata_id, paths=job.paths)
            if prepared["completed"]:
                progress(total, total)
                return {}
            completed = 0
            for name, entry in files.items():
                size = entry.size_bytes or 0
                for attempt in range(2):
                    prepared = wait(job.target, action="file", path=name)
                    offset = prepared["offset"]
                    if type(offset) is not int or not 0 <= offset <= size:
                        raise ValueError("Invalid file resume offset")
                    progress(completed + offset, total)
                    if prepared["complete"]:
                        break
                    receiving = prepared["stream_id"]
                    streams.append((job.target, receiving))
                    exporting = self.wait(job.source, Operation.DATASETS_EXPORT, path=name, offset=offset,
                                          size_bytes=size, modified=entry.modified)
                    streams.append((job.source, exporting["stream_id"]))

                    def chunks():
                        nonlocal rate_deadline
                        current = offset
                        for chunk in source.receive_stream(gateway.endpoint(job.source), exporting["stream_id"]):
                            current += len(chunk)
                            if current > size:
                                raise ValueError("File exceeds manifest size")
                            if job.bandwidth_bytes_per_second:
                                rate_deadline = max(rate_deadline, self.clock()) + len(chunk) / job.bandwidth_bytes_per_second
                                delay = rate_deadline - self.clock()
                                while delay > 0:
                                    self.sleep(min(1, delay))
                                    delay = rate_deadline - self.clock()
                            yield chunk
                            progress(completed + current, total)
                    target.send_stream(gateway.endpoint(job.target), receiving, chunks())
                    if wait(job.target, action="verify", path=name)["verified"]:
                        break
                    if attempt:
                        raise ValidationError("dataset_checksum_mismatch", code="dataset_checksum_mismatch")
                completed += size
                progress(completed, total)
            wait(job.target, action="commit")
            return {}
        except (OSError, ConnectionError) as exc:
            uncertain = True
            raise UncertainImport() from exc
        finally:
            if not uncertain:
                for node, identifier in streams:
                    try:
                        gateway.request(node, Operation.CLEANUP, streams=[identifier])
                    except (OSError, ConnectionError, NodeCommandError):
                        pass
                try:
                    gateway.request(job.target, Operation.CLEANUP, dataset_sessions=[session_id])
                except (OSError, ConnectionError, NodeCommandError):
                    pass

    def recover(self):
        # The controller's exclusive loop lock makes previous running jobs abandoned.
        SyncJob.objects.filter(state="running").update(state="queued", started_at=None, transferred_bytes=0)

    def discard(self, job, result):
        self.gateway.request(
            job.target,
            Operation.IMAGE_DELETE,
            image_id=result["id"],
            owner="shared" if job.publish_shared else str(job.owner_id),
        )

    def tick(self):
        from gpu_platform_control.services.sync_policies import schedule_transfers

        schedule_transfers()
        for job in SyncJob.objects.filter(state__in=["completed", "failed", "cleaning"],
                cleanup_image__has_key="streams").select_related("source", "target"):
            self.clean_image_streams(job)
        for job in SyncJob.objects.filter(state="cleaning").select_related("target"):
            finish_cleanup(job, self.discard)
        for identifier in (
            SyncJob.objects.filter(state="queued").order_by("created_at").values_list("pk", flat=True)
        ):
            run_sync(identifier, self.transfer, self.discard)
