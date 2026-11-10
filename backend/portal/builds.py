import logging
import base64
import uuid
from pathlib import Path
from django.db import transaction
from django.db.models import Sum
from .configuration import config
from .models import ImageBuild, ImageArtifact
from .registry import RegistryClient, sync_registry_users
from .services import audit, policy_for
from .kube import KubeError

log = logging.getLogger(__name__)


def ensure_base_build():
    from django.contrib.auth import get_user_model
    if not config()['registry'].get('bootstrap_workspace', True):
        return
    if ImageArtifact.objects.filter(shared=True, verified=True, state='ready', name='Ubuntu 22.04').exists():
        return
    if ImageBuild.objects.filter(bootstrap=True).exists():
        return
    administrator = get_user_model().objects.filter(is_superuser=True, is_active=True).first()
    if administrator:
        recipe = Path(__file__).with_name('workspace.Dockerfile')
        if not recipe.exists():
            recipe = Path(__file__).resolve().parents[2] / 'images/Dockerfile'
        ImageBuild.objects.create(user=administrator, name='platform-ubuntu-ssh', dockerfile=recipe.read_text(), bootstrap=True)

def build_objects(build):
    from .configuration import base_config
    from .node_context import node_config
    cfg = node_config(build.node, base_config()); name = 'build-'+build.pk.hex
    labels = {'platform.build': str(build.pk), 'platform.user': str(build.user_id)}
    meta = {'name': name, 'labels': labels}
    repo = f'user-{build.user_id}/{build.name}'
    reference = f"{cfg['registry']['host']}/{repo}:{build.pk.hex}"
    cm = {'apiVersion': 'v1', 'kind': 'ConfigMap', 'metadata': meta, 'data': {'Dockerfile': build.dockerfile}}
    if build.bootstrap:
        if not build.user.is_superuser:
            raise RuntimeError('基础镜像初始化需要平台管理员')
        source = Path(__file__).with_name('workspace_runner.sh')
        if not source.exists(): source = Path(__file__).resolve().parents[2]/'images/runner.sh'
        cm['data']['runner.sh'] = source.read_text()
        source = Path(__file__).with_name('workspace_docker.sh')
        if not source.exists(): source = Path(__file__).resolve().parents[2]/'images/docker.sh'
        cm['data']['docker.sh'] = source.read_text()
        assets = Path(__file__).with_name('workspace_assets')
        if not assets.exists(): assets = Path(__file__).resolve().parents[2]/'images/vendor'
        cm['data']['LICENSE.blesh'] = (assets/'LICENSE.blesh').read_text()
        cm['binaryData'] = {'ble-0.4.0-devel3.tar.xz': base64.b64encode((assets/'ble-0.4.0-devel3.tar.xz').read_bytes()).decode()}
    mounts = [{'name': 'context', 'mountPath': '/context'}, {'name': 'dockerfile', 'mountPath': '/definition', 'readOnly': True},
              {'name': 'cache', 'mountPath': '/home/user/.local/share/buildkit'}, {'name': 'auth', 'mountPath': '/auth', 'readOnly': True},
              {'name': 'ca', 'mountPath': '/registry-ca', 'readOnly': True}, {'name': 'buildkit-config', 'mountPath': '/config', 'readOnly': True}]
    command = ['buildctl-daemonless.sh', 'build', '--frontend', 'dockerfile.v0', '--local', 'dockerfile=/definition',
               '--local', 'context=/context', '--opt', 'platform=linux/'+cfg['platform']['architecture'],
               '--output', 'type=image,name='+reference+',push=true,oci-mediatypes=true']
    if build.bootstrap:
        command += ['--opt', 'build-arg:BASE_IMAGE='+cfg['registry']['base_image']]
    command = ['sh', '-ec', 'attempt=0; until nc -zw 2 "$REGISTRY_HOST" "$REGISTRY_PORT"; do attempt=$((attempt+1)); [ "$attempt" -lt 30 ] || exit 1; sleep 2; done; exec "$@"', 'build', *command]
    registry_host, _, registry_port = cfg['registry']['host'].rpartition(':')
    spec = {'restartPolicy': 'Never', 'automountServiceAccountToken': False, 'enableServiceLinks': False,
            'securityContext': {'runAsUser': 1000, 'runAsGroup': 1000, 'fsGroup': 1000, 'runAsNonRoot': True},
            'containers': [{'name': 'buildkit', 'image': cfg['registry']['buildkit_image'], 'command': command,
                'env': [{'name': 'DOCKER_CONFIG', 'value': '/auth'}, {'name': 'BUILDKITD_FLAGS', 'value': '--oci-worker-no-process-sandbox --config /config/buildkitd.toml'},
                        {'name': 'REGISTRY_HOST', 'value': registry_host}, {'name': 'REGISTRY_PORT', 'value': registry_port}],
                'securityContext': {'runAsUser': 1000, 'runAsGroup': 1000, 'seccompProfile': {'type': 'Unconfined'}, 'appArmorProfile': {'type': 'Unconfined'}},
                'resources': {'requests': {'cpu': '1', 'memory': '1Gi', 'ephemeral-storage': '2Gi'}, 'limits': {'cpu': '4', 'memory': '8Gi', 'ephemeral-storage': '30Gi'}},
                'volumeMounts': mounts}],
            'volumes': [{'name': 'context', 'emptyDir': {'sizeLimit': '4Gi'}}, {'name': 'dockerfile', 'configMap': {'name': name}},
                {'name': 'cache', 'emptyDir': {'sizeLimit': '25Gi'}},
                {'name': 'auth', 'secret': {'secretName': f'registry-u{build.user_id}', 'items': [{'key': '.dockerconfigjson', 'path': 'config.json'}]}},
                {'name': 'ca', 'configMap': {'name': 'registry-ca'}}, {'name': 'buildkit-config', 'configMap': {'name': 'buildkit-config'}}]}
    if cfg['registry'].get('build_apparmor_profile'):
        spec['containers'][0]['securityContext']['appArmorProfile'] = {'type': 'Localhost', 'localhostProfile': cfg['registry']['build_apparmor_profile']}
    if build.context_git:
        spec['initContainers'] = [{'name': 'context', 'image': cfg['registry']['git_image'],
            'command': ['git', '-c', 'http.followRedirects=false', 'clone', '--depth', '1', '--', build.context_git, '/context'],
            'securityContext': {'allowPrivilegeEscalation': False, 'capabilities': {'drop': ['ALL']}, 'seccompProfile': {'type': 'RuntimeDefault'}},
            'resources': {'requests': {'cpu': '100m', 'memory': '128Mi'}, 'limits': {'cpu': '1', 'memory': '512Mi'}},
            'volumeMounts': [{'name': 'context', 'mountPath': '/context'}]}]
    if build.bootstrap:
        spec['initContainers'] = [{'name': 'context', 'image': cfg['registry']['buildkit_image'],
            'command': ['sh', '-ec', 'mkdir -p /context/vendor; cp -L /definition/Dockerfile /definition/runner.sh /definition/docker.sh /context/; cp -L /definition/ble-0.4.0-devel3.tar.xz /definition/LICENSE.blesh /context/vendor/'],
            'securityContext': {'allowPrivilegeEscalation': False, 'capabilities': {'drop': ['ALL']},
                                'seccompProfile': {'type': 'RuntimeDefault'}},
            'resources': {'requests': {'cpu': '100m', 'memory': '64Mi'}, 'limits': {'cpu': '1', 'memory': '128Mi'}},
            'volumeMounts': [{'name': 'context', 'mountPath': '/context'},
                            {'name': 'dockerfile', 'mountPath': '/definition', 'readOnly': True}]}]
    job = {'apiVersion': 'batch/v1', 'kind': 'Job', 'metadata': meta, 'spec': {'parallelism': 1, 'completions': 1, 'backoffLimit': 0, 'ttlSecondsAfterFinished': 86400,
            'activeDeadlineSeconds': 3600, 'template': {'metadata': {'labels': labels}, 'spec': spec}}}
    # Build deadlines are independent of user batch compute jobs, which have none.
    return cm, job, repo, reference

