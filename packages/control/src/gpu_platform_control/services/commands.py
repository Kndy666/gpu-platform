"""Business mutations accept primitive intent and return materialized records."""

from django.contrib.auth import get_user_model, password_validation
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.translation import gettext_lazy as _
from gpu_platform_control.core.accounts.models import UserGroup, UserProfile
from gpu_platform_control.core.requests.models import ResourceRequest
from gpu_platform_control.core.retention.models import ImageArtifact, ImageBuild
from gpu_platform_control.core.nodes.models import ComputeNode, default_node_id
from gpu_platform_control.core.quotas.models import RegisteredResource, ResourceQuota
from gpu_platform_control.core.quotas.accounting import quantity
from gpu_platform_control.core.policies import group_policy
from gpu_platform_control.core.services import audit, validate_request, resize_home
from gpu_platform_control.core.identity import validate_login_name
from .boundary import authenticated, staff_only, find
from .read_models import materialize


def timestamp(value):
    if not value:
        return None
    if isinstance(value, str):
        value = parse_datetime(value)
    if value is None:
        raise ValidationError(_('Enter a valid date and time.'))
    return timezone.make_aware(value) if timezone.is_naive(value) else value


@authenticated
@transaction.atomic
def submit_task(actor, values):
    node = find(ComputeNode, pk=values.get('node') or default_node_id())
    source = find(ResourceRequest, pk=values['source_environment']) if values.get('source_environment') else None
    image = source.image if source else find(ImageArtifact, pk=values['image'])
    if source:
        from gpu_platform_control.core.features import require
        require(source.node, 'save_environment')
    sizes = dict(values.get('volume_sizes', {}))
    if source:
        sizes.setdefault('work', source.volume_sizes['work'])
    task = ResourceRequest(user=actor, node=node, image=image,
        purpose=values.get('purpose', ''), mode=values.get('mode', 'interactive'),
        resource_quantities=values.get('resource_quantities', {}), resource_allocations=values.get('resource_allocations', {}),
        volume_sizes=sizes, starts_at=timestamp(values.get('starts_at')),
        ends_at=timestamp(values.get('ends_at')), source_environment=source,
        argv=values.get('argv', []), working_dir=values.get('working_dir', '/workspace'),
        environment=values.get('environment', {}), notes=values.get('notes', ''), login_name=actor.username)
    if any(value == "max" for entries in task.resource_allocations.values() for entry in entries for value in entry.values()):
        from gpu_platform_contracts.assessment import ResourceIntent, ResourceAssessment
        from gpu_platform_contracts.messages import Operation
        from gpu_platform_control.slots.gateway import Gateway
        from gpu_platform_control.core.policies import effective_policy
        policy = effective_policy(actor)
        intent = ResourceIntent(owner=str(actor.pk), resources=task.resource_quantities,
            allocation_requests=task.resource_allocations,
            limits={key: None if policy.unlimited else value for key, value in policy.resources.items()},
            per_allocation_limits={key: None if policy.unlimited else value for key, value in policy.per_allocation.items()})
        assessment = ResourceAssessment.model_validate(Gateway().request(node, Operation.RESOURCE_ASSESS, **intent.model_dump(mode="json")))
        task.resource_allocations = {group: [{key: str(value) for key, value in entry.items()} for entry in entries]
                                     for group, entries in assessment.allocations.items()}
        if not task.resource_allocations:
            raise ValidationError(_('Adjust the per-device requests or GPU count to fit the available quotas.'))
    validate_request(task)
    task.full_clean()
    task.save()
    from gpu_platform_control.core.approvals import submit_request
    task = submit_request(task)
    return materialize(task)


