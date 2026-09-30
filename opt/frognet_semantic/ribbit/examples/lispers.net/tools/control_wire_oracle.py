#!/usr/bin/env python3
"""Offline byte oracle using Dino Farinacci's own lisp.py packet classes.
Stubs optional runtime modules; exercises only pure packet encoders."""
import argparse,sys,types,importlib.util
from pathlib import Path

def load(root):
    for n in ['netifaces','ecdsa','chacha','poly1305','geopy','curve25519','distro']:
        sys.modules.setdefault(n,types.ModuleType(n))
    if 'Crypto' not in sys.modules:
        crypto=types.ModuleType('Crypto');cipher=types.ModuleType('Crypto.Cipher');cipher.AES=object();crypto.Cipher=cipher;sys.modules['Crypto']=crypto;sys.modules['Crypto.Cipher']=cipher
    p=Path(root)/'lisp'/'lisp.py';spec=importlib.util.spec_from_file_location('dino_lisp',p);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

def register4(l):
    mr=l.lisp_map_register();mr.record_count=1;mr.nonce=0x1122334455667788;mr.use_ttl_for_timeout=True
    er=l.lisp_eid_record();er.record_ttl=0x8000003c;er.rloc_count=1;er.eid=l.lisp_address(l.LISP_AFI_IPV4,'198.18.70.0',32,0);er.eid.mask_len=24
    rr=l.lisp_rloc_record();rr.priority=1;rr.weight=100;rr.reach_bit=True;rr.rloc=l.lisp_address(l.LISP_AFI_IPV4,'192.0.2.70',32,0)
    return mr.encode()+er.encode()+rr.encode()

def reply4(l):
    mr=l.lisp_map_reply();mr.record_count=1;mr.nonce=0x8877665544332211
    # record TTL 1440: what lispers.net's map-server puts in a proxy Map-Reply (lisp_ms_process_map_request "ttl = 1440";
    # observed on the running control, acceptance L1.14). This oracle used to assume the registration's TTL.
    er=l.lisp_eid_record();er.record_ttl=1440;er.rloc_count=1;er.authoritative=True;er.eid=l.lisp_address(l.LISP_AFI_IPV4,'198.18.70.0',32,0);er.eid.mask_len=24
    rr=l.lisp_rloc_record();rr.priority=1;rr.weight=100;rr.reach_bit=True;rr.rloc=l.lisp_address(l.LISP_AFI_IPV4,'192.0.2.70',32,0)
    return mr.encode()+er.encode()+rr.encode()

def etr_register4(l):
    """What lisp-etr.py lisp_build_map_register sends to one map-server for two IPv4 database-mappings
    (198.18.0.0/16 -> 192.0.2.16, 198.18.128.0/17 -> 192.0.2.17), built with the control's own classes:
    xtr-id present, use-TTL-for-timeout, map-notify requested, record TTL LISP_REGISTER_TTL, authoritative,
    R bit, L bit clear (host-dependent in the control), xTR-ID/site-ID trailer, HMAC-SHA-1 over the whole
    packet (lisp_compute_auth)."""
    mr=l.lisp_map_register();mr.nonce=0xaabbccdddfdfdf00+1;mr.xtr_id_present=True;mr.use_ttl_for_timeout=True
    mr.map_notify_requested=True;mr.merge_register_requested=False;mr.proxy_reply_requested=False
    mr.alg_id=l.LISP_SHA_1_96_ALG_ID;mr.key_id=1;mr.xtr_id=0x0123456789abcdef0011223344556677;mr.site_id=0x55
    recs=b"";n=0
    for eid,mask,rloc in (('198.18.0.0',16,'192.0.2.16'),('198.18.128.0',17,'192.0.2.17')):
        er=l.lisp_eid_record();er.rloc_count=1;er.authoritative=True;er.record_ttl=l.LISP_REGISTER_TTL
        er.eid=l.lisp_address(l.LISP_AFI_IPV4,eid,32,0);er.eid.mask_len=mask
        rr=l.lisp_rloc_record();rr.priority=1;rr.weight=100;rr.reach_bit=True;rr.local_bit=False
        rr.rloc=l.lisp_address(l.LISP_AFI_IPV4,rloc,32,0)
        recs+=er.encode()+rr.encode();n+=1
    mr.record_count=n
    packet=mr.encode()+recs+mr.encode_xtr_id(b"")
    return l.lisp_compute_auth(packet,mr,"etr-secret")

def etr_notify_ack(l,notify,password):
    """What the control's ETR sends back for a received Map-Notify (lisp.py lisp_process_map_notify ->
    lisp_send_map_notify_ack): same nonce, key-id and alg; type Map-Notify-Ack; record count 0 (the control
    sets it to 0 while still copying the EID-records); HMAC with the map-server password."""
    mn=l.lisp_map_notify("");rest=mn.decode(notify)
    assert rest is not None,'control could not decode the notify'
    mn.map_notify_ack=True;mn.record_count=0
    return mn.encode(mn.eid_records,password)

def main():
    a=argparse.ArgumentParser();a.add_argument('control_root');x=a.parse_args();l=load(x.control_root)
    print('register4='+register4(l).hex());print('reply4='+reply4(l).hex());print('etr_register4='+etr_register4(l).hex())
if __name__=='__main__':main()
