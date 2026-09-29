import os, socket, struct, unittest, hmac, hashlib
from conformance.normalize import canonical


def reg4(prefix=(198,18,70,0), mask=24, rloc=(192,0,2,70), ttl=0x8000003c, nonce=0x1122334455667788, rloc2=None):
    first=(3<<28)|0x800|1 # Map-Register, T-bit, one record
    p=struct.pack('!I',first)+struct.pack('=QBB',nonce,0,0)+struct.pack('!H',0)
    count=2 if rloc2 else 1
    p+=struct.pack('!IBBHHH',ttl,count,mask,0,0,1)+bytes(prefix)
    p+=struct.pack('!BBBBHH',1,100,0,0,1,1)+bytes(rloc)
    if rloc2:p+=struct.pack('!BBBBHH',2,50,0,0,1,1)+bytes(rloc2)
    return p

def req4(target=(198,18,70,9), mask=32, nonce=0x8877665544332211):
    first=(1<<28)|1 # Map-Request, one record, ITR-RLOC-count encoded as 0 => one
    p=struct.pack('!I',first)+struct.pack('=Q',nonce)
    p+=struct.pack('!H',0) # source-EID AFI none
    p+=struct.pack('!H',1)+bytes((192,0,2,1)) # one ITR-RLOC
    p+=bytes((0,mask))+struct.pack('!H',1)+bytes(target)
    return p

def reg4_multi(records, nonce=0x2233445566778899, flags=0x800):
    """Build a Map-Register containing independent IPv4 mapping records."""
    first=(3<<28)|flags|len(records)
    p=struct.pack('!I',first)+struct.pack('=QBB',nonce,0,0)+struct.pack('!H',0)
    for rec in records:
        prefix=rec['prefix']; mask=rec['mask']; ttl=rec.get('ttl',0x8000003c); rlocs=rec['rlocs']
        p+=struct.pack('!IBBHHH',ttl,len(rlocs),mask,0,0,1)+bytes(prefix)
        for rloc in rlocs:
            address=rloc['address']; priority=rloc.get('priority',1); weight=rloc.get('weight',100)
            p+=struct.pack('!BBBBHH',priority,weight,0,0,1,1)+bytes(address)
    return p

def auth_reg4_multi(records, password, key_id=12, flags=0x900, nonce=0x33445566778899aa):
    raw=bytearray(reg4_multi(records,nonce=nonce,flags=flags))
    raw[12]=key_id; raw[13]=1; raw[14:16]=struct.pack('!H',20); raw[16:16]=bytes(20)
    raw[16:36]=hmac.new(password.encode(),bytes(raw),hashlib.sha1).digest()
    return bytes(raw)

def mapping_prefix(x): return x.get('prefix') if isinstance(x,dict) else None
def rloc_addresses(x): return [r['address'] for r in x.get('rlocs',[])] if isinstance(x,dict) else []

