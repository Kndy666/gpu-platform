import base64,importlib.util,io,json,tempfile,uuid
from pathlib import Path
from unittest import TestCase
from unittest.mock import Mock,patch

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('node_agent_save',ROOT/'deploy/node-agent.py')
agent=importlib.util.module_from_spec(spec);spec.loader.exec_module(agent)


class GPUEnvironmentSaveTests(TestCase):
    def test_gpu_snapshot_clears_execution_identity_from_image_configuration(self):
        operation=uuid.uuid4();source=uuid.uuid4()
        cfg={'platform':{'namespace':'gpu-platform','workload_namespace':'gpu-workloads','architecture':'arm64'},
            'registry':{'host':'registry.example:5000'},'network':{'registry_service_ip':'10.43.0.50'}}
        pod={'metadata':{'labels':{'platform.user':'7'}},'status':{'phase':'Running','containerStatuses':[
            {'name':'workspace','containerID':'containerd://'+'c'*64}]},'spec':{'containers':[
                {'name':'workspace','resources':{'limits':{'gpu.platform/compute':'100'}},'volumeMounts':[{'mountPath':'/workspace'}]}]}}
        auth={'auths':{'registry.example:5000':{'auth':'test-identity'}}}
        def kube(*args):
            if 'pods' in args:return json.dumps({'items':[pod]})
            return json.dumps({'data':{'.dockerconfigjson':base64.b64encode(json.dumps(auth).encode()).decode()}})
        response=io.BytesIO(json.dumps({'layers':[{'size':12}],'config':{'size':3}}).encode())
        response.headers={'Docker-Content-Digest':'sha256:'+'a'*64}
        opener=Mock();opener.open.return_value=response
        obj={'metadata':{'name':'snapshot-'+operation.hex},'data':{'request':json.dumps({'id':str(operation),'source':str(source),'owner':7})}}
        with tempfile.TemporaryDirectory() as directory,patch.object(agent,'IMAGE_CLEANUP_DIR',Path(directory)),\
             patch.object(agent,'kube',side_effect=kube),patch.object(agent,'run',return_value='') as run,\
             patch.object(agent.ssl,'create_default_context'),patch.object(agent.urllib.request,'build_opener',return_value=opener):
            result=agent.save_environment(cfg,obj)
            self.assertEqual(result['size_bytes'],15)
            self.assertEqual(list(Path(directory).iterdir()),[])
        commit=next(call.args[0] for call in run.call_args_list if 'commit' in call.args[0])
        self.assertIn('--pause=true',commit)
        for name in ['PLATFORM_GPU_TASK','PLATFORM_GPU_DEVICES','PLATFORM_GPU_LEASE','CUDA_MPS_PIPE_DIRECTORY','CUDA_VISIBLE_DEVICES']:
            self.assertIn('ENV '+name+'=',commit)
        self.assertIn('ENV NVIDIA_VISIBLE_DEVICES=void',commit)
