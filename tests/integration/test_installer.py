from copy import deepcopy
import importlib.util
from pathlib import Path
import sys
import tempfile
from unittest import TestCase
from unittest.mock import patch
import yaml

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('platform_installer', ROOT/'deploy/install.py')
installer = importlib.util.module_from_spec(spec); spec.loader.exec_module(installer)

class InstallerSimulationTests(TestCase):
    def test_clean_machine_entrypoint_renders_arm64_and_amd64_with_two_pools(self):
        for arch in ['arm64','amd64']:
            with self.subTest(architecture=arch), tempfile.TemporaryDirectory() as d:
                cfg=yaml.safe_load((ROOT/'config.example.yaml').read_text())
                cfg['platform'].update(public_host='new-lab.example.edu',https_origin='http://new-lab.example.edu',ssh_ports=[31000,31009],data_storage_class='new-pool-a')
                cfg['network']['public_ports_approved']=True
                cfg['storage']['pools']=[{'name':n,'path':'/srv/'+n+'/volumes','storage_class':'new-'+n,'buffer_percent':10} for n in ['pool-a','pool-b']]
                cfg['storage']['datasets']='/srv/pool-a/datasets'
                facts={'architecture':arch,'cpu':24,'memory_gib':128,'node_ip':'192.0.2.25','interface':'ens3','gpu_inventory':['compatible GPU']*2,'pools':deepcopy(cfg['storage']['pools']),'existing_runtimes':{}}
                config_path=Path(d)/'config.yaml';config_path.write_text(yaml.safe_dump(cfg));out=Path(d)/'rendered'
                argv=['install.py','--config',str(config_path),'--render-only','--output',str(out)]
                with patch.object(sys,'argv',argv),patch.object(installer,'discover',return_value=facts),patch.object(installer,'apply') as apply:
                    installer.main();apply.assert_not_called()
                resolved=yaml.safe_load((out/'resolved-config.yaml').read_text())
                classes=yaml.safe_load((out/'rawfile-values.yaml').read_text())['storageClasses']
                self.assertEqual(resolved['platform']['architecture'],arch)
                self.assertIn('zot-linux-'+arch+':',resolved['registry']['zot_image'])
                self.assertEqual(resolved['platform']['node_ip'],'192.0.2.25')
                self.assertEqual([c['name'] for c in classes],['new-pool-a','new-pool-b'])
                self.assertEqual(resolved['platform']['ssh_ports'],[31000,31009])
