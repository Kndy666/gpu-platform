"""Resolve structured execution errors for the selected user language."""

import json
from django.utils.translation import gettext as _, get_language
from gpu_platform_contracts.texts import LocalizedText


def message(code, node=None, parameters=None):
    known = {
        "feature_disabled": _("This feature is unavailable on the selected node."),
        "node_unavailable": _("The selected node is unavailable."),
        "image_quota_exceeded": _("Image quota is exhausted."),
        "image_architecture_mismatch": _("The image architecture does not match the destination node."),
        "image_unavailable": _("The selected image is unavailable."),
        "bandwidth_invalid": _("Bandwidth must be zero or greater."),
        "sync_conflicting_request": _("Wait for the current image copy to finish before changing its sharing mode."),
        "sync_target_is_source": _("Select a different destination node."),
        "dataset_path_invalid": _("Use relative dataset paths within the public directory."),
        "sync_failed": _("Synchronization failed."),
        "sync_size_exceeded": _("The copied image exceeds its reserved size."),
        "sync_delivery_uncertain": _("Waiting for the node to confirm the copied image."),
        "task_canceled": _("Task canceled."),
        "build_result_missing_image": _("The builder did not return an image."),
    }
    if code in known:
        return known[code]
    for module in node.capabilities.get("modules", []) if node else []:
        text = module.get("texts", {}).get(code)
        if text:
            return (
                LocalizedText.model_validate(text)
                .resolve(get_language() or "en")
                .format(**(parameters or {}))
            )
    return _("The operation failed. Review the execution logs for details.")


def execution_error(value, node=None):
    if not value:
        return ""
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except ValueError:
            return message(value, node)
    else:
        parsed = value
    if isinstance(parsed, dict) and "code" in parsed:
        return message(parsed["code"], node, parsed.get("parameters"))
    return message("", node)
