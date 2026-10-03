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
# Re-governance of native registrations on site-policy change (v0.45). Deleting the site that authorized a native
# registration must make it stop resolving; restoring the site must make it resolve again — with no action by the ETR.
# Conventional analogue: removing a site from the control's Map-Server removes its registrations.
import json,subprocess
from os import environ as E
RAM_HOST=E.get('RIBBIT_RAM_HOST','127.0.0.1'); RAM_PORT=E.get('RIBBIT_RAM_PORT','8788')
BIN='./ribbit_cpp/ribbit-lisp'
class C:
 def __init__(self): self.p=subprocess.Popen([BIN,'--ram',RAM_HOST,RAM_PORT],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
 def call(self,op,**a):
  self.p.stdin.write(json.dumps({'operation':op,'args':a})+'\n');self.p.stdin.flush();r=json.loads(self.p.stdout.readline())
  if not r['ok']: raise RuntimeError(r['error'])
  return r['result']
 def close(self): self.p.terminate();self.p.wait(timeout=5)
IID,G,SITE,P,R='0','','198.35.0.0/16','198.35.1.0/24','192.0.2.135'
ms=C(); etr=C(); itr=C()
try:
 assert ms.call('site.add',iid=IID,prefix=SITE,group=G,accept_more_specifics=True)=='good'
 assert ms.call('resolver.wait_site',iid=IID,prefix=SITE,group=G,active=True)=='good'
 assert ms.call('ms_governor.start',iid=IID,group=G,name='ms-regov')=='good'
 assert etr.call('etr.identity',name='etr-rg',xtr_id='3c3c')=='good'
 assert etr.call('etr_liveness.start',interval_s=1,lifetime_s=10)=='good'
 assert etr.call('database_mapping.add',iid=IID,prefix=P,group=G,rloc_set=[R])=='good'
 assert etr.call('etr_decision.wait',governor='ms-regov',iid=IID,group=G,prefix=P,decision='accepted',timeout_s=5)=='good'
 assert itr.call('resolver.wait',iid=IID,prefix=P,group=G,present=True,rloc=R,timeout_s=5)=='good'
 # the site goes: the registration must stop resolving
 assert ms.call('site.delete',iid=IID,prefix=SITE,group=G)=='good'
 assert itr.call('resolver.wait',iid=IID,prefix=P,group=G,present=False,timeout_s=5)=='good'
 assert itr.call('resolution.get',iid=IID,prefix='198.35.1.1/32',group=G)==[]
 # the site comes back: it resolves again, the ETR did nothing
 assert ms.call('site.add',iid=IID,prefix=SITE,group=G,accept_more_specifics=True)=='good'
 assert itr.call('resolver.wait',iid=IID,prefix=P,group=G,present=True,rloc=R,timeout_s=5)=='good'
 # a narrower site that does not cover the prefix does not authorize it
 assert ms.call('site.delete',iid=IID,prefix=SITE,group=G)=='good'
 assert ms.call('site.add',iid=IID,prefix='198.35.2.0/24',group=G,accept_more_specifics=True)=='good'
 assert itr.call('resolver.wait',iid=IID,prefix=P,group=G,present=False,timeout_s=5)=='good'
 print('PASS native re-governance: site deleted -> registration stops resolving; site restored -> resolves again;',
       'a site that does not cover the prefix does not authorize it; the ETR took no action')
finally:
 try:etr.call('database_mapping.delete',iid=IID,prefix=P,group=G)
 except Exception:pass
 for s in (SITE,'198.35.2.0/24'):
  try:ms.call('site.delete',iid=IID,prefix=s,group=G)
  except Exception:pass
 for c in (ms,etr,itr):
  try:c.close()
  except Exception:pass
