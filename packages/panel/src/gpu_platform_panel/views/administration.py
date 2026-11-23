from copy import deepcopy
from django.contrib.auth.decorators import login_required, user_passes_test
from django.http import JsonResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.views.decorators.http import require_POST, require_GET
import gpu_platform_control.services.administration as service
from gpu_platform_control.services.usage import current_usage, usage_statistics
from .adapter import service_arguments, errors_as_http, render_result, notify, edit, receivers, parameters
from django.core.exceptions import ValidationError
from django.contrib import messages
from ..forms import (
    NodeForm,
    MemberForm,
    GroupForm,
    DeleteGroupForm,
    RecycleSettingsForm,
    PushSettingsForm,
    ReviewForm,
)

staff_required = user_passes_test(lambda user: user.is_active and user.is_staff)


@staff_required
@errors_as_http
def approvals(request):
    action = request.POST.get("action")
    query = parameters(request.GET)
    if request.method == "POST" and (action == "push" or request.POST.get("channel")):
        query["tab"] = "push"
    result = service.approvals(request.user, query=query)
    push_form = PushSettingsForm(
        request.POST if request.method == "POST" and action == "push" else None,
        options=result.data["form_options"],
    )
    forms = receivers(request, result.data["channels"])
    for receiver in forms:
        for name, field in receiver["form"].fields.items():
            field = deepcopy(field)
            field.required = False
            push_form.fields["receiver:" + receiver["channel"] + ":" + name] = field

    selected = next((item["form"] for item in forms if item["form"].is_bound), None)
    values = None
    error_form = selected or push_form
    if selected and selected.is_valid():
        values = selected.payload()
    elif push_form.is_bound and push_form.is_valid():
        payload = push_form.payload()
        values = {**{key: value for key, value in payload.items() if not key.startswith("receiver:")}, "action": "push"}
        values["recipients"] = {}
        for key, value in payload.items():
            if key.startswith("receiver:") and value:
                _, channel, field = key.split(":", 2)
                values["recipients"].setdefault(channel, {})[field] = value
    elif request.method == "POST" and action in {"request", "extension"}:
        values = parameters(request.POST)
        review = ReviewForm({**values, "action": values.get("decision")})
        if review.is_valid():
            values = {
                **values,
                **review.payload(),
                "action": action,
                "decision": review.cleaned_data["action"],
            }
        else:
            values = None
            for errors in review.errors.values():
                for error in errors:
                    messages.error(request, error)
    if values is not None:
        try:
            saved = service.approvals(request.user, values=values)
        except ValidationError as error:
            if action in {"request", "extension"}:
                for message in error.messages:
                    messages.error(request, message)
            else:
                error_form.application_error(error)
        else:
            notify(request, saved)
            return redirect(
                reverse("manage_approvals") + ("?tab=push" if action == "push" or selected else "")
            )
    return render_result(
        request, "portal/manage_approvals.html", result, push_form=push_form, receiver_forms=forms,
        receiver_fields=[push_form[name] for name in push_form.fields if name.startswith("receiver:")],
        token_configured=any(any(row["values"].values()) for row in result.data["channels"])
    )


@staff_required
@errors_as_http
def performance(request):
    result = service.performance(request.user, **service_arguments(request))
    history = service.performance_history(request.user, query=parameters(request.GET))
    return render_result(request, "portal/manage_performance.html", result, **history.data)


@staff_required
@errors_as_http
def nodes(request, pk=None):
    result, form = edit(request, service.nodes, NodeForm, pk)
    if result.complete:
        notify(request, result)
        return redirect("manage_nodes")
    return render_result(request, "portal/manage_nodes.html", result, form=form)


@staff_required
@errors_as_http
def containers(request):
    result = service.containers(request.user, **service_arguments(request))
    return render_result(request, "portal/manage_containers.html", result)


@staff_required
@errors_as_http
def members(request):
    result = service.members(request.user, **service_arguments(request))
    return render_result(request, "portal/manage_members.html", result)


@staff_required
@errors_as_http
def member_edit(request, pk=None):
    result, form = edit(request, service.member_edit, MemberForm, pk)
    if result.complete:
        notify(request, result)
        return redirect("manage_members")
    return render_result(request, "portal/manage_member.html", result, form=form)


@staff_required
@errors_as_http
def groups(request):
    result = service.groups(request.user, **service_arguments(request))
    return render_result(request, "portal/manage_groups.html", result)


@staff_required
@errors_as_http
def group_edit(request, pk=None):
    result, form = edit(request, service.group_edit, GroupForm, pk)
    if result.complete:
        notify(request, result)
        return redirect("manage_groups")
    return render_result(request, "portal/manage_group.html", result, form=form)


@staff_required
@errors_as_http
def group_delete(request, pk):
    result, form = edit(request, service.group_delete, DeleteGroupForm, pk)
    if result.complete:
        notify(request, result)
        return redirect("manage_groups")
    return render_result(request, "portal/manage_group_delete.html", result, form=form)


@staff_required
@errors_as_http
def storage(request):
    result = service.storage(request.user, **service_arguments(request))
    return render_result(request, "portal/manage_storage.html", result)


@staff_required
@errors_as_http
def volume_delete(request, pk):
    result = service.volume_delete(request.user, pk, **service_arguments(request))
    if result.complete:
        notify(request, result)
        return redirect(reverse("manage_storage"))
    return render_result(request, "portal/manage_volume_delete.html", result)


@staff_required
@errors_as_http
def images(request):
    result = service.images(request.user, **service_arguments(request))
    return render_result(request, "portal/manage_images.html", result)


@staff_required
@errors_as_http
def settings(request):
    result, form = edit(request, service.settings, RecycleSettingsForm)
    if result.complete:
        notify(request, result)
        return redirect("manage_settings")
    return render_result(request, "portal/manage_settings.html", result, form=form)


@staff_required
@errors_as_http
def audits(request):
    result = service.audits(request.user, **service_arguments(request))
    return render_result(request, "portal/manage_audits.html", result)


@staff_required
@require_GET
def performance_data(request):
    from django.template.loader import render_to_string

    result = service.performance(request.user, query=parameters(request.GET))
    response = JsonResponse({"html": render_to_string("portal/performance_nodes.html", result.data, request)})
    response["Cache-Control"] = "no-store"
    return response


@login_required
@require_POST
@errors_as_http
def image_action(request, pk, action):
    result = service.image_action(request.user, pk, action, **service_arguments(request))
    notify(request, result)
    if request.user.is_staff and request.POST.get("origin") == "management":
        return redirect(reverse("manage_images") + f"?node={result.identity}")
    return redirect("images")


@staff_required
@require_GET
def usage(request):
    query = parameters(request.GET)
    try:
        report = usage_statistics(request.user, query=query)
        error = None
    except ValidationError as exc:
        report = usage_statistics(request.user)
        error = exc.messages[0]
    response = render_result(request, "portal/manage_usage.html", report,
                             live=current_usage(request.user).data, filter_error=error)
    response["Cache-Control"] = "no-store"
    if error:
        response.status_code = 400
    return response


@staff_required
@require_GET
def usage_data(request):
    from django.template.loader import render_to_string

    response = JsonResponse({"html": render_to_string(
        "portal/usage_live.html", {"live": current_usage(request.user).data}, request
    )})
    response["Cache-Control"] = "no-store"
    return response
