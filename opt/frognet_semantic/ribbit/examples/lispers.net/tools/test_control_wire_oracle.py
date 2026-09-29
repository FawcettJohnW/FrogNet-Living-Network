#!/usr/bin/env python3
import argparse,subprocess,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tests.test_wire_contract import reg4,req4
from control_wire_oracle import load,register4,reply4,etr_register4,etr_notify_ack
p=argparse.ArgumentParser();p.add_argument('control_root');p.add_argument('--ribbit',default='./ribbit_cpp/ribbit-lisp');a=p.parse_args()
l=load(a.control_root)
assert reg4()==register4(l),'Map-Register fixture differs from control encoder'
proc=subprocess.Popen([a.ribbit],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
def call(op,**args):
 proc.stdin.write(json.dumps({'operation':op,'args':args})+'\n');proc.stdin.flush();o=json.loads(proc.stdout.readline());assert o['ok'],o;return o['result']
call('site.add',iid='0',prefix='198.18.70.0/24',group='');call('wire.register4',hex=reg4().hex());got=bytes.fromhex(call('wire.request4',hex=req4().hex()));expected=reply4(l)
for pf,r in (('198.18.0.0/16','192.0.2.16'),('198.18.128.0/17','192.0.2.17')): call('database_mapping.add',iid='0',prefix=pf,group='',rloc_set=[r])
etr=bytes.fromhex(call('wire.etr_register4',ms_key_id=1,ms_alg='sha1',ms_password='etr-secret',want_map_notify=True,
    merge=False,proxy_reply=False,xtr_id='0123456789abcdef0011223344556677',site_id='55',nonce='aabbccdddfdfdf01'))
etr_expected=etr_register4(l)
# a Map-Notify for that register from our Map-Server, then the ETR's Map-Notify-Ack vs the control's
call('site.add',iid='0',prefix='198.18.0.0/16',group='',accept_more_specifics=True,key_id=1,password='etr-secret')
notify=bytes.fromhex(call('wire.register4_notify',hex=etr.hex())['notify_hex'])
call('etr_map_server.add',address='192.0.2.250',alg='sha1',key_id=1,password='etr-secret',want_map_notify=True,site_id=85)
ack=call('wire.etr_notify4',hex=notify.hex(),source='192.0.2.250')
ack=bytes.fromhex(ack['ack_hex']); ack_expected=etr_notify_ack(l,notify,'etr-secret')
proc.terminate();assert got==expected, f'Map-Reply differs\nours={got.hex()}\ncontrol={expected.hex()}'
assert etr==etr_expected, f'ETR Map-Register differs\nours={etr.hex()}\ncontrol={etr_expected.hex()}'
assert ack==ack_expected, f'ETR Map-Notify-Ack differs\nours={ack.hex()}\ncontrol={ack_expected.hex()}'
print('PASS register4 fixture == control encoder')
print('PASS ribbit reply4 == control encoder')
print('PASS ribbit etr_register4 == control lisp_build_map_register')
print('PASS ribbit etr_notify4 ack == control lisp_send_map_notify_ack')
