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
# Map-Server resolution cost vs size of the held registration table (found in the first cross-Internet run, v0.45).
import os as _os
import json,subprocess,sys,time,threading
sys.path.insert(0,'.')
from tests.test_wire_contract import reg4,req4
p=subprocess.Popen(['./ribbit_cpp/ribbit-lisp','--ram',_os.environ.get('RIBBIT_RAM_HOST','127.0.0.1'),_os.environ.get('RIBBIT_RAM_PORT','8788')],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
def call(op,**a):
 p.stdin.write(json.dumps({'operation':op,'args':a})+'\n');p.stdin.flush();r=json.loads(p.stdout.readline()); assert r['ok'],r; return r['result']
def run(hx,N=10000):
 lines=''.join(json.dumps({'operation':'wire.request4','args':{'hex':hx}})+'\n' for _ in range(N))
 t0=time.perf_counter(); th=threading.Thread(target=lambda:(p.stdin.write(lines),p.stdin.flush())); th.start()
 for _ in range(N): p.stdout.readline()
 th.join(); return (time.perf_counter()-t0)/N*1e6
call('site.add',iid='0',prefix='198.0.0.0/8',group='',accept_more_specifics=True); call('resolver.wait_site',iid='0',prefix='198.0.0.0/8',group='',active=True)
assert call('wire.register4',hex=reg4().hex())=='good'; call('resolver.wait',iid='0',prefix='198.18.70.0/24',group='',present=True)
hit=req4().hex(); print(f'held table ~1 entry: {run(hit):.1f} us/request')
for n in (1000,3000):
 for i in range(n - (0 if n==1000 else 1000)):
  a=(i//250)%250; b=i%250
  call('registration.put',iid='0',prefix=f'198.{100+a}.{b}.0/24',group='',ttl=60,rloc_set=['192.0.2.1'])
 call('resolver.wait',iid='0',prefix=f'198.{100+a}.{b}.0/24',group='',present=True)
 print(f'held table ~{n} entries: {run(hit):.1f} us/request')
p.terminate()
