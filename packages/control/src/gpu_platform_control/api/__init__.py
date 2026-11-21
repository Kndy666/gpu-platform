"""Typed HTTP operations with generated OpenAPI and shared service authorization."""

import inspect
import json
import re
from typing import cast
from uuid import UUID

from django.http import HttpResponse, JsonResponse
from django.utils.translation import gettext as _
from ninja import Body, Header, NinjaAPI, Query, Status
from ninja.errors import HttpError, ValidationError
from ninja.security import HttpBearer
from pydantic import BaseModel

from gpu_platform_control.services import operations

from .routes import ROUTES
from .schemas import (
    MODELS,
    BuildData,
    Envelope,
    Error,
    GroupData,
    ImageData,
    Page,
    Response,
    SyncData,
    TaskData,
    UserData,
    VolumeData,
)
from . import schemas

api = NinjaAPI(
    title="Resource control API",
    version="1.0",
    urls_namespace="api-v1",
    openapi_url="/openapi.json",
    docs_url="/docs",
)


class BearerAuth(HttpBearer):
    def authenticate(self, request, token):
        user = operations.bearer_user(request)
        if user is None:
            raise HttpError(401, "unauthorized")
        request.user = user
        return user


@api.exception_handler(HttpError)
def http_error(request, exc):
    code = {401: "unauthorized", 403: "permission_denied", 404: "not_found", 405: "method_not_allowed"}.get(
        exc.status_code, "http_error"
    )
    messages = {
        401: _("Provide a valid access token."),
        403: _("This operation requires additional permissions."),
        404: _("The requested item is unavailable."),
        405: _("This HTTP method is unavailable for this operation."),
    }
    response = api.create_response(
        request,
        {
            "error": {
                "code": code,
                "message": messages.get(exc.status_code, _("The request could not be processed.")),
            }
        },
        status=exc.status_code,
    )
    if exc.status_code == 401:
        response["WWW-Authenticate"] = "Bearer"
    response["Cache-Control"] = "no-store"
    return response


@api.exception_handler(ValidationError)
def invalid_input(request, exc):
    fields: dict[str, list[str]] = {}
    for error in exc.errors:
        location = [str(value) for value in error["loc"] if value not in {"body", "payload"}]
        fields.setdefault(".".join(location), []).append(error["type"])
    return api.create_response(
        request,
        {"error": {"code": "validation_error", "message": _("Check the request fields."), "fields": fields}},
        status=400,
    )


RESPONSES = {
    "gpus": "GPUInventoryResponse",
    "resources": "ResourcesResponse",
    "timeline": "TimelineResponse",
    "performance": "PerformanceResponse",
    "connection": "ConnectionResponse",
    "node": "NodeResponse",
    "nodes": "NodeListResponse",
}

DATA_RESPONSES = {
    "me": Envelope[UserData],
    "member": Envelope[UserData],
    "members": Envelope[Page[UserData]],
    "group": Envelope[GroupData],
    "groups": Envelope[Page[GroupData]],
    "task": Envelope[TaskData],
    "tasks": Envelope[Page[TaskData]],
    "image": Envelope[ImageData],
    "images": Envelope[Page[ImageData]],
    "build": Envelope[BuildData],
    "builds": Envelope[Page[BuildData]],
    "volume": Envelope[VolumeData],
    "volumes": Envelope[Page[VolumeData]],
    "jobs": Envelope[Page[SyncData]],
}

QUERIES = {
    "resources": schemas.NodeQuery,
    "gpus": schemas.NodeQuery,
    "performance": schemas.NodeQuery,
    "timeline": schemas.TimelineQuery,
    "tasks": schemas.TaskQuery,
    "containers": schemas.ContainerQuery,
    "images": schemas.ImageQuery,
    "datasets": schemas.DatasetQuery,
    "download": schemas.DownloadQuery,
    "logs": schemas.LogQuery,
    "members": schemas.SearchQuery,
    "approval_queue": schemas.ApprovalQuery,
    "audits": schemas.AuditQuery,
    **{
        name: schemas.NodePageQuery
        for name in ("snapshots", "builds", "volumes", "storage", "managed_images")
    },
    **{name: schemas.PageQuery for name in ("nodes", "groups", "jobs")},
}


def _register(route, handler, summary, schema):
    parameters = [(converter or "str", name) for converter, name in re.findall(r"<(?:(\w+):)?(\w+)>", route)]
    path = "/" + re.sub(r"<(?:(\w+):)?(\w+)>", lambda match: "{" + match[2] + "}", route)
    for method in handler.api_methods:

        def operation(request, response, _handler=handler, **values):
            values.pop("payload", None)
            values.pop("idempotency_key", None)
            query = values.pop("query", None)
            if query is not None:
                request.GET = request.GET.copy()
                for key, value in query.model_dump(
                    mode="json", exclude_none=True, exclude_unset=True
                ).items():
                    request.GET[key] = str(value).lower() if isinstance(value, bool) else str(value)
            result = _handler(request, **values)
            if isinstance(result, JsonResponse):
                for key, value in result.items():
                    if key.lower() not in {"content-length", "content-type"}:
                        response[key] = value
                response.cookies.update(result.cookies)
                return Status(result.status_code, json.loads(result.content))
            return result

        operation.__name__ = handler.__name__ + "_" + method.lower()
        signature = [
            inspect.Parameter("request", inspect.Parameter.POSITIONAL_OR_KEYWORD),
            inspect.Parameter("response", inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=HttpResponse),
        ]
        signature += [
            inspect.Parameter(
                name,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                annotation={"int": int, "uuid": UUID}.get(converter, str),
            )
            for converter, name in parameters
        ]
        request_schema = "GroupDelete" if handler.__name__ == "group" and method == "DELETE" else schema
        if method == "GET" and handler.__name__ in QUERIES:
            signature.append(
                inspect.Parameter(
                    "query",
                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                    annotation=QUERIES[handler.__name__],
                    default=Query(...),
                )
            )
        if request_schema and (method in {"POST", "PUT", "PATCH"} or request_schema == "GroupDelete"):
            signature.append(
                inspect.Parameter(
                    "payload",
                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                    annotation=MODELS[request_schema],
                    default=Body(None),
                )
            )
        if handler.api_idempotent and method not in {"GET", "HEAD"}:
            signature.append(
                inspect.Parameter(
                    "idempotency_key",
                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                    annotation=str | None,
                    default=Header(None, alias="Idempotency-Key"),
                )
            )
        setattr(operation, "__signature__", inspect.Signature(signature))
        model: type[BaseModel] = (
            MODELS[RESPONSES[handler.__name__]]
            if method == "GET" and handler.__name__ in RESPONSES
            else Response
        )
        if method == "GET" and handler.__name__ in DATA_RESPONSES:
            model = cast(type[BaseModel], DATA_RESPONSES[handler.__name__])
        if handler.__name__ in {"image_sync", "dataset_sync"}:
            model = Envelope[SyncData]
        responses = {status: model for status in (200, 201, 202, 204)}
        responses.update(
            {status: Error for status in (400, 401, 403, 404, 405, 409, 413, 415, 422, 500, 503)}
        )
        api.api_operation(
            [method],
            path,
            summary=summary,
            response=responses,
            exclude_unset=True,
            auth=None if method in handler.api_public else BearerAuth(),
            operation_id=operation.__name__,
            url_name="api_" + handler.__name__,
        )(operation)


for route, handler, summary, schema in ROUTES:
    _register(route, handler, summary, schema)
