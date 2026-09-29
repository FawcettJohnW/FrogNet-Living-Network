import os, unittest
from conformance.normalize import canonical, rloc_addresses
class SiteAuthorizationContract(unittest.TestCase):
    adapter=None
    def need(self,*ops):
        miss=[x for x in ops if x not in self.adapter.capabilities()]
        if miss:self.skipTest('adapter lacks '+','.join(miss))
    def mutable(self):
        if os.getenv('LISP_ALLOW_MUTATION')!='1':self.skipTest('set LISP_ALLOW_MUTATION=1')
    def test_unknown_site_registration_rejected_without_state_mutation(self):
        self.need('site.add','site.delete','registration.put','resolution.get');self.mutable()
        site='198.19.0.0/24';bad='198.19.1.0/24'
        try:
            self.adapter.call('site.add',iid='0',prefix=site,group='',accept_more_specifics=False)
            if 'resolver.wait_site' in self.adapter.capabilities():
                self.assertEqual(self.adapter.call('resolver.wait_site',iid='0',prefix=site,group='',active=True),'good')
            self.assertEqual(self.adapter.call('registration.put',iid='0',prefix=bad,group='',ttl=60,rloc_set=['192.0.2.80']),'unauthorized')
            self.assertTrue(canonical(self.adapter.call('resolution.get',iid='0',prefix='198.19.1.1/32',group='')) in (None,[],{}))
        finally:
            self.adapter.call('site.delete',iid='0',prefix=site,group='')
            if 'resolver.wait_site' in self.adapter.capabilities(): self.assertEqual(self.adapter.call('resolver.wait_site',iid='0',prefix=site,group='',active=False),'good')
    def test_exact_configured_site_authorized(self):
        self.need('site.add','site.delete','registration.put','registration.delete','resolution.get');self.mutable()
        p='198.19.2.0/24'
        try:
            self.adapter.call('site.add',iid='0',prefix=p,group='',accept_more_specifics=False)
            if 'resolver.wait_site' in self.adapter.capabilities():
                self.assertEqual(self.adapter.call('resolver.wait_site',iid='0',prefix=p,group='',active=True),'good')
            self.assertEqual(self.adapter.call('registration.put',iid='0',prefix=p,group='',ttl=60,rloc_set=['192.0.2.81']),'good')
            if 'resolver.wait' in self.adapter.capabilities(): self.assertEqual(self.adapter.call('resolver.wait',iid='0',prefix=p,group='',present=True),'good')
            self.assertEqual(rloc_addresses(self.adapter.call('resolution.get',iid='0',prefix='198.19.2.1/32',group='')),['192.0.2.81'])
        finally:
            try:self.adapter.call('registration.delete',iid='0',prefix=p,group='')
            finally:
                self.adapter.call('site.delete',iid='0',prefix=p,group='')
                if 'resolver.wait_site' in self.adapter.capabilities(): self.assertEqual(self.adapter.call('resolver.wait_site',iid='0',prefix=p,group='',active=False),'good')
    def test_accept_more_specifics_authorizes_child(self):
        self.need('site.add','site.delete','registration.put','registration.delete','resolution.get');self.mutable()
        parent='198.19.4.0/22';child='198.19.5.0/24'
        try:
            self.adapter.call('site.add',iid='0',prefix=parent,group='',accept_more_specifics=True)
            if 'resolver.wait_site' in self.adapter.capabilities():
                self.assertEqual(self.adapter.call('resolver.wait_site',iid='0',prefix=parent,group='',active=True),'good')
            self.assertEqual(self.adapter.call('registration.put',iid='0',prefix=child,group='',ttl=60,rloc_set=['192.0.2.82']),'good')
            if 'resolver.wait' in self.adapter.capabilities(): self.assertEqual(self.adapter.call('resolver.wait',iid='0',prefix=child,group='',present=True),'good')
            self.assertEqual(rloc_addresses(self.adapter.call('resolution.get',iid='0',prefix='198.19.5.7/32',group='')),['192.0.2.82'])
        finally:
            try:self.adapter.call('registration.delete',iid='0',prefix=child,group='')
            finally:
                self.adapter.call('site.delete',iid='0',prefix=parent,group='')
                if 'resolver.wait_site' in self.adapter.capabilities(): self.assertEqual(self.adapter.call('resolver.wait_site',iid='0',prefix=parent,group='',active=False),'good')
    def test_more_specific_rejected_without_ams(self):
        self.need('site.add','site.delete','registration.put','resolution.get');self.mutable()
        parent='198.19.8.0/22';child='198.19.9.0/24'
        try:
            self.adapter.call('site.add',iid='0',prefix=parent,group='',accept_more_specifics=False)
            if 'resolver.wait_site' in self.adapter.capabilities():
                self.assertEqual(self.adapter.call('resolver.wait_site',iid='0',prefix=parent,group='',active=True),'good')
            self.assertEqual(self.adapter.call('registration.put',iid='0',prefix=child,group='',ttl=60,rloc_set=['192.0.2.83']),'unauthorized')
        finally:
            self.adapter.call('site.delete',iid='0',prefix=parent,group='')
            if 'resolver.wait_site' in self.adapter.capabilities(): self.assertEqual(self.adapter.call('resolver.wait_site',iid='0',prefix=parent,group='',active=False),'good')

