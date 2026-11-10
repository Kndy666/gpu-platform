from django.contrib import messages
from django.contrib.auth import get_user_model, update_session_auth_hash
from django.contrib.auth.decorators import user_passes_test
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .configuration import config
from .audit_display import audit_presentation
from .image_lifecycle import keep_image, recycle_image
from .management_forms import MemberForm, GroupForm, DeleteGroupForm, RecycleSettingsForm
from .models import PlatformSettings, UserGroup, VolumeAllocation
from .services import audit
from .volumes import annotate_volume_usage, request_volume_deletion, volume_busy
from .access import image_actions_for, volumes_for
from .queries import member_list, group_list, managed_image_list, image_summary, pool_list, audit_list
from .queries import container_list
from .ssh_access import administrator_access

staff_required = user_passes_test(lambda user: user.is_active and user.is_staff)


@staff_required
def nodes(request, pk=None):
    from .models import ComputeNode, default_node_id
    from .nodes import NodeForm
    default_node_id()
    node = get_object_or_404(ComputeNode, pk=pk) if pk else None
    form = NodeForm(request.POST if request.method == 'POST' else None, instance=node)
    if request.method == 'POST' and form.is_valid():
        node = form.save()
        audit(request.user, 'node_updated', node.pk, name=node.name, enabled=node.enabled, maintenance=node.maintenance)
        messages.success(request, '节点设置已保存，后台将同步连接状态。')
        return redirect('manage_nodes')
    return render(request, 'portal/manage_nodes.html', {'section': 'nodes', 'section_title': '计算节点',
                  'nodes': ComputeNode.objects.order_by('pk'), 'form': form, 'editing': node})


def page(request, queryset):
    return Paginator(queryset, 25).get_page(request.GET.get('page'))


@staff_required
def containers(request):
    from .models import ResourceRequest
    from .views import history_page
    query=request.GET.get('q','').strip();status=request.GET.get('status','');mode=request.GET.get('mode','')
    return render(request,'portal/manage_containers.html',{'section':'containers','section_title':'容器',
        'tasks':history_page(request,container_list(query,status,mode),'page',25),'query':query,'status':status,'mode':mode,
        'states':ResourceRequest.State.choices,'ssh_access':administrator_access(request.user)})


@staff_required
def members(request):
    query = request.GET.get('q', '').strip()
    users = member_list(query)
    return render(request, 'portal/manage_members.html', {'section': 'members', 'section_title': '成员与账号', 'page': page(request, users), 'query': query})


@staff_required
@transaction.atomic
def member_edit(request, pk=None):
    member = get_object_or_404(get_user_model().objects.select_related('profile__group'), pk=pk) if pk else None
    if member and member.is_staff and not request.user.is_superuser and member.pk != request.user.pk:
        raise PermissionDenied
    form = MemberForm(request.POST if request.method == 'POST' else None, actor=request.user, member=member)
    if request.method == 'POST' and form.is_valid():
        try:
            user = form.save()
        except (ValidationError, IntegrityError) as exc:
            form.add_error(None, exc if isinstance(exc, ValidationError) else '用户名已存在。')
        else:
            if user.pk == request.user.pk and form.cleaned_data['password']:
                update_session_auth_hash(request, user)
            messages.success(request, '成员设置已保存。')
            return redirect('manage_members')
    return render(request, 'portal/manage_member.html', {'section': 'members', 'section_title': member.username if member else '增加成员', 'form': form, 'member': member})


@staff_required
def groups(request):
    groups = group_list()
    return render(request, 'portal/manage_groups.html', {'section': 'groups', 'section_title': '用户组', 'groups': groups})


