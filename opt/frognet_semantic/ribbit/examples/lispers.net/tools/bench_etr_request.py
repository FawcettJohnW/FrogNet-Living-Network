#!/usr/bin/env python3
# End-to-end request cost through the JSON-lines command interface, warmed held views, FNW1 backend.
# Compares the ETR path (wire.etr_request4, held database mapping) with the Map-Server path (wire.request4,
# held registration view) in the same process and session. Includes JSON parse, hex decode/encode and pipe
# I/O on both sides of the adapter: this is interface cost, not a microbenchmark of the lookup.
import json,subprocess,struct,time,statistics,sys,threading,socket
import os as _os; RAM_HOST=_os.environ.get('RIBBIT_RAM_HOST','127.0.0.1'); RAM_PORT=_os.environ.get('RIBBIT_RAM_PORT','8788')
BIN='./ribbit_cpp/ribbit-lisp'; N=int(sys.argv[1]) if len(sys.argv)>1 else 20000
p=subprocess.Popen([BIN,'--ram',RAM_HOST,RAM_PORT],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True,bufsize=1)
def call(op,**a):
 p.stdin.write(json.dumps({'operation':op,'args':a})+'\n');p.stdin.flush();r=json.loads(p.stdout.readline())
 if not r['ok']: raise RuntimeError(r['error'])
 return r['result']
def req4(t,n):
 x=struct.pack('!I',(1<<28)|1)+struct.pack('=Q',n)+struct.pack('!H',0)+struct.pack('!H',1)+bytes((192,0,2,1))
 return (x+bytes((0,32))+struct.pack('!H',1)+bytes(t)).hex()
def req6(t,n):
 x=struct.pack('!I',(1<<28)|1)+struct.pack('=Q',n)+struct.pack('!H',0)+struct.pack('!H',2)+socket.inet_pton(socket.AF_INET6,'2001:db8:ffff::1')
 return (x+bytes((0,128))+struct.pack('!H',2)+socket.inet_pton(socket.AF_INET6,t)).hex()
def reg4(prefix,mask,rloc):
 x=struct.pack('!I',(3<<28)|0x800|1)+struct.pack('=QBB',7,0,0)+struct.pack('!H',0)
 x+=struct.pack('!IBBHHH',0x8000003c,1,mask,0,0,1)+bytes(prefix)+struct.pack('!BBBBHH',1,100,0,0,1,1)+bytes(rloc)
 return x.hex()
def run(op,hexes):
 lines=''.join(json.dumps({'operation':op,'args':{'hex':h}})+'\n' for h in hexes)
 def feed(): p.stdin.write(lines); p.stdin.flush()   # separate writer: the pipes must not deadlock
 t0=time.perf_counter(); th=threading.Thread(target=feed); th.start()
 for _ in hexes:
  r=json.loads(p.stdout.readline()); assert r['ok'],r
 el=time.perf_counter()-t0; th.join()
 return el/len(hexes)*1e6
