#!/usr/bin/env python3
# Atomicity of a multi-record Map-Register while site policy changes underneath it (FNW1).
# Each round: publish a site for record 1 only, IMMEDIATELY send a two-record register (record 2 has no site),
# then check that record 1 was not applied unless the register was accepted as a whole.
import json,subprocess,struct,sys
import os as _os; RAM_HOST=_os.environ.get('RIBBIT_RAM_HOST','127.0.0.1'); RAM_PORT=_os.environ.get('RIBBIT_RAM_PORT','8788')
sys.path.insert(0,'.')
from tests.test_wire_contract import reg4_multi
BIN=sys.argv[1] if len(sys.argv)>1 else './ribbit_cpp/ribbit-lisp'; N=int(sys.argv[2]) if len(sys.argv)>2 else 200
p=subprocess.Popen([BIN,'--ram',RAM_HOST,RAM_PORT],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
def call(op,**a):
 p.stdin.write(json.dumps({'operation':op,'args':a})+'\n');p.stdin.flush();r=json.loads(p.stdout.readline())
 if not r['ok']: raise RuntimeError(r['error'])
 return r['result']
broken=0; outcomes={}
for i in range(N):
 a,b=10+(i%200),(i//200)
 p1=f'198.30.{a}.0/24'; p2=f'198.31.{a}.0/24'
 raw=reg4_multi([{'prefix':(198,30,a,0),'mask':24,'rlocs':[{'address':(192,0,2,a)}]},
                 {'prefix':(198,31,a,0),'mask':24,'rlocs':[{'address':(192,0,2,a)}]}],nonce=0x1000+i)
 call('site.add',iid='0',prefix=p1,group='',accept_more_specifics=False)
 r=call('wire.register4',hex=raw.hex()); outcomes[r]=outcomes.get(r,0)+1
 got=call('resolution.get',iid='0',prefix=f'198.30.{a}.1/32',group='')
 applied=isinstance(got,dict) and got.get('prefix')==p1
 if r!='good' and applied: broken+=1
 for pf in (p1,p2):
  try:call('registration.delete',iid='0',prefix=pf,group='')
  except Exception:pass
 call('site.delete',iid='0',prefix=p1,group='')
 call('resolver.wait_site',iid='0',prefix=p1,group='',active=False)
p.terminate()
print(f'binary={BIN} rounds={N} outcomes={outcomes} partial_applications={broken}')