def normalized_policy(values, defaults):
    from gpu_platform_control.core.quotas.rules import quota_rules
    rules = quota_rules()
    unlimited = values.get('unlimited', defaults.get('unlimited', False))
    resources = {**defaults.get('resources', {}), **values.get('resource_limits', {})}
    per_allocation = {**defaults.get('per_allocation', {}), **values.get('per_allocation_limits', {})}
    registered = {item.key: item for item in RegisteredResource.objects.filter(key__in=set(resources) | set(per_allocation))}
    if set(resources) | set(per_allocation) != set(registered):
        raise ValidationError(_('Unknown resource.'), code='resource_unavailable')
    if any(not item.has_member_quota or item.key in rules for item in registered.values()):
        raise ValidationError(_('Unknown resource.'), code='resource_unavailable')
    for key, value in resources.items():
        if (registered[key].accounting == 'volume' or key == 'system_storage') and value is None:
            raise ValidationError(_('Set a resource limit.'), code='resource_quota_required')
        amount = None if value is None else quantity(value)
        if key == 'system_storage' and amount is not None and amount != amount.to_integral_value():
            raise ValidationError(_('Set a whole number.'), code='resource_amount_invalid')
        resources[key] = None if (unlimited and key != 'system_storage' and registered[key].accounting == 'task') or amount is None else str(amount)
    for key, value in per_allocation.items():
        if not registered[key].per_allocation:
            raise ValidationError(_('Unknown resource.'), code='resource_allocation_unavailable')
        per_allocation[key] = None if unlimited or value is None else str(quantity(value))
    result = {'unlimited': unlimited, 'resources': resources, 'per_allocation': per_allocation}
    for key, fallback in [('concurrent_tasks', 1), ('interactive_hours', 120)]:
        value = values.get(key, defaults.get(key, fallback))
        if not unlimited and (isinstance(value, bool) or not isinstance(value, int) or value < 1):
            raise ValidationError(_('Set a resource limit.'))
        result[key] = None if unlimited else value
    return result


@staff_only
@transaction.atomic
def save_group(actor, values, group_id=None):
    group = find(UserGroup, pk=group_id) if group_id else UserGroup()
    policy = normalized_policy(values, group_policy(group) if group.pk else {})
    for key in ('name', 'home_size_gib', 'extension_limit', 'approval_mode'):
        if key in values:
            setattr(group, key, values[key])
    for key in ('unlimited', 'concurrent_tasks', 'interactive_hours'):
        setattr(group, key, policy[key])
    group.full_clean()
    if values.get('make_default'):
        UserGroup.objects.filter(is_default=True).exclude(pk=group.pk).update(is_default=False)
        group.is_default = True
    group.save()
    for key, value in policy['resources'].items():
        ResourceQuota.objects.update_or_create(group=group, resource_id=key,
            defaults={'limit': value, 'per_allocation_limit': policy['per_allocation'].get(key)})
    audit(actor, 'group_updated' if group_id else 'group_created', group.pk, name=group.name)
    return materialize(group)


@staff_only
@transaction.atomic
def save_member(actor, values, member_id=None):
    User = get_user_model()
    member = find(User, pk=member_id) if member_id else User()
    if member.pk and member.is_staff and not actor.is_superuser and member.pk != actor.pk:
        raise PermissionDenied
    group = find(UserGroup, pk=values.get('group') or (member.profile.group_id if member.pk else UserGroup.objects.order_by('-is_default', 'pk').first().pk))
    initial = member.profile.resource_limits if member.pk else group_policy(group)
    policy = normalized_policy(values, initial)
    policy['resources'].pop('system_storage', None)
    for key in ('username', 'first_name', 'email', 'is_active'):
        if key in values:
            setattr(member, key, values[key])
    if 'is_staff' in values:
        if values['is_staff'] != member.is_staff and not actor.is_superuser:
            raise PermissionDenied
        member.is_staff = values['is_staff']
    if member.pk and (member.pk == actor.pk or member.is_superuser) and (not member.is_active or not member.is_staff):
        raise ValidationError(_('The platform administrator account requires active administrator privileges.'))
    validate_login_name(member.username)
    password = values.get('password')
    if password:
        password_validation.validate_password(password, member)
        member.set_password(password)
    elif not member.pk:
        raise ValidationError(_('Set an initial password.'))
    member.full_clean(exclude=['password'])
    member.save()
    profile = UserProfile.objects.select_for_update().get(user=member)
    profile.group = group
    profile.resource_limits = policy
    for key in ('extension_limit', 'approval_mode'):
        setattr(profile, key, values.get(key, getattr(profile, key)))
    profile.full_clean()
    profile.save()
    member.profile = profile
    resize_home(member, int(values.get('home_size_gib', profile.home_size_gib if member_id else group.home_size_gib)), actor)
    audit(actor, 'member_updated' if member_id else 'member_created', member.pk, username=member.username, group=group.name)
    return materialize(member)


