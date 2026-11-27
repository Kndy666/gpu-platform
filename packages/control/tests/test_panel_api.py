from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.utils import timezone

from gpu_platform_control.core.api_auth import issue_token
from gpu_platform_control.core.nodes.models import ComputeNode
from gpu_platform_control.core.requests.models import ExtensionRequest, ResourceRequest
from gpu_platform_control.core.retention.models import ImageArtifact
from gpu_platform_control.core.tasks.models import Workspace

pytestmark = pytest.mark.django_db
API = "/api/v1/"
JSON = "application/json"


@pytest.fixture
def member():
    return get_user_model().objects.create_user("sam", password="old-passphrase")


@pytest.fixture
def staff():
    return get_user_model().objects.create_user("operator", is_staff=True, password="old-passphrase")


def api_client(user):
    return Client(enforce_csrf_checks=True, HTTP_AUTHORIZATION="Bearer " + issue_token(user)["token"])


def test_browser_session_lifecycle(member):
    browser = Client(enforce_csrf_checks=True)
    credentials = {"username": member.username, "password": "old-passphrase"}
    sign_in = API + "auth/session"
    csrf = browser.get(API + "auth/csrf").json()["data"]["csrf_token"]
    assert browser.post(sign_in, credentials, content_type=JSON).json()["error"]["code"] == "csrf_failed"
    browser.defaults["HTTP_X_CSRFTOKEN"] = csrf
    assert (
        browser.post(sign_in, {**credentials, "password": "incorrect"}, content_type=JSON).status_code == 401
    )
    response = browser.post(sign_in, credentials, content_type=JSON)
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["csrf_token"] != csrf
    assert data["user"] == browser.get(API + "me").json()["data"]
    assert data["user"]["pending_approval_count"] == 0
    assert set(data["user"]["features"]) == {"public_datasets", "synchronization", "notifications"}
    for header in ("Bearer invalid", "Basic invalid", ""):
        denied = browser.get(API + "me", HTTP_AUTHORIZATION=header)
        assert denied.status_code == 401 and denied["WWW-Authenticate"] == "Bearer"
    assert browser.post(API + "auth/tokens", {}, content_type=JSON).status_code == 403
    browser.defaults["HTTP_X_CSRFTOKEN"] = data["csrf_token"]
    token = browser.post(API + "auth/tokens", {}, content_type=JSON).json()["data"]
    assert browser.get(API + "auth/tokens").json()["data"]["items"][0]["current"] is False
    assert browser.get(API + "auth/tokens/" + token["id"]).json()["data"]["token"] == token["token"]
    assert browser.delete(API + "auth/token").status_code == 401
    denied = browser.delete(API + "auth/tokens/" + token["id"], HTTP_ORIGIN="https://foreign.invalid")
    assert denied.status_code == 403 and denied.json()["error"]["code"] == "csrf_failed"
    rotated = {
        "old_password": credentials["password"],
        "new_password1": "changed-passphrase",
        "new_password2": "changed-passphrase",
    }
    assert (
        browser.post(API + "me/password", rotated, content_type=JSON).json()["data"][
            "reauthentication_required"
        ]
        is False
    )
    assert browser.get(API + "me").json()["data"]["id"] == member.pk
    assert Client(HTTP_AUTHORIZATION="Bearer " + token["token"]).get(API + "me").status_code == 401
    assert browser.delete(sign_in, HTTP_X_CSRFTOKEN="").status_code == 403
    assert browser.delete(sign_in).json()["data"]["signed_out"]
    assert browser.get(API + "me").status_code == 401


def test_admin_password_edit_keeps_only_own_browser_session(staff):
    browser = Client(enforce_csrf_checks=True)
    browser.force_login(staff)
    other = Client()
    other.force_login(staff)
    csrf = browser.get(API + "auth/csrf").json()["data"]["csrf_token"]
    response = browser.patch(
        API + f"admin/members/{staff.pk}",
        {"password": "rotated-passphrase"},
        content_type=JSON,
        HTTP_X_CSRFTOKEN=csrf,
    )
    assert response.status_code == 200
    assert browser.get(API + "me").json()["data"]["id"] == staff.pk
    assert other.get(API + "me").status_code == 401


