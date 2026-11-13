"""Exercise portal and API entry points against the same accounts and objects."""
from datetime import timedelta
import json
from pathlib import Path
import re
import tempfile
import uuid
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.utils import timezone
from portal.api_auth import issue_token
from portal.api_urls import ROUTES
from portal.models import (AuditLog, EnvironmentSnapshot, ImageArtifact, ImageBuild, ResourceRequest,
                           StoragePool, SystemObservation, UserGroup, VolumeAllocation, Workspace)
from portal.services import approve
from tests.factories import fixtures, task, set_member_settings


class ParityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin, cls.a, cls.b, cls.image = fixtures()

    def setUp(self):
        self.web = Client()
        self.api = Client()
        self.login(self.a)

    def login(self, user):
        self.web.force_login(user)
        self.api.defaults['HTTP_AUTHORIZATION'] = 'Bearer '+issue_token(user)['token']

    def call(self, method, path, data=None, **headers):
        return getattr(self.api, method)('/api/v1/'+path, data=json.dumps(data) if data is not None else '',
            content_type='application/json', **headers)

    def payload(self, **updates):
        return {'purpose': 'parity', 'mode': 'interactive', 'image': str(self.image.pk), 'source_kind': 'image',
            'gpu': 0, 'cpu': 2, 'memory_gib': 4, 'workspace_gib': 20, 'working_dir': '/workspace',
            'starts_at': timezone.now().isoformat(), 'ends_at': (timezone.now()+timedelta(hours=2)).isoformat(), **updates}

    def test_every_protected_operation_requires_bearer_and_every_admin_operation_requires_staff(self):
        anonymous = Client()
        checked = 0
        for route, view, _, _ in ROUTES:
            path = re.sub(r'<int:[^>]+>', '999999', route)
            path = re.sub(r'<uuid:[^>]+>', str(uuid.UUID(int=1)), path)
            path = re.sub(r'<str:[^>]+>', 'keep', path)
            for method in view.api_methods:
                if method in view.api_public:
                    continue
                with self.subTest(route=route, method=method):
                    response = getattr(anonymous, method.lower())('/api/v1/'+path, data='', content_type='application/json')
                    self.assertEqual(response.status_code, 401, response.content)
                    if view.api_staff:
                        response = self.call(method.lower(), path)
                        self.assertEqual(response.status_code, 403, response.content)
                    checked += 1
        self.assertGreater(checked, 40)

    def test_overview_and_timeline_use_the_same_global_counts_and_visibility(self):
        SystemObservation.objects.create(name='resources', data={'gpu': {'total': 4, 'available': 3}})
        mine = task(self.a, self.image, status='running')
        other = task(self.b, self.image, status='running', purpose='private-project')
        for req in [mine, other]:
            Workspace.objects.create(request=req, object_name='task-'+req.pk.hex, started_at=timezone.now()-timedelta(minutes=2))
        task(self.b, self.image, status='queued')
        html = self.web.get('/')
        data = self.call('get', 'resources').json()['data']
        for field in ['running_count', 'queued_count', 'resources', 'storage_tib']:
            self.assertEqual(data[field], html.context[field])
        web_timeline = self.web.get('/timeline/').json()
        api_timeline = self.call('get', 'timeline').json()['data']
        self.assertEqual(web_timeline['blocks'], api_timeline['blocks'])
        self.assertNotIn('private-project', json.dumps(api_timeline))
        SystemObservation.objects.filter(name='resources').update(observed_at=timezone.now()-timedelta(minutes=3))
        self.assertIsNone(self.web.get('/').context['resources'])
        self.assertIsNone(self.call('get', 'resources').json()['data']['resources'])

    def test_member_search_groups_storage_and_image_totals_match_management(self):
        self.a.first_name = '测试姓名'
        self.a.save(update_fields=['first_name'])
        req = task(self.a, self.image)
        approve(req.pk, self.admin)
        ImageArtifact.objects.create(owner=self.a, name='waiting', reference='private:waiting', size_bytes=123,
            recycle_after=timezone.now()+timedelta(hours=1), cleanup_error='retry')
        self.login(self.admin)
        html = self.web.get('/management/members/', {'q': ' 姓名 '})
        rows = self.call('get', 'admin/members?q=%20姓名%20').json()['data']['items']
        self.assertEqual([r['id'] for r in rows], [u.pk for u in html.context['page']])
        html = self.web.get('/management/groups/')
        rows = self.call('get', 'admin/groups').json()['data']['items']
        self.assertEqual({r['id']: r['member_count'] for r in rows}, {g.pk: g.member_count for g in html.context['groups']})
        html = self.web.get('/management/storage/')
        data = self.call('get', 'admin/storage').json()['data']
        self.assertEqual([r['id'] for r in data['pools']], [p.pk for p in html.context['pools']])
        for volume, row in zip(html.context['page'], data['volumes']['items']):
            self.assertEqual(row['pool_name'], volume.pool.name)
            self.assertEqual(row['username'], volume.user.username)
            self.assertEqual(row['busy'], volume.busy)
        html = self.web.get('/management/images/')
        data = self.call('get', 'admin/images').json()['data']
        self.assertEqual(data['summary'], {field: html.context[field] for field in ['ready_bytes', 'waiting_count', 'error_count']})

    def test_task_detail_includes_the_work_volume_and_ssh_config_shown_by_the_portal(self):
        req = task(self.a, self.image)
        approve(req.pk, self.admin)
        ResourceRequest.objects.filter(pk=req.pk).update(status='running')
        Workspace.objects.filter(request=req).update(ssh_port=30042)
        html = self.web.get(f'/tasks/{req.pk}/')
        data = self.call('get', f'tasks/{req.pk}').json()['data']
        self.assertEqual(data['image_name'], html.context['task'].image.name)
        self.assertEqual(data['volume']['id'], str(html.context['volume'].pk))
        self.assertEqual(data['volume']['pool_name'], html.context['volume'].pool.name)
        self.assertEqual(data['connection']['port'], 30042)
        self.assertIn('HostKeyAlias task-'+str(req.pk), data['connection']['config'])

    def test_task_submission_and_group_quota_match_both_entry_points(self):
        payload = self.payload()
        web = self.web.post('/environments/new/', payload)
        api = self.call('post', 'tasks', payload)
        self.assertEqual(web.status_code, 302)
        self.assertEqual(api.status_code, 201, api.content)
        rows = list(ResourceRequest.objects.order_by('created_at'))
        fields = ['gpu','cpu','memory_gib','workspace_gib','starts_at','ends_at','working_dir','image_id','login_name','status']
        self.assertEqual({k:getattr(rows[0], k) for k in fields}, {k:getattr(rows[1], k) for k in fields})
        group = self.a.profile.group
        set_member_settings(self.a,cpu=1)
        before = ResourceRequest.objects.count()
        self.assertEqual(self.web.post('/environments/new/', payload).status_code, 200)
        self.assertEqual(self.call('post', 'tasks', payload).status_code, 400)
        self.assertEqual(ResourceRequest.objects.count(), before)

    def test_program_from_development_and_save_have_the_same_owner_and_environment(self):
        sources = [task(self.a, self.image, gpu=0, status='running') for _ in range(2)]
        html = self.web.get('/training/new/', {'source': sources[0].pk})
        self.assertEqual(html.context['form'].initial['workspace_gib'], sources[0].workspace_gib)
        payload = self.payload(mode='batch', command='printf test', source_kind='environment', ends_at='', environment={'OMP_NUM_THREADS':'2'})
        web_payload = {**payload, 'source_environment': str(sources[0].pk), 'variables': 'OMP_NUM_THREADS=2'}
        web_payload.pop('environment')
        self.assertEqual(self.web.post('/training/new/', web_payload).status_code, 302)
        api_payload = {**payload, 'source_environment': str(sources[1].pk)}
        self.assertEqual(self.call('post', 'tasks', api_payload).status_code, 201)
        jobs = list(ResourceRequest.objects.filter(mode='batch').order_by('created_at'))
        for job, source in zip(jobs, sources):
            self.assertEqual(job.source_environment_id, source.pk)
            self.assertEqual(job.workspace_gib, payload['workspace_gib'])
            self.assertEqual(job.environment, {'OMP_NUM_THREADS':'2'})
            self.assertIsNone(job.ends_at)
        self.assertEqual(self.web.post(f'/tasks/{sources[0].pk}/save/').status_code, 302)
        self.assertEqual(self.call('post', f'tasks/{sources[1].pk}/save', {}).status_code, 202)
        self.assertEqual(EnvironmentSnapshot.objects.filter(status='pending').count(), 2)
        self.login(self.admin)
        for source in sources:
            self.assertEqual(self.web.post(f'/tasks/{source.pk}/save/').status_code, 404)
            self.assertEqual(self.call('post', f'tasks/{source.pk}/save', {}).status_code, 404)

    def test_approve_extend_stop_and_reject_produce_the_same_state(self):
        self.login(self.admin)
        requests = [task(self.a, self.image) for _ in range(2)]
        self.assertEqual(self.web.post(f'/tasks/{requests[0].pk}/review/', {'action':'approve','priority':'100'}).status_code, 302)
        self.assertEqual(self.call('post', f'tasks/{requests[1].pk}/review', {'action':'approve','priority':100}).status_code, 200)
        for req in requests:
            req.refresh_from_db();self.assertEqual((req.status,req.priority), ('approved',100))
        end = timezone.now()+timedelta(hours=3)
        self.web.post(f'/tasks/{requests[0].pk}/review/', {'action':'extend','ends_at':end.isoformat(),'reason':'needed'})
        self.assertEqual(self.call('post', f'tasks/{requests[1].pk}/review', {'action':'extend','ends_at':end.isoformat(),'reason':'needed'}).status_code, 200)
        for req in requests:
            req.refresh_from_db();self.assertEqual(req.ends_at,end)
        self.login(self.a)
        self.web.post(f'/tasks/{requests[0].pk}/stop/')
        self.assertEqual(self.call('post', f'tasks/{requests[1].pk}/stop', {}).status_code, 202)
        self.assertTrue(all(Workspace.objects.get(request=req).stop_requested for req in requests))
        requests = [task(self.a,self.image) for _ in range(2)]
        self.login(self.admin)
        self.web.post(f'/tasks/{requests[0].pk}/review/', {'action':'reject','reason':'no capacity'})
        self.assertEqual(self.call('post', f'tasks/{requests[1].pk}/review', {'action':'reject','reason':'no capacity'}).status_code, 200)
        self.assertEqual(ResourceRequest.objects.filter(pk__in=[r.pk for r in requests],status='rejected').count(),2)

    def test_admin_image_actions_aliases_and_regular_user_isolation(self):
        images = [ImageArtifact.objects.create(owner=self.b,name=str(i),reference=f'private:{i}',verified=True,temporary=True) for i in range(2)]
        for image in images:
            self.assertEqual(self.call('get', f'images/{image.pk}').status_code,404)
            self.assertEqual(self.web.post(f'/images/{image.pk}/recycle/').status_code,404)
        self.login(self.admin)
        for image in images:
            self.assertEqual(self.call('get', f'images/{image.pk}').status_code,200)
        self.assertEqual(self.web.post(f'/images/{images[0].pk}/recycle/').status_code,302)
        self.assertEqual(self.call('delete', f'images/{images[1].pk}').status_code,202)
        for image in images:
            image.refresh_from_db();self.assertIsNotNone(image.recycle_after)
        self.web.post(f'/images/{images[0].pk}/keep/')
        self.assertEqual(self.call('post', f'images/{images[1].pk}/keep',{}).status_code,200)
        for image in images:
            image.refresh_from_db();self.assertFalse(image.temporary);self.assertIsNone(image.recycle_after)

    def test_token_creation_reveal_and_revocation_match_own_web_list(self):
        issued = self.call('post','auth/tokens',{'name':'agent','expires_in':0})
        self.assertEqual(issued.status_code,201,issued.content)
        credential=issued.json()['data']
        self.assertContains(self.web.get('/api/'),credential['token'])
        self.assertEqual(self.call('get','auth/tokens/'+credential['id']).json()['data']['token'],credential['token'])
        self.login(self.b)
        self.assertEqual(self.call('get','auth/tokens/'+credential['id']).status_code,404)
        self.login(self.admin)
        self.assertEqual(self.call('get','auth/tokens/'+credential['id']).status_code,404)
        self.login(self.a)
        self.assertEqual(self.call('delete','auth/tokens/'+credential['id']).status_code,200)
        self.assertNotContains(self.web.get('/api/'),credential['token'])

    def test_staff_cannot_read_other_users_private_builds_keys_tokens_or_snapshot_history(self):
        req=task(self.a,self.image,gpu=0,status='running')
        snapshot=EnvironmentSnapshot.objects.create(source=req)
        build=ImageBuild.objects.create(user=self.a,name='private',dockerfile='FROM private')
        token=issue_token(self.a)
        key=self.a.sshkey_set.first()
        self.login(self.admin)
        for path in [f'builds/{build.pk}',f'snapshots/{snapshot.pk}',f'auth/tokens/{token["id"]}']:
            self.assertEqual(self.call('get',path).status_code,404)
        self.assertEqual(self.call('delete',f'keys/{key.pk}').status_code,404)
        self.assertEqual(self.call('get','builds').json()['data']['count'],0)

    def test_role_demotion_is_rechecked_before_idempotent_response_replay(self):
        req=task(self.a,self.image)
        image=ImageArtifact.objects.create(owner=self.a,name='private',reference='private:test',verified=True,temporary=True)
        staff=get_user_model().objects.create_user('operator',is_staff=True)
        self.login(staff)
        stop_headers={'HTTP_IDEMPOTENCY_KEY':'stop-one'}
        image_headers={'HTTP_IDEMPOTENCY_KEY':'keep-one'}
        self.assertEqual(self.call('post',f'tasks/{req.pk}/stop',{},**stop_headers).status_code,202)
        self.assertEqual(self.call('post',f'images/{image.pk}/keep',{},**image_headers).status_code,200)
        get_user_model().objects.filter(pk=staff.pk).update(is_staff=False)
        self.assertFalse(self.call('get','me').json()['data']['permissions']['manage_tasks'])
        self.assertEqual(self.call('post',f'tasks/{req.pk}/stop',{},**stop_headers).status_code,404)
        self.assertEqual(self.call('post',f'images/{image.pk}/keep',{},**image_headers).status_code,404)
        self.assertEqual(self.call('get','admin/members').status_code,403)
        get_user_model().objects.filter(pk=staff.pk).update(is_staff=True)
        self.assertTrue(self.call('get','me').json()['data']['permissions']['manage_tasks'])
        self.assertEqual(self.call('get','admin/members').status_code,200)

    def test_unlimited_group_does_not_grant_admin_and_disabled_account_cannot_use_api(self):
        group=self.a.profile.group;group.unlimited=True;group.save(update_fields=['unlimited'])
        data=self.call('get','me').json()['data']
        self.assertTrue(data['group']['unlimited'])
        self.assertFalse(any(data['permissions'].values()))
        self.assertEqual(self.call('post','admin/groups',{'name':'escalation'}).status_code,403)
        self.assertEqual(self.call('post','tasks',{**self.payload(),'user':self.b.pk}).status_code,400)
        get_user_model().objects.filter(pk=self.a.pk).update(is_active=False)
        self.assertEqual(self.call('get','resources').status_code,401)

    def test_staff_cannot_promote_users_or_modify_another_admin(self):
        staff=get_user_model().objects.create_user('operator',is_staff=True)
        self.login(staff)
        response=self.call('patch',f'admin/members/{self.a.pk}',{'is_staff':True})
        self.assertEqual(response.status_code,200,response.content)
        self.a.refresh_from_db();self.assertFalse(self.a.is_staff)
        self.assertEqual(self.call('patch',f'admin/members/{self.admin.pk}',{'first_name':'changed'}).status_code,403)
        self.assertEqual(self.web.get(f'/management/members/{self.admin.pk}/').status_code,403)

    def test_retention_settings_audit_labels_and_dataset_search_match(self):
        self.login(self.admin)
        self.web.post('/management/settings/',{'workspace_retention_days':5,'snapshot_hours':24,'failed_snapshot_hours':72})
        api=self.call('get','admin/settings').json()['data']
        self.assertEqual(api['workspace_retention_days'],5)
        self.assertEqual(self.call('patch','admin/settings',{'workspace_retention_days':7}).status_code,200)
        self.assertEqual(self.web.get('/management/settings/').context['form'].instance.workspace_retention_days,7)
        html=self.web.get('/management/audits/')
        rows=self.call('get','admin/audits').json()['data']['items']
        self.assertEqual([r['label'] for r in rows],[r.label for r in html.context['page']])
        with tempfile.TemporaryDirectory() as directory,override_settings(PLATFORM_DATASET_ROOT=directory):
            Path(directory,'sample.txt').write_text('same content')
            Path(directory,'other.txt').write_text('other')
            html=self.web.get('/datasets/',{'q':' sample '})
            rows=self.call('get','datasets?q=%20sample%20').json()['data']['items']
            self.assertEqual([r['name'] for r in rows],[r['name'] for r in html.context['page']])
            response=self.call('get','datasets?path=sample.txt')
            self.assertEqual(b''.join(response.streaming_content),b'same content')

    def test_image_sections_and_private_build_history_match_the_portal(self):
        ImageArtifact.objects.create(owner=self.a,name='permanent',reference='private:p',verified=True)
        ImageArtifact.objects.create(owner=self.a,name='temporary',reference='private:t',verified=True,temporary=True)
        ImageArtifact.objects.create(owner=self.b,name='other',reference='private:o',verified=True,temporary=True)
        html=self.web.get('/images/')
        for kind,field in [('permanent','images'),('temporary','temporary_images')]:
            rows=self.call('get','images?kind='+kind).json()['data']['items']
            self.assertEqual([row['id'] for row in rows],[str(image.pk) for image in html.context[field]])
        dockerfile='FROM ubuntu:22.04\nRUN true'
        self.assertEqual(self.web.post('/images/',{'name':'web-build','dockerfile':dockerfile}).status_code,302)
        first=ImageBuild.objects.get(name='web-build')
        self.assertEqual(self.call('post','builds',{'name':'blocked','dockerfile':dockerfile}).status_code,409)
        first.status='succeeded';first.save(update_fields=['status'])
        self.assertEqual(self.call('post','builds',{'name':'api-build','dockerfile':dockerfile}).status_code,201)
        html=self.web.get('/images/')
        rows=self.call('get','builds').json()['data']['items']
        self.assertEqual([row['id'] for row in rows],[str(build.pk) for build in html.context['builds']])

    def test_idle_volume_delete_matches_web_and_cannot_replay_after_demotion(self):
        req=task(self.a,self.image);approve(req.pk,self.admin)
        req.status='canceled';req.save(update_fields=['status'])
        Workspace.objects.filter(request=req).update(cleanup_complete=True)
        work=VolumeAllocation.objects.get(request=req)
        home=VolumeAllocation.objects.get(user=self.a,kind='home')
        staff=get_user_model().objects.create_user('operator',is_staff=True)
        self.login(staff)
        self.assertEqual(self.web.post(f'/management/storage/{home.pk}/delete/').status_code,302)
        headers={'HTTP_IDEMPOTENCY_KEY':'delete-volume'}
        self.assertEqual(self.call('delete',f'volumes/{work.pk}',**headers).status_code,202)
        home.refresh_from_db();work.refresh_from_db()
        self.assertEqual(home.state,work.state)
        self.assertEqual(work.state,'deleting')
        get_user_model().objects.filter(pk=staff.pk).update(is_staff=False)
        self.assertEqual(self.call('delete',f'volumes/{work.pk}',**headers).status_code,403)
