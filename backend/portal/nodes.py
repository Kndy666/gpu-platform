"""Node registration shared by the portal, CLI and API."""
from django import forms
from django.shortcuts import get_object_or_404
from .models import ComputeNode, ResourceRequest, default_node_id


def selected_node(value=None):
    from django.http import Http404
    try:
        identifier = int(value) if value is not None and value != '' else default_node_id()
    except (ValueError, TypeError) as exc:
        raise Http404('无效节点') from exc
    return get_object_or_404(ComputeNode, pk=identifier)


def node_data(node, administrator=False):
    result = {'id': node.pk, 'name': node.name, 'architecture': node.architecture,
              'transport': node.transport, 'enabled': node.enabled, 'maintenance': node.maintenance,
              'state': node.state, 'state_label': node.get_state_display(), 'last_seen': node.last_seen,
              'error': node.error}
    if administrator:
        result.update(ssh_port=node.ssh_port, ssh_user=node.ssh_user, identity=node.identity)
    return result


class NodeForm(forms.ModelForm):
    class Meta:
        model = ComputeNode
        fields = ['name', 'transport', 'ssh_port', 'ssh_user', 'identity', 'architecture', 'enabled', 'maintenance']
        labels = {'transport': '连接方式', 'ssh_port': '主机上的反向 SSH 端口', 'ssh_user': '从机服务账户',
                  'architecture': 'CPU 架构', 'enabled': '启用状态同步'}

    def clean(self):
        values = super().clean()
        connection_fields = {'name', 'transport', 'ssh_port', 'ssh_user', 'identity', 'architecture'}
        if self.instance.pk and connection_fields.intersection(self.changed_data):
            if ResourceRequest.objects.filter(node=self.instance, workspace__cleanup_complete=False).exists():
                raise forms.ValidationError('请先结束并释放该节点的任务，再修改连接配置。')
            original = ComputeNode.objects.get(pk=self.instance.pk)
            if original.transport == 'local' and (values.get('transport') != 'local' or values.get('name') != 'local'):
                raise forms.ValidationError('主机节点的标识和连接方式固定。')
        if values.get('transport') == 'local' and ComputeNode.objects.filter(transport='local').exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError('只能有一个本机执行节点。')
        return values
