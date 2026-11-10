import base64
import importlib.util
import io
import json
from pathlib import Path
import tempfile
from unittest.mock import Mock, patch

from django.core.exceptions import ValidationError
from django.test import TestCase, SimpleTestCase
from django.utils import timezone

from portal.configuration import config
from portal.forms import RequestForm
from portal.models import (ComputeNode, EnvironmentSnapshot, ImageArtifact, ResourceRequest, StoragePool,
                           SystemObservation, VolumeAllocation, Workspace)
from portal.node_context import using_node
from portal.node_transport import NodeUnavailable, encode_message, read_message
from portal.nodes import NodeForm
from portal.reconcile import Reconciler, reserve_dispatch
from portal.services import approve, validate_request
from tests.factories import fixtures, task

ROOT = Path(__file__).resolve().parents[2]


class NodeIsolationTests(TestCase):
    def setUp(self):
        self.admin, self.alice, self.bob, self.image = fixtures()
        self.local = self.image.node
        self.worker = ComputeNode.objects.create(name='worker-a', transport='ssh', ssh_port=2201,
            identity='worker-a', architecture='amd64', state='online', last_seen=timezone.now())
        self.remote_image = ImageArtifact.objects.create(node=self.worker, name='Ubuntu AMD64',
            architecture='amd64', reference='test/amd64:1', digest='sha256:'+'b'*64, verified=True, shared=True)
        self.remote_pool = StoragePool.objects.create(node=self.worker, name='test', path='/test',
            storage_class='rawfile-test', capacity_gib=2000, free_gib=2000, healthy=True, observed_at=timezone.now())

    def request(self, **kwargs):
        return task(self.alice, self.remote_image, node=self.worker, **kwargs)

    def test_home_and_work_volumes_are_bound_to_selected_node(self):
        first = task(self.alice, self.image)
        second = self.request()
        approve(first.pk, self.admin)
        approve(second.pk, self.admin)
        homes = VolumeAllocation.objects.filter(user=self.alice, kind='home')
        self.assertEqual(set(homes.values_list('node_id', flat=True)), {self.local.pk, self.worker.pk})
        self.assertEqual(second.volumeallocation.pool_id, self.remote_pool.pk)
        self.assertEqual(second.volumeallocation.node_id, self.worker.pk)
        self.assertEqual(VolumeAllocation.objects.filter(node=self.local, kind='work').count(), 1)

    def test_cross_node_images_and_source_environments_are_rejected(self):
        request = self.request()
        request.image = self.image
        with self.assertRaisesRegex(ValidationError, '镜像'):
            validate_request(request)
        request.image = self.remote_image
        request.source_environment = task(self.alice, self.image)
        with self.assertRaisesRegex(ValidationError, '同一节点'):
            validate_request(request)

    def test_node_context_is_scoped_and_restores_configuration(self):
        self.worker.runtime_config = {'storage': {'datasets': '/worker/datasets'}}
        original = config()['storage']['datasets']
        with using_node(self.worker):
            self.assertEqual(list(ImageArtifact.objects.values_list('pk', flat=True)), [self.remote_image.pk])
            self.assertEqual(config()['platform']['architecture'], 'amd64')
            self.assertEqual(config()['storage']['datasets'], '/worker/datasets')
            self.assertEqual(ImageArtifact._base_manager.count(), 2)
        self.assertEqual(ImageArtifact.objects.count(), 2)
        self.assertEqual(config()['storage']['datasets'], original)

    def test_form_only_offers_images_and_gpu_inventory_from_selected_node(self):
        form = RequestForm(user=self.alice, mode='interactive', initial={'node': self.worker.pk})
        self.assertEqual(form.instance.node_id, self.worker.pk)
        self.assertEqual(list(form.fields['image'].queryset), [self.remote_image])

    def test_offline_node_preserves_running_task_leases_and_ports(self):
        request = self.request()
        approve(request.pk, self.admin)
        ResourceRequest.objects.filter(pk=request.pk).update(status='running')
        Workspace.objects.filter(pk=request.pk).update(ssh_port=30022, started_at=timezone.now(), dispatch_started_at=timezone.now())
        kube = Mock()
        with patch('portal.node_transport.node_call', side_effect=NodeUnavailable('offline')):
            Reconciler(node=self.worker, kube=kube).tick()
        request.refresh_from_db()
        request.workspace.refresh_from_db()
        self.worker.refresh_from_db()
        self.assertEqual(request.status, 'running')
        self.assertFalse(request.workspace.cleanup_complete)
        self.assertEqual(request.workspace.ssh_port, 30022)
        self.assertEqual(self.worker.state, 'offline')
        kube.delete_owned.assert_not_called()
        self.assertTrue(StoragePool.objects.get(node=self.local).healthy)

    def test_global_dispatch_quota_is_shared_across_execution_nodes(self):
        first = task(self.alice, self.image, cpu=60)
        second = self.request(cpu=40)
        approve(first.pk, self.admin)
        approve(second.pk, self.admin)
        self.assertTrue(reserve_dispatch(first, first.workspace))
        with using_node(self.worker):
            self.assertFalse(reserve_dispatch(second, second.workspace))
        second.workspace.refresh_from_db()
        self.assertIsNone(second.workspace.dispatch_started_at)

    def test_running_node_connection_cannot_be_changed(self):
        request = self.request()
        approve(request.pk, self.admin)
        data = {name: getattr(self.worker, name) for name in NodeForm.Meta.fields}
        data['ssh_port'] = 2203
        form = NodeForm(data, instance=self.worker)
        self.assertFalse(form.is_valid())
        self.assertIn('释放', str(form.errors))

    def test_node_management_requires_staff(self):
        self.client.force_login(self.alice)
        self.assertEqual(self.client.get('/management/nodes/').status_code, 302)
        self.client.force_login(self.admin)
        response = self.client.get('/management/nodes/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.worker.name)

    def test_home_usage_on_another_node_does_not_block_idle_volume(self):
        from portal.volumes import volume_busy, annotate_volume_usage
        request = self.request()
        approve(request.pk, self.admin)
        local_home = VolumeAllocation.objects.create(user=self.alice, node=self.local,
            kind='home', pool=StoragePool.objects.get(node=self.local), size_gib=50, pvc_name='idle-local-home')
        self.assertFalse(volume_busy(local_home))
        annotate_volume_usage([local_home])
        self.assertFalse(local_home.busy)

    def test_remote_logs_use_the_request_node(self):
        from portal.api_auth import issue_token
        from portal.kube import KubeClient
        request = self.request()
        fake = Mock()
        fake.path = KubeClient.path
        fake.list.return_value = [{'metadata': {'name': 'remote-pod'}, 'status': {'phase': 'Running'}}]
        fake.call.return_value = 'worker log'
        self.client.defaults['HTTP_AUTHORIZATION'] = 'Bearer ' + issue_token(self.alice)['token']
        with patch('portal.kube.KubeClient', return_value=fake) as client:
            response = self.client.get(f'/api/v1/tasks/{request.pk}/logs')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['data']['text'], 'worker log')
        client.assert_called_once_with(node=self.worker)

    def test_worker_registration_api_requires_staff(self):
        from portal.api_auth import issue_token
        self.client.defaults['HTTP_AUTHORIZATION'] = 'Bearer ' + issue_token(self.alice)['token']
        self.assertEqual(self.client.get('/api/v1/nodes').status_code, 200)
        response = self.client.post('/api/v1/nodes', data='{}', content_type='application/json')
        self.assertEqual(response.status_code, 403)

    def test_node_filtered_api_lists_do_not_mix_storage_or_images(self):
        from portal.api_auth import issue_token
        local = task(self.alice, self.image)
        remote = self.request()
        approve(local.pk, self.admin)
        approve(remote.pk, self.admin)
        self.client.defaults['HTTP_AUTHORIZATION'] = 'Bearer ' + issue_token(self.alice)['token']
        for endpoint in ('tasks', 'images', 'volumes'):
            response = self.client.get('/api/v1/' + endpoint, {'node': self.worker.pk})
            self.assertEqual(response.status_code, 200)
            items = response.json()['data']['items']
            self.assertTrue(items)
            self.assertTrue(all(item['node_id'] == self.worker.pk for item in items))
        self.assertEqual(self.client.get('/api/v1/tasks', {'node': 'bad'}).status_code, 404)

    def test_resource_counts_follow_selected_node(self):
        from portal.queries import resource_overview
        task(self.alice, self.image, status='queued')
        self.request(status='queued')
        self.assertEqual(resource_overview()['queued_count'], 2)
        self.assertEqual(resource_overview(self.worker)['queued_count'], 1)

    def test_reconnected_worker_resumes_scoped_synchronization(self):
        from copy import deepcopy
        from portal.node_context import active_node
        cfg = deepcopy(config())
        cfg['platform']['architecture'] = 'amd64'
        observation = {'protocol': 1, 'config': cfg, 'pools': [{
            'name': 'test', 'path': '/test', 'storage_class': 'rawfile-test',
            'buffer_percent': 5, 'capacity_gib': 2000, 'free_gib': 1800}]}
        self.worker.state = 'offline'
        self.worker.save(update_fields=['state'])
        reconciler = Reconciler(node=self.worker, kube=Mock())
        def check_scope():
            self.assertEqual(active_node.get(), self.worker)
            self.assertEqual(StoragePool.objects.count(), 1)
        with patch('portal.node_transport.node_call', side_effect=[observation, {'ports': []}]), \
                patch('portal.builds.ensure_base_build'), patch.object(reconciler, '_tick', side_effect=check_scope) as tick:
            reconciler.tick()
        tick.assert_called_once()
        self.worker.refresh_from_db()
        self.assertEqual(self.worker.state, 'online')
        self.assertIsNotNone(self.worker.last_seen)

    def test_api_chooses_the_selected_nodes_default_image(self):
        from portal.api_auth import issue_token
        self.client.defaults['HTTP_AUTHORIZATION'] = 'Bearer ' + issue_token(self.alice)['token']
        response = self.client.post('/api/v1/tasks', data=json.dumps({'node': self.worker.pk,
            'mode': 'batch', 'purpose': 'remote run', 'command': 'true', 'gpu': 0,
            'cpu': 2, 'memory_gib': 4, 'workspace_gib': 20}), content_type='application/json')
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()['data']['node_id'], self.worker.pk)
        self.assertEqual(response.json()['data']['image_id'], str(self.remote_image.pk))

    def test_timeline_can_select_one_node(self):
        from portal.timeline import allocation_timeline
        for image, node in ((self.image, self.local), (self.remote_image, self.worker)):
            req = task(self.alice, image, node=node)
            approve(req.pk, self.admin)
            Workspace.objects.filter(pk=req.pk).update(started_at=timezone.now())
        result = allocation_timeline(self.admin, node=self.worker)
        ids = {block['id'] for block in result['blocks']}
        self.assertEqual(ids, {str(ResourceRequest.objects.get(node=self.worker).pk)})

    def test_timeline_groups_entries_and_resource_rows_by_node(self):
        from portal.timeline import allocation_timeline
        for image, node in ((self.image, self.local), (self.remote_image, self.worker)):
            req = task(self.alice, image, node=node)
            approve(req.pk, self.admin)
            Workspace.objects.filter(pk=req.pk).update(started_at=timezone.now())
        result = allocation_timeline(self.alice)
        self.assertEqual([group['id'] for group in result['groups']], [self.local.pk, self.worker.pk])
        for group in result['groups']:
            self.assertEqual(len(group['entries']), 1)
            self.assertTrue(all(row['node_id'] == group['id'] for row in group['rows']))
            self.assertTrue(all(block['node_id'] == group['id'] for block in group['blocks']))
        row_ids = [row['id'] for row in result['rows']]
        self.assertEqual(len(row_ids), len(set(row_ids)))

    def test_grouped_timeline_preserves_other_users_task_privacy(self):
        from portal.timeline import allocation_timeline
        req = task(self.bob, self.remote_image, node=self.worker, purpose='private experiment')
        Workspace.objects.create(request=req, object_name='private', started_at=timezone.now())
        group = next(group for group in allocation_timeline(self.alice)['groups'] if group['id'] == self.worker.pk)
        self.assertEqual(len(group['entries']), 1)
        self.assertIsNone(group['entries'][0]['id'])
        self.assertIsNone(group['entries'][0]['url'])
        self.assertEqual(group['entries'][0]['title'], '已分配')

    def test_dashboard_keeps_each_nodes_resource_summary_and_gpu_inventory_together(self):
        from portal.models import SystemObservation
        for node, cpu, count in ((self.local, 88, 4), (self.worker, 64, 8)):
            SystemObservation.objects.create(node=node, name='resources', data={'cpu': {'total': cpu, 'available': cpu, 'used': 0}})
            SystemObservation.objects.create(node=node, name='gpu_inventory', data={'devices': [
                {'index': index, 'uuid': node.name + str(index), 'name': 'NVIDIA GeForce RTX 3080', 'memory_mib': 10240}
                for index in range(count)]})
        self.client.force_login(self.alice)
        response = self.client.get('/')
        groups = response.context['node_groups']
        self.assertEqual([group['resources']['cpu']['available'] for group in groups], [88, 64])
        self.assertEqual([len(group['gpus']) for group in groups], [4, 8])
        self.assertContains(response, 'class="node-card"', count=2)
        self.assertNotContains(response, 'SM</dd>')

    def test_extension_reaches_local_or_remote_execution_node(self):
        from datetime import timedelta
        from portal.services import extend
        for image, node in ((self.image, self.local), (self.remote_image, self.worker)):
            with self.subTest(node=node.name):
                req = task(self.alice, image, node=node)
                approve(req.pk, self.admin)
                Workspace.objects.filter(pk=req.pk).update(dispatch_started_at=timezone.now())
                new_end = req.ends_at + timedelta(hours=1)
                with patch('portal.kube.KubeClient') as client:
                    extend(req.pk, self.admin, new_end, 'continue')
                    client.assert_called_once_with(node=node)
                    self.assertEqual(client.return_value.call.call_args.args[2]['metadata']['annotations']['platform.ends-at'], new_end.isoformat())
                with patch('portal.kube.KubeClient') as client:
                    client.return_value.call.side_effect = NodeUnavailable('offline')
                    with self.assertRaises(ValidationError):
                        extend(req.pk, self.admin, new_end + timedelta(hours=1), 'continue')
                req.refresh_from_db()
                self.assertEqual(req.ends_at, new_end)


