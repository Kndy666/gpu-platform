import json
from datetime import timedelta
from django.test import Client,TestCase
from django.utils import timezone
from portal.api_auth import issue_token
from portal.models import ResourceRequest,Workspace,SystemObservation,EnvironmentSnapshot
from portal.services import approve,work_volume
from tests.factories import fixtures,task


class EnvironmentRequestTests(TestCase):
    @classmethod
    def setUpTestData(cls):cls.admin,cls.user,cls.other,cls.image=fixtures()

    def setUp(self):
        self.client.force_login(self.user)
        self.api=Client(HTTP_AUTHORIZATION='Bearer '+issue_token(self.user)['token'])

    def source(self,owner=None,gpu=1):
        source=task(owner or self.user,self.image,gpu=gpu,workspace_gib=100)
        approve(source.pk,self.admin)
        source.status='running';source.save(update_fields=['status']);return source

    def test_gpu_environment_has_save_and_run_buttons_and_accepts_both_endpoints(self):
        source=self.source()
        response=self.client.get(f'/tasks/{source.pk}/')
        self.assertContains(response,'保存镜像')
        self.assertContains(response,f'/training/new/?source={source.pk}')
        self.assertEqual(self.client.post(f'/tasks/{source.pk}/save/').status_code,302)
        second=self.source()
        response=self.api.post(f'/api/v1/tasks/{second.pk}/save',data='{}',content_type='application/json')
        self.assertEqual(response.status_code,202,response.content)
        self.assertEqual(EnvironmentSnapshot.objects.count(),2)
        self.client.force_login(self.other)
        self.assertEqual(self.client.post(f'/tasks/{source.pk}/save/').status_code,404)

    def test_gpu_source_and_requested_workspace_size_are_preserved(self):
        source=self.source()
        response=self.client.get('/training/new/',{'source':source.pk})
        self.assertIn(source,response.context['form'].fields['source_environment'].queryset)
        payload={'purpose':'GPU handoff','mode':'batch','source_environment':str(source.pk),'command':'true','workspace_gib':40}
        response=self.api.post('/api/v1/tasks',data=json.dumps(payload),content_type='application/json')
        self.assertEqual(response.status_code,201,response.content)
        target=ResourceRequest.objects.get(pk=response.json()['data']['id'])
        self.assertEqual(target.workspace_gib,40)
        approve(target.pk,self.admin)
        self.assertNotEqual(work_volume(target).pk,work_volume(source).pk)

    def test_omitted_size_defaults_to_source_and_invalid_source_is_rejected(self):
        source=self.source()
        payload={'purpose':'source defaults','mode':'batch','source_environment':str(source.pk),'command':'true'}
        response=self.api.post('/api/v1/tasks',data=json.dumps(payload),content_type='application/json')
        self.assertEqual(response.status_code,201,response.content)
        self.assertEqual(response.json()['data']['workspace_gib'],100)
        other=self.source(self.other)
        response=self.api.post('/api/v1/tasks',data=json.dumps({**payload,'source_environment':str(other.pk)}),content_type='application/json')
        self.assertEqual(response.status_code,400)

    def test_resource_capacity_matches_browser_and_api_and_observations_expire(self):
        SystemObservation.objects.create(name='resources',data={'cpu':{'available':14,'total':16},'memory_gib':{'available':28,'total':32}})
        SystemObservation.objects.create(name='gpu_inventory',data={'devices':[{'uuid':'GPU-a','index':0,'sm_count':114,
            'memory_mib':24564,'usable_memory_mib':24052,'used_memory_mib':4096,'used_compute_percent':50,'ready':True}]})
        page=self.client.get('/environments/new/')
        self.assertContains(page,'当前余量')
        self.assertContains(page,'data-capacity-cpu>14')
        browser=self.client.get('/resources/available/').json()
        api=self.api.get('/api/v1/resources').json()['data']['request_capacity']
        self.assertEqual(browser,api)
        self.assertEqual(browser['gpus'][0]['compute_percent'],50)
        self.assertEqual(browser['gpus'][0]['memory_gib'],19.5)
        self.assertEqual(browser['workspace_gib']['available'],500)
        SystemObservation.objects.filter(name='resources').update(observed_at=timezone.now()-timedelta(minutes=3))
        stale=self.client.get('/resources/available/').json()
        self.assertFalse(stale['fresh']);self.assertIsNone(stale['cpu']);self.assertFalse(stale['gpus'][0]['ready'])

    def test_stopping_environment_cannot_be_selected_or_saved(self):
        source=self.source();Workspace.objects.filter(request=source).update(stop_requested=True)
        response=self.client.get('/training/new/')
        self.assertNotIn(source,response.context['form'].fields['source_environment'].queryset)
        self.assertEqual(self.client.post(f'/tasks/{source.pk}/save/').status_code,404)
