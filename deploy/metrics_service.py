"""Install performance sampling independently of workspace and GPU services."""
from pathlib import Path
import shutil
import subprocess


def install_metrics(root, config):
    library = Path('/usr/local/lib/gpu-platform')
    library.mkdir(mode=0o755, parents=True, exist_ok=True)
    target = library / 'host-metrics.py'
    shutil.copyfile(Path(root) / 'deploy/host-metrics.py', target)
    target.chmod(0o644)
    unit = Path('/etc/systemd/system/gpu-platform-metrics.service')
    unit.write_text('[Unit]\nDescription=GPU platform host performance sampling\nAfter=k3s.service\n'
        'Wants=k3s.service\n[Service]\nType=simple\nEnvironment=KUBECONFIG=/etc/rancher/k3s/k3s.yaml\n'
        'ExecStart=/usr/bin/python3 ' + str(target) + ' --config ' + str(config) + '\n'
        'Restart=on-failure\nRestartSec=5\nNoNewPrivileges=true\nProtectSystem=strict\nProtectHome=read-only\n'
        'PrivateTmp=true\nNice=10\nCPUQuota=10%\nMemoryMax=256M\n'
        '[Install]\nWantedBy=multi-user.target\n')
    subprocess.run(['systemctl', 'daemon-reload'], check=True)
    subprocess.run(['systemctl', 'enable', 'gpu-platform-metrics.service'], check=True)
    subprocess.run(['systemctl', 'restart', 'gpu-platform-metrics.service'], check=True)
