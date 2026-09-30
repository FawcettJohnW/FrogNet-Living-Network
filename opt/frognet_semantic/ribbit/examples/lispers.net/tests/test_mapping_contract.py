import os, unittest
from conformance.normalize import canonical, rloc_addresses, mapping_prefix

class MappingContract(unittest.TestCase):
    adapter=None
    def need(self,*ops):
        miss=[x for x in ops if x not in self.adapter.capabilities()]
        if miss:self.skipTest("adapter lacks "+",".join(miss))
    def test_longest_prefix_match(self):
        self.need("map_cache.add","map_cache.get","map_cache.delete")
        if os.getenv("LISP_ALLOW_MUTATION") != "1": self.skipTest("set LISP_ALLOW_MUTATION=1")
        entries=[("198.51.100.0/24","192.0.2.24"),("198.51.100.128/25","192.0.2.25")]
        try:
            for p,r in entries:
                self.assertEqual(self.adapter.call("map_cache.add",iid="0",prefix=p,group="",rloc_set=[r]),"good")
                if "map_cache.wait" in self.adapter.capabilities(): self.assertEqual(self.adapter.call("map_cache.wait",iid="0",prefix=p,group="",present=True),"good")
            got=canonical(self.adapter.call("map_cache.get",iid="0",prefix="198.51.100.200/32",group=""))
            self.assertTrue(got not in (None,[],{}))
            self.assertEqual(mapping_prefix(got),"198.51.100.128/25")
            self.assertEqual(rloc_addresses(got),["192.0.2.25"])
        finally:
            for p,_ in entries:
                try:self.adapter.call("map_cache.delete",iid="0",prefix=p,group="")
                except Exception:pass
    def test_iid_isolation(self):
        self.need("map_cache.add","map_cache.get","map_cache.delete")
        if os.getenv("LISP_ALLOW_MUTATION") != "1": self.skipTest("set LISP_ALLOW_MUTATION=1")
        p="203.0.113.0/24"
        try:
            self.adapter.call("map_cache.add",iid="101",prefix=p,group="",rloc_set=["192.0.2.101"])
            self.adapter.call("map_cache.add",iid="202",prefix=p,group="",rloc_set=["192.0.2.202"])
            if "map_cache.wait" in self.adapter.capabilities():
                self.assertEqual(self.adapter.call("map_cache.wait",iid="101",prefix=p,group="",present=True),"good")
                self.assertEqual(self.adapter.call("map_cache.wait",iid="202",prefix=p,group="",present=True),"good")
            a=canonical(self.adapter.call("map_cache.get",iid="101",prefix="203.0.113.7/32",group=""))
            b=canonical(self.adapter.call("map_cache.get",iid="202",prefix="203.0.113.7/32",group=""))
            self.assertEqual(rloc_addresses(a),["192.0.2.101"])
            self.assertEqual(rloc_addresses(b),["192.0.2.202"])
        finally:
            for iid in ("101","202"):
                try:self.adapter.call("map_cache.delete",iid=iid,prefix=p,group="")
                except Exception:pass

    def test_map_cache_wait_observes_a_specific_rloc(self):
        # Observed convergence for replacement: a reader must be able to wait for a specific value, and a
        # value that is not there must not satisfy the wait.
        self.need("map_cache.add","map_cache.wait","map_cache.delete")
        if os.getenv("LISP_ALLOW_MUTATION") != "1": self.skipTest("set LISP_ALLOW_MUTATION=1")
        p="198.18.79.0/24"
        try:
            self.assertEqual(self.adapter.call("map_cache.add",iid="79",prefix=p,group="",rloc_set=["192.0.2.1"]),"good")
            self.assertEqual(self.adapter.call("map_cache.wait",iid="79",prefix=p,group="",present=True,rloc="192.0.2.1"),"good")
            self.assertEqual(self.adapter.call("map_cache.wait",iid="79",prefix=p,group="",present=True,rloc="192.0.2.2",timeout_s=0.5),"timeout")
            self.assertEqual(self.adapter.call("map_cache.add",iid="79",prefix=p,group="",rloc_set=["192.0.2.2"]),"good")
            self.assertEqual(self.adapter.call("map_cache.wait",iid="79",prefix=p,group="",present=True,rloc="192.0.2.2"),"good")
            self.assertEqual(rloc_addresses(canonical(self.adapter.call("map_cache.get",iid="79",prefix="198.18.79.9/32",group=""))),["192.0.2.2"])
        finally:
            try:self.adapter.call("map_cache.delete",iid="79",prefix=p,group="")
            except Exception:pass

class DatabaseMappingContract(unittest.TestCase):
    adapter=None
    def need(self,*ops):
        miss=[x for x in ops if x not in self.adapter.capabilities()]
        if miss:self.skipTest("adapter lacks "+",".join(miss))
    def test_database_mapping_longest_prefix_iid_and_withdrawal(self):
        self.need("database_mapping.add","database_mapping.get","database_mapping.delete")
        if os.getenv("LISP_ALLOW_MUTATION") != "1": self.skipTest("set LISP_ALLOW_MUTATION=1")
        entries=[("77","198.18.0.0/16","192.0.2.16"),("77","198.18.128.0/17","192.0.2.17"),("88","198.18.128.0/17","192.0.2.88")]
        try:
            for iid,p,r in entries:
                self.assertEqual(self.adapter.call("database_mapping.add",iid=iid,prefix=p,group="",rloc_set=[r]),"good")
                if "database_mapping.wait" in self.adapter.capabilities():
                    self.assertEqual(self.adapter.call("database_mapping.wait",iid=iid,prefix=p,group="",present=True),"good")
            got=canonical(self.adapter.call("database_mapping.get",iid="77",prefix="198.18.200.1/32",group=""))
            self.assertEqual(mapping_prefix(got),"198.18.128.0/17")
            self.assertEqual(rloc_addresses(got),["192.0.2.17"])
            other=canonical(self.adapter.call("database_mapping.get",iid="88",prefix="198.18.200.1/32",group=""))
            self.assertEqual(rloc_addresses(other),["192.0.2.88"])
            self.assertEqual(self.adapter.call("database_mapping.delete",iid="77",prefix="198.18.128.0/17",group=""),"good")
            if "database_mapping.wait" in self.adapter.capabilities():
                self.assertEqual(self.adapter.call("database_mapping.wait",iid="77",prefix="198.18.128.0/17",group="",present=False),"good")
            fallback=canonical(self.adapter.call("database_mapping.get",iid="77",prefix="198.18.200.1/32",group=""))
            self.assertEqual(mapping_prefix(fallback),"198.18.0.0/16")
            self.assertEqual(rloc_addresses(fallback),["192.0.2.16"])
        finally:
            for iid,p,_ in entries:
                try:self.adapter.call("database_mapping.delete",iid=iid,prefix=p,group="")
                except Exception:pass
