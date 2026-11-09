#!/usr/bin/env python3
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile

def run(argv, **kwargs): return subprocess.run([str(v) for v in argv], check=True, text=True, **kwargs)

def main():
    p = argparse.ArgumentParser(); p.add_argument('image'); p.add_argument('--metadata', type=Path)
    args = p.parse_args()
    if os.geteuid() != 0: raise SystemExit('Run with sudo')
    image = json.loads(run(['docker', 'image', 'inspect', args.image], stdout=subprocess.PIPE).stdout)[0]
    source = args.image if '/' in args.image else 'docker.io/library/'+args.image
    digest = image.get('Descriptor', {}).get('digest')
    if not digest: raise RuntimeError('Docker must provide the OCI manifest descriptor digest')
    with tempfile.TemporaryDirectory() as d:
        archive = Path(d)/'image.tar'
        run(['docker', 'save', '-o', archive, args.image])
        run(['k3s', 'ctr', 'images', 'import', archive])
    repository = source.rsplit(':', 1)[0] if ':' in source.rsplit('/', 1)[-1] else source
    for alias in [repository+'@'+digest, source+'@'+digest]:
        run(['k3s', 'ctr', 'images', 'tag', '--force', source, alias])
    if args.metadata:
        args.metadata.write_text(json.dumps({'image': source, 'digest': digest, 'architecture': image['Architecture']}, indent=2))

if __name__ == '__main__': main()
