#!/usr/bin/env python3
import argparse
import json
from pathlib import Path
import time
import uuid
from portal_lifecycle import Acceptance, kube

from environment import require_features, namespace

def query(expression):
    code = 'import json; from portal.models import ImageArtifact,ImageBuild; print(json.dumps('+expression+',default=str))'
    raw = kube('-n', namespace(), 'exec', 'deploy/platform', '--', 'python', 'manage.py', 'shell', '-c', code)
    return json.loads(raw.strip().splitlines()[-1])


def main():
    require_features('cluster')
    p = argparse.ArgumentParser(); p.add_argument('--base', required=True); p.add_argument('--directory', type=Path, required=True)
    args = p.parse_args(); test = Acceptance(args.base, args.directory); test.prepare()
    base = query(f'list(ImageArtifact.objects.filter(pk="{test.image}").values("reference","digest"))')[0]
    dockerfile = f'''FROM {base['reference']}@{base['digest']}
USER root
RUN printf acceptance-image-marker > /opt/acceptance-image-marker
USER 1000:1000
'''
    name = 'acceptance-image-'+uuid.uuid4().hex[:6]
    response = test.users[0]['browser'].post('/images/', {'name': name, 'dockerfile': dockerfile, 'context_git': ''})
    response.raise_for_status()
    deadline = time.monotonic()+900
    while time.monotonic() < deadline:
        builds = query(f'list(ImageBuild.objects.filter(name="{name}").values("id","status","error","log","artifact_id"))')
        if not builds: raise RuntimeError('Build submission did not create a request')
        build = builds[0]
        args.directory.joinpath('image-build.json').write_text(json.dumps(build, indent=2))
        print(build['id'], build['status'], build['error'], flush=True)
        if build['status'] == 'failed': raise RuntimeError(build['error']+'\n'+build['log'][-3000:])
        if build['status'] == 'succeeded':
            args.directory.joinpath('personal-image-id').write_text(build['artifact_id'])
            test.passed('real_rootless_buildkit_private_image', artifact_id=build['artifact_id'])
            return
        time.sleep(10)
    raise TimeoutError('Image build')

if __name__ == '__main__': main()
