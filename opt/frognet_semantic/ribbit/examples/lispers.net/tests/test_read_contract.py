import unittest
from conformance.normalize import canonical

class ReadContract(unittest.TestCase):
    adapter = None
    def need(self, op):
        if op not in self.adapter.capabilities(): self.skipTest(f"adapter lacks {op}")
    def test_system_is_mapping(self):
        self.need("system.get"); x=canonical(self.adapter.call("system.get")); self.assertIsInstance(x, dict)
    def test_map_cache_is_collection(self):
        self.need("map_cache.list"); x=canonical(self.adapter.call("map_cache.list")); self.assertIsInstance(x, (list,dict))
    def test_site_cache_is_collection(self):
        self.need("site_cache.list"); x=canonical(self.adapter.call("site_cache.list")); self.assertIsInstance(x, (list,dict))


class TransportStatsContract(unittest.TestCase):
    """Bytes on the wire are the evidence for transport claims: the participant must expose its own FNW1 session
    counters (bytes out/in, raw/REPEAT/SAME requests). Local backend reports backend 'local' and zeros."""
    adapter=None
    def test_transport_stats_are_exposed(self):
        if 'transport.stats' not in self.adapter.capabilities(): self.skipTest('adapter lacks transport.stats')
        st=self.adapter.call('transport.stats')
        for k in ('backend','bytes_out','bytes_in','raw','repeat','same'): self.assertIn(k,st)
        self.assertIn(st['backend'],('local','fnw1'))
        if st['backend']=='fnw1': self.assertGreater(st['bytes_out'],0)
