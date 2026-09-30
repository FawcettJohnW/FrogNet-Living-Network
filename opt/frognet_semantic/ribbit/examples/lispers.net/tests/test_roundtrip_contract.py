import os, unittest, uuid
from conformance.normalize import canonical

class RoundTripContract(unittest.TestCase):
    adapter=None
    def setUp(self):
        if os.getenv("LISP_ALLOW_MUTATION") != "1": self.skipTest("set LISP_ALLOW_MUTATION=1")
    def need(self,*ops):
        miss=[x for x in ops if x not in self.adapter.capabilities()]
        if miss:self.skipTest("adapter lacks "+",".join(miss))
    def test_map_resolver_add_get_delete(self):
        self.need("map_resolver.add","map_resolver.get","map_resolver.delete")
        address=os.environ.get("LISP_TEST_RESOLVER","192.0.2.254")
        try:
            a=self.adapter.call("map_resolver.add", address=address)
            if "map_resolver.wait" in self.adapter.capabilities(): self.assertEqual(self.adapter.call("map_resolver.wait",address=address,present=True),"good")
            g=self.adapter.call("map_resolver.get", address=address)
            self.assertIsNotNone(canonical(g)); self.assertNotEqual(canonical(g), [])
        finally:
            self.adapter.call("map_resolver.delete", address=address)
            if "map_resolver.wait" in self.adapter.capabilities(): self.assertEqual(self.adapter.call("map_resolver.wait",address=address,present=False),"good")
