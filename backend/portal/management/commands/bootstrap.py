from django.core.management.base import BaseCommand
from portal.configuration import config
from portal.models import UserGroup, StoragePool
import os
from django.contrib.auth import get_user_model

class Command(BaseCommand):
    help = 'Initialize missing policies and storage pools; preserve administrator changes'
    def handle(self, *args, **options):
        cfg = config()
        if not UserGroup.objects.exists():
            for index, (role, policy) in enumerate(cfg['policies'].items()):
                UserGroup.objects.create(name={'temporary': '临时用户', 'longterm': '长期用户'}.get(role, role), is_default=index == 0, **policy)
        for pool in cfg['storage']['pools']:
            StoragePool.objects.get_or_create(name=pool['name'], defaults=pool)
        password = os.environ.get('PLATFORM_INITIAL_ADMIN_PASSWORD')
        if password:
            admin, created = get_user_model().objects.get_or_create(username=os.environ.get('PLATFORM_INITIAL_ADMIN', 'admin'),
                defaults={'is_staff': True, 'is_superuser': True, 'is_active': True})
            if created:
                admin.set_password(password)
                admin.save(update_fields=['password'])
                group, _ = UserGroup.objects.get_or_create(name='管理员', defaults={'unlimited': True})
                admin.profile.group = group
                admin.profile.save(update_fields=['group'])
        self.stdout.write('Bootstrap completed; existing policies preserved.')
