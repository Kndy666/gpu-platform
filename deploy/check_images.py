#!/usr/bin/env python3
"""Check versioned vendor image manifests for a target architecture without pulling layers."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import datetime
import json
from pathlib import Path
import subprocess
import yaml

def images_in(value):
    if isinstance(value, dict):
        if isinstance(value.get('image'), str): yield value['image']
        for child in value.values(): yield from images_in(child)
    elif isinstance(value, list):
        for child in value: yield from images_in(child)

def platforms_in(value):
    if isinstance(value, dict):
        if 'platform' in value and isinstance(value['platform'], dict): yield value['platform']
        for child in value.values(): yield from platforms_in(child)
    elif isinstance(value, list):
        for child in value: yield from platforms_in(child)

def check(item):
    image, arch = item
    pull = image
    if image.startswith('docker.io/'): pull = 'docker.m.daocloud.io/'+image[len('docker.io/'):]
    elif image.startswith('registry.k8s.io/'): pull = 'k8s.m.daocloud.io/'+image[len('registry.k8s.io/'):]
    elif '/' not in image.split(':')[0] or image.startswith('moby/') or image.startswith('alpine/'):
        pull = 'docker.m.daocloud.io/'+('library/' if '/' not in image else '')+image
    elif image.startswith('nvcr.io/'): pull = 'nvcr.m.daocloud.io/'+image[len('nvcr.io/'):]
    result = {'image': image, 'source': pull, 'architecture': arch}
    try:
        attempt = subprocess.run(['docker', 'manifest', 'inspect', '--verbose', pull], text=True, capture_output=True, timeout=50)
        if attempt.returncode:
            if pull != image:
                attempt = subprocess.run(['docker', 'manifest', 'inspect', '--verbose', image], text=True, capture_output=True, timeout=50)
                result['source'] = image
            if attempt.returncode: raise RuntimeError(attempt.stderr[-500:])
        data = json.loads(attempt.stdout)
        platforms = list(platforms_in(data))
        result['platforms'] = platforms
        result['verified'] = any(p.get('architecture') == arch and p.get('os') == 'linux' for p in platforms)
        def digests(value):
            if isinstance(value, dict):
                if 'digest' in value: yield value['digest']
                for child in value.values(): yield from digests(child)
            elif isinstance(value, list):
                for child in value: yield from digests(child)
        result['digests'] = sorted(set(digests(data)))
    except Exception as e:
        result.update(verified=False, error=str(e))
    print(image, 'PASS' if result.get('verified') else 'UNVERIFIED', flush=True)
    return result

def main():
    parser = argparse.ArgumentParser(); parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, action='append', default=[])
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(); cfg = yaml.safe_load(args.config.read_text())
    images = {cfg['registry'][k] for k in ['zot_image', 'buildkit_image', 'git_image']}
    for path in args.manifest:
        for obj in yaml.safe_load_all(path.read_text()): images.update(images_in(obj))
    if any(image.endswith(':latest') or ':' not in image.split('/')[-1] for image in images): raise RuntimeError('Every image needs a version or digest')
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(check, [(image, cfg['platform']['architecture']) for image in sorted(images)]))
    args.output.write_text(json.dumps({'observed_at': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'images': results}, indent=2))
    if not all(r['verified'] for r in results): raise SystemExit(1)

if __name__ == '__main__': main()
