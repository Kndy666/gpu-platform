from django import template
from django.urls import reverse

from portal.models import ExtensionRequest, ResourceRequest

register = template.Library()


@register.inclusion_tag('portal/manage_navigation.html', takes_context=True)
def management_navigation(context):
    section = context['section']
    current = section
    if section == 'approvals':
        current = 'approvals-' + context.get('approval_tab', 'pending')
    approval_url = reverse('manage_approvals')
    groups = [
        ('概览', [('overview', '概览', reverse('management'))]),
        ('资源管理', [
            ('nodes', '节点', reverse('manage_nodes')),
            ('performance', '性能', reverse('manage_performance')),
            ('containers', '容器', reverse('manage_containers')),
            ('storage', '存储', reverse('manage_storage')),
            ('images', '镜像', reverse('manage_images')),
            ('datasets', '公用目录', reverse('manage_datasets')),
        ]),
        ('成员与权限', [
            ('members', '成员', reverse('manage_members')),
            ('groups', '用户组', reverse('manage_groups')),
        ]),
        ('审批与推送', [
            ('approvals-pending', '待审批', approval_url + '?tab=pending'),
            ('approvals-push', '推送设置', approval_url + '?tab=push'),
            ('approvals-history', '发送记录', approval_url + '?tab=history'),
        ]),
        ('系统', [
            ('settings', '保留设置', reverse('manage_settings')),
            ('audits', '审计记录', reverse('manage_audits')),
        ]),
    ]
    pending_count = (ResourceRequest.objects.filter(status='submitted').count()
                     + ExtensionRequest.objects.filter(status='pending').count())
    navigation = []
    for label, entries in groups:
        items = [{'label': title, 'url': url, 'active': key == current}
                 for key, title, url in entries]
        navigation.append({'label': label, 'items': items,
                           'active': any(item['active'] for item in items),
                           'count': pending_count if label == '审批与推送' else 0})
    return {'navigation': navigation, 'current_title': context['section_title']}