class WireContract(unittest.TestCase):
    adapter=None
    def need(self,*ops):
        miss=[x for x in ops if x not in self.adapter.capabilities()]
        if miss:self.skipTest('adapter lacks '+','.join(miss))
    def mutable(self):
        if os.getenv('LISP_ALLOW_MUTATION')!='1':self.skipTest('set LISP_ALLOW_MUTATION=1')
    def test_map_register_to_map_request_to_map_reply_ipv4(self):
        self.need('wire.register4','wire.request4','registration.delete');self.mutable()
        p='198.18.70.0/24'; nonce=0x8877665544332211
        try:
            self.assertEqual(self.adapter.call('wire.register4',hex=reg4().hex()),'good')
            if 'resolver.wait' in self.adapter.capabilities(): self.assertEqual(self.adapter.call('resolver.wait',iid='0',prefix=p,group='',present=True),'good')
            out=self.adapter.call('wire.request4',hex=req4(nonce=nonce).hex())
            b=bytes.fromhex(out)
            self.assertGreaterEqual(len(b),40)
            first=struct.unpack('!I',b[:4])[0]
            self.assertEqual(first>>28,2);self.assertEqual(first&0xff,1)
            self.assertEqual(struct.unpack('=Q',b[4:12])[0],nonce)
            self.assertEqual(struct.unpack('!H',b[22:24])[0],1)
            self.assertEqual(socket.inet_ntoa(b[24:28]),'198.18.70.0')
            self.assertEqual(struct.unpack('!H',b[34:36])[0],1)
            self.assertEqual(socket.inet_ntoa(b[36:40]),'192.0.2.70')
        finally:
            try:self.adapter.call('registration.delete',iid='0',prefix=p,group='')
            except Exception:pass

    def test_two_record_map_register_applies_both_records(self):
        self.need('wire.register4','wire.request4','registration.delete');self.mutable()
        p1='198.18.74.0/24'; p2='198.18.75.128/25'
        raw=reg4_multi([
            {'prefix':(198,18,74,0),'mask':24,'rlocs':[{'address':(192,0,2,74)}]},
            {'prefix':(198,18,75,128),'mask':25,'rlocs':[{'address':(192,0,2,75),'priority':2,'weight':80}]},
        ])
        try:
            self.assertEqual(self.adapter.call('wire.register4',hex=raw.hex()),'good')
            if 'resolver.wait' in self.adapter.capabilities():
                self.assertEqual(self.adapter.call('resolver.wait',iid='0',prefix=p1,group='',present=True),'good')
                self.assertEqual(self.adapter.call('resolver.wait',iid='0',prefix=p2,group='',present=True),'good')
            b1=bytes.fromhex(self.adapter.call('wire.request4',hex=req4(target=(198,18,74,9)).hex()))
            b2=bytes.fromhex(self.adapter.call('wire.request4',hex=req4(target=(198,18,75,200)).hex()))
            self.assertEqual(socket.inet_ntoa(b1[36:40]),'192.0.2.74')
            self.assertEqual(socket.inet_ntoa(b2[36:40]),'192.0.2.75')
        finally:
            for pfx in (p1,p2):
                try:self.adapter.call('registration.delete',iid='0',prefix=pfx,group='')
                except Exception:pass

    def test_multirecord_malformed_later_record_is_atomic(self):
        self.need('wire.register4','resolution.get','registration.delete');self.mutable()
        p1='198.18.76.0/24'
        raw=reg4_multi([
            {'prefix':(198,18,76,0),'mask':24,'rlocs':[{'address':(192,0,2,76)}]},
            {'prefix':(198,18,77,0),'mask':24,'rlocs':[{'address':(192,0,2,77)}]},
        ])[:-3]
        try:
            with self.assertRaises(RuntimeError):self.adapter.call('wire.register4',hex=raw.hex())
            self.assertIn(canonical(self.adapter.call('resolution.get',iid='0',prefix='198.18.76.1/32',group='')),(None,[],{}))
        finally:
            for pfx in (p1,'198.18.77.0/24'):
                try:self.adapter.call('registration.delete',iid='0',prefix=pfx,group='')
                except Exception:pass

    def test_multirecord_authorization_failure_is_atomic(self):
        self.need('site.add','site.delete','wire.register4','resolution.get','registration.delete');self.mutable()
        p1='198.18.78.0/24';p2='198.18.79.0/24'
        raw=reg4_multi([
            {'prefix':(198,18,78,0),'mask':24,'rlocs':[{'address':(192,0,2,78)}]},
            {'prefix':(198,18,79,0),'mask':24,'rlocs':[{'address':(192,0,2,79)}]},
        ])
        try:
            self.adapter.call('site.add',iid='0',prefix=p1,group='',accept_more_specifics=False)
            if 'resolver.wait_site' in self.adapter.capabilities(): self.assertEqual(self.adapter.call('resolver.wait_site',iid='0',prefix=p1,group='',active=True),'good')  # observe the site before relying on it
            self.assertEqual(self.adapter.call('wire.register4',hex=raw.hex()),'unauthorized')
            self.assertIn(canonical(self.adapter.call('resolution.get',iid='0',prefix='198.18.78.1/32',group='')),(None,[],{}))
        finally:
            for pfx in (p1,p2):
                try:self.adapter.call('registration.delete',iid='0',prefix=pfx,group='')
                except Exception:pass
            self.adapter.call('site.delete',iid='0',prefix=p1,group='')
            if 'resolver.wait_site' in self.adapter.capabilities():
                self.assertEqual(self.adapter.call('resolver.wait_site',iid='0',prefix=p1,group='',active=False),'good')

    def test_multirecord_refresh_rejects_changed_later_record_atomically(self):
        self.need('wire.register4','wire.request4','registration.delete');self.mutable()
        p1='198.18.80.0/24';p2='198.18.81.0/24'
        initial=[
            {'prefix':(198,18,80,0),'mask':24,'rlocs':[{'address':(192,0,2,80)}]},
            {'prefix':(198,18,81,0),'mask':24,'rlocs':[{'address':(192,0,2,81)}]},
        ]
        changed=[initial[0],{'prefix':(198,18,81,0),'mask':24,'rlocs':[{'address':(192,0,2,181)}]}]
        try:
            self.assertEqual(self.adapter.call('wire.register4',hex=reg4_multi(initial).hex()),'good')
            if 'resolver.wait' in self.adapter.capabilities():
                self.assertEqual(self.adapter.call('resolver.wait',iid='0',prefix=p1,group='',present=True),'good')
                self.assertEqual(self.adapter.call('resolver.wait',iid='0',prefix=p2,group='',present=True),'good')
            self.assertEqual(self.adapter.call('wire.register4',hex=reg4_multi(changed,flags=0x1800).hex()),'rejected')
            b1=bytes.fromhex(self.adapter.call('wire.request4',hex=req4(target=(198,18,80,1)).hex()))
            b2=bytes.fromhex(self.adapter.call('wire.request4',hex=req4(target=(198,18,81,1)).hex()))
            self.assertEqual(socket.inet_ntoa(b1[36:40]),'192.0.2.80')
            self.assertEqual(socket.inet_ntoa(b2[36:40]),'192.0.2.81')
        finally:
            for pfx in (p1,p2):
                try:self.adapter.call('registration.delete',iid='0',prefix=pfx,group='')
                except Exception:pass

    def test_multirecord_withdrawal_applies_each_record(self):
        self.need('wire.register4','resolution.get','registration.delete');self.mutable()
        p1='198.18.82.0/24';p2='198.18.83.0/24'; src='192.0.2.82'
        live=[
            {'prefix':(198,18,82,0),'mask':24,'rlocs':[{'address':(192,0,2,82)}]},
            {'prefix':(198,18,83,0),'mask':24,'rlocs':[{'address':(192,0,2,82)}]},
        ]
        zero=[dict(x,ttl=0) for x in live]
        try:
            self.assertEqual(self.adapter.call('wire.register4',hex=reg4_multi(live).hex(),source=src),'good')
            if 'resolver.wait' in self.adapter.capabilities():
                self.assertEqual(self.adapter.call('resolver.wait',iid='0',prefix=p1,group='',present=True),'good')
                self.assertEqual(self.adapter.call('resolver.wait',iid='0',prefix=p2,group='',present=True),'good')
            self.assertEqual(self.adapter.call('wire.register4',hex=reg4_multi(zero).hex(),source=src),'good')
            if 'resolver.wait' in self.adapter.capabilities():
                self.assertEqual(self.adapter.call('resolver.wait',iid='0',prefix=p1,group='',present=False),'good')
                self.assertEqual(self.adapter.call('resolver.wait',iid='0',prefix=p2,group='',present=False),'good')
            for target in ('198.18.82.1/32','198.18.83.1/32'):
                self.assertIn(canonical(self.adapter.call('resolution.get',iid='0',prefix=target,group='')),(None,[],{}))
        finally:
            for pfx in (p1,p2):
                try:self.adapter.call('registration.delete',iid='0',prefix=pfx,group='')
                except Exception:pass

    def test_multirecord_withdrawal_source_failure_is_atomic(self):
        self.need('wire.register4','wire.request4','registration.delete');self.mutable()
        p1='198.18.86.0/24';p2='198.18.87.0/24'
        live=[
            {'prefix':(198,18,86,0),'mask':24,'rlocs':[{'address':(192,0,2,86)}]},
            {'prefix':(198,18,87,0),'mask':24,'rlocs':[{'address':(192,0,2,87)}]},
        ]
        zero=[dict(x,ttl=0) for x in live]
        try:
            self.assertEqual(self.adapter.call('wire.register4',hex=reg4_multi(live).hex()),'good')
            if 'resolver.wait' in self.adapter.capabilities():
                self.assertEqual(self.adapter.call('resolver.wait',iid='0',prefix=p1,group='',present=True),'good')
                self.assertEqual(self.adapter.call('resolver.wait',iid='0',prefix=p2,group='',present=True),'good')
            self.assertEqual(self.adapter.call('wire.register4',hex=reg4_multi(zero).hex(),source='192.0.2.86'),'ignored')
            b1=bytes.fromhex(self.adapter.call('wire.request4',hex=req4(target=(198,18,86,1)).hex()))
            b2=bytes.fromhex(self.adapter.call('wire.request4',hex=req4(target=(198,18,87,1)).hex()))
            self.assertEqual(socket.inet_ntoa(b1[36:40]),'192.0.2.86')
            self.assertEqual(socket.inet_ntoa(b2[36:40]),'192.0.2.87')
        finally:
            for pfx in (p1,p2):
                try:self.adapter.call('registration.delete',iid='0',prefix=pfx,group='')
                except Exception:pass

    def test_multirecord_notify_copies_all_records_and_authenticates_once(self):
        self.need('site.add','site.delete','wire.register4_notify','wire.auth_verify','registration.delete');self.mutable()
        parent='198.18.84.0/23'; password='multi notify secret'; nonce=0x33445566778899aa
        records=[
            {'prefix':(198,18,84,0),'mask':24,'rlocs':[{'address':(192,0,2,84)}]},
            {'prefix':(198,18,85,128),'mask':25,'rlocs':[{'address':(192,0,2,85),'priority':2,'weight':70}]},
        ]
        raw=auth_reg4_multi(records,password,key_id=14,nonce=nonce)
        try:
            self.adapter.call('site.add',iid='0',prefix=parent,group='',accept_more_specifics=True,key_id=14,password=password)
            if 'resolver.wait_site' in self.adapter.capabilities(): self.assertEqual(self.adapter.call('resolver.wait_site',iid='0',prefix=parent,group='',active=True),'good')  # observe the site before relying on it
            out=self.adapter.call('wire.register4_notify',hex=raw.hex())
            self.assertEqual(out['result'],'good'); notify=bytes.fromhex(out['notify_hex'])
            self.assertEqual(struct.unpack('!I',notify[:4])[0]>>28,4)
            self.assertEqual(struct.unpack('!I',notify[:4])[0]&0xff,2)
            self.assertEqual(struct.unpack('=Q',notify[4:12])[0],nonce)
            self.assertEqual(notify[36:],raw[36:])
            self.assertTrue(self.adapter.call('wire.auth_verify',hex=notify.hex(),password=password))
        finally:
            for pfx in ('198.18.84.0/24','198.18.85.128/25'):
                try:self.adapter.call('registration.delete',iid='0',prefix=pfx,group='')
                except Exception:pass
            self.adapter.call('site.delete',iid='0',prefix=parent,group='')
            if 'resolver.wait_site' in self.adapter.capabilities():
                self.assertEqual(self.adapter.call('resolver.wait_site',iid='0',prefix=parent,group='',active=False),'good')

    def test_multiple_locator_records_survive_wire_path(self):
        self.need('wire.register4','wire.request4','registration.delete');self.mutable()
        p='198.18.71.0/24'
        try:
            self.adapter.call('wire.register4',hex=reg4(prefix=(198,18,71,0),rloc=(192,0,2,71),rloc2=(192,0,2,72)).hex())
            if 'resolver.wait' in self.adapter.capabilities(): self.assertEqual(self.adapter.call('resolver.wait',iid='0',prefix=p,group='',present=True),'good')
            b=bytes.fromhex(self.adapter.call('wire.request4',hex=req4(target=(198,18,71,9)).hex()))
            self.assertEqual(b[16],2)
            self.assertEqual(socket.inet_ntoa(b[36:40]),'192.0.2.71')
            self.assertEqual(socket.inet_ntoa(b[48:52]),'192.0.2.72')
        finally:
            try:self.adapter.call('registration.delete',iid='0',prefix=p,group='')
            except Exception:pass
    def test_etr_map_request_answers_from_held_database_mapping_ipv4(self):
        # RFC 9301: the ETR owns the authoritative database mapping and answers Map-Requests from it,
        # choosing the best-matching EID-prefix. Map-Server registration truth must not be consulted.
        self.need('wire.etr_request4','database_mapping.add','database_mapping.delete','wire.register4','registration.delete')
        self.mutable()
        entries=[('0','198.18.0.0/16','192.0.2.16'),('0','198.18.128.0/17','192.0.2.17')]
        decoy='198.18.200.0/24'
        nonce=0x0102030405060708
        try:
            for iid,p,r in entries:
                self.assertEqual(self.adapter.call('database_mapping.add',iid=iid,prefix=p,group='',rloc_set=[r]),'good')
                if 'database_mapping.wait' in self.adapter.capabilities():
                    self.assertEqual(self.adapter.call('database_mapping.wait',iid=iid,prefix=p,group='',present=True),'good')
            # a more-specific Map-Server registration exists for the same target; the ETR must ignore it
            self.assertEqual(self.adapter.call('wire.register4',hex=reg4(prefix=(198,18,200,0),mask=24,rloc=(192,0,2,99)).hex()),'good')
            if 'resolver.wait' in self.adapter.capabilities():
                self.assertEqual(self.adapter.call('resolver.wait',iid='0',prefix=decoy,group='',present=True),'good')
            b=bytes.fromhex(self.adapter.call('wire.etr_request4',hex=req4(target=(198,18,200,1),mask=32,nonce=nonce).hex()))
            first=struct.unpack('!I',b[:4])[0]
            self.assertEqual(first>>28,2)                       # Map-Reply
            self.assertEqual(first&0xff,1)                      # exactly one record
            self.assertEqual(struct.unpack('=Q',b[4:12])[0],nonce)
            self.assertEqual(struct.unpack('!I',b[12:16])[0],1440) # control ETR: record TTL 1440 (lisp_etr_process_map_request)
            self.assertEqual(b[16],1)                           # one locator
            self.assertEqual(b[17],17)                          # EID mask length: the /17, not the /16 or the /24
            self.assertTrue(b[18]&0x10)                         # authoritative
            self.assertEqual(struct.unpack('!H',b[22:24])[0],1)
            self.assertEqual(socket.inet_ntoa(b[24:28]),'198.18.128.0')
            self.assertEqual(struct.unpack('!H',b[34:36])[0],1)
            self.assertEqual(socket.inet_ntoa(b[36:40]),'192.0.2.17')
            self.assertTrue(struct.unpack('!H',b[32:34])[0]&0x1)   # R (reachable) bit, as the control sets it
            self.assertEqual(len(b),40)                         # nothing after the one record
            if 'resolver.stats' in self.adapter.capabilities():
                st=canonical(self.adapter.call('resolver.stats'))
                if isinstance(st,dict) and 'database_mapping_reads' in st:
                    self.assertEqual(st['database_mapping_reads'],0)
        finally:
            for iid,p,_ in entries:
                try:self.adapter.call('database_mapping.delete',iid=iid,prefix=p,group='')
                except Exception:pass
            try:self.adapter.call('registration.delete',iid='0',prefix=decoy,group='')
            except Exception:pass

    def test_etr_map_request_answers_from_held_database_mapping_ipv6(self):
        # IPv6 counterpart of the IPv4 ETR contract; asserted separately, not assumed from shared code.
        self.need('wire.etr_request6','database_mapping.add','database_mapping.delete','wire.register6','registration.delete')
        self.mutable()
        entries=[('0','2001:db8:e7::/48','2001:db8:ffff::48'),('0','2001:db8:e7:8000::/49','2001:db8:ffff::49')]
        decoy='2001:db8:e7:c800::/56'
        nonce=0x1112131415161718
        try:
            for iid,p,r in entries:
                self.assertEqual(self.adapter.call('database_mapping.add',iid=iid,prefix=p,group='',rloc_set=[r]),'good')
                if 'database_mapping.wait' in self.adapter.capabilities():
                    self.assertEqual(self.adapter.call('database_mapping.wait',iid=iid,prefix=p,group='',present=True),'good')
            self.assertEqual(self.adapter.call('wire.register6',hex=reg6(prefix='2001:db8:e7:c800::',mask=56,rloc='2001:db8:ffff::99').hex()),'good')
            if 'resolver.wait' in self.adapter.capabilities():
                self.assertEqual(self.adapter.call('resolver.wait',iid='0',prefix=decoy,group='',present=True),'good')
            b=bytes.fromhex(self.adapter.call('wire.etr_request6',hex=req6(target='2001:db8:e7:c801::1',mask=128,nonce=nonce).hex()))
            first=struct.unpack('!I',b[:4])[0]
            self.assertEqual(first>>28,2)
            self.assertEqual(first&0xff,1)
            self.assertEqual(struct.unpack('=Q',b[4:12])[0],nonce)
            self.assertEqual(struct.unpack('!I',b[12:16])[0],1440)
            self.assertEqual(b[16],1)
            self.assertEqual(b[17],49)                           # the /49, not the /48 or the decoy /56
            self.assertTrue(b[18]&0x10)                          # authoritative
            self.assertEqual(struct.unpack('!H',b[22:24])[0],2)
            self.assertEqual(socket.inet_ntop(socket.AF_INET6,b[24:40]),'2001:db8:e7:8000::')
            self.assertTrue(struct.unpack('!H',b[44:46])[0]&0x1)   # R bit
            self.assertEqual(struct.unpack('!H',b[46:48])[0],2)
            self.assertEqual(socket.inet_ntop(socket.AF_INET6,b[48:64]),'2001:db8:ffff::49')
            self.assertEqual(len(b),64)
            if 'resolver.stats' in self.adapter.capabilities():
                st=canonical(self.adapter.call('resolver.stats'))
                if isinstance(st,dict) and 'database_mapping_reads' in st:
                    self.assertEqual(st['database_mapping_reads'],0)
        finally:
            for iid,p,_ in entries:
                try:self.adapter.call('database_mapping.delete',iid=iid,prefix=p,group='')
                except Exception:pass
            try:self.adapter.call('registration.delete',iid='0',prefix=decoy,group='')
            except Exception:pass

    def test_etr_map_register_from_held_database_mapping_is_accepted_by_map_server(self):
        # ETR role: build the Map-Register for the ETR's own held database mapping (lisp-etr.py
        # lisp_build_map_register), then hand it to the Map-Server path, which must authenticate it and register
        # both EID-prefixes. xTR-ID/site-ID trailer present; record TTL 3 (LISP_REGISTER_TTL); authoritative.
        self.need('wire.etr_register4','database_mapping.add','database_mapping.delete','wire.register4',
                  'site.add','site.delete','resolution.get','registration.delete')
        self.mutable()
        entries=[('198.19.0.0/16','192.0.2.26'),('198.19.128.0/17','192.0.2.27')]
        try:
            self.adapter.call('site.add',iid='0',prefix='198.19.0.0/16',group='',accept_more_specifics=True,key_id=1,password='etr-secret')
            if 'resolver.wait_site' in self.adapter.capabilities(): self.assertEqual(self.adapter.call('resolver.wait_site',iid='0',prefix='198.19.0.0/16',group='',active=True),'good')  # observe the site before relying on it
            for p,r in entries:
                self.assertEqual(self.adapter.call('database_mapping.add',iid='0',prefix=p,group='',rloc_set=[r]),'good')
                if 'database_mapping.wait' in self.adapter.capabilities():
                    self.assertEqual(self.adapter.call('database_mapping.wait',iid='0',prefix=p,group='',present=True),'good')
            b=bytes.fromhex(self.adapter.call('wire.etr_register4',ms_key_id=1,ms_alg='sha1',ms_password='etr-secret',
                want_map_notify=True,merge=False,proxy_reply=False,xtr_id='0123456789abcdef0011223344556677',site_id='55',
                nonce='aabbccdddfdfdf01'))
            first=struct.unpack('!I',b[:4])[0]
            self.assertEqual(first>>28,3)                      # Map-Register
            self.assertTrue(first&0x02000000)                  # I: xTR-ID present
            self.assertTrue(first&0x800)                       # T: use TTL for timeout
            self.assertTrue(first&0x100)                       # M: map-notify requested
            self.assertEqual(first&0xff,2)                     # both database entries, one record each
            self.assertEqual(b[12],1); self.assertEqual(b[13],1)   # key-id, SHA-1
            self.assertEqual(struct.unpack('!H',b[14:16])[0],20)
            self.assertEqual(b[-24:],bytes.fromhex('0123456789abcdef00112233445566770000000000000055'))
            self.assertEqual(self.adapter.call('wire.register4',hex=b.hex()),'good')
            for p,r in entries:
                if 'resolver.wait' in self.adapter.capabilities():
                    self.assertEqual(self.adapter.call('resolver.wait',iid='0',prefix=p,group='',present=True),'good')
            got=canonical(self.adapter.call('resolution.get',iid='0',prefix='198.19.200.1/32',group=''))
            self.assertEqual(mapping_prefix(got),'198.19.128.0/17'); self.assertEqual(rloc_addresses(got),['192.0.2.27'])
            got=canonical(self.adapter.call('resolution.get',iid='0',prefix='198.19.1.1/32',group=''))
            self.assertEqual(mapping_prefix(got),'198.19.0.0/16'); self.assertEqual(rloc_addresses(got),['192.0.2.26'])
        finally:
            for p,_ in entries:
                for op in ('database_mapping.delete','registration.delete'):
                    try:self.adapter.call(op,iid='0',prefix=p,group='')
                    except Exception:pass
            try:self.adapter.call('site.delete',iid='0',prefix='198.19.0.0/16',group='')
            except Exception:pass

    def test_etr_map_register_built_from_held_map_server_configuration(self):
        # Map-server configuration is ETR-owned truth (control: lispconfig lisp_map_server_command -> lisp_ms):
        # the register is built from the held map-server view and the held database mapping; the password
        # stays process-private and is never published. Same packet as the explicit-argument build.
        self.need('etr_map_server.add','etr_map_server.get','etr_map_server.delete','wire.etr_register4',
                  'database_mapping.add','database_mapping.delete')
        self.mutable()
        ms='192.0.2.250'; entries=[('198.20.0.0/16','192.0.2.36'),('198.20.128.0/17','192.0.2.37')]
        xtr='0123456789abcdef0011223344556677'
        try:
            for p,r in entries:
                self.assertEqual(self.adapter.call('database_mapping.add',iid='0',prefix=p,group='',rloc_set=[r]),'good')
                if 'database_mapping.wait' in self.adapter.capabilities():
                    self.assertEqual(self.adapter.call('database_mapping.wait',iid='0',prefix=p,group='',present=True),'good')
            self.assertEqual(self.adapter.call('etr_map_server.add',address=ms,alg='sha1',key_id=1,password='etr-secret',
                                               want_map_notify=True,merge=False,proxy_reply=False,refresh=False,site_id=85),'good')
            if 'etr_map_server.wait' in self.adapter.capabilities():
                self.assertEqual(self.adapter.call('etr_map_server.wait',address=ms,present=True),'good')
            cfg=canonical(self.adapter.call('etr_map_server.get',address=ms))
            self.assertEqual(cfg.get('address'),ms); self.assertEqual(cfg.get('key_id'),1); self.assertEqual(cfg.get('alg'),'sha1')
            self.assertTrue(cfg.get('want_map_notify')); self.assertEqual(cfg.get('site_id'),85)
            self.assertNotIn('password',cfg)
            held=self.adapter.call('wire.etr_register4',map_server=ms,xtr_id=xtr,nonce='aabbccdddfdfdf01')
            explicit=self.adapter.call('wire.etr_register4',ms_key_id=1,ms_alg='sha1',ms_password='etr-secret',
                                       want_map_notify=True,merge=False,proxy_reply=False,xtr_id=xtr,site_id='55',nonce='aabbccdddfdfdf01')
            self.assertEqual(held,explicit)
            self.assertEqual(self.adapter.call('etr_map_server.delete',address=ms),'good')
            if 'etr_map_server.wait' in self.adapter.capabilities():
                self.assertEqual(self.adapter.call('etr_map_server.wait',address=ms,present=False),'good')
            with self.assertRaises(Exception):
                self.adapter.call('wire.etr_register4',map_server=ms,xtr_id=xtr)
        finally:
            for p,_ in entries:
                try:self.adapter.call('database_mapping.delete',iid='0',prefix=p,group='')
                except Exception:pass
            try:self.adapter.call('etr_map_server.delete',address=ms)
            except Exception:pass

    def test_etr_consumes_map_notify_and_acks_it(self):
        # ETR side of Map-Notify (lisp.py lisp_process_map_notify / lisp_send_map_notify_ack): verify the notify
        # with the configured map-server's key, answer with a Map-Notify-Ack carrying the same nonce, key-id and
        # alg, the EID-records copied, record count 0 (as the control sends it), HMAC with the same key.
        self.need('wire.etr_notify4','wire.etr_register4','wire.register4_notify','etr_map_server.add','etr_map_server.delete',
                  'database_mapping.add','database_mapping.delete','site.add','site.delete','wire.auth_verify')
        self.mutable()
        ms='192.0.2.249'; pw='notify-secret'; prefixes=('198.23.0.0/16','198.23.128.0/17')
        try:
            self.adapter.call('site.add',iid='0',prefix='198.23.0.0/16',group='',accept_more_specifics=True,key_id=3,password=pw)
            if 'resolver.wait_site' in self.adapter.capabilities(): self.assertEqual(self.adapter.call('resolver.wait_site',iid='0',prefix='198.23.0.0/16',group='',active=True),'good')  # observe the site before relying on it
            for p,r in zip(prefixes,('192.0.2.66','192.0.2.67')):
                self.assertEqual(self.adapter.call('database_mapping.add',iid='0',prefix=p,group='',rloc_set=[r]),'good')
                if 'database_mapping.wait' in self.adapter.capabilities():
                    self.assertEqual(self.adapter.call('database_mapping.wait',iid='0',prefix=p,group='',present=True),'good')
            self.assertEqual(self.adapter.call('etr_map_server.add',address=ms,alg='sha256',key_id=3,password=pw,want_map_notify=True,site_id=7),'good')
            if 'etr_map_server.wait' in self.adapter.capabilities():
                self.assertEqual(self.adapter.call('etr_map_server.wait',address=ms,present=True),'good')
            reg=self.adapter.call('wire.etr_register4',map_server=ms,xtr_id='0a0b0c0d',nonce='aabbccdddfdfdf01')
            out=self.adapter.call('wire.register4_notify',hex=reg)
            self.assertEqual(out['result'],'good'); notify=bytes.fromhex(out['notify_hex'])
            r=self.adapter.call('wire.etr_notify4',hex=notify.hex(),source=ms)
            self.assertEqual(r['result'],'good'); ack=bytes.fromhex(r['ack_hex'])
            first=struct.unpack('!I',ack[:4])[0]
            self.assertEqual(first>>28,5); self.assertEqual(first&0xff,0)
            self.assertEqual(ack[4:14],notify[4:14])                       # nonce, key-id, alg
            alen=struct.unpack('!H',ack[14:16])[0]; self.assertEqual(alen,32)
            self.assertEqual(ack[16+alen:],notify[16+alen:])               # EID-records (and trailer) copied
            self.assertTrue(self.adapter.call('wire.auth_verify',hex=ack.hex(),password=pw))
            bad=bytearray(notify); bad[-1]^=1
            self.assertEqual(self.adapter.call('wire.etr_notify4',hex=bytes(bad).hex(),source=ms)['result'],'auth-failed')
            self.assertEqual(self.adapter.call('wire.etr_notify4',hex=notify.hex(),source='192.0.2.248')['result'],'unknown-map-server')
        finally:
            for p in prefixes:
                for op in ('database_mapping.delete','registration.delete'):
                    try:self.adapter.call(op,iid='0',prefix=p,group='')
                    except Exception:pass
            for op,kw in (('etr_map_server.delete',{'address':ms}),('site.delete',{'iid':'0','prefix':'198.23.0.0/16','group':''})):
                try:self.adapter.call(op,**kw)
                except Exception:pass

    def test_unmapped_request_gets_negative_native_forward_reply(self):
        self.need('wire.request4')
        nonce=0x0102030405060708
        b=bytes.fromhex(self.adapter.call('wire.request4',hex=req4(target=(203,0,113,199),nonce=nonce).hex()))
        self.assertEqual(struct.unpack('!I',b[:4])[0]>>28,2)
        self.assertEqual(struct.unpack('=Q',b[4:12])[0],nonce)
        self.assertEqual(b[16],0) # zero locators
        action_auth=struct.unpack('!H',b[18:20])[0]
        self.assertEqual((action_auth>>13)&7,1) # native-forward
        self.assertTrue(action_auth&0x1000)
        self.assertEqual(struct.unpack('!I',b[12:16])[0],15)

    def test_authoritative_unregistered_site_gets_one_minute_negative_reply(self):
        # a site that does NOT accept more-specifics answers with its own prefix; one that does answers for the
        # requested EID (lispers.net lisp_ms_process_map_request; found on hardware, acceptance L1.5 / L1.5b)
        self.need('site.add','site.delete','wire.request4');self.mutable()
        site='198.18.88.0/24'; nonce=0x1020304050607080
        try:
            self.assertEqual(self.adapter.call('site.add',iid='0',prefix=site,group='',accept_more_specifics=False),'good')
            if 'resolver.wait_site' in self.adapter.capabilities():
                self.assertEqual(self.adapter.call('resolver.wait_site',iid='0',prefix=site,group='',active=True),'good')
            b=bytes.fromhex(self.adapter.call('wire.request4',hex=req4(target=(198,18,88,9),nonce=nonce).hex()))
            self.assertEqual(struct.unpack('!I',b[:4])[0]>>28,2)
            self.assertEqual(struct.unpack('=Q',b[4:12])[0],nonce)
            self.assertEqual(struct.unpack('!I',b[12:16])[0],1)
            self.assertEqual(b[16],0)
            self.assertEqual(b[17],24)
            action_auth=struct.unpack('!H',b[18:20])[0]
            self.assertEqual((action_auth>>13)&7,1)
            self.assertTrue(action_auth&0x1000)
            self.assertEqual(struct.unpack('!H',b[22:24])[0],1)
            self.assertEqual(socket.inet_ntoa(b[24:28]),'198.18.88.0')
        finally:
            try:
                self.adapter.call('site.delete',iid='0',prefix=site,group='')
                if 'resolver.wait_site' in self.adapter.capabilities():
                    self.assertEqual(self.adapter.call('resolver.wait_site',iid='0',prefix=site,group='',active=False),'good')
            except Exception:pass

    def test_unmapped_ipv6_request_gets_negative_native_forward_reply(self):
        self.need('wire.request6')
        nonce=0x0a0b0c0d0e0f1011
        b=bytes.fromhex(self.adapter.call('wire.request6',hex=req6(target='2001:db8:dead::199',nonce=nonce).hex()))
        self.assertEqual(struct.unpack('!I',b[:4])[0]>>28,2)
        self.assertEqual(struct.unpack('=Q',b[4:12])[0],nonce)
        self.assertEqual(struct.unpack('!I',b[12:16])[0],15)
        self.assertEqual(b[16],0); self.assertEqual(b[17],128)
        self.assertEqual((struct.unpack('!H',b[18:20])[0]>>13)&7,1)
        self.assertEqual(struct.unpack('!H',b[22:24])[0],2)
        self.assertEqual(socket.inet_ntop(socket.AF_INET6,b[24:40]),'2001:db8:dead::199')

    def test_authoritative_unregistered_ipv6_site_gets_one_minute_negative_reply(self):
        # a site that does NOT accept more-specifics answers with its own prefix; one that does answers for the
        # requested EID (lispers.net lisp_ms_process_map_request; found on hardware, acceptance L1.5 / L1.5b)
        self.need('site.add','site.delete','wire.request6');self.mutable()
        site='2001:db8:88::/64'; nonce=0x1112131415161718
        try:
            self.assertEqual(self.adapter.call('site.add',iid='0',prefix=site,group='',accept_more_specifics=False),'good')
            if 'resolver.wait_site' in self.adapter.capabilities():
                self.assertEqual(self.adapter.call('resolver.wait_site',iid='0',prefix=site,group='',active=True),'good')
            b=bytes.fromhex(self.adapter.call('wire.request6',hex=req6(target='2001:db8:88::9',nonce=nonce).hex()))
            self.assertEqual(struct.unpack('!I',b[12:16])[0],1)
            self.assertEqual(b[16],0); self.assertEqual(b[17],64)
            self.assertEqual((struct.unpack('!H',b[18:20])[0]>>13)&7,1)
            self.assertEqual(struct.unpack('!H',b[22:24])[0],2)
            self.assertEqual(socket.inet_ntop(socket.AF_INET6,b[24:40]),'2001:db8:88::')
        finally:
            try:
                self.adapter.call('site.delete',iid='0',prefix=site,group='')
                if 'resolver.wait_site' in self.adapter.capabilities():
                    self.assertEqual(self.adapter.call('resolver.wait_site',iid='0',prefix=site,group='',active=False),'good')
            except Exception:pass

    def test_truncated_register_rejected(self):
        self.need('wire.register4')
        with self.assertRaises(RuntimeError):self.adapter.call('wire.register4',hex='30000001')

