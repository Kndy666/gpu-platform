import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import pytest
from unittest import TestCase
from unittest.mock import patch


import gpu_platform_runtime_k3s

spec = importlib.util.spec_from_file_location(
    "resource_view_installer", Path(gpu_platform_runtime_k3s.__file__).parent / "host/resource-view.py"
)
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

            with (
                patch.object(installer, "RESOURCE_VIEW_ROOT", root),
                patch.object(installer.time, "monotonic", side_effect=[0, 0.1]),
                patch.object(installer.time, "sleep", side_effect=mount_views) as sleep,
            ):
                installer.wait_for_resource_views()
            sleep.assert_called_once()

    def test_partial_mount_times_out(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "proc").mkdir()
            (root / "proc/cpuinfo").touch()
            with (
                patch.object(installer, "RESOURCE_VIEW_ROOT", root),
                patch.object(installer.time, "monotonic", side_effect=[0, 31]),
            ):
                with self.assertRaises(RuntimeError):
                    installer.wait_for_resource_views()

    def exercise_package(self, valid):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "patches").mkdir(parents=True)
            (root / "packages").mkdir()
            package = root / "packages/fix.deb"
            package.write_bytes(b"test package")
            digest = hashlib.sha256(package.read_bytes()).hexdigest() if valid else "0" * 64
            (root / "patches/lxcfs-packages.json").write_text(
                json.dumps(
                    {
                        "version": "5.0.0-0ubuntu2+platform2",
                        "packages": {"arm64": {"file": "fix.deb", "sha256": digest}},
                    }
                )
            )

            def output(args, **kwargs):
                return {"dpkg-query": "5.0.4-1", "dpkg": "arm64", "k3s": '{"items":[]}'}.get(
                    args[0], ""
                )

            with (
                patch.object(installer, "ROOT", root),
                patch.object(installer, "run", side_effect=output) as run,
                patch.object(installer.shutil, "which", return_value="/usr/bin/lxcfs"),
            ):
                if valid:
                    installer.install_lxcfs({"platform": {"workload_namespace": "gpu-workloads"}})
                    self.assertIn(
                        ["apt-get", "install", "-y", "--allow-downgrades", "--no-install-recommends", package],
                        [call.args[0] for call in run.call_args_list],
                    )
                    self.assertIn(
                        ["systemctl", "stop", "lxcfs"], [call.args[0] for call in run.call_args_list]
                    )
                else:
                    with self.assertRaises(RuntimeError):
                        installer.install_lxcfs({"platform": {"workload_namespace": "gpu-workloads"}})
                    self.assertEqual(run.call_count, 2)

    def test_valid_package_installs_during_idle_maintenance(self):
        self.exercise_package(True)

    def test_corrupt_package_is_rejected_before_installation(self):
        self.exercise_package(False)

    def test_active_user_container_prevents_service_change(self):
        pod = {"spec": {"volumes": [{"hostPath": {"path": "/var/lib/lxcfs"}}]}}
        with patch.object(installer, "run", return_value=json.dumps({"items": [pod]})) as run:
            with self.assertRaises(RuntimeError):
                installer.require_idle_views({"platform": {"workload_namespace": "gpu-workloads"}})
        self.assertEqual(run.call_count, 1)


@pytest.mark.parametrize('path', [
    '/var/lib/lxcfs', '/var/lib/lxcfs/proc', '/var/lib/lxcfs/proc/meminfo', '/var/lib',
])
def test_running_host_path_resource_views_block_service_changes(path):
    pod = {'status': {'phase': 'Running'}, 'spec': {'volumes': [{'name': 'views', 'hostPath': {'path': path}}],
           'containers': [{'name': 'environment', 'volumeMounts': [{'name': 'views', 'mountPath': '/proc/meminfo', 'subPath': 'lxcfs/proc/meminfo'}]}]}}
    with patch.object(installer, 'run', return_value=json.dumps({'items': [pod]})) as run:
        with pytest.raises(RuntimeError):
            installer.require_idle_views({'platform': {'workload_namespace': 'workloads'}})
    assert run.call_count == 1


@pytest.mark.parametrize('phase,path', [
    ('Succeeded', '/var/lib/lxcfs'), ('Failed', '/var/lib/lxcfs/proc'),
    ('Running', '/var/lib/lxcfs-backup'), ('Running', '/data/workspace'),
])
def test_finished_or_unrelated_mounts_do_not_block_resource_view_maintenance(phase, path):
    pod = {'status': {'phase': phase}, 'spec': {'volumes': [{'hostPath': {'path': path}}]}}
    with patch.object(installer, 'run', return_value=json.dumps({'items': [pod]})):
        installer.require_idle_views({'platform': {'workload_namespace': 'workloads'}})


def test_source_built_approved_package_can_replace_newer_distribution_version(tmp_path):
    (tmp_path / 'patches').mkdir()
    (tmp_path / 'patches/lxcfs-packages.json').write_text(json.dumps({
        'version': '5.0.0-0ubuntu2+platform2', 'packages': {},
    }))
    (tmp_path / 'os-release').write_text('VERSION_ID="24.04"\n')

    def output(arguments, **kwargs):
        return {'dpkg-query': '5.0.4-1', 'dpkg': 'amd64', 'k3s': '{"items": []}'}.get(arguments[0], '')

    def path(value):
        return tmp_path / 'os-release' if str(value) == '/etc/os-release' else Path(value)

    with patch.object(installer, 'ROOT', tmp_path), patch.object(installer, 'Path', side_effect=path), \
            patch.object(installer, 'run', side_effect=output) as run:
        installer.install_lxcfs({'platform': {'workload_namespace': 'workloads'}})
    commands = [call.args[0] for call in run.call_args_list]
    inspection = next(index for index, command in enumerate(commands) if command[0] == 'k3s')
    build = next(index for index, command in enumerate(commands) if command[0] == tmp_path / 'build-resource-view.sh')
    install = next(index for index, command in enumerate(commands) if command[0] == 'apt-get')
    assert inspection < build < install
    assert '--allow-downgrades' in commands[install]
    assert commands[install][-1].name == 'lxcfs_5.0.0-0ubuntu2+platform2_amd64.deb'
