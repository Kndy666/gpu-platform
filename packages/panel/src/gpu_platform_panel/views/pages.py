from django.contrib.auth.decorators import login_required, user_passes_test
from django.http import JsonResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST, require_GET
import gpu_platform_control.services.pages as service
from .adapter import service_arguments, errors_as_http, render_result, notify, edit, receivers, parameters
from django.core.exceptions import ValidationError
from ..forms import EndTimeForm, ExtensionForm, ImageBuildForm, RequestForm, ReviewForm, SSHKeyForm, TokenIssueForm

staff_required = user_passes_test(lambda user: user.is_active and user.is_staff)


@login_required
@errors_as_http
def dashboard(request):
    result = service.dashboard(request.user, **service_arguments(request))
    return render_result(request, "portal/dashboard.html", result)


@login_required
@errors_as_http
def tasks(request):
    result = service.tasks(request.user, **service_arguments(request))
    return render_result(request, "portal/tasks.html", result)


@login_required
@errors_as_http
def task(request, pk):
    return _render_task(request, pk)


def _render_task(request, pk, *, extension_form=None, review_form=None, shorten_form=None):
    result = service.task(request.user, pk, query=parameters(request.GET))
    return render_result(
        request, "portal/task.html", result,
        extension_form=extension_form or ExtensionForm(),
        review_form=review_form or ReviewForm(auto_id="review_%s"),
        shorten_form=shorten_form or EndTimeForm(auto_id="shorten_%s"),
    )


@login_required
@errors_as_http
def synchronization(request):
    result = service.synchronization(request.user, **service_arguments(request))
    return render_result(request, "portal/syncs.html", result)


@login_required
@errors_as_http
def keys(request):
    result, form = edit(request, service.keys, SSHKeyForm)
    if result.complete:
        notify(request, result)
        return redirect("keys")
    response = render_result(request, "portal/keys.html", result, form=form)
    response["Cache-Control"] = "no-store"
    return response


@login_required
@errors_as_http
def images(request):
    result, form = edit(request, service.images, ImageBuildForm)
    if result.complete:
        notify(request, result)
        return redirect("images")
    response = render_result(
        request, "portal/images.html", result, form=form if form.options.get("nodes") else None
    )
    response["Cache-Control"] = "no-store"
    return response


@login_required
@errors_as_http
def api_access(request):
    result, form = edit(request, service.api_access, TokenIssueForm)
    if result.complete:
        notify(request, result)
        return redirect("api_access")
    response = render_result(
        request, "portal/api_access.html", result, form=form, api_url=request.build_absolute_uri("/api/v1")
    )
    response["Cache-Control"] = "no-store"
    return response


@login_required
@errors_as_http
def notification_preferences(request):
    result = service.notification_preferences(request.user)
    forms = receivers(request, result.data["channels"])
    for receiver in forms:
        form = receiver["form"]
        if form.is_bound and form.is_valid():
            try:
                saved = service.notification_preferences(request.user, values=form.payload())
            except ValidationError as error:
                form.application_error(error)
            else:
                notify(request, saved)
                return redirect("notification_preferences")
    response = render_result(request, "portal/notification_preferences.html", result, forms=forms)
    response["Cache-Control"] = "no-store"
    return response


@login_required
@errors_as_http
def new_request(request, mode="interactive"):
    query = parameters(request.GET)
    if request.method == "POST":
        query["node"] = request.POST.get("node")
    result, form = edit(
        request, service.new_request, RequestForm, mode, form_kwargs={"mode": mode}, query=query
    )
    if result.complete:
        notify(request, result)
        return redirect("task", pk=result.identity)
    return render_result(request, "portal/request.html", result, form=form)


@login_required
@require_POST
@errors_as_http
def delete_key(request, pk):
    result = service.delete_key(request.user, pk, **service_arguments(request))
    notify(request, result)
    return redirect("keys")


