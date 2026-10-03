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
 iid='9301';g='held-db';p16='198.18.0.0/16';p17='198.18.128.0/17';t='198.18.200.1/32'
 assert r.call('database_mapping.get',iid=iid,prefix=t,group=g)==[]
 assert w.call('database_mapping.add',iid=iid,prefix=p16,group=g,rloc_set=['192.0.2.16'])=='good'
 assert r.call('database_mapping.wait',iid=iid,prefix=p16,group=g,present=True)=='good'
 assert rs(r.call('database_mapping.get',iid=iid,prefix=t,group=g))==['192.0.2.16']
 assert r.call('resolver.stats')['database_mapping_reads']==0
 assert w.call('database_mapping.add',iid=iid,prefix=p17,group=g,rloc_set=['192.0.2.17'])=='good'
 assert r.call('database_mapping.wait',iid=iid,prefix=p17,group=g,present=True)=='good'
 for _ in range(5):
  assert rs(r.call('database_mapping.get',iid=iid,prefix=t,group=g))==['192.0.2.17']
  assert r.call('resolver.stats')['database_mapping_reads']==0
 assert w.call('database_mapping.delete',iid=iid,prefix=p17,group=g)=='good'
 assert r.call('database_mapping.wait',iid=iid,prefix=p17,group=g,present=False)=='good'
 assert rs(r.call('database_mapping.get',iid=iid,prefix=t,group=g))==['192.0.2.16']
 assert r.call('resolver.stats')['database_mapping_reads']==0
 print('PASS held database-mapping LPM, zero warmed reads, inactive withdrawal fallback')
finally:
 # leave the shared memory as found: a later run against the same server (RIBBIT_RAM_EXTERNAL=1) starts from empty
 for _p in ('198.18.0.0/16','198.18.128.0/17'):
  try:w.call('database_mapping.delete',iid='9301',prefix=_p,group='held-db')
  except Exception:pass
 r.close();w.close()
