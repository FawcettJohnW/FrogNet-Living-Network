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
# Bytes on the wire for ONE database change through the native path (v0.43): the ETR writes its database cell; the
# Map-Server's governor, holding that variable, wakes, writes the governed registration and its decision; the ETR
# observes its decision; an ITR observes the new RLOC. FNW1 frame bytes per process (transport.stats), TCP/IP headers
# excluded. v0.41 candidate (claim copy) measured 2,427 / 1,548 / 1,920 B for the same change.
import json,subprocess
import os as _os; RAM_HOST=_os.environ.get('RIBBIT_RAM_HOST','127.0.0.1'); RAM_PORT=_os.environ.get('RIBBIT_RAM_PORT','8788')
def P(): return subprocess.Popen(['./ribbit_cpp/ribbit-lisp','--ram',RAM_HOST,RAM_PORT],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
def call(p,op,**a):
 p.stdin.write(json.dumps({'operation':op,'args':a})+'\n');p.stdin.flush();r=json.loads(p.stdout.readline())
 if not r['ok']: raise RuntimeError(r['error'])
 return r['result']
def b(p): s=call(p,'transport.stats'); return s['bytes_out']+s['bytes_in']
ms=P(); etr=P(); itr=P()
try:
 call(ms,'site.add',iid='0',prefix='198.29.0.0/16',group='',accept_more_specifics=True)
 call(ms,'resolver.wait_site',iid='0',prefix='198.29.0.0/16',group='',active=True)
 call(ms,'ms_governor.start',iid='0',group='',name='ms-bytes')
 call(etr,'etr.identity',name='etr-bytes',xtr_id='0f0f')
 call(etr,'etr_liveness.start',interval_s=30,lifetime_s=120)   # long interval: keep beats out of the change window
 call(etr,'database_mapping.add',iid='0',prefix='198.29.1.0/24',group='',rloc_set=['192.0.2.29'])
 assert call(etr,'etr_decision.wait',governor='ms-bytes',iid='0',group='',prefix='198.29.1.0/24',decision='accepted',timeout_s=5)=='good'
 assert call(itr,'resolver.wait',iid='0',prefix='198.29.1.0/24',group='',present=True,rloc='192.0.2.29',timeout_s=5)=='good'
 e0,m0,i0=b(etr),b(ms),b(itr)
 call(etr,'database_mapping.add',iid='0',prefix='198.29.1.0/24',group='',rloc_set=['198.51.100.29'])
 assert call(itr,'resolver.wait',iid='0',prefix='198.29.1.0/24',group='',present=True,rloc='198.51.100.29',timeout_s=5)=='good'
 e1,m1,i1=b(etr),b(ms),b(itr)
 print(f'one change, native path: ETR process {e1-e0} B, Map-Server process {m1-m0} B, ITR process {i1-i0} B '
       f'(v0.41 candidate with claim copy: 2427 / 1548 / 1920 B; conventional: one full register, 88 B for N=1)')
finally:
 try:call(etr,'database_mapping.delete',iid='0',prefix='198.29.1.0/24',group='')
 except Exception:pass
 try:call(ms,'site.delete',iid='0',prefix='198.29.0.0/16',group='')
 except Exception:pass
 for p in (ms,etr,itr): p.terminate()