try:
 for pf,r in (('198.51.0.0/16','192.0.2.161'),('198.51.128.0/17','192.0.2.171')):
  call('database_mapping.add',iid='0',prefix=pf,group='',rloc_set=[r]); call('database_mapping.wait',iid='0',prefix=pf,group='',present=True)
 call('site.add',iid='0',prefix='198.51.200.0/24',group=''); call('resolver.wait_site',iid='0',prefix='198.51.200.0/24',group='',active=True)
 assert call('wire.register4',hex=reg4((198,51,200,0),24,(192,0,2,99)))=='good'
 call('resolver.wait',iid='0',prefix='198.51.200.0/24',group='',present=True)
 for pf,r in (('2001:db8:e9::/48','2001:db8:ffff::248'),('2001:db8:e9:8000::/49','2001:db8:ffff::249')):
  call('database_mapping.add',iid='0',prefix=pf,group='',rloc_set=[r]); call('database_mapping.wait',iid='0',prefix=pf,group='',present=True)
 hx6=[req6('2001:db8:e9:c800::%x'%(1+i%200),i) for i in range(N)]
 hx=[req4((198,51,200,1+(i%200)),i) for i in range(N)]
 run('wire.etr_request4',hx[:2000]); run('wire.request4',hx[:2000])        # warm
 run('wire.etr_request6',hx6[:2000])
 etr=[];ms=[];e6=[]
 for _ in range(5): etr.append(run('wire.etr_request4',hx)); ms.append(run('wire.request4',hx)); e6.append(run('wire.etr_request6',hx6))
 call('etr_map_server.add',address='192.0.2.252',alg='sha1',key_id=1,password='etr-secret',want_map_notify=True,site_id=85)
 call('etr_map_server.wait',address='192.0.2.252',present=True)
 reg=[];regh=[]
 for _ in range(5):
  lines=''.join(json.dumps({'operation':'wire.etr_register4','args':{'ms_key_id':1,'ms_alg':'sha1','ms_password':'etr-secret','want_map_notify':True,'xtr_id':'0123456789abcdef0011223344556677','site_id':'55','nonce':'%016x'%(0xaabbccdddfdfdf00+i)}})+'\n' for i in range(N//4))
  def feed(): p.stdin.write(lines); p.stdin.flush()
  t0=time.perf_counter(); th=threading.Thread(target=feed); th.start()
  for _ in range(N//4):
   r=json.loads(p.stdout.readline()); assert r['ok'],r
  reg.append((time.perf_counter()-t0)/(N//4)*1e6); th.join()
  lines=''.join(json.dumps({'operation':'wire.etr_register4','args':{'map_server':'192.0.2.252','xtr_id':'0123456789abcdef0011223344556677','nonce':'%016x'%(0xaabbccdddfdfdf00+i)}})+'\n' for i in range(N//4))
  t0=time.perf_counter(); th=threading.Thread(target=feed); th.start()
  for _ in range(N//4):
   r=json.loads(p.stdout.readline()); assert r['ok'],r
  regh.append((time.perf_counter()-t0)/(N//4)*1e6); th.join()
 call('site.add',iid='0',prefix='198.51.0.0/16',group='',accept_more_specifics=True,key_id=1,password='etr-secret')
 notify=call('wire.register4_notify',hex=call('wire.etr_register4',map_server='192.0.2.252',xtr_id='01'))['notify_hex']
 ack=[]
 for _ in range(5):
  lines=''.join(json.dumps({'operation':'wire.etr_notify4','args':{'hex':notify,'source':'192.0.2.252'}})+'\n' for i in range(N//4))
  def feed(): p.stdin.write(lines); p.stdin.flush()
  t0=time.perf_counter(); th=threading.Thread(target=feed); th.start()
  for _ in range(N//4):
   r=json.loads(p.stdout.readline()); assert r['ok'] and r['result']['result']=='good',r
  ack.append((time.perf_counter()-t0)/(N//4)*1e6); th.join()
 st=call('resolver.stats')
 print(f'N={N} per run, 5 runs, pipelined through the JSON-lines interface, FNW1 backend, warmed')
 print(f'wire.etr_request4 (ETR, held database mapping): median {statistics.median(etr):.2f} us/request  runs {[round(x,2) for x in etr]}')
 print(f'wire.request4     (Map-Server, held registration): median {statistics.median(ms):.2f} us/request  runs {[round(x,2) for x in ms]}')
 print(f'wire.etr_request6 (ETR, held database mapping): median {statistics.median(e6):.2f} us/request  runs {[round(x,2) for x in e6]}')
 print(f'wire.etr_register4 (ETR Map-Register, 2 IPv4 records, HMAC-SHA-1): median {statistics.median(reg):.2f} us/register  runs {[round(x,2) for x in reg]}')
 print(f'wire.etr_register4 (held map-server configuration): median {statistics.median(regh):.2f} us/register  runs {[round(x,2) for x in regh]}')
 print(f'wire.etr_notify4 (verify Map-Notify, build Map-Notify-Ack, HMAC-SHA-1): median {statistics.median(ack):.2f} us/notify  runs {[round(x,2) for x in ack]}')
 print(f'map_server_reads after runs: {st.get("map_server_reads")}')
 print(f'database_mapping_reads after runs: {st.get("database_mapping_reads")}  request_reads: {st.get("request_reads", st.get("reads"))}')
finally:
 for pf in ('198.51.0.0/16','198.51.128.0/17','2001:db8:e9::/48','2001:db8:e9:8000::/49'):
  try:call('database_mapping.delete',iid='0',prefix=pf,group='')
  except Exception:pass
 try:call('site.delete',iid='0',prefix='198.51.0.0/16',group='')
 except Exception:pass
 try:call('etr_map_server.delete',address='192.0.2.252')
 except Exception:pass
 try:call('site.delete',iid='0',prefix='198.51.200.0/24',group='')
 except Exception:pass
 try:call('registration.delete',iid='0',prefix='198.51.200.0/24',group='')
 except Exception:pass
 p.terminate()