@login_required
@require_POST
@errors_as_http
def request_extension(request, pk):
    form = ExtensionForm(request.POST)
    if form.is_valid():
        try:
            saved = service.request_extension(request.user, pk, values=form.payload())
        except ValidationError as error:
            form.application_error(error)
        else:
            notify(request, saved)
            return redirect("task", pk=pk)
    return _render_task(request, pk, extension_form=form)


@login_required
@require_POST
@errors_as_http
def shorten_time(request, pk):
    form = EndTimeForm(request.POST, auto_id="shorten_%s")
    if form.is_valid():
        try:
            saved = service.shorten_time(request.user, pk, values=form.payload())
        except ValidationError as error:
            form.application_error(error)
        else:
            notify(request, saved)
            return redirect("task", pk=pk)
    return _render_task(request, pk, shorten_form=form)


@login_required
@require_POST
@errors_as_http
def stop(request, pk):
    result = service.stop(request.user, pk, **service_arguments(request))
    notify(request, result)
    next_url = request.POST.get("next", "")
    if next_url and url_has_allowed_host_and_scheme(
        next_url, {request.get_host()}, require_https=request.is_secure()
    ):
        return redirect(next_url)
    return redirect("task", pk=pk)


@staff_required
@require_POST
@errors_as_http
def review(request, pk):
    query = parameters(request.GET)
    form = ReviewForm(request.POST, auto_id="review_%s")
    if form.is_valid():
        try:
            saved = service.review(request.user, pk, values=form.payload())
        except ValidationError as error:
            form.application_error(error)
        else:
            notify(request, saved)
            origin = {"from": "management"} if query.get("from") == "management" else None
            return redirect(reverse("task", kwargs={"pk": pk}, query=origin))
    return _render_task(request, pk, review_form=form)


@login_required
@require_POST
@errors_as_http
def save_environment(request, pk):
    result = service.save_environment(request.user, pk, **service_arguments(request))
    notify(request, result)
    return redirect("images")


@login_required
@require_POST
@errors_as_http
def delete_api_token(request, pk):
    result = service.delete_api_token(request.user, pk, **service_arguments(request))
    notify(request, result)
    return redirect("api_access")


@login_required
@require_POST
@errors_as_http
def sync_image(request, pk):
    result = service.sync_image(request.user, pk, **service_arguments(request))
    notify(request, result)
    return redirect("syncs")


@staff_required
@require_POST
@errors_as_http
def sync_datasets(request):
    result = service.sync_datasets(request.user, **service_arguments(request))
    notify(request, result)
    return redirect("syncs")


@login_required
@require_GET
@errors_as_http
def timeline(request):
    result = service.timeline(request.user, **service_arguments(request))
    response = JsonResponse(result.data)
    response["Cache-Control"] = "no-store"
    return response


@login_required
@require_GET
@errors_as_http
def available_resources(request):
    result = service.available_resources(request.user, **service_arguments(request))
    response = JsonResponse(result.data)
    response["Cache-Control"] = "no-store"
    return response


@login_required
@require_GET
@errors_as_http
def task_status(request, pk):
    result = service.task_status(request.user, pk, **service_arguments(request))
    response = JsonResponse(result.data)
    response["Cache-Control"] = "no-store"
    return response


@staff_required
def management(request):
    return redirect("manage_nodes")


def health(request):
    result = service.health(request.user)
    return JsonResponse(result.data, status=200 if result.data["reconciler"] == "ok" else 503)


def language(request):
    from django.http import HttpResponseBadRequest, HttpResponseRedirect
    from django.utils.http import url_has_allowed_host_and_scheme
    from django.views.decorators.http import require_POST
    from gpu_platform_control.services import set_language

    @require_POST
    def update(request):
        selected = request.POST.get("language", "")
        if selected not in {"en", "zh-hans"}:
            return HttpResponseBadRequest()
        if request.user.is_authenticated:
            set_language(request.user, selected)
        next_url = request.POST.get("next", "/")
        if not url_has_allowed_host_and_scheme(
            next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
        ):
            next_url = "/"
        response = HttpResponseRedirect(next_url)
        response.set_cookie(
            "django_language", selected, samesite="Lax", secure=request.is_secure(), httponly=True
        )
        return response

    return update(request)
