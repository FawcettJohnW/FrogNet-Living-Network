#!/usr/bin/env python3
import json, subprocess, sys, time
import os as _os; RAM_HOST=_os.environ.get('RIBBIT_RAM_HOST','127.0.0.1'); RAM_PORT=_os.environ.get('RIBBIT_RAM_PORT','8788')

BIN='./ribbit_cpp/ribbit-lisp'
class Client:
    def __init__(self):
        self.p=subprocess.Popen([BIN,'--ram',RAM_HOST,RAM_PORT],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
    def call(self,op,**args):
        self.p.stdin.write(json.dumps({'operation':op,'args':args})+'\n'); self.p.stdin.flush()
        out=json.loads(self.p.stdout.readline())
        if not out.get('ok'): raise RuntimeError(out.get('error'))
        return out['result']
    def close(self):
        self.p.terminate(); self.p.wait(timeout=5)

def rlocs(x): return [r['address'] for r in x.get('rlocs',[])] if isinstance(x,dict) else []

r=Client(); w=Client()
try:
    iid='9001'; group='held-cross-process'; p='203.0.113.160/27'; target='203.0.113.161/32'
    # Warm an empty resolver participant before any length exists.
    assert r.call('resolution.get',iid=iid,prefix=target,group=group)==[]
    assert r.call('resolver.stats')['request_reads']>=1
    # The Map-Server's site policy must authorize the registration (v0.42: no site, no registration).
    assert w.call('site.add',iid=iid,prefix=p,group=group,accept_more_specifics=False)=='good'
    assert w.call('resolver.wait_site',iid=iid,prefix=p,group=group,active=True)=='good'
    # A different process publishes registration + governance + manifest truth.
    assert w.call('registration.put',iid=iid,prefix=p,group=group,ttl=0x80000001,use_register_ttl=True,
                  rloc_set=['192.0.2.210'])=='good'
    assert r.call('resolver.wait',iid=iid,prefix=p,group=group,present=True)=='good'
    assert rlocs(r.call('resolution.get',iid=iid,prefix=target,group=group))==['192.0.2.210']
    assert r.call('resolver.stats')['request_reads']==0
    for _ in range(5):
        assert rlocs(r.call('resolution.get',iid=iid,prefix=target,group=group))==['192.0.2.210']
        assert r.call('resolver.stats')['request_reads']==0
    # Expiry is derived from held value + clock; no reaper/write is needed.
    deadline=time.monotonic()+3
    while time.monotonic()<deadline and r.call('resolution.get',iid=iid,prefix=target,group=group)!=[]: pass
    assert r.call('resolution.get',iid=iid,prefix=target,group=group)==[]
    assert r.call('resolver.stats')['request_reads']==0
    # Republish, then source-authorized TTL-0 withdrawal through the same wake path.
    assert w.call('registration.put',iid=iid,prefix=p,group=group,ttl=60,rloc_set=['192.0.2.211'])=='good'
    assert r.call('resolver.wait',iid=iid,prefix=p,group=group,present=True,rloc='192.0.2.211')=='good'
    assert rlocs(r.call('resolution.get',iid=iid,prefix=target,group=group))==['192.0.2.211']
    assert w.call('registration.put',iid=iid,prefix=p,group=group,ttl=0,source='192.0.2.211',rloc_set=['192.0.2.211'])=='good'
    assert r.call('resolver.wait',iid=iid,prefix=p,group=group,present=False)=='good'
    assert r.call('resolution.get',iid=iid,prefix=target,group=group)==[]
    assert r.call('resolver.stats')['request_reads']==0
    # Administrative deregistration is governor-owned inactive truth, also held.
    assert w.call('registration.put',iid=iid,prefix=p,group=group,ttl=60,rloc_set=['192.0.2.213'])=='good'
    assert r.call('resolver.wait',iid=iid,prefix=p,group=group,present=True,rloc='192.0.2.213')=='good'
    assert w.call('registration.delete',iid=iid,prefix=p,group=group)=='good'
    assert r.call('resolver.wait',iid=iid,prefix=p,group=group,present=False)=='good'
    assert r.call('resolution.get',iid=iid,prefix=target,group=group)==[]
    # Public site policy joins the same held participant; secrets do not.
    site='203.0.114.0/24'; child='203.0.114.128/25'
    assert w.call('site.add',iid=iid,prefix=site,group=group,accept_more_specifics=True,password='private-only')=='good'
    assert r.call('resolver.wait_site',iid=iid,prefix=site,group=group,active=True)=='good'
    assert r.call('registration.put',iid=iid,prefix=child,group=group,ttl=60,rloc_set=['192.0.2.212'])=='good'
    assert r.call('resolver.stats')['authorization_reads']==0
    assert r.call('resolver.wait',iid=iid,prefix=child,group=group,present=True)=='good'
    assert rlocs(r.call('resolution.get',iid=iid,prefix='203.0.114.129/32',group=group))==['192.0.2.212']
    print('PASS cross-process warm wake, zero request reads, expiry, withdrawal')
    print('PASS held site-policy wake and zero warmed authorization reads')
finally:
    try: w.call('site.delete',iid='9001',prefix='203.0.113.160/27',group='held-cross-process')
    except Exception: pass
    r.close(); w.close()