def _wire_auth_gate(self):
    self.need('site.add','site.delete','wire.register4','resolution.get','registration.delete');self.mutable()
    from tests.test_wire_contract import auth_reg4
    p='198.18.70.0/24'
    try:
        self.adapter.call('site.add',iid='0',prefix=p,group='',accept_more_specifics=False,key_id=9,password='site secret')
        if 'resolver.wait_site' in self.adapter.capabilities():
            self.assertEqual(self.adapter.call('resolver.wait_site',iid='0',prefix=p,group='',active=True),'good')
        bad=auth_reg4('wrong secret',1,9)
        self.assertEqual(self.adapter.call('wire.register4',hex=bad.hex()),'auth-failed')
        self.assertTrue(canonical(self.adapter.call('resolution.get',iid='0',prefix='198.18.70.1/32',group='')) in (None,[],{}))
        good=auth_reg4('site secret',1,9)
        self.assertEqual(self.adapter.call('wire.register4',hex=good.hex()),'good')
        if 'resolver.wait' in self.adapter.capabilities(): self.assertEqual(self.adapter.call('resolver.wait',iid='0',prefix=p,group='',present=True),'good')
        self.assertEqual(rloc_addresses(self.adapter.call('resolution.get',iid='0',prefix='198.18.70.1/32',group='')),['192.0.2.70'])
    finally:
        try:self.adapter.call('registration.delete',iid='0',prefix=p,group='')
        finally:
            self.adapter.call('site.delete',iid='0',prefix=p,group='')
            if 'resolver.wait_site' in self.adapter.capabilities(): self.assertEqual(self.adapter.call('resolver.wait_site',iid='0',prefix=p,group='',active=False),'good')
SiteAuthorizationContract.test_wire_authentication_gates_state_mutation=_wire_auth_gate

def _no_authorizing_site_rejected(self):
    # CONTRACT (v0.6): a registration for an unknown/unconfigured site is not accepted. That holds when NO site
    # is configured at all, and after the LAST site is deleted — removing policy must not open the Map-Server.
    # Control: lisp_process_map_register skips a record with no site ("Site not found").
    self.need('site.add','site.delete','registration.put','resolution.get');self.mutable()
    iid='4242'; site='198.42.0.0/16'; p='198.42.1.0/24'
    r=self.adapter.call('registration.put',iid=iid,prefix=p,group='',ttl=60,rloc_set=['192.0.2.42'])
    self.assertEqual(r,'unauthorized')
    self.assertEqual(self.adapter.call('resolution.get',iid=iid,prefix='198.42.1.1/32',group=''),[])
    self.adapter.call('site.add',iid=iid,prefix=site,group='',accept_more_specifics=True)
    if 'resolver.wait_site' in self.adapter.capabilities():
        self.assertEqual(self.adapter.call('resolver.wait_site',iid=iid,prefix=site,group='',active=True),'good')
    self.adapter.call('site.delete',iid=iid,prefix=site,group='')
    if 'resolver.wait_site' in self.adapter.capabilities():
        self.assertEqual(self.adapter.call('resolver.wait_site',iid=iid,prefix=site,group='',active=False),'good')
    try:
        r=self.adapter.call('registration.put',iid=iid,prefix=p,group='',ttl=60,rloc_set=['192.0.2.42'])
        self.assertEqual(r,'unauthorized')
    finally:
        try:self.adapter.call('registration.delete',iid=iid,prefix=p,group='')
        except Exception:pass
SiteAuthorizationContract.test_register_with_no_authorizing_site_is_rejected=_no_authorizing_site_rejected
