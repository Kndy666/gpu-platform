from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST, require_http_methods

from gpu_platform_control.services import inbox as service

from ..forms import SendNoticeForm
from .adapter import errors_as_http, notify, parameters, render_result


staff_required = user_passes_test(lambda user: user.is_active and user.is_staff)


def private(response):
    response["Cache-Control"] = "private, no-store"
    return response


@login_required
@require_GET
@errors_as_http
def inbox(request):
    return private(render_result(request, "portal/inbox.html", service.inbox(request.user, query=parameters(request.GET))))


@login_required
@require_http_methods(["GET", "POST"])
@errors_as_http
def notice(request, pk):
    if request.method == "POST":
        service.read_notice(request.user, pk)
        return redirect("notice", pk=pk)
    return private(render_result(request, "portal/notice.html", service.notice_detail(request.user, pk)))


@login_required
@require_POST
@errors_as_http
def dismiss_banner(request, pk):
    service.dismiss_banner(request.user, pk)
    return private(JsonResponse({}))


@login_required
@require_POST
@errors_as_http
def reminders(request):
    displayed = [int(value) for value in request.POST.getlist("shown")[:10] if value.isascii()
                 and value.isdecimal() and len(value) <= 18]
    return private(JsonResponse(service.reminders(request.user, displayed=displayed).data))


@staff_required
@require_http_methods(["GET", "POST"])
@errors_as_http
def send_notice(request):
    result = service.composer(request.user)
    form = SendNoticeForm(request.POST if request.method == "POST" else None, options=result.data["form_options"])
    preview = None
    if form.is_bound and form.is_valid():
        token = request.POST.get("confirmation", "") if request.POST.get("action") == "send" else None
        try:
            saved = service.send_notice(request.user, form.payload(), confirmation=token)
        except ValidationError as error:
            form.application_error(error)
        else:
            if saved.complete:
                notify(request, saved)
                return redirect(reverse("manage_approvals") + "?tab=history&origin=manual")
            preview = saved.data
    return render_result(request, "portal/send_notice.html", result, form=form, preview=preview)


@staff_required
@require_POST
@errors_as_http
def stop_notice(request, pk):
    try:
        saved = service.stop_notice(request.user, pk, request.POST.get("action"))
    except ValidationError as error:
        for message in error.messages:
            messages.error(request, message)
    else:
        notify(request, saved)
    return redirect(reverse("manage_approvals") + "?tab=history&origin=manual")
