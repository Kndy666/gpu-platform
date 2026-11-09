#!/usr/bin/env python3
import argparse
import json
from pathlib import Path
import time
import uuid
from portal_lifecycle import Acceptance, kube

def query(expression):
    code = 'import json; from portal.models import ImageArtifact,ImageBuild; print(json.dumps('+expression+',default=str))'
    raw = kube('-n', 'gpu-platform', 'exec', 'deploy/platform', '--', 'python', 'manage.py', 'shell', '-c', code)
    return json.loads(raw.strip().splitlines()[-1])

def main():
    p = argparse.ArgumentParser(); p.add_argument('--base', required=True); p.add_argument('--directory', type=Path, required=True)
    args = p.parse_args(); test = Acceptance(args.base, args.directory); test.prepare()
    base = query('list(ImageArtifact.objects.filter(shared=True,verified=True).values("reference","digest"))')[0]
    dockerfile = f'''FROM {base['reference']}@{base['digest']}
USER root
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-pip && rm -rf /var/lib/apt/lists/*
RUN python3 -m pip install --no-cache-dir -i https://mirrors.aliyun.com/pypi/simple --target=/opt/experiment rich==13.9.4
ENV PYTHONPATH=/opt/experiment
USER 1000:1000
'''
    name = 'acceptance-rich-'+uuid.uuid4().hex[:6]
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
