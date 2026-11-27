"""Typed HTTP operations with generated OpenAPI and shared service authorization."""

import inspect
import json
import re
from typing import Any, cast
from uuid import UUID

from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.utils.translation import gettext as _
from ninja import Body, Header, NinjaAPI, Query, Status
from ninja.errors import HttpError, ValidationError
from ninja.security import APIKeyCookie, HttpBearer
from ninja.utils import check_csrf
from pydantic import BaseModel

from gpu_platform_control.core.api_auth import bearer_user
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
    def __init__(self, staff=False):
        super().__init__()
        self.staff = staff

    def authenticate(self, request, token):
        user = bearer_user(request)
        if user is None:
            raise HttpError(401, "unauthorized")
        if self.staff and not user.is_staff:
            raise HttpError(403, "permission_denied")
        request.user = user
        return user


def require_csrf(request):
    if check_csrf(request) is not None:
        raise HttpError(403, "csrf_failed")
    return True


def login_csrf(request):
    if "Authorization" in request.headers and bearer_user(request) is None:
        raise HttpError(401, "unauthorized")
    return require_csrf(request)


class SessionAuth(APIKeyCookie):
    param_name = settings.SESSION_COOKIE_NAME

    def __init__(self, staff=False):
        super().__init__(csrf=False)
        self.staff = staff

    def authenticate(self, request, key):
        if (
            "Authorization" in request.headers
            or not request.user.is_authenticated
            or not request.user.is_active
        ):
            raise HttpError(401, "unauthorized")
        require_csrf(request)
        if self.staff and not request.user.is_staff:
            raise HttpError(403, "permission_denied")
        return request.user


@api.exception_handler(HttpError)
def http_error(request, exc):
    code = {401: "unauthorized", 403: "forbidden", 404: "not_found", 405: "method_not_allowed"}.get(
        exc.status_code, "http_error"
    )
    messages = {
        401: _("Sign in or provide a valid access token."),
        403: _("This operation requires additional permissions."),
        404: _("The requested item is unavailable."),
        405: _("This HTTP method is unavailable for this operation."),
    }
    if str(exc) == "csrf_failed":
        code = "csrf_failed"
        messages[403] = _("The security check failed. Refresh the page and try again.")
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
    "me": Envelope[schemas.ProfileData],
    "datasets": Envelope[schemas.DatasetPage],
    "extensions": Envelope[Page[schemas.ExtensionData]],
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

OPERATION_RESPONSES = {
    "csrf": Envelope[schemas.CsrfData],
    "health": Envelope[schemas.HealthData],
    "session": Envelope[schemas.SessionData],
    "extension_review": Envelope[schemas.ExtensionData],
    "request_options": Envelope[schemas.RequestOptionsData],
    "build_options": Envelope[schemas.BuildOptionsData],
    "sync_options": Envelope[schemas.SyncOptionsData],
    "member_options": Envelope[schemas.MemberOptionsData],
    "group_options": Envelope[schemas.GroupOptionsData],
    "node_options": Envelope[schemas.NodeOptionsData],
    "shorten": Envelope[TaskData],
    "review": Envelope[TaskData],
    "inbox_list": Envelope[Page[schemas.InboxItem]],
    "inbox_detail": Envelope[schemas.InboxItem],
    "inbox_read": Envelope[schemas.InboxItem],
    "inbox_reminders": Envelope[schemas.RemindersData],
    "inbox_dismiss": Envelope[schemas.DismissData],
    "notice_composer": Envelope[schemas.ComposerData],
    "notice_create": Envelope[schemas.NoticeCreated],
    "notice_history": Envelope[Page[schemas.NoticeHistoryItem]],
    "notice_stop": Envelope[schemas.NoticeStopped],
    "notification_settings": Envelope[schemas.NotificationSettingsData],
    "performance_history": Envelope[schemas.PerformanceHistoryData],
    "usage_live": Envelope[schemas.LiveUsageData],
    "usage_history": Envelope[schemas.UsageHistoryData],
}


QUERIES = {
    "request_options": schemas.RequestOptionsQuery,
    "member_options": schemas.MemberOptionsQuery,
    "group_options": schemas.GroupOptionsQuery,
    "node_options": schemas.NodeOptionsQuery,
    "performance_history": schemas.PerformanceHistoryQuery,
    "usage_history": schemas.UsageQuery,
    "inbox_list": schemas.PageQuery,
    "notice_history": schemas.NoticeHistoryQuery,
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
            payload = values.pop("payload", None)
            if payload is not None:
                request.validated_payload = payload.model_dump(mode="json", exclude_unset=True)
            values.pop("csrf_header", None)
            values.pop("idempotency_key", None)
            query = values.pop("query", None)
            if query is not None:
                request.GET = request.GET.copy()
                for key, value in query.model_dump(
                    mode="json", by_alias=True, exclude_none=True, exclude_unset=True
                ).items():
                    if isinstance(value, list):
                        request.GET.setlist(key, value)
                        continue
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
            payload_type = MODELS[request_schema]
            required = any(field.is_required() for field in payload_type.model_fields.values())
            signature.append(
                inspect.Parameter(
                    "payload",
                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                    annotation=payload_type,
                    default=Body(... if required else None),
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
        browser_login = handler is operations.session and method == "POST"
        if method in {"POST", "PUT", "PATCH", "DELETE"} and handler is not operations.token:
            if browser_login or method not in handler.api_public:
                signature.append(
                    inspect.Parameter(
                        "csrf_header",
                        inspect.Parameter.POSITIONAL_OR_KEYWORD,
                        annotation=str if browser_login else str | None,
                        default=Header(
                            ... if browser_login else None,
                            alias="X-CSRFToken",
                            description="Required for browser sessions. Use the token from /auth/csrf or /auth/session.",
                        ),
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
        if handler.__name__ in OPERATION_RESPONSES:
            model = cast(type[BaseModel], OPERATION_RESPONSES[handler.__name__])
            if handler is operations.session and method == "DELETE":
                model = Envelope[schemas.SignedOutData]
            responses = {201 if handler.__name__ == "notice_create" else 200: model}
        if handler is operations.image and method == "DELETE":
            responses = {200: Envelope[ImageData], 202: Envelope[ImageData]}
        responses.update(
            {status: Error for status in (400, 401, 403, 404, 405, 409, 413, 415, 422, 500, 503)}
        )
        if handler is operations.health:
            responses[503] = Envelope[schemas.HealthData]
        staff = handler.api_staff or (handler in {operations.nodes, operations.node} and method != "GET")
        auth: Any = [BearerAuth(staff), SessionAuth(staff)]
        if method in handler.api_public:
            auth = login_csrf if handler is operations.session and method == "POST" else None
        elif handler is operations.token:
            auth = [BearerAuth()]
        api.api_operation(
            [method],
            path,
            summary=summary,
            response=responses,
            exclude_unset=True,
            auth=auth,
            operation_id=operation.__name__,
            url_name="api_" + handler.__name__,
        )(operation)


for route, handler, summary, schema in ROUTES:
    _register(route, handler, summary, schema)
