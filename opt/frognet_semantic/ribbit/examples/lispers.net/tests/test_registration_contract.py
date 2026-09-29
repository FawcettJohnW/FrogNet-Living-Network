import os, unittest
from conformance.normalize import canonical, rloc_addresses, mapping_prefix

class RegistrationContract(unittest.TestCase):
    adapter=None
    def need(self,*ops):
        miss=[x for x in ops if x not in self.adapter.capabilities()]
        if miss:self.skipTest("adapter lacks "+",".join(miss))
    def mutable(self):
        if os.getenv("LISP_ALLOW_MUTATION") != "1": self.skipTest("set LISP_ALLOW_MUTATION=1")
    def test_registration_resolves_registered_rlocs(self):
        self.need("registration.put","resolution.get","registration.delete"); self.mutable()
        p="198.18.10.0/24"
        try:
            self.assertEqual(self.adapter.call("registration.put",iid="0",prefix=p,group="",ttl=60,rloc_set=[{"address":"192.0.2.10","priority":1,"weight":70},{"address":"192.0.2.11","priority":1,"weight":30}]),"good")
            got=canonical(self.adapter.call("resolution.get",iid="0",prefix="198.18.10.7/32",group=""))
            self.assertEqual(mapping_prefix(got),p)
            self.assertEqual(set(rloc_addresses(got)),{"192.0.2.10","192.0.2.11"})
        finally:
            try:self.adapter.call("registration.delete",iid="0",prefix=p,group="")
            except Exception:pass
    def test_registration_replaces_locator_set(self):
        self.need("registration.put","resolution.get","registration.delete"); self.mutable()
        p="198.18.20.0/24"
        try:
            self.adapter.call("registration.put",iid="0",prefix=p,group="",ttl=60,rloc_set=["192.0.2.20"])
            self.adapter.call("registration.put",iid="0",prefix=p,group="",ttl=60,rloc_set=["192.0.2.21"])
            if "resolver.wait" in self.adapter.capabilities(): self.assertEqual(self.adapter.call("resolver.wait",iid="0",prefix=p,group="",present=True,rloc="192.0.2.21"),"good")
            got=canonical(self.adapter.call("resolution.get",iid="0",prefix="198.18.20.1/32",group=""))
            self.assertEqual(rloc_addresses(got),["192.0.2.21"])
        finally:
            try:self.adapter.call("registration.delete",iid="0",prefix=p,group="")
            except Exception:pass
    def test_zero_ttl_deregisters(self):
        self.need("registration.put","resolution.get","registration.delete"); self.mutable()
        p="198.18.30.0/24"
        try:
            self.adapter.call("registration.put",iid="0",prefix=p,group="",ttl=60,source="192.0.2.30",rloc_set=["192.0.2.30"])
            self.adapter.call("registration.put",iid="0",prefix=p,group="",ttl=0,source="192.0.2.30",rloc_set=["192.0.2.30"])
            if "resolver.wait" in self.adapter.capabilities():
                self.assertEqual(self.adapter.call("resolver.wait",iid="0",prefix=p,group="",present=False),"good")
            got=canonical(self.adapter.call("resolution.get",iid="0",prefix="198.18.30.1/32",group=""))
            self.assertTrue(got in (None,[],{}),got)
        finally:
            try:self.adapter.call("registration.delete",iid="0",prefix=p,group="")
            except Exception:pass
    def test_zero_ttl_from_non_rloc_is_ignored(self):
        self.need("registration.put","resolution.get","registration.delete"); self.mutable()
        p="198.18.31.0/24"
        try:
            self.adapter.call("registration.put",iid="0",prefix=p,group="",ttl=60,source="192.0.2.31",rloc_set=["192.0.2.31"])
            result=self.adapter.call("registration.put",iid="0",prefix=p,group="",ttl=0,source="192.0.2.99",rloc_set=["192.0.2.31"])
            self.assertIn(result,("ignored","good"))
            got=canonical(self.adapter.call("resolution.get",iid="0",prefix="198.18.31.1/32",group=""))
            self.assertEqual(rloc_addresses(got),["192.0.2.31"])
        finally:
            try:self.adapter.call("registration.delete",iid="0",prefix=p,group="")
            except Exception:pass
    def test_merge_registration_unions_independent_xtrs(self):
        self.need("registration.put","resolution.get","registration.delete"); self.mutable()
        p="198.18.50.0/24"
        try:
            self.adapter.call("registration.put",iid="0",prefix=p,group="",ttl=60,merge=True,site_id="77",xtr_id="xtr-a",rloc_set=["192.0.2.50"])
            self.adapter.call("registration.put",iid="0",prefix=p,group="",ttl=60,merge=True,site_id="77",xtr_id="xtr-b",rloc_set=["192.0.2.51"])
            if "resolver.wait" in self.adapter.capabilities():
                self.assertEqual(self.adapter.call("resolver.wait",iid="0",prefix=p,group="",present=True,rloc="192.0.2.50"),"good")
                self.assertEqual(self.adapter.call("resolver.wait",iid="0",prefix=p,group="",present=True,rloc="192.0.2.51"),"good")
            got=canonical(self.adapter.call("resolution.get",iid="0",prefix="198.18.50.1/32",group=""))
            self.assertEqual(set(rloc_addresses(got)),{"192.0.2.50","192.0.2.51"})
        finally:
            try:self.adapter.call("registration.delete",iid="0",prefix=p,group="")
            except Exception:pass
    def test_merge_replacement_changes_only_own_xtr_truth(self):
        self.need("registration.put","resolution.get","registration.delete"); self.mutable()
        p="198.18.51.0/24"
        try:
            self.adapter.call("registration.put",iid="0",prefix=p,group="",ttl=60,merge=True,site_id="78",xtr_id="xtr-a",rloc_set=["192.0.2.52"])
            self.adapter.call("registration.put",iid="0",prefix=p,group="",ttl=60,merge=True,site_id="78",xtr_id="xtr-b",rloc_set=["192.0.2.53"])
            self.adapter.call("registration.put",iid="0",prefix=p,group="",ttl=60,merge=True,site_id="78",xtr_id="xtr-a",rloc_set=["192.0.2.54"])
            if "resolver.wait" in self.adapter.capabilities():
                self.assertEqual(self.adapter.call("resolver.wait",iid="0",prefix=p,group="",present=True,rloc="192.0.2.53"),"good")
                self.assertEqual(self.adapter.call("resolver.wait",iid="0",prefix=p,group="",present=True,rloc="192.0.2.54"),"good")
            got=canonical(self.adapter.call("resolution.get",iid="0",prefix="198.18.51.1/32",group=""))
            self.assertEqual(set(rloc_addresses(got)),{"192.0.2.53","192.0.2.54"})
        finally:
            try:self.adapter.call("registration.delete",iid="0",prefix=p,group="")
            except Exception:pass
    def test_refresh_cannot_change_locator_set(self):
        self.need("registration.put","resolution.get","registration.delete"); self.mutable()
        p="198.18.40.0/24"
        try:
            self.adapter.call("registration.put",iid="0",prefix=p,group="",ttl=60,rloc_set=["192.0.2.40"])
            result=self.adapter.call("registration.put",iid="0",prefix=p,group="",ttl=60,refresh=True,rloc_set=["192.0.2.41"])
            self.assertIn(result,("rejected","good")) # control API adapters may expose rejection as successful transport
            got=canonical(self.adapter.call("resolution.get",iid="0",prefix="198.18.40.1/32",group=""))
            self.assertEqual(rloc_addresses(got),["192.0.2.40"])
        finally:
            try:self.adapter.call("registration.delete",iid="0",prefix=p,group="")
            except Exception:pass

    def test_nonmerge_replaces_prior_merged_truths(self):
        self.need("registration.put","resolution.get","registration.delete"); self.mutable()
        p="198.18.52.0/24"
        try:
            self.adapter.call("registration.put",iid="0",prefix=p,group="",ttl=60,merge=True,xtr_id="xtr-a",rloc_set=["192.0.2.55"])
            self.adapter.call("registration.put",iid="0",prefix=p,group="",ttl=60,merge=True,xtr_id="xtr-b",rloc_set=["192.0.2.56"])
            self.adapter.call("registration.put",iid="0",prefix=p,group="",ttl=60,merge=False,rloc_set=["192.0.2.57"])
            if "resolver.wait" in self.adapter.capabilities():
                self.assertEqual(self.adapter.call("resolver.wait",iid="0",prefix=p,group="",present=True,rloc="192.0.2.57"),"good")
            got=canonical(self.adapter.call("resolution.get",iid="0",prefix="198.18.52.1/32",group=""))
            self.assertEqual(rloc_addresses(got),["192.0.2.57"])
        finally:
            try:self.adapter.call("registration.delete",iid="0",prefix=p,group="")
            except Exception:pass

