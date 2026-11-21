import os
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from gpu_platform_control.core.accounts.models import UserGroup
from gpu_platform_control.core.nodes.models import ComputeNode
from gpu_platform_control.slots.configuration import cluster_config


class Command(BaseCommand):
    help = "Initialize configured nodes, groups, and the initial administrator"

    def handle(self, *args, **options):
        configuration = cluster_config()
        for name, host in configuration.hosts.items():
            if "node" not in host.roles:
                continue
            ComputeNode.objects.get_or_create(
                name=name,
                defaults={
                    "architecture": host.architecture,
                },
            )
        fields = {field.name for field in UserGroup._meta.fields} - {"id", "name", "is_default"}
        for index, (name, values) in enumerate(configuration.control.groups.items()):
            UserGroup.objects.get_or_create(
                name=name,
                defaults={
                    "is_default": index == 0 and not UserGroup.objects.filter(is_default=True).exists(),
                    **{key: value for key, value in values.items() if key in fields},
                },
            )
        password = os.environ.get("PLATFORM_INITIAL_ADMIN_PASSWORD")
        if password:
            admin, created = get_user_model().objects.get_or_create(
                username=os.environ.get("PLATFORM_INITIAL_ADMIN", "admin"),
                defaults={"is_staff": True, "is_superuser": True, "is_active": True},
            )
            if created:
                admin.set_password(password)
                admin.save(update_fields=["password"])
                group, _ = UserGroup.objects.get_or_create(
                    name="Administrators", defaults={"unlimited": True}
                )
                admin.profile.group = group
                admin.profile.home_size_gib = group.home_size_gib
                from gpu_platform_control.core.policies import settings_from_group

                values = settings_from_group(group)
                for name, value in values.items():
                    setattr(admin.profile, name, value)
                admin.profile.save(update_fields=["group", "home_size_gib", *values])
        self.stdout.write("Bootstrap completed.")