def reg6(prefix='2001:db8:70::',mask=64,rloc='2001:db8:ffff::70',ttl=0x8000003c,nonce=0x123456789abcdef0):
    first=(3<<28)|0x800|1
    p=struct.pack('!I',first)+struct.pack('=QBB',nonce,0,0)+struct.pack('!H',0)
    p+=struct.pack('!IBBHHH',ttl,1,mask,0,0,2)+socket.inet_pton(socket.AF_INET6,prefix)
    p+=struct.pack('!BBBBHH',1,100,0,0,1,2)+socket.inet_pton(socket.AF_INET6,rloc)
    return p

def reg6_multi(records, nonce=0x456789abcdef0123, flags=0x800):
    first=(3<<28)|flags|len(records)
    p=struct.pack('!I',first)+struct.pack('=QBB',nonce,0,0)+struct.pack('!H',0)
    for rec in records:
        prefix=rec['prefix']; mask=rec['mask']; ttl=rec.get('ttl',0x8000003c); rlocs=rec['rlocs']
        p+=struct.pack('!IBBHHH',ttl,len(rlocs),mask,0,0,2)+socket.inet_pton(socket.AF_INET6,prefix)
        for rloc in rlocs:
            p+=struct.pack('!BBBBHH',rloc.get('priority',1),rloc.get('weight',100),0,0,1,2)+socket.inet_pton(socket.AF_INET6,rloc['address'])
    return p