@staff_required
@transaction.atomic
def group_edit(request, pk=None):
    group = get_object_or_404(UserGroup, pk=pk) if pk else None
    form = GroupForm(request.POST if request.method == 'POST' else None, instance=group)
    if request.method == 'POST' and form.is_valid():
        group = form.save()
        audit(request.user, 'group_updated' if pk else 'group_created', group.pk, **form.audit_data())
        messages.success(request, '用户组已保存。')
        return redirect('manage_groups')
    return render(request, 'portal/manage_group.html', {'section': 'groups', 'section_title': group.name if group else '新建用户组', 'form': form, 'group': group})


@staff_required
@transaction.atomic
def group_delete(request, pk):
    group = get_object_or_404(UserGroup, pk=pk)
    form = DeleteGroupForm(request.POST if request.method == 'POST' else None, group=group)
    if request.method == 'POST' and form.is_valid():
        try:
            form.save(request.user)
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, '用户组已删除。')
            return redirect('manage_groups')
    return render(request, 'portal/manage_group_delete.html', {'section': 'groups', 'section_title': '删除用户组', 'group': group, 'form': form, 'member_count': group.members.count()})


@staff_required
def storage(request):
    pools = pool_list()
    volumes = volumes_for(request.user).exclude(state='deleted').order_by('user__username', 'kind')
    rows = page(request, volumes)
    rows.object_list = list(rows.object_list)
    annotate_volume_usage(rows.object_list)
    return render(request, 'portal/manage_storage.html', {'section': 'storage', 'section_title': '存储', 'pools': pools, 'page': rows})


@staff_required
def volume_delete(request, pk):
    volume = get_object_or_404(VolumeAllocation.objects.select_related('user', 'pool'), pk=pk)
    if request.method == 'POST':
        try:
            request_volume_deletion(volume.pk, request.user)
        except ValidationError as exc:
            messages.error(request, '；'.join(exc.messages))
        else:
            messages.success(request, '已安排删除数据卷。')
            return redirect('manage_storage')
    return render(request, 'portal/manage_volume_delete.html', {'section': 'storage', 'section_title': '删除数据卷',
                  'volume': volume, 'busy': volume_busy(volume)})


@staff_required
def images(request):
    images = managed_image_list()
    return render(request, 'portal/manage_images.html', {'section': 'images', 'section_title': '镜像回收', 'page': page(request, images),
        **image_summary(images)})


@staff_required
def settings(request):
    form = RecycleSettingsForm(request.POST if request.method == 'POST' else None, instance=PlatformSettings.load())
    if request.method == 'POST' and form.is_valid():
        form.save()
        audit(request.user, 'recycle_settings_updated', 'platform', **form.cleaned_data)
        messages.success(request, '保留时间已保存，将用于后续结束的任务和开始计时的快照。')
        return redirect('manage_settings')
    cfg = config()
    return render(request, 'portal/manage_settings.html', {'section': 'settings', 'section_title': '设置', 'form': form,
        'public_host': cfg['platform']['public_host'], 'ssh_start': cfg['platform']['ssh_ports'][0], 'ssh_end': cfg['platform']['ssh_ports'][1]})


@staff_required
def audits(request):
    rows = page(request, audit_list())
    for row in rows:
        for name, value in audit_presentation(row).items():
            setattr(row, name, value)
    return render(request, 'portal/manage_audits.html', {'section': 'audits', 'section_title': '审计记录', 'page': rows})


@require_POST
def image_action(request, pk, action):
    if not request.user.is_authenticated:
        from django.contrib.auth.views import redirect_to_login
        return redirect_to_login(request.get_full_path())
    visible = image_actions_for(request.user)
    get_object_or_404(visible, pk=pk)
    if action not in {'keep', 'recycle'}:
        raise PermissionDenied
    try:
        (keep_image if action == 'keep' else recycle_image)(pk, request.user)
    except ValidationError as exc:
        messages.error(request, '；'.join(exc.messages))
    else:
        messages.success(request, '镜像已长期保留。' if action == 'keep' else '镜像将在 24 小时后回收，可在此之前保留。')
    return redirect('manage_images' if request.user.is_staff and request.POST.get('origin') == 'management' else 'images')
