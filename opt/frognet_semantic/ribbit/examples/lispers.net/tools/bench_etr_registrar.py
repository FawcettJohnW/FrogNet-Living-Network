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
# Trigger latency of the registrar participant: from etr_map_server.add returning on the ETR to the UDP
# Map-Register arriving at the map-server socket (held-read wake + build + HMAC + sendto), FNW1, 1-core sandbox.
import json,subprocess,socket,select,time,statistics,sys
import os as _os; RAM_HOST=_os.environ.get('RIBBIT_RAM_HOST','127.0.0.1'); RAM_PORT=_os.environ.get('RIBBIT_RAM_PORT','8788')
BIN='./ribbit_cpp/ribbit-lisp'; PORT=24343; N=int(sys.argv[1]) if len(sys.argv)>1 else 200
p=subprocess.Popen([BIN,'--ram',RAM_HOST,RAM_PORT],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
def call(op,**a):
 p.stdin.write(json.dumps({'operation':op,'args':a})+'\n');p.stdin.flush();r=json.loads(p.stdout.readline())
 if not r['ok']: raise RuntimeError(r['error'])
 return r['result']
s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM); s.bind(('127.0.0.3',PORT))
try:
 call('database_mapping.add',iid='0',prefix='198.22.0.0/16',group='',rloc_set=['192.0.2.56'])
 call('database_mapping.wait',iid='0',prefix='198.22.0.0/16',group='',present=True)
 call('etr_map_server.delete',address='127.0.0.3')           # a stale active entry would not be 'new'
 call('etr_map_server.wait',address='127.0.0.3',present=False)
 call('etr_registrar.start',xtr_id='0123456789abcdef0011223344556677',first_s=3600,interval_s=3600,udp_port=PORT)
 lat=[]
 for i in range(N):
  t0=time.perf_counter(); call('etr_map_server.add',address='127.0.0.3',alg='sha1',key_id=1,password='x',site_id=1)
  r,_,_=select.select([s],[],[],2.0); assert r,'no register'; s.recvfrom(4096); lat.append((time.perf_counter()-t0)*1e6)
  call('etr_map_server.delete',address='127.0.0.3')
 lat.sort()
 print(f'registrar trigger latency, N={N}: median {statistics.median(lat):.0f} us, p90 {lat[int(N*0.9)]:.0f} us, max {lat[-1]:.0f} us')
finally:
 try:call('etr_registrar.stop')
 except Exception:pass
 try:call('database_mapping.delete',iid='0',prefix='198.22.0.0/16',group='')
 except Exception:pass
 p.terminate()
