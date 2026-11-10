import base64
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import uuid
from unittest import TestCase
from unittest.mock import Mock, patch


def load_agent():
    spec = importlib.util.spec_from_file_location('node_agent_test', Path(__file__).resolve().parents[2] / 'deploy/node-agent.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class NodeImageCleanupTests(TestCase):
    def test_local_expiry_stops_due_interactive_pods_while_controller_is_offline(self):
        pods = [{'metadata': {'name': name, 'annotations': annotation}} for name, annotation in (
            ('expired', {'platform.ends-at': '2000-01-01T00:00:00+00:00'}),
            ('future', {'platform.ends-at': '2100-01-01T00:00:00+00:00'}),
            ('batch', {}))]
        with patch.object(self.agent, 'kube', side_effect=[json.dumps({'items': pods}), '', '{"items":[]}']) as kube, \
                patch.object(self.agent, 'observe'), patch.object(self.agent, 'retry_image_cleanup'):
            self.agent.tick(self.cfg)
        deletes = [call.args for call in kube.call_args_list if 'delete' in call.args]
        self.assertEqual(deletes, [('-n', 'workloads', 'delete', 'pod', 'expired', '--wait=false', '--ignore-not-found')])

    def setUp(self):
        self.agent = load_agent()
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.agent.IMAGE_CLEANUP_DIR = Path(directory.name) / 'cleanup'
        self.operation, self.source = uuid.uuid4(), uuid.uuid4()
        self.cfg = {'platform': {'namespace': 'platform', 'workload_namespace': 'workloads', 'architecture': 'arm64'},
                    'registry': {'host': 'registry.test:5000'}, 'network': {'registry_service_ip': '10.43.0.50'}}
        self.obj = {'metadata': {'name': 'snapshot-' + self.operation.hex},
                    'data': {'request': json.dumps({'id': str(self.operation), 'source': str(self.source), 'owner': 7})}}
        self.refs = ['registry.test:5000/user-7/environment:' + self.operation.hex,
                     'docker.io/library/platform-save:' + self.operation.hex]
        self.pod = {'metadata': {'labels': {'platform.user': '7'}}, 'spec': {'containers': [{'volumeMounts': []}]},
                    'status': {'phase': 'Running', 'containerStatuses': [{'name': 'workspace', 'containerID': 'containerd://' + 'a' * 64}]}}
        auth = {'auths': {'registry.test:5000': {'auth': 'dGVzdDp0ZXN0'}}}
        self.secret = {'data': {'.dockerconfigjson': base64.b64encode(json.dumps(auth).encode()).decode()}}
        self.kube = Mock(side_effect=lambda *args: json.dumps(self.secret if 'secret' in args else {'items': [self.pod]}))
        self.response = Mock()
        self.response.read.return_value = json.dumps({'layers': [{'size': 3}], 'config': {'size': 2}}).encode()
        self.response.headers = {'Docker-Content-Digest': 'sha256:' + 'b' * 64}
        self.opener = Mock()
        self.opener.open.return_value.__enter__ = Mock(return_value=self.response)
        self.opener.open.return_value.__exit__ = Mock(return_value=False)

    def save(self, run):
        with patch.object(self.agent, 'run', run), patch.object(self.agent, 'kube', self.kube), \
             patch.object(self.agent.ssl, 'create_default_context'), \
             patch.object(self.agent.urllib.request, 'build_opener', return_value=self.opener):
            return self.agent.save_environment(self.cfg, self.obj)

    def removed(self, run):
        return [call.args[0][-1] for call in run.call_args_list if 'rm' in call.args[0]]

    def test_success_releases_both_exact_local_references(self):
        run = Mock(return_value='')
        result = self.save(run)
        self.assertEqual(result['size_bytes'], 5)
        self.assertEqual(result['reference'], self.refs[0])
        self.assertEqual(self.removed(run), self.refs)
        self.assertFalse(list(self.agent.IMAGE_CLEANUP_DIR.iterdir()))

    def test_commit_convert_push_failures_and_timeout_still_cleanup(self):
        for stage, error in [('commit', RuntimeError('commit failed')), ('convert', RuntimeError('convert failed')),
                             ('push', RuntimeError('push failed')), ('commit', subprocess.TimeoutExpired('commit', 300))]:
            with self.subTest(stage=stage, error=type(error).__name__):
                def command(args, **kwargs):
                    if stage in args:
                        # The durable record must exist before any image creation.
                        self.assertTrue((self.agent.IMAGE_CLEANUP_DIR / (self.operation.hex + '.json')).exists())
                        raise error
                    return ''
                run = Mock(side_effect=command)
                with self.assertRaises(type(error)) as caught:
                    self.save(run)
                self.assertIs(caught.exception, error)
                self.assertEqual(self.removed(run), self.refs)
                self.assertFalse(list(self.agent.IMAGE_CLEANUP_DIR.iterdir()))

    def test_manifest_validation_failure_still_cleanup(self):
        self.opener.open.side_effect = RuntimeError('manifest failed')
        run = Mock(return_value='')
        with self.assertRaisesRegex(RuntimeError, 'manifest failed'):
            self.save(run)
        self.assertEqual(self.removed(run), self.refs)

    def test_cleanup_failure_preserves_save_result_and_retries_after_restart(self):
        def unavailable(args, **kwargs):
            if 'rm' in args:
                raise subprocess.CalledProcessError(1, args)
            return ''
        run = Mock(side_effect=unavailable)
        with patch('builtins.print'):
            self.assertEqual(self.save(run)['size_bytes'], 5)
        self.assertEqual(self.removed(run), self.refs)
        record = self.agent.IMAGE_CLEANUP_DIR / (self.operation.hex + '.json')
        self.assertEqual(json.loads(record.read_text()), self.refs)
        self.assertEqual(record.stat().st_mode & 0o777, 0o600)
        restarted = load_agent()
        restarted.IMAGE_CLEANUP_DIR = self.agent.IMAGE_CLEANUP_DIR
        with patch.object(restarted, 'run', return_value='') as retry:
            restarted.retry_image_cleanup()
        self.assertEqual(self.removed(retry), self.refs)
        self.assertFalse(record.exists())

    def test_cleanup_failure_does_not_replace_original_save_exception(self):
        original = RuntimeError('original push failure')
        def command(args, **kwargs):
            if 'push' in args:
                raise original
            if 'rm' in args:
                raise subprocess.TimeoutExpired(args, 60)
            return ''
        with patch('builtins.print'), self.assertRaises(RuntimeError) as caught:
            self.save(Mock(side_effect=command))
        self.assertIs(caught.exception, original)
        self.assertTrue(list(self.agent.IMAGE_CLEANUP_DIR.glob('*.json')))