def req6(target='2001:db8:70::9',mask=128,nonce=0xfedcba9876543210):
    first=(1<<28)|1
    p=struct.pack('!I',first)+struct.pack('=Q',nonce)+struct.pack('!H',0)
    p+=struct.pack('!H',2)+socket.inet_pton(socket.AF_INET6,'2001:db8:ffff::1')
    p+=bytes((0,mask))+struct.pack('!H',2)+socket.inet_pton(socket.AF_INET6,target)
    return p

def _ipv6_test(self):
    self.need('wire.register6','wire.request6','registration.delete');self.mutable()
    p='2001:db8:70::/64';nonce=0xfedcba9876543210
    try:
        self.assertEqual(self.adapter.call('wire.register6',hex=reg6().hex()),'good')
        if 'resolver.wait' in self.adapter.capabilities():
            self.assertEqual(self.adapter.call('resolver.wait',iid='0',prefix=p,group='',present=True),'good')
        b=bytes.fromhex(self.adapter.call('wire.request6',hex=req6(nonce=nonce).hex()))
        self.assertEqual(struct.unpack('!I',b[:4])[0]>>28,2)
        self.assertEqual(struct.unpack('=Q',b[4:12])[0],nonce)
        self.assertEqual(struct.unpack('!H',b[22:24])[0],2)
        self.assertEqual(socket.inet_ntop(socket.AF_INET6,b[24:40]),'2001:db8:70::')
        self.assertEqual(struct.unpack('!H',b[46:48])[0],2)
        self.assertEqual(socket.inet_ntop(socket.AF_INET6,b[48:64]),'2001:db8:ffff::70')
    finally:
        try:self.adapter.call('registration.delete',iid='0',prefix=p,group='')
        except Exception:pass
