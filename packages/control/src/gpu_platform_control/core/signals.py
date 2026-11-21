from django.contrib.auth import get_user_model
from django.db.models.signals import post_save
from django.dispatch import receiver
from gpu_platform_control.core.accounts.models import UserProfile, UserGroup


@receiver(post_save, sender=get_user_model())
def create_profile(sender, instance, created, **kwargs):
    if created:
        group = UserGroup.objects.filter(is_default=True).first() or UserGroup.objects.order_by("pk").first()
        if group is None:
            group = UserGroup.objects.create(name="Default", is_default=True)
        from .policies import settings_from_group

        UserProfile.objects.create(
            user=instance,
            group=group,
            home_size_gib=group.home_size_gib,
            **settings_from_group(group),
        )
