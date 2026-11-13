#!/usr/bin/env python3
import argparse
import json
from pathlib import Path
from portal_lifecycle import Acceptance, kube


from environment import require_features, namespace

def main():
    require_features('cluster')
    p = argparse.ArgumentParser(); p.add_argument('--base', required=True); p.add_argument('--directory', type=Path, required=True)
    args = p.parse_args(); test = Acceptance(args.base, args.directory); test.prepare()
    artifact = (args.directory/'personal-image-id').read_text().strip()
    test.image = artifact
    task = test.create(test.users[0], '验收 · 个人镜像环境复用与 Remote-SSH', gpu=0)
    test.wait(task, {'running'}, timeout=300)
    result = test.ssh(task, 'cat /opt/acceptance-image-marker')
    assert result.stdout == 'acceptance-image-marker'
    code = '''import json
from portal.models import ImageArtifact
from portal.registry import RegistryClient,decode
from portal.kube import KubeClient
from portal.configuration import config
image=ImageArtifact.objects.get(pk=ARTIFACT)
users=json.loads(decode(KubeClient().get('secrets',config()['platform']['namespace'],'registry-state')['data']['users.json']))
owner=RegistryClient(users[str(image.owner_id)]); other=RegistryClient(users[OTHER_ID])
host,repo=image.reference.split('/',1); repo=repo.rsplit(':',1)[0]
url='https://'+host+'/v2/'+repo+'/manifests/'+image.digest
accepted=owner.session.get(url,timeout=10);accepted.raise_for_status()
read=other.session.get(url,timeout=10)
write=other.session.put(url,data=accepted.content,headers={'Content-Type':accepted.headers['Content-Type']},timeout=10)
assert read.status_code in (401,403,404),read.status_code
assert write.status_code in (401,403,404),write.status_code
print(json.dumps({'owner_read':accepted.status_code,'other_read':read.status_code,'other_write':write.status_code}))
'''.replace('ARTIFACT', repr(artifact)).replace('OTHER_ID', repr(str(test.users[1]['id'])))
    raw = kube('-n',namespace(),'exec','deploy/platform','--','python','manage.py','shell','-c',code)
    acl = json.loads(raw.strip().splitlines()[-1])
    assert artifact not in test.users[1]['browser'].get('/images/').text
    test.passed('private_image_reuse_and_cross_user_acl', persisted_file=True, **acl)
    (args.directory/'remote-ssh-task.json').write_text(json.dumps({'id':task['id'],'path':task['path']}))

if __name__ == '__main__': main()
