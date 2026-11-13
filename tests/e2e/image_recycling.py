#!/usr/bin/env python3
"""Real Zot smoke check; run only in an isolated Docker network with /check fixtures.

Use the business image with source mounted at /src, /check/data as PLATFORM_DATA,
/check/config.yaml as PLATFORM_CONFIG, and the fixture TLS CA at
/etc/platform-registry-ca/tls.crt. Run setup, start Zot from /check/zot.json,
then run check. Set PLATFORM_TEST_REGISTRY_DIR to the fixture directory and use its data subdirectory.
"""
import base64
from datetime import timedelta
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from unittest.mock import Mock
from urllib.parse import urljoin
import uuid

import bcrypt
import django

from environment import require_features

require_features('registry')
fixture = Path(os.environ['PLATFORM_TEST_REGISTRY_DIR']).resolve()
if Path(os.environ['PLATFORM_DATA']).resolve() != fixture / 'data':
    raise RuntimeError('PLATFORM_DATA must point to the registry fixture data directory')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()
from django.core.management import call_command
from django.contrib.auth import get_user_model
from django.utils import timezone
from portal.configuration import config
from portal.image_lifecycle import collect_images
from portal.models import ImageArtifact, ResourceRequest
from portal.registry import RegistryClient, zot_config

directory = fixture
owner_identity = {'login': 'test-owner', 'password': 'isolated-owner-test', 'reader': {'login': 'test-owner-reader'}}
collector = {'login': 'test-collector', 'password': 'isolated-collector-test'}
if sys.argv[1] == 'setup':
    call_command('initdb', verbosity=0)
    user = get_user_model().objects.create_user('registry-validation')
    cfg = zot_config({str(user.pk): owner_identity}, collector)
    cfg['log']['level'] = 'error'
    (directory/'zot.json').write_text(json.dumps(cfg))
    (directory/'auth/htpasswd').write_text('\n'.join(identity['login']+':'+bcrypt.hashpw(identity['password'].encode(), bcrypt.gensalt()).decode() for identity in (owner_identity, collector))+'\n')
    print('Isolated registry configuration ready')
    raise SystemExit()

user = get_user_model().objects.get(username='registry-validation')
owner = RegistryClient(owner_identity)
gc = RegistryClient(collector)
repo = f'user-{user.pk}/environment'
base = f'https://{owner.host}/v2/{repo}'
for attempt in range(30):
    try:
        if owner.session.get(f'https://{owner.host}/v2/', timeout=1).status_code == 200:
            break
    except Exception:
        time.sleep(.2)
else:
    raise RuntimeError('Isolated registry did not start')
token = uuid.uuid4().hex
content = json.dumps({'architecture': config()['platform']['architecture'], 'os': 'linux', 'rootfs': {'type': 'layers', 'diff_ids': []}, 'config': {'Labels': {'test': token}}}).encode()
digest = 'sha256:'+hashlib.sha256(content).hexdigest()
upload = owner.session.post(base+'/blobs/uploads/', timeout=10)
upload.raise_for_status()
location = urljoin(base+'/', upload.headers['Location'])
response = owner.session.put(location+('&' if '?' in location else '?')+'digest='+digest,
    data=content, headers={'Content-Type': 'application/octet-stream'}, timeout=10)
response.raise_for_status()
manifest = json.dumps({'schemaVersion': 2, 'mediaType': 'application/vnd.oci.image.manifest.v1+json',
    'config': {'mediaType': 'application/vnd.oci.image.config.v1+json', 'digest': digest, 'size': len(content)}, 'layers': []}).encode()
for tag in (token, token+'-keep'):
    response = owner.session.put(base+'/manifests/'+tag, data=manifest,
        headers={'Content-Type': 'application/vnd.oci.image.manifest.v1+json'}, timeout=10)
    response.raise_for_status()
image_digest, size = owner.manifest(repo, token)
image = ImageArtifact.objects.create(owner=user, name='isolated snapshot', reference=owner.host+'/'+repo+':'+token,
    digest=image_digest, size_bytes=size, temporary=True, verified=True, recycle_after=timezone.now()-timedelta(hours=1))
kube = Mock()
kube.get.return_value = {'data': {'collector.json': base64.b64encode(json.dumps(collector).encode()).decode()}}
req = ResourceRequest.objects.create(user=user, image=image, mode='batch', purpose='hold', status='submitted')
collect_images(kube)
image.refresh_from_db()
assert image.state == 'ready' and image.recycle_after is None
assert owner.manifest(repo, token)[0] == image_digest
req.status = 'succeeded'
req.save()
image.recycle_after = timezone.now()-timedelta(hours=1)
image.save()
assert gc.session.get(base+'/manifests/'+token, timeout=10).status_code in (401, 403)
collect_images(kube)
image.refresh_from_db()
assert image.state == 'deleted', image.cleanup_error
assert owner.session.get(base+'/manifests/'+token, timeout=10).status_code == 404
assert owner.manifest(repo, token+'-keep')[0] == image_digest
req.refresh_from_db()
assert req.image_id == image.pk
gc.delete_tag(repo, token)
gc.delete_tag(repo, token+'-keep')
print('Isolated Zot verification: hold during review, tag deletion, shared-digest protection, delete-only collector, retry idempotency and history retention: OK')
