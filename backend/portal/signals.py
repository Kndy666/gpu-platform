from django.contrib.auth import get_user_model
from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import UserProfile, UserGroup

@receiver(post_save, sender=get_user_model())
def create_profile(sender, instance, created, **kwargs):
    if created:
        group = UserGroup.objects.filter(is_default=True).first() or UserGroup.objects.order_by('pk').first()
        if group is None:
            group = UserGroup.objects.create(name='默认用户组', is_default=True)
        UserProfile.objects.create(user=instance, group=group, home_quota_gib=50 if group.unlimited else min(50, group.home_gib))
