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
# Liveness of native registrations ([LIVE_WHILE_ITS_SESSION_IS_V1]). A governed registration lives only while its ETR
# is alive: each ETR writes ONE liveness cell (etr-live/<xtr_id>) once, held by its own session; the memory removes it
# when that session ends, and resolvers holding etr-live are handed the removal. Nothing beats, nothing is derived
# from time. Conventional analogue: a registration expires ~3 minutes after the last Map-Register (LISP_REGISTER_TTL).
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
 assert a.call('etr_liveness.start')=='good'
 assert b.call('etr_liveness.start')=='good'
 for e,r in ((a,'192.0.2.131'),(b,'192.0.2.132'),(c,'192.0.2.133')):
  assert e.call('database_mapping.add',iid=IID,prefix=P,group=G,rloc_set=[r])=='good'
  assert e.call('etr_decision.wait',governor='ms-live',iid=IID,group=G,prefix=P,decision='accepted',timeout_s=5)=='good'
 assert itr.call('resolver.wait',iid=IID,prefix=P,group=G,present=True,rloc='192.0.2.131',timeout_s=5)=='good'
 assert itr.call('resolver.wait',iid=IID,prefix=P,group=G,present=True,rloc='192.0.2.132',timeout_s=5)=='good'
 # C never published liveness: accepted by policy, but not resolvable (a native registration needs a live ETR)
 assert itr.call('resolver.wait',iid=IID,prefix=P,group=G,present=True,rloc='192.0.2.133',timeout_s=2)=='timeout'
 # A dies: its session ends and its liveness cell goes with it; its contribution disappears now. B stays.
 a.p.kill(); a.p.wait()
 assert itr.call('resolver.wait',iid=IID,prefix=P,group=G,present=True,rloc='192.0.2.131',absent=True,timeout_s=2)=='good'
 assert rl(itr.call('resolution.get',iid=IID,prefix='198.34.200.1/32',group=G))==['192.0.2.132']
 # B stops liveness cleanly (its cell is removed; its session stays): same result
 assert b.call('etr_liveness.stop')=='good'
 assert itr.call('resolver.wait',iid=IID,prefix=P,group=G,present=False,timeout_s=2)=='good'
 assert itr.call('resolution.get',iid=IID,prefix='198.34.200.1/32',group=G)==[]
 # the old arguments are refused, not ignored
 try: b.call('etr_liveness.start',interval_s=1,lifetime_s=5); raise AssertionError('interval_s accepted')
 except RuntimeError as x: assert 'is gone' in str(x),x
 # B starts again: resolvable again, at once
 assert b.call('etr_liveness.start')=='good'
 assert itr.call('resolver.wait',iid=IID,prefix=P,group=G,present=True,rloc='192.0.2.132',timeout_s=2)=='good'
 print('PASS native liveness: one session-held liveness cell per ETR, a dead ETR\'s contribution stops resolving when its',
       'session ends, stop removes it, live ETRs unaffected, an ETR that never published liveness does not resolve')
finally:
 for e in (b,c):
  try:e.call('database_mapping.delete',iid=IID,prefix=P,group=G)
  except Exception:pass
 try:ms.call('site.delete',iid=IID,prefix='198.34.0.0/16',group=G)
 except Exception:pass
 for e in (ms,a,b,c,itr):
  try:e.close()
  except Exception:pass
