from django import template
from django.urls import reverse

from portal.models import ExtensionRequest, ResourceRequest

register = template.Library()


@register.inclusion_tag('portal/manage_navigation.html', takes_context=True)
def management_navigation(context):
    section = context['section']
    groups = [
        ('概览', [('overview', '概览', 'management')]),
        ('资源管理', [
            ('nodes', '节点', 'manage_nodes'),
            ('performance', '性能', 'manage_performance'),
            ('containers', '容器', 'manage_containers'),
            ('storage', '存储', 'manage_storage'),
            ('images', '镜像', 'manage_images'),
            ('datasets', '公用目录', 'manage_datasets'),
        ]),
        ('成员与权限', [
            ('members', '成员', 'manage_members'),
            ('groups', '用户组', 'manage_groups'),
        ]),
        ('审批与推送', [('approvals', '审批与推送', 'manage_approvals')]),
        ('系统', [
            ('settings', '保留设置', 'manage_settings'),
            ('audits', '审计记录', 'manage_audits'),
        ]),
    ]
    pending_count = (ResourceRequest.objects.filter(status='submitted').count()
                     + ExtensionRequest.objects.filter(status='pending').count())
    primary, secondary = [], []
    for label, entries in groups:
        active = any(key == section for key, _, _ in entries)
        primary.append({'label': label, 'url': reverse(entries[0][2]), 'active': active,
                        'count': pending_count if label == '审批与推送' else 0})
        if active and len(entries) > 1:
            secondary = [{'label': title, 'url': reverse(route), 'active': key == section}
                         for key, title, route in entries]
    if section == 'approvals':
        tab = context.get('approval_tab', 'pending')
        secondary = [{'label': label, 'url': reverse('manage_approvals') + '?tab=' + key,
                      'active': tab == key}
                     for key, label in [('pending', '待审批'), ('push', '推送设置'), ('history', '发送记录')]]
    return {'primary': primary, 'secondary': secondary}
