from unittest import TestCase
from portal.gpu_math import choose_devices,sm_count,resolve_gpu_max,validate_gpu_requests

class GPUMaximumTests(TestCase):
    def setUp(self):
        self.limits={'compute_per_card_percent':100,'memory_per_card_gib':23,
            'compute_total_percent':200,'memory_total_gib':48,
            'devices':[{'sm_count':114,'memory_gib':23} for _ in range(4)]}
        self.maximum={'compute_percent':'max','memory_gib':'max'}

    def test_maximum_is_shared_within_total_group_quota(self):
        self.assertEqual(resolve_gpu_max([self.maximum]*4,self.limits),[{'compute_percent':50,'memory_gib':12}]*4)

    def test_fixed_values_are_preserved_and_dimensions_are_independent(self):
        specs=[{'compute_percent':100,'memory_gib':'max'},{'compute_percent':'max','memory_gib':8}]
        self.limits.update(compute_total_percent=150,memory_total_gib=24)
        self.assertEqual(resolve_gpu_max(specs,self.limits),[{'compute_percent':100,'memory_gib':16},{'compute_percent':50,'memory_gib':8}])
        self.assertEqual(specs[0]['memory_gib'],'max')

    def test_unlimited_group_still_uses_physical_capacity(self):
        self.limits.update(compute_total_percent=None,memory_total_gib=None)
        self.assertEqual(resolve_gpu_max([self.maximum]*4,self.limits),[{'compute_percent':100,'memory_gib':23}]*4)

    def test_per_card_caps_and_integer_remainder(self):
        self.limits.update(compute_per_card_percent=50,memory_per_card_gib=4,compute_total_percent=75,memory_total_gib=6)
        self.assertEqual(resolve_gpu_max([self.maximum]*2,self.limits),[{'compute_percent':38,'memory_gib':3},{'compute_percent':37,'memory_gib':3}])

    def test_heterogeneous_cards_fit_fixed_rows_and_maximum(self):
        self.limits['devices']=[{'sm_count':114,'memory_gib':8},{'sm_count':114,'memory_gib':23}]
        self.assertEqual(resolve_gpu_max([{'compute_percent':50,'memory_gib':'max'},{'compute_percent':50,'memory_gib':16}],self.limits),
            [{'compute_percent':50,'memory_gib':8},{'compute_percent':50,'memory_gib':16}])

    def test_impossible_budget_and_missing_inventory_fail(self):
        for changes in [{'compute_total_percent':3},{'memory_total_gib':1},{'devices':[]},{'compute_per_card_percent':0}]:
            with self.subTest(changes=changes),self.assertRaises(ValueError):resolve_gpu_max([self.maximum]*2,{**self.limits,**changes})

    def test_workers_accept_only_resolved_numeric_values(self):
        with self.assertRaises(ValueError):validate_gpu_requests([self.maximum],1)
        for invalid in [True,'MAX',None,2.5]:
            with self.subTest(invalid=invalid),self.assertRaises(ValueError):
                resolve_gpu_max([{'compute_percent':invalid,'memory_gib':'max'}],self.limits)

class GPUAllocationTests(TestCase):
    def setUp(self):
        self.devices=[{'uuid':'GPU-'+str(i),'index':i,'sm_count':114,'usable_memory_mib':24000,'ready':True} for i in range(4)]
    def test_rounding_and_whole_card(self):
        self.assertEqual(sm_count(114,30),34)
        self.assertEqual(sm_count(114,100),114)
    def test_multi_card_is_distinct_and_all_or_nothing(self):
        allocations=choose_devices(self.devices,[],[{'compute_percent':50,'memory_gib':8} for _ in range(2)])
        self.assertEqual(len({a['uuid'] for a in allocations}),2)
        self.assertEqual([a['sm_count'] for a in allocations],[56,56])
        self.assertIsNone(choose_devices(self.devices,[],[{'compute_percent':25,'memory_gib':4} for _ in range(5)]))
    def test_on_card_memory_and_compute_both_fit(self):
        initial=choose_devices(self.devices,[],[{'compute_percent':50,'memory_gib':19} for _ in range(1)])
        next_=choose_devices(self.devices,[initial],[{'compute_percent':25,'memory_gib':8} for _ in range(1)])
        self.assertNotEqual(initial[0]['uuid'],next_[0]['uuid'])
    def test_reuse_partial_card_before_opening_another(self):
        initial=choose_devices(self.devices,[],[{'compute_percent':25,'memory_gib':4} for _ in range(1)])
        next_=choose_devices(self.devices,[initial],[{'compute_percent':30,'memory_gib':4} for _ in range(1)])
        self.assertEqual(initial[0]['uuid'],next_[0]['uuid'])
    def test_offline_cards_are_never_allocated(self):
        self.devices[0]['ready']=False
        self.assertNotEqual(choose_devices(self.devices,[],[{'compute_percent':100,'memory_gib':8} for _ in range(1)])[0]['uuid'],'GPU-0')
    def test_fragmented_cards_do_not_satisfy_whole_card_request(self):
        leases=[[{'uuid':d['uuid'],'sm_count':56,'compute_percent':50,'memory_mib':8192}] for d in self.devices]
        self.assertIsNone(choose_devices(self.devices,leases,[{'compute_percent':100,'memory_gib':8} for _ in range(1)]))

    def test_mixed_shares_preserve_cuda_order_and_reuse_partial_gpu(self):
        holder=choose_devices(self.devices,[],[{'compute_percent':25,'memory_gib':2}])
        result=choose_devices(self.devices,[holder],[{'compute_percent':100,'memory_gib':8},{'compute_percent':50,'memory_gib':4}])
        self.assertEqual([(a['slot'],a['index'],a['sm_count'],a['memory_mib']) for a in result],[(0,1,114,8192),(1,0,56,4096)])

    def test_heterogeneous_vram_assignment_does_not_strand_the_large_request(self):
        devices=self.devices[:2];devices[1]['usable_memory_mib']=8*1024
        result=choose_devices(devices,[],[{'compute_percent':50,'memory_gib':4},{'compute_percent':50,'memory_gib':16}])
        self.assertEqual([a['index'] for a in result],[1,0])
        before=[d.copy() for d in devices]
        self.assertIsNone(choose_devices(devices,[],[{'compute_percent':50,'memory_gib':16}]*2))
        self.assertEqual(devices,before)
