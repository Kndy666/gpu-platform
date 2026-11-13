from urllib.parse import parse_qs, urlsplit

from django.test import TestCase
from django.utils import timezone

from portal.models import ComputeNode, ImageArtifact, StoragePool, VolumeAllocation, Workspace
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
                Workspace.objects.create(request=request, object_name=f'{node.name}-container-{index}')
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
        menu = sidebar.split('<nav class="admin-navigation"', 1)[1]
        self.assertEqual(menu.count('<use href='), 4)
        for icon in ['i-chip', 'i-admin', 'i-check', 'i-settings']:
            self.assertIn(f'<use href="#{icon}"/>', menu)
        self.assertNotIn('aria-label="管理分类"', content)
        self.assertNotContains(response, 'class="manage-primary"')
        self.assertNotContains(response, 'class="manage-secondary"')

    def test_approval_pages_select_their_sidebar_entry_and_page_title(self):
        for tab, title in [('pending', '待审批'), ('push', '推送设置'), ('history', '推送记录')]:
            response = self.client.get('/management/approvals/', {'tab': tab})
            self.assertContains(response, f'aria-current="page">{title}')
            self.assertContains(response, f'<h1>{title}</h1>')
            self.assertContains(response, 'aria-current="page"', count=1)

    def test_workbench_keeps_global_navigation(self):
        response = self.client.get('/')
        self.assertContains(response, '开发环境')
        self.assertContains(response, 'href="/management/"')
        self.assertNotContains(response, 'data-admin-menu')

    def test_management_opens_nodes_and_uses_updated_menu_names(self):
        self.assertRedirects(self.client.get('/management/'), '/management/nodes/')
        response = self.client.get('/management/settings/')
        self.assertContains(response, 'aria-current="page">设置')
        self.assertContains(response, '<h1>设置</h1>')
        self.assertNotContains(response, '保留设置')
        self.assertNotContains(response, '>概览<')
        self.client.force_login(self.user)
        self.assertEqual(self.client.get('/management/').status_code, 302)
        self.assertEqual(self.client.get('/management/nodes/').status_code, 302)

    def test_containers_filter_and_paginate_within_selected_node(self):
        filters = {'node': self.worker.pk, 'q': 'volume', 'status': 'submitted', 'mode': 'interactive'}
        response = self.client.get('/management/containers/', filters)
        rows = response.context['tasks']
        self.assertEqual(rows.paginator.count, 26)
        self.assertTrue(all(row.node_id == self.worker.pk for row in rows))
        self.assertNotContains(response, 'local-volume-')
        next_url = rows.next_url
        for key, value in filters.items():
            self.assertEqual(parse_qs(urlsplit(next_url).query)[key], [str(value)])
        second = self.client.get('/management/containers/' + next_url)
        self.assertEqual(len(second.context['tasks']), 1)
        self.assertEqual(second.context['node'], self.worker)
        row = rows[0]
        details = self.client.get(f'/tasks/{row.pk}/?from=management')
        self.assertContains(details, f'href="/management/containers/?node={self.worker.pk}"')
        local = self.client.get('/management/containers/')
        self.assertEqual(local.context['tasks'].paginator.count, 26)
        self.assertTrue(all(row.node_id == self.local.pk for row in local.context['tasks']))

    def test_images_filter_totals_and_pagination_by_node_including_disabled_nodes(self):
        self.worker.enabled = False
        self.worker.save(update_fields=['enabled'])
        ImageArtifact.objects.bulk_create([
            ImageArtifact(node=self.worker, name=f'worker-image-{index}', reference=f'worker:{index}',
                          size_bytes=100, recycle_after=timezone.now(), cleanup_error='Retry')
            for index in range(26)
        ])
        ImageArtifact.objects.create(node=self.worker, name='deleted-image', reference='deleted:1',
                                     state='deleted', size_bytes=900, cleanup_error='Old error')
        self.image.size_bytes = 700
        self.image.save(update_fields=['size_bytes'])
        response = self.client.get('/management/images/', {'node': self.worker.pk})
        self.assertContains(response, '<h1>镜像</h1>')
        self.assertContains(response, 'data-node-filter')
        self.assertNotContains(response, '>切换</button>')
        self.assertNotContains(response, '镜像回收')
        self.assertIn(self.worker, response.context['nodes'])
        self.assertEqual(response.context['ready_bytes'], 2600)
        self.assertEqual(response.context['waiting_count'], 26)
        self.assertEqual(response.context['error_count'], 26)
        self.assertEqual(response.context['page'].paginator.count, 26)
        self.assertTrue(all(image.node_id == self.worker.pk for image in response.context['page']))
        self.assertNotContains(response, self.image.name)
        second = self.client.get('/management/images/' + response.context['page'].next_url)
        self.assertEqual(second.context['node'], self.worker)
        self.assertEqual(len(second.context['page']), 1)
        local = self.client.get('/management/images/')
        self.assertEqual(local.context['ready_bytes'], 700)
        self.assertEqual(local.context['waiting_count'], 0)
        self.assertEqual(local.context['error_count'], 0)
        self.assertEqual(list(local.context['page']), [self.image])

    def test_node_filters_show_empty_nodes_and_reject_invalid_identifiers(self):
        for path, context_key in [('/management/images/', 'page'), ('/management/containers/', 'tasks')]:
            with self.subTest(path=path):
                response = self.client.get(path, {'node': self.empty.pk})
                self.assertEqual(response.context[context_key].paginator.count, 0)
                self.assertEqual(response.context['node'], self.empty)
                for value in ['invalid', 999999]:
                    self.assertEqual(self.client.get(path, {'node': value}).status_code, 404)

    def test_image_actions_return_to_image_node(self):
        image = ImageArtifact.objects.create(node=self.worker, owner=self.user, name='snapshot',
                                             reference='worker:snapshot', temporary=True)
        for action in ['recycle', 'keep']:
            response = self.client.post(f'/images/{image.pk}/{action}/', {'origin': 'management'})
            self.assertRedirects(response, f'/management/images/?node={self.worker.pk}')
            image.refresh_from_db()
            self.assertEqual(image.recycle_after is None, action == 'keep')
        image.state = 'deleting'
        image.save(update_fields=['state'])
        response = self.client.post(f'/images/{image.pk}/keep/', {'origin': 'management'}, follow=True)
        self.assertContains(response, '镜像已经开始回收')
        self.assertEqual(response.context['node'], self.worker)

    def test_push_validation_stays_on_push_page(self):
        response = self.client.post('/management/approvals/', {'action': 'push', 'reminder_minutes': -1})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['approval_tab'], 'push')
        self.assertContains(response, 'name="reminder_minutes"')
        self.assertTrue(response.context['push_form'].errors)
