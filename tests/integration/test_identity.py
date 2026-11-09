from datetime import timedelta
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone
from portal.forms import RequestForm
from portal.identity import validate_login_name
from portal.manifests import task_objects
from portal.models import ResourceRequest, Workspace
from portal.services import approve
from tests.factories import fixtures, task


class IdentityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin, cls.user, cls.other, cls.image = fixtures()

    def test_login_home_and_resource_views_follow_request_identity(self):
        req = task(self.user, self.image)
        approve(req.pk, self.admin)
        req.refresh_from_db()
        secret, pod, _ = task_objects(req, Workspace.objects.get(request=req))
        self.assertEqual(req.login_user, 'alice')
        self.assertEqual(req.home_dir, '/home/alice')
        mounts = pod['spec']['containers'][0]['volumeMounts']
        self.assertEqual(next(m['mountPath'] for m in mounts if m['name']=='home'), '/home/alice')
        self.assertIn('TASK_USER=alice', secret['stringData']['task.env'])
        self.assertTrue(all(m['readOnly'] for m in mounts if m['name']=='resource-view'))
        self.assertFalse(any('hostPath' in volume for volume in pod['spec']['volumes']))

    def test_account_rename_preserves_running_task_login(self):
        req=task(self.user,self.image)
        approve(req.pk,self.admin)
        self.user.username='alice-new'
        self.user.save(update_fields=['username'])
        req=ResourceRequest.objects.select_related('user').get(pk=req.pk)
        self.assertEqual(req.login_user,'alice')
        self.assertEqual(req.home_dir,'/home/alice')
        self.client.force_login(self.user)
        data=self.client.get(f'/tasks/{req.pk}/status/').json()
        self.assertEqual(data['login_name'],'alice')

    def test_request_accepts_only_own_home_directory(self):
        tomorrow=timezone.localdate()+timedelta(days=1)
        data={'image':str(self.image.pk),'purpose':'dev','gpu':0,'cpu':2,'memory_gib':4,'workspace_gib':20,
              'starts_at':str(tomorrow)+'T10:00','ends_at':str(tomorrow)+'T12:00'}
        for path,allowed in [('/home/alice/project',True),('/home/bob/project',False),('/home/user',False)]:
            form=RequestForm({**data,'working_dir':path},user=self.user,mode='interactive')
            self.assertEqual(form.is_valid(),allowed,form.errors)

    def test_unsafe_or_system_names_cannot_become_container_logins(self):
        for name in ['root','sshd','sudo','-option','a/b','x\nname','a'*33]:
            with self.subTest(name=name),self.assertRaises(ValidationError):validate_login_name(name)
        for name in ['admin','alice','student_1','Team-A']:
            self.assertEqual(validate_login_name(name),name)
