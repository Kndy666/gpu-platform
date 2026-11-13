from django import forms
from django.contrib.auth import get_user_model, password_validation
from django.core.exceptions import ValidationError
from django.db import transaction

from .models import PlatformSettings, UserGroup, UserProfile, GROUP_QUOTA_FIELDS, NotificationSettings, APPROVAL_CHOICES
from .services import audit, resize_home
from .identity import validate_login_name


class MemberForm(forms.Form):
    username = forms.CharField(label='用户名', max_length=32)
    first_name = forms.CharField(label='姓名', max_length=150, required=False)
    email = forms.EmailField(label='邮箱', required=False)
    group = forms.ModelChoiceField(label='用户组', queryset=UserGroup.objects.none(), empty_label=None)
    home_quota_gib = forms.IntegerField(label='个人卷 GiB', min_value=1, initial=50)
    password = forms.CharField(label='初始密码', required=False, widget=forms.PasswordInput(attrs={'autocomplete': 'new-password'}))
    is_active = forms.BooleanField(label='启用账号', required=False, initial=True)
    is_staff = forms.BooleanField(label='管理员', required=False)
    inherit_quotas = forms.BooleanField(label='沿用用户组默认额度', required=False, initial=True)
    unlimited = forms.BooleanField(label='资源不限额', required=False)
    extension_limit = forms.IntegerField(label='每个环境可延期次数', min_value=0, required=False,
        help_text='留空沿用用户组；0 表示关闭用户延期申请。')

    def __init__(self, *args, actor, member=None, **kwargs):
        self.actor, self.member = actor, member
        if member:
            kwargs.setdefault('initial', {**{k: getattr(member, k) for k in ('username', 'first_name', 'email', 'is_active', 'is_staff')},
                                          'group': member.profile.group_id, 'home_quota_gib': member.profile.home_quota_gib})
        super().__init__(*args, **kwargs)
        from .services import policy_for
        policy = policy_for(member) if member else UserGroup.objects.filter(is_default=True).first() or UserGroup.objects.first()
        self.initial['inherit_quotas'] = not bool(member and member.profile.quota_overrides)
        self.initial['unlimited'] = policy.unlimited if policy else False
        if member:
            self.initial['extension_limit'] = member.profile.extension_limit
        self.fields['extension_limit'].widget.attrs['placeholder'] = '沿用用户组：' + str(policy.extension_limit if policy else 1)
        for name in GROUP_QUOTA_FIELDS:
            model_field = UserGroup._meta.get_field(name)
            self.fields[name] = forms.IntegerField(label=model_field.verbose_name, required=False,
                min_value=0 if name.startswith('gpu_') else 1,
                max_value=100 if name == 'gpu_compute_per_card_percent' else None)
            self.initial[name] = getattr(policy, name, None)
        self.fields['group'].queryset = UserGroup.objects.order_by('-is_default', 'name')
        self.group_defaults = {str(group.pk): {'unlimited': group.unlimited,
            **{name: getattr(group, name) for name in GROUP_QUOTA_FIELDS}}
            for group in self.fields['group'].queryset}
        if not member:
            self.initial['group'] = self.fields['group'].queryset.values_list('pk', flat=True).first()
        self.fields['is_staff'].disabled = not actor.is_superuser
        if member:
            self.fields['password'].label = '新密码'

    def clean(self):
        data = super().clean()
        name = data.get('username', '')
        get_user_model()._meta.get_field('username').run_validators(name)
        try:
            validate_login_name(name)
        except ValidationError as exc:
            self.add_error('username', exc)
        duplicate = get_user_model().objects.filter(username=name)
        if self.member:
            duplicate = duplicate.exclude(pk=self.member.pk)
        if duplicate.exists():
            self.add_error('username', '用户名已存在。')
        policy = data.get('group')
        if not policy:
            self.add_error('group', '请选择用户组。')
        inherited = data.get('inherit_quotas', False)
        # Omitted quota controls preserve the member's current configuration.
        if 'inherit_quotas' not in self.data and 'unlimited' not in self.data and not any(name in self.data for name in GROUP_QUOTA_FIELDS):
            inherited = not bool(self.member and self.member.profile.quota_overrides)
            data['inherit_quotas'] = inherited
            if not inherited:
                for name, value in self.member.profile.quota_overrides.items():
                    data[name] = value
        if not inherited and not data.get('unlimited'):
            for name in GROUP_QUOTA_FIELDS:
                if data.get(name) is None:
                    self.add_error(name, '请填写成员额度。')
        effective_unlimited = policy.unlimited if inherited and policy else data.get('unlimited')
        home_limit = policy.home_gib if inherited and policy else data.get('home_gib')
        if not effective_unlimited and home_limit is not None and data.get('home_quota_gib', 0) > home_limit:
            self.add_error('home_quota_gib', f'个人卷容量超过成员上限 {home_limit} GiB。')
        password = data.get('password')
        if not password and not self.member:
            self.add_error('password', '请设置初始密码。')
        if password:
            try:
                password_validation.validate_password(password, self.member or get_user_model()(username=name))
            except ValidationError as exc:
                self.add_error('password', exc)
        if self.member and self.member.pk == self.actor.pk and (not data.get('is_active') or not data.get('is_staff')):
            raise ValidationError('请通过另一位管理员调整自己的管理权限。')
        if self.member and self.member.is_superuser and (not data.get('is_active') or not data.get('is_staff')):
            raise ValidationError('平台管理员需要保持启用和管理权限。')
        return data

    @property
    def resource_fields(self):
        return [self[name] for name in GROUP_QUOTA_FIELDS]

    @transaction.atomic
    def save(self):
        data = self.cleaned_data
        user = self.member or get_user_model()()
        for field in ('username', 'first_name', 'email', 'is_active', 'is_staff'):
            setattr(user, field, data[field])
        if data['password']:
            user.set_password(data['password'])
        user.save()
        user.profile.group = data['group']
        user.profile.quota_overrides = {} if data['inherit_quotas'] else {'unlimited': data['unlimited'],
            **{name: data[name] for name in GROUP_QUOTA_FIELDS if not data['unlimited']}}
        user.profile.extension_limit = data.get('extension_limit')
        user.profile.save(update_fields=['group', 'quota_overrides', 'extension_limit'])
        resize_home(user, data['home_quota_gib'], self.actor)
        audit(self.actor, 'member_updated' if self.member else 'member_created', user.pk, username=user.username, group=data['group'].name, enabled=user.is_active, administrator=user.is_staff)
        return user


