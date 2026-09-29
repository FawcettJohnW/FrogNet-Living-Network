#!/usr/bin/env python3
# Registration between Ribbit participants as published truth (TRANSPORT-ARCHITECTURE.md): no Map-Register, no
# Map-Notify, no UDP. Each ETR's database mapping is its own truth under its identity; the Map-Server's governor
# participant holds every ETR's database variable directly (no copy), applies site policy, and publishes the governed
# registration and its decision; each ETR observes its decisions; an independent ITR resolves governed truth.
import json,subprocess
import os as _os; RAM_HOST=_os.environ.get('RIBBIT_RAM_HOST','127.0.0.1'); RAM_PORT=_os.environ.get('RIBBIT_RAM_PORT','8788')
BIN='./ribbit_cpp/ribbit-lisp'
class C:
 def __init__(self): self.p=subprocess.Popen([BIN,'--ram',RAM_HOST,RAM_PORT],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
 def call(self,op,**a):
  self.p.stdin.write(json.dumps({'operation':op,'args':a})+'\n');self.p.stdin.flush();r=json.loads(self.p.stdout.readline())
  if not r['ok']: raise RuntimeError(r['error'])
  return r['result']
 def close(self): self.p.terminate();self.p.wait(timeout=5)
def rl(x): return sorted(r['address'] for r in x.get('rlocs',[])) if isinstance(x,dict) else []
IID,G='0',''; P16,P17,OUT='198.27.0.0/16','198.27.128.0/17','198.99.0.0/16'
ms=C(); a=C(); b=C(); itr=C()
try:
 assert ms.call('site.add',iid=IID,prefix=P16,group=G,accept_more_specifics=True)=='good'
 assert ms.call('resolver.wait_site',iid=IID,prefix=P16,group=G,active=True)=='good'
 assert ms.call('ms_governor.start',iid=IID,group=G,name='ms-1')=='good'
 assert a.call('etr.identity',name='etr-a',xtr_id='0a0a0a0a')=='good'
 assert b.call('etr.identity',name='etr-b',xtr_id='0b0b0b0b')=='good'
 # v0.44: a native registration resolves only while its ETR publishes liveness
 assert a.call('etr_liveness.start',interval_s=1,lifetime_s=5)=='good' and b.call('etr_liveness.start',interval_s=1,lifetime_s=5)=='good'
 # ETR A: its database is its truth; it sends nothing to anyone
 for p,r in ((P16,'192.0.2.16'),(P17,'192.0.2.17'),(OUT,'192.0.2.99')):
  assert a.call('database_mapping.add',iid=IID,prefix=p,group=G,rloc_set=[r])=='good'
 assert a.call('etr_decision.wait',governor='ms-1',iid=IID,group=G,prefix=P16,decision='accepted',timeout_s=5)=='good'
 assert a.call('etr_decision.wait',governor='ms-1',iid=IID,group=G,prefix=P17,decision='accepted',timeout_s=5)=='good'
 assert a.call('etr_decision.wait',governor='ms-1',iid=IID,group=G,prefix=OUT,decision='rejected',timeout_s=5)=='good'
 # ETR B claims the same /17: merged per ETR
 assert b.call('database_mapping.add',iid=IID,prefix=P17,group=G,rloc_set=['192.0.2.117'])=='good'
 assert b.call('etr_decision.wait',governor='ms-1',iid=IID,group=G,prefix=P17,decision='accepted',timeout_s=5)=='good'
 assert itr.call('resolver.wait',iid=IID,prefix=P17,group=G,present=True,rloc='192.0.2.117',timeout_s=5)=='good'
 assert itr.call('resolver.wait',iid=IID,prefix=P17,group=G,present=True,rloc='192.0.2.17',timeout_s=5)=='good'
 assert rl(itr.call('resolution.get',iid=IID,prefix='198.27.200.1/32',group=G))==['192.0.2.117','192.0.2.17']
 assert rl(itr.call('resolution.get',iid=IID,prefix='198.27.1.1/32',group=G))==['192.0.2.16']
 assert itr.call('resolution.get',iid=IID,prefix='198.99.1.1/32',group=G)==[]
 # B withdraws: only B's contribution goes
 assert b.call('database_mapping.delete',iid=IID,prefix=P17,group=G)=='good'
 assert b.call('etr_decision.wait',governor='ms-1',iid=IID,group=G,prefix=P17,decision='withdrawn',timeout_s=5)=='good'
 assert itr.call('resolver.wait',iid=IID,prefix=P17,group=G,present=True,rloc='192.0.2.117',absent=True,timeout_s=5)=='good'
 assert rl(itr.call('resolution.get',iid=IID,prefix='198.27.200.1/32',group=G))==['192.0.2.17']
 # nothing was copied: no claim variable exists
 assert ms.call('ram.count',variable='etr-claim')==0
 print('PASS native registration: ETR truth governed in place by the Map-Server participant, decisions observed,',
       'merged and withdrawn per ETR, resolved by an independent ITR; no packet, no copy of ETR truth')
finally:
 for c,p in ((a,P16),(a,P17),(a,OUT)):
  try:c.call('database_mapping.delete',iid=IID,prefix=p,group=G)
  except Exception:pass
 try:ms.call('site.delete',iid=IID,prefix=P16,group=G)
 except Exception:pass
 for c in (ms,a,b,itr):
  try:c.close()
  except Exception:pass
