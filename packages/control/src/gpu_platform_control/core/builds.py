"""Build intent, quota enforcement, and artifact records."""

from decimal import Decimal
from django.db import transaction
from django.db.models import Q
from gpu_platform_contracts.handoffs import Image
from gpu_platform_contracts.messages import Operation
from gpu_platform_contracts.slots.build import BuildSpec, BuildResult
from gpu_platform_control.slots.gateway import NodeCommandError
from gpu_platform_control.core.retention.models import ImageArtifact, ImageBuild
from .services import audit


class BuildReconciler:
    def __init__(self, gateway, node):
        self.gateway, self.node = gateway, node

    def authorized_images(self, build):
        images = ImageArtifact.objects.filter(node=self.node, verified=True, state="ready")
        if build.bootstrap:
            images = images.filter(shared=True)
        elif not build.user.is_staff:
            images = images.filter(Q(owner=build.user) | Q(shared=True))
        return [
            Image(
                id=image.node_image_id or image.reference,
                owner=image.node_image_owner or str(image.owner_id or "shared"),
                node_id=self.node.name,
                reference=image.reference,
                architecture=image.architecture,
                form="reference",
            )
            for image in images
        ]

    def tick(self):
        if self.node.maintenance:
            return
        for build in ImageBuild.objects.filter(
            node=self.node, status__in=["submitted", "building"]
        ).select_related("user"):
            if not build.dispatch_payload:
                build.dispatch_payload = BuildSpec(
                    id=str(build.pk),
                    owner="shared" if build.bootstrap else str(build.user_id),
                    description=build.dockerfile,
                    context=build.context_ref if build.bootstrap else build.context_git,
                    architecture=self.node.architecture,
                    resources={},
                    images=self.authorized_images(build),
                ).model_dump(mode="json")
                build.save(update_fields=["dispatch_payload"])
            try:
                result = BuildResult.model_validate(
                    self.gateway.request(self.node, Operation.IMAGE_BUILD, **build.dispatch_payload)
                )
            except NodeCommandError as exc:
                if exc.message.code == "operation.pending":
                    ImageBuild.objects.filter(pk=build.pk).update(status="building")
                    continue
                build.error = exc.message.model_dump_json()
                if exc.message.code in {"command.failed", "node.maintenance", "protocol.incompatible"}:
                    build.save(update_fields=["error"])
                    continue
                build.status = "failed"
                build.save(update_fields=["status", "error"])
                audit(build.user, "image_build_failed", build.pk, name=build.name)
                continue
            except (ConnectionError, OSError):
                continue
            build.log = result.logs
            build.error = ""
            if result.state == "completed":
                image = result.image
                if image is None:
                    build.status, build.error = "failed", "build_result_missing_image"
                    build.save(update_fields=["status", "error"])
                    continue
                with transaction.atomic():
                    from gpu_platform_control.core.accounts.models import UserProfile

                    UserProfile.objects.select_for_update().get(user=build.user)
                    from .quotas.accounting import check_image_quota
                    from django.core.exceptions import ValidationError

                    permitted = True
                    if not build.bootstrap:
                        try:
                            check_image_quota(build.user, self.node, Decimal(image.size_bytes) / Decimal(2**30))
                        except ValidationError:
                            permitted = False
                    if not permitted:
                        self.gateway.request(
                            self.node,
                            Operation.IMAGE_DELETE,
                            image_id=image.id,
                            owner="shared" if build.bootstrap else str(build.user_id),
                        )
                        build.status, build.error = "failed", "image_quota_exceeded"
                    else:
                        artifact, _ = ImageArtifact.objects.get_or_create(
                            node=self.node,
                            reference=image.reference,
                            defaults={
                                "owner": None if build.bootstrap else build.user,
                                "name": build.name,
                                "digest": image.digest,
                                "node_image_id": image.id,
                                "node_image_owner": image.owner,
                                "size_bytes": image.size_bytes,
                                "architecture": image.architecture,
                                "verified": True,
                                "shared": build.bootstrap,
                            },
                        )
                        build.artifact, build.status = artifact, "succeeded"
                    build.save()
                    audit(build.user, "image_build_" + build.status, build.pk, name=build.name)
            elif result.state == "failed":
                build.status = "failed"
                build.error = result.error.model_dump_json() if result.error else "build_failed"
                build.save()
                audit(build.user, "image_build_failed", build.pk, name=build.name)
            else:
                build.status = "building" if result.state == "running" else "submitted"
                build.save(update_fields=["status", "log", "error"])
