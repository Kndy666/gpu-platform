"""HTTP adaptation kept in the panel package."""

from functools import wraps
from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.http import Http404
from django.shortcuts import render
from gpu_platform_control.services.boundary import NotFound
from django.core.exceptions import ValidationError


def parameters(values):
    return {key: values.getlist(key) if len(values.getlist(key)) > 1 else values[key] for key in values}


def service_arguments(request):
    return {
        "values": parameters(request.POST) if request.method == "POST" else None,
        "query": parameters(request.GET),
    }


def errors_as_http(function):
    @wraps(function)
    def view(request, *args, **kwargs):
        try:
            return function(request, *args, **kwargs)
        except NotFound as exc:
            raise Http404 from exc
        except ValidationError as exc:
            if getattr(exc, "code", None) in {"node_invalid", "node_not_found", "dataset_not_found"}:
                raise Http404 from exc
            raise

    return view


def notify(request, result):
    for level, message in result.notices:
        if level == "refresh_session":
            request.user.refresh_from_db()
            update_session_auth_hash(request, request.user)
        else:
            getattr(messages, level)(request, message)


def render_result(request, template, result, **extra):
    notify(request, result)
    return render(
        request,
        template,
        {**result.data, **extra},
    )


def edit(request, service, form_class, *args, form_kwargs=None, query=None):
    query = parameters(request.GET) if query is None else query
    result = service(request.user, *args, query=query)
    form = form_class(
        request.POST if request.method == "POST" else None,
        options=result.data.get("form_options", {}),
        **(form_kwargs or {}),
    )
    if request.method == "POST" and form.is_valid():
        try:
            result = service(request.user, *args, values=form.payload(), query=query)
        except ValidationError as error:
            form.application_error(error)
    return result, form


def receivers(request, channels):
    from ..forms import ReceiverForm

    return [
        {
            "channel": channel["channel"],
            "form": ReceiverForm(
                request.POST
                if request.method == "POST" and request.POST.get("channel") == channel["channel"]
                else None,
                options=channel,
            ),
        }
        for channel in channels
    ]
