#!/usr/bin/env python3
"""Exercise resource views and tools in a disposable CPU workspace."""
import argparse
import json
from pathlib import Path
import re
import tempfile

from portal_lifecycle import Acceptance
from resource_view import inspect_resource_view
from workspace_cleanup import cleanup_account


from environment import deployment_config, enabled, kube, namespace, portal_url, require_features

def app(code, payload=None):
    output = kube('-n', namespace(), 'exec', '-i', 'deployment/platform', '--',
                  'python', 'manage.py', 'shell', '-c', code, data=json.dumps(payload or {}))
    return json.loads(output.strip().splitlines()[-1])


def main():
    require_features('cluster')
    parser = argparse.ArgumentParser()
    parser.add_argument('--resource-view-only', action='store_true')
    args = parser.parse_args()
    cfg = deployment_config()
    with tempfile.TemporaryDirectory(prefix='gpu-platform-tools-check-') as directory:
        test = Acceptance(portal_url(), Path(directory))
        try:
            test.prepare()
            task = test.create(test.users[0], 'Workspace tools check', cpu=2, memory_gib=2)
            test.wait(task, {'running'})
            pod = app('''import json,sys
from portal.models import Workspace
print(json.dumps(Workspace.objects.get(request_id=json.load(sys.stdin)['id']).object_name))''',
                      {'id': task['id']})
            result = inspect_resource_view(namespace('workload_namespace'), pod, 2, 2)
            if args.resource_view_only:
                print(json.dumps(result))
                return
            commands = '''set -e
test "$(id -u)" = 1000
test "$(sudo -n id -u)" = 0
test "$(nproc)" = 2
ping -c 1 127.0.0.1 >/dev/null
printf '#include <stdio.h>\nint main(){puts("compile-ok");}\n' > /workspace/check.c
gcc /workspace/check.c -o /workspace/check
test "$(/workspace/check)" = compile-ok
cmake --version >/dev/null
ninja --version >/dev/null
htop --version >/dev/null
bwrap --unshare-all --ro-bind / / true
'''
            test.ssh(task, commands)
            if enabled('network'):
                test.ssh(task, 'sudo -n apt-get update && sudo -n apt-get install -y --no-install-recommends tree && tree --version', timeout=180)
                result['apt_install'] = 'pass'
            else:
                result['apt_install'] = 'skipped (enable network)'
            shell = test.ssh(task, "TERM=xterm-256color bash -ic 'printf \"__BLE_VERSION__%s__END__\\n\" \"$BLE_VERSION\"'", tty=True)
            assert re.search(r'__BLE_VERSION__[0-9][^\r\n]*__END__', shell.stdout)
            result.update(ssh=True, c_compile=True, bubblewrap=True, bash_highlighting=True)
            print(json.dumps(result))
        finally:
            for user in test.users:
                cleanup_account(app, cfg, user['username'], {'user_id': user['id']}, 'acceptance-')
            if test.users:
                app('''import json,sys
from portal.models import UserGroup
UserGroup.objects.filter(name=json.load(sys.stdin)['name']).delete()
print(json.dumps(True))''', {'name': test.users[0]['username']})


if __name__ == '__main__':
    main()
