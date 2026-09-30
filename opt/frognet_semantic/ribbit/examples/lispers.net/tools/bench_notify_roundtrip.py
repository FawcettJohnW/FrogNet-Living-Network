#!/usr/bin/env python3
# Map-Notify round trip over UDP: map-server socket sends a notify to the registrar's control socket; time until
# the Map-Notify-Ack arrives back (recvfrom, verify HMAC-SHA-256, build ack, HMAC, sendto, publish count).
import json,subprocess,socket,select,time,statistics,sys
import os as _os; RAM_HOST=_os.environ.get('RIBBIT_RAM_HOST','127.0.0.1'); RAM_PORT=_os.environ.get('RIBBIT_RAM_PORT','8788')
BIN='./ribbit_cpp/ribbit-lisp'; PORT=24345; N=int(sys.argv[1]) if len(sys.argv)>1 else 500
def P(): return subprocess.Popen([BIN,'--ram',RAM_HOST,RAM_PORT],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
def call(p,op,**a):
 p.stdin.write(json.dumps({'operation':op,'args':a})+'\n');p.stdin.flush();r=json.loads(p.stdout.readline())
 if not r['ok']: raise RuntimeError(r['error'])
 return r['result']
ms=socket.socket(socket.AF_INET,socket.SOCK_DGRAM); ms.bind(('127.0.0.7',PORT))
etr=P(); msp=P()
try:
 call(msp,'site.add',iid='0',prefix='198.26.0.0/16',group='',accept_more_specifics=True,key_id=2,password='rt')
 call(etr,'database_mapping.add',iid='0',prefix='198.26.0.0/16',group='',rloc_set=['192.0.2.86'])
 call(etr,'database_mapping.wait',iid='0',prefix='198.26.0.0/16',group='',present=True)
 call(etr,'etr_map_server.add',address='127.0.0.7',alg='sha256',key_id=2,password='rt',want_map_notify=True)
 call(etr,'etr_map_server.wait',address='127.0.0.7',present=True)
 call(etr,'etr_registrar.start',xtr_id='07',first_s=0.1,interval_s=3600,udp_port=PORT)
 r,_,_=select.select([ms],[],[],3); reg,addr=ms.recvfrom(4096)
 notify=bytes.fromhex(call(msp,'wire.register4_notify',hex=reg.hex())['notify_hex'])
 lat=[]
 for i in range(N):
  t0=time.perf_counter(); ms.sendto(notify,addr); r,_,_=select.select([ms],[],[],2); assert r; ms.recvfrom(4096)
  lat.append((time.perf_counter()-t0)*1e6)
 lat.sort(); print(f'Map-Notify -> Map-Notify-Ack UDP round trip, N={N}: median {statistics.median(lat):.0f} us, p90 {lat[int(N*.9)]:.0f} us, max {lat[-1]:.0f} us')
finally:
 for op,kw in (('etr_registrar.stop',{}),('database_mapping.delete',dict(iid='0',prefix='198.26.0.0/16',group='')),('etr_map_server.delete',dict(address='127.0.0.7'))):
  try:call(etr,op,**kw)
  except Exception:pass
 etr.terminate(); msp.terminate()
