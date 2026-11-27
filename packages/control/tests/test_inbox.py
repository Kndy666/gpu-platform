from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from gpu_platform_control.core.events.models import Notification, NotificationSettings
from gpu_platform_control.core.notifications import MANUAL_NOTICE, deliver_one
from gpu_platform_control.core.services import audit
from gpu_platform_control.services import inbox
from gpu_platform_control.services.boundary import NotFound
from gpu_platform_control.services.notifications import set_notification_receiver
from gpu_platform_control.slots.configuration import cluster_config


pytestmark = pytest.mark.django_db


@pytest.fixture
def members(settings):
    settings.PLATFORM_CLUSTER_CONFIG = {
        "control": {"notify": [{"module": "inapp"}, {"module": "pushplus"}]},
        "hosts": {"main": {"roles": ["control", "node"]}},
    }
    cluster_config.cache_clear()
    users = get_user_model()
    admin = users.objects.create_user("admin", is_staff=True)
    member = users.objects.create_user("member")
    other = users.objects.create_user("another_member")
    yield admin, member, other
    cluster_config.cache_clear()


def notice_values(recipient, **overrides):
    return {
        "audience": "member", "member": recipient.pk, "title": "Title", "content": "Body",
        "presentation": "toast", "seconds": 8, "link": "/tasks/", **overrides,
    }


def publish(admin, values):
    preview = inbox.send_notice(admin, values).data
    sent = inbox.send_notice(admin, values, confirmation=preview["confirmation"])
    return sent.identity


def test_panel_notice_delivery_and_reading_are_scoped_to_the_recipient(members):
    admin, member, other = members
    client = Client()
    client.force_login(admin)
    send_url = reverse("send_notice")
    assert client.get(send_url).status_code == 200
    values = notice_values(member)
    preview = client.post(send_url, {**values, "action": "preview"}).context["preview"]
    assert preview["count"] == 1
    response = client.post(send_url, {**values, "action": "send", "confirmation": preview["confirmation"]})
    assert response.status_code == 302
    assert client.get(response.url).status_code == 200
    campaign = Notification.objects.get(event=MANUAL_NOTICE, recipient__isnull=True).pk
    assert deliver_one(in_app=True)
    client.force_login(member)
    assert client.get(reverse("inbox")).status_code == 200
    reminder_url = reverse("inbox_reminders")
    assert client.get(reminder_url).status_code == 405
    first = client.post(reminder_url).json()
    [item] = first["toasts"]
    assert first["unread"] == 1 and item["seconds"] == 8
    assert client.post(reminder_url).json()["toasts"] == []
    assert client.post(reminder_url).json()["unread"] == 1

    notice_url = reverse("notice", args=[item["id"]])
    client.force_login(other)
    assert client.get(notice_url).status_code == 404
    assert client.post(notice_url).status_code == 404
    client.force_login(member)
    assert client.post(notice_url).status_code == 302
    assert client.get(notice_url).status_code == 200
    assert client.post(reminder_url).json()["unread"] == 0
    [record] = inbox.sending_history(admin, query={"origin": "manual"}).data["rows"]
    assert (record["id"], record["delivered"], record["read"]) == (campaign, 1, 1)


def test_cancellation_stops_queued_delivery_and_withdrawal_redacts_the_inbox(members):
    admin, member, other = members
    campaign = publish(admin, notice_values(member, audience="all", presentation="banner", seconds=None))
    assert deliver_one(in_app=True)
    inbox.stop_notice(admin, campaign, "cancel")
    assert not deliver_one(in_app=True)
    header = Notification.objects.get(pk=campaign)
    assert header.state == "canceled"
    users = Notification.objects.filter(payload__campaign=campaign)
    assert users.filter(state="sent").count() == 1
    assert users.filter(state="canceled").count() == 2

    delivered = users.get(state="sent")
    assert inbox.reminders(delivered.recipient).data["banners"][0]["id"] == delivered.pk
    inbox.stop_notice(admin, campaign, "withdraw")
    assert inbox.reminders(delivered.recipient).data == {
        "unread": 0, "toasts": [], "banners": [], "remove": [],
    }
    item = inbox.notice_detail(delivered.recipient, delivered.pk).data["notice"]
    assert item["withdrawn"] and not item["content"] and not item["link"]
    with pytest.raises(NotFound):
        inbox.notice_detail(other, delivered.pk)


def test_confirmation_binds_the_active_recipient_list_and_cannot_send_twice(members):
    admin, member, other = members
    values = notice_values(member, audience="all")
    preview = inbox.send_notice(admin, values).data
    assert preview["count"] == 3
    other.is_active = False
    other.save(update_fields=["is_active"])
    with pytest.raises(ValidationError):
        inbox.send_notice(admin, values, confirmation=preview["confirmation"])
    assert not Notification.objects.exists()
    preview = inbox.send_notice(admin, values).data
    assert preview["count"] == 2
    sent = inbox.send_notice(admin, values, confirmation=preview["confirmation"])
    repeated = inbox.send_notice(admin, values, confirmation=preview["confirmation"])
    assert sent.identity == repeated.identity
    assert Notification.objects.filter(event=MANUAL_NOTICE, recipient__isnull=False).count() == 2
    with pytest.raises(PermissionDenied):
        inbox.send_notice(member, values)
    with pytest.raises(ValidationError):
        inbox.send_notice(admin, {**values, "link": "//example.invalid/"})


def test_expiry_and_dismissal_control_display_without_marking_a_notice_read(members):
    admin, member, _ = members
    now = timezone.now()
    publish(admin, notice_values(member, presentation="banner", seconds=None, expires_at=now + timedelta(minutes=1)))
    assert deliver_one(in_app=True)
    reminders = inbox.reminders(member).data
    [banner] = reminders["banners"]
    inbox.dismiss_banner(member, banner["id"])
    reminders = inbox.reminders(member).data
    assert reminders["banners"] == [] and reminders["unread"] == 1

    publish(admin, notice_values(member, expires_at=now + timedelta(minutes=1)))
    assert deliver_one(in_app=True)
    expired = Notification.objects.filter(recipient=member, state="sent").latest("pk")
    expired.payload["expires_at"] = (now - timedelta(minutes=1)).isoformat()
    expired.save(update_fields=["payload"])
    reminders = inbox.reminders(member, displayed=[expired.pk]).data
    assert reminders["toasts"] == [] and reminders["unread"] == 2
    assert reminders["remove"] == [expired.pk]
    assert len(inbox.inbox(member).data["items"]) == 2


def test_automatic_events_keep_in_app_recipients_and_send_wechat_to_administrators(members):
    admin, member, other = members
    NotificationSettings.objects.create(enabled=True, events=["approve"], receivers={"inapp": {"events": ["approve"]}})
    audit(member, "approve", "approval-1")
    assert set(Notification.objects.values_list("channel", "recipient_id")) == {
        ("inapp", member.pk), ("pushplus", admin.pk),
    }
    assert deliver_one(in_app=True)
    assert len(inbox.reminders(member).data["toasts"]) == 1
    assert inbox.reminders(other).data["unread"] == 0
    with pytest.raises(PermissionDenied):
        set_notification_receiver(member, "pushplus", {"token": "a" * 32})
