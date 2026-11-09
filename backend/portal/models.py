import uuid
from django.conf import settings
from django.db import models
from django.db.models import Q
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator

GROUP_QUOTA_FIELDS = ('gpu_compute_percent', 'gpu_memory_gib', 'gpu_compute_per_card_percent', 'gpu_memory_per_card_gib', 'cpu', 'memory_gib', 'workspace_gib', 'home_gib', 'image_gib',
                      'concurrent_tasks', 'interactive_hours')

class UserGroup(models.Model):
    name = models.CharField(max_length=80, unique=True, verbose_name='用户组名称')
    is_default = models.BooleanField(default=False, verbose_name='默认用户组')
    unlimited = models.BooleanField(default=False, verbose_name='资源不限额')
    gpu_compute_percent = models.PositiveIntegerField(default=200, null=True, blank=True, verbose_name='总算力 %')
    gpu_memory_gib = models.PositiveIntegerField(default=48, null=True, blank=True, verbose_name='总显存 GiB')
    gpu_compute_per_card_percent = models.PositiveIntegerField(default=100, null=True, blank=True, validators=[MaxValueValidator(100)],verbose_name='单卡算力上限 %')
    gpu_memory_per_card_gib = models.PositiveIntegerField(default=24, null=True, blank=True, verbose_name='单卡显存上限 GiB')
    cpu = models.PositiveIntegerField(default=32, null=True, blank=True, verbose_name='CPU 核')
    memory_gib = models.PositiveIntegerField(default=96, null=True, blank=True, verbose_name='内存 GiB')
    workspace_gib = models.PositiveIntegerField(default=500, null=True, blank=True, verbose_name='工作卷 GiB')
    home_gib = models.PositiveIntegerField(default=50, null=True, blank=True, verbose_name='个人卷上限 GiB')
    image_gib = models.PositiveIntegerField(default=50, null=True, blank=True, verbose_name='镜像额度 GiB')
    concurrent_tasks = models.PositiveIntegerField(default=1, null=True, blank=True, verbose_name='并发任务数')
    interactive_hours = models.PositiveIntegerField(default=48, null=True, blank=True, verbose_name='最长环境时段（小时）')

    def clean(self):
        super().clean()
        if self.unlimited:
            for name in GROUP_QUOTA_FIELDS:
                setattr(self, name, None)
        else:
            errors = {name: '请设置资源限额。' for name in GROUP_QUOTA_FIELDS if getattr(self, name) is None}
            if errors:
                raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.clean()
        if self.unlimited and kwargs.get('update_fields') is not None:
            kwargs['update_fields'] = set(kwargs['update_fields']) | set(GROUP_QUOTA_FIELDS)
        return super().save(*args, **kwargs)

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = '用户组'
        verbose_name_plural = verbose_name
        constraints = [models.UniqueConstraint(fields=['is_default'], condition=Q(is_default=True), name='one_default_user_group'),
            models.CheckConstraint(name='group_quota_mode', condition=(
                Q(unlimited=True, **{name+'__isnull': True for name in GROUP_QUOTA_FIELDS}) |
                Q(unlimited=False, **{name+'__isnull': False for name in GROUP_QUOTA_FIELDS},
                  gpu_compute_per_card_percent__lte=100,
                  **{name+'__gte': 0 if name.startswith('gpu_') else 1 for name in GROUP_QUOTA_FIELDS}))) ]

class UserProfile(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='profile', verbose_name='用户')
    group = models.ForeignKey(UserGroup, on_delete=models.PROTECT, related_name='members', verbose_name='用户组')
    uid = models.PositiveIntegerField(default=1000)
    gid = models.PositiveIntegerField(default=1000)
    home_quota_gib = models.PositiveIntegerField(default=50, verbose_name='个人卷 GiB')
    registry_secret = models.CharField(max_length=100, blank=True)

    class Meta:
        verbose_name = '用户与个人卷'
        verbose_name_plural = verbose_name