WireContract.test_ipv6_register_request_reply=_ipv6_test

def _ipv6_multirecord_test(self):
    self.need('wire.register6','wire.request6','registration.delete');self.mutable()
    p1='2001:db8:74::/64';p2='2001:db8:75:8000::/65'
    raw=reg6_multi([
        {'prefix':'2001:db8:74::','mask':64,'rlocs':[{'address':'2001:db8:ffff::74'}]},
        {'prefix':'2001:db8:75:8000::','mask':65,'rlocs':[{'address':'2001:db8:ffff::75','priority':2,'weight':80}]},
    ])
    try:
        self.assertEqual(self.adapter.call('wire.register6',hex=raw.hex()),'good')
        if 'resolver.wait' in self.adapter.capabilities():
            self.assertEqual(self.adapter.call('resolver.wait',iid='0',prefix=p1,group='',present=True),'good')
            self.assertEqual(self.adapter.call('resolver.wait',iid='0',prefix=p2,group='',present=True),'good')
        b1=bytes.fromhex(self.adapter.call('wire.request6',hex=req6(target='2001:db8:74::9').hex()))
        b2=bytes.fromhex(self.adapter.call('wire.request6',hex=req6(target='2001:db8:75:8000::9').hex()))
        self.assertEqual(socket.inet_ntop(socket.AF_INET6,b1[48:64]),'2001:db8:ffff::74')
        self.assertEqual(socket.inet_ntop(socket.AF_INET6,b2[48:64]),'2001:db8:ffff::75')
    finally:
        for pfx in (p1,p2):
            try:self.adapter.call('registration.delete',iid='0',prefix=pfx,group='')
            except Exception:pass
