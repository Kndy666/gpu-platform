from io import StringIO
import json
from django.core.management import call_command
from django.test import TestCase
from portal.models import AuditLog, EnvironmentSnapshot, ImageArtifact, ResourceRequest, ImageBuild, SSHKey, Workspace
from portal.services import parse_ssh_key
from tests.factories import fixtures, task

class PortalTests(TestCase):
    def setUp(self): self.admin, self.a, self.b, self.image = fixtures()

    def test_requires_login(self):
        for path in ['/', '/keys/', '/tasks/', '/images/', '/environments/new/']:
            self.assertEqual(self.client.get(path).status_code, 302)

    def test_own_pages_render(self):
        self.client.force_login(self.a)
        for path in ['/', '/keys/', '/tasks/', '/images/', '/environments/new/']:
            with self.subTest(path=path): self.assertEqual(self.client.get(path).status_code, 200)

    def test_other_task_and_stop_are_not_found(self):
        req = task(self.a, self.image)
        self.client.force_login(self.b)
        self.assertEqual(self.client.get(f'/tasks/{req.pk}/').status_code, 404)
        self.assertEqual(self.client.get(f'/tasks/{req.pk}/status/').status_code, 404)
        self.assertEqual(self.client.post(f'/tasks/{req.pk}/stop/').status_code, 404)

    def test_other_images_and_build_logs_hidden(self):
        ImageArtifact.objects.create(owner=self.a, name='secret-project', reference='private:a', verified=True)
        ImageBuild.objects.create(user=self.a, name='secret-build', dockerfile='FROM test', log='secret build output')
        self.client.force_login(self.b)
        response = self.client.get('/images/')
        self.assertNotContains(response, 'secret-project')
        self.assertNotContains(response, 'secret build output')

    def test_image_card_selects_only_an_available_image(self):
        own = ImageArtifact.objects.create(owner=self.a, name='my-app', reference='private:app', verified=True)
        other = ImageArtifact.objects.create(owner=self.b, name='other-app', reference='private:other', verified=True)
        unverified = ImageArtifact.objects.create(owner=self.a, name='pending-app', reference='private:pending', verified=False)
        self.client.force_login(self.a)
        self.assertContains(self.client.get('/images/'), f'/training/new/?image={own.pk}')
        for image in (own, self.image):
            with self.subTest(image=image.name):
                response = self.client.get('/training/new/', {'image': image.pk})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(str(response.context['form']['image'].value()), str(image.pk))
        for image in (other, unverified):
            with self.subTest(image=image.name):
                self.assertEqual(self.client.get('/training/new/', {'image': image.pk}).status_code, 404)

    def test_invalid_source_links_return_not_found(self):
        self.client.force_login(self.a)
        for field in ('image', 'source'):
            with self.subTest(field=field):
                self.assertEqual(self.client.get('/training/new/', {field: 'invalid-uuid'}).status_code, 404)

    def test_disabled_ssh_key_can_be_enabled_again(self):
        key = SSHKey.objects.get(user=self.a)
        key.public_key, key.fingerprint = parse_ssh_key(key.public_key)
        key.save()
        self.client.force_login(self.a)
        self.assertEqual(self.client.post(f'/keys/{key.pk}/disable/').status_code, 302)
        response = self.client.post('/keys/', {'name': key.name, 'public_key': key.public_key})
        self.assertEqual(response.status_code, 302)
        key.refresh_from_db()
        self.assertTrue(key.enabled)
        self.assertEqual(SSHKey.objects.filter(user=self.a, fingerprint=key.fingerprint).count(), 1)
        self.assertTrue(AuditLog.objects.filter(actor=self.a, action='ssh_key_enable', target=str(key.pk)).exists())

    def test_duplicate_enabled_key_keeps_form_and_database_usable(self):
        key = SSHKey.objects.get(user=self.a)
        key.public_key, key.fingerprint = parse_ssh_key(key.public_key)
        key.save()
        self.client.force_login(self.a)
        response = self.client.post('/keys/', {'name': key.name, 'public_key': key.public_key})
        self.assertIn('public_key', response.context['form'].errors)
        self.assertEqual(SSHKey.objects.filter(user=self.a).count(), 1)

    def test_build_context_invalid_port_is_a_form_error(self):
        self.client.force_login(self.a)
        response = self.client.post('/images/', {'name': 'test-build', 'dockerfile': 'FROM ubuntu:22.04',
                                                'context_git': 'https://github.com:99999/project/repo'})
        self.assertIn('context_git', response.context['form'].errors)
        self.assertFalse(ImageBuild.objects.exists())

    def test_image_history_is_paginated_without_loading_all_logs(self):
        oldest = ImageBuild.objects.create(user=self.a, name='oldest-build', dockerfile='FROM ubuntu', log='oldest-build-log')
        for index in range(10):
            ImageBuild.objects.create(user=self.a, name=f'build-{index}', dockerfile='FROM ubuntu', log='recent-build-log')
        source = task(self.a, self.image)
        for _ in range(21):
            EnvironmentSnapshot.objects.create(source=source, status='succeeded')
        self.client.force_login(self.a)
        response = self.client.get('/images/')
        self.assertEqual(len(response.context['builds']), 10)
        self.assertEqual(len(response.context['snapshots']), 20)
        self.assertNotContains(response, oldest.log)
        response = self.client.get('/images/', {'builds_page': 2, 'snapshots_page': 2})
        self.assertContains(response, oldest.log)
        self.assertEqual(len(response.context['builds']), 1)
        self.assertEqual(len(response.context['snapshots']), 1)
        self.assertIn('snapshots_page=2', response.context['builds'].previous_url)

    def test_task_pagination_preserves_selected_mode(self):
        for index in range(21):
            task(self.a, self.image, mode='batch', ends_at=None, purpose=f'program-{index}')
        task(self.b, self.image, mode='batch', ends_at=None, purpose='other-user-program')
        self.client.force_login(self.a)
        response = self.client.get('/tasks/', {'mode': 'batch'})
        self.assertEqual(len(response.context['tasks']), 20)
        self.assertIn('mode=batch', response.context['tasks'].next_url)
        self.assertNotContains(response, 'other-user-program')
        response = self.client.get('/tasks/', {'mode': 'batch', 'page': 2})
        self.assertEqual(len(response.context['tasks']), 1)

    def test_new_batch_form_sets_no_duration(self):
        self.client.force_login(self.a)
        response = self.client.post('/training/new/', {'source_kind':'image', 'image': str(self.image.pk), 'gpu': 1, 'cpu': 8, 'memory_gib': 32,
             'gpu_requests':json.dumps([{'compute_percent':100,'memory_gib':8}]),
             'workspace_gib': 100, 'purpose': 'Train model', 'command': 'python train.py', 'working_dir': '/workspace'})
        self.assertEqual(response.status_code, 302, response.content.decode())
        req = ResourceRequest.objects.get()
        self.assertIsNone(req.ends_at)
        self.assertEqual(req.argv, ['/bin/bash','-c','python train.py'])

    def test_mixed_gpu_rows_survive_invalid_submission_and_save(self):
        self.client.force_login(self.a)
        specs=[{'compute_percent':100,'memory_gib':8},{'compute_percent':50,'memory_gib':4}]
        payload={'source_kind':'image','image':str(self.image.pk),'gpu':2,'gpu_requests':json.dumps(specs),
            'cpu':2,'memory_gib':4,'workspace_gib':20,'command':'true','working_dir':'/workspace','purpose':''}
        response=self.client.post('/training/new/',payload)
        self.assertEqual(response.status_code,200)
        self.assertEqual(json.loads(response.context['form']['gpu_requests'].value()),specs)
        payload['purpose']='Mixed GPU program'
        self.assertEqual(self.client.post('/training/new/',payload).status_code,302)
        req=ResourceRequest.objects.get()
        self.assertEqual(req.gpu_requests,specs)
        self.assertEqual((req.gpu_compute_total,req.gpu_memory_total_gib),(150,12))

    def test_private_image_form_tampering_rejected(self):
        self.image.owner, self.image.shared = self.b, False
        self.image.save()
        self.client.force_login(self.a)
        response = self.client.post('/environments/new/', {'image': str(self.image.pk), 'gpu': 1, 'cpu': 8, 'memory_gib': 32,
             'workspace_gib': 100, 'purpose': 'tamper', 'mode': 'interactive', 'hours': 1, 'working_dir': '/workspace'})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(ResourceRequest.objects.exists())

    def test_student_cannot_review_tasks(self):
        req = task(self.a, self.image)
        self.client.force_login(self.a)
        self.assertEqual(self.client.post(f'/tasks/{req.pk}/review/', {'action': 'approve'}).status_code, 302)
        self.assertEqual(ResourceRequest.objects.get(pk=req.pk).status, 'submitted')

    def test_active_tasks_offer_stop_in_list_and_detail_but_finished_tasks_do_not(self):
        active = task(self.a, self.image, purpose='active-program', status='running')
        finished = task(self.a, self.image, purpose='finished-program', status='canceled')
        self.client.force_login(self.a)
        for path in ('/', '/tasks/', f'/tasks/{active.pk}/'):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertContains(response, f'action="/tasks/{active.pk}/stop/"')
                self.assertNotContains(response, f'action="/tasks/{finished.pk}/stop/"')
        self.assertNotContains(self.client.get(f'/tasks/{finished.pk}/'), 'data-stop-form')

    def test_status_watch_tracks_connection_stop_and_errors_without_early_port(self):
        req = task(self.a, self.image, status='approved')
        ws = Workspace.objects.create(request=req, object_name='watch-state', ssh_port=30047)
        self.client.force_login(self.a)
        response = self.client.get(f'/tasks/{req.pk}/')
        self.assertContains(response, 'data-ssh-port=""')
        self.assertIsNone(self.client.get(f'/tasks/{req.pk}/status/').json()['ssh_port'])
        req.status = 'running'
        req.save(update_fields=['status'])
        ws.stop_requested = True
        ws.error = '正在释放资源'
        ws.save(update_fields=['stop_requested', 'error'])
        data = self.client.get(f'/tasks/{req.pk}/status/').json()
        self.assertEqual(data['ssh_port'], 30047)
        self.assertTrue(data['stop_requested'])
        self.assertEqual(data['error'], ws.error)
        response = self.client.get(f'/tasks/{req.pk}/')
        self.assertContains(response, 'data-ssh-port="30047"')
        self.assertContains(response, 'data-stop-requested="true"')
        self.assertContains(response, f'data-error="{ws.error}"')

    def test_list_stop_returns_to_same_page_and_rejects_external_redirect(self):
        self.client.force_login(self.a)
        req = task(self.a, self.image, mode='batch', ends_at=None)
        target = '/tasks/?mode=batch&page=2'
        response = self.client.post(f'/tasks/{req.pk}/stop/', {'next': target})
        self.assertRedirects(response, target)
        req.refresh_from_db()
        self.assertEqual(req.status, 'canceled')
        req = task(self.a, self.image)
        response = self.client.post(f'/tasks/{req.pk}/stop/', {'next': 'https://other.invalid/'})
        self.assertRedirects(response, f'/tasks/{req.pk}/')

    def test_shared_workspace_image_does_not_require_retaining_build_history(self):
        self.assertFalse(ImageBuild.objects.exists())
        call_command('bootstrap_workspace', wait=1, stdout=StringIO())
        self.assertFalse(ImageBuild.objects.exists())

    def test_running_task_provides_ssh_config_with_frozen_login(self):
        req = task(self.a, self.image, status='running', login_name='workspace-user')
        ws = Workspace.objects.create(request=req, object_name='ssh-config', ssh_port=30047)
        self.client.force_login(self.a)
        response = self.client.get(f'/tasks/{req.pk}/')
        for value in [f'Host task-{req.pk}',
                      'HostName '+response.context['host'], 'Port 30047', 'User workspace-user',
                      f'HostKeyAlias task-{req.pk}']:
            self.assertContains(response, value)
        for status, port in [('queued', 30047), ('canceled', None), ('running', None)]:
            with self.subTest(status=status, port=port):
                req.status = status
                req.save(update_fields=['status'])
                ws.ssh_port = port
                ws.save(update_fields=['ssh_port'])
                self.assertNotContains(self.client.get(f'/tasks/{req.pk}/'), 'id="ssh-config"')
