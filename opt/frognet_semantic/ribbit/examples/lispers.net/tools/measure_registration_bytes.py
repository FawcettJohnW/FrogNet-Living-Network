#!/usr/bin/env python3
# Bytes on the wire for ETR registration: native Ribbit path (ETR publishes database-mapping truth; a Map-Server-side
# participant holds it with a held read) versus repeated full RFC 9301 Map-Registers over UDP.
# Native numbers are FNW1 frame bytes from each participant's own session counters (transport.stats), excluding
# process start-up; TCP/IP headers are not included. UDP numbers are Map-Register bytes built by wire.etr_register4
# (byte-identical to the control), split as the control splits (<=20 records per packet), shown with and without
# 28 bytes of IPv4+UDP header per packet. BLDC is not in this tree: these are FNW1 numbers, not FNWP/BLDC numbers.
import json,subprocess,sys
import os as _os; RAM_HOST=_os.environ.get('RIBBIT_RAM_HOST','127.0.0.1'); RAM_PORT=_os.environ.get('RIBBIT_RAM_PORT','8788')
BIN='./ribbit_cpp/ribbit-lisp'
class C:
 def __init__(self): self.p=subprocess.Popen([BIN,'--ram',RAM_HOST,RAM_PORT],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
 def call(self,op,**a):
  self.p.stdin.write(json.dumps({'operation':op,'args':a})+'\n');self.p.stdin.flush();r=json.loads(self.p.stdout.readline())
  if not r['ok']: raise RuntimeError(r['error'])
  return r['result']
 def bytes(self): s=self.call('transport.stats'); return s['bytes_out']+s['bytes_in']
 def close(self): self.p.terminate();self.p.wait(timeout=5)
def prefixes(n,base):
 return [f'10.{base+i//250}.{i%250}.0/24' for i in range(n)]
def udp_register_bytes(etr,n):
 # the byte-identical encoder builds ONE packet with all n records: header 16 + SHA-1 auth 20 + 28 per IPv4 record
 # with one RLOC + 24 xTR-ID/site-ID trailer. Check that against the encoder, then split as the control does
 # (at most 20 records per Map-Register, lisp_build_map_register).
 hx=etr.call('wire.etr_register4',ms_key_id=1,ms_alg='sha1',ms_password='m',want_map_notify=True,
             xtr_id='0123456789abcdef0011223344556677',site_id='1',nonce='aabbccdddfdfdf01')
 assert len(hx)//2==60+28*n,(len(hx)//2,n)
 chunks=[min(20,n-s) for s in range(0,n,20)]
 return sum(60+28*k for k in chunks),len(chunks)
rows=[]
for n,base in ((1,11),(10,21),(100,31)):
 etr=C(); ms=C()
 try:
  pf=prefixes(n,base)
  # MS side holds the ETR's database truth (the view a native Map-Server governs)
  ms.call('database_mapping.get',iid='0',prefix='0.0.0.0/0',group='')         # bootstrap the held view (warm, empty)
  e0,m0=etr.bytes(),ms.bytes()
  for i,p in enumerate(pf): etr.call('database_mapping.add',iid='0',prefix=p,group='',rloc_set=[f'192.0.2.{1+i%250}'])
  assert ms.call('database_mapping.wait',iid='0',prefix=pf[-1],group='',present=True)=='good'
  e1,m1=etr.bytes(),ms.bytes()
  # steady-state refresh with no change: re-publish every cell unchanged (what liveness-by-freshness would cost)
  for i,p in enumerate(pf[:-1]): etr.call('database_mapping.add',iid='0',prefix=p,group='',rloc_set=[f'192.0.2.{1+i%250}'])
  # the last cell is re-published with a marker RLOC so the holder's convergence can be OBSERVED (no sleep);
  # its bytes are part of the measurement
  etr.call('database_mapping.add',iid='0',prefix=pf[-1],group='',rloc_set=['203.0.113.1'])
  assert ms.call('database_mapping.wait',iid='0',prefix=pf[-1],group='',present=True,rloc='203.0.113.1',timeout_s=10)=='good'
  e2,m2=etr.bytes(),ms.bytes()
  # one change: the first prefix moves to a new RLOC
  etr.call('database_mapping.add',iid='0',prefix=pf[0],group='',rloc_set=['198.51.100.99'])
  assert ms.call('database_mapping.wait',iid='0',prefix=pf[0],group='',present=True,rloc='198.51.100.99',timeout_s=10)=='good'
  e3,m3=etr.bytes(),ms.bytes()
  udp,pk=udp_register_bytes(etr,n)
  rows.append((n,e1-e0,m1-m0,e2-e1,m2-m1,e3-e2,m3-m2,udp,pk))
 finally:
  for p in prefixes(n,base):
   try:etr.call('database_mapping.delete',iid='0',prefix=p,group='')
   except Exception:pass
  etr.close(); ms.close()
print('N = database-mapping entries (1 RLOC each). Native = FNW1 frame bytes (out+in) per participant. UDP = full Map-Register(s).')
print(f"{'N':>4} | {'establish ETR':>13} {'MS':>8} | {'refresh ETR':>11} {'MS':>8} | {'change ETR':>10} {'MS':>6} | {'UDP register':>12} {'pkts':>4} {'w/ hdrs':>8}")
for n,ee,me,er,mr,ec,mc,udp,pk in rows:
 print(f"{n:>4} | {ee:>13} {me:>8} | {er:>11} {mr:>8} | {ec:>10} {mc:>6} | {udp:>12} {pk:>4} {udp+28*pk:>8}")
print('A conventional ETR sends the full UDP register(s) at establishment, every 60 s refresh, and on change.')
