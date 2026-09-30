#!/usr/bin/env python3
# The registrar's control socket (control: lisp-etr.py binds one ephemeral UDP socket, lisp_ephem_socket, sends
# Map-Registers from it — lisp_send_sockets[0] — and receives the map-server's Map-Notify on it, then answers
# with a Map-Notify-Ack to the map-server's control port). Here the map-server's UDP side is this script; the
# notify itself is produced by an independent Ribbit-LISP Map-Server process.
import json,subprocess,socket,struct,select,sys
import os as _os; RAM_HOST=_os.environ.get('RIBBIT_RAM_HOST','127.0.0.1'); RAM_PORT=_os.environ.get('RIBBIT_RAM_PORT','8788')
BIN='./ribbit_cpp/ribbit-lisp'; PORT=int(sys.argv[1]) if len(sys.argv)>1 else 24344
class C:
 def __init__(self): self.p=subprocess.Popen([BIN,'--ram',RAM_HOST,RAM_PORT],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
 def call(self,op,**a):
  self.p.stdin.write(json.dumps({'operation':op,'args':a})+'\n');self.p.stdin.flush();r=json.loads(self.p.stdout.readline())
  if not r['ok']: raise RuntimeError(r['error'])
  return r['result']
 def close(self): self.p.terminate();self.p.wait(timeout=5)
MS='127.0.0.4'; PW='notify-loop-secret'
ms=socket.socket(socket.AF_INET,socket.SOCK_DGRAM); ms.bind((MS,PORT))
def recv(t):
 r,_,_=select.select([ms],[],[],t); return ms.recvfrom(4096) if r else (None,None)
etr=C(); msp=C(); obs=C()
try:
 assert msp.call('site.add',iid='0',prefix='198.24.0.0/16',group='',accept_more_specifics=True,key_id=4,password=PW) in ('good',None,'')
 assert etr.call('database_mapping.add',iid='0',prefix='198.24.0.0/16',group='',rloc_set=['192.0.2.76'])=='good'
 assert etr.call('database_mapping.wait',iid='0',prefix='198.24.0.0/16',group='',present=True)=='good'
 assert etr.call('etr_map_server.add',address=MS,alg='sha256',key_id=4,password=PW,want_map_notify=True,site_id=9)=='good'
 assert etr.call('etr_map_server.wait',address=MS,present=True)=='good'
 assert etr.call('etr_registrar.start',xtr_id='0c0d',first_s=0.2,interval_s=5,udp_port=PORT)=='good'
 reg,etr_addr=recv(3.0); assert reg,'no Map-Register'
 out=msp.call('wire.register4_notify',hex=reg.hex()); assert out['result']=='good',(out,'records',reg[3])
 notify=bytes.fromhex(out['notify_hex'])
 ms.sendto(notify,etr_addr)                               # the map-server answers to the register's source port
 ack,frm=recv(1.5); assert ack,'no Map-Notify-Ack on the map-server control port'
 assert struct.unpack('!I',ack[:4])[0]>>28==5 and ack[4:14]==notify[4:14],ack.hex()
 assert msp.call('wire.auth_verify',hex=ack.hex(),password=PW)
 assert frm==etr_addr,(frm,etr_addr)                      # sent from the same control socket
 bad=bytearray(notify); bad[-1]^=1; ms.sendto(bytes(bad),etr_addr)
 ack2,_=recv(1.0); assert ack2 is None,'tampered notify must not be acknowledged'
 assert obs.call('etr_registrar.notify_wait',address=MS,min_acks=1,timeout_s=5)=='good'
 n=obs.call('etr_registrar.notified',address=MS); assert n['acks']==1 and n['rejected']==1 and 'password' not in n,n
 # a map-server published by another process: this ETR holds no key for it -> a recorded failure, not a crash
 assert obs.call('etr_map_server.add',address='127.0.0.5',alg='sha1',key_id=1,password='elsewhere')=='good'
 for _ in range(2):
  try:
   s5=obs.call('etr_registrar.sent',address='127.0.0.5')
   if isinstance(s5,dict) and 'error' in s5: break
  except Exception: pass
  obs.call('etr_registrar.wait',address='127.0.0.5',min_sends=0,timeout_s=2)
 s5=obs.call('etr_registrar.sent',address='127.0.0.5'); assert isinstance(s5,dict) and 'no key' in s5.get('error',''),s5
 ms.sendto(notify,etr_addr); ack3,_=recv(1.5); assert ack3,'registrar must keep working after a map-server it cannot sign for'
 obs.call('etr_map_server.delete',address='127.0.0.5')
 assert etr.call('etr_registrar.stop')=='good'
 assert obs.call('etr_registrar.state_wait',state='stopped',timeout_s=8)=='good'
 print('PASS registrar control socket: register sent, notify from an independent Map-Server verified and',
       'acknowledged from the same socket, tampered notify dropped, counts observed by another process, keyless map-server recorded not fatal, clean stop')
finally:
 try:etr.call('database_mapping.delete',iid='0',prefix='198.24.0.0/16',group='')
 except Exception:pass
 try:etr.call('etr_map_server.delete',address=MS)
 except Exception:pass
 for c in (etr,msp,obs):
  try:c.close()
  except Exception:pass
