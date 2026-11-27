from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client
from django.utils import timezone

from gpu_platform_control.core.events.models import Notification, NotificationSettings
from gpu_platform_control.core.nodes.models import ComputeNode
from gpu_platform_control.core.notifications import MANUAL_NOTICE, deliver_one, mark_node_offline
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


def submission_for(admin):
    return inbox.composer(admin).data["form_options"]["initial"]["submission"]


def publish(admin, values):
    return inbox.send_notice(admin, values, submission=submission_for(admin)).identity


def test_toast_is_shown_once_and_stays_unread_until_the_recipient_reads_it(members):
    admin, member, other = members
    campaign = publish(admin, notice_values(member))
    assert deliver_one(in_app=True)
    client = Client()
    client.force_login(member)
    reminders = "/api/v1/inbox/reminders"
    first = client.post(reminders, {}, content_type="application/json").json()["data"]
    [item] = first["toasts"]
    assert first["unread"] == 1 and item["seconds"] == 8
    again = client.post(reminders, {}, content_type="application/json").json()["data"]
    assert again["toasts"] == [] and again["unread"] == 1

    read = f"/api/v1/inbox/{item['id']}/read"
    client.force_login(other)
    assert client.post(read, {}, content_type="application/json").status_code == 404
    client.force_login(member)
    assert client.post(read, {}, content_type="application/json").status_code == 200
    assert client.post(reminders, {}, content_type="application/json").json()["data"]["unread"] == 0
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


def test_sending_uses_current_active_recipients_and_cannot_send_twice(members):
    admin, member, other = members
    values = notice_values(member, audience="all")
    composer = inbox.composer(admin).data
    assert composer["recipient_counts"]["all"] == 3
    submission = composer["form_options"]["initial"]["submission"]
    other.is_active = False
    other.save(update_fields=["is_active"])
    sent = inbox.send_notice(admin, values, submission=submission)
    repeated = inbox.send_notice(admin, values, submission=submission)
    assert sent.identity == repeated.identity
    assert Notification.objects.filter(event=MANUAL_NOTICE, recipient__isnull=False).count() == 2
    with pytest.raises(PermissionDenied):
        inbox.send_notice(member, values, submission=submission)
    with pytest.raises(ValidationError):
        inbox.send_notice(admin, {**values, "link": "//example.invalid/"}, submission=submission_for(admin))


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


def test_offline_alert_stops_appearing_as_a_toast_after_24_hours(members, monkeypatch):
    admin, _, _ = members
    node = ComputeNode.objects.create(name="worker")
    mark_node_offline(node.pk)
    audit(admin, "approve", "approval-1")
    alert = Notification.objects.get(event="node_offline")
    approval = Notification.objects.get(event="approve")
    deadline = alert.created_at + timedelta(hours=24)
    delivery_time = deadline - timedelta(minutes=5)
    assert deliver_one(clock=lambda: delivery_time, in_app=True)
    assert deliver_one(clock=lambda: delivery_time, in_app=True)
    approval.refresh_from_db()
    assert "expires_at" not in approval.payload

    monkeypatch.setattr(timezone, "now", lambda: deadline)
    reminders = inbox.reminders(admin, displayed=[alert.pk]).data
    assert [toast["id"] for toast in reminders["toasts"]] == [approval.pk]
    assert reminders["remove"] == [alert.pk]
    assert reminders["unread"] == 2
    detail = inbox.notice_detail(admin, alert.pk).data["notice"]
    assert detail["unread"]


def test_api_notice_lifecycle_and_recipient_isolation(members):
    admin, member, other = members
    client = Client()
    client.force_login(admin)
    url = "/api/v1/admin/notices"
    composer = client.get(url + "/composer").json()["data"]
    assert composer["available"] and composer["recipient_counts"]["all"] == 3
    values = notice_values(member, presentation="banner", seconds=None)
    headers = {"content_type": "application/json", "HTTP_IDEMPOTENCY_KEY": "maintenance"}
    assert client.post(url, {**values, "link": "//foreign.invalid/"}, **headers).status_code == 400
    assert not inbox.campaigns().exists()
    sent = client.post(url, values, **headers)
    assert sent.status_code == 201
    campaign = sent.json()["data"]["id"]
    assert sent.json()["data"]["recipients"] == 1
    assert client.post(url, values, **headers)["Idempotency-Replayed"] == "true"
    assert client.post(url, {**values, "title": "Changed"}, **headers).status_code == 409
    assert deliver_one(in_app=True) and not deliver_one(in_app=True)

    api = "/api/v1/inbox"
    client.force_login(member)
    listing = client.get(api, {"page_size": 1}).json()["data"]
    item = listing["items"][0]["id"]
    assert (listing["count"], listing["page_size"], listing["next_page"]) == (1, 1, None)
    detail = f"{api}/{item}"
    assert client.get(detail).json()["data"]["unread"]
    assert (
        client.post(api + "/reminders", {}, content_type="application/json").json()["data"]["banners"][0][
            "id"
        ]
        == item
    )
    assert client.post(detail + "/dismiss", {}, content_type="application/json").json()["data"]["dismissed"]
    assert (
        client.post(api + "/reminders", {}, content_type="application/json").json()["data"]["banners"] == []
    )
    assert not client.post(detail + "/read", {}, content_type="application/json").json()["data"]["unread"]
    client.force_login(other)
    assert client.get(api).json()["data"]["count"] == 0
    assert (
        client.get(detail).status_code
        == client.post(detail + "/read", {}, content_type="application/json").status_code
        == client.post(detail + "/dismiss", {}, content_type="application/json").status_code
        == 404
    )

    client.force_login(admin)
    history = client.get(url + "/history", {"origin": "manual", "page_size": 1}).json()["data"]
    assert history["items"][0]["read"] == history["items"][0]["delivered"] == history["count"] == 1
    assert client.get(url + "/history", {"origin": "automatic"}).json()["data"]["count"] == 0
    assert client.post(
        f"{url}/{campaign}/stop", {"action": "withdraw"}, content_type="application/json"
    ).json()["data"]["stopped"]
    queued = client.post(url, values, content_type="application/json").json()["data"]["id"]
    assert (
        client.post(f"{url}/{queued}/stop", {"action": "cancel"}, content_type="application/json").status_code
        == 200
    )
    assert not deliver_one(in_app=True)
    client.force_login(member)
    assert client.get(detail).json()["data"]["withdrawn"]
    assert client.post(api + "/reminders", {"shown": [item]}, content_type="application/json").json()["data"][
        "remove"
    ] == [item]


def test_api_inapp_event_selection_controls_delivery(members):
    admin, member, _ = members
    client = Client()
    client.force_login(admin)
    url = "/api/v1/admin/notifications"
    settings = client.get(url).json()["data"]
    assert settings["inapp_enabled"] and settings["wechat_available"]
    assert (
        client.patch(url, {"inapp_events": ["unregistered"]}, content_type="application/json").status_code
        == 400
    )
    updated = client.patch(url, {"inapp_events": ["approve"]}, content_type="application/json")
    assert updated.json()["data"]["inapp_events"] == ["approve"]
    audit(member, "reject", "task")
    audit(member, "approve", "task")
    assert list(Notification.objects.filter(recipient=member).values_list("event", flat=True)) == ["approve"]
