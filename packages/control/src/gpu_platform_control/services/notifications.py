from django.utils.translation import gettext_lazy as tr, get_language
from django.core.exceptions import PermissionDenied, ValidationError
from gpu_platform_contracts.slots.notify import IN_APP_CHANNEL
from django.db import transaction
from gpu_platform_control.core.accounts.models import UserProfile
from gpu_platform_control.core.events.models import NotificationSettings
from gpu_platform_control.core.notifications import encode_token
from gpu_platform_control.slots import load
from gpu_platform_control.slots.configuration import cluster_config


def notification_channels(user, *, defaults=False):
    result = []
    for selection in cluster_config().control.notify:
        if selection.module == IN_APP_CHANNEL or (selection.module == "pushplus" and not defaults):
            continue
        module = load("notify", selection.module, options=selection.options)
        values = (
            NotificationSettings.load().receivers if defaults else user.profile.notification_receivers
        ).get(selection.module, {})
        fields = {key: dict(value) for key, value in module.recipient_fields().items()}
        for key, specification in fields.items():
            text = module.descriptor.texts.get(key)
            specification["label"] = (
                text.resolve(get_language() or "en") if text else key.replace("_", " ").capitalize()
            )
        result.append(
            {
                "channel": selection.module,
                "fields": fields,
                "values": {
                    key: bool(values.get(key)) if fields[key].get("secret") else values.get(key, "")
                    for key in fields
                },
            }
        )
    return result


@transaction.atomic
def set_notification_receiver(user, channel, values, *, defaults=False):
    if channel == "pushplus" and not defaults:
        raise PermissionDenied
    selection = next((item for item in cluster_config().control.notify if item.module == channel), None)
    if selection is None or channel == IN_APP_CHANNEL:
        raise ValidationError(tr("Notification channel is unavailable."), code="channel_unavailable")
    module = load("notify", selection.module, options=selection.options)
    fields = module.recipient_fields()
    if set(values) - set(fields):
        raise ValidationError(tr("Unknown receiver field."), code="receiver_field_unknown")
    if defaults:
        if not user.is_staff:
            raise PermissionDenied
        profile = NotificationSettings.objects.select_for_update().get(pk=NotificationSettings.load().pk)
        receivers = profile.receivers
    else:
        profile = UserProfile.objects.select_for_update().get(user=user)
        receivers = profile.notification_receivers
    recipient = dict(receivers.get(channel, {}))
    for key, value in values.items():
        if not isinstance(value, str) or len(value) > 4096:
            raise ValidationError(tr("Invalid receiver field."), code="receiver_field_invalid")
        import re
        if value and fields[key].get("pattern") and not re.fullmatch(fields[key]["pattern"], value):
            raise ValidationError(tr("Invalid receiver field."), code="receiver_field_invalid")
        recipient[key] = encode_token(value) if fields[key].get("secret") and value else value
    for key, specification in fields.items():
        if specification.get("required") and (not recipient.get(key)):
            raise ValidationError(tr("Required receiver field."), code="receiver_field_required")
    receivers[channel] = recipient
    profile.save(update_fields=["receivers" if defaults else "notification_receivers"])
    return {"channel": channel, "configured": True}
