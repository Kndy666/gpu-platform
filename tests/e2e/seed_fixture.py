"""Explicit fixture used by the administrator-controlled E2E harness."""
import json
import sys
from django.contrib.auth import get_user_model
from django.db import transaction
from portal.configuration import config
from portal.models import ImageArtifact, UserGroup
from portal.policies import settings_from_group

payload=json.load(sys.stdin)
with transaction.atomic():
    image=ImageArtifact.objects.get(pk=payload['image'],verified=True,shared=True)
    assert image.architecture==config()['platform']['architecture']
    group, _ = UserGroup.objects.get_or_create(name=payload['users'][0]['username'], defaults={
        'cpu': 2, 'memory_gib': 4, 'workspace_gib': 4, 'home_gib': 1, 'image_gib': 10,
        'concurrent_tasks': 1, 'interactive_hours': 2, 'approval_mode': 'manual'})
    users=[]
    for value in payload['users']:
        assert value['username'].startswith('acceptance-')
        user,created=get_user_model().objects.get_or_create(username=value['username'])
        if created:
            user.set_password(value['password']);user.save(update_fields=['password'])
            profile=user.profile
            profile.group=group
            profile.home_quota_gib=1
            for name,value in settings_from_group(group).items():
                setattr(profile,name,value)
            profile.save()
        users.append({'username':user.username,'id':user.pk})
print(json.dumps({'image':str(image.pk),'users':users}))