class GroupForm(forms.ModelForm):
    make_default = forms.BooleanField(label='新成员默认加入', required=False)

    class Meta:
        model = UserGroup
        fields = ['name', 'unlimited', *GROUP_QUOTA_FIELDS, 'extension_limit']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.initial['make_default'] = self.instance.is_default
        if self.instance.is_default:
            self.fields['make_default'].disabled = True
        unlimited = self.fields['unlimited'].clean(self.data.get('unlimited')) if self.is_bound else self.instance.unlimited
        for name, field in self.fields.items():
            if name in {'name', 'make_default', 'unlimited', 'extension_limit'}:
                continue
            field.required = not unlimited
            field.disabled = unlimited
            if unlimited:
                self.initial[name] = None
            field.min_value = 0 if name.startswith('gpu_') else 1
            field.widget.attrs['min'] = field.min_value
            if name.startswith('gpu_'):field.widget.attrs.update(step=1,**{'aria-label':field.label})
        self.fields['gpu_compute_per_card_percent'].widget.attrs['max']=100
        self.fields['extension_limit'].required = False
        self.fields['extension_limit'].min_value = 0
        self.fields['extension_limit'].widget.attrs['min'] = 0

    def clean(self):
        data = super().clean()
        if data.get('extension_limit') is None:
            data['extension_limit'] = self.instance.extension_limit
        for name in GROUP_QUOTA_FIELDS:
            value = data.get(name)
            if data.get('unlimited'):
                data[name] = None
                continue
            if value is None:
                continue
            if value < (0 if name.startswith('gpu_') else 1):
                self.add_error(name, '请输入有效的配额。')
        home_limit = data.get('home_gib')
        if not data.get('unlimited') and self.instance.pk and home_limit is not None and self.instance.members.filter(quota_overrides={}, home_quota_gib__gt=home_limit).exists():
            self.add_error('home_gib', '部分成员的个人卷额度超过此值，请先调整成员额度。')
        return data

    def audit_data(self):
        return {name: value for name, value in self.cleaned_data.items() if value is not None}

    @transaction.atomic
    def save(self, commit=True):
        group = super().save(commit=False)
        if self.cleaned_data['make_default']:
            UserGroup.objects.filter(is_default=True).exclude(pk=group.pk).update(is_default=False)
            group.is_default = True
        group.save()
        return group


