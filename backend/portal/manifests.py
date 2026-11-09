import shlex
import json
from pathlib import Path
from .configuration import config
from .models import VolumeAllocation
from .gpu_math import COMPUTE, MEMORY, GATE
from .ssh_access import authorized_keys

def labels(req):
    return {'platform.task': str(req.pk), 'platform.user': str(req.user_id), 'app.kubernetes.io/managed-by': 'gpu-platform'}

def metadata(name, req):
    return {'name': name, 'labels': labels(req)}

def pvc(volume):
    return {'apiVersion': 'v1', 'kind': 'PersistentVolumeClaim',
            'metadata': {'name': volume.pvc_name, 'labels': {'platform.volume': str(volume.pk), 'platform.user': str(volume.user_id)}},
            'spec': {'accessModes': ['ReadWriteOnce'], 'storageClassName': volume.pool.storage_class,
                     'resources': {'requests': {'storage': f'{volume.size_gib}Gi'}}}}

def task_objects(req, ws):
    cfg = config()['platform']
    home = VolumeAllocation.objects.exclude(state='deleted').get(user=req.user, kind='home')
    from .services import work_volume
    work = work_volume(req)
    keys = authorized_keys(req.user_id)
    runner_path = Path(__file__).with_name('workspace_runner.sh')
    if not runner_path.exists():
        runner_path = Path(__file__).resolve().parents[2] / 'images/runner.sh'
    task_env = '\n'.join(f'{key}={shlex.quote(str(value))}' for key, value in {'TASK_MODE': req.mode, 'TASK_DIRECTORY': req.working_dir,
        'TASK_USER': req.login_user, 'TASK_HOME': req.home_dir, 'TASK_CPU': req.cpu, 'TASK_ENV_NAMES': ' '.join(req.environment)}.items()) + '\n'
    task_env += '\n'.join(f'export {key}={shlex.quote(value)}' for key, value in req.environment.items()) + '\n'
    task_command = '#!/bin/bash\nexec ' + shlex.join(req.argv) + '\n' if req.argv else '#!/bin/bash\nexit 0\n'
    secret = {'apiVersion': 'v1', 'kind': 'Secret', 'metadata': metadata(ws.object_name, req), 'type': 'Opaque',
              'stringData': {'authorized_keys': keys, 'task.env': task_env, 'command.sh': task_command, 'runner.sh': runner_path.read_text()}}
    resources = {'cpu': str(req.cpu), 'memory': f'{req.memory_gib}Gi', 'ephemeral-storage': '10Gi'}
    if req.gpu:
        resources[COMPUTE] = str(req.gpu_compute_total)
        resources[MEMORY] = str(req.gpu_memory_total_gib*1024)
        secret['stringData'].update(NVIDIA_VISIBLE_DEVICES='void',PLATFORM_GPU_LEASE='pending')
    spec = {'restartPolicy': 'Never', 'runtimeClassName': cfg['runtime_classes']['gpu' if req.gpu else 'cpu'],
            'automountServiceAccountToken': False, 'serviceAccountName': 'workspace',
            'enableServiceLinks': False, 'terminationGracePeriodSeconds': 70,
            'securityContext': {'runAsNonRoot': True, 'runAsUser': 1000, 'runAsGroup': 1000, 'fsGroup': 1000,
                                'fsGroupChangePolicy': 'OnRootMismatch', 'seccompProfile': {'type': 'Localhost', 'localhostProfile': 'gpu-platform-workspace.json'}},
            'containers': [{'name': 'workspace', 'image': req.image_reference, 'imagePullPolicy': 'IfNotPresent',
                            'command': ['/bin/bash', '/run/platform/runner.sh', '/run/platform/task.env'],
                            'env': [{'name': 'HOME', 'value': req.home_dir}, {'name': 'USER', 'value': req.login_user}, {'name': 'LOGNAME', 'value': req.login_user}],
                            'ports': [{'name': 'ssh', 'containerPort': 2222}],
                            'resources': {'requests': resources.copy(), 'limits': resources.copy()},
                            'securityContext': {'allowPrivilegeEscalation': True,
                                'appArmorProfile': {'type': 'Localhost', 'localhostProfile': 'gpu-platform-workspace'}},
                            'readinessProbe': {'tcpSocket': {'port': 2222}, 'initialDelaySeconds': 3, 'periodSeconds': 5},
                            'volumeMounts': [{'name': 'home', 'mountPath': req.home_dir}, {'name': 'work', 'mountPath': '/workspace'},
                                             {'name': 'datasets', 'mountPath': '/datasets', 'readOnly': True},
                                             {'name': 'config', 'mountPath': '/run/platform', 'readOnly': True},
                                             {'name': 'run', 'mountPath': '/run/user'}, {'name': 'shm', 'mountPath': '/dev/shm'}]
                                            + [{'name': 'resource-view', 'mountPath': '/'+path, 'subPath': path, 'readOnly': True}
                                               for path in ['proc/cpuinfo', 'proc/meminfo', 'proc/stat', 'proc/uptime', 'proc/swaps',
                                                            'proc/diskstats', 'proc/loadavg', 'sys/devices/system/cpu/online']]}],
            'volumes': [{'name': 'home', 'persistentVolumeClaim': {'claimName': home.pvc_name}},
                        {'name': 'work', 'persistentVolumeClaim': {'claimName': work.pvc_name}},
                        {'name': 'datasets', 'persistentVolumeClaim': {'claimName': 'public-datasets', 'readOnly': True}},
                        {'name': 'resource-view', 'persistentVolumeClaim': {'claimName': 'resource-view', 'readOnly': True}},
                        {'name': 'config', 'secret': {'secretName': ws.object_name, 'defaultMode': 0o440}},
                        {'name': 'run', 'emptyDir': {'medium': 'Memory', 'sizeLimit': '64Mi'}},
                        {'name': 'shm', 'emptyDir': {'medium': 'Memory', 'sizeLimit': f'{max(1, req.memory_gib // 4)}Gi'}}]}
    if req.image.owner_id:
        if not req.user.profile.registry_secret:
            raise RuntimeError('私有仓库凭证尚未配置')
        spec['imagePullSecrets'] = [{'name': req.user.profile.registry_secret}]
    if req.user.profile.registry_secret:
        spec['volumes'].append({'name': 'registry-auth', 'secret': {'secretName': req.user.profile.registry_secret,
            'items': [{'key': 'docker-reader.json', 'path': 'config.json'}], 'defaultMode': 0o440}})
        spec['containers'][0]['volumeMounts'].append({'name': 'registry-auth', 'mountPath': '/run/platform-registry', 'readOnly': True})
    spec['containers'][0]['env'].append({'name':'NVIDIA_VISIBLE_DEVICES','value':'void'})
    gpu_annotations={}
    if req.gpu:
        spec['schedulingGates']=[{'name':GATE}]
        spec['containers'][0]['env'][-1]={'name':'NVIDIA_VISIBLE_DEVICES','valueFrom':{'secretKeyRef':{'name':ws.object_name,'key':'NVIDIA_VISIBLE_DEVICES'}}}
        spec['containers'][0]['env'] += [
            {'name':'NVIDIA_DRIVER_CAPABILITIES','value':'compute,utility'},
            {'name':'CUDA_VISIBLE_DEVICES','valueFrom':{'secretKeyRef':{'name':ws.object_name,'key':'NVIDIA_VISIBLE_DEVICES'}}},
            {'name':'CUDA_MPS_PIPE_DIRECTORY','value':'/run/platform-mps'},
            {'name':'PLATFORM_GPU_TASK','value':str(req.pk)},
            {'name':'PLATFORM_GPU_DEVICES','valueFrom':{'secretKeyRef':{'name':ws.object_name,'key':'NVIDIA_VISIBLE_DEVICES'}}},
            {'name':'PLATFORM_GPU_LEASE','valueFrom':{'secretKeyRef':{'name':ws.object_name,'key':'PLATFORM_GPU_LEASE'}}}]
        spec['volumes'].append({'name':'gpu-connections','persistentVolumeClaim':{'claimName':'gpu-connections','readOnly':True}})
        spec['containers'][0]['volumeMounts'].append({'name':'gpu-connections','mountPath':'/run/platform-mps',
            'subPath':'t_'+req.pk.hex,'readOnly':True})
        gpu_annotations={'gpu.platform/request':json.dumps(req.gpu_requests),'gpu.platform/config-secret':ws.object_name}
    host = config()['registry']['host']
    spec['volumes'].append({'name': 'registry-ca', 'configMap': {'name': 'registry-ca'}})
    spec['containers'][0]['volumeMounts'].append({'name': 'registry-ca',
        'mountPath': '/etc/docker/certs.d/'+host+'/ca.crt', 'subPath': 'tls.crt', 'readOnly': True})
    meta = metadata(ws.object_name, req)
    meta['annotations']=gpu_annotations
    meta['labels']['kueue.x-k8s.io/queue-name'] = f'user-{req.user_id}'
    # Kueue priorities are named, pre-installed classes, not a second business scheduler.
    meta['labels']['kueue.x-k8s.io/priority-class'] = 'platform-high' if req.priority > 0 else 'platform-normal'
    if req.mode == 'batch':
        workload = {'apiVersion': 'batch/v1', 'kind': 'Job', 'metadata': meta,
                    'spec': {'suspend': True, 'parallelism': 1, 'completions': 1, 'backoffLimit': 0,
                             'template': {'metadata': {'labels': labels(req),'annotations':gpu_annotations}, 'spec': spec}}}
    else:
        # Add the gate ourselves: never rely on an absent/misconfigured webhook to fail closed.
        spec.setdefault('schedulingGates',[]).insert(0,{'name': 'kueue.x-k8s.io/admission'})
        workload = {'apiVersion': 'v1', 'kind': 'Pod', 'metadata': meta, 'spec': spec}
    service = {'apiVersion': 'v1', 'kind': 'Service', 'metadata': metadata(ws.object_name, req),
               'spec': {'type': 'NodePort', 'selector': labels(req),
                        'ports': [{'name': 'ssh', 'port': 2222, 'targetPort': 2222, 'nodePort': ws.ssh_port}]}}
    return secret, workload, service