WireContract.test_ipv6_multirecord_register=_ipv6_multirecord_test

def auth_reg4(password='secret',alg=1,key_id=7):
    base=bytearray(reg4())
    alen=20 if alg==1 else 32
    # Insert authentication bytes after the 16-byte header and update key/alg/len.
    base[12]=key_id;base[13]=alg;base[14:16]=struct.pack('!H',alen)
    base[16:16]=bytes(alen)
    digest=hmac.new(password.encode(),bytes(base),hashlib.sha1 if alg==1 else hashlib.sha256).digest()
    base[16:16+alen]=digest
    return bytes(base)

def _auth_test(self):
    self.need('wire.auth_verify')
    p=auth_reg4('correct horse',1,9)
    self.assertTrue(self.adapter.call('wire.auth_verify',hex=p.hex(),password='correct horse'))
    self.assertFalse(self.adapter.call('wire.auth_verify',hex=p.hex(),password='wrong'))
    q=bytearray(p);q[-1]^=1
    self.assertFalse(self.adapter.call('wire.auth_verify',hex=bytes(q).hex(),password='correct horse'))
    p2=auth_reg4('sha256 secret',2,11)
    self.assertTrue(self.adapter.call('wire.auth_verify',hex=p2.hex(),password='sha256 secret'))
