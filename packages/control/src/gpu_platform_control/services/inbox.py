"""Stand-alone notices and the signed-in user's inbox."""

from collections import Counter
from datetime import timezone as utc
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext_lazy as tr

from gpu_platform_contracts.slots.notify import IN_APP_CHANNEL
from gpu_platform_control.core.accounts.models import UserGroup
from gpu_platform_control.core.events.models import Notification
from gpu_platform_control.core.notifications import MANUAL_NOTICE, channel_configured
from gpu_platform_control.core.services import audit

from .boundary import authenticated, find, parameters, result, staff_only
from .pages import history_page


PENDING = ("pending", "sending")
PRESENTATIONS = [("toast", tr("Bottom-right reminder")), ("banner", tr("Top banner")), ("inbox", tr("Inbox only"))]
AUDIENCES = [("member", tr("One user")), ("group", tr("User group")), ("all", tr("All active users"))]
SUBMISSION_SALT = "platform.send-notice-direct"


def campaigns():
    return Notification.objects.filter(channel=IN_APP_CHANNEL, event=MANUAL_NOTICE, recipient__isnull=True)


def recipients_of(campaign):
    return Notification.objects.filter(channel=IN_APP_CHANNEL, payload__campaign=campaign.pk)


def inbox_for(actor):
    return Notification.objects.filter(recipient=actor, channel=IN_APP_CHANNEL, state__in=("sent", "revoked"))


def inbox_item(item):
    withdrawn = item.state == "revoked"
    return {
        "id": item.pk,
        "title": tr("Notification withdrawn") if withdrawn else item.title,
        "content": "" if withdrawn else item.content,
        "link": "" if withdrawn else item.payload.get("link", ""),
        "sent_at": item.sent_at,
        "unread": not withdrawn and not item.payload.get("read_at"),
        "withdrawn": withdrawn,
        "seconds": item.payload.get("seconds", 5),
    }


@authenticated
def inbox(actor, *, query=None):
    items = inbox_for(actor).order_by("-sent_at", "-pk")
    page = history_page(parameters(query), items)
    rows = [inbox_item(item) for item in page]
    page.object_list = []
    return result({"items": rows, "page": page})


@authenticated
@transaction.atomic
def read_notice(actor, pk):
    item = find(inbox_for(actor).select_for_update(), pk=pk)
    if item.state == "sent" and not item.payload.get("read_at"):
        item.payload["read_at"] = timezone.now().isoformat()
        item.save(update_fields=["payload"])
    return result({"notice": inbox_item(item)})


@authenticated
def notice_detail(actor, pk):
    return result({"notice": inbox_item(find(inbox_for(actor), pk=pk))})


@authenticated
@transaction.atomic
def dismiss_banner(actor, pk):
    item = find(inbox_for(actor).select_for_update(), pk=pk)
    if item.state == "sent" and item.payload.get("presentation") == "banner":
        item.payload["dismissed_at"] = timezone.now().isoformat()
        item.save(update_fields=["payload"])
    return result(complete=True)


@authenticated
@transaction.atomic
def reminders(actor, *, displayed=()):
    sent = inbox_for(actor).filter(state="sent")
    unread = sent.filter(payload__read_at__isnull=True)
    current_time = timezone.now().astimezone(utc.utc)
    now = current_time.isoformat()
    active = sent.filter(Q(payload__expires_at__isnull=True) | Q(payload__expires_at__gt=now))
    banners = active.filter(payload__presentation="banner", payload__dismissed_at__isnull=True)
    candidates = active.filter(payload__read_at__isnull=True, payload__shown_at__isnull=True).filter(
        Q(payload__presentation="toast") | Q(payload__presentation__isnull=True)
    )
    toasts = list(candidates.select_for_update().order_by("-sent_at", "-pk")[:3])
    for item in toasts:
        item.payload["shown_at"] = now
    if toasts:
        Notification.objects.bulk_update(toasts, ["payload"])
    banner = banners.select_for_update().order_by("-sent_at", "-pk").first()
    display_banner = None
    if banner:
        display_banner = inbox_item(banner)
        if seconds := banner.payload.get("seconds"):
            shown_at = banner.payload.get("shown_at")
            if not shown_at:
                banner.payload["shown_at"] = now
                banner.save(update_fields=["payload"])
            else:
                remaining = seconds - (current_time - parse_datetime(shown_at)).total_seconds()
                if remaining > 0:
                    display_banner["seconds"] = remaining
                else:
                    banner.payload["dismissed_at"] = now
                    banner.save(update_fields=["payload"])
                    display_banner = None
    return result({
        "unread": unread.count(),
        "toasts": [inbox_item(item) for item in toasts],
        "banners": [display_banner] if display_banner else [],
        "remove": list(inbox_for(actor).filter(pk__in=displayed).filter(
            Q(state="revoked") | Q(payload__expires_at__lte=now)
        ).values_list("pk", flat=True)),
    })


