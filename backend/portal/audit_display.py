import json

from .models import ResourceRequest

ACTION_LABELS = {
    'extension_submitted': '申请延期', 'extension_approved': '延期通过', 'extension_rejected': '延期拒绝',
    'approval_rule_updated': '修改审批方式', 'push_settings_updated': '修改微信推送设置',
    'ending_soon': '环境即将到期', 'running': '容器已启动', 'node_offline': '节点离线',
    'submit': '提交申请', 'approve': '批准申请', 'reject': '拒绝申请', 'stop_requested': '停止任务',
    'finished': '任务结束', 'resources_released': '资源已释放', 'environment_saved': '环境已保存',
    'save_environment': '保存环境', 'image_kept': '保留镜像', 'image_recycle_requested': '安排镜像回收',
    'image_recycled': '镜像已回收', 'member_created': '增加成员', 'member_updated': '修改成员',
    'policy_updated': '修改角色配额', 'recycle_settings_updated': '修改回收设置', 'home_resize': '扩容个人卷',
    'group_created': '新建用户组', 'group_updated': '修改用户组', 'group_deleted': '删除用户组',
    'acceptance_cleanup': '清理验收数据', 'delivery_cleanup': '清理交付数据', 'cleanup': '清理数据',
    'volume_deleted': '数据卷已删除', 'volume_delete_requested': '删除数据卷', 'volume_deletion_notice': '工作卷到期提醒', 'ssh_key_add': '添加 SSH 公钥',
    'ssh_key_disable': '停用 SSH 公钥', 'ssh_key_enable': '启用 SSH 公钥', 'image_build_submit': '提交镜像构建', 'image_build_succeeded': '镜像构建完成',
    'api_token_issued': '生成 API 令牌', 'api_token_revoked': '撤销 API 令牌', 'password_changed': '修改密码',
    'image_build_failed': '镜像构建失败', 'long_batch': '长时间运行提醒', 'extend': '延长环境时段',
    'base_image_updated': '更新基础镜像', 'user_test_cleanup': '清理测试环境',
    'ssh_access_updated':'更新容器 SSH 权限',
    'workspace_copied':'调整工作卷容量',
    'task_deleted': '删除任务记录',
}


DETAIL_LABELS = {
    'extension_id': '延期申请', 'scope': '配置范围', 'mode': '审批方式', 'events': '推送事件', 'automatic': '自动审批',
    'users_removed': '已移除账号', 'requests_removed': '已移除任务', 'volumes_removed': '已移除数据卷',
    'remaining_accounts': '当前账号数', 'remaining_requests': '当前任务数', 'production_shared_images': '共享镜像数',
    'removed_test_image_tags': '已移除测试镜像版本', 'images_removed': '已移除镜像', 'pvc_names': '数据卷',
    'name': '名称', 'username': '用户名', 'role': '用户组', 'group': '用户组',
    'purpose': '任务名称', 'node': '节点', 'work_volume_id': '工作卷',
    'enabled': '启用账号', 'administrator': '管理员', 'reason': '原因', 'message': '说明',
    'status': '状态', 'exit_code': '退出码', 'error': '错误', 'priority': '优先级',
    'size_gib': '容量 GiB', 'size_bytes': '大小（字节）', 'reference': '镜像地址', 'digest': '内容指纹',
    'image': '镜像', 'pvc': '数据卷', 'fingerprint': '公钥指纹', 'ends_at': '结束时间',
    'expires_in': '令牌有效期',
    'key_count':'可用公钥数',
    'destination': '迁入用户组', 'moved_members': '已迁移成员', 'make_default': '设为默认组',
    'unlimited': '资源不限额', 'gpu': 'GPU', 'cpu': 'CPU 核', 'memory_gib': '内存 GiB', 'workspace_gib': '工作卷 GiB',
    'gpu_compute_percent':'总算力 %','gpu_memory_gib':'总显存 GiB',
    'gpu_compute_per_card_percent':'单卡算力上限 %','gpu_memory_per_card_gib':'单卡显存上限 GiB',
    'home_gib': '个人卷上限 GiB', 'image_gib': '镜像额度 GiB', 'concurrent_tasks': '并发任务数',
    'interactive_hours': '环境最长时段（小时）', 'snapshot_hours': '成功或取消后保留（小时）',
    'failed_snapshot_hours': '失败后保留（小时）', 'workspace_retention_days': '工作卷保留（天）', 'kind': '数据卷类型',
}


def detail_items(detail):
    items = []
    for key, value in detail.items():
        if key == 'expires_in' and type(value) is int:
            value = '永久' if value == 0 else f'{value // 86400} 天' if value % 86400 == 0 else f'{value} 秒'
        if key == 'status' and isinstance(value, str):
            value = dict(ResourceRequest.State.choices).get(value, value)
        if key == 'role' and isinstance(value, str):
            value = {'temporary': '临时用户', 'longterm': '长期用户'}.get(value, value)
        structured = isinstance(value, (dict, list))
        if structured:
            value = json.dumps(value, ensure_ascii=False, indent=2)
        elif isinstance(value, bool):
            value = '是' if value else '否'
        elif value is None:
            value = '—'
        items.append({'label': DETAIL_LABELS.get(key, key.replace('_', ' ')), 'value': value, 'structured': structured})
    return items


def audit_presentation(row):
    return {'label': ACTION_LABELS.get(row.action, '系统事件'),
            'target_label': '平台' if row.target == 'platform' else row.target,
            'details': detail_items(row.detail)}
