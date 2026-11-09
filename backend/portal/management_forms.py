from django import forms
from django.contrib.auth import get_user_model, password_validation
from django.core.exceptions import ValidationError
from django.db import transaction

from .models import PlatformSettings, UserGroup, UserProfile, GROUP_QUOTA_FIELDS
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

    def __init__(self, *args, actor, member=None, **kwargs):
        self.actor, self.member = actor, member
        if member:
            kwargs.setdefault('initial', {**{k: getattr(member, k) for k in ('username', 'first_name', 'email', 'is_active', 'is_staff')},
                                          'group': member.profile.group_id, 'home_quota_gib': member.profile.home_quota_gib})
        super().__init__(*args, **kwargs)
        self.fields['group'].queryset = UserGroup.objects.order_by('-is_default', 'name')
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
        elif not policy.unlimited and data.get('home_quota_gib', 0) > policy.home_gib:
            self.add_error('home_quota_gib', f'该用户组的个人卷上限为 {policy.home_gib} GiB。')
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
        user.profile.save(update_fields=['group'])
        resize_home(user, data['home_quota_gib'], self.actor)
        audit(self.actor, 'member_updated' if self.member else 'member_created', user.pk, username=user.username, group=data['group'].name, enabled=user.is_active, administrator=user.is_staff)
        return user


class GroupForm(forms.ModelForm):
    make_default = forms.BooleanField(label='新成员默认加入', required=False)

    class Meta:
        model = UserGroup
        fields = ['name', 'unlimited', *GROUP_QUOTA_FIELDS]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.initial['make_default'] = self.instance.is_default
        if self.instance.is_default:
            self.fields['make_default'].disabled = True
        unlimited = self.fields['unlimited'].clean(self.data.get('unlimited')) if self.is_bound else self.instance.unlimited
        for name, field in self.fields.items():
            if name in {'name', 'make_default', 'unlimited'}:
                continue
            field.required = not unlimited
            field.disabled = unlimited
            if unlimited:
                self.initial[name] = None
            field.min_value = 0 if name.startswith('gpu_') else 1
            field.widget.attrs['min'] = field.min_value
            if name.startswith('gpu_'):field.widget.attrs.update(step=1,**{'aria-label':field.label})
        self.fields['gpu_compute_per_card_percent'].widget.attrs['max']=100

    def clean(self):
        data = super().clean()
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
        if not data.get('unlimited') and self.instance.pk and home_limit is not None and self.instance.members.filter(home_quota_gib__gt=home_limit).exists():
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
        if destination and not destination.unlimited and self.group.members.filter(home_quota_gib__gt=destination.home_gib).exists():
            self.add_error('destination', '目标用户组的个人卷上限低于部分成员的现有额度。')
        return data

    @transaction.atomic
    def save(self, actor):
        group = UserGroup.objects.get(pk=self.group.pk)
        destination = self.cleaned_data.get('destination') or UserGroup.objects.exclude(pk=group.pk).order_by('-is_default', 'pk').first()
        if destination is None:
            raise ValidationError('至少保留一个用户组。')
        if not destination.unlimited and group.members.filter(home_quota_gib__gt=destination.home_gib).exists():
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