@pytest.fixture
def environment(member):
    now = timezone.now()
    node = ComputeNode.objects.create(
        name="remote",
        architecture="amd64",
        state="online",
        capabilities={"features": {"save_environment": True, "image_sync": True}},
    )
    image = ImageArtifact.objects.create(
        node=node, owner=member, name="base", architecture="amd64", reference="base", verified=True
    )
    task = ResourceRequest.objects.create(
        node=node,
        user=member,
        image=image,
        purpose="training",
        mode="interactive",
        status="running",
        starts_at=now - timedelta(hours=1),
        ends_at=now + timedelta(hours=4),
    )
    Workspace.objects.create(request=task, started_at=now)
    return task


def test_form_options_follow_selected_source_and_actor(member, staff, environment):
    client = api_client(member)
    temporary = ImageArtifact.objects.create(
        node=environment.node, owner=member, name="conversion", reference="conversion", verified=True,
        temporary=True,
    )
    for field, source in (("source", environment.pk), ("image", environment.image_id)):
        options = client.get(API + "request-options", {"mode": "batch", field: str(source)}).json()["data"]
        assert options["initial"]["node"] == options["node"]["id"] == environment.node_id
        assert options["initial"]["source_kind"] == ("environment" if field == "source" else "image")
        assert "capacity" in options and options["defaults"]["mode"] == "batch"
        assert options["defaults"]["image"] == str(environment.image_id)
        assert {image["id"] for image in options["images"]} == {str(environment.image_id)}
    assert client.get(
        API + "request-options", {"mode": "batch", "image": str(temporary.pk)}
    ).status_code == 404
    assert client.get(API + "builds/options").json()["data"]["nodes"] == []
    sync = client.get(API + "syncs/options").json()["data"]
    assert sync["can_sync_images"] and not sync["can_sync_datasets"]
    assert {image["id"] for image in sync["images"]} == {str(environment.image_id)}
    admin = api_client(staff)
    assert (
        admin.get(API + "admin/members/options", {"member_id": member.pk}).json()["data"]["member"]["id"]
        == member.pk
    )
    group = member.profile.group_id
    assert admin.get(API + "admin/groups/options", {"group_id": group}).json()["data"]["group"]["id"] == group
    node = admin.get(
        API + "admin/nodes/options", {"node_id": environment.node_id, "transport": "local"}
    ).json()["data"]
    assert node["editing"] and node["initial"]["transport"] == "local"


def test_task_time_permissions_history_and_idempotency(member, staff, environment):
    task = environment
    entry = ExtensionRequest.objects.create(
        request=task,
        previous_ends_at=task.ends_at,
        ends_at=task.ends_at + timedelta(hours=1),
        reason="analysis",
    )
    client, admin = api_client(member), api_client(staff)
    path = API + f"tasks/{task.pk}"
    detail = client.get(path).json()["data"]
    assert detail["can_shorten_time"] and not detail["can_adjust_time"] and not detail["can_extend"]
    assert detail["can_save_environment"] and detail["node_state"] == "online"
    assert detail["extension_history"] == [detail["pending_extension"]]
    queue = admin.get(API + "admin/approvals", {"kind": "extensions"}).json()["data"]["items"]
    assert [(row["id"], row["request_purpose"], row["username"], row["node_id"]) for row in queue] == [
        (str(entry.pk), task.purpose, member.username, task.node_id)
    ]
    assert admin.get(path).json()["data"]["can_adjust_time"]
    stranger = api_client(get_user_model().objects.create_user("another"))
    assert stranger.get(path).status_code == 404
    shorter = {"ends_at": (task.ends_at - timedelta(hours=1)).isoformat()}
    assert stranger.post(path + "/shorten", shorter, content_type=JSON).status_code == 404
    assert admin.post(path + "/shorten", shorter, content_type=JSON).status_code == 404
    for client_, operation, payload in (
        (client, "shorten", shorter),
        (
            admin,
            "review",
            {"action": "adjust_time", "ends_at": task.ends_at.isoformat(), "reason": "approved"},
        ),
    ):
        first = client_.post(
            path + "/" + operation, payload, content_type=JSON, HTTP_IDEMPOTENCY_KEY="reschedule"
        )
        repeat = client_.post(
            path + "/" + operation, payload, content_type=JSON, HTTP_IDEMPOTENCY_KEY="reschedule"
        )
        assert first.status_code == repeat.status_code == 200
        assert repeat["Idempotency-Replayed"] == "true"
        assert first.json() == repeat.json()
        task.refresh_from_db()
        assert task.ends_at.isoformat() == payload["ends_at"]
    assert first.json()["data"]["pending_extension"] is None
    assert first.json()["data"]["extension_history"][0]["status"] == "superseded"
    assert (
        admin.post(path + "/review", {"action": "adjust_time", **shorter}, content_type=JSON).status_code
        == 400
    )


