#!/usr/bin/env python3
import json,subprocess
import os as _os; RAM_HOST=_os.environ.get('RIBBIT_RAM_HOST','127.0.0.1'); RAM_PORT=_os.environ.get('RIBBIT_RAM_PORT','8788')
BIN='./ribbit_cpp/ribbit-lisp'
class C:
 def __init__(self): self.p=subprocess.Popen([BIN,'--ram',RAM_HOST,RAM_PORT],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
 def call(self,op,**a):
  self.p.stdin.write(json.dumps({'operation':op,'args':a})+'\n');self.p.stdin.flush();r=json.loads(self.p.stdout.readline());
  if not r['ok']: raise RuntimeError(r['error'])
  return r['result']
 def close(self): self.p.terminate();self.p.wait(timeout=5)
def rs(x): return [r['address'] for r in x.get('rlocs',[])] if isinstance(x,dict) else []
r=C();w=C()
try:
 iid='9201';g='held-map-cache';p24='198.51.100.0/24';p25='198.51.100.128/25';t='198.51.100.200/32'
 assert r.call('map_cache.get',iid=iid,prefix=t,group=g)==[]
 assert w.call('map_cache.add',iid=iid,prefix=p24,group=g,rloc_set=['192.0.2.24'])=='good'
 assert r.call('map_cache.wait',iid=iid,prefix=p24,group=g,present=True)=='good'
 assert rs(r.call('map_cache.get',iid=iid,prefix=t,group=g))==['192.0.2.24']
 assert r.call('resolver.stats')['map_cache_reads']==0
 assert w.call('map_cache.add',iid=iid,prefix=p25,group=g,rloc_set=['192.0.2.25'])=='good'
 assert r.call('map_cache.wait',iid=iid,prefix=p25,group=g,present=True)=='good'
 for _ in range(5):
  assert rs(r.call('map_cache.get',iid=iid,prefix=t,group=g))==['192.0.2.25']
  assert r.call('resolver.stats')['map_cache_reads']==0
 assert w.call('map_cache.delete',iid=iid,prefix=p25,group=g)=='good'
 assert r.call('map_cache.wait',iid=iid,prefix=p25,group=g,present=False)=='good'
 assert rs(r.call('map_cache.get',iid=iid,prefix=t,group=g))==['192.0.2.24']
 assert r.call('resolver.stats')['map_cache_reads']==0
 print('PASS held map-cache dynamic /N, LPM, zero warmed reads, inactive withdrawal fallback')
finally:
 # leave the shared memory as found: a later run against the same server (RIBBIT_RAM_EXTERNAL=1) starts from empty
 for _p in ('198.51.100.0/24','198.51.100.128/25'):
  try:w.call('map_cache.delete',iid='9201',prefix=_p,group='held-map-cache')
  except Exception:pass
 r.close();w.close()
