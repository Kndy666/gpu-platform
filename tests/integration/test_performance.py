from datetime import timedelta
import json

from django.test import TestCase
from django.utils import timezone

from portal.api_auth import issue_token
from portal.models import ComputeNode, SystemObservation
from portal.node_context import using_node
from portal.performance import performance_nodes, record_performance
from tests.factories import fixtures


class PerformanceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin, cls.alice, cls.bob, cls.image = fixtures()
        cls.local = cls.image.node
        cls.worker = ComputeNode.objects.create(name='worker', transport='ssh', ssh_port=2201, identity='worker', state='online')

    def snapshot(self, **changes):
        return {'version': 1, 'sampled_at': timezone.now().isoformat(), 'interval_seconds': 5,
            'cpu': {'percent': 12.5, 'cores': 64, 'load': [1, 2, 3], 'temperature_c': 52, 'temperature_sensors': []},
            'memory': {'percent': 25, 'used_gib': 16, 'total_gib': 64, 'available_gib': 48},
            'gpus': [{'index': 0, 'uuid': 'GPU-test', 'name': 'NVIDIA GeForce RTX 3080', 'utilization_percent': 80,
                'memory_used_mib': 4096, 'memory_total_mib': 10240, 'memory_percent': 40, 'temperature_c': 66, 'power_w': 205}],
            'disks': [{'name': 'disk1', 'percent': 5, 'used_gib': 300, 'total_gib': 6000}],
            'network': [{'name': 'eth0', 'rx_mib_s': 1.25, 'tx_mib_s': 0}], 'errors': [], **changes}

    def authenticate(self, user):
        self.client.defaults['HTTP_AUTHORIZATION'] = 'Bearer ' + issue_token(user)['token']

    def test_performance_page_and_api_require_staff(self):
        self.assertEqual(self.client.get('/management/performance/').status_code, 302)
        self.assertEqual(self.client.get('/api/v1/admin/performance').status_code, 401)
        self.client.force_login(self.alice)
        self.authenticate(self.alice)
        self.assertEqual(self.client.get('/management/performance/').status_code, 302)
        self.assertEqual(self.client.get('/management/performance/data/').status_code, 302)
        self.assertEqual(self.client.get('/api/v1/admin/performance').status_code, 403)

    def test_annotation_sync_is_scoped_to_its_execution_node(self):
        snapshot = self.snapshot()
        with using_node(self.worker):
            record_performance([{'metadata': {'annotations': {'platform.performance': json.dumps(snapshot)}}}])
        self.assertEqual(SystemObservation.objects.get(name='performance').node_id, self.worker.pk)
        record_performance([{'metadata': {'annotations': {'platform.performance': '{broken'}}}])
        self.assertEqual(SystemObservation.objects.filter(name='performance').count(), 1)
        self.assertIsNone(performance_nodes(self.local)[0]['metrics'])

    def test_expiry_uses_sample_time_even_when_database_was_recently_updated(self):
        SystemObservation.objects.create(node=self.worker, name='performance', data=self.snapshot(
            sampled_at=(timezone.now() - timedelta(seconds=60)).isoformat()))
        node = performance_nodes(self.worker)[0]
        self.assertFalse(node['fresh'])
        self.assertEqual(node['metrics']['cpu']['percent'], 12.5)
        SystemObservation.objects.filter(node=self.worker, name='performance').update(data=self.snapshot())
        self.assertTrue(performance_nodes(self.worker)[0]['fresh'])
        ComputeNode.objects.filter(pk=self.worker.pk).update(state='offline')
        self.assertFalse(performance_nodes(self.worker)[0]['fresh'])

    def test_api_filters_nodes_and_reports_actual_load_without_allocations(self):
        SystemObservation.objects.create(node=self.worker, name='performance', data=self.snapshot())
        self.authenticate(self.admin)
        data = self.client.get('/api/v1/admin/performance', {'node': self.worker.pk}).json()['data']
        self.assertEqual(data['refresh_seconds'], 5)
        self.assertEqual(len(data['nodes']), 1)
        self.assertEqual(data['nodes'][0]['metrics']['gpus'][0]['utilization_percent'], 80)
        self.assertEqual(data['nodes'][0]['metrics']['cpu']['temperature_c'], 52)
        self.assertEqual(self.client.get('/api/v1/admin/performance').json()['data']['nodes'][0]['id'], self.local.pk)
        self.assertEqual(self.client.get('/api/v1/admin/performance', {'node': 'bad'}).status_code, 404)

    def test_html_refresh_and_missing_temperature(self):
        snapshot = self.snapshot()
        snapshot['cpu']['temperature_c'] = None
        SystemObservation.objects.create(node=self.worker, name='performance', data=snapshot)
        self.client.force_login(self.admin)
        page = self.client.get('/management/performance/')
        self.assertEqual(page.status_code, 200)
        worker = next(node for node in page.context['nodes'] if node['id'] == self.worker.pk)
        self.assertIsNone(worker['metrics']['cpu']['temperature_c'])
        partial = self.client.get('/management/performance/data/')
        self.assertEqual(partial.status_code, 200)
        self.assertTrue(partial.json()['html'])
        self.assertIn('no-store', partial['Cache-Control'])

    def test_openapi_exposes_admin_metrics_and_node_filter(self):
        schema = self.client.get('/api/v1/openapi.json').json()
        operation = schema['paths']['/admin/performance']['get']
        self.assertEqual(operation['x-required-role'], 'staff')
        self.assertTrue(any(item['name'] == 'node' for item in operation['parameters']))
        self.assertEqual(operation['responses']['200']['content']['application/json']['schema']['$ref'],
                         '#/components/schemas/PerformanceResponse')
