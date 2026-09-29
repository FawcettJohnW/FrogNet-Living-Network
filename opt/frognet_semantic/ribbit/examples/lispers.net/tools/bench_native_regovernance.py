#!/usr/bin/env python3
# Re-governance latency: site deleted / restored on the Map-Server -> an independent ITR observes the native
# registration stop / start resolving. Derived at read: no registration is rewritten. FNW1, JSON interface included.
import json,subprocess,time,statistics,sys
from os import environ as E
RAM_HOST=E.get('RIBBIT_RAM_HOST','127.0.0.1'); RAM_PORT=E.get('RIBBIT_RAM_PORT','8788')
N=int(sys.argv[1]) if len(sys.argv)>1 else 50
def P(): return subprocess.Popen(['./ribbit_cpp/ribbit-lisp','--ram',RAM_HOST,RAM_PORT],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
def call(p,op,**a):
 p.stdin.write(json.dumps({'operation':op,'args':a})+'\n');p.stdin.flush();r=json.loads(p.stdout.readline())
 if not r['ok']: raise RuntimeError(r['error'])
 return r['result']
ms=P(); etr=P(); itr=P(); SITE,PF,R='198.36.0.0/16','198.36.1.0/24','192.0.2.136'
try:
 call(ms,'site.add',iid='0',prefix=SITE,group='',accept_more_specifics=True); call(ms,'resolver.wait_site',iid='0',prefix=SITE,group='',active=True)
 call(ms,'ms_governor.start',iid='0',group='',name='ms-rgb')
 call(etr,'etr.identity',name='etr-rgb',xtr_id='3d3d'); call(etr,'etr_liveness.start',interval_s=5,lifetime_s=60)
 call(etr,'database_mapping.add',iid='0',prefix=PF,group='',rloc_set=[R])
 assert call(itr,'resolver.wait',iid='0',prefix=PF,group='',present=True,rloc=R,timeout_s=5)=='good'
 off=[];on=[]
 for i in range(N):
  t0=time.perf_counter(); call(ms,'site.delete',iid='0',prefix=SITE,group='')
  assert call(itr,'resolver.wait',iid='0',prefix=PF,group='',present=False,timeout_s=5)=='good'; off.append((time.perf_counter()-t0)*1e6)
  t0=time.perf_counter(); call(ms,'site.add',iid='0',prefix=SITE,group='',accept_more_specifics=True)
  assert call(itr,'resolver.wait',iid='0',prefix=PF,group='',present=True,rloc=R,timeout_s=5)=='good'; on.append((time.perf_counter()-t0)*1e6)
 off.sort(); on.sort()
 print(f're-governance, N={N}: site deleted -> ITR stops resolving median {statistics.median(off):.0f} us (p90 {off[int(N*.9)]:.0f}); '
       f'site restored -> resolves median {statistics.median(on):.0f} us (p90 {on[int(N*.9)]:.0f})')
finally:
 for p_,op,kw in ((etr,'database_mapping.delete',dict(iid='0',prefix=PF,group='')),(ms,'site.delete',dict(iid='0',prefix=SITE,group=''))):
  try:call(p_,op,**kw)
  except Exception:pass
 for p_ in (ms,etr,itr): p_.terminate()
