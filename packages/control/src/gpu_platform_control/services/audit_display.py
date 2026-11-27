from django.utils.translation import gettext_lazy as tr
import json
from gpu_platform_control.core.requests.models import ResourceRequest

ACTION_LABELS = {
    "node_created": tr("Server added"),
    "node_updated": tr("Server settings updated"),
    "sync_requested": tr("Synchronization requested"),
    "sync_completed": tr("Synchronization completed"),
    "workspace_ssh_connected": tr("SSH connection opened"),
    "workspace_ssh_rejected": tr("SSH connection denied"),
    "extension_submitted": tr("Request extension"),
    "extension_approved": tr("Extension approved"),
    "extension_rejected": tr("Extension rejected"),
    "extension_superseded": tr("Extension superseded"),
    "approval_rule_updated": tr("Update approval policy"),
    "push_settings_updated": tr("Update notification settings"),
    "notice_sent": tr("Send notification"),
    "notice_canceled": tr("Cancel remaining delivery"),
    "notice_revoked": tr("Withdraw notification"),
    "ending_soon": tr("Dev environment ending soon"),
    "running": tr("Task started"),
    "node_offline": tr("Server offline"),
    "submit": tr("Submit request"),
    "approve": tr("Approve request"),
    "reject": tr("Reject request"),
    "stop_requested": tr("Stop task"),
    "finished": tr("Task ended"),
    "resources_released": tr("Resources released"),
    "environment_saved": tr("Dev environment saved as an image"),
    "save_environment": tr("Save dev environment as an image"),
    "image_recycle_requested": tr("Schedule image deletion"),
    "image_delete_requested": tr("Request image deletion"),
    "image_recycled": tr("Image deleted"),
    "member_created": tr("Add member"),
    "member_updated": tr("Update member"),
    "policy_updated": tr("Update group quotas"),
    "recycle_settings_updated": tr("Update retention settings"),
    "home_resize": tr("Expand home volume"),
    "group_created": tr("Create group"),
    "group_updated": tr("Update group"),
    "group_deleted": tr("Delete group"),
    "cleanup": tr("Clean data"),
    "volume_deleted": tr("Volume deleted"),
    "volume_delete_requested": tr("Delete volume"),
    "volume_deletion_notice": tr("Work volume expiry reminder"),
    "ssh_key_add": tr("Add SSH key"),
    "ssh_key_disable": tr("Disable SSH key"),
    "ssh_key_enable": tr("Enable SSH key"),
    "image_build_submit": tr("Submit image build"),
    "image_build_succeeded": tr("Image build completed"),
    "api_token_issued": tr("Create API token"),
    "api_token_revoked": tr("Revoke API token"),
    "password_changed": tr("Change password"),
    "image_build_failed": tr("Image build failed"),
    "long_batch": tr("Batch job duration reminder"),
    "extend": tr("Extend dev environment"),
    "adjust_time": tr("Change dev environment end time"),
    "base_image_updated": tr("Update base image"),
    "user_test_cleanup": tr("Clean test environments"),
    "ssh_access_updated": tr("Update environment SSH access"),
    "workspace_copied": tr("Resize work volume"),
    "task_deleted": tr("Delete task record"),
}
DETAIL_LABELS = {
    "extension_id": tr("Extension request"),
    "scope": tr("Configuration scope"),
    "mode": tr("Approval policy"),
    "events": tr("Notification events"),
    "inapp_events": tr("Events shown in the panel"),
    "recipients": tr("Recipients"),
    "automatic": tr("Automatic approval"),
    "users_removed": tr("Accounts removed"),
    "requests_removed": tr("Tasks removed"),
    "volumes_removed": tr("Volumes removed"),
    "remaining_accounts": tr("Current accounts"),
    "remaining_requests": tr("Current tasks"),
    "production_shared_images": tr("Shared images"),
    "removed_test_image_tags": tr("Test image versions removed"),
    "images_removed": tr("Images removed"),
    "name": tr("Name"),
    "username": tr("Username"),
    "role": tr("User groups"),
    "group": tr("User groups"),
    "purpose": tr("Task name"),
    "node": tr("Server"),
    "node_id": tr("Server"),
    "source_node": tr("Source server"),
    "target_node": tr("Destination server"),
    "work_volume_id": tr("Work volume"),
    "enabled": tr("Enable account"),
    "administrator": tr("Administrator"),
    "reason": tr("Reason"),
    "message": tr("Description"),
    "status": tr("State"),
    "exit_code": tr("Exit code"),
    "error": tr("Error"),
    "priority": tr("Priority"),
    "size_gib": tr("Capacity GiB"),
    "size_bytes": tr("Size (bytes)"),
    "reference": tr("Image reference"),
    "digest": tr("Image version identifier"),
    "image": tr("Images"),
    "pvc": tr("Volumes"),
    "fingerprint": tr("Public key fingerprint"),
    "ends_at": tr("Ends at"),
    "previous_ends_at": tr("Previous end time"),
    "expires_in": tr("Token lifetime"),
    "key_count": tr("Enabled public keys"),
    "destination": tr("Destination group"),
    "moved_members": tr("Members moved"),
    "make_default": tr("Set as default group"),
    "unlimited": tr("Unlimited resources"),
    "gpu": "GPU",
    "cpu": tr("CPU cores"),
    "memory_gib": tr("Memory GiB"),
    "workspace_gib": tr("Work volume GiB"),
    "gpu_compute_percent": tr("Total GPU compute %"),
    "gpu_memory_gib": tr("Total GPU memory GiB"),
    "gpu_compute_per_card_percent": tr("GPU compute limit per device %"),
    "gpu_memory_per_card_gib": tr("GPU memory limit per device GiB"),
    "home_gib": tr("Home volume limit GiB"),
    "image_gib": tr("Image quota GiB"),
    "concurrent_tasks": tr("Concurrent tasks"),
    "interactive_hours": tr("Maximum environment duration (hours)"),
    "snapshot_hours": tr("Retention after success or cancellation (hours)"),
    "failed_snapshot_hours": tr("Retention after failure (hours)"),
    "workspace_retention_days": tr("Work volume retention (days)"),
    "kind": tr("Volume type"),
}


