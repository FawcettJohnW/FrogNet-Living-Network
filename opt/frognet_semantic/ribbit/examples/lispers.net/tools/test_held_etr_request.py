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
# Independent processes: one writes ETR database-mapping truth, another (the ETR) answers wire Map-Requests
# from its held view. Zero request-time database-mapping reads; inactive withdrawal falls back to the /16.
import json,subprocess,struct,socket
import os as _os; RAM_HOST=_os.environ.get('RIBBIT_RAM_HOST','127.0.0.1'); RAM_PORT=_os.environ.get('RIBBIT_RAM_PORT','8788')
BIN='./ribbit_cpp/ribbit-lisp'
class C:
 def __init__(self): self.p=subprocess.Popen([BIN,'--ram',RAM_HOST,RAM_PORT],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
 def call(self,op,**a):
  self.p.stdin.write(json.dumps({'operation':op,'args':a})+'\n');self.p.stdin.flush();r=json.loads(self.p.stdout.readline())
  if not r['ok']: raise RuntimeError(r['error'])
  return r['result']
 def close(self): self.p.terminate();self.p.wait(timeout=5)
def req4(target,nonce):
 p=struct.pack('!I',(1<<28)|1)+struct.pack('=Q',nonce)+struct.pack('!H',0)+struct.pack('!H',1)+bytes((192,0,2,1))
 return (p+bytes((0,32))+struct.pack('!H',1)+bytes(target)).hex()
def req6(target,nonce):
 p=struct.pack('!I',(1<<28)|1)+struct.pack('=Q',nonce)+struct.pack('!H',0)+struct.pack('!H',2)+socket.inet_pton(socket.AF_INET6,'2001:db8:ffff::1')
 return (p+bytes((0,128))+struct.pack('!H',2)+socket.inet_pton(socket.AF_INET6,target)).hex()
def reply6(hx):
 b=bytes.fromhex(hx); return b[17],socket.inet_ntop(socket.AF_INET6,b[24:40]),socket.inet_ntop(socket.AF_INET6,b[48:64]),len(b)
def reply(hx):
 b=bytes.fromhex(hx); return b[17],socket.inet_ntoa(b[24:28]),socket.inet_ntoa(b[36:40]),struct.unpack('!I',b[12:16])[0]
etr=C();w=C()
p16='198.51.0.0/16';p17='198.51.128.0/17';T=(198,51,200,1)
try:
 assert w.call('database_mapping.add',iid='0',prefix=p16,group='',rloc_set=['192.0.2.161'])=='good'
 assert etr.call('database_mapping.wait',iid='0',prefix=p16,group='',present=True)=='good'
 assert reply(etr.call('wire.etr_request4',hex=req4(T,1)))==(16,'198.51.0.0','192.0.2.161',1440)
 assert etr.call('resolver.stats')['database_mapping_reads']==0
 assert w.call('database_mapping.add',iid='0',prefix=p17,group='',rloc_set=['192.0.2.171'])=='good'
 assert etr.call('database_mapping.wait',iid='0',prefix=p17,group='',present=True)=='good'
 for n in range(5):
  assert reply(etr.call('wire.etr_request4',hex=req4(T,10+n)))==(17,'198.51.128.0','192.0.2.171',1440)
  assert etr.call('resolver.stats')['database_mapping_reads']==0
 assert w.call('database_mapping.delete',iid='0',prefix=p17,group='')=='good'
 assert etr.call('database_mapping.wait',iid='0',prefix=p17,group='',present=False)=='good'
 assert reply(etr.call('wire.etr_request4',hex=req4(T,99)))==(16,'198.51.0.0','192.0.2.161',1440)
 assert etr.call('resolver.stats')['database_mapping_reads']==0
 # ETR Map-Register from the held view, consumed by an independent Map-Server process
 ms=C()
 try:
  assert ms.call('site.add',iid='0',prefix='198.51.0.0/16',group='',accept_more_specifics=True,key_id=1,password='etr-secret') in ('good',None,'')
  assert w.call('database_mapping.add',iid='0',prefix=p17,group='',rloc_set=['192.0.2.171'])=='good'
  assert etr.call('database_mapping.wait',iid='0',prefix=p17,group='',present=True)=='good'
  hx=etr.call('wire.etr_register4',ms_key_id=1,ms_alg='sha1',ms_password='etr-secret',want_map_notify=True,xtr_id='0123456789abcdef0011223344556677',site_id='55')
  assert etr.call('resolver.stats')['database_mapping_reads']==0
  assert ms.call('wire.register4',hex=hx)=='good'
  assert ms.call('resolver.wait',iid='0',prefix=p17,group='',present=True)=='good'
  got=ms.call('resolution.get',iid='0',prefix='198.51.200.1/32',group='')
  assert got['prefix']==p17 and got['rlocs'][0]['address']=='192.0.2.171',got
  for pf in (p16,p17):
   try:ms.call('registration.delete',iid='0',prefix=pf,group='')
   except Exception:pass
  ms.call('site.delete',iid='0',prefix='198.51.0.0/16',group='')
 finally: ms.close()
 assert w.call('database_mapping.delete',iid='0',prefix=p17,group='')=='good'
 assert etr.call('database_mapping.wait',iid='0',prefix=p17,group='',present=False)=='good'
 # map-server configuration: written on the ETR, held, used for the register with zero request-time reads;
 # an independent RAM reader verifies the published cell carries no password
 assert etr.call('etr_map_server.add',address='192.0.2.251',alg='sha1',key_id=1,password='etr-secret',want_map_notify=True,site_id=85)=='good'
 assert etr.call('etr_map_server.wait',address='192.0.2.251',present=True)=='good'
 assert w.call('database_mapping.add',iid='0',prefix=p17,group='',rloc_set=['192.0.2.171'])=='good'
 assert etr.call('database_mapping.wait',iid='0',prefix=p17,group='',present=True)=='good'
 held=etr.call('wire.etr_register4',map_server='192.0.2.251',xtr_id='0123456789abcdef0011223344556677')
 st=etr.call('resolver.stats'); assert st['map_server_reads']==0 and st['database_mapping_reads']==0,st
 other=C()
 try: assert other.call('etr_map_server.wait',address='192.0.2.251',present=True)=='good'; cfg=other.call('etr_map_server.get',address='192.0.2.251'); assert 'password' not in cfg and cfg['site_id']==85,cfg
 finally: other.close()
 # Map-Notify from an independent Map-Server process, verified and acknowledged by the ETR; the Map-Server verifies the ack
 ms2=C()
 try:
  assert ms2.call('site.add',iid='0',prefix='198.51.0.0/16',group='',accept_more_specifics=True,key_id=1,password='etr-secret') in ('good',None,'')
  out=ms2.call('wire.register4_notify',hex=held); assert out['result']=='good',out
  r=etr.call('wire.etr_notify4',hex=out['notify_hex'],source='192.0.2.251'); assert r['result']=='good',r
  assert int(r['ack_hex'][0],16)==5
  assert ms2.call('wire.auth_verify',hex=r['ack_hex'],password='etr-secret')
  for pf in (p16,p17):
   try:ms2.call('registration.delete',iid='0',prefix=pf,group='')
   except Exception:pass
  ms2.call('site.delete',iid='0',prefix='198.51.0.0/16',group='')
 finally: ms2.close()
 r=subprocess.run(['./tools/check-ms-secret'],capture_output=True,text=True); assert r.returncode==0,(r.stdout,r.stderr)
 assert etr.call('etr_map_server.delete',address='192.0.2.251')=='good'
 assert w.call('database_mapping.delete',iid='0',prefix=p17,group='')=='good'
 assert etr.call('database_mapping.wait',iid='0',prefix=p17,group='',present=False)=='good'
 q48='2001:db8:e8::/48';q49='2001:db8:e8:8000::/49';T6='2001:db8:e8:c801::1'
 assert w.call('database_mapping.add',iid='0',prefix=q48,group='',rloc_set=['2001:db8:ffff::148'])=='good'
 assert w.call('database_mapping.add',iid='0',prefix=q49,group='',rloc_set=['2001:db8:ffff::149'])=='good'
 assert etr.call('database_mapping.wait',iid='0',prefix=q49,group='',present=True)=='good'
 assert etr.call('database_mapping.wait',iid='0',prefix=q48,group='',present=True)=='good'
 assert reply6(etr.call('wire.etr_request6',hex=req6(T6,7)))==(49,'2001:db8:e8:8000::','2001:db8:ffff::149',64)
 assert etr.call('resolver.stats')['database_mapping_reads']==0
 assert w.call('database_mapping.delete',iid='0',prefix=q49,group='')=='good'
 assert etr.call('database_mapping.wait',iid='0',prefix=q49,group='',present=False)=='good'
 assert reply6(etr.call('wire.etr_request6',hex=req6(T6,8)))==(48,'2001:db8:e8::','2001:db8:ffff::148',64)
 assert etr.call('resolver.stats')['database_mapping_reads']==0
 print('PASS independent-process ETR Map-Request from held database mapping, zero request-time reads, withdrawal fallback, IPv4 and IPv6; ETR Map-Register from the held view accepted by an independent Map-Server; held map-server configuration, zero reads, no published password; Map-Notify from an independent Map-Server verified and acknowledged')
finally:
 for p in (p16,p17,'2001:db8:e8::/48','2001:db8:e8:8000::/49'):
  try:w.call('database_mapping.delete',iid='0',prefix=p,group='')
  except Exception:pass
 etr.close();w.close()
