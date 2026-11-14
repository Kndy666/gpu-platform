from copy import deepcopy
import hashlib
import importlib.util
import io
import os
from pathlib import Path
import sys
import subprocess
import tempfile
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch
import yaml

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('platform_installer', ROOT/'deploy/install.py')
installer = importlib.util.module_from_spec(spec); spec.loader.exec_module(installer)

class InstallerSimulationTests(TestCase):
    def test_dataset_directory_uses_installing_or_configured_user(self):
        for configured, sudo_user, expected_user in [('', 'operator', 'operator'), ('data-owner', 'operator', 'data-owner')]:
            with self.subTest(configured=configured), tempfile.TemporaryDirectory() as directory:
                path = Path(directory)/'datasets'
                with patch.dict(os.environ, {'SUDO_USER': sudo_user}), \
                        patch.object(installer.pwd, 'getpwnam', return_value=SimpleNamespace(pw_uid=2100, pw_gid=2200)) as account, \
                        patch.object(installer, 'run') as run:
                    installer.prepare_datasets({'datasets': str(path), 'datasets_owner': configured})
                account.assert_called_once_with(expected_user)
                run.assert_called_once_with(['install', '-d', '-m', '0755', '-o', '2100', '-g', '2200', str(path)])

    def test_dataset_directory_retains_existing_non_root_ownership(self):
        with patch.object(installer, 'Path') as path, patch.object(installer, 'run') as run:
            path.return_value.exists.return_value = True
            path.return_value.stat.return_value.st_uid = 2100
            installer.prepare_datasets({'datasets': '/data/datasets'})
            run.assert_not_called()

    def test_root_owned_dataset_directory_gets_workspace_identity_without_sudo_user(self):
        for environment in ({}, {'SUDO_USER': 'root'}):
            with self.subTest(environment=environment), patch.dict(os.environ, environment, clear=True), \
                    patch.object(installer, 'Path') as path, patch.object(installer, 'run') as run:
                path.return_value.exists.return_value = True
                path.return_value.stat.return_value.st_uid = 0
                path.return_value.__str__.return_value = '/data/datasets'
                installer.prepare_datasets({'datasets': '/data/datasets'})
                run.assert_called_once_with(['install', '-d', '-m', '0755', '-o', '1000', '-g', '1000', '/data/datasets'])

    def test_worker_client_is_installed_outside_the_private_checkout(self):
        spec = importlib.util.spec_from_file_location('node_services_test', ROOT/'deploy/node_services.py')
        services = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(services)
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            source = temporary/'private-checkout'
            (source/'deploy').mkdir(parents=True)
            (source/'backend/portal').mkdir(parents=True)
            for name in ('deploy/worker-rpc.py', 'backend/portal/node_transport.py'):
                (source/name).write_text((ROOT/name).read_text())
            installed = temporary/'system'
            (installed/'usr/local/bin').mkdir(parents=True)
            (installed/'etc/systemd/system').mkdir(parents=True)
            def target(path):
                return installed/str(path).lstrip('/')
            previous = os.umask(0o077)
            try:
                with patch.object(services, 'Path', side_effect=target), \
                        patch.object(services.pwd, 'getpwnam'), patch.object(services.subprocess, 'run'):
                    services.install_node_services({'platform': {'role': 'worker'}}, source)
            finally:
                os.umask(previous)
            library = installed/'usr/local/lib/gpu-platform'
            self.assertEqual(library.stat().st_mode & 0o777, 0o755)
            self.assertEqual((library/'portal/node_transport.py').stat().st_mode & 0o777, 0o644)
            unit = (installed/'etc/systemd/system/gpu-platform-worker.service').read_text()
            self.assertIn(str(library/'worker-rpc.py'), unit)
            self.assertNotIn(str(source), unit)
            self.assertIn('RuntimeDirectoryPreserve=yes', unit)
            result = subprocess.run([sys.executable, str(library/'worker-rpc.py'), '--help'],
                                    capture_output=True, text=True, timeout=10, env={**os.environ, 'PYTHONPATH': ''})
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_worker_render_disables_portal_and_uses_worker_architecture(self):
        with tempfile.TemporaryDirectory() as directory:
            cfg = yaml.safe_load((ROOT/'config.worker.example.yaml').read_text())
            facts = {'architecture': 'amd64', 'node_ip': '192.0.2.25', 'interface': 'ens3'}
            installer.render(cfg, facts, Path(directory))
            values = yaml.safe_load((Path(directory)/'app-values.yaml').read_text())
            self.assertTrue(values['worker'])
            self.assertEqual(values['config']['platform']['data_size_gib'], 0)
            self.assertEqual(values['config']['platform']['architecture'], 'amd64')

    def test_download_falls_back_after_timeout_and_checks_integrity(self):
        payload = b'verified chart archive'
        digest = hashlib.sha256(payload).hexdigest()
        urls = ['https://mirror.example.com/chart.tgz', 'https://example.com/chart.tgz']
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(installer.urllib.request, 'urlopen', side_effect=[TimeoutError(), io.BytesIO(payload)]) as open_url:
            destination = Path(directory) / 'chart.tgz'
            installer.download(urls, destination, digest)
            self.assertEqual(destination.read_bytes(), payload)
            self.assertEqual([call.args[0] for call in open_url.call_args_list], urls)

    def test_download_rejects_corrupt_archive(self):
        digest = hashlib.sha256(b'verified chart archive').hexdigest()
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(installer.urllib.request, 'urlopen', return_value=io.BytesIO(b'corrupt archive')):
            destination = Path(directory) / 'chart.tgz'
            with self.assertRaises(RuntimeError):
                installer.download(['https://example.com/chart.tgz'], destination, digest)
            self.assertFalse(destination.exists())
            self.assertFalse(destination.with_suffix('.part').exists())

    def test_clean_machine_entrypoint_renders_arm64_and_amd64_with_two_pools(self):
        for arch in ['arm64','amd64']:
            with self.subTest(architecture=arch), tempfile.TemporaryDirectory() as d:
                cfg=yaml.safe_load((ROOT/'config.example.yaml').read_text())
                cfg['platform'].update(public_host='new-lab.example.edu',https_origin='http://new-lab.example.edu',ssh_ports=[31000,31009],data_storage_class='new-pool-a')
                cfg['network']['public_ports_approved']=True
                cfg['storage']['pools']=[{'name':n,'path':'/srv/'+n+'/volumes','storage_class':'new-'+n,'buffer_percent':10} for n in ['pool-a','pool-b']]
                cfg['storage']['datasets']='/srv/pool-a/datasets'
                facts={'architecture':arch,'cpu':24,'memory_gib':128,'node_ip':'192.0.2.25','interface':'ens3','gpu_inventory':['compatible GPU']*2,'pools':deepcopy(cfg['storage']['pools']),'existing_runtimes':{}}
                config_path=Path(d)/'config.yaml';config_path.write_text(yaml.safe_dump(cfg));out=Path(d)/'rendered'
                argv=['install.py','--config',str(config_path),'--render-only','--output',str(out)]
                with patch.object(sys,'argv',argv),patch.object(installer,'discover',return_value=facts),patch.object(installer,'apply') as apply:
                    installer.main();apply.assert_not_called()
                resolved=yaml.safe_load((out/'resolved-config.yaml').read_text())
                classes=yaml.safe_load((out/'rawfile-values.yaml').read_text())['storageClasses']
                self.assertEqual(resolved['platform']['architecture'],arch)
                self.assertIn('zot-linux-'+arch+':',resolved['registry']['zot_image'])
                self.assertEqual(resolved['platform']['node_ip'],'192.0.2.25')
                self.assertEqual([c['name'] for c in classes],['new-pool-a','new-pool-b'])
                self.assertEqual(resolved['platform']['ssh_ports'],[31000,31009])
