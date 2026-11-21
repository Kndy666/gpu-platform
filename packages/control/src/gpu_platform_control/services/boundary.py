"""Authenticated application operations and materialized results."""

from dataclasses import dataclass, field
from functools import wraps
from urllib.parse import urlencode
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied
from .read_models import materialize


class NotFound(LookupError):
    pass


class Parameters(dict):
    def getlist(self, key):
        value = self.get(key, [])
        return value if isinstance(value, list) else [value]

    def dict(self):
        return {key: value[-1] if isinstance(value, list) else value for key, value in self.items()}

    def copy(self):
        return Parameters(self)

    def urlencode(self):
        return urlencode(self, doseq=True)


def parameters(values):
    return Parameters(values or {})


@dataclass(frozen=True)
class Result:
    data: dict = field(default_factory=dict)
    complete: bool = False
    identity: object = None
    notices: tuple = ()


def result(data=None, *, complete=False, identity=None, notices=()):
    return Result(
        materialize(data or {}),
        complete,
        identity,
        tuple((level, str(message)) for level, message in notices),
    )


def authenticated(function):
    @wraps(function)
    def call(actor, *args, **kwargs):
        if not actor.is_authenticated or not actor.is_active:
            raise PermissionDenied
        return function(actor, *args, **kwargs)

    return call


def staff_only(function):
    @wraps(function)
    @authenticated
    def call(actor, *args, **kwargs):
        if not actor.is_staff:
            raise PermissionDenied
        return function(actor, *args, **kwargs)

    return call


def find(model_or_query, **filters):
    query = model_or_query.objects if hasattr(model_or_query, "objects") else model_or_query
    try:
        return query.get(**filters)
    except (ObjectDoesNotExist, ValueError, TypeError) as exc:
        raise NotFound from exc


def approval_count():
    from gpu_platform_control.core.requests.models import ExtensionRequest, ResourceRequest

    return (
        ResourceRequest.objects.filter(status="submitted").count()
        + ExtensionRequest.objects.filter(status="pending").count()
    )
