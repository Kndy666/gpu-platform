import importlib.util
from contextlib import nullcontext
import json
from pathlib import Path
import subprocess
import tempfile
from unittest import TestCase
from unittest.mock import patch
import uuid

spec=importlib.util.spec_from_file_location('gpu_runtime',Path(__file__).resolve().parents[2]/'deploy/gpu_runtime.py')
runtime=importlib.util.module_from_spec(spec);spec.loader.exec_module(runtime)


class GPURuntimeTests(TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory();self.addCleanup(self.directory.cleanup)
        self.records=Path(self.directory.name)
        self.patch=patch.object(runtime,'RECORDS',self.records);self.patch.start();self.addCleanup(self.patch.stop)
        self.manager=runtime.GPUManager({'platform':{'workload_namespace':'gpu-workloads'}})

    def test_release_uses_complete_names_and_keeps_other_tasks(self):
        task=str(uuid.uuid4());name='t_'+uuid.UUID(task).hex;part='p_'+uuid.UUID(task).hex+'_0'
        record={'task':task,'allocations':[{'uuid':'GPU-a','partition':part}]};runtime.save(record)
        def mps(*args):
            if args[:2]==('namespace','list'):
                return name+',shared\nother-task,shared' if '--format=csv,noheader' in args else name[:18]+'...'
            if args[:2]==('sm-partition','list'):
                return 'shared,GPU-a,'+part+',14,28\nshared,GPU-a,another-part,14,28' if '--format=csv,noheader' in args else part[:18]+'...'
            return ''
        with patch.object(runtime,'mps',side_effect=mps) as control:self.manager.release(record)
        self.assertFalse((self.records/(task+'.json')).exists())
        self.assertIn(('namespace','delete',name,'--server=shared','--force'),[c.args for c in control.call_args_list])
        deletes=[c.args[2] for c in control.call_args_list if c.args[:2]==('sm-partition','delete')]
        self.assertEqual(deletes,[part])

    def test_second_card_failure_rolls_back_first_partition_and_lease(self):
        task=str(uuid.uuid4());part='p_'+uuid.UUID(task).hex+'_0'
        pod={'metadata':{'uid':'pod-uid','name':'pod','labels':{'platform.task':task},
            'annotations':{'gpu.platform/request':'[{"compute_percent":100,"memory_gib":4},{"compute_percent":50,"memory_gib":3}]'}}}
        devices=[{'uuid':'GPU-'+str(i),'index':i,'sm_count':114,'usable_memory_mib':24000,'ready':True} for i in range(2)]
        def mps(*args):
            if args[:2]==('sm-partition','create'):
                if args[2].endswith('_1'):raise subprocess.CalledProcessError(1,args,output='device unavailable')
                return 'created on device'
            if args[:2]==('namespace','list'):return ''
            if args[:2]==('sm-partition','list'):return 'shared,GPU-0,'+part+',28,56'
            return ''
        with patch.object(runtime,'mps',side_effect=mps) as control,patch.object(self.manager,'kube') as kube:
            with self.assertRaises(subprocess.CalledProcessError):self.manager.prepare(pod,devices)
        self.assertEqual(list(self.records.iterdir()),[])
        self.assertIn(('sm-partition','delete',part,'--server=shared','--device=GPU-0'),[c.args for c in control.call_args_list])
        kube.assert_not_called()

    def lost_record(self,cgroup=None):
        record={'task':str(uuid.uuid4()),'pod_uid':'old-pod','allocations':[{'uuid':'GPU-a',
            'compute_percent':25,'sm_count':28,'memory_mib':3072,'partition':'old-partition'}]}
        if cgroup:record['cgroup']=str(cgroup)
        runtime.save(record);return record

    def test_daemon_restarted_without_shared_server_releases_stale_lease(self):
        record=self.lost_record(self.records/'gone-cgroup')
        def mps(*args):
            if args[:2]==('namespace','list'):raise subprocess.CalledProcessError(1,args,output='Server shared not found')
            if args[:2]==('server','list'):return ''
            self.fail(str(args))
        with patch.object(runtime,'mps',side_effect=mps):self.manager.release(record)
        self.assertEqual(self.manager.records(),[])

    def test_healthy_shared_server_errors_keep_lease_for_retry(self):
        record=self.lost_record()
        def mps(*args):
            if args[:2]==('namespace','list'):raise subprocess.CalledProcessError(1,args)
            if args[:2]==('server','list'):return 'shared,0,123,Ready,/pipes/shared'
            self.fail(str(args))
        with patch.object(runtime,'mps',side_effect=mps):
            with self.assertRaises(subprocess.CalledProcessError):self.manager.release(record)
        self.assertEqual(len(self.manager.records()),1)

    def test_live_cgroup_keeps_lease_after_daemon_restart(self):
        cgroup=self.records/'live-cgroup';cgroup.mkdir();record=self.lost_record(cgroup)
        with patch.object(runtime,'mps',side_effect=subprocess.CalledProcessError(1,['namespace','list'])):
            with self.assertRaises(subprocess.CalledProcessError):self.manager.release(record)
        self.assertEqual(len(self.manager.records()),1)

    def run_recovery_tick(self,live_cgroup=False):
        record=self.lost_record(self.records if live_cgroup else None)
        device={'uuid':'GPU-a','index':0,'sm_count':114,'usable_memory_mib':24052,'ready':True}
        started=[]
        def mps(*args):
            if args[:2]==('namespace','list'):raise subprocess.CalledProcessError(1,args,output='Server shared not found')
            if args[:2]==('server','list'):return 'shared,0,123,Ready,/pipes/shared' if started else ''
            if args[:2]==('server','create'):started.append(True)
            return ''
        def kube(*args,**kw):
            if args[:3]==('-n','gpu-workloads','get'):return json.dumps({'items':[]})
            if args[:2]==('get','nodes'):return json.dumps({'items':[{'metadata':{'name':'node'},'status':{'capacity':{}}}]})
            return ''
        with patch.object(runtime,'STATE',self.records),patch.object(runtime,'PIPES',self.records/'pipes'),\
             patch.object(runtime,'locked',side_effect=nullcontext),patch.object(runtime,'cuda_inventory',return_value=[device]),\
             patch.object(runtime,'mps',side_effect=mps),patch.object(runtime,'run'),patch.object(runtime.time,'sleep'),\
             patch.object(self.manager,'kube',side_effect=kube) as calls:
            self.manager.tick()
        return device,started,calls.call_args_list

    def test_restart_recovers_capacity_after_collecting_dead_leases(self):
        device,started,calls=self.run_recovery_tick()
        self.assertTrue(started);self.assertTrue(device['ready'])
        self.assertEqual(device['used_compute_percent'],0)
        self.assertEqual(self.manager.records(),[])
        patches=[json.loads(c.args[-1]) for c in calls if c.args[:2]==('patch','node')]
        self.assertEqual(patches[0]['status']['capacity'][runtime.COMPUTE],'100')

    def test_pending_cgroup_cleanup_still_publishes_unavailable_inventory(self):
        with self.assertLogs(runtime.log,level='WARNING'):
            device,started,calls=self.run_recovery_tick(live_cgroup=True)
        self.assertFalse(started);self.assertFalse(device['ready'])
        self.assertEqual(device['used_compute_percent'],25)
        self.assertEqual(len(self.manager.records()),1)
        patches=[json.loads(c.args[-1]) for c in calls if c.args[:2]==('patch','node')]
        self.assertEqual(patches[0]['status']['capacity'][runtime.COMPUTE],'0')
