from urllib.parse import parse_qs, urlsplit

from django.test import TestCase
from django.utils import timezone

from portal.models import ComputeNode, StoragePool, VolumeAllocation
from tests.factories import fixtures, task


class ManagementLayoutTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin, cls.user, cls.other, cls.image = fixtures()
        cls.local = cls.image.node
        cls.worker = ComputeNode.objects.create(name='worker', identity='worker', state='offline')
        cls.empty = ComputeNode.objects.create(name='empty', identity='empty')
        cls.local_pool = StoragePool.objects.get(node=cls.local)
        cls.worker_pool = StoragePool.objects.create(node=cls.worker, name=cls.local_pool.name,
            path='/worker', storage_class='worker-pool', capacity_gib=2000, free_gib=1800,
            healthy=True, observed_at=timezone.now())
        for node, pool in [(cls.local, cls.local_pool), (cls.worker, cls.worker_pool)]:
            for index in range(26):
                request = task(cls.user, cls.image, node=node, purpose=f'{node.name}-volume-{index}')
                VolumeAllocation.objects.create(node=node, user=cls.user, pool=pool, request=request,
                    kind='work', size_gib=1, pvc_name=f'volume-{node.pk}-{index}')

    def setUp(self):
        self.client.force_login(self.admin)

    def test_storage_keeps_pools_volumes_and_capacity_with_their_node(self):
        response = self.client.get('/management/storage/')
        nodes = {group['node'].pk: group for group in response.context['storage_nodes']}
        for node, pool in [(self.local, self.local_pool), (self.worker, self.worker_pool)]:
            group = nodes[node.pk]
            self.assertEqual([item.pk for item in group['pools']], [pool.pk])
            self.assertEqual(group['capacity_tib'], pool.capacity_gib / 1024)
            self.assertEqual(group['page'].paginator.count, 26)
            self.assertTrue(all(volume.node_id == node.pk for volume in group['page']))
            self.assertFalse(group['expanded'])
        self.assertEqual(nodes[self.empty.pk]['page'].paginator.count, 0)
        self.assertContains(response, '等待存储池同步')
        self.assertContains(response, '离线')

    def test_node_pagination_preserves_other_pages_and_opens_target_node(self):
        response = self.client.get('/management/storage/', {f'page_{self.local.pk}': 2,
            f'page_{self.worker.pk}': 1, 'open_node': self.local.pk})
        nodes = {group['node'].pk: group for group in response.context['storage_nodes']}
        self.assertEqual(len(nodes[self.local.pk]['page']), 1)
        self.assertEqual(len(nodes[self.worker.pk]['page']), 25)
        self.assertTrue(nodes[self.local.pk]['expanded'])
        link = nodes[self.worker.pk]['next_url']
        query = parse_qs(urlsplit(link).query)
        self.assertEqual(query[f'page_{self.local.pk}'], ['2'])
        self.assertEqual(query[f'page_{self.worker.pk}'], ['2'])
        self.assertEqual(query['open_node'], [str(self.worker.pk)])
        self.assertEqual(urlsplit(link).fragment, f'storage-node-{self.worker.pk}')
        invalid = self.client.get('/management/storage/', {f'page_{self.local.pk}': 'invalid'})
        self.assertEqual(invalid.context['storage_nodes'][0]['page'].number, 1)

    def test_group_edit_keeps_member_navigation_selected_and_pending_count_visible(self):
        response = self.client.get(f'/management/groups/{self.user.profile.group_id}/')
        self.assertContains(response, 'aria-current="page">用户组')
        self.assertContains(response, '52 条待审批')
        sidebar, content = response.content.decode().split('</aside>', 1)
        self.assertIn('aria-label="管理分类"', sidebar)
        self.assertIn('成员与权限', sidebar)
        self.assertIn('href="/management/storage/"', sidebar)
        self.assertNotIn('aria-label="管理分类"', content)
        self.assertNotContains(response, 'class="manage-primary"')
        self.assertNotContains(response, 'class="manage-secondary"')

    def test_approval_pages_select_their_sidebar_entry_and_page_title(self):
        for tab, title in [('pending', '待审批'), ('push', '推送设置'), ('history', '发送记录')]:
            response = self.client.get('/management/approvals/', {'tab': tab})
            self.assertContains(response, f'aria-current="page">{title}')
            self.assertContains(response, f'<h1>{title}</h1>')
            self.assertContains(response, 'aria-current="page"', count=1)

    def test_workbench_keeps_global_navigation(self):
        response = self.client.get('/')
        self.assertContains(response, '开发环境')
        self.assertContains(response, 'href="/management/"')
        self.assertNotContains(response, 'data-admin-menu')

    def test_push_validation_stays_on_push_page(self):
        response = self.client.post('/management/approvals/', {'action': 'push', 'reminder_minutes': -1})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['approval_tab'], 'push')
        self.assertContains(response, 'name="reminder_minutes"')
        self.assertTrue(response.context['push_form'].errors)
