from django import forms
from django.db.models import Q
from django.core.exceptions import ValidationError
from .models import ResourceRequest, SSHKey, ImageArtifact, ImageBuild, ComputeNode, default_node_id
from .services import parse_ssh_key, validate_request, gpu_request_limits
from .gpu_math import has_gpu_max,resolve_gpu_max
from .api_auth import TOKEN_LIFETIMES, TOKEN_SECONDS


def request_defaults(mode):
    data = {'gpu': 0 if mode == 'interactive' else 1, 'cpu': 2 if mode == 'interactive' else 8,
            'gpu_requests':[] if mode=='interactive' else [{'compute_percent':100,'memory_gib':8}],
            'memory_gib': 4 if mode == 'interactive' else 16, 'workspace_gib': 20, 'working_dir': '/workspace'}
    if mode == 'interactive':
        from datetime import timedelta
        from django.utils import timezone
        start = timezone.localtime().replace(second=0, microsecond=0)
        data.update(starts_at=start, ends_at=start+timedelta(hours=4))
    return data


def default_image(user, node=None):
    query = ImageArtifact.objects.filter(Q(owner=user) | Q(shared=True), verified=True)
    if node:
        query = query.filter(node=node)
    return query.order_by('-shared', '-created_at').first()


class TokenIssueForm(forms.Form):
    name = forms.CharField(label='名称', max_length=100, required=False,
                           widget=forms.TextInput(attrs={'placeholder': '例如：笔记本、Agent'}))
    expires_in = forms.TypedChoiceField(label='有效期', choices=TOKEN_LIFETIMES,
                                       coerce=int, initial=TOKEN_SECONDS)

class SSHKeyForm(forms.ModelForm):
    class Meta:
        model = SSHKey
        fields = ['name', 'public_key']
        labels = {'name': '密钥名称', 'public_key': 'SSH 公钥'}
        widgets = {'public_key': forms.Textarea(attrs={'rows': 3, 'placeholder': 'ssh-ed25519 AAAA…'})}

    def clean_public_key(self):
        key, fp = parse_ssh_key(self.cleaned_data['public_key'])
        self.instance.fingerprint = fp
        return key

