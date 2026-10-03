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
# Periodic ETR registration as a participant (control: lisp-etr.py lisp_process_register_timer every
# LISP_MAP_REGISTER_INTERVAL=60 s, first via a 5 s trigger timer, refresh=True; a newly configured map-server gets
# an immediate register with refresh=False — lisp_etr_map_server_command). Intervals are shortened here.
# The registrar sends each Map-Register over UDP to the map-server and publishes what it sent as its own truth.
import json,subprocess,socket,struct,select,time,sys
import os as _os; RAM_HOST=_os.environ.get('RIBBIT_RAM_HOST','127.0.0.1'); RAM_PORT=_os.environ.get('RIBBIT_RAM_PORT','8788')
BIN='./ribbit_cpp/ribbit-lisp'; PORT=int(sys.argv[1]) if len(sys.argv)>1 else 24342
class C:
 def __init__(self): self.p=subprocess.Popen([BIN,'--ram',RAM_HOST,RAM_PORT],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
 def call(self,op,**a):
  self.p.stdin.write(json.dumps({'operation':op,'args':a})+'\n');self.p.stdin.flush();r=json.loads(self.p.stdout.readline())
  if not r['ok']: raise RuntimeError(r['error'])
  return r['result']
 def close(self): self.p.terminate();self.p.wait(timeout=5)
def listener(addr):
 s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM); s.bind((addr,PORT)); return s
def recv(socks,timeout):
 r,_,_=select.select(socks,[],[],timeout)
 return [(s.getsockname()[0],s.recvfrom(4096)[0]) for s in r]
def flags(b): return struct.unpack('!I',b[:4])[0]
ms1,ms2='127.0.0.1','127.0.0.2'; XTR='0123456789abcdef0011223344556677'
l1,l2=listener(ms1),listener(ms2)
etr=C(); msproc=C(); obs=C()
pfx=('198.21.0.0/16','198.21.128.0/17')
try:
 assert msproc.call('site.add',iid='0',prefix='198.21.0.0/16',group='',accept_more_specifics=True,key_id=1,password='etr-secret') in ('good',None,'')
 for p,r in zip(pfx,('192.0.2.46','192.0.2.47')):
  assert etr.call('database_mapping.add',iid='0',prefix=p,group='',rloc_set=[r])=='good'
  assert etr.call('database_mapping.wait',iid='0',prefix=p,group='',present=True)=='good'
 assert etr.call('etr_map_server.add',address=ms1,alg='sha1',key_id=1,password='etr-secret',want_map_notify=True,refresh=True,site_id=85)=='good'
 assert etr.call('etr_map_server.wait',address=ms1,present=True)=='good'
 assert etr.call('etr_registrar.start',xtr_id=XTR,first_s=0.2,interval_s=1.5,udp_port=PORT)=='good'
 # first periodic send to ms1: refresh requested by the map-server and this is the timer -> refresh bit set
 got=recv([l1,l2],3.0); assert got and got[0][0]==ms1,got; b=got[0][1]
 assert flags(b)>>28==3 and flags(b)&0xff==2 and flags(b)&0x1000 and flags(b)&0x02000000,hex(flags(b))
 assert b[4:12]==bytes.fromhex('01dfdfdfddccbbaa'),b[4:12].hex()          # control nonce, first packet of the run
 assert msproc.call('wire.register4',hex=b.hex())=='good'
 for p in pfx: assert msproc.call('resolver.wait',iid='0',prefix=p,group='',present=True)=='good'
 # a newly configured map-server gets an immediate register, not a periodic one: refresh bit clear
 t0=time.time()
 assert etr.call('etr_map_server.add',address=ms2,alg='sha1',key_id=1,password='etr-secret',want_map_notify=True,refresh=True,site_id=85)=='good'
 got=recv([l2],1.2); assert got,'no immediate register to the new map-server'
 b2=got[0][1]; assert not flags(b2)&0x1000, 'triggered register must not carry refresh'
 assert time.time()-t0<1.2
 assert msproc.call('wire.register4',hex=b2.hex())=='good'
 # the next period reaches both map-servers
 seen=set(); end=time.time()+4.0
 while len(seen)<2 and time.time()<end:
  for a,pkt in recv([l1,l2],end-time.time()):
   assert flags(pkt)&0x1000; seen.add(a)
 assert seen=={ms1,ms2},seen
 # what the registrar sent is its own published truth; an independent process observes it, no password
 assert obs.call('etr_registrar.wait',address=ms1,min_sends=2,timeout_s=5)=='good'
 s=obs.call('etr_registrar.sent',address=ms1)
 assert s['sends']>=2 and s['records']==2 and 'password' not in s,s
 assert etr.call('etr_registrar.stop')=='good'
 late=recv([l1,l2],3.2)                                   # two intervals: a stopped registrar sends nothing
 assert not late,('register after stop',late)
 print('PASS periodic ETR registrar: timer sends with refresh, immediate register to a new map-server,',
       'UDP datagrams accepted by an independent Map-Server, published send truth observed by another process')
finally:
 for pf in pfx:
  try:etr.call('database_mapping.delete',iid='0',prefix=pf,group='')
  except Exception:pass
 for a in (ms1,ms2):
  try:etr.call('etr_map_server.delete',address=a)
  except Exception:pass
 for c in (etr,msproc,obs):
  try:c.close()
  except Exception:pass
