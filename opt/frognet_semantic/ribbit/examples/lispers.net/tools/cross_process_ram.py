#!/usr/bin/env python3
# Two independent Ribbit-LISP processes over one FNW1 RAM: shared visibility and replacement visibility.
# v0.35: the reader OBSERVES convergence (map_cache.wait on the held view, optionally for a specific RLOC)
# instead of asserting that another process's write is visible immediately. Writers stay asynchronous.
import json, subprocess, sys
exe,host,port=sys.argv[1],sys.argv[2],sys.argv[3]
def start(): return subprocess.Popen([exe,'--ram',host,port],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
def call(p,op,**args):
 p.stdin.write(json.dumps({'operation':op,'args':args})+'\n');p.stdin.flush(); r=json.loads(p.stdout.readline());
 if not r['ok']: raise RuntimeError(r['error'])
 return r['result']
a,b=start(),start(); prefix='198.18.77.0/24'
try:
 assert call(a,'map_cache.add',iid='77',prefix=prefix,group='',rloc_set=['192.0.2.1'])=='good'
 assert call(b,'map_cache.wait',iid='77',prefix=prefix,group='',present=True,rloc='192.0.2.1')=='good'
 x=call(b,'map_cache.get',iid='77',prefix='198.18.77.9/32',group=''); assert x['rlocs'][0]['address']=='192.0.2.1',x
 assert call(a,'map_cache.add',iid='77',prefix=prefix,group='',rloc_set=['192.0.2.2'])=='good'
 assert call(b,'map_cache.wait',iid='77',prefix=prefix,group='',present=True,rloc='192.0.2.2')=='good'
 y=call(b,'map_cache.get',iid='77',prefix='198.18.77.9/32',group=''); assert y['rlocs'][0]['address']=='192.0.2.2',y
 print('cross_process_visibility=PASS'); print('replacement_visibility=PASS')
finally:
 try: call(a,'map_cache.delete',iid='77',prefix=prefix,group='')
 except: pass
 for p in (a,b): p.terminate(); p.wait(timeout=2)
