from pathlib import PurePosixPath
from urllib.parse import urlencode
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import StreamingHttpResponse, HttpResponseBadRequest
from django.core.exceptions import ValidationError
from .adapter import errors_as_http
from django.shortcuts import render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_safe
from gpu_platform_control.services.datasets import browse, Download
from django.utils.http import content_disposition_header


@login_required
@require_safe
@errors_as_http
def browse_datasets(request, management=False):
    path = PurePosixPath(request.GET.get("path", ""))
    relative = "" if str(path) == "." else str(path)
    query = request.GET.get("q", "").strip()
    try:
        result = browse(
            request.user,
            relative=relative,
            query=query,
            node_id=request.GET.get("node"),
            management=management,
        )
    except ValidationError as error:
        if getattr(error, "code", None) in {"feature_disabled", "node_disabled"}:
            return HttpResponseBadRequest("; ".join(error.messages), content_type="text/plain; charset=utf-8")
        raise
    content, node = result["content"], result["node"]
    if isinstance(content, Download):
        response = StreamingHttpResponse(content.chunks, content_type="application/octet-stream")
        response["Content-Disposition"] = content_disposition_header(True, content.filename)
        response["Content-Length"] = content.size_bytes
        return response
    endpoint = reverse("manage_datasets" if management else "datasets")

    def url(value):
        return (
            endpoint + "?" + urlencode({"node": node["id"], "path": "" if str(value) == "." else str(value)})
        )

    for entry in content:
        entry["url"] = url(entry["path"]) if entry["kind"] != "link" else None
    crumbs = [{"name": node["name"] + _(" public data"), "url": url("")}]
    for index, part in enumerate(path.parts):
        crumbs.append({"name": part, "url": url(PurePosixPath(*path.parts[: index + 1]))})
    return render(
        request,
        "portal/datasets.html",
        {
            "base_template": "portal/manage_base.html" if management else "portal/base.html",
            "section": "datasets",
            "section_title": _("Public datasets"),
            "management": management,
            "page": Paginator(content, 100).get_page(request.GET.get("page")),
            "node": node,
            "nodes": result["nodes"],
            "query": query,
            "relative": relative,
            "breadcrumbs": crumbs,
            "parent_url": url(path.parent) if path.parts else None,
        },
    )
