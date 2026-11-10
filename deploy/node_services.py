"""Install the role-specific host service; both roles use the same project."""
from pathlib import Path
import pwd
import shlex
import shutil
import subprocess
import yaml


def install_node_services(cfg, root):
    worker = cfg['platform'].get('role', 'controller') == 'worker'
    if worker:
        try:
            pwd.getpwnam('gpu-platform')
        except KeyError:
            subprocess.run(['useradd', '--system', '--user-group', '--create-home', '--home-dir',
                            '/var/lib/gpu-platform/ssh-user', '--shell', '/bin/sh', 'gpu-platform'], check=True)
        directory = Path('/etc/gpu-platform')
        directory.mkdir(mode=0o750, parents=True, exist_ok=True)
        config_path = directory / 'config.yaml'
        config_path.write_text(yaml.safe_dump(cfg))
        config_path.chmod(0o600)
        library = Path('/usr/local/lib/gpu-platform')
        (library / 'portal').mkdir(mode=0o755, parents=True, exist_ok=True)
        library.chmod(0o755)
        (library / 'portal').chmod(0o755)
        endpoint = library / 'worker-rpc.py'
        for source, target in ((root / 'deploy/worker-rpc.py', endpoint),
                               (root / 'backend/portal/node_transport.py', library / 'portal/node_transport.py')):
            shutil.copyfile(source, target)
            target.chmod(0o644)
        wrapper = Path('/usr/local/bin/gpu-platform-worker')
        wrapper.write_text('#!/bin/sh\nexec /usr/bin/python3 ' + shlex.quote(str(endpoint)) + ' "$@"\n')
        wrapper.chmod(0o755)
        name = 'gpu-platform-worker'
        command = f'/usr/bin/python3 "{endpoint}" serve --config /etc/gpu-platform/config.yaml'
    else:
        directory = Path(cfg.get('controller', {}).get('key_directory', '/var/lib/gpu-platform/ssh-keys'))
        if not directory.is_absolute() or '\n' in str(directory) or '\r' in str(directory):
            raise RuntimeError('Controller key directory must be an absolute path')
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        start, end = cfg['platform']['ssh_ports']
        name = 'gpu-platform-links'
        command = f'/usr/bin/python3 "{root}/deploy/node-links.py" --key-dir "{directory}" --ports {start}:{end}'
    unit = Path('/etc/systemd/system') / (name + '.service')
    unit.write_text('[Unit]\nDescription=GPU platform node connection service\nAfter=network-online.target k3s.service\n'
                    'Wants=network-online.target\n[Service]\nType=simple\nExecStart=' + command + '\n'
                    'Restart=on-failure\nRestartSec=3\nKillMode=control-group\nUMask=0077\n'
                    'RuntimeDirectory=gpu-platform\nRuntimeDirectoryMode=0755\n'
                    '[Install]\nWantedBy=multi-user.target\n')
    subprocess.run(['systemctl', 'daemon-reload'], check=True)
    subprocess.run(['systemctl', 'enable', '--now', name], check=True)
    subprocess.run(['systemctl', 'restart', name], check=True)