def detail_items(detail):
    items = []
    for key, value in detail.items():
        if key == "_module":
            continue
        if key == "expires_in" and type(value) is int:
            value = (
                tr("Permanent")
                if value == 0
                else tr("{value_0} days").format(value_0=value // 86400)
                if value % 86400 == 0
                else tr("{value_0} seconds").format(value_0=value)
            )
        if key == "status" and isinstance(value, str):
            value = dict(ResourceRequest.State.choices).get(value, value)
        if key == "role" and isinstance(value, str):
            value = {"temporary": tr("Temporary members"), "longterm": tr("Long-term members")}.get(
                value, value
            )
        structured = isinstance(value, (dict, list))
        if structured:
            value = json.dumps(value, ensure_ascii=False, indent=2)
        elif isinstance(value, bool):
            value = tr("Yes") if value else tr("No")
        elif value is None:
            value = "—"
        items.append(
            {"label": DETAIL_LABELS.get(key, key.replace("_", " ")), "value": value, "structured": structured}
        )
    return items


def audit_presentation(row):
    label = ACTION_LABELS.get(row.action)
    if label is None and row.detail.get("_module"):
        from django.utils.translation import get_language
        from gpu_platform_contracts.texts import LocalizedText
        from gpu_platform_control.core.nodes.models import ComputeNode

        source = row.detail["_module"]
        if source.get("source", "").startswith("control:"):
            from importlib.metadata import entry_points
            matches = list(entry_points(group="gpu_platform.slots." + source["slot"], name=source["module"]))
            if len(matches) == 1:
                text = matches[0].load().descriptor.events.get(row.action)
                if text:
                    label = text.resolve(get_language() or "en")
        node = ComputeNode.objects.filter(pk=source.get("node_id")).first()
        for module in node.capabilities.get("modules", []) if node else []:
            if module["slot"] == source.get("slot") and row.action in module.get("events", {}):
                label = LocalizedText.model_validate(module["events"][row.action]).resolve(get_language() or "en")
                break
    return {
        "label": label or tr("System event"),
        "target_label": tr("Platform") if row.target == "platform" else row.target,
        "details": detail_items(row.detail),
    }
