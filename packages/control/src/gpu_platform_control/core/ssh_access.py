"""Public keys accepted by workspaces and administrator connection metadata."""

from django.db.models import Q
from gpu_platform_control.core.accounts.models import SSHKey


def authorized_keys(user_id):
    keys = (
        SSHKey.objects.filter(enabled=True, user__is_active=True)
        .filter(Q(user_id=user_id) | Q(user__is_staff=True))
        .order_by("user_id", "pk")
    )
    values = list(dict.fromkeys(keys.values_list("public_key", flat=True)))
    return "".join(value + "\n" for value in values)


def administrator_access(user):
    if not user.is_active or not user.is_staff:
        return None
    keys = list(
        SSHKey.objects.filter(user=user, enabled=True).order_by("pk").values("id", "name", "fingerprint")
    )
    return {"ready": bool(keys), "keys": keys, "sudo": True}
