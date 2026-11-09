from copy import deepcopy
import importlib.util
import json
from pathlib import Path
from unittest import TestCase

spec=importlib.util.spec_from_file_location('workspace_runtime',Path(__file__).resolve().parents[2]/'deploy/workspace-runtime.py')
runtime=importlib.util.module_from_spec(spec);spec.loader.exec_module(runtime)


class WorkspaceRuntimeTests(TestCase):
    def test_keyrings_remain_blocked_with_runtime_supported_errno(self):
        profile=json.loads((runtime.ROOT/'deploy/profiles/workspace-seccomp.json').read_text())
        rules=[rule for rule in profile['syscalls'] if 'keyctl' in rule['names']]
        self.assertEqual(len(rules),1)
        self.assertEqual(rules[0]['action'],'SCMP_ACT_ERRNO')
        self.assertEqual(rules[0]['errnoRet'],38)
        for rule in profile['syscalls']:
            if rule['action']=='SCMP_ACT_ALLOW':
                self.assertFalse({'keyctl','add_key','request_key'} & set(rule['names']))

    def test_native_views_keep_masking_device_denials_and_capabilities(self):
        default={'mounts':[{'destination':'/proc','type':'proc','source':'proc'}],
                 'process':{'capabilities':{'bounding':['CAP_SETUID','CAP_SETGID']}},
                 'linux':{'maskedPaths':['/proc/keys'],'readonlyPaths':['/proc/sys'],
                          'resources':{'devices':[{'allow':False,'access':'rwm'}]}}}
        before=deepcopy(default);result=runtime.workspace_spec(default)
        self.assertEqual(default,before)
        self.assertEqual(result['process'],before['process'])
        self.assertEqual(result['linux']['maskedPaths'],['/proc/keys'])
        self.assertEqual(result['linux']['readonlyPaths'],['/proc/sys'])
        native={m['destination']:m for m in result['mounts']}
        self.assertEqual(native['/run/platform-proc']['source'],'proc')
        self.assertEqual(native['/run/platform-sys']['type'],'sysfs')
        self.assertIn('ro',native['/run/platform-sys']['options'])
        self.assertEqual(result['linux']['resources']['devices'][0],{'allow':False,'access':'rwm'})
        self.assertEqual(result['linux']['resources']['devices'][1],{'allow':True,'type':'c','major':10,'minor':200,'access':'rwm'})
        self.assertEqual(result['linux']['devices'][0]['path'],'/dev/net/tun')

    def test_cpu_and_gpu_use_separate_standard_runtime_handlers(self):
        template=runtime.runtime_template({'cpu':'workspace-runc','gpu':'workspace-nvidia'})
        self.assertIn('{{ template "base" . }}',template)
        self.assertIn('BinaryName = "/usr/bin/runc"',template)
        self.assertIn('BinaryName = "/usr/bin/nvidia-container-runtime"',template)
        self.assertEqual(template.count('base_runtime_spec'),2)
