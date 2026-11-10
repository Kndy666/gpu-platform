import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
from unittest import TestCase
from unittest.mock import patch


spec = importlib.util.spec_from_file_location('resource_view_installer', Path(__file__).resolve().parents[2] / 'deploy/resource-view.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class ResourceViewInstallerTests(TestCase):
    def test_waits_for_delayed_fuse_mount(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def mount_views(_):
                for path in installer.RESOURCE_VIEW_PATHS:
                    view = root / path
                    view.parent.mkdir(parents=True, exist_ok=True)
                    view.touch()
            with patch.object(installer, 'RESOURCE_VIEW_ROOT', root), \
                    patch.object(installer.time, 'monotonic', side_effect=[0, 0.1]), \
                    patch.object(installer.time, 'sleep', side_effect=mount_views) as sleep:
                installer.wait_for_resource_views()
            sleep.assert_called_once()

    def test_partial_mount_times_out_with_missing_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'proc').mkdir()
            (root / 'proc/cpuinfo').touch()
            with patch.object(installer, 'RESOURCE_VIEW_ROOT', root), \
                    patch.object(installer.time, 'monotonic', side_effect=[0, 31]):
                with self.assertRaises(RuntimeError) as error:
                    installer.wait_for_resource_views()
            self.assertIn('proc/meminfo', str(error.exception))
            self.assertIn('proc/stat', str(error.exception))
            self.assertIn('sys/devices/system/cpu/online', str(error.exception))
            self.assertNotIn('proc/cpuinfo', str(error.exception))

    def test_installed_fix_is_left_running(self):
        with patch.object(installer, 'run', return_value='5.0.0-0ubuntu2+platform2') as run:
            installer.install_lxcfs({'platform': {'workload_namespace': 'gpu-workloads'}})
        self.assertEqual(run.call_count, 1)

    def exercise_package(self, valid):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'deploy/patches').mkdir(parents=True)
            (root / 'deploy/packages').mkdir()
            package = root / 'deploy/packages/fix.deb'
            package.write_bytes(b'test package')
            digest = hashlib.sha256(package.read_bytes()).hexdigest() if valid else '0' * 64
            (root / 'deploy/patches/lxcfs-packages.json').write_text(json.dumps({'version': '5.0.0-0ubuntu2+platform2', 'packages': {'arm64': {'file': 'fix.deb', 'sha256': digest}}}))
            def output(args, **kwargs):
                return {'dpkg-query': '5.0.0-0ubuntu2', 'dpkg': 'arm64', 'k3s': '{"items":[]}'}.get(args[0], '')
            with patch.object(installer, 'ROOT', root), patch.object(installer, 'run', side_effect=output) as run, \
                    patch.object(installer.shutil, 'which', return_value='/usr/bin/lxcfs'):
                if valid:
                    installer.install_lxcfs({'platform': {'workload_namespace': 'gpu-workloads'}})
                    self.assertIn(['apt-get', 'install', '-y', '--no-install-recommends', package], [call.args[0] for call in run.call_args_list])
                    self.assertIn(['systemctl', 'stop', 'lxcfs'], [call.args[0] for call in run.call_args_list])
                    self.assertFalse(any('USR1' in str(call.args[0]) for call in run.call_args_list))
                else:
                    with self.assertRaisesRegex(RuntimeError, 'invalid'):
                        installer.install_lxcfs({'platform': {'workload_namespace': 'gpu-workloads'}})
                    self.assertEqual(run.call_count, 2)

    def test_valid_package_installs_during_idle_maintenance(self):
        self.exercise_package(True)

    def test_corrupt_package_is_rejected_before_installation(self):
        self.exercise_package(False)

    def test_active_user_container_prevents_service_change(self):
        pod = {'spec': {'volumes': [{'persistentVolumeClaim': {'claimName': 'resource-view'}}]}}
        with patch.object(installer, 'run', return_value=json.dumps({'items': [pod]})) as run:
            with self.assertRaisesRegex(RuntimeError, 'Stop user containers'):
                installer.require_idle_views({'platform': {'workload_namespace': 'gpu-workloads'}})
        self.assertEqual(run.call_count, 1)
