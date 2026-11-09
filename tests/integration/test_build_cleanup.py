from unittest.mock import Mock, patch

from django.test import TestCase

from portal.builds import BuildReconciler
from portal.models import ImageBuild
from portal.services import policy_for
from tests.factories import fixtures


class BuildCleanupTests(TestCase):
    def setUp(self):
        self.admin, self.user, self.other, self.image = fixtures()
        self.kube = Mock()
        self.kube.path.side_effect = lambda resource, ns, name: f'/{ns}/{resource}/{name}'
        self.rec = BuildReconciler(self.kube)

    def context(self, build):
        return {'metadata': {'name': 'build-' + build.pk.hex, 'uid': str(build.pk),
                             'labels': {'platform.build': str(build.pk)}}}

    def test_cleanup_removes_only_finished_owned_contexts(self):
        done = ImageBuild.objects.create(user=self.user, name='done', dockerfile='FROM test', status='succeeded')
        failed = ImageBuild.objects.create(user=self.user, name='failed', dockerfile='FROM test', status='failed')
        active = ImageBuild.objects.create(user=self.user, name='active', dockerfile='FROM test', status='building')
        unrelated = self.context(done)
        unrelated['metadata']['name'] = 'unrelated'
        invalid = {'metadata': {'name': 'bad', 'labels': {'platform.build': 'bad'}}}
        self.kube.list.return_value = [self.context(done), self.context(failed), self.context(active), unrelated, invalid]
        self.rec.cleanup_contexts()
        self.assertEqual(self.kube.call.call_count, 2)
        for build in (done, failed):
            self.kube.call.assert_any_call('DELETE', f'/{self.rec.ns}/configmaps/build-{build.pk.hex}',
                                          {'preconditions': {'uid': str(build.pk)}})

    def test_over_quota_build_deletes_its_tag_only(self):
        build = ImageBuild.objects.create(user=self.user, name='app', dockerfile='FROM test', status='building')
        self.kube.get.return_value = {'status': {'conditions': [{'type': 'Complete', 'status': 'True'}]}}
        self.kube.list.return_value = []
        with patch('portal.builds.RegistryClient') as client:
            client.return_value.manifest.return_value = ('sha256:' + 'a' * 64, policy_for(self.user).image_gib * 2**30 + 1)
            self.rec.one(build, {str(self.user.pk): {'login': 'test', 'password': 'test'}})
            client.return_value.delete_tag.assert_called_once_with(f'user-{self.user.pk}/app', build.pk.hex)
            client.return_value.delete_manifest.assert_not_called()
        build.refresh_from_db()
        self.assertEqual(build.status, 'failed')
