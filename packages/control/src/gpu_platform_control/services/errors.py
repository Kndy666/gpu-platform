"""Resolve structured execution errors for the selected user language."""

import json
from django.utils.translation import gettext as _, get_language
from gpu_platform_contracts.texts import LocalizedText


def message(code, node=None, parameters=None):
    known = {
        "feature_disabled": _("This server does not offer this feature. Choose a server that supports it."),
        "node_unavailable": _("This server is unavailable. Select another server or try again later."),
        "image_quota_exceeded": _("There is too little image space left. Delete unused images or ask an administrator to increase the quota."),
        "image_architecture_mismatch": _("This image cannot run on the destination server. Choose a server with a matching architecture."),
        "image_unavailable": _("This image is unavailable. Choose another image from the catalog."),
        "bandwidth_invalid": _("Bandwidth must be zero or greater."),
        "sync_conflicting_request": _("Wait for the current image copy to finish before changing its sharing mode."),
        "sync_target_is_source": _("Choose a destination server different from the source."),
        "dataset_path_invalid": _("Enter a path relative to the shared datasets folder."),
        "sync_failed": _("The copy failed. Check the source and destination servers, then retry synchronization."),
        "sync_size_exceeded": _("The copy needs more space than requested. Check the image size and available space before retrying."),
        "sync_delivery_uncertain": _("The destination server is checking the copy. Check synchronization progress again shortly."),
        "task_canceled": _("Task canceled."),
        "build_result_missing_image": _("The build finished without an image. Check the build log before retrying."),
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
