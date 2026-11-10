from datetime import timedelta
from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST
from django.views.decorators.http import require_http_methods
from .configuration import config
from .forms import SSHKeyForm, RequestForm, ImageBuildForm, ReviewForm
from .models import ResourceRequest, SSHKey, VolumeAllocation, ImageArtifact, ImageBuild, SystemObservation, EnvironmentSnapshot
from .services import audit, approve, reject, request_stop, extend
from .access import tasks_for
from .queries import resource_overview, image_catalog, audit_list

def history_page(request, queryset, parameter='page', size=20):
    page = Paginator(queryset, size).get_page(request.GET.get(parameter))
    query = request.GET.copy()
    for name, available, number in (
        ('previous_url', page.has_previous(), page.number - 1),
        ('next_url', page.has_next(), page.number + 1),
    ):
        if available:
            query[parameter] = number
            setattr(page, name, '?' + query.urlencode())
    return page

@login_required
def dashboard(request):
    tasks = ResourceRequest.objects.filter(user=request.user).select_related('user', 'workspace')[:8]
    return render(request, 'portal/dashboard.html', {**resource_overview(), 'tasks': tasks})

@login_required
def keys(request):
    form = SSHKeyForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        key = form.save(commit=False)
        key.user = request.user
        try:
            with transaction.atomic():
                existing = SSHKey.objects.filter(user=request.user, fingerprint=key.fingerprint).first()
                if existing and existing.enabled:
                    form.add_error('public_key', '此公钥已添加。')
                else:
                    restored = existing is not None
                    if existing:
                        key = existing
                        key.enabled = True
                        key.save(update_fields=['enabled'])
                    else:
                        key.save()
                    audit(request.user, 'ssh_key_enable' if restored else 'ssh_key_add', key.pk, fingerprint=key.fingerprint)
        except IntegrityError:
            form.add_error('public_key', '此公钥已添加。')
        if not form.errors:
            messages.success(request, '公钥已启用，正在同步至运行中的容器。' if restored else '公钥已添加，正在同步至运行中的容器。')
            return redirect('keys')
    return render(request, 'portal/keys.html', {'form': form, 'keys': SSHKey.objects.filter(user=request.user).order_by('pk')})

@login_required
@require_POST
def delete_key(request, pk):
    key = get_object_or_404(SSHKey, pk=pk, user=request.user)
    key.enabled = False
    key.save(update_fields=['enabled'])
    audit(request.user, 'ssh_key_disable', key.pk)
    messages.success(request, '公钥已停用，正在同步至运行中的容器。')
    return redirect('keys')

@login_required
@transaction.atomic
def new_request(request, mode='interactive'):
    initial = {}
    if request.GET.get('node'):
        initial['node'] = request.GET['node']
    try:
        if mode == 'batch' and request.GET.get('image'):
            image = get_object_or_404(ImageArtifact.objects.filter(Q(owner=request.user) | Q(shared=True), verified=True), pk=request.GET['image'])
            initial.update(source_kind='image', image=image, node=image.node)
        if mode == 'batch' and request.GET.get('source'):
            source = get_object_or_404(ResourceRequest, pk=request.GET['source'], user=request.user, mode='interactive', status='running')
            initial.update(source_kind='environment', source_environment=source, purpose=source.purpose+' · 运行', workspace_gib=source.workspace_gib, node=source.node)
    except ValidationError as exc:
        raise Http404 from exc
    form = RequestForm(request.POST or None, user=request.user, mode=mode, initial=initial)
    if request.method == 'POST' and form.is_valid():
        req = form.save()
        audit(request.user, 'submit', req.pk)
        return redirect('task', pk=req.pk)
    from .queries import request_capacity
    return render(request, 'portal/request.html', {'form': form, 'mode': mode, 'capacity':request_capacity(request.user,node=form.node),
        'title': '新建开发环境' if mode == 'interactive' else '运行程序'})

@login_required
def tasks(request):
    mode = 'batch' if request.GET.get('mode') == 'batch' else 'interactive'
    tasks = history_page(request, ResourceRequest.objects.filter(user=request.user, mode=mode).select_related('user', 'workspace'))
    return render(request, 'portal/tasks.html', {'tasks': tasks, 'mode': mode})

@login_required
def task(request, pk):
    query = tasks_for(request.user)
    req = get_object_or_404(query.select_related('workspace', 'image', 'user'), pk=pk)
    ws = getattr(req, 'workspace', None)
    from .services import work_owner
    volume = VolumeAllocation.objects.filter(request=work_owner(req)).first()
    from .ssh_access import administrator_access
    admin_access=administrator_access(request.user)
    managed=bool(request.user.is_staff and (request.GET.get('from')=='management' or req.user_id!=request.user.pk))
    return render(request, 'portal/task.html', {'task': req, 'ws': ws, 'volume': volume,
                  'host': config()['platform']['public_host'],'administrator_access':admin_access,
                  'admin_connection':bool(request.user.is_staff and req.user_id!=request.user.pk),'managed':managed})

@login_required
def task_status(request, pk):
    query = tasks_for(request.user)
    req = get_object_or_404(query.select_related('workspace', 'user'), pk=pk)
    ws = getattr(req, 'workspace', None)
    return JsonResponse({'id': str(req.pk), 'status': req.status, 'label': req.get_status_display(),
                         'login_name': req.login_user, 'home_dir': req.home_dir,
                         'ssh_port': ws.ssh_port if ws and req.status == 'running' else None,
                         'stop_requested': bool(ws and ws.stop_requested),
                         'exit_code': ws.exit_code if ws else None, 'error': ws.error if ws else ''})

