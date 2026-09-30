import os,unittest
from conformance.normalize import canonical, rloc_addresses, mapping_prefix
class DDTContract(unittest.TestCase):
    adapter=None
    def need(self,*ops):
        miss=[x for x in ops if x not in self.adapter.capabilities()]
        if miss:self.skipTest('adapter lacks '+','.join(miss))
    def mutable(self):
        if os.getenv('LISP_ALLOW_MUTATION')!='1':self.skipTest('set LISP_ALLOW_MUTATION=1')
    def test_delegation_longest_prefix_referral_set(self):
        self.need('ddt.add','ddt.get','ddt.delete');self.mutable();p1='10.0.0.0/8';p2='10.20.0.0/16'
        try:
            self.adapter.call('ddt.add',iid='0',prefix=p1,group='',rloc_set=['192.0.2.90'])
            self.adapter.call('ddt.add',iid='0',prefix=p2,group='',rloc_set=['192.0.2.91','192.0.2.92'])
            if 'resolver.wait_ddt' in self.adapter.capabilities():
                self.assertEqual(self.adapter.call('resolver.wait_ddt',iid='0',prefix=p1,group='',present=True),'good')
                self.assertEqual(self.adapter.call('resolver.wait_ddt',iid='0',prefix=p2,group='',present=True),'good')
            got=canonical(self.adapter.call('ddt.get',iid='0',prefix='10.20.30.1/32',group=''))
            self.assertEqual(mapping_prefix(got),p2);self.assertEqual(set(rloc_addresses(got)),{'192.0.2.91','192.0.2.92'})
        finally:
            self.adapter.call('ddt.delete',iid='0',prefix=p2,group='');self.adapter.call('ddt.delete',iid='0',prefix=p1,group='')
    def test_delegations_are_iid_isolated(self):
        self.need('ddt.add','ddt.get','ddt.delete');self.mutable();p='10.30.0.0/16'
        try:
            self.adapter.call('ddt.add',iid='7',prefix=p,group='',rloc_set=['192.0.2.93'])
            if 'resolver.wait_ddt' in self.adapter.capabilities():
                self.assertEqual(self.adapter.call('resolver.wait_ddt',iid='7',prefix=p,group='',present=True),'good')
            self.assertTrue(canonical(self.adapter.call('ddt.get',iid='8',prefix='10.30.1.1/32',group='')) in (None,[],{}))
            self.assertEqual(rloc_addresses(self.adapter.call('ddt.get',iid='7',prefix='10.30.1.1/32',group='')),['192.0.2.93'])
        finally:self.adapter.call('ddt.delete',iid='7',prefix=p,group='')
