"""Dispatch intent and reconcile typed reports without execution manifests."""

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from gpu_platform_contracts.environment import EnvironmentIdentity
from gpu_platform_contracts.handoffs import VolumeRequest
from gpu_platform_contracts.messages import (
    CapabilityReport, NodeReport, Operation, TaskSpec, TaskStatus,
)
from gpu_platform_contracts.assessment import ResourceIntent, ResourceAssessment
from gpu_platform_control.core.requests.models import ResourceRequest
from gpu_platform_control.core.tasks.models import Workspace
from gpu_platform_control.core.retention.models import VolumeAllocation
from gpu_platform_control.core.nodes.models import SystemObservation
from gpu_platform_control.core.services import work_volume
from gpu_platform_control.core.ssh_access import authorized_keys
from gpu_platform_control.core.tasks.lifecycle import (
    allocate_port, complete_cleanup, mark_finished, reserve_dispatch,
)
from gpu_platform_control.services.resources import (
    register_capabilities,
    reserve_resources,
)
from gpu_platform_control.slots.gateway import Gateway, NodeCommandError
from gpu_platform_control.services.peer_ssh import prepare_identity, supported
from gpu_platform_contracts.peer_ssh import FEATURE


class Reconciler:
    def __init__(self, node, gateway=None, clock=timezone.now, *, metrics_history=None):
        self.node, self.gateway, self.clock = node, gateway or Gateway(), clock
        self.metrics_history = metrics_history

    def task_spec(self, request, workspace):
        user = request.user
        profile = user.profile
        home = VolumeAllocation.objects.exclude(state="deleted").get(
            node=request.node, user=user, kind="home"
        )
        work = work_volume(request)
        volumes = [
            VolumeRequest(id=str(home.pk), size_gib=home.size_gib, kind="home", mount_path=request.home_dir),
            VolumeRequest(id=str(work.pk), size_gib=work.size_gib, kind="work", mount_path="/workspace"),
        ]
        from gpu_platform_control.services.execution import gpu_requests
        from gpu_platform_control.core.quotas.accounting import definitions

        allocations = gpu_requests(request)
        registered = definitions(self.node)
        resources = {key: value for key, value in request.resource_quantities.items()
                     if key in registered and registered[key].resource.accounting == "task"}

        return TaskSpec(
            id=str(request.pk),
            node_id=self.node.name,
            owner=str(user.pk),
            kind=request.mode,
            image=request.image.node_image_id or request.image_reference or request.image.reference,
            image_owner=request.image.node_image_owner or str(request.image.owner_id or request.user_id),
            resources=resources,
            volumes=volumes,
            gpus=allocations,
            identity=EnvironmentIdentity(
                username=request.login_user,
                uid=profile.uid,
                gid=profile.gid,
                ssh_keys=authorized_keys(user.pk).splitlines(),
            ),
            expires_at=request.ends_at,
            command=request.argv,
            environment=request.environment,
            public_datasets=[""] if self.node.capabilities.get("features", {}).get("public_datasets") else [],
            priority=request.priority,
            login_port=workspace.ssh_port,
            extensions={
                "working_directory": request.working_dir,
                "resource_allocations": request.resource_allocations,
                "image_owner": request.image.node_image_owner
                or str(request.image.owner_id or request.user_id),
            },
        )

    @transaction.atomic
    def apply_status(self, status):
        request = ResourceRequest.objects.select_for_update().filter(node=self.node, pk=status.id).first()
        if request is None:
            return
        workspace = Workspace.objects.filter(request=request).first()
        if workspace is None:
            return
        if workspace.ended_at:
            if status.state in {"ended", "failed"}:
                transaction.on_commit(lambda: self.acknowledge_task(status.id))
            return
        workspace.node_port = status.port
        workspace.started_at = status.started_at or workspace.started_at
        workspace.save(update_fields=["node_port", "started_at"])
        if status.state in {"ended", "failed"}:
            state = (
                "failed"
                if status.state == "failed"
                else "canceled"
                if workspace.stop_requested
                else "expired"
                if request.ends_at and self.clock() >= request.ends_at
                else "succeeded"
            )
            detail = status.error.model_dump_json() if status.error else ""
            if mark_finished(request, state, status.exit_code, detail, status.ended_at):
                transaction.on_commit(lambda: self.acknowledge_task(status.id))
        else:
            ResourceRequest.objects.filter(pk=request.pk).update(status=status.state)
            if status.state == "running" and request.status != "running":
                from gpu_platform_control.core.services import audit

                audit(None, "running", request.pk, node=self.node.name)

    def acknowledge_task(self, identifier):
        try:
            self.gateway.request(self.node, Operation.TASK_ACK, ids=[identifier])
        except NodeCommandError as exc:
            if exc.message.code != "operation.pending":
                raise
            return
        request = ResourceRequest.objects.get(node=self.node, pk=identifier)
        complete_cleanup(request)

    def observe(self):
        capabilities = CapabilityReport.model_validate(
            self.gateway.request(self.node, Operation.CAPABILITIES)
        )
        report = NodeReport.model_validate(self.gateway.request(self.node, Operation.STATUS))
        if report.node_id != self.node.name:
            raise ConnectionError("node_identity_mismatch")
        register_capabilities(self.node.pk, capabilities)
        self.node.refresh_from_db()
        if report.maintenance != self.node.maintenance:
            self.gateway.request(self.node, Operation.MAINTENANCE, enabled=self.node.maintenance)
        SystemObservation.objects.update_or_create(
            node=self.node, name="node_report", defaults={"data": report.model_dump(mode="json")}
        )
        from gpu_platform_control.core.performance import record_performance

        performance = record_performance(self.node, report)
        if performance and self.metrics_history:
            self.metrics_history(self.node.pk, performance)
        for status in report.tasks:
            self.apply_status(status)
        # A successful node acknowledgement may have lost its reply and stopped reports.
        pending_cleanup = Workspace.objects.filter(
            request__node=self.node, ended_at__isnull=False, cleanup_complete=False
        ).exclude(request_id__in=[status.id for status in report.tasks])
        for identifier in pending_cleanup.values_list("request_id", flat=True):
            transaction.on_commit(lambda identifier=str(identifier): self.acknowledge_task(identifier))
        if report.events:
            from gpu_platform_control.core.events.ingest import ingest_events

            identifiers = ingest_events(self.node, report.events)
            transaction.on_commit(
                lambda: self.gateway.request(self.node, Operation.EVENTS_ACK, ids=identifiers)
            )

    def dispatch(self, request):
        from gpu_platform_control.core.workspace_copy import ensure_workspace_copy

        workspace = Workspace.objects.get(request=request)
        if workspace.ended_at:
            return
        if workspace.stop_requested or (request.ends_at and request.ends_at <= self.clock()):
            if workspace.dispatch_payload:
                result = self.gateway.request(self.node, Operation.TASK_STOP, task_id=str(request.pk))
                self.apply_status(TaskStatus.model_validate(result))
            else:
                mark_finished(request, "canceled" if workspace.stop_requested else "expired")
            return
        if request.starts_at and request.starts_at > self.clock():
            return
        if self.node.maintenance or not self.node.enabled:
            return
        if not ensure_workspace_copy(self.gateway, request):
            return
        if workspace.dispatch_payload:
            # A repeated start confirms uncertain delivery using the same immutable intent.
            payload = workspace.dispatch_payload
        else:
            with transaction.atomic():
                if not reserve_dispatch(request, workspace):
                    return
                if self.gateway.module(self.node, "entry").requires_public_port:
                    allocate_port(workspace)
                try:
                    spec = self.task_spec(request, workspace)
                    intent = ResourceIntent(
                        owner=spec.owner, resources=spec.resources, gpus=spec.gpus, volumes=spec.volumes
                    )
                    assessment = ResourceAssessment.model_validate(
                        self.gateway.request(
                            self.node, Operation.RESOURCE_ASSESS, **intent.model_dump(mode="json")
                        )
                    )
                    if not assessment.feasible:
                        transaction.set_rollback(True)
                        return
                    spec.gpus = assessment.gpus
                    from gpu_platform_control.core.quotas.accounting import definitions
                    registered = definitions(self.node)
                    spec.resources = {key: amount for key, amount in assessment.quantities.items()
                                      if key in registered and registered[key].resource.accounting == "task" and amount > 0}
                    request.resource_quantities.update({key: str(value) for key, value in spec.resources.items()})
                    request.save(update_fields=["resource_quantities"])
                    payload = spec.model_dump(mode="json")
                    reserve_resources(request.user, self.node, str(request.pk), payload["resources"])
                    if supported(self.node):
                        peer = prepare_identity(self.gateway, self.node, request.pk)
                        payload["extensions"][FEATURE] = peer.model_dump(mode="json")
                except ValidationError:
                    transaction.set_rollback(True)
                    return
                workspace.dispatch_payload = payload
                from gpu_platform_control.core.nodes.capabilities import allocation_snapshot

                workspace.allocations = allocation_snapshot(self.node, payload["gpus"])
                workspace.save(update_fields=["dispatch_payload", "allocations"])
        result = self.gateway.request(self.node, Operation.TASK_START, **payload)
        self.apply_status(TaskStatus.model_validate(result))

    def tick(self):
        try:
            self.observe()
            from .storage import sync_storage
            from gpu_platform_control.core.snapshots import sync_snapshots
            from gpu_platform_control.core.builds import BuildReconciler
            from gpu_platform_control.core.image_lifecycle import collect_images

            sync_storage(self.gateway, self.node)
            sync_snapshots(self.gateway, self.node)
            BuildReconciler(self.gateway, self.node).tick()
            collect_images(self.gateway, self.node)
            requests = ResourceRequest.objects.filter(
                node=self.node, status__in=["approved", "scheduled", "queued", "starting", "running"]
            )
            for request in requests.select_related("user__profile", "image").order_by(
                "-priority", "created_at"
            ):
                workspace = request.workspace
                if (
                    request.status in {"running", "starting"}
                    and not workspace.stop_requested
                    and not (request.ends_at and request.ends_at <= self.clock())
                ):
                    keys = authorized_keys(request.user.pk).splitlines()
                    previous = workspace.dispatch_payload.get("identity", {}).get("ssh_keys", [])
                    if keys != previous:
                        self.gateway.request(
                            self.node, Operation.TASK_KEYS, task_id=str(request.pk), keys=keys
                        )
                        if workspace.dispatch_payload:
                            workspace.dispatch_payload["identity"]["ssh_keys"] = keys
                            workspace.save(update_fields=["dispatch_payload"])
                    continue
                try:
                    self.dispatch(request)
                except NodeCommandError as exc:
                    if exc.message.code != "operation.pending":
                        Workspace.objects.filter(pk=request.pk).update(error=exc.message.model_dump_json())
        except (ConnectionError, OSError):
            # Connectivity changes admission; running-task state remains report-driven.
            from gpu_platform_control.core.notifications import mark_node_offline

            mark_node_offline(self.node.pk)
