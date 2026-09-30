#!/usr/bin/env python3
# Steady-state cost of native liveness: an ETR with N database mappings refreshes ONE cell per interval regardless of
# N; a resolver holding the liveness cells receives one cell per beat. Conventional: every 60 s one full Map-Register
# per map-server carrying all N records (60+28N bytes, IPv4, one RLOC, SHA-1, xTR-ID trailer; <=20 records/packet).
import json,subprocess,sys
import os as _os; RAM_HOST=_os.environ.get('RIBBIT_RAM_HOST','127.0.0.1'); RAM_PORT=_os.environ.get('RIBBIT_RAM_PORT','8788')
def P(): return subprocess.Popen(['./ribbit_cpp/ribbit-lisp','--ram',RAM_HOST,RAM_PORT],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
def call(p,op,**a):
 p.stdin.write(json.dumps({'operation':op,'args':a})+'\n');p.stdin.flush();r=json.loads(p.stdout.readline())
 if not r['ok']: raise RuntimeError(r['error'])
 return r['result']
def b(p): s=call(p,'transport.stats'); return s['bytes_out']+s['bytes_in']
BEATS=10
etr=P(); itr=P()
try:
 call(etr,'etr.identity',name='etr-lb',xtr_id='2b2b')
 call(itr,'resolution.get',iid='0',prefix='203.0.113.1/32',group='')      # warm the resolver (holds liveness)
 call(etr,'etr_liveness.start',interval_s=0.2,lifetime_s=2)
 import time
 time.sleep(0.5)                                    # measurement only (not a convergence test): let beats flow
 s0e,s0i=call(etr,'transport.stats'),call(itr,'transport.stats')
 time.sleep(3.0)                                    # ~15 beats at 0.2 s
 s1e,s1i=call(etr,'transport.stats'),call(itr,'transport.stats')
 beats=(s1e['raw']+s1e['repeat'])-(s0e['raw']+s0e['repeat'])-1   # requests sent by the ETR in the window, minus the stats call
 de=(s1e['bytes_out']+s1e['bytes_in'])-(s0e['bytes_out']+s0e['bytes_in'])
 di=(s1i['bytes_out']+s1i['bytes_in'])-(s0i['bytes_out']+s0i['bytes_in'])
 # each beat is one liveness write plus one bounded held read on the ETR's control cell (two requests)
 nb=max(1,beats//2)
 print(f'native liveness, {nb} heartbeats in 3 s: ETR {de/nb:.0f} B per beat (write + held read), resolver {di/nb:.0f} B per beat '
       f'(its held liveness view receiving the cell) — independent of the number of database mappings')
 for n in (1,10,100):
  chunks=[min(20,n-s) for s in range(0,n,20)]; udp=sum(60+28*k for k in chunks)
  print(f'conventional refresh, N={n}: {udp} B of Map-Register(s) per map-server every 60 s ({udp+28*len(chunks)} with IP/UDP headers)')
finally:
 try:call(etr,'etr_liveness.stop')
 except Exception:pass
 etr.terminate(); itr.terminate()
