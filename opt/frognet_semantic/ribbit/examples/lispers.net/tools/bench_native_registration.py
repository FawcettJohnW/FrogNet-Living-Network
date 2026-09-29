#!/usr/bin/env python3
# Native registration latency: ETR changes a database mapping -> governor (holding the ETR's variable) re-governs ->
# an independent ITR observes the new RLOC. N changes, FNW1, 1-core sandbox. Includes the JSON interface on both ends.
import json,subprocess,time,statistics,sys
import os as _os; RAM_HOST=_os.environ.get('RIBBIT_RAM_HOST','127.0.0.1'); RAM_PORT=_os.environ.get('RIBBIT_RAM_PORT','8788')
N=int(sys.argv[1]) if len(sys.argv)>1 else 100
def P(): return subprocess.Popen(['./ribbit_cpp/ribbit-lisp','--ram',RAM_HOST,RAM_PORT],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
def call(p,op,**a):
 p.stdin.write(json.dumps({'operation':op,'args':a})+'\n');p.stdin.flush();r=json.loads(p.stdout.readline())
 if not r['ok']: raise RuntimeError(r['error'])
 return r['result']
ms=P(); etr=P(); itr=P(); PF='198.33.1.0/24'
try:
 call(ms,'site.add',iid='0',prefix='198.33.0.0/16',group='',accept_more_specifics=True)
 call(ms,'resolver.wait_site',iid='0',prefix='198.33.0.0/16',group='',active=True)
 call(ms,'ms_governor.start',iid='0',group='',name='ms-bench')
 call(etr,'etr.identity',name='etr-bench',xtr_id='0e0e')
 call(etr,'etr_liveness.start',interval_s=5,lifetime_s=30)   # v0.44: native registrations need a live ETR
 call(etr,'database_mapping.add',iid='0',prefix=PF,group='',rloc_set=['192.0.2.1'])
 call(itr,'resolver.wait',iid='0',prefix=PF,group='',present=True,rloc='192.0.2.1',timeout_s=5)
 lat=[]
 for i in range(N):
  r=f'198.51.100.{1+i%250}' if i%2==0 else f'192.0.2.{1+i%250}'
  t0=time.perf_counter(); call(etr,'database_mapping.add',iid='0',prefix=PF,group='',rloc_set=[r])
  assert call(itr,'resolver.wait',iid='0',prefix=PF,group='',present=True,rloc=r,timeout_s=5)=='good'
  lat.append((time.perf_counter()-t0)*1e6)
 lat.sort(); print(f'native registration: database change -> governor -> ITR resolves new RLOC, N={N}: median {statistics.median(lat):.0f} us, p90 {lat[int(N*.9)]:.0f} us, max {lat[-1]:.0f} us')
finally:
 try:call(etr,'database_mapping.delete',iid='0',prefix=PF,group='')
 except Exception:pass
 try:call(ms,'site.delete',iid='0',prefix='198.33.0.0/16',group='')
 except Exception:pass
 for p in (ms,etr,itr): p.terminate()
