import io
import json
from datetime import timedelta
from pathlib import Path
import tempfile
from unittest.mock import patch
import zipfile

from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from portal.api_auth import TOKEN_PREFIX, issue_token, token_id
from portal.models import (AuditLog, EnvironmentSnapshot, ImageArtifact, ImageBuild, PlatformSettings,
                           ResourceRequest, SSHKey, StoragePool, SystemObservation, UserGroup, VolumeAllocation, Workspace)
from portal.services import approve
from tests.factories import fixtures, task, set_member_settings


class APITests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin, cls.a, cls.b, cls.image = fixtures()

    def setUp(self):
        self.token = issue_token(self.a)['token']
        self.client.defaults['HTTP_AUTHORIZATION'] = 'Bearer '+self.token

    def call(self, method, path, payload=None, **headers):
        data = json.dumps(payload) if payload is not None else ''
        return getattr(self.client, method)(f'/api/v1/{path}', data=data, content_type='application/json', **headers)

    def staff(self):
        self.client.defaults['HTTP_AUTHORIZATION'] = 'Bearer '+issue_token(self.admin)['token']

    def create_task(self, **fields):
        response = self.call('post', 'tasks', {'purpose': 'api-environment', **fields})
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()['data']

    def test_multi_gpu_shares_use_compute_and_vram_group_limits(self):
        group=self.a.profile.group
        set_member_settings(self.a,gpu_compute_percent=200,gpu_memory_gib=16)
        specs=[{'compute_percent':100,'memory_gib':8},{'compute_percent':50,'memory_gib':4}]
        result=self.create_task(gpu_requests=specs)
        self.assertEqual((result['gpu'],result['gpu_requests']),(2,specs))
        self.assertEqual((result['gpu_compute_total'],result['gpu_memory_total_gib']),(150,12))
        self.assertEqual(result['gpu_allocations'],[])
        self.assertEqual(self.call('post','tasks',{'purpose':'over-compute','gpu_requests':specs+[{'compute_percent':51,'memory_gib':1}]}).status_code,400)
        self.assertEqual(self.call('post','tasks',{'purpose':'over-vram','gpu_requests':[{'compute_percent':50,'memory_gib':17}]}).status_code,400)

    def test_invalid_gpu_percent_and_protected_runtime_settings(self):
        for value in [0,1,101]:
            self.assertEqual(self.call('post','tasks',{'purpose':'bad-share','gpu':1,'gpu_requests':[{'compute_percent':value,'memory_gib':8}]}).status_code,400)
        for key in ['CUDA_MPS_PIPE_DIRECTORY','NVIDIA_VISIBLE_DEVICES','CUDA_VISIBLE_DEVICES']:
            self.assertEqual(self.call('post','tasks',{'purpose':'bad-runtime','mode':'batch','gpu':1,'command':'true','environment':{key:'all'}}).status_code,400)

    def test_gpu_rows_validate_shape_types_and_card_count(self):
        valid={'compute_percent':50,'memory_gib':4}
        for specs in [False,{},[None],[{}],[{**valid,'compute_percent':True}],[{**valid,'memory_gib':0}],
                      [{**valid,'memory_gib':1.5}],[{**valid,'uuid':'GPU-picked'}]]:
            with self.subTest(specs=specs):
                self.assertEqual(self.call('post','tasks',{'purpose':'bad-rows','gpu':1,'gpu_requests':specs}).status_code,400)
        for count in [0,2]:
            self.assertEqual(self.call('post','tasks',{'purpose':'mismatch','gpu':count,'gpu_requests':[valid]}).status_code,400)

    def test_fractional_gpu_policy_checks_per_card_and_total_limits(self):
        group=self.a.profile.group
        group.gpu_compute_percent=75;group.gpu_memory_gib=6
        group.gpu_compute_per_card_percent=50;group.gpu_memory_per_card_gib=4;group.save()
        set_member_settings(self.a,**{name:getattr(group,name) for name in ['gpu_compute_percent','gpu_memory_gib','gpu_compute_per_card_percent','gpu_memory_per_card_gib']})
        cases = [
            [{'compute_percent': 51, 'memory_gib': 2}],
            [{'compute_percent': 50, 'memory_gib': 5}],
            [{'compute_percent': 50, 'memory_gib': 2}] * 2,
            [{'compute_percent': 25, 'memory_gib': 4}] * 2,
        ]
        for specs in cases:
            with self.subTest(specs=specs):
                response = self.call('post', 'tasks', {'purpose': 'quota-check', 'gpu_requests': specs})
                self.assertEqual(response.status_code, 400, response.content)
        self.assertFalse(ResourceRequest.objects.exists())
        result=self.create_task(gpu_requests=[{'compute_percent':50,'memory_gib':4},{'compute_percent':25,'memory_gib':2}])
        self.assertEqual((result['gpu_compute_total'],result['gpu_memory_total_gib']),(75,6))
        from portal.queues import queue_objects
        cluster,_=queue_objects(self.a,group)
        resources={v['name']:v for v in cluster['spec']['resourceGroups'][0]['flavors'][0]['resources']}
        self.assertEqual(resources['gpu.platform/compute']['borrowingLimit'],'75')
        self.assertEqual(resources['gpu.platform/memory-mib']['borrowingLimit'],str(6*1024))

    def test_group_api_roundtrips_fine_grained_gpu_limits(self):
        self.staff()
        payload={'name':'半卡用户','gpu_compute_percent':75,'gpu_memory_gib':6,
                 'gpu_compute_per_card_percent':50,'gpu_memory_per_card_gib':4}
        response=self.call('post','admin/groups',payload)
        self.assertEqual(response.status_code,201,response.content)
        pk=response.json()['data']['id']
        data=self.call('get','admin/groups/'+str(pk)).json()['data']
        self.assertTrue(all(data[key]==value for key,value in payload.items()))
        self.assertEqual(self.call('patch','admin/groups/'+str(pk),{'gpu_compute_per_card_percent':101}).status_code,400)
        self.assertEqual(self.call('patch','admin/groups/'+str(pk),{'gpu_compute_percent':50,'gpu_memory_per_card_gib':2}).status_code,200)
        self.assertEqual(self.call('delete','admin/groups/'+str(pk)).status_code,200)

    def test_max_gpu_options_resolve_with_live_group_limits_before_storage(self):
        SystemObservation.objects.create(name='gpu_inventory',data={'devices':[{'uuid':'GPU-'+str(i),'index':i,
            'memory_mib':24564,'usable_memory_mib':24052,'sm_count':114,'ready':True} for i in range(2)]})
        group=self.a.profile.group
        group.gpu_compute_percent=75;group.gpu_memory_gib=6
        group.gpu_compute_per_card_percent=50;group.gpu_memory_per_card_gib=4;group.save()
        set_member_settings(self.a,**{name:getattr(group,name) for name in ['gpu_compute_percent','gpu_memory_gib','gpu_compute_per_card_percent','gpu_memory_per_card_gib']})
        limits=self.call('get','gpus').json()['data']['request_limits']
        self.assertEqual((limits['compute_per_card_percent'],limits['memory_per_card_gib']),(50,4))
        specs=[{'compute_percent':'max','memory_gib':'max'}]*2
        data=self.create_task(gpu_requests=specs)
        expected=[{'compute_percent':38,'memory_gib':3},{'compute_percent':37,'memory_gib':3}]
        self.assertEqual(data['gpu_requests'],expected)
        self.assertEqual(ResourceRequest.objects.get(pk=data['id']).gpu_requests,expected)
        mixed=self.create_task(gpu_requests=[{'compute_percent':25,'memory_gib':2},{'compute_percent':'max','memory_gib':'max'}])
        self.assertEqual(mixed['gpu_requests'],[{'compute_percent':25,'memory_gib':2},{'compute_percent':50,'memory_gib':4}])
        set_member_settings(self.a,gpu_compute_percent=40,gpu_memory_gib=2)
        self.assertEqual(self.create_task(gpu_requests=specs)['gpu_requests'],[{'compute_percent':20,'memory_gib':1}]*2)

    def test_max_gpu_options_fail_when_unresolvable_or_shape_is_invalid(self):
        specs=[{'compute_percent':'max','memory_gib':'max'}]
        self.assertEqual(self.call('post','tasks',{'purpose':'missing-inventory','gpu_requests':specs}).status_code,400)
        SystemObservation.objects.create(name='gpu_inventory',data={'devices':[{'uuid':'GPU-a','index':0,
            'memory_mib':24564,'usable_memory_mib':24052,'sm_count':114,'ready':True}]})
        for extra in [{'gpu':2},{'gpu_requests':specs*2},{'gpu_requests':[{'compute_percent':'MAX','memory_gib':'max'}]},
                      {'gpu_requests':[{'compute_percent':True,'memory_gib':'max'}]}, {'request_limits':{'compute_total_percent':999}}]:
            with self.subTest(extra=extra):
                self.assertEqual(self.call('post','tasks',{'purpose':'bad-max','gpu_requests':specs,**extra}).status_code,400)
        set_member_settings(self.a,gpu_compute_percent=0)
        self.assertEqual(self.call('post','tasks',{'purpose':'zero-quota','gpu_requests':specs}).status_code,400)

    def test_unlimited_gpu_max_uses_whole_card_with_reserved_vram_excluded(self):
        set_member_settings(self.a,unlimited=True)
        SystemObservation.objects.create(name='gpu_inventory',data={'devices':[{'uuid':'GPU-a','index':0,
            'memory_mib':24564,'usable_memory_mib':24052,'sm_count':114,'ready':True,'used_compute_percent':80,'used_memory_mib':16000}]})
        data=self.create_task(gpu_requests=[{'compute_percent':'max','memory_gib':'max'}])
        self.assertEqual(data['gpu_requests'],[{'compute_percent':100,'memory_gib':23}])
        limits=self.call('get','gpus').json()['data']['request_limits']
        self.assertIsNone(limits['compute_total_percent'])
        self.assertEqual(limits['memory_per_card_gib'],23)

    def test_mixed_gpu_manifest_and_timeline_match_each_physical_device(self):
        from portal.manifests import task_objects
        from portal.timeline import allocation_timeline
        from portal.services import reserve_volume
        from portal.gpu_math import COMPUTE,MEMORY
        specs=[{'compute_percent':100,'memory_gib':8},{'compute_percent':50,'memory_gib':4}]
        req=task(self.a,self.image,gpu=2,gpu_requests=specs,status='running')
        reserve_volume(self.a,1,'home');reserve_volume(self.a,1,'work',req)
        ws=Workspace.objects.create(request=req,object_name='mixed',started_at=timezone.now()-timedelta(minutes=2),
            gpu_devices=['GPU-b','GPU-a'],gpu_allocations=[{'slot':0,'uuid':'GPU-b','index':1,'sm_count':114},
                                                        {'slot':1,'uuid':'GPU-a','index':0,'sm_count':56}])
        _,pod,_=task_objects(req,ws)
        self.assertEqual(pod['spec']['containers'][0]['resources']['requests'][COMPUTE],'150')
        self.assertEqual(pod['spec']['containers'][0]['resources']['requests'][MEMORY],str(12*1024))
        self.assertEqual(json.loads(pod['metadata']['annotations']['gpu.platform/request']),specs)
        blocks={b['row']:b for b in allocation_timeline(self.a)['blocks']}
        self.assertEqual((blocks['GPU-b']['compute_percent'],blocks['GPU-b']['memory_gib']),(100,8))
        self.assertEqual((blocks['GPU-a']['compute_percent'],blocks['GPU-a']['memory_gib']),(50,4))

    def test_gpu_inventory_and_schema_cover_shared_multi_card_operations(self):
        SystemObservation.objects.create(name='gpu_inventory',data={'devices':[{'uuid':'GPU-a','index':0,'name':'4090 D','memory_mib':24564,'usable_memory_mib':24052,'sm_count':114,'used_compute_percent':30,'used_memory_mib':6144,'used_sm_count':34,'ready':True}]})
        inventory=self.call('get','gpus').json()['data']['devices']
        self.assertEqual(inventory[0]['available_compute_percent'],70)
        self.assertEqual(self.call('get','resources').json()['data']['gpus'],inventory)
        schema=self.call('get','openapi.json').json()
        self.assertIn('/gpus',schema['paths'])
        self.assertIn('gpu_requests',schema['components']['schemas']['TaskRequest']['properties'])
        self.assertIn('gpu_memory_gib',schema['components']['schemas']['GroupRequest']['properties'])
        self.assertEqual(schema['paths']['/gpus']['get']['responses']['200']['content']['application/json']['schema']['$ref'],'#/components/schemas/GPUInventoryResponse')
        self.assertIn('gpu_allocations',schema['components']['schemas']['TaskGPUResources']['properties'])

    def test_password_token_issue_and_individual_revocation(self):
        response = self.call('post', 'auth/token', {'username': self.a.username, 'password': 'test-password-2938'})
        self.assertEqual(response.status_code, 201, response.content)
        credential = response.json()['data']
        self.assertEqual(credential['expires_in'], 604800)
        token = credential['token']
        self.assertTrue(token.startswith(TOKEN_PREFIX))
        header = {'HTTP_AUTHORIZATION': 'Bearer '+token}
        self.assertEqual(self.call('get', 'me', **header).status_code, 200)
        self.assertEqual(self.call('delete', 'auth/token', **header).status_code, 200)
        self.assertEqual(self.call('get', 'me', **header).status_code, 401)
        self.assertEqual(self.call('get', 'me').status_code, 200)
        self.assertEqual(self.call('post', 'auth/token', {'username': self.a.username, 'password': 'wrong'}).status_code, 401)

    def test_tokens_cannot_replace_browser_sessions_or_other_way_round(self):
        browser = Client()
        browser.cookies['sessionid'] = self.token.removeprefix(TOKEN_PREFIX)
        self.assertEqual(browser.get('/tasks/').status_code, 302)
        self.client.defaults.pop('HTTP_AUTHORIZATION')
        self.client.force_login(self.a)
        self.assertEqual(self.call('get', 'me').status_code, 401)

    def test_configurable_token_expiry_and_permanent_revocation(self):
        credentials = {'username': self.a.username, 'password': 'test-password-2938'}
        issued = self.call('post', 'auth/token', {**credentials, 'expires_in': 3600})
        self.assertEqual(issued.status_code, 201, issued.content)
        credential = issued.json()['data']
        self.assertEqual(credential['expires_in'], 3600)
        self.assertIsNotNone(credential['expires_at'])
        header = {'HTTP_AUTHORIZATION': 'Bearer '+credential['token']}
        with patch('django.utils.timezone.now', return_value=timezone.now()+timedelta(hours=2)):
            self.assertEqual(self.call('get', 'me', **header).status_code, 401)
        issued = self.call('post', 'auth/token', {**credentials, 'expires_in': 0})
        self.assertEqual(issued.status_code, 201, issued.content)
        credential = issued.json()['data']
        self.assertEqual(credential['expires_in'], 0)
        self.assertIsNone(credential['expires_at'])
        header = {'HTTP_AUTHORIZATION': 'Bearer '+credential['token']}
        with patch('django.utils.timezone.now', return_value=timezone.now()+timedelta(days=3650)):
            self.assertEqual(self.call('get', 'me', **header).status_code, 200)
        self.assertEqual(self.call('delete', 'auth/token', **header).status_code, 200)
        self.assertEqual(self.call('get', 'me', **header).status_code, 401)

    def test_invalid_lifetimes_do_not_issue_tokens(self):
        credentials = {'username': self.a.username, 'password': 'test-password-2938'}
        before = Session.objects.count()
        for value in [-1, True, 1.5, '0', None, 3153600001]:
            with self.subTest(value=value):
                response = self.call('post', 'auth/token', {**credentials, 'expires_in': value})
                self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(Session.objects.count(), before)

    def test_token_expiry_disabled_user_and_password_change(self):
        row = Session.objects.get(session_key=self.token.removeprefix(TOKEN_PREFIX))
        row.expire_date = timezone.now()
        row.save(update_fields=['expire_date'])
        self.assertEqual(self.call('get', 'me').status_code, 401)
        self.client.defaults['HTTP_AUTHORIZATION'] = 'Bearer '+issue_token(self.a)['token']
        self.a.is_active = False
        self.a.save(update_fields=['is_active'])
        self.assertEqual(self.call('get', 'me').status_code, 401)
        self.a.is_active = True
        self.a.save(update_fields=['is_active'])
        self.client.defaults['HTTP_AUTHORIZATION'] = 'Bearer '+issue_token(self.a)['token']
        self.a.set_password('changed-password-7829')
        self.a.save(update_fields=['password'])
        self.assertEqual(self.call('get', 'me').status_code, 401)

    def test_self_password_endpoint_invalidates_old_token(self):
        response = self.call('post', 'me/password', {'old_password': 'test-password-2938',
            'new_password1': 'new-safe-password-970318', 'new_password2': 'new-safe-password-970318'})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(response.json()['data']['reauthentication_required'])
        self.assertEqual(self.call('get', 'me').status_code, 401)

    def test_me_resources_and_public_discovery(self):
        data = self.call('get', 'me').json()['data']
        self.assertEqual(data['username'], self.a.username)
        self.assertNotIn('password', data)
        self.assertNotIn('registry_secret', data)
        self.assertEqual(data['usage']['gpu'], 0)
        response = self.call('get', 'resources')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['data']['timezone'], 'Asia/Shanghai')
        self.assertEqual(response.json()['data']['default_image_id'], str(self.image.pk))
        self.client.defaults.pop('HTTP_AUTHORIZATION')
        self.assertEqual(self.call('get', '').status_code, 200)

    def test_unknown_methods_fields_and_json_are_errors(self):
        self.assertEqual(self.call('post', 'me', {}).status_code, 405)
        self.assertEqual(self.call('post', 'tasks', {'purpose': 'bad', 'status': 'running'}).status_code, 400)
        self.assertEqual(self.call('post', 'tasks', {'purpose': 'bad', 'mode': []}).status_code, 400)
        self.assertEqual(self.client.post('/api/v1/tasks', data='{', content_type='application/json').status_code, 400)
        self.assertEqual(self.client.post('/api/v1/tasks', data='x', content_type='text/plain').status_code, 415)
        response = self.call('get', 'unknown')
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()['error']['code'], 'not_found')

    def test_development_defaults_and_group_validation(self):
        result = self.create_task()
        self.assertEqual((result['mode'], result['gpu'], result['cpu'], result['memory_gib']), ('interactive', 0, 2, 4))
        self.assertEqual(result['status'], 'submitted')
        self.assertIsNone(result['connection'])
        self.assertEqual(self.call('post', 'tasks', {'purpose': 'too-large', 'gpu': 999}).status_code, 400)
        SSHKey.objects.filter(user=self.a).update(enabled=False)
        self.assertEqual(self.call('post', 'tasks', {'purpose': 'missing-key'}).status_code, 400)

    def test_post_idempotency_replays_and_rejects_changed_parameters(self):
        payload = {'purpose': 'one-resource'}
        headers = {'HTTP_IDEMPOTENCY_KEY': 'same-resource'}
        first = self.call('post', 'tasks', payload, **headers)
        second = self.call('post', 'tasks', payload, **headers)
        self.assertEqual(first.status_code, 201, first.content)
        self.assertEqual(first.json(), second.json())
        self.assertEqual(second['Idempotency-Replayed'], 'true')
        self.assertEqual(ResourceRequest.objects.count(), 1)
        changed = self.call('post', 'tasks', {'purpose': 'other'}, **headers)
        self.assertEqual(changed.status_code, 409)
        self.assertEqual(ResourceRequest.objects.count(), 1)
        self.client.defaults['HTTP_AUTHORIZATION'] = 'Bearer '+issue_token(self.b)['token']
        self.assertEqual(self.call('post', 'tasks', payload, **headers).status_code, 201)
        self.assertEqual(ResourceRequest.objects.count(), 2)

    def test_task_visibility_scope_and_slim_lists(self):
        mine = task(self.a, self.image, command='private command', environment={'SECRET': 'mine'})
        other = task(self.b, self.image)
        result = self.call('get', 'tasks').json()['data']['items']
        self.assertEqual([row['id'] for row in result], [str(mine.pk)])
        self.assertNotIn('command', result[0])
        self.assertEqual(self.call('get', f'tasks/{mine.pk}').json()['data']['environment'], {'SECRET': 'mine'})
        for suffix in ('', '/connection', '/logs'):
            self.assertEqual(self.call('get', f'tasks/{other.pk}'+suffix).status_code, 404)
        self.assertEqual(self.call('post', f'tasks/{other.pk}/stop', {}).status_code, 404)
        self.assertEqual(self.call('get', 'tasks?scope=all').status_code, 403)
        self.staff()
        self.assertEqual(self.call('get', 'tasks?scope=all').json()['data']['count'], 2)

    def test_cancel_and_admin_approval_with_connection_config(self):
        result = self.create_task()
        pk = result['id']
        self.assertEqual(self.call('post', f'tasks/{pk}/review', {'action': 'approve'}).status_code, 403)
        self.staff()
        response = self.call('post', f'tasks/{pk}/review', {'action': 'approve'})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['data']['status'], 'approved')
        ResourceRequest.objects.filter(pk=pk).update(status='running')
        Workspace.objects.filter(request_id=pk).update(ssh_port=30047)
        connection = self.call('get', f'tasks/{pk}/connection').json()['data']['connection']
        self.assertIn('HostKeyAlias task-'+pk, connection['config'])
        self.assertEqual(connection['user'], self.a.username)
        response = self.call('post', f'tasks/{pk}/stop', {})
        self.assertEqual(response.status_code, 202, response.content)
        self.assertTrue(response.json()['data']['stop_requested'])

    def test_submitted_request_can_be_canceled(self):
        pk = self.create_task()['id']
        response = self.call('post', f'tasks/{pk}/stop', {})
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()['data']['status'], 'canceled')

    def test_program_command_environment_and_source_handoff(self):
        result = self.create_task(mode='batch', command='python /workspace/main.py', environment={'OMP_NUM_THREADS': '2'})
        req = ResourceRequest.objects.get(pk=result['id'])
        self.assertIsNone(req.ends_at)
        self.assertEqual(req.environment, {'OMP_NUM_THREADS': '2'})
        self.assertEqual(req.argv, ['/bin/bash', '-c', 'python /workspace/main.py'])
        self.assertEqual(self.call('post', 'tasks', {'purpose': 'bad', 'mode': 'batch', 'command': 'true', 'environment': {'HOME': '/tmp'}}).status_code, 400)
        source = task(self.a, self.image, gpu=0)
        approve(source.pk, self.admin)
        source.status = 'running'
        source.save(update_fields=['status'])
        target = self.create_task(mode='batch', source_environment=str(source.pk), command='true')
        self.assertEqual(target['source_environment_id'], str(source.pk))

    def test_save_operation_and_build_history(self):
        source = task(self.a, self.image, gpu=0, status='running')
        saved = self.call('post', f'tasks/{source.pk}/save', {})
        self.assertEqual(saved.status_code, 202, saved.content)
        snapshot = saved.json()['data']
        self.assertEqual(self.call('get', f'snapshots/{snapshot["id"]}').status_code, 200)
        self.assertEqual(self.call('get', 'snapshots').json()['data']['count'], 1)
        again = self.call('post', f'tasks/{source.pk}/save', {})
        self.assertEqual(again.json()['data']['id'], snapshot['id'])
        built = self.call('post', 'builds', {'name': 'api-build', 'dockerfile': 'FROM ubuntu:22.04'})
        self.assertEqual(built.status_code, 201, built.content)
        self.assertEqual(self.call('post', 'builds', {'name': 'second', 'dockerfile': 'FROM ubuntu'}).status_code, 409)
        obj = ImageBuild.objects.get(pk=built.json()['data']['id'])
        obj.log = 'build output'
        obj.save(update_fields=['log'])
        self.assertNotIn('log', self.call('get', 'builds').json()['data']['items'][0])
        self.assertEqual(self.call('get', f'builds/{obj.pk}').json()['data']['log'], 'build output')

    def test_key_registration_disable_and_restore(self):
        public = SSHKey.objects.filter(user=self.a).first().public_key
        payload = {'name': 'agent-key', 'public_key': public}
        first = self.call('post', 'keys', payload)
        self.assertEqual(first.status_code, 201, first.content)
        self.assertEqual(self.call('post', 'keys', payload).status_code, 409)
        key_id = first.json()['data']['id']
        self.assertEqual(self.call('delete', f'keys/{key_id}').status_code, 200)
        restored = self.call('post', 'keys', payload)
        self.assertEqual(restored.json()['data']['id'], key_id)
        other = SSHKey.objects.filter(user=self.b).first()
        self.assertEqual(self.call('delete', f'keys/{other.pk}').status_code, 404)
        self.assertEqual(self.call('patch', f'keys/{key_id}', {'enabled': 'yes'}).status_code, 400)

    def test_private_images_and_recycling(self):
        image = ImageArtifact.objects.create(owner=self.a, name='saved', reference='private:tag', verified=True, temporary=True)
        other = ImageArtifact.objects.create(owner=self.b, name='secret', reference='private:other', verified=True)
        self.assertEqual(self.call('get', f'images/{other.pk}').status_code, 404)
        self.assertEqual(self.call('post', f'images/{image.pk}/keep', {}).status_code, 200)
        image.refresh_from_db()
        self.assertFalse(image.temporary)
        self.assertEqual(self.call('delete', f'images/{image.pk}').status_code, 202)
        image.refresh_from_db()
        self.assertIsNotNone(image.recycle_after)
        self.assertEqual(self.call('delete', f'images/{self.image.pk}').status_code, 404)

    def test_admin_endpoints_require_staff_and_can_create_edit_groups_members(self):
        for path in ['admin/overview', 'admin/members', 'admin/groups', 'admin/storage', 'admin/images', 'admin/audits', 'admin/settings']:
            self.assertEqual(self.call('get', path).status_code, 403)
        self.staff()
        created = self.call('post', 'admin/groups', {'name': 'api-group', 'cpu': 12})
        self.assertEqual(created.status_code, 201, created.content)
        group_id = created.json()['data']['id']
        created = self.call('post', 'admin/members', {'username': 'api-member', 'password': 'new-member-password-682714', 'group': group_id})
        self.assertEqual(created.status_code, 201, created.content)
        member = created.json()['data']
        self.assertTrue(member['is_active'])
        response = self.call('patch', f'admin/members/{member["id"]}', {'first_name': 'API member'})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['data']['first_name'], 'API member')
        default = UserGroup.objects.get(is_default=True)
        deleted = self.call('delete', f'admin/groups/{group_id}', {'destination': default.pk})
        self.assertEqual(deleted.status_code, 200, deleted.content)
        self.assertEqual(get_user_model().objects.get(pk=member['id']).profile.group_id, default.pk)

    def test_admin_protection_quota_settings_and_audits(self):
        self.staff()
        self.assertEqual(self.call('patch', f'admin/members/{self.admin.pk}', {'is_active': False}).status_code, 400)
        response = self.call('patch', 'admin/settings', {'workspace_retention_days': 5})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(PlatformSettings.load().workspace_retention_days, 5)
        self.assertEqual(self.call('patch', 'admin/settings', {'workspace_retention_days': 0}).status_code, 400)
        self.assertEqual(self.call('patch', 'admin/settings', {'public_host': 'other'}).status_code, 400)
        self.assertGreater(self.call('get', 'admin/audits').json()['data']['count'], 0)

    def test_idle_volume_deletion_and_ownership(self):
        req = task(self.a, self.image)
        approve(req.pk, self.admin)
        volume = VolumeAllocation.objects.get(request=req)
        self.assertEqual(self.call('delete', f'volumes/{volume.pk}').status_code, 403)
        self.staff()
        self.assertEqual(self.call('delete', f'volumes/{volume.pk}').status_code, 400)
        req.status = 'canceled'
        req.save(update_fields=['status'])
        Workspace.objects.filter(request=req).update(cleanup_complete=True)
        response = self.call('delete', f'volumes/{volume.pk}')
        self.assertEqual(response.status_code, 202, response.content)
        self.assertEqual(response.json()['data']['state'], 'deleting')
        self.assertEqual(self.call('get', 'admin/storage').status_code, 200)

    def test_dataset_browse_download_and_traversal(self):
        with tempfile.TemporaryDirectory() as directory, override_settings(PLATFORM_DATASET_ROOT=directory):
            root = Path(directory)
            (root/'sample.txt').write_text('sample')
            (root/'escape').symlink_to('/etc/passwd')
            rows = self.call('get', 'datasets').json()['data']['items']
            self.assertIn('sample.txt', [row['name'] for row in rows])
            response = self.call('get', 'datasets/download?path=sample.txt')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(b''.join(response.streaming_content), b'sample')
            self.assertEqual(self.call('get', 'datasets/download?path=escape').status_code, 404)
            self.assertEqual(self.call('get', 'datasets?path=..').status_code, 404)
            self.assertEqual(self.call('get', 'datasets?path=%00').status_code, 404)

    def test_pagination_schema_and_skill_archive(self):
        self.assertEqual(self.call('get', 'tasks?page_size=101').status_code, 400)
        self.assertEqual(self.call('get', 'tasks?page=0').status_code, 400)
        self.assertEqual(self.call('get', 'tasks?page=99').json()['data']['items'], [])
        schema = self.call('get', 'openapi.json').json()
        self.assertEqual(schema['openapi'], '3.0.3')
        self.assertIn('/tasks/{pk}/review', schema['paths'])
        self.assertEqual(schema['paths']['/auth/token']['post']['security'], [])
        response = self.call('get', 'skill.zip')
        self.assertEqual(response.status_code, 200)
        archive = zipfile.ZipFile(io.BytesIO(b''.join(response.streaming_content)))
        self.assertIn('gpu-platform/SKILL.md', archive.namelist())
        self.assertIn('gpu-platform/scripts/client.py', archive.namelist())
        self.assertFalse(any('..' in path or path.endswith('.pyc') for path in archive.namelist()))

    def test_api_access_page_issues_cookie_separate_token(self):
        self.client.force_login(self.a)
        self.assertEqual(self.client.get('/api/').status_code, 200)
        response = self.client.post('/api/', {'name': 'laptop'}, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.redirect_chain, [('/api/', 302)])
        self.assertEqual(response.context['tokens'][0]['name'], 'laptop')
        self.assertEqual(response['Cache-Control'], 'no-store')
        count = Session.objects.count()
        refreshed = self.client.get('/api/')
        self.assertEqual(Session.objects.count(), count)
        self.assertEqual(refreshed.context['tokens'], response.context['tokens'])

    def test_api_access_page_can_issue_permanent_and_fixed_lifetimes(self):
        self.client.force_login(self.a)
        for seconds in [0, 2592000]:
            response = self.client.post('/api/', {'expires_in': seconds}, follow=True)
            credential = response.context['tokens'][0]
            self.assertIsNotNone(credential['created_at'])
            self.assertEqual(credential['expires_at'] is None, seconds == 0)
        before = Session.objects.count()
        response = self.client.post('/api/', {'expires_in': -1})
        self.assertFalse(response.context['form'].is_valid())
        self.assertEqual(Session.objects.count(), before)

    def test_token_api_lists_only_own_metadata_and_revokes_selected_token(self):
        other = issue_token(self.b, 0, 'other account')
        second = issue_token(self.a, 0, 'agent')
        self.client.force_login(self.a)
        rows = self.call('get', 'auth/tokens').json()['data']['items']
        self.assertEqual({row['id'] for row in rows}, {second['id'], token_id(self.token[4:])})
        current = next(row for row in rows if row['current'])
        self.assertEqual(current['id'], token_id(self.token[4:]))
        serialized = json.dumps(rows)
        for secret in [self.token, second['token'], other['token'], self.a.get_session_auth_hash()]:
            self.assertNotIn(secret, serialized)
        self.assertTrue(all('token' not in row for row in rows))
        self.assertEqual(self.call('delete', f'auth/tokens/{other["id"]}').status_code, 404)
        self.assertEqual(self.call('delete', f'auth/tokens/{second["id"]}').status_code, 200)
        self.assertEqual(self.call('get', 'me', HTTP_AUTHORIZATION='Bearer '+second['token']).status_code, 401)
        self.assertEqual(self.call('get', 'me').status_code, 200)

    def test_web_delete_is_post_only_csrf_protected_and_owned(self):
        second = issue_token(self.a, 0, 'delete me')
        other = issue_token(self.b)
        path = f'/api/tokens/{second["id"]}/delete/'
        protected = Client(enforce_csrf_checks=True)
        protected.force_login(self.a)
        self.assertEqual(protected.post(path).status_code, 403)
        self.client.force_login(self.a)
        self.assertEqual(self.client.get(path).status_code, 405)
        self.assertEqual(self.client.post(f'/api/tokens/{other["id"]}/delete/').status_code, 404)
        response = self.client.post(path, follow=True)
        self.assertNotContains(response, second['token'])
        self.assertContains(response, self.token)
        self.assertEqual(self.call('get', 'me', HTTP_AUTHORIZATION='Bearer '+second['token']).status_code, 401)

    def test_expired_credentials_can_be_listed_and_revoked(self):
        credential = issue_token(self.a)
        row = Session.objects.get(session_key=credential['token'][4:])
        row.expire_date = timezone.now()-timedelta(seconds=1)
        row.save(update_fields=['expire_date'])
        rows = self.call('get', 'auth/tokens').json()['data']['items']
        record = next(row for row in rows if row['id'] == credential['id'])
        self.assertEqual(record['status'], 'expired')
        self.assertEqual(self.call('delete', f'auth/tokens/{credential["id"]}').status_code, 200)

    def test_current_token_can_be_revoked_by_its_public_id(self):
        response = self.call('delete', 'auth/tokens/'+token_id(self.token[4:]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.call('get', 'me').status_code, 401)