class BuildReconciler:
    def __init__(self, kube): self.kube = kube; self.ns = config()['platform']['build_namespace']

    def tick(self):
        users = sync_registry_users(self.kube)
        for build in ImageBuild.objects.filter(status__in=['submitted', 'building']).select_related('user__profile'):
            try: self.one(build, users)
            except Exception as e:
                log.exception('镜像构建同步失败 %s', build.pk)
                ImageBuild.objects.filter(pk=build.pk).update(error=str(e)[:2000])
        self.cleanup_contexts()

    def cleanup_contexts(self):
        contexts = {}
        for obj in self.kube.list('configmaps', self.ns, 'platform.build'):
            try:
                build_id = uuid.UUID(obj['metadata'].get('labels', {}).get('platform.build', ''))
            except (ValueError, TypeError):
                continue
            if obj['metadata']['name'] == 'build-' + build_id.hex:
                contexts[build_id] = obj
        finished = ImageBuild.objects.filter(pk__in=contexts, status__in=['succeeded', 'failed']).values_list('pk', flat=True)
        for build_id in finished:
            obj = contexts[build_id]
            self.kube.call('DELETE', self.kube.path('configmaps', self.ns, obj['metadata']['name']),
                           {'preconditions': {'uid': obj['metadata']['uid']}})

    def one(self, build, users):
        name = 'build-'+build.pk.hex
        cm, job, repo, reference = build_objects(build)
        policy = policy_for(build.user)
        if build.status == 'submitted':
            total = ImageArtifact._base_manager.filter(owner=build.user).exclude(state='deleted').aggregate(n=Sum('size_bytes'))['n'] or 0
            if not policy.unlimited and total >= policy.image_gib * 2**30:
                ImageBuild.objects.filter(pk=build.pk).update(status='failed', error='个人镜像额度已用满')
                return
            self.kube.ensure(cm, 'configmaps', self.ns)
            self.kube.ensure(job, 'jobs', self.ns, 'batch/v1')
            ImageBuild.objects.filter(pk=build.pk).update(status='building', error='')
        obj = self.kube.get('jobs', self.ns, name, 'batch/v1')
        if obj is None:
            ImageBuild.objects.filter(pk=build.pk).update(status='failed', error='构建任务缺失，请重新提交')
            return
        pods = self.kube.list('pods', self.ns, f'platform.build={build.pk}')
        for pod in pods:
            path = self.kube.path('pods', self.ns, pod['metadata']['name'])+'/log'
            logtext = ''
            if pod.get('status', {}).get('phase') in {'Running', 'Succeeded', 'Failed'}:
                try:
                    logtext = self.kube.call('GET', path, params={'container': 'buildkit', 'tailLines': 3000, 'limitBytes': 250000}, text=True)
                except KubeError as e:
                    if e.status != 400: raise
            if logtext: ImageBuild.objects.filter(pk=build.pk).update(log=logtext)
        conditions = obj.get('status', {}).get('conditions', [])
        failed = next((c for c in conditions if c['type'] == 'Failed' and c['status'] == 'True'), None)
        if failed:
            ImageBuild.objects.filter(pk=build.pk).update(status='failed', error=failed.get('message', '构建失败'))
            audit(None, 'image_build_failed', build.pk)
        elif any(c['type'] == 'Complete' and c['status'] == 'True' for c in conditions):
            registry = RegistryClient(users[str(build.user_id)])
            digest, size = registry.manifest(repo, build.pk.hex)
            total = ImageArtifact.objects.filter(owner=build.user).exclude(state='deleted').aggregate(n=Sum('size_bytes'))['n'] or 0
            if not policy.unlimited and total + size > policy.image_gib * 2**30:
                registry.delete_tag(repo, build.pk.hex)
                ImageBuild.objects.filter(pk=build.pk).update(status='failed', error='构建结果超出个人镜像额度')
                return
            with transaction.atomic():
                current = ImageBuild.objects.get(pk=build.pk)
                if current.artifact_id: return
                display_name = 'Ubuntu 22.04' if build.bootstrap else build.name
                artifact = ImageArtifact.objects.create(owner=build.user, name=display_name, reference=reference,
                    digest=digest, size_bytes=size, architecture=config()['platform']['architecture'], verified=True, shared=build.bootstrap)
                current.artifact, current.status, current.error = artifact, 'succeeded', ''
                current.save(update_fields=['artifact', 'status', 'error'])
                audit(None, 'image_build_succeeded', build.pk, digest=digest, size_bytes=size)