class RequestForm(forms.ModelForm):
    gpu_requests = forms.JSONField(required=False,widget=forms.HiddenInput,label='逐卡配置')
    source_kind = forms.ChoiceField(choices=[('image', '镜像'), ('environment', '已配置的环境')], required=False, initial='image')
    source_environment = forms.ModelChoiceField(queryset=ResourceRequest.objects.none(), required=False, label='开发环境')
    command = forms.CharField(required=False, label='运行命令', widget=forms.Textarea(attrs={'rows': 4}))
    variables = forms.CharField(required=False, label='环境变量', widget=forms.Textarea(attrs={'rows': 3, 'placeholder': 'OMP_NUM_THREADS=8'}))
    class Meta:
        model = ResourceRequest
        fields = ['node', 'purpose', 'image', 'source_environment', 'gpu', 'gpu_requests', 'cpu', 'memory_gib', 'workspace_gib', 'starts_at', 'ends_at', 'command', 'working_dir', 'notes']
        labels = {'purpose': '名称', 'image': '环境镜像', 'gpu': 'GPU', 'cpu': 'CPU 核', 'memory_gib': '内存 GiB', 'workspace_gib': '工作卷 GiB', 'starts_at': '开始', 'ends_at': '结束', 'working_dir': '工作目录', 'notes': '备注'}
        widgets = {'starts_at': forms.DateTimeInput(format='%Y-%m-%dT%H:%M', attrs={'type': 'datetime-local'}), 'ends_at': forms.DateTimeInput(format='%Y-%m-%dT%H:%M', attrs={'type': 'datetime-local'}), 'notes': forms.Textarea(attrs={'rows': 2})}

    def __init__(self, *args, user, mode, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.user, self.instance.mode = user, mode
        self.instance.login_name = user.username
        selected = self.data.get('node') or self.initial.get('node') or default_node_id()
        if isinstance(selected, ComputeNode):
            selected = selected.pk
        self.node = ComputeNode.objects.filter(pk=selected).first() if str(selected).isdigit() else None
        self.node = self.node or ComputeNode.objects.get(pk=default_node_id())
        self.instance.node = self.node
        self.initial['node'] = self.node.pk
        self.fields['node'].queryset = ComputeNode.objects.filter(enabled=True)
        self.fields['node'].required = False
        self.fields['node'].empty_label = None
        self.fields['node'].widget.attrs['data-node-select'] = 'request'
        self.fields['gpu'].label='GPU 数量'
        from .models import SystemObservation
        inventory=SystemObservation.objects.filter(node=self.node, name='gpu_inventory').first()
        self.gpu_limits=gpu_request_limits(user,inventory,node=self.node)
        count=len(inventory.data.get('devices',[])) if inventory else 4
        self.fields['gpu'].widget=forms.Select(choices=[(i,f'{i} 张') for i in range(count+1)])
        self.fields['command'].widget.attrs['placeholder'] = f'/home/{user.username}/venvs/app/bin/python /home/{user.username}/project/main.py'
        self.mode = mode
        self.fields['image'].queryset = ImageArtifact.objects.filter(Q(owner=user) | Q(shared=True), node=self.node, verified=True).order_by('-shared', '-created_at')
        self.fields['image'].required = False
        if mode == 'batch' and self.data.get('source_kind') == 'environment':
            self.fields['workspace_gib'].required = False
        self.fields['source_environment'].queryset = ResourceRequest.objects.filter(node=self.node, user=user, mode='interactive', status='running').exclude(workspace__stop_requested=True)
        images = self.fields['image'].queryset
        environments = self.fields['source_environment'].queryset
        self.fields['image'].empty_label = None if images.exists() else '暂无可用镜像'
        self.fields['source_environment'].empty_label = None if environments.exists() else '暂无运行中的开发环境'
        self.source_workspaces={str(source.pk):{'size_gib':getattr(source,'volumeallocation',None).size_gib if getattr(source,'volumeallocation',None) else source.workspace_gib}
            for source in environments.select_related('volumeallocation')}
        if not self.initial.get('source_environment'):
            self.initial['source_environment'] = environments.values_list('pk', flat=True).first()
        for key in ('starts_at', 'ends_at'):
            self.fields[key].required = mode == 'interactive'
        defaults = request_defaults(mode)
        if 'workspace_gib' in kwargs.get('initial', {}):
            defaults.pop('workspace_gib')
        self.initial.update(defaults)
        if not self.initial.get('image'):
            self.initial['image'] = (images.filter(shared=True).values_list('pk', flat=True).first() or images.values_list('pk', flat=True).first())

    def clean(self):
        data = super().clean()
        data['node'] = data.get('node') or self.node
        if data.get('gpu_requests') is None:data['gpu_requests']=[]
        if has_gpu_max(data['gpu_requests']):
            try:data['gpu_requests']=resolve_gpu_max(data['gpu_requests'],self.gpu_limits)
            except ValueError as exc:self.add_error('gpu_requests',str(exc))
        source = data.get('source_environment') if self.mode == 'batch' and data.get('source_kind') == 'environment' else None
        data['source_environment'] = source
        if data.get('source_kind') == 'environment' and self.mode == 'batch' and not source:
            self.add_error('source_environment', '请选择开发环境。')
        if source:
            self.instance.login_name = source.login_user
            data['image'] = source.image
            if data.get('workspace_gib') is None:
                data['workspace_gib']=self.source_workspaces[str(source.pk)]['size_gib']
        if not data.get('image'):
            self.add_error('image', '请选择镜像。')
        for key, value in data.items():
            if key in self.Meta.fields:
                setattr(self.instance, key, value)
        self.instance.argv = ['/bin/bash', '-c', data.get('command', '').strip()] if self.mode == 'batch' else []
        self.instance.environment = {}
        if self.mode == 'batch' and not data.get('command', '').strip():
            self.add_error('command', '请填写运行命令。')
        for line in data.get('variables', '').splitlines():
            if not line.strip(): continue
            key, sep, value = line.partition('=')
            if not sep:
                self.add_error('variables', '每行填写 NAME=value。')
                break
            self.instance.environment[key.strip()] = value.strip()
        if data.get('image') and not self.errors:
            try:
                validate_request(self.instance)
            except ValidationError as e:
                self.add_error(None, e)
        return data

class ImageBuildForm(forms.ModelForm):
    class Meta:
        model = ImageBuild
        fields = ['node', 'name', 'dockerfile', 'context_git']
        labels = {'name': '版本名称', 'dockerfile': 'Dockerfile', 'context_git': '构建上下文 Git URL（可选，HTTPS）'}
        widgets = {'dockerfile': forms.Textarea(attrs={'rows': 10})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['node'].queryset = ComputeNode.objects.filter(enabled=True)
        self.fields['node'].required = False
        self.fields['node'].initial = default_node_id()
        self.fields['node'].empty_label = None

    def clean_node(self):
        node = self.cleaned_data.get('node') or ComputeNode.objects.get(pk=default_node_id())
        if not node.enabled or node.maintenance or (node.transport == 'ssh' and node.state != 'online'):
            raise ValidationError('所选节点暂不能构建镜像。')
        return node

    def clean_context_git(self):
        from urllib.parse import urlsplit
        import ipaddress
        value = self.cleaned_data['context_git']
        if value:
            try:
                url = urlsplit(value)
                port = url.port
            except ValueError as exc:
                raise ValidationError('请输入有效的 HTTPS Git URL。') from exc
            if url.scheme != 'https' or not url.hostname or url.username or url.password or port not in {None, 443}:
                raise ValidationError('只允许无内嵌凭证的 HTTPS Git URL。')
            if url.hostname in {'localhost', 'kubernetes.default.svc'} or '.' not in url.hostname:
                raise ValidationError('不允许内网构建上下文。')
            from .configuration import config
            if url.hostname not in config()['registry']['git_hosts']:
                raise ValidationError('请选择管理员配置的 Git 站点。')
            try:
                address = ipaddress.ip_address(url.hostname)
            except ValueError:
                pass
            else:
                if not address.is_global:
                    raise ValidationError('不允许内网构建上下文。')
        return value

class ReviewForm(forms.Form):
    action = forms.ChoiceField(choices=[('approve', '通过'), ('reject', '拒绝'), ('stop', '强制停止'), ('extend', '延长交互任务')])
    reason = forms.CharField(label='原因', widget=forms.Textarea(attrs={'rows': 2}), required=False)
    ends_at = forms.DateTimeField(label='新的结束时间', required=False, widget=forms.DateTimeInput(attrs={'type': 'datetime-local'}))
    priority = forms.ChoiceField(label='优先级', choices=[('0', '普通'), ('100', '高')], required=False)
