from django import template
from django.urls import reverse
from django.utils.translation import gettext as _

from gpu_platform_control.services import approval_count
from gpu_platform_control.services.navigation import features

register = template.Library()


@register.inclusion_tag("portal/manage_navigation.html", takes_context=True)
def management_navigation(context):
    section = context["section"]
    current = section
    if section == "approvals":
        current = "approvals-" + context.get("approval_tab", "pending")
    approval_url = reverse("manage_approvals")
    groups = [
        (
            _("Resources"),
            "i-chip",
            [
                ("nodes", _("Nodes"), reverse("manage_nodes")),
                ("performance", _("Performance"), reverse("manage_performance")),
                ("usage", _("Usage statistics"), reverse("manage_usage")),
                ("containers", _("Containers"), reverse("manage_containers")),
                ("storage", _("Storage"), reverse("manage_storage")),
                ("images", _("Images"), reverse("manage_images")),
            ],
        ),
        (
            _("Members and permissions"),
            "i-admin",
            [
                ("members", _("Members"), reverse("manage_members")),
                ("groups", _("User groups"), reverse("manage_groups")),
            ],
        ),
        (
            _("Approvals and notifications"),
            "i-check",
            [
                ("approvals-pending", _("Pending approval"), approval_url + "?tab=pending"),
                ("approvals-push", _("Notification settings"), approval_url + "?tab=push"),
                ("approvals-history", _("Notification history"), approval_url + "?tab=history"),
            ],
        ),
        (
            _("System"),
            "i-settings",
            [
                ("settings", _("Settings"), reverse("manage_settings")),
                ("audits", _("Audit log"), reverse("manage_audits")),
            ],
        ),
    ]
    pending_count = approval_count()
    navigation = []
    for label, icon, entries in groups:
        items = [
            {"label": title, "url": url, "active": key == current}
            for key, title, url in entries
        ]
        navigation.append(
            {
                "label": label,
                "icon": icon,
                "items": items,
                "active": any(item["active"] for item in items),
                "count": pending_count if label == _("Approvals and notifications") else 0,
            }
        )
    return {"navigation": navigation, "current_title": context["section_title"]}


@register.simple_tag(takes_context=True)
def platform_features(context):
    actor = context["request"].user
    return features(actor) if actor.is_authenticated and actor.is_active else {}


@register.filter
def compact_number(value):
    from decimal import Decimal, InvalidOperation
    try:
        number = Decimal(str(value))
        if not number.is_finite():
            return "—"
        return format(number.quantize(Decimal("0.1")), "f").rstrip("0").rstrip(".") if number % 1 else str(int(number))
    except (InvalidOperation, TypeError, ValueError):
        return "—"


def display_scale(value, unit):
    from decimal import Decimal
    amount = Decimal(str(value))
    sizes = {"B": 1, "KiB": 1024, "MiB": 1024**2, "GiB": 1024**3, "TiB": 1024**4}
    if unit not in sizes:
        return Decimal(1), "%" if unit == "percent" else str(_("cores")) if unit == "cores" else "" if unit == "count" else unit
    byte_amount = abs(amount) * sizes[unit]
    units = [(1024**4, "TiB"), (1024**3, "GiB"), (1024**2, "MiB"), (1024, "KiB"), (1, "B")]
    divisor, label = next((size, label) for size, label in units if byte_amount >= size or size == 1)
    return Decimal(sizes[unit]) / divisor, label


@register.filter
def quantity(value, unit="GiB"):
    if value is None or value == "":
        return "—"
    from decimal import Decimal, InvalidOperation
    try:
        scale, label = display_scale(value, unit)
        return f"{compact_number(Decimal(str(value)) * scale)} {label}"
    except (InvalidOperation, TypeError, ValueError):
        return "—"


@register.filter
def available_gpu_memory(value):
    from decimal import Decimal

    if value and value >= 1:
        gib, mib = divmod(Decimal(str(value)) * 1024, 1024)
        return f"{gib} GiB {compact_number(mib)} MiB" if mib else f"{gib} GiB"
    return quantity(value)


@register.filter
def precise_number(value):
    from decimal import Decimal, InvalidOperation

    try:
        amount = Decimal(str(value))
    except InvalidOperation:
        return "—"
    return format(amount.normalize(), "f") if amount.is_finite() else "—"


@register.simple_tag
def resource_pair(available, total, unit):
    if available is None or total is None:
        return "—"
    from decimal import Decimal
    scale, label = display_scale(total, unit)
    return f"{compact_number(Decimal(str(available)) * scale)} / {compact_number(Decimal(str(total)) * scale)} {label}"



@register.filter
def unit_label(unit):
    return "%" if unit == "percent" else str(_("cores")) if unit == "cores" else "" if unit == "count" else unit



@register.simple_tag
def scaled_quantity(value, unit):
    from decimal import Decimal
    if value is None:
        return {"value": "—", "unit": ""}
    scale, label = display_scale(value, unit)
    return {"value": compact_number(Decimal(str(value)) * scale), "unit": label}
