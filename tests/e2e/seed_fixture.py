"""Explicit fixture used by the administrator-controlled E2E harness."""
import json
import sys
from django.contrib.auth import get_user_model
from django.db import transaction
from portal.configuration import config
from portal.models import ImageArtifact

payload=json.load(sys.stdin)
with transaction.atomic():
    image=ImageArtifact.objects.get(pk=payload['image'],verified=True,shared=True)
    assert image.architecture==config()['platform']['architecture']
    users=[]
    for index,value in enumerate(payload['users']):
        assert value['username'].startswith('acceptance-')
        user,created=get_user_model().objects.get_or_create(username=value['username'])
        if created:
            user.set_password(value['password']);user.save(update_fields=['password'])
            user.profile.home_quota_gib=10 if index==0 else 1
            user.profile.save(update_fields=['home_quota_gib'])
        users.append({'username':user.username,'id':user.pk})
print(json.dumps({'image':str(image.pk),'users':users}))
