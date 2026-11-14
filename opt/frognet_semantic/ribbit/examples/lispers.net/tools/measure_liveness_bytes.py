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
# Steady-state cost of native liveness ([LIVE_WHILE_ITS_SESSION_IS_V1]): an ETR writes ONE cell once, held by its
# session; nothing recurs. A resolver holding the liveness cells receives that cell once, and its removal once when the
# ETR's session ends. Conventional: every 60 s one full Map-Register per map-server carrying all N records (60+28N
# bytes, IPv4, one RLOC, SHA-1, xTR-ID trailer; <=20 records/packet).
import json,subprocess,sys
import os as _os; RAM_HOST=_os.environ.get('RIBBIT_RAM_HOST','127.0.0.1'); RAM_PORT=_os.environ.get('RIBBIT_RAM_PORT','8788')
def P(): return subprocess.Popen(['./ribbit_cpp/ribbit-lisp','--ram',RAM_HOST,RAM_PORT],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
def call(p,op,**a):
 p.stdin.write(json.dumps({'operation':op,'args':a})+'\n');p.stdin.flush();r=json.loads(p.stdout.readline())
 if not r['ok']: raise RuntimeError(r['error'])
 return r['result']
def b(p): s=call(p,'transport.stats'); return s['bytes_out']+s['bytes_in']
etr=P(); itr=P()
try:
 call(etr,'etr.identity',name='etr-lb',xtr_id='2b2b')
 call(itr,'resolution.get',iid='0',prefix='203.0.113.1/32',group='')      # warm the resolver (holds liveness)
 call(etr,'etr_liveness.start')
 import time
 time.sleep(0.5)                                    # measurement only (not a convergence test)
 s0e,s0i=call(etr,'transport.stats'),call(itr,'transport.stats')
 time.sleep(3.0)                                    # a window with the ETR alive and nothing else happening
 s1e,s1i=call(etr,'transport.stats'),call(itr,'transport.stats')
 sent=(s1e['raw']+s1e['repeat'])-(s0e['raw']+s0e['repeat'])   # requests the ETR sent in the window
 de=(s1e['bytes_out']+s1e['bytes_in'])-(s0e['bytes_out']+s0e['bytes_in'])
 di=(s1i['bytes_out']+s1i['bytes_in'])-(s0i['bytes_out']+s0i['bytes_in'])
 print(f'native liveness, 3 s alive: ETR {sent} requests, {de} B; resolver {di} B '
       f'-- one cell written once, independent of the number of database mappings and of time')
 for n in (1,10,100):
  chunks=[min(20,n-s) for s in range(0,n,20)]; udp=sum(60+28*k for k in chunks)
  print(f'conventional refresh, N={n}: {udp} B of Map-Register(s) per map-server every 60 s ({udp+28*len(chunks)} with IP/UDP headers)')
finally:
 try:call(etr,'etr_liveness.stop')
 except Exception:pass
 etr.terminate(); itr.terminate()