class SSHKey(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, verbose_name='用户')
    name = models.CharField(max_length=100, verbose_name='名称')
    public_key = models.TextField(verbose_name='公钥')
    fingerprint = models.CharField(max_length=100, verbose_name='指纹')
    enabled = models.BooleanField(default=True, verbose_name='启用')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='创建时间')

    class Meta:
        verbose_name = 'SSH 公钥'
        verbose_name_plural = verbose_name
        constraints = [models.UniqueConstraint(fields=['user', 'fingerprint'], name='unique_user_key')]

class StoragePool(models.Model):
    name = models.SlugField(unique=True, verbose_name='名称')
    path = models.CharField(max_length=500, unique=True)
    storage_class = models.CharField(max_length=100)
    capacity_gib = models.PositiveBigIntegerField(default=0, verbose_name='总容量 GiB')
    free_gib = models.PositiveBigIntegerField(default=0, verbose_name='空闲 GiB')
    reserved_gib = models.PositiveBigIntegerField(default=0, verbose_name='已预留 GiB')
    buffer_percent = models.PositiveIntegerField(default=5, verbose_name='缓冲比例')
    healthy = models.BooleanField(default=False, verbose_name='健康')
    observed_at = models.DateTimeField(null=True, blank=True, verbose_name='更新时间')

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = '存储池'
        verbose_name_plural = verbose_name

class ImageArtifact(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, verbose_name='所属用户')
    name = models.CharField(max_length=120, verbose_name='名称')
    reference = models.CharField(max_length=500, verbose_name='镜像地址')
    digest = models.CharField(max_length=100, blank=True, verbose_name='内容指纹')
    size_bytes = models.PositiveBigIntegerField(default=0, verbose_name='大小（字节）')
    architecture = models.CharField(max_length=20, default='arm64', verbose_name='架构')
    shared = models.BooleanField(default=False, verbose_name='共享')
    verified = models.BooleanField(default=False, verbose_name='已验证')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='创建时间')
    temporary = models.BooleanField(default=False, verbose_name='运行快照')
    state = models.CharField(max_length=16, default='ready', choices=[('ready', '可用'), ('deleting', '回收中'), ('deleted', '已回收')], verbose_name='状态')
    recycle_after = models.DateTimeField(null=True, blank=True, verbose_name='回收时间')
    deleted_at = models.DateTimeField(null=True, blank=True, verbose_name='回收完成时间')
    cleanup_error = models.TextField(blank=True, verbose_name='回收错误')
    cleanup_retry_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = '环境镜像'
        verbose_name_plural = verbose_name

class PlatformSettings(models.Model):
    singleton = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    snapshot_hours = models.PositiveIntegerField(default=24, verbose_name='成功或取消后保留（小时）')
    failed_snapshot_hours = models.PositiveIntegerField(default=72, verbose_name='失败后保留（小时）')
    workspace_retention_days = models.PositiveIntegerField(default=3, verbose_name='工作卷保留（天）')

    @classmethod
    def load(cls):
        return cls.objects.get_or_create(singleton=1)[0]

