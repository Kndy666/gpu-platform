"""Presentation metadata declared by the selected accelerator module."""

from django.utils.translation import get_language, gettext_lazy as tr
from gpu_platform_contracts.texts import LocalizedText


def accelerator_module(node):
    return next((module for module in node.capabilities.get("modules", []) if module["slot"] == "gpu"), {})


def accelerator_texts(node):
    texts = accelerator_module(node).get("texts", {})
    defaults = {
        "device_label": "GPU",
        "device_memory_label": tr("GPU memory"),
        "device_details_label": tr("GPU allocations"),
    }
    keys = {
        "device_label": "gpu.device_label",
        "device_memory_label": "gpu.memory_label",
        "device_details_label": "gpu.details_label",
    }
    return {
        key: LocalizedText.model_validate(texts[keys[key]]).resolve(get_language() or "en")
        if keys[key] in texts
        else str(default)
        for key, default in defaults.items()
    }
