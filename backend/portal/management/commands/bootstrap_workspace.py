from pathlib import Path
import time
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from portal.configuration import config
from portal.kube import KubeClient
from portal.models import ImageArtifact, ImageBuild
from portal.registry import sync_registry_users

class Command(BaseCommand):
    help = 'Prepare the shared Ubuntu 22.04 workspace image'

    def add_arguments(self, parser):
        parser.add_argument('--wait', type=int, default=1800)
        parser.add_argument('--rebuild', action='store_true')

    def handle(self, *args, **options):
        if not options['rebuild'] and ImageArtifact.objects.filter(shared=True, verified=True, state='ready',
                name='Ubuntu 22.04', architecture=config()['platform']['architecture']).exists():
            self.stdout.write('A verified shared workspace image is available.'); return
        if not config()['registry'].get('bootstrap_workspace', True):
            raise CommandError('Register a verified shared workspace image before completing installation')
        admin = get_user_model().objects.filter(is_superuser=True, is_active=True).first()
        if not admin: raise CommandError('Initialize the administrator account first')
        recipe = Path(__file__).resolve().parents[2]/'workspace.Dockerfile'
        if not recipe.exists(): recipe = Path(__file__).resolve().parents[4]/'images/Dockerfile'
        build = ImageBuild.objects.filter(bootstrap=True, status__in=['submitted','building']).first()
        if not build:
            build = ImageBuild.objects.create(user=admin, name='platform-ubuntu-ssh', dockerfile=recipe.read_text(), bootstrap=True)
        self.stdout.write(f'Workspace base image build: {build.pk}')
        deadline = time.monotonic()+options['wait']
        while time.monotonic() < deadline:
            build.refresh_from_db()
            if build.status == 'failed': raise CommandError(build.error+'\n'+build.log[-4000:])
            if build.status == 'succeeded':
                sync_registry_users(KubeClient())
                self.stdout.write(f'Workspace base image ready: {build.artifact.reference}@{build.artifact.digest}')
                return
            time.sleep(5)
        raise CommandError('Workspace image preparation is still running; inspect the image build in the administrator portal')