class ResourceRequest(models.Model):
    class State(models.TextChoices):
        SUBMITTED = 'submitted', '待审核'
        REJECTED = 'rejected', '已拒绝'
        APPROVED = 'approved', '已批准'
        SCHEDULED = 'scheduled', '已预约'
        PREPARING = 'preparing', '准备环境'
        QUEUED = 'queued', '排队中'
        STARTING = 'starting', '启动中'
        RUNNING = 'running', '运行中'
        SUCCEEDED = 'succeeded', '成功'
        FAILED = 'failed', '失败'
        CANCELED = 'canceled', '人工取消'
        EXPIRED = 'expired', '交互任务到期'
        CLEANED = 'cleaned', '工作卷已回收'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, verbose_name='用户')
    image = models.ForeignKey(ImageArtifact, on_delete=models.PROTECT, verbose_name='镜像')
    login_name = models.CharField(max_length=32, blank=True, editable=False)
    image_reference = models.CharField(max_length=600, blank=True)
    gpu = models.PositiveIntegerField(default=0, verbose_name='GPU')
    gpu_requests = models.JSONField(default=list, blank=True, verbose_name='逐卡配置')
    cpu = models.PositiveIntegerField(default=8, verbose_name='CPU 核')
    memory_gib = models.PositiveIntegerField(default=32, verbose_name='内存 GiB')
    workspace_gib = models.PositiveIntegerField(default=100, verbose_name='工作卷 GiB')
    purpose = models.CharField(max_length=500, verbose_name='名称')
    notes = models.TextField(blank=True, verbose_name='备注')
    mode = models.CharField(max_length=16, choices=[('interactive', '开发环境'), ('batch', '运行程序')], verbose_name='运行模式')
    starts_at = models.DateTimeField(null=True, blank=True, verbose_name='开始时间')
    ends_at = models.DateTimeField(null=True, blank=True, verbose_name='结束时间')
    source_environment = models.ForeignKey('self', null=True, blank=True, on_delete=models.PROTECT, related_name='training_requests', verbose_name='源开发环境')
    command = models.TextField(blank=True, verbose_name='运行命令')
    argv = models.JSONField(default=list, blank=True)
    working_dir = models.CharField(max_length=500, default='/workspace', verbose_name='工作目录')
    environment = models.JSONField(default=dict, blank=True, verbose_name='环境变量')
    priority = models.IntegerField(default=0, verbose_name='优先级')
    status = models.CharField(max_length=16, choices=State.choices, default=State.SUBMITTED, verbose_name='状态')
    review_reason = models.TextField(blank=True, verbose_name='审核意见')
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='reviews', verbose_name='审核人')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='创建时间')
    reviewed_at = models.DateTimeField(null=True, blank=True, verbose_name='审核时间')

    class Meta:
        verbose_name = '资源申请'
        verbose_name_plural = verbose_name
        ordering = ['-created_at']
        constraints = [models.CheckConstraint(condition=Q(mode='interactive', starts_at__isnull=False, ends_at__isnull=False, ends_at__gt=models.F('starts_at')) | Q(mode='batch', ends_at__isnull=True), name='mode_schedule')]

    def __str__(self):
        return self.purpose

    @property
    def login_user(self):
        return self.login_name or self.user.username

    @property
    def home_dir(self):
        return '/home/' + self.login_user

    @property
    def gpu_compute_total(self):
        return sum(item['compute_percent'] for item in self.gpu_requests)

    @property
    def gpu_memory_total_gib(self):
        return sum(item['memory_gib'] for item in self.gpu_requests)

    @property
    def gpu_rows(self):
        workspace=getattr(self,'workspace',None)
        allocations={item['slot']:item for item in workspace.gpu_allocations} if workspace else {}
        return [{'number':slot+1,**request,'allocation':allocations.get(slot)} for slot,request in enumerate(self.gpu_requests)]

    @property
    def can_stop(self):
        return self.status in {'submitted', 'approved', 'scheduled', 'preparing', 'queued', 'starting', 'running'}

    @property
    def stop_label(self):
        return '停止任务' if self.status in {'starting', 'running'} else '取消任务'

    @property
    def stop_pending_label(self):
        return '正在停止' if self.status in {'starting', 'running'} else '正在取消'

class VolumeAllocation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, verbose_name='用户')
    request = models.OneToOneField(ResourceRequest, on_delete=models.PROTECT, null=True, blank=True, verbose_name='资源申请')
    kind = models.CharField(max_length=10, choices=[('home', '永久卷'), ('work', '工作卷')], verbose_name='类型')
    pool = models.ForeignKey(StoragePool, on_delete=models.PROTECT, verbose_name='存储池')
    size_gib = models.PositiveIntegerField(verbose_name='容量 GiB')
    used_bytes = models.PositiveBigIntegerField(null=True, blank=True, verbose_name='已用字节')
    pvc_name = models.CharField(max_length=100, unique=True, verbose_name='卷名称')
    state = models.CharField(max_length=20, default='reserved')
    retain_until = models.DateTimeField(null=True, blank=True, verbose_name='保留至')
    notified_at = models.DateTimeField(null=True, blank=True, verbose_name='通知时间')
    deletion_error = models.TextField(blank=True, verbose_name='删除错误')
    deletion_pv_name = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='创建时间')

    class Meta:
        verbose_name = '数据卷'
        verbose_name_plural = verbose_name
        constraints = [models.UniqueConstraint(fields=['user'], condition=Q(kind='home') & ~Q(state='deleted'), name='one_home_per_user')]

