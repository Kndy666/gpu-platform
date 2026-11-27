from django.utils.translation import gettext_lazy as tr
from datetime import timedelta
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from gpu_platform_control.core.requests.models import ExtensionRequest, ResourceRequest
from .policies import approval_mode, extension_limit
from .services import adjust_end_time, approve, audit, can_adjust_time, extend, policy_for


def submit_request(req):
    audit(req.user, "submit", req.pk, node_id=req.node_id)
    if approval_mode(req.user) == "automatic":
        return approve(req.pk, None, "policy_automatic")
    return req


def validate_extension(req, ends_at):
    if not can_adjust_time(req, getattr(req, "workspace", None), timezone.now()):
        raise ValidationError(tr("Only active dev environments can be extended. Check the task status."))
    if req.extension_count >= extension_limit(req.user):
        raise ValidationError(tr("This dev environment has reached its extension limit. Save your work before the end time."))
    if not ends_at or ends_at <= req.ends_at:
        raise ValidationError(tr("Choose a later end time."))
    policy = policy_for(req.user)
    if not policy.unlimited and ends_at - req.ends_at > timedelta(hours=policy.interactive_hours):
        raise ValidationError(
            tr("Each extension may add up to {value_0} hours.").format(value_0=policy.interactive_hours)
        )


@transaction.atomic
def request_extension(request_id, actor, ends_at, reason):
    req = ResourceRequest.objects.select_for_update(of=("self",)).select_related("user", "workspace").get(
        pk=request_id
    )
    if req.user_id != actor.pk:
        raise ValidationError(tr("You can request extensions for your own environments."))
    validate_extension(req, ends_at)
    if not reason.strip():
        raise ValidationError(tr("Enter a reason for the extension."))
    if req.extensions.filter(status="pending").exists():
        raise ValidationError(tr("An extension is awaiting review. Check the task for the decision."))
    entry = ExtensionRequest.objects.create(
        request=req, previous_ends_at=req.ends_at, ends_at=ends_at, reason=reason.strip()
    )
    audit(
        actor,
        "extension_submitted",
        req.pk,
        extension_id=str(entry.pk),
        ends_at=ends_at.isoformat(),
        reason=reason,
    )
    if approval_mode(actor) == "automatic":
        return review_extension(entry.pk, None, "approve", "policy_automatic")
    return entry


@transaction.atomic
def adjust_time(request_id, actor, ends_at, reason="", *, by_owner=False):
    req = adjust_end_time(request_id, actor, ends_at, reason, by_owner=by_owner)
    pending = req.extensions.filter(status="pending").first()
    if pending is not None:
        record_extension_review(pending, actor, "superseded")


@transaction.atomic
def review_extension(identifier, actor, action, reason=""):
    if actor is not None and (not actor.is_staff):
        raise ValidationError(tr("Administrator permission is required."))
    request_id = ExtensionRequest.objects.values_list("request_id", flat=True).get(pk=identifier)
    req = ResourceRequest.objects.select_for_update().get(pk=request_id)
    entry = req.extensions.get(pk=identifier)
    if entry.status != "pending":
        raise ValidationError(tr("This extension request has already been reviewed."))
    if actor is None and approval_mode(req.user) != "automatic":
        raise ValidationError(tr("This member requires manual approval."))
    if action == "approve":
        validate_extension(req, entry.ends_at)
        if entry.previous_ends_at != req.ends_at:
            raise ValidationError(tr("The end time changed. Submit a new extension request."))
        extend(req.pk, actor, entry.ends_at, entry.reason, automatic=actor is None)
        status = "approved"
    elif action == "reject" and actor is not None and reason.strip():
        status = "rejected"
    else:
        raise ValidationError(tr("Select approve or reject, and provide a reason for rejection."))
    return record_extension_review(entry, actor, status, reason)


def record_extension_review(entry, actor, status, reason=""):
    entry.status = status
    (entry.reviewed_by, entry.review_reason, entry.reviewed_at) = (actor, reason, timezone.now())
    entry.save(update_fields=["status", "reviewed_by", "review_reason", "reviewed_at"])
    audit(
        actor,
        "extension_" + entry.status,
        entry.request_id,
        extension_id=str(entry.pk),
        ends_at=entry.ends_at.isoformat(),
        reason=reason,
    )
    return entry