@authenticated
@transaction.atomic
def submit_build(actor, values):
    from gpu_platform_control.core.features import provides
    from urllib.parse import urlsplit

    node = find(ComputeNode, pk=values.get('node') or default_node_id())
    if not node.enabled or node.maintenance or node.state != 'online' or not provides(node, 'build') or not provides(node, 'registry'):
        raise ValidationError(_('The selected node is unavailable for image builds.'))
    context = values.get('context_git') or ''
    if context:
        try:
            url = urlsplit(context)
            valid = (url.scheme == 'https' and bool(url.hostname) and not url.username and not url.password
                     and (url.port is None or 1 <= url.port <= 65535))
        except ValueError:
            valid = False
        if not valid:
            raise ValidationError(_('Enter an HTTPS Git repository URL without embedded credentials.'))
    if ImageBuild.objects.filter(user=actor, status__in=["submitted", "building"]).exists():
        raise ValidationError(_("Wait for your current image build to finish."))
    build = ImageBuild(user=actor, node=node, name=values.get('name', ''), dockerfile=values.get('dockerfile', ''), context_git=context)
    build.full_clean()
    build.save()
    audit(actor, 'image_build_submit', build.pk, name=build.name)
    return materialize(build)


@staff_only
@transaction.atomic
def save_node(actor, values, node_id=None):
    from gpu_platform_control.slots.configuration import cluster_config
    node = find(ComputeNode, pk=node_id) if node_id else ComputeNode()
    name = values.get('name', node.name)
    host = cluster_config().hosts.get(name)
    if host is None or 'node' not in host.roles:
        raise ValidationError(_('Select a node defined in the cluster configuration.'))
    if node.pk and name != node.name and ResourceRequest.objects.filter(node=node, workspace__cleanup_complete=False).exists():
        raise ValidationError(_('Finish this node\'s tasks and release their resources before changing its connection.'))
    node.name, node.architecture = name, host.architecture
    for key in ('enabled', 'maintenance'):
        if key in values:
            setattr(node, key, values[key])
    node.full_clean()
    node.save()
    audit(actor, 'node_updated' if node_id else 'node_created', node.pk, name=node.name)
    return materialize(node)


@staff_only
@transaction.atomic
def delete_group(actor, group_id, destination_id=None):
    group = find(UserGroup, pk=group_id)
    destination = find(UserGroup, pk=destination_id) if destination_id else UserGroup.objects.exclude(pk=group_id).order_by('-is_default', 'pk').first()
    if destination is None or destination.pk == group.pk:
        raise ValidationError(_('Keep at least one user group.'))
    moved = UserProfile.objects.filter(group=group).update(group=destination)
    if group.is_default:
        group.is_default = False
        group.save(update_fields=['is_default'])
        destination.is_default = True
        destination.save(update_fields=['is_default'])
    audit(actor, 'group_deleted', group.pk, destination=destination.name, moved_members=moved)
    group.delete()
    return {'deleted': True}


@authenticated
@transaction.atomic
def change_password(actor, old_password, new_password1, new_password2):
    if not actor.check_password(old_password):
        raise ValidationError(_('The current password is incorrect.'))
    if new_password1 != new_password2:
        raise ValidationError(_('The new passwords do not match.'))
    password_validation.validate_password(new_password1, actor)
    actor.set_password(new_password1)
    actor.save(update_fields=['password'])
    audit(actor, 'password_changed', actor.pk)
    return {'changed': True, 'reauthentication_required': True}
