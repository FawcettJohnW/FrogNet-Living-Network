#!/usr/bin/env python3
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#                                                              #
#  SPDX-License-Identifier: GPL-2.0-only                       #
#                                                              #
#  This program is free software; you can redistribute it      #
#  and/or modify it under the terms of the GNU General Public  #
#  License as published by the Free Software Foundation;       #
#  version 2 of the License, and no other version.             #
#                                                              #
#  This program is distributed in the hope that it will be     #
#  useful, but WITHOUT ANY WARRANTY; without even the implied  #
#  warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR     #
#  PURPOSE.  See the GNU General Public License for details.   #
#                                                              #
#  See COPYRIGHT and LICENSE at the root of this tree.         #
################################################################
# Liveness of native registrations (v0.44). A governed registration lives only while its ETR is alive: each ETR
# publishes ONE liveness cell (etr-live/<xtr_id>: alive_until), refreshed by a heartbeat participant; resolvers hold
# the liveness cells and treat a native registration as absent once its ETR's alive_until has passed. No reaper, no
# write when an ETR dies: death is derived from time. Conventional analogue: a registration expires ~3 minutes after
# the last Map-Register (LISP_REGISTER_TTL).
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
IID,G,P='0','','198.34.128.0/17'
ms=C(); a=C(); b=C(); c=C(); itr=C()
try:
 assert ms.call('site.add',iid=IID,prefix='198.34.0.0/16',group=G,accept_more_specifics=True)=='good'
 assert ms.call('resolver.wait_site',iid=IID,prefix='198.34.0.0/16',group=G,active=True)=='good'
 assert ms.call('ms_governor.start',iid=IID,group=G,name='ms-live')=='good'
 for e,n,x in ((a,'etr-la','1a1a'),(b,'etr-lb','1b1b'),(c,'etr-lc','1c1c')): assert e.call('etr.identity',name=n,xtr_id=x)=='good'
 assert a.call('etr_liveness.start',interval_s=0.3,lifetime_s=1.2)=='good'
 assert b.call('etr_liveness.start',interval_s=0.3,lifetime_s=1.2)=='good'
 for e,r in ((a,'192.0.2.131'),(b,'192.0.2.132'),(c,'192.0.2.133')):
  assert e.call('database_mapping.add',iid=IID,prefix=P,group=G,rloc_set=[r])=='good'
  assert e.call('etr_decision.wait',governor='ms-live',iid=IID,group=G,prefix=P,decision='accepted',timeout_s=5)=='good'
 assert itr.call('resolver.wait',iid=IID,prefix=P,group=G,present=True,rloc='192.0.2.131',timeout_s=5)=='good'
 assert itr.call('resolver.wait',iid=IID,prefix=P,group=G,present=True,rloc='192.0.2.132',timeout_s=5)=='good'
 # C never published liveness: accepted by policy, but not resolvable (a native registration needs a live ETR)
 assert itr.call('resolver.wait',iid=IID,prefix=P,group=G,present=True,rloc='192.0.2.133',timeout_s=2)=='timeout'
 # A dies: nothing is written; its contribution disappears once alive_until passes. B stays.
 a.p.kill(); a.p.wait()
 assert itr.call('resolver.wait',iid=IID,prefix=P,group=G,present=True,rloc='192.0.2.131',absent=True,timeout_s=8)=='good'
 assert rl(itr.call('resolution.get',iid=IID,prefix='198.34.200.1/32',group=G))==['192.0.2.132']
 # B stops its heartbeat cleanly: same result
 assert b.call('etr_liveness.stop')=='good'
 for _ in range(4):
  if itr.call('resolution.get',iid=IID,prefix='198.34.200.1/32',group=G)==[]: break
  itr.call('resolver.wait',iid=IID,prefix=P,group=G,present=True,rloc='192.0.2.132',absent=True,timeout_s=3)
 assert itr.call('resolution.get',iid=IID,prefix='198.34.200.1/32',group=G)==[]
 print('PASS native liveness: one liveness cell per ETR, dead ETR contributions stop resolving without any write,',
       'live ETRs unaffected, an ETR that never published liveness does not resolve')
finally:
 for e in (b,c):
  try:e.call('database_mapping.delete',iid=IID,prefix=P,group=G)
  except Exception:pass
 try:ms.call('site.delete',iid=IID,prefix='198.34.0.0/16',group=G)
 except Exception:pass
 for e in (ms,a,b,c,itr):
  try:e.close()
  except Exception:pass