def _audience(values):
    users = get_user_model().objects.filter(is_active=True).order_by("pk")
    scope = values.get("audience")
    if scope == "member":
        users = users.filter(pk=values.get("member") or None)
        label = users.values_list("username", flat=True).first()
    elif scope == "group":
        group = UserGroup.objects.filter(pk=values.get("group") or None).first()
        label = group.name if group else None
        users = users.filter(profile__group=group)
    elif scope == "all":
        label = tr("All active users")
    else:
        raise ValidationError({"audience": tr("Choose who will receive the notification.")})
    ids = list(users.values_list("pk", flat=True))
    if not ids or not label:
        raise ValidationError({"audience": tr("There are no active users in this selection.")})
    return ids, str(label)


def _notice_values(values):
    title = str(values.get("title", "")).strip()
    content = str(values.get("content", "")).strip()
    if not 1 <= len(title) <= 100 or not 1 <= len(content) <= 4000:
        raise ValidationError(tr("Enter a title and message within the length limits."))
    link = str(values.get("link", "")).strip()
    if link and (len(link) > 500 or not link.startswith("/") or link.startswith("//")
                 or "\\" in link or any(ord(char) < 32 for char in link)
                 or not url_has_allowed_host_and_scheme(link, allowed_hosts=set())):
        raise ValidationError({"link": tr("Enter a path within this panel, starting with /.")})
    presentation = values.get("presentation")
    if presentation not in dict(PRESENTATIONS):
        raise ValidationError({"presentation": tr("Choose how to show the notification.")})
    seconds = values.get("seconds")
    if seconds is not None and (not isinstance(seconds, int) or not 3 <= seconds <= 60):
        raise ValidationError({"seconds": tr("Enter a duration between 3 and 60 seconds.")})
    if presentation == "toast" and seconds is None:
        seconds = 5
    if presentation == "inbox":
        seconds = None
    expires_at = values.get("expires_at")
    if expires_at and (timezone.is_naive(expires_at) or expires_at <= timezone.now()):
        raise ValidationError({"expires_at": tr("Choose a time in the future.")})
    metadata = {"presentation": presentation, "seconds": seconds, "link": link}
    if expires_at:
        metadata["expires_at"] = expires_at.astimezone(utc.utc).isoformat()
    return title, content, metadata


@staff_only
def composer(actor):
    members = list(get_user_model().objects.filter(is_active=True).order_by("username")
                   .values_list("pk", "username", "profile__group_id"))
    submission = signing.dumps({"id": uuid4().hex, "actor": actor.pk}, salt=SUBMISSION_SALT)
    return result({
        "section": "notifications-send", "section_title": tr("Send notification"),
        "available": channel_configured(IN_APP_CHANNEL),
        "recipient_counts": {"all": len(members), "groups": dict(Counter(member[2] for member in members))},
        "form_options": {
            "members": [(pk, username) for pk, username, _ in members],
            "groups": list(UserGroup.objects.order_by("name").values_list("pk", "name")),
            "audiences": AUDIENCES, "presentations": PRESENTATIONS,
            "initial": {"submission": submission},
        },
    })


@staff_only
@transaction.atomic
def send_notice(actor, values, *, submission):
    if not channel_configured(IN_APP_CHANNEL):
        raise ValidationError(tr("In-app notifications are unavailable."))
    try:
        nonce = signing.loads(submission, salt=SUBMISSION_SALT)
    except signing.BadSignature as error:
        raise ValidationError(tr("Reload the page and try sending again.")) from error
    if nonce["actor"] != actor.pk:
        raise ValidationError(tr("Reload the page and try sending again."))
    event_key = "notice:" + nonce["id"]
    previous = campaigns().filter(event_key=event_key).first()
    if previous is not None:
        return result(complete=True, identity=previous.pk, notices=[("success", tr("Notification queued."))])
    ids, audience = _audience(values)
    title, content, metadata = _notice_values(values)
    campaign, created = Notification.objects.get_or_create(
        event_key=event_key,
        defaults={"channel": IN_APP_CHANNEL, "event": MANUAL_NOTICE, "title": title, "content": content, "payload": {
            **metadata, "sender": actor.username, "audience": audience, "count": len(ids),
        }},
    )
    if created:
        Notification.objects.bulk_create([
            Notification(
                recipient_id=recipient_id, channel=IN_APP_CHANNEL, event=MANUAL_NOTICE,
                event_key=f"notice:{campaign.pk}:{recipient_id}", title=title, content=content,
                payload={**metadata, "campaign": campaign.pk, "code": MANUAL_NOTICE, "parameters": {}},
            )
            for recipient_id in ids
        ])
        audit(actor, "notice_sent", campaign.pk, title=title, recipients=len(ids))
    return result(complete=True, identity=campaign.pk, notices=[("success", tr("Notification queued."))])