# Added v0.5: control-derived registration lifetime semantics.
def _expiry_tests():
    import time
    def test_ttl_seconds_encoding_expires_without_reaper(self):
        self.need("registration.put","resolution.get","registration.delete"); self.mutable()
        p="198.18.60.0/24"
        try:
            # Dino's EID-record TTL bit 31 means low 31 bits are seconds.
            self.adapter.call("registration.put",iid="0",prefix=p,group="",ttl=0x80000001,use_register_ttl=True,rloc_set=["192.0.2.60"])
            live=canonical(self.adapter.call("resolution.get",iid="0",prefix="198.18.60.1/32",group=""))
            self.assertEqual(rloc_addresses(live),["192.0.2.60"])
            time.sleep(2.05)
            dead=canonical(self.adapter.call("resolution.get",iid="0",prefix="198.18.60.1/32",group=""))
            self.assertTrue(dead in (None,[],{}),dead)
        finally:
            try:self.adapter.call("registration.delete",iid="0",prefix=p,group="")
            except Exception:pass
    def test_refresh_extends_own_registration_lifetime(self):
        self.need("registration.put","resolution.get","registration.delete"); self.mutable()
        p="198.18.61.0/24"
        try:
            args=dict(iid="0",prefix=p,group="",ttl=0x80000002,use_register_ttl=True,merge=True,xtr_id="expiry-a",rloc_set=["192.0.2.61"])
            self.adapter.call("registration.put",**args)
            time.sleep(1.05)
            self.adapter.call("registration.put",refresh=True,**args)
            time.sleep(1.05)
            live=canonical(self.adapter.call("resolution.get",iid="0",prefix="198.18.61.1/32",group=""))
            self.assertEqual(rloc_addresses(live),["192.0.2.61"])
            time.sleep(1.20)
            dead=canonical(self.adapter.call("resolution.get",iid="0",prefix="198.18.61.1/32",group=""))
            self.assertTrue(dead in (None,[],{}),dead)
        finally:
            try:self.adapter.call("registration.delete",iid="0",prefix=p,group="")
            except Exception:pass
    RegistrationContract.test_ttl_seconds_encoding_expires_without_reaper=test_ttl_seconds_encoding_expires_without_reaper
    RegistrationContract.test_refresh_extends_own_registration_lifetime=test_refresh_extends_own_registration_lifetime