class DeleteGroupForm(forms.Form):
    destination = forms.ModelChoiceField(label='将成员移至', queryset=UserGroup.objects.none(), required=False, empty_label=None)

    def __init__(self, *args, group, **kwargs):
        self.group = group
        super().__init__(*args, **kwargs)
        self.fields['destination'].queryset = UserGroup.objects.exclude(pk=group.pk).order_by('-is_default', 'name')
        self.fields['destination'].required = group.members.exists()
        if not self.fields['destination'].required:
            self.fields.pop('destination')

    def clean(self):
        data = super().clean()
        if not UserGroup.objects.exclude(pk=self.group.pk).exists():
            raise ValidationError('至少保留一个用户组。')
        destination = data.get('destination')
        if destination and not destination.unlimited and self.group.members.filter(quota_overrides={}, home_quota_gib__gt=destination.home_gib).exists():
            self.add_error('destination', '目标用户组的个人卷上限低于部分成员的现有额度。')
        return data

    @transaction.atomic
    def save(self, actor):
        group = UserGroup.objects.get(pk=self.group.pk)
        destination = self.cleaned_data.get('destination') or UserGroup.objects.exclude(pk=group.pk).order_by('-is_default', 'pk').first()
        if destination is None:
            raise ValidationError('至少保留一个用户组。')
        if not destination.unlimited and group.members.filter(quota_overrides={}, home_quota_gib__gt=destination.home_gib).exists():
            raise ValidationError('目标用户组的个人卷额度不足。')
        moved = UserProfile.objects.filter(group=group).update(group=destination)
        if group.is_default:
            UserGroup.objects.filter(pk=group.pk).update(is_default=False)
            UserGroup.objects.filter(pk=destination.pk).update(is_default=True)
        audit(actor, 'group_deleted', group.pk, name=group.name, destination=destination.name, moved_members=moved)
        group.delete()


class RecycleSettingsForm(forms.ModelForm):
    class Meta:
        model = PlatformSettings
        fields = ['workspace_retention_days', 'snapshot_hours', 'failed_snapshot_hours']

    def clean(self):
        data = super().clean()
        for name, value in list(data.items()):
            maximum = 3650 if name == 'workspace_retention_days' else 8760
            if not 1 <= value <= maximum:
                self.add_error(name, f'请输入 1–{maximum}。')
        return data


class ApprovalRuleForm(forms.Form):
    scope = forms.ChoiceField(choices=[('group', '用户组'), ('member', '成员')])
    target = forms.IntegerField(min_value=1)
    mode = forms.ChoiceField(choices=[('', '沿用用户组'), *APPROVAL_CHOICES], required=False)

    def clean(self):
        data = super().clean()
        if data.get('scope') == 'group' and not data.get('mode'):
            self.add_error('mode', '请选择人工审批或自动通过。')
        return data

    @transaction.atomic
    def save(self, actor):
        from django.shortcuts import get_object_or_404
        scope, target, mode = (self.cleaned_data[name] for name in ('scope', 'target', 'mode'))
        obj = get_object_or_404(UserGroup, pk=target) if scope == 'group' else get_object_or_404(UserProfile, user_id=target)
        obj.approval_mode = mode
        obj.save(update_fields=['approval_mode'])
        audit(actor, 'approval_rule_updated', target, scope=scope, mode=mode)
        return obj


class PushSettingsForm(forms.ModelForm):
    token = forms.CharField(label='PushPlus Token', required=False, widget=forms.PasswordInput(attrs={'autocomplete': 'new-password'}))
    events = forms.MultipleChoiceField(label='推送事件', required=False, widget=forms.CheckboxSelectMultiple)

    class Meta:
        model = NotificationSettings
        fields = ['enabled', 'events', 'reminder_minutes']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from .notifications import EVENTS
        self.fields['events'].choices = list(EVENTS.items())

    def clean(self):
        data = super().clean()
        token = data.get('token', '').strip()
        if token and (len(token) != 32 or not all(char in '0123456789abcdefABCDEF' for char in token)):
            self.add_error('token', '请输入完整的 32 位 PushPlus Token。')
        if data.get('enabled') and not token and not self.instance.token_ciphertext:
            self.add_error('token', '启用推送前请填写 Token。')
        minutes = data.get('reminder_minutes')
        if minutes is not None and not 1 <= minutes <= 1440:
            self.add_error('reminder_minutes', '请输入 1–1440 分钟。')
        return data

    def save(self, commit=True):
        obj = super().save(commit=False)
        if self.cleaned_data.get('token'):
            from .notifications import encode_token
            obj.token_ciphertext = encode_token(self.cleaned_data['token'].strip())
        if commit:
            obj.save()
        return obj