class NodeAPITests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin, cls.alice, cls.bob, cls.image = fixtures()
        cls.local = cls.image.node
        cls.worker = ComputeNode.objects.create(name='worker-a', transport='ssh', ssh_port=2201,
            identity='worker-a', architecture='amd64', state='online', last_seen=timezone.now())
        cls.remote_image = ImageArtifact.objects.create(node=cls.worker, name='Ubuntu AMD64', size_bytes=1234,
            architecture='amd64', reference='test/amd64:1', digest='sha256:'+'b'*64, verified=True, shared=True)
        StoragePool.objects.create(node=cls.worker, name='test', path='/test', storage_class='rawfile-test',
            capacity_gib=2000, free_gib=2000, healthy=True, observed_at=timezone.now())

    def setUp(self):
        self.authenticate(self.alice)

    def authenticate(self, user):
        from portal.api_auth import issue_token
        self.client.defaults['HTTP_AUTHORIZATION'] = 'Bearer ' + issue_token(user)['token']

    def requests_on_both_nodes(self):
        local = task(self.alice, self.image)
        remote = task(self.alice, self.remote_image, node=self.worker)
        for req in (local, remote):
            approve(req.pk, self.admin)
        return local, remote

    def test_resources_expose_dashboard_groups_and_filter_one_node(self):
        for node, cpu, count in ((self.local, 88, 4), (self.worker, 64, 8)):
            SystemObservation.objects.create(node=node, name='resources', data={
                'cpu': {'total': cpu, 'available': cpu, 'used': 0}})
            SystemObservation.objects.create(node=node, name='gpu_inventory', data={'devices': [
                {'index': index, 'uuid': node.name + str(index), 'name': 'NVIDIA GeForce RTX 3080', 'memory_mib': 10240}
                for index in range(count)]})
        data = self.client.get('/api/v1/resources').json()['data']
        self.assertEqual([group['id'] for group in data['nodes']], [self.local.pk, self.worker.pk])
        self.assertEqual([group['resources']['cpu']['available'] for group in data['nodes']], [88, 64])
        self.assertEqual([len(group['gpus']) for group in data['nodes']], [4, 8])
        selected = self.client.get('/api/v1/resources', {'node': self.worker.pk}).json()['data']
        self.assertEqual(selected['nodes'], [data['nodes'][1]])
        self.assertEqual(selected['resources']['cpu']['total'], 64)
        self.assertEqual(selected['default_image_id'], str(self.remote_image.pk))

    def test_snapshot_node_filter_preserves_ownership_and_identifies_node(self):
        local, remote = self.requests_on_both_nodes()
        private = task(self.bob, self.remote_image, node=self.worker)
        for req in (local, remote, private):
            EnvironmentSnapshot.objects.create(source=req)
        self.assertEqual(self.client.get('/api/v1/snapshots').json()['data']['count'], 2)
        data = self.client.get('/api/v1/snapshots', {'node': self.worker.pk}).json()['data']
        self.assertEqual(data['count'], 1)
        record = data['items'][0]
        self.assertEqual(record['source_id'], str(remote.pk))
        self.assertEqual((record['node_id'], record['node_name']), (self.worker.pk, self.worker.name))
        self.assertEqual(self.client.get('/api/v1/snapshots/' + record['id']).json()['data'], record)
        self.authenticate(self.admin)
        self.assertEqual(self.client.get('/api/v1/snapshots', {'node': self.worker.pk}).json()['data']['count'], 0)

    def test_admin_node_filter_applies_to_pools_volumes_containers_and_image_totals(self):
        local, remote = self.requests_on_both_nodes()
        self.authenticate(self.admin)
        containers = self.client.get('/api/v1/admin/containers', {'node': self.worker.pk}).json()['data']
        self.assertEqual([item['id'] for item in containers['items']], [str(remote.pk)])
        images = self.client.get('/api/v1/admin/images', {'node': self.worker.pk}).json()['data']
        self.assertEqual([item['id'] for item in images['items']], [str(self.remote_image.pk)])
        self.assertEqual(images['summary']['ready_bytes'], 1234)
        storage = self.client.get('/api/v1/admin/storage', {'node': self.worker.pk}).json()['data']
        self.assertEqual(len(storage['pools']), 1)
        self.assertEqual(storage['pools'][0]['node_name'], self.worker.name)
        self.assertEqual(storage['volumes']['count'], 2)
        self.assertTrue(all(item['node_id'] == self.worker.pk for item in storage['volumes']['items']))
        self.assertEqual(self.client.get('/api/v1/admin/storage').json()['data']['volumes']['count'], 4)

    def test_new_node_filters_reject_unknown_nodes_and_keep_staff_permissions(self):
        paths = ('snapshots', 'admin/containers', 'admin/images', 'admin/storage')
        for path in paths[1:]:
            self.assertEqual(self.client.get('/api/v1/' + path, {'node': self.worker.pk}).status_code, 403)
        self.authenticate(self.admin)
        for path in paths:
            for node in ('bad', 999999):
                with self.subTest(path=path, node=node):
                    self.assertEqual(self.client.get('/api/v1/' + path, {'node': node}).status_code, 404)

    def test_node_registration_and_maintenance_via_api(self):
        self.authenticate(self.admin)
        response = self.client.post('/api/v1/nodes', data=json.dumps({'name': 'worker-b', 'transport': 'ssh',
            'ssh_port': 2202, 'ssh_user': 'gpu-platform', 'identity': 'worker-b', 'architecture': 'amd64',
            'enabled': True, 'maintenance': False}), content_type='application/json')
        self.assertEqual(response.status_code, 201, response.content)
        node = response.json()['data']
        response = self.client.patch('/api/v1/nodes/' + str(node['id']), data=json.dumps({'maintenance': True}),
            content_type='application/json')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(response.json()['data']['maintenance'])
        self.assertTrue(response.json()['data']['enabled'])
        self.authenticate(self.alice)
        visible = self.client.get('/api/v1/nodes/' + str(node['id'])).json()['data']
        self.assertNotIn('identity', visible)
        self.assertNotIn('ssh_user', visible)
        self.assertEqual(self.client.patch('/api/v1/nodes/' + str(node['id']), data='{}', content_type='application/json').status_code, 403)

    def test_timeline_api_exposes_queued_tasks_per_node_with_privacy(self):
        for user, image, node, purpose in ((self.alice, self.image, self.local, 'local'),
                (self.alice, self.remote_image, self.worker, 'my program'),
                (self.bob, self.remote_image, self.worker, 'private program')):
            req = task(user, image, node=node, purpose=purpose, mode='batch', status='queued', starts_at=None, ends_at=None)
            Workspace.objects.create(request=req, object_name=str(req.pk))
        data = self.client.get('/api/v1/timeline', {'node': self.worker.pk}).json()['data']
        self.assertEqual([group['id'] for group in data['groups']], [self.worker.pk])
        queued = data['groups'][0]['queued']
        self.assertEqual(len(queued), 2)
        self.assertEqual([entry['title'] for entry in queued], ['my program', '排队任务'])
        self.assertIsNone(queued[1]['id'])
        self.assertIsNone(queued[1]['url'])
        self.assertEqual(data['blocks'], [])
        self.authenticate(self.admin)
        queued = self.client.get('/api/v1/timeline', {'node': self.worker.pk}).json()['data']['groups'][0]['queued']
        self.assertEqual(queued[1]['title'], 'private program')

    def test_openapi_discovers_grouped_resources_queue_and_all_node_filters(self):
        schema = self.client.get('/api/v1/openapi.json').json()
        for path in ('resources', 'gpus', 'timeline', 'tasks', 'images', 'builds', 'snapshots', 'volumes',
                     'datasets', 'datasets/download', 'admin/containers', 'admin/storage', 'admin/images'):
            parameters = schema['paths']['/' + path]['get']['parameters']
            parameter = next(item for item in parameters if item['name'] == 'node')
            self.assertEqual(parameter['schema']['type'], 'integer')
        self.assertIn('201', schema['paths']['/nodes']['post']['responses'])
        self.assertEqual(schema['paths']['/nodes/{pk}']['patch']['x-required-role'], 'staff')
        for path, expected in (('nodes', 'NodeListResponse'), ('nodes/{pk}', 'NodeResponse'), ('timeline', 'TimelineResponse')):
            self.assertEqual(schema['paths']['/' + path]['get']['responses']['200']['content']['application/json']['schema']['$ref'],
                '#/components/schemas/' + expected)
        self.assertIn('nodes', schema['components']['schemas']['ResourcesResponse']['properties']['data']['properties'])
        self.assertIn('queued', schema['components']['schemas']['TimelineGroup']['properties'])


class WorkerProtocolTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        spec = importlib.util.spec_from_file_location('worker_rpc_tests', ROOT / 'deploy/worker-rpc.py')
        cls.worker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.worker)
        spec = importlib.util.spec_from_file_location('node_links_tests', ROOT / 'deploy/node-links.py')
        cls.links = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.links)

    def test_json_protocol_rejects_unterminated_and_non_object_messages(self):
        for data in (b'{}', b'[]\n', b'null\n'):
            with self.assertRaises(ValueError):
                read_message(io.BytesIO(data))
        value = {'operation': 'observe', 'parameters': {}}
        self.assertEqual(read_message(io.BytesIO(encode_message(value))), value)

    def test_dataset_read_is_bounded_and_symlinks_cannot_escape_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'data').write_bytes(b'dataset')
            (root / 'escape').symlink_to('/etc/passwd')
            cfg = {'storage': {'datasets': directory}}
            response = self.worker.dataset_request(cfg, action='read', path='data')
            self.assertEqual(base64.b64decode(response['data']), b'dataset')
            with self.assertRaises(OSError):
                self.worker.dataset_request(cfg, action='read', path='escape')
            with self.assertRaises(ValueError):
                self.worker.dataset_request(cfg, action='read', path='../etc/passwd')

    def test_kube_rpc_rejects_external_urls_and_parent_paths(self):
        for path in ('https://example.com/api/v1/pods', '/api/v1/../etc/passwd', '/api/v1/%2e%2e/pods'):
            with self.assertRaises(ValueError):
                self.worker.kube_request('GET', path)

    def test_ssh_connection_only_uses_loopback_and_validated_key_names(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'worker-a').touch()
            links = self.links.Links(root, root / 'run', 30022, 30121)
            connection = {'name': 'worker-a', 'identity': 'worker-a', 'user': 'gpu-platform', 'port': 2201}
            command, host = links.command(connection)
            self.assertEqual(host, 'gpu-platform@127.0.0.1')
            self.assertIn('BatchMode=yes', command)
            self.assertIn('StrictHostKeyChecking=yes', command)
            with self.assertRaises(ValueError):
                links.command({**connection, 'identity': '../../secret'})
            with self.assertRaises(ValueError):
                links.call(connection, 'forwards', {'ports': [22]})

    def test_expired_node_budget_never_starts_another_request(self):
        from portal.node_transport import node_call, request_budget
        with patch('portal.node_transport.socket_call') as call:
            with request_budget(-1), self.assertRaises(NodeUnavailable):
                node_call(Mock(), 'observe')
            call.assert_not_called()
