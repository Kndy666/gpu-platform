"""HTTP operations for panel catalogs, administrator reports and notices."""

import hashlib
from uuid import uuid4

from . import administration, catalog, inbox, pages, usage
from .commands import timestamp
from .operations import body, endpoint, page, success


@endpoint("GET")
def request_options(request):
    data = pages.new_request(
        request.user, request.GET.get("mode", "interactive"), query=request.GET.dict()
    ).data
    return success({**data["form_options"], "capacity": data["capacity"]})


@endpoint("GET")
def build_options(request):
    return success(catalog.build_options(request.user))


@endpoint("GET")
def sync_options(request):
    from .synchronization import synchronization_options

    options = synchronization_options(request.user)
    return success(
        {
            **options,
            "nodes": [{"id": n.pk, "name": n.name, "architecture": n.architecture} for n in options["nodes"]],
            "images": list(options["images"].values("id", "name", "node_id", "architecture", "shared")),
        }
    )


@endpoint("GET", staff=True)
def member_options(request):
    return success(catalog.member_options(request.user, request.GET.get("member_id")))


@endpoint("GET", staff=True)
def group_options(request):
    return success(catalog.group_options(request.user, request.GET.get("group_id")))


@endpoint("GET", staff=True)
def node_options(request):
    return success(
        administration.node_options(
            request.user, request.GET.get("node_id"), transport=request.GET.get("transport")
        )
    )


@endpoint("GET", staff=True)
def performance_history(request):
    query = dict(request.GET.lists())
    if "nodes" in query:
        query["nodes"] = [pk.strip() for value in query["nodes"] for pk in value.split(",")]
    return success(administration.performance_history(request.user, query=query).data)


@endpoint("GET", staff=True)
def usage_live(request):
    return success(usage.current_usage(request.user).data)


@endpoint("GET", staff=True)
def usage_history(request):
    return success(usage.usage_statistics(request.user, query=request.GET.dict()).data)


@endpoint("GET")
def inbox_list(request):
    return page(request, inbox.inbox_for(request.user).order_by("-sent_at", "-pk"), inbox.inbox_item)


@endpoint("GET")
def inbox_detail(request, pk):
    return success(inbox.notice_detail(request.user, pk).data["notice"])


@endpoint("POST")
def inbox_read(request, pk):
    return success(inbox.read_notice(request.user, pk).data["notice"])


@endpoint("POST")
def inbox_dismiss(request, pk):
    inbox.dismiss_banner(request.user, pk)
    return success({"dismissed": True})


@endpoint("POST")
def inbox_reminders(request):
    return success(inbox.reminders(request.user, displayed=body(request, {"shown"}).get("shown", [])).data)


@endpoint("GET", staff=True)
def notice_composer(request):
    data = inbox.composer(request.user).data
    return success(
        {
            "available": data["available"],
            "recipient_counts": data["recipient_counts"],
            **{key: data["form_options"][key] for key in ("members", "groups", "audiences", "presentations")},
        }
    )


@endpoint("POST", staff=True, idempotent=True)
def notice_create(request):
    values = body(
        request,
        {"audience", "member", "group", "title", "content", "link", "presentation", "seconds", "expires_at"},
    )
    values["expires_at"] = timestamp(values.get("expires_at"))
    key = request.headers.get("Idempotency-Key") or str(uuid4())
    event_key = hashlib.sha256(f"{request.user.pk}:{key}".encode()).hexdigest()
    campaign = inbox.create_campaign(request.user, values, "api-notice:" + event_key)
    return success({"id": campaign.pk, "recipients": campaign.payload["count"]}, 201)


@endpoint("GET", staff=True)
def notice_history(request):
    return page(
        request, inbox.history_items(request.GET.get("origin")), None, serialize_page=inbox.history_rows
    )


@endpoint("POST", staff=True)
def notice_stop(request, pk):
    action = body(request, {"action"})["action"]
    inbox.stop_notice(request.user, pk, action)
    return success({"stopped": True, "action": action})