def test_administrator_reports_use_selected_servers_and_dates(member, staff, environment):
    admin = api_client(staff)
    second = ComputeNode.objects.create(name="second")
    third = ComputeNode.objects.create(name="third")
    for values in ([str(environment.node_id), str(second.pk)], f"{environment.node_id},{second.pk}"):
        response = admin.get(
            API + "admin/performance/history", {"nodes": values, "days": 7, "metric": "memory"}
        )
        history = response.json()["data"]
        assert history["history_metric"] == "memory" and history["history"]["days"] == 7
        assert {n["id"] for n in history["history"]["nodes"]} == {environment.node_id, second.pk}
        assert next(n for n in history["history_nodes"] if n["id"] == third.pk)["selected"] is False
    performance = admin.get(API + "admin/performance").json()["data"]
    assert {"state", "used_mib", "max_mib", "last_at"} <= performance["archive"].keys()
    assert admin.get(API + "admin/usage/live").json()["data"]["running_count"] == 1
    date = timezone.localdate().isoformat()
    filters = {"from": date, "to": date, "account": str(member.pk), "node": str(environment.node_id)}
    history = admin.get(API + "admin/usage/history", filters).json()["data"]
    assert history["filters"] == filters and history["totals"]["applications"] == 1
    assert (
        admin.get(
            API + "admin/usage/history", {**filters, "from": "2030-10-10", "to": "2029-10-10"}
        ).status_code
        == 400
    )


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "admin/members/options"),
        ("get", "admin/groups/options"),
        ("get", "admin/nodes/options"),
        ("get", "admin/notifications"),
        ("patch", "admin/notifications"),
        ("get", "admin/usage/live"),
        ("get", "admin/usage/history"),
        ("get", "admin/performance"),
        ("get", "admin/performance/history"),
        ("get", "admin/notices/composer"),
        ("post", "admin/notices"),
        ("get", "admin/notices/history"),
        ("post", "admin/notices/1/stop"),
        ("post", "tasks/00000000-0000-0000-0000-000000000001/review"),
        ("post", "nodes"),
        ("patch", "nodes/1"),
    ],
)
def test_staff_routes_require_staff(member, method, path):
    assert getattr(api_client(member), method)(API + path).status_code == 403


def test_api_registers_and_updates_remote_server(tmp_path, settings, monkeypatch, staff):
    import yaml
    from gpu_platform_control.slots.configuration import cluster_config

    settings.PLATFORM_CLUSTER_CONFIG = None
    path = tmp_path / "cluster.yaml"
    path.write_text(yaml.safe_dump({"hosts": {"local": {"roles": ["control", "node"]}}}))
    monkeypatch.setenv("PLATFORM_CONFIG", str(path))
    admin = api_client(staff)
    values = {
        "name": "worker",
        "address": "worker.invalid",
        "transport": "reverse-ssh",
        "architecture": "amd64",
        "transport_options": {"host": "worker.invalid", "ssh_port": 2229, "identity": "test"},
    }
    registered = admin.post(API + "nodes", values, content_type=JSON)
    assert registered.status_code == 201
    server = registered.json()["data"]
    assert server["enabled"] and not server["maintenance"]
    url = API + f"nodes/{server['id']}"
    assert (
        admin.patch(
            url, {"transport_options": {"ssh_port": 2228}, "maintenance": True}, content_type=JSON
        ).status_code
        == 200
    )
    host = cluster_config().hosts["worker"]
    assert host.address == "worker.invalid"
    assert host.slots["transport"].options["ssh_port"] == 2228
    assert host.slots["transport"].options["identity"] == "test"
    before = path.read_bytes()
    assert admin.patch(url, {"transport_options": {"ssh_port": -1}}, content_type=JSON).status_code == 400
    assert admin.patch(url, {"enabled": False}, content_type=JSON).json()["data"]["maintenance"]
    assert path.read_bytes() == before