WireContract.test_map_register_hmac_sha1_sha256=_auth_test

def auth_notify_reg4(password='notify secret',key_id=12):
    # Build an authenticated register with Want-Map-Notify set before hashing.
    base=bytearray(reg4())
    first=struct.unpack('!I',base[:4])[0]|0x100
    base[:4]=struct.pack('!I',first);base[12]=key_id;base[13]=1;base[14:16]=struct.pack('!H',20);base[16:16]=bytes(20)
    base[16:36]=hmac.new(password.encode(),bytes(base),hashlib.sha1).digest()
    return bytes(base)

def _notify_test(self):
    self.need('site.add','site.delete','wire.register4_notify','registration.delete');self.mutable()
    pfx='198.18.70.0/24';nonce=0x1122334455667788
    try:
        self.adapter.call('site.add',iid='0',prefix=pfx,group='',accept_more_specifics=False,key_id=12,password='notify secret')
        if 'resolver.wait_site' in self.adapter.capabilities(): self.assertEqual(self.adapter.call('resolver.wait_site',iid='0',prefix=pfx,group='',active=True),'good')  # observe the site before relying on it
        out=self.adapter.call('wire.register4_notify',hex=auth_notify_reg4().hex())
        self.assertEqual(out['result'],'good');b=bytes.fromhex(out['notify_hex'])
        self.assertEqual(struct.unpack('!I',b[:4])[0]>>28,4);self.assertEqual(struct.unpack('=Q',b[4:12])[0],nonce)
        self.assertTrue(self.adapter.call('wire.auth_verify',hex=b.hex(),password='notify secret'))
        self.assertEqual(socket.inet_ntoa(b[48:52]),'198.18.70.0')
    finally:
        try:self.adapter.call('registration.delete',iid='0',prefix=pfx,group='')
        finally:
            self.adapter.call('site.delete',iid='0',prefix=pfx,group='')
            if 'resolver.wait_site' in self.adapter.capabilities():
                self.assertEqual(self.adapter.call('resolver.wait_site',iid='0',prefix=pfx,group='',active=False),'good')
