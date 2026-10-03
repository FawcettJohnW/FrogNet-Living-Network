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
import json, subprocess
import os as _os; RAM_HOST=_os.environ.get('RIBBIT_RAM_HOST','127.0.0.1'); RAM_PORT=_os.environ.get('RIBBIT_RAM_PORT','8788')
BIN='./ribbit_cpp/ribbit-lisp'
class Client:
    def __init__(self):
        self.p=subprocess.Popen([BIN,'--ram',RAM_HOST,RAM_PORT],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
    def call(self,op,**args):
        self.p.stdin.write(json.dumps({'operation':op,'args':args})+'\n'); self.p.stdin.flush()
        out=json.loads(self.p.stdout.readline())
        if not out.get('ok'): raise RuntimeError(out.get('error'))
        return out['result']
    def close(self):
        self.p.terminate(); self.p.wait(timeout=5)
def rlocs(x): return [r['address'] for r in x.get('rlocs',[])] if isinstance(x,dict) else []
r=Client(); w=Client()
try:
    iid='9101'; group='held-ddt'; p8='10.0.0.0/8'; p16='10.20.0.0/16'; target='10.20.30.1/32'
    assert r.call('ddt.get',iid=iid,prefix=target,group=group)==[]
    assert w.call('ddt.add',iid=iid,prefix=p8,group=group,rloc_set=['192.0.2.90'])=='good'
    assert r.call('resolver.wait_ddt',iid=iid,prefix=p8,group=group,present=True)=='good'
    assert rlocs(r.call('ddt.get',iid=iid,prefix=target,group=group))==['192.0.2.90']
    assert r.call('resolver.stats')['ddt_reads']==0
    assert w.call('ddt.add',iid=iid,prefix=p16,group=group,rloc_set=['192.0.2.91','192.0.2.92'])=='good'
    assert r.call('resolver.wait_ddt',iid=iid,prefix=p16,group=group,present=True)=='good'
    for _ in range(5):
        assert rlocs(r.call('ddt.get',iid=iid,prefix=target,group=group))==['192.0.2.91','192.0.2.92']
        assert r.call('resolver.stats')['ddt_reads']==0
    assert w.call('ddt.delete',iid=iid,prefix=p16,group=group)=='good'
    assert r.call('resolver.wait_ddt',iid=iid,prefix=p16,group=group,present=False)=='good'
    assert rlocs(r.call('ddt.get',iid=iid,prefix=target,group=group))==['192.0.2.90']
    assert r.call('resolver.stats')['ddt_reads']==0
    assert w.call('ddt.delete',iid=iid,prefix=p8,group=group)=='good'
    assert r.call('resolver.wait_ddt',iid=iid,prefix=p8,group=group,present=False)=='good'
    assert r.call('ddt.get',iid=iid,prefix=target,group=group)==[]
    assert r.call('resolver.stats')['ddt_reads']==0
    print('PASS held DDT dynamic /N wake, LPM, zero warmed reads, tombstone fallback/withdrawal')
finally:
    r.close(); w.close()
