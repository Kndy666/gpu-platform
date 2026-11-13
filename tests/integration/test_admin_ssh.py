import base64
from copy import deepcopy
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding,PublicFormat
from django.test import Client,TestCase
from portal.api_auth import issue_token
from portal.kube import KubeClient
from portal.models import AuditLog,SSHKey,Workspace
from portal.reconcile import Reconciler
from portal.services import approve
from portal.ssh_access import authorized_keys,sync_authorized_keys
from tests.factories import fixtures,task
from tests.integration.test_reconcile import FakeKube


class PatchKube(FakeKube):
    path=staticmethod(KubeClient.path)
    def __init__(self):super().__init__();self.patches=[]
    def call(self,method,path,body):
        assert method=='PATCH' and set(body)=={'stringData'}
        self.patches.append(deepcopy(body))
        obj=self.objects['secrets',path.rsplit('/',1)[-1]]
        if 'data' in obj:
            obj['data'].update({k:base64.b64encode(v.encode()).decode() for k,v in body['stringData'].items()})
        else:obj['stringData'].update(body['stringData'])
        return deepcopy(obj)


class AdministratorSSHTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin,cls.a,cls.b,cls.image=fixtures()
        key=Ed25519PrivateKey.generate().public_key().public_bytes(Encoding.OpenSSH,PublicFormat.OpenSSH).decode()
        cls.admin_key=SSHKey.objects.create(user=cls.admin,name='管理密钥',public_key=key,fingerprint='admin-fingerprint')

    def test_only_active_owner_and_administrator_keys_are_authorized(self):
        owner=self.a.sshkey_set.get().public_key;other=self.b.sshkey_set.get().public_key
        self.assertEqual(set(authorized_keys(self.a.pk).splitlines()),{owner,self.admin_key.public_key})
        self.assertNotIn(other,authorized_keys(self.a.pk))
        for field in ('enabled','is_active','is_staff'):
            obj=self.admin_key if field=='enabled' else self.admin
            setattr(obj,field,False);obj.save(update_fields=[field])
            self.assertNotIn(self.admin_key.public_key,authorized_keys(self.a.pk))
            setattr(obj,field,True);obj.save(update_fields=[field])
        self.a.is_active=False;self.a.save(update_fields=['is_active'])
        self.assertNotIn(owner,authorized_keys(self.a.pk))

    def test_duplicate_keys_are_deduplicated_and_group_name_does_not_grant_ssh(self):
        SSHKey.objects.create(user=self.a,name='same-key',public_key=self.admin_key.public_key,fingerprint='same-fingerprint')
        self.assertEqual(authorized_keys(self.a.pk).count(self.admin_key.public_key),1)
        self.admin.is_staff=False;self.admin.save(update_fields=['is_staff'])
        self.assertIn(self.admin_key.public_key,authorized_keys(self.a.pk))
        self.assertNotIn(self.admin_key.public_key,authorized_keys(self.b.pk))

    def test_new_and_running_containers_receive_current_keys(self):
        kube=PatchKube();rec=Reconciler(kube)
        req=task(self.a,self.image,gpu=0);approve(req.pk,self.admin);req.refresh_from_db()
        rec.reconcile_task(req);ws=Workspace.objects.get(request=req)
        self.assertIn(self.admin_key.public_key,kube.objects['secrets',ws.object_name]['stringData']['authorized_keys'])
        self.admin.is_staff=False;self.admin.save(update_fields=['is_staff'])
        rec.reconcile_task(req)
        keys=kube.objects['secrets',ws.object_name]['stringData']['authorized_keys']
        self.assertNotIn(self.admin_key.public_key,keys)
        self.assertIn(self.a.sshkey_set.get().public_key,keys)
        self.assertEqual(len(kube.patches),1)
        rec.reconcile_task(req);self.assertEqual(len(kube.patches),1)
        self.admin.is_staff=True;self.admin.save(update_fields=['is_staff'])
        rec.reconcile_task(req)
        self.assertIn(self.admin_key.public_key,kube.objects['secrets',ws.object_name]['stringData']['authorized_keys'])
        self.admin_key.enabled=False;self.admin_key.save(update_fields=['enabled'])
        rec.reconcile_task(req)
        self.assertNotIn(self.admin_key.public_key,kube.objects['secrets',ws.object_name]['stringData']['authorized_keys'])

    def test_gpu_environment_and_unrelated_secret_fields_survive_key_update(self):
        req=task(self.a,self.image)
        original={'task.env':'export CUDA_VISIBLE_DEVICES=GPU-allocated\n','NVIDIA_VISIBLE_DEVICES':'GPU-allocated',
                  'PLATFORM_GPU_LEASE':'lease-123','authorized_keys':'old-key\n'}
        current={'metadata':{'name':'gpu-config','labels':{'platform.task':str(req.pk)}},
                 'data':{k:base64.b64encode(v.encode()).decode() for k,v in original.items()}}
        kube=PatchKube();kube.objects['secrets','gpu-config']=deepcopy(current)
        desired=authorized_keys(self.a.pk)
        sync_authorized_keys(kube,'work',req,current,desired)
        updated=kube.objects['secrets','gpu-config']
        self.assertEqual(set(kube.patches[0]['stringData']),{'authorized_keys'})
        for key in original:
            if key!='authorized_keys':self.assertEqual(current['data'][key],updated['data'][key])
        sync_authorized_keys(kube,'work',req,updated,desired)
        self.assertEqual(len(kube.patches),1)
        self.assertEqual(AuditLog.objects.filter(action='ssh_access_updated',target=str(req.pk)).count(),1)
        current['metadata']['labels']['platform.task']='unrelated'
        with self.assertRaises(RuntimeError):sync_authorized_keys(kube,'work',req,current,desired)

    def test_staff_container_search_and_connection_match_web_and_api(self):
        req=task(self.a,self.image,status='running',purpose='inspect-me')
        Workspace.objects.create(request=req,object_name='inspect-container',ssh_port=30047)
        other=task(self.b,self.image,status='queued');Workspace.objects.create(request=other,object_name='queued-container')
        self.client.force_login(self.admin)
        api=Client(HTTP_AUTHORIZATION='Bearer '+issue_token(self.admin)['token'])
        query={'q':'alice','status':'running','mode':'interactive'}
        web=self.client.get('/management/containers/',query)
        self.assertContains(web,'inspect-me');self.assertNotContains(web,'queued-container')
        self.assertContains(web,f'/tasks/{req.pk}/?from=management#connection')
        self.assertEqual([t.pk for t in web.context['tasks']],[req.pk])
        data=api.get('/api/v1/admin/containers',query).json()['data']['items']
        self.assertEqual([row['id'] for row in data],[str(req.pk)])
        self.assertEqual(data[0]['connection']['port'],30047)
        html=self.client.get(f'/tasks/{req.pk}/?from=management')
        self.assertContains(html,'User alice')
        conn=api.get(f'/api/v1/tasks/{req.pk}/connection').json()['data']
        self.assertEqual(conn['administrator_access']['keys'][0]['fingerprint'],'admin-fingerprint')
        self.assertTrue(conn['administrator_access']['ready']);self.assertTrue(conn['administrator_access']['sudo'])
        self.assertEqual(conn['connection']['user'],'alice')
        self.assertTrue(api.get('/api/v1/me').json()['data']['permissions']['connect_containers'])
        self.admin.is_staff=False;self.admin.save(update_fields=['is_staff'])
        self.assertEqual(api.get('/api/v1/admin/containers').status_code,403)
        self.assertEqual(api.get(f'/api/v1/tasks/{req.pk}/connection').status_code,404)

    def test_normal_users_cannot_browse_or_connect_to_others(self):
        req=task(self.a,self.image,status='running');Workspace.objects.create(request=req,object_name='private',ssh_port=30047)
        self.client.force_login(self.b)
        api=Client(HTTP_AUTHORIZATION='Bearer '+issue_token(self.b)['token'])
        self.assertEqual(self.client.get('/management/containers/').status_code,302)
        self.assertEqual(self.client.get(f'/tasks/{req.pk}/').status_code,404)
        self.assertEqual(api.get('/api/v1/admin/containers').status_code,403)
        self.assertEqual(api.get(f'/api/v1/tasks/{req.pk}/connection').status_code,404)
        own=task(self.b,self.image)
        self.assertNotIn('administrator_access',api.get(f'/api/v1/tasks/{own.pk}/connection').json()['data'])

    def test_missing_admin_key_and_finished_task_have_no_ready_connection(self):
        self.admin_key.enabled=False;self.admin_key.save(update_fields=['enabled'])
        self.client.force_login(self.admin)
        self.assertFalse(self.client.get('/management/containers/').context['ssh_access']['ready'])
        req=task(self.a,self.image,status='running');ws=Workspace.objects.create(request=req,object_name='no-key',ssh_port=30047)
        self.assertFalse(self.client.get(f'/tasks/{req.pk}/').context['administrator_access']['ready'])
        api=Client(HTTP_AUTHORIZATION='Bearer '+issue_token(self.admin)['token'])
        data=api.get(f'/api/v1/tasks/{req.pk}/connection').json()['data']
        self.assertFalse(data['administrator_access']['ready'])
        req.status='succeeded';req.save(update_fields=['status'])
        ws.ssh_port=None;ws.cleanup_complete=True;ws.save(update_fields=['ssh_port','cleanup_complete'])
        self.assertIsNone(api.get(f'/api/v1/tasks/{req.pk}/connection').json()['data']['connection'])