WireContract.test_want_map_notify_ack_preserves_nonce_and_authenticates=_notify_test


def _wire_ttl0_sender_authorization(self):
    self.need('wire.register4','resolution.get','registration.delete'); self.mutable()
    p='198.18.73.0/24'
    live=reg4(prefix=(198,18,73,0),rloc=(192,0,2,73),ttl=0x8000003c)
    zero=reg4(prefix=(198,18,73,0),rloc=(192,0,2,73),ttl=0)
    try:
        self.assertEqual(self.adapter.call('wire.register4',hex=live.hex(),source='192.0.2.73'),'good')
        if 'resolver.wait' in self.adapter.capabilities(): self.assertEqual(self.adapter.call('resolver.wait',iid='0',prefix=p,group='',present=True),'good')
        self.assertEqual(self.adapter.call('wire.register4',hex=zero.hex(),source='192.0.2.99'),'ignored')
        got=canonical(self.adapter.call('resolution.get',iid='0',prefix='198.18.73.1/32',group=''))
        self.assertEqual([r['address'] for r in got['rlocs']],['192.0.2.73'])
        self.assertEqual(self.adapter.call('wire.register4',hex=zero.hex()),'ignored')
        got=canonical(self.adapter.call('resolution.get',iid='0',prefix='198.18.73.1/32',group=''))
        self.assertEqual([r['address'] for r in got['rlocs']],['192.0.2.73'])
        self.assertEqual(self.adapter.call('wire.register4',hex=zero.hex(),source='192.0.2.73'),'good')
        if 'resolver.wait' in self.adapter.capabilities():
            self.assertEqual(self.adapter.call('resolver.wait',iid='0',prefix=p,group='',present=False),'good')
        self.assertIn(canonical(self.adapter.call('resolution.get',iid='0',prefix='198.18.73.1/32',group='')),(None,[],{}))
    finally:
        try:self.adapter.call('registration.delete',iid='0',prefix=p,group='')
        except Exception:pass
WireContract.test_wire_ttl0_requires_registered_sender=_wire_ttl0_sender_authorization

# Site fixtures (v0.42). These tests register without configuring a site; they predate site authorization (v0.6)
# and passed only because an empty site table skipped authorization — a bug fixed in v0.42. Each now runs with a
# site covering exactly what it registers; the site is removed afterwards and its removal observed.
def _site_setup(tc,sites):
    tc._fixture_sites=[]
    if not sites or 'site.add' not in tc.adapter.capabilities() or os.getenv('LISP_ALLOW_MUTATION')!='1': return
    for iid,prefix,ams in sites:
        tc.adapter.call('site.add',iid=iid,prefix=prefix,group='',accept_more_specifics=ams)
        tc._fixture_sites.append((iid,prefix))
        if 'resolver.wait_site' in tc.adapter.capabilities():
            assert tc.adapter.call('resolver.wait_site',iid=iid,prefix=prefix,group='',active=True)=='good'
def _site_teardown(tc):
    for iid,prefix in getattr(tc,'_fixture_sites',[]):
        try:
            tc.adapter.call('site.delete',iid=iid,prefix=prefix,group='')
            if 'resolver.wait_site' in tc.adapter.capabilities():
                tc.adapter.call('resolver.wait_site',iid=iid,prefix=prefix,group='',active=False)
        except Exception: pass

WireContract.FIXTURE_SITES={'test_ipv6_register_request_reply': [('0', '2001:db8:70::/64', False)], 'test_ipv6_multirecord_register': [('0', '2001:db8:74::/64', False), ('0', '2001:db8:75:8000::/65', False)], 'test_wire_ttl0_requires_registered_sender': [('0', '198.18.73.0/24', False)], 'test_map_register_to_map_request_to_map_reply_ipv4': [('0', '198.18.70.0/24', False)], 'test_two_record_map_register_applies_both_records': [('0', '198.18.74.0/24', False), ('0', '198.18.75.128/25', False)], 'test_multirecord_malformed_later_record_is_atomic': [('0', '198.18.76.0/24', False), ('0', '198.18.77.0/24', False)], 'test_multirecord_refresh_rejects_changed_later_record_atomically': [('0', '198.18.80.0/24', False), ('0', '198.18.81.0/24', False)], 'test_multirecord_withdrawal_applies_each_record': [('0', '198.18.82.0/24', False), ('0', '198.18.83.0/24', False)], 'test_multirecord_withdrawal_source_failure_is_atomic': [('0', '198.18.86.0/24', False), ('0', '198.18.87.0/24', False)], 'test_multiple_locator_records_survive_wire_path': [('0', '198.18.71.0/24', False)], 'test_etr_map_request_answers_from_held_database_mapping_ipv4': [('0', '198.18.200.0/24', False)], 'test_etr_map_request_answers_from_held_database_mapping_ipv6': [('0', '2001:db8:e7:c800::/56', False)]}
WireContract.setUp=lambda self:_site_setup(self,WireContract.FIXTURE_SITES.get(self._testMethodName))
WireContract.tearDown=lambda self:_site_teardown(self)