def test_datasets_choose_enabled_servers_and_stream_downloads(member, monkeypatch):
    from gpu_platform_control.core.datasets import Download

    node = ComputeNode.objects.create(name="data", capabilities={"features": {"public_datasets": True}})
    ComputeNode.objects.create(name="disabled", enabled=False, capabilities=node.capabilities)
    ComputeNode.objects.create(name="other", capabilities={})
    monkeypatch.setattr(
        "gpu_platform_control.services.datasets.dataset_content",
        lambda relative, query, node: [{"name": "sample", "path": "sample"}],
    )
    monkeypatch.setattr(
        "gpu_platform_control.services.operations.dataset_download",
        lambda path, node: Download(path, 4, iter([b"data"])),
    )
    session = Client(enforce_csrf_checks=True)
    session.force_login(member)
    for client in (session, api_client(member)):
        listing = client.get(API + "datasets").json()["data"]
        assert listing["nodes"] == [listing["node"]] == [{"id": node.pk, "name": node.name}]
        assert listing["count"] == 1
        response = client.get(API + "datasets/download", {"path": "sample"})
        assert response.status_code == 200 and b"".join(response.streaming_content) == b"data"


def test_openapi_describes_browser_security_and_api_contracts():
    schema = Client().get(API + "openapi.json").json()
    paths = schema["paths"]
    password = paths[API + "me/password"]["post"]
    assert password["security"] == [{"BearerAuth": []}, {"SessionAuth": []}]
    assert {p["name"] for p in password["parameters"]} >= {"X-CSRFToken"}
    assert paths[API + "auth/token"]["delete"]["security"] == [{"BearerAuth": []}]
    login = paths[API + "auth/session"]["post"]
    assert not login.get("security")
    assert next(p for p in login["parameters"] if p["name"] == "X-CSRFToken")["required"]
    assert {p["name"] for p in paths[API + "admin/usage/history"]["get"]["parameters"]} == {
        "from",
        "to",
        "model",
        "node",
        "account",
    }
    notice = paths[API + "admin/notices"]["post"]
    assert {p["name"] for p in notice["parameters"]} >= {"Idempotency-Key"}
    assert "201" in notice["responses"] and notice["requestBody"]["required"]
    image_path = API + "images/{pk}"
    assert set(paths[image_path]) == {"get", "delete"}
    assert set(path for path in paths if path.startswith(image_path + "/")) == {image_path + "/sync"}
    delete = paths[image_path]["delete"]
    assert {p["name"] for p in delete["parameters"]} >= {"Idempotency-Key", "X-CSRFToken"}
    assert "requestBody" not in delete
    assert {status for status in delete["responses"] if status.startswith("2")} == {"200", "202"}
    image_schema = paths[image_path]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    for status in ("200", "202"):
        assert delete["responses"][status]["content"]["application/json"]["schema"] == image_schema
    assert "temporary" not in schema["components"]["schemas"]["ImageData"]["properties"]


def test_public_health_endpoints_report_worker_freshness():
    from gpu_platform_control.core.nodes.models import SystemObservation

    client = Client()
    for status in (503, 200):
        if status == 200:
            SystemObservation.objects.create(name="reconciler", data={})
        probe = client.get("/healthz/")
        api = client.get(API + "health")
        assert probe.status_code == api.status_code == status
        assert probe.json() == api.json()["data"]
