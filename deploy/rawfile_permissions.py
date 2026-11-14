#!/usr/bin/env python3
"""Helm post-renderer and ownership extension for the pinned Rawfile CSI driver."""
import ast
import hashlib
import inspect
import os
from pathlib import Path
import sys


def update_permissions(volume_id: str, storage_pool: str) -> None:
    import os
    import stat
    from utils.volume_manager import manager

    volume = manager.get_volume_path(storage_pool, volume_id)
    pool_fd = os.open(volume.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        owner = os.fstat(pool_fd)
        try:
            volume_fd = os.open(volume.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=pool_fd)
        except FileNotFoundError:
            return
        try:
            # Descriptor-relative traversal also avoids following user-created symlinks.
            for _, _, files, directory_fd in os.fwalk('.', dir_fd=volume_fd, follow_symlinks=False):
                os.fchown(directory_fd, owner.st_uid, owner.st_gid)
                os.fchmod(directory_fd, 0o700)
                for name in files:
                    if not stat.S_ISREG(os.stat(name, dir_fd=directory_fd, follow_symlinks=False).st_mode):
                        continue
                    descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
                    try:
                        if stat.S_ISREG(os.fstat(descriptor).st_mode):
                            os.fchown(descriptor, owner.st_uid, owner.st_gid)
                            os.fchmod(descriptor, 0o600)
                    finally:
                        os.close(descriptor)
        finally:
            os.close(volume_fd)
    finally:
        os.close(pool_fd)


def patch_driver(root):
    path = root / 'utils/rawfile.py'
    source = path.read_text()
    expected = 'ec233fd048d0ffe76463c4ebb189d446b1749b36815ac1dc3541c68a5ff0b234'
    if hashlib.sha256(source.encode()).hexdigest() != expected:
        raise RuntimeError('Review the storage ownership extension for this Rawfile driver version')
    function = next(node for node in ast.parse(source).body
                    if isinstance(node, ast.FunctionDef) and node.name == 'update_permissions')
    lines = source.splitlines(keepends=True)
    lines[function.lineno - 1:function.end_lineno] = [inspect.getsource(update_permissions)]
    path.write_text(''.join(lines))

    path = root / 'utils/snapshot_manager.py'
    source = path.read_text()
    marker = '                creation_time = time.time()'
    if source.count(marker) != 1:
        raise RuntimeError('Review Rawfile snapshot creation before updating the driver')
    source = source.replace(marker, '                from utils.rawfile import update_permissions\n'
                            '                update_permissions(volume_id, metadata(volume_id)["storage_pool"])\n' + marker)
    path.write_text(source)


def render_manifests(documents, source):
    for document in documents:
        if document.get('kind') != 'DaemonSet':
            continue
        pod = document['spec']['template']
        driver = next(container for container in pod['spec']['containers'] if container['name'] == 'csi-driver')
        driver['command'] = ['python', '/etc/platform-storage/rawfile_permissions.py', '--driver']
        driver['volumeMounts'].append({'name': 'platform-storage-owner', 'mountPath': '/etc/platform-storage', 'readOnly': True})
        name = document['metadata']['name'] + '-ownership'
        pod['spec']['volumes'].append({'name': 'platform-storage-owner', 'configMap': {'name': name}})
        pod['metadata'].setdefault('annotations', {})['platform.gpu/storage-owner-script'] = hashlib.sha256(source.encode()).hexdigest()
        documents.append({'apiVersion': 'v1', 'kind': 'ConfigMap', 'metadata': {'name': name},
                          'data': {'rawfile_permissions.py': source}})
        return documents
    raise RuntimeError('Rawfile node DaemonSet is missing')


if __name__ == '__main__':
    if sys.argv[1:2] == ['--driver']:
        patch_driver(Path('/rawfile'))
        os.execv('/docker-entrypoint.sh', ['/docker-entrypoint.sh', *(sys.argv[2:] or ['csi-driver'])])
    else:
        import yaml
        documents = [document for document in yaml.safe_load_all(sys.stdin) if document]
        yaml.safe_dump_all(render_manifests(documents, Path(__file__).read_text()), sys.stdout, sort_keys=False)