def _history_row(item, counts):
    state = item.state
    manual = item.event == MANUAL_NOTICE
    pending = counts.get("pending", 0)
    if manual and state == "pending" and not pending:
        state = "sent" if counts.get("sent") else "failed" if counts.get("failed") else "skipped"
    return {
        "id": item.pk, "title": item.title, "content": item.content,
        "manual": manual, "state": dict(Notification._meta.get_field("state").choices)[state],
        "created_at": item.created_at, "sender": item.payload.get("sender"),
        "audience": item.payload.get("audience") if manual else
                    tr("Administrators") if item.channel == "pushplus" else item.recipient.username if item.recipient else "",
        "channel": tr("In-app") if item.channel == IN_APP_CHANNEL else
                   tr("WeChat") if item.channel == "pushplus" else item.channel,
        "count": item.payload.get("count", 1),
        "delivered": counts.get("sent", 0) + counts.get("revoked", 0) if manual else int(bool(item.sent_at)),
        "read": counts.get("read", 0) if manual else int(bool(item.payload.get("read_at"))),
        "show_read": item.channel == IN_APP_CHANNEL,
        "can_cancel": manual and item.state == "pending" and bool(pending),
        "can_revoke": manual and item.state != "revoked" and bool(counts.get("sent")),
        "changed_by": item.payload.get("changed_by"),
        "changed_at": parse_datetime(item.payload["changed_at"]) if item.payload.get("changed_at") else None,
    }


@staff_only
def sending_history(actor, *, query):
    origin = query.get("origin")
    items = Notification.objects.exclude(event=MANUAL_NOTICE, recipient__isnull=False)
    if origin == "manual":
        items = items.filter(event=MANUAL_NOTICE)
    elif origin == "automatic":
        items = items.exclude(event=MANUAL_NOTICE)
    items = items.select_related("recipient").order_by("-created_at", "-pk")
    page = history_page(parameters(query), items)
    ids = [item.pk for item in page if item.event == MANUAL_NOTICE]
    counts = {
        row["payload__campaign"]: row
        for row in Notification.objects.filter(channel=IN_APP_CHANNEL, payload__campaign__in=ids)
        .values("payload__campaign").annotate(
            pending=Count("id", filter=Q(state__in=PENDING)),
            sent=Count("id", filter=Q(state="sent")),
            revoked=Count("id", filter=Q(state="revoked")),
            failed=Count("id", filter=Q(state="failed")),
            read=Count("id", filter=Q(payload__read_at__isnull=False)),
        )
    }
    rows = [_history_row(item, counts.get(item.pk, {})) for item in page]
    page.object_list = []
    return result({
        "section": "notifications-history", "section_title": tr("Sending history"), "origin": origin,
        "rows": rows, "page": page,
    })


@staff_only
@transaction.atomic
def stop_notice(actor, pk, action):
    campaign = find(campaigns().select_for_update(), pk=pk)
    children = recipients_of(campaign)
    if action == "cancel" and campaign.state == "pending" and children.filter(state__in=PENDING).exists():
        state = "canceled"
    elif action == "withdraw" and campaign.state != "revoked" and children.filter(state="sent").exists():
        state = "revoked"
    else:
        raise ValidationError(tr("This notification can no longer be changed."))
    children.filter(state__in=PENDING).update(state="canceled")
    if state == "revoked":
        children.filter(state="sent").update(state="revoked")
    campaign.state = state
    campaign.payload.update(changed_by=actor.username, changed_at=timezone.now().isoformat())
    campaign.save(update_fields=["state", "payload"])
    audit(actor, "notice_" + state, campaign.pk, title=campaign.title)
    message = tr("Notification withdrawn.") if state == "revoked" else tr("Remaining delivery canceled.")
    return result(complete=True, notices=[("success", message)])