class Workspace(models.Model):
    request = models.OneToOneField(ResourceRequest, on_delete=models.PROTECT, primary_key=True, verbose_name='资源申请')
    object_name = models.CharField(max_length=100, unique=True, verbose_name='对象名称')
    ssh_port = models.PositiveIntegerField(null=True, blank=True, unique=True, verbose_name='SSH 端口')
    started_at = models.DateTimeField(null=True, blank=True, verbose_name='启动时间')
    dispatch_started_at = models.DateTimeField(null=True, blank=True)
    expire_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True, verbose_name='结束时间')
    exit_code = models.IntegerField(null=True, blank=True, verbose_name='退出码')
    error = models.TextField(blank=True, verbose_name='错误')
    stop_requested = models.BooleanField(default=False, verbose_name='已请求停止')
    cleanup_complete = models.BooleanField(default=False, verbose_name='已释放资源')
    gpu_devices = models.JSONField(default=list, blank=True)
    gpu_allocations = models.JSONField(default=list, blank=True)

    class Meta:
        verbose_name = '运行实例'
        verbose_name_plural = verbose_name

class EnvironmentSnapshot(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    source = models.ForeignKey(ResourceRequest, on_delete=models.PROTECT, related_name='snapshots', verbose_name='源环境')
    target = models.OneToOneField(ResourceRequest, null=True, blank=True, on_delete=models.PROTECT, related_name='environment_snapshot', verbose_name='对象')
    status = models.CharField(max_length=20, default='pending', choices=[('pending', '等待保存'), ('working', '保存中'), ('succeeded', '已保存'), ('failed', '保存失败')], verbose_name='状态')
    artifact = models.ForeignKey(ImageArtifact, null=True, blank=True, on_delete=models.PROTECT, verbose_name='保存的镜像')
    error = models.TextField(blank=True, verbose_name='错误')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='创建时间')

    class Meta:
        constraints = [models.UniqueConstraint(fields=['source'], condition=Q(status__in=['pending', 'working']), name='one_active_environment_save')]
        verbose_name = '环境保存'
        verbose_name_plural = verbose_name

class ImageBuild(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, verbose_name='用户')
    name = models.SlugField(max_length=80, verbose_name='名称')
    dockerfile = models.TextField(verbose_name='Dockerfile')
    context_git = models.URLField(blank=True, verbose_name='Git 地址')
    bootstrap = models.BooleanField(default=False, editable=False)
    status = models.CharField(max_length=20, default='submitted', choices=[('submitted', '待构建'), ('building', '构建中'), ('succeeded', '完成'), ('failed', '失败')], verbose_name='状态')
    artifact = models.ForeignKey(ImageArtifact, null=True, blank=True, on_delete=models.PROTECT, verbose_name='保存的镜像')
    log = models.TextField(blank=True, verbose_name='构建日志')
    error = models.TextField(blank=True, verbose_name='错误')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='创建时间')

    class Meta:
        verbose_name = '镜像构建'
        verbose_name_plural = verbose_name

class AuditLog(models.Model):
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, verbose_name='操作人')
    action = models.CharField(max_length=80, verbose_name='操作')
    target = models.CharField(max_length=100, verbose_name='对象')
    detail = models.JSONField(default=dict, verbose_name='详情')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='创建时间')

    class Meta:
        verbose_name = '审计记录'
        verbose_name_plural = verbose_name

class SystemObservation(models.Model):
    name = models.CharField(max_length=80, unique=True, verbose_name='名称')
    data = models.JSONField(default=dict, verbose_name='观测数据')
    observed_at = models.DateTimeField(auto_now=True, verbose_name='更新时间')

    class Meta:
        verbose_name = '资源观测'
        verbose_name_plural = verbose_name