@login_required
@require_POST
def stop(request, pk):
    query = tasks_for(request.user)
    req = get_object_or_404(query, pk=pk)
    request_stop(req.pk, request.user)
    messages.success(request, '已请求停止任务。')
    next_url = request.POST.get('next', '')
    if next_url and url_has_allowed_host_and_scheme(next_url, {request.get_host()}, require_https=request.is_secure()):
        return redirect(next_url)
    return redirect('task', pk=pk)

@user_passes_test(lambda u: u.is_active and u.is_staff)
@require_POST
def review(request, pk):
    form = ReviewForm(request.POST)
    if form.is_valid():
        d = form.cleaned_data
        try:
            if d['action'] == 'approve':
                approve(pk, request.user, d['reason'], int(d['priority'] or 0))
            elif d['action'] == 'reject':
                reject(pk, request.user, d['reason'])
            elif d['action'] == 'stop':
                request_stop(pk, request.user, d['reason'])
            else:
                extend(pk, request.user, d['ends_at'], d['reason'])
        except ValidationError as e:
            messages.error(request, '；'.join(e.messages))
        else:
            messages.success(request, '操作已记录。')
    else:
        messages.error(request, form.errors.as_text())
    return redirect('task', pk=pk)

@login_required
def images(request):
    form = ImageBuildForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        build = form.save(commit=False)
        build.user = request.user
        if ImageBuild.objects.filter(user=request.user, status__in=['submitted', 'building']).exists():
            form.add_error(None, '请等待上一份镜像构建结束。')
        else:
            build.save()
            audit(request.user, 'image_build_submit', build.pk)
            return redirect('images')
    return render(request, 'portal/images.html', {'form': form,
        'images': history_page(request, image_catalog(request.user, 'permanent'), 'images_page'),
        'temporary_images': history_page(request, image_catalog(request.user, 'temporary'), 'temporary_page'),
        'builds': history_page(request, ImageBuild.objects.filter(user=request.user).order_by('-created_at', '-pk'), 'builds_page', size=10),
        'snapshots': history_page(request, EnvironmentSnapshot.objects.filter(source__user=request.user).select_related('source', 'artifact').order_by('-created_at', '-pk'), 'snapshots_page')})

@login_required
def timeline(request):
    from .timeline import allocation_timeline
    return JsonResponse(allocation_timeline(request.user, request.GET.get('date')))

@login_required
@require_POST
@transaction.atomic
def save_environment(request, pk):
    source = get_object_or_404(ResourceRequest.objects.exclude(workspace__stop_requested=True), pk=pk, user=request.user, mode='interactive', status='running')
    if not source.snapshots.filter(status__in=['pending', 'working']).exists():
        EnvironmentSnapshot.objects.create(source=source)
        audit(request.user, 'save_environment', source.pk)
        messages.success(request, '正在保存环境。')
    return redirect('images')


@login_required
@require_http_methods(['GET'])
def available_resources(request):
    from .queries import request_capacity
    from .nodes import selected_node
    node = selected_node(request.GET.get('node'))
    response=JsonResponse(request_capacity(request.user,node=node))
    response['Cache-Control']='no-store'
    return response

@user_passes_test(lambda u: u.is_active and u.is_staff)
def management(request):
    from django.contrib.auth import get_user_model
    return render(request, 'portal/management.html', {'section': 'overview', 'section_title': '管理', 'pending': ResourceRequest.objects.filter(status='submitted').select_related('user', 'image', 'workspace'),
        'active': tasks_for(request.user).filter(status__in=['approved', 'running', 'starting', 'queued', 'scheduled', 'preparing']).select_related('user', 'workspace'),
        'members': get_user_model().objects.count(), 'audits': audit_list()[:12]})

def health(request):
    obs = SystemObservation.objects.filter(name='reconciler').order_by('-observed_at').first()
    ok = bool(obs and obs.observed_at >= timezone.now() - timedelta(seconds=60))
    return JsonResponse({'web': 'ok', 'reconciler': 'ok' if ok else 'stale'}, status=200 if ok else 503)


@login_required
@require_http_methods(['GET', 'POST'])
def api_access(request):
    from .api_auth import issue_token, list_tokens, TOKEN_SECONDS
    from .forms import TokenIssueForm
    form = TokenIssueForm({**request.POST.dict(), 'expires_in': request.POST.get('expires_in', TOKEN_SECONDS)}
                          if request.method == 'POST' else None)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            credential = issue_token(request.user, form.cleaned_data['expires_in'], form.cleaned_data['name'])
            audit(request.user, 'api_token_issued', credential['id'], name=credential['name'], expires_in=credential['expires_in'])
        messages.success(request, '令牌已生成。')
        return redirect('api_access')
    response = render(request, 'portal/api_access.html', {'tokens': list_tokens(request.user, include_secrets=True), 'form': form,
                      'api_url': request.build_absolute_uri('/api/v1')})
    response['Cache-Control'] = 'no-store'
    return response


@login_required
@require_POST
@transaction.atomic
def delete_api_token(request, pk):
    from .api_auth import revoke_token
    if not revoke_token(request.user, pk):
        raise Http404
    audit(request.user, 'api_token_revoked', pk)
    messages.success(request, '令牌已删除。')
    return redirect('api_access')