_expiry_tests()

# Site fixtures (v0.42). These tests register without configuring a site; they predate site authorization (v0.6)
# and passed only because an empty site table skipped authorization — a bug fixed in v0.42. Each now runs with a
# site covering exactly what it registers; the site is removed afterwards and its removal observed.
def _site_setup(tc,sites):
    tc._fixture_sites=[]
    if not sites or 'site.add' not in tc.adapter.capabilities() or os.getenv('LISP_ALLOW_MUTATION')!='1': return
    for iid,prefix,ams in sites:
        tc.adapter.call('site.add',iid=iid,prefix=prefix,group='',accept_more_specifics=ams)
        tc._fixture_sites.append((iid,prefix))
        if 'resolver.wait_site' in tc.adapter.capabilities():
            assert tc.adapter.call('resolver.wait_site',iid=iid,prefix=prefix,group='',active=True)=='good'
def _site_teardown(tc):
    for iid,prefix in getattr(tc,'_fixture_sites',[]):
        try:
            tc.adapter.call('site.delete',iid=iid,prefix=prefix,group='')
            if 'resolver.wait_site' in tc.adapter.capabilities():
                tc.adapter.call('resolver.wait_site',iid=iid,prefix=prefix,group='',active=False)
        except Exception: pass

RegistrationContract.FIXTURE_SITES={'test_registration_resolves_registered_rlocs': [('0', '198.18.10.0/24', False)], 'test_registration_replaces_locator_set': [('0', '198.18.20.0/24', False)], 'test_zero_ttl_deregisters': [('0', '198.18.30.0/24', False)], 'test_zero_ttl_from_non_rloc_is_ignored': [('0', '198.18.31.0/24', False)], 'test_merge_registration_unions_independent_xtrs': [('0', '198.18.50.0/24', False)], 'test_merge_replacement_changes_only_own_xtr_truth': [('0', '198.18.51.0/24', False)], 'test_refresh_cannot_change_locator_set': [('0', '198.18.40.0/24', False)], 'test_nonmerge_replaces_prior_merged_truths': [('0', '198.18.52.0/24', False)], 'test_ttl_seconds_encoding_expires_without_reaper': [('0', '198.18.60.0/24', False)], 'test_refresh_extends_own_registration_lifetime': [('0', '198.18.61.0/24', False)]}
RegistrationContract.setUp=lambda self:_site_setup(self,RegistrationContract.FIXTURE_SITES.get(self._testMethodName))
RegistrationContract.tearDown=lambda self:_site_teardown(self)
