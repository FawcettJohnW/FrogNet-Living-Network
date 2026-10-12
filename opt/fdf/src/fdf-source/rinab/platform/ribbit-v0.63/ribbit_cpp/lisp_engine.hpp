/***************************************************************
 *  Copyright (C) 2016-2026 Fawcett Innovations LLC            *
 *                                                             *
 *  SPDX-License-Identifier: GPL-2.0-only                      *
 *                                                             *
 *  This program is free software; you can redistribute it     *
 *  and/or modify it under the terms of the GNU General Public *
 *  License as published by the Free Software Foundation;      *
 *  version 2 of the License, and no other version.            *
 *                                                             *
 *  This program is distributed in the hope that it will be    *
 *  useful, but WITHOUT ANY WARRANTY; without even the implied *
 *  warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR    *
 *  PURPOSE.  See the GNU General Public License for details.  *
 *                                                             *
 *  See COPYRIGHT and LICENSE at the root of this tree.        *
 **************************************************************/
// lisp_engine.hpp -- the Ribbit-LISP engine: wire codecs and class Engine, moved verbatim out of ribbit_lisp.cpp
// so that more than one program can hold an Engine (ribbit-lisp stdin dispatcher, the C++ LispHandler).
// Include it in ONE translation unit per program: the wire helpers have internal linkage.
#pragma once
#include <arpa/inet.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <unistd.h>
#include <algorithm>
#include <cstdint>
#include <iostream>
#include <map>
#include <array>
#include <functional>
#include <set>
#include <thread>
#include <atomic>
#include <random>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>
#include <cstring>
#include <memory>
#include <chrono>
#include <openssl/hmac.h>
#include <openssl/crypto.h>
#include <openssl/evp.h>
#include <linux/futex.h>
#include <sys/syscall.h>
#include <unistd.h>
#include <climits>
#include "frogram.hpp"
#include "ebr.hpp"
using frogram::Json;
struct Prefix {
    int family=0,bits=0;
    unsigned char a[16]{
    };
    std::string text;
};
static Prefix prefix_parse(const std::string&s){
    Prefix p;
    auto slash=s.find('/');
    std::string ip=s.substr(0,slash);
    p.family=ip.find(':')==std::string::npos?AF_INET:AF_INET6;
    int max=p.family==AF_INET?32:128;
    p.bits=slash==std::string::npos?max:std::stoi(s.substr(slash+1));
    if(p.bits<0||p.bits>max||inet_pton(p.family,ip.c_str(),p.a)!=1) throw std::runtime_error("bad prefix syntax");
    p.text=s;
    return p;
}
static bool contains(const Prefix&p,const Prefix&q){
    if(p.family!=q.family||p.bits>q.bits)return false;
    int n=p.bits/8,r=p.bits%8;
    if(n&&memcmp(p.a,q.a,n))return false;
    return !r||((p.a[n]>>(8-r))==(q.a[n]>>(8-r)));
}
static std::string q(const std::string&s){
    return Json::quote(s);
}
// The prefix a key names, with its host bits cleared, in the one text form: "addr/len". A held tuple is found by it.
static std::string masked_key(const Prefix&p,int bits){
    unsigned char a[16]; std::memcpy(a,p.a,16); const int len=p.family==AF_INET?4:16;
    for(int i=0;i<len;++i){ int keep=bits-8*i; if(keep<=0)a[i]=0; else if(keep<8)a[i]&=(unsigned char)(0xff<<(8-keep)); }
    char b[INET6_ADDRSTRLEN]; if(!inet_ntop(p.family,a,b,sizeof b))throw std::runtime_error("bad prefix");
    return std::string(b)+"/"+std::to_string(bits);
}
static std::string prefix_key(const std::string&prefix){ Prefix x=prefix_parse(prefix); return masked_key(x,x.bits); }
// [HELD_TUPLES_V1] John 2026-09-28, "you know what I think about copying": a participant holds the tuples it does not
// own -- it does not copy them. A held table used to be a snapshot: every cell that arrived copied the WHOLE table
// (every Mapping, its strings and locators) to publish one change, and every lookup copied every held registration
// into a local vector. Now each tuple is held once, behind its own atomic pointer, in an insert-only table whose
// entries are never unlinked: a new cell replaces its instance's pointer (the old tuple goes to the EBR), and a
// reader goes straight to the prefix it wants and reads the tuples where they are. One writer per table (its watch
// thread) and any number of lock-free readers; nothing is locked, copied or scanned.
template<class T> class HeldSlot {
public:
    // A held tuple and the id of the cell it came from. Two paths put tuples here -- the watch that follows the memory,
    // and a participant holding the cells its own region operation wrote ([OWN_WRITES_HELD_V1]) -- so a put replaces
    // a tuple only with a newer one: the id decides, and the same cell arriving twice is held once.
    struct Version { uint64_t id; T v; };
    struct Member { std::string instance; std::atomic<const Version*> val{nullptr}; Member* next=nullptr; };
    struct Group { std::string key; std::atomic<Member*> members{nullptr}; Group* next=nullptr; };
    HeldSlot(){ for(auto&b:buckets_)b.store(nullptr,std::memory_order_relaxed); }
    ~HeldSlot(){
        for(auto&b:buckets_)for(Group*g=b.load(std::memory_order_acquire);g;){
            for(Member*m=g->members.load(std::memory_order_acquire);m;){ Member*n=m->next; delete m->val.load(); delete m; m=n; }
            Group*n=g->next; delete g; g=n; }
    }
    HeldSlot(const HeldSlot&)=delete; HeldSlot&operator=(const HeldSlot&)=delete;
    // Hold v (from cell `id`) under (key, instance). Returns what the caller must hand to the EBR: the version replaced,
    // or v itself when an equal or newer version is already held.
    const Version* put(const std::string&key,const std::string&instance,const Version*v){
        Member*m=member(group(key),instance);
        const Version*cur=m->val.load(std::memory_order_acquire);
        for(;;){
            if(cur&&cur->id>=v->id)return v;
            if(m->val.compare_exchange_weak(cur,v,std::memory_order_acq_rel,std::memory_order_acquire))return cur;
        }
    }
    template<class F> void each_in(const std::string&key,F f)const{          // inside an EBR guard; instance order
        const Group*g=find(key); if(!g)return;
        std::vector<const Member*> all;
        for(const Member*m=g->members.load(std::memory_order_acquire);m;m=m->next)all.push_back(m);
        std::sort(all.begin(),all.end(),[](const Member*a,const Member*b){ return a->instance<b->instance; });
        for(auto*m:all)if(const Version*v=m->val.load(std::memory_order_acquire))f(m->instance,v->v);
    }
    const T* get(const std::string&key,const std::string&instance)const{
        const Group*g=find(key); if(!g)return nullptr;
        for(const Member*m=g->members.load(std::memory_order_acquire);m;m=m->next)
            if(m->instance==instance){ const Version*v=m->val.load(std::memory_order_acquire); return v?&v->v:nullptr; }
        return nullptr;
    }
private:
    static constexpr size_t B=4096;
    std::array<std::atomic<Group*>,B> buckets_;
    static size_t h(const std::string&k){ return std::hash<std::string>()(k)&(B-1); }
    Group* find(const std::string&key)const{
        for(Group*g=buckets_[h(key)].load(std::memory_order_acquire);g;g=g->next)if(g->key==key)return g;
        return nullptr;
    }
    Group* group(const std::string&key){                 // insert-only, compare-and-swap: any number of writers
        auto&b=buckets_[h(key)];
        for(;;){ Group*head=b.load(std::memory_order_acquire);
            for(Group*g=head;g;g=g->next)if(g->key==key)return g;
            Group*n=new Group; n->key=key; n->next=head;
            if(b.compare_exchange_strong(head,n,std::memory_order_acq_rel))return n;
            delete n; }
    }
    Member* member(Group*g,const std::string&instance){
        for(;;){ Member*head=g->members.load(std::memory_order_acquire);
            for(Member*m=head;m;m=m->next)if(m->instance==instance)return m;
            Member*n=new Member; n->instance=instance; n->next=head;
            if(g->members.compare_exchange_strong(head,n,std::memory_order_acq_rel))return n;
            delete n; }
    }
};
struct Rloc{
    std::string address;
    int priority=1,weight=100;
    // [LCAF_LOCATOR_V1] a locator that arrived as an LCAF RLOC record (geo-coordinates, explicit-locator-path,
    // replication-list-entry, JSON ... inside an AFI-list): the complete record, hex, returned byte for byte in
    // Map-Replies; `address` is the AFI-list's own address. Found by acceptance L2.14 (Ribbit refused them).
    std::string lcaf;
};
struct Mapping{
    std::string iid="0",prefix,group,xtr_id,site_id;
    std::string live_key;       // native registration: resolvable only while etr-live/<live_key> is fresh (v0.44)
    std::vector<Rloc> rlocs;
    uint32_t ttl=0;
    int64_t last_registered=0,expires_at=0;
    bool registered=true,merge=false,use_register_ttl=false;
};
static int64_t now_ms(){
    return std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::system_clock::now().time_since_epoch()).count();
}
static int64_t registration_lifetime(uint32_t ttl,bool use_ttl){
    if(!use_ttl)return 180;
    if(ttl&0x80000000u)return (int64_t)(ttl&0x7fffffffu);
    return (int64_t)ttl*60;
}
static uint16_t be16(const unsigned char*p){
    return (uint16_t(p[0])<<8)|p[1];
}
static uint32_t be32(const unsigned char*p){
    return (uint32_t(p[0])<<24)|(uint32_t(p[1])<<16)|(uint32_t(p[2])<<8)|p[3];
}
static void put16(std::vector<unsigned char>&o,uint16_t v){
    o.push_back(v>>8);
    o.push_back(v);
}
static void put32(std::vector<unsigned char>&o,uint32_t v){
    o.push_back(v>>24);
    o.push_back(v>>16);
    o.push_back(v>>8);
    o.push_back(v);
}
static void put64_native(std::vector<unsigned char>&o,uint64_t v){
    unsigned char*p=(unsigned char*)&v;
    o.insert(o.end(),p,p+8);
}
static std::vector<unsigned char> unhex(const std::string&s){
    if(s.size()%2)throw std::runtime_error("odd hex");
    std::vector<unsigned char>o;
    for(size_t i=0;i<s.size();i+=2)o.push_back((unsigned char)std::stoul(s.substr(i,2),nullptr,16));
    return o;
}
static std::string hex(const std::vector<unsigned char>&v){
    static const char*d="0123456789abcdef";
    std::string s;
    for(auto c:v){
        s+=d[c>>4];
        s+=d[c&15];
    }
    return s;
}
static bool verify_register_hmac(const std::vector<unsigned char>&p,const std::string&password){
    if(p.size()<16)return false;
    int alg=p[13];
    size_t alen=be16(p.data()+14);
    if(alg==0)return alen==0;
    if((alg==1&&alen!=20)||(alg==2&&alen!=32)||p.size()<16+alen)return false;
    std::vector<unsigned char>z=p;
    std::fill(z.begin()+16,z.begin()+16+alen,0);
    unsigned char md[EVP_MAX_MD_SIZE];
    unsigned int n=0;
    const EVP_MD*evp=alg==1?EVP_sha1():EVP_sha256();
    if(!HMAC(evp,password.data(),(int)password.size(),z.data(),z.size(),md,&n))return false;
    return n==alen&&CRYPTO_memcmp(md,p.data()+16,alen)==0;
}
static std::vector<unsigned char> make_notify_from_register(const std::vector<unsigned char>&reg,const std::string&password){
    if(reg.size()<16)throw std::runtime_error("truncated map-register");
    uint32_t rf=be32(reg.data());
    size_t alen=be16(reg.data()+14);
    if(reg.size()<16+alen)throw std::runtime_error("truncated auth");
    int alg=reg[13];
    std::vector<unsigned char>o;
    put32(o,(4u<<28)|(rf&0xff));
    o.insert(o.end(),reg.begin()+4,reg.begin()+14);
    put16(o,(uint16_t)alen);
    size_t authoff=o.size();
    o.insert(o.end(),alen,0);
    o.insert(o.end(),reg.begin()+16+alen,reg.end());
    if(alg!=0){
        unsigned char md[EVP_MAX_MD_SIZE];
        unsigned int n=0;
        const EVP_MD*evp=alg==1?EVP_sha1():EVP_sha256();
        if(!HMAC(evp,password.data(),(int)password.size(),o.data(),o.size(),md,&n)||n!=alen)throw std::runtime_error("notify hmac failure");
        std::copy(md,md+alen,o.begin()+authoff);
    }
    return o;
}
static std::string addr4(const unsigned char*p){
    char b[INET_ADDRSTRLEN];
    if(!inet_ntop(AF_INET,p,b,sizeof(b)))throw std::runtime_error("bad ipv4");
    return b;
}
static void append4(std::vector<unsigned char>&o,const std::string&s){
    unsigned char a[4];
    if(inet_pton(AF_INET,s.c_str(),a)!=1)throw std::runtime_error("ipv4 required");
    o.insert(o.end(),a,a+4);
}
struct WireReg{
    std::string iid="0",group;
    uint64_t nonce=0;
    uint32_t ttl=0;
    bool use_ttl=false,refresh=false,merge=false,notify=false;
    std::string prefix,xtr_id;
    int mask=0;
    std::vector<Rloc> rlocs;
};
// [LCAF_INSTANCE_ID_V1] Instance-ID on the wire (RFC 8060, LCAF type 2): an EID whose AFI is 16387 carries Rsvd1,
// Flags, Type 2, IID mask-len, Length, a 32-bit Instance ID, then the plain AFI and address. The acceptance test
// (L2.12) found every wire path fixed to instance 0. strip_iid_* rewrite each EID to its plain form and return the
// instance (one per message); the decoders then read plain EIDs, authentication still runs over the ORIGINAL bytes,
// and add_iid_to_reply puts the LCAF back into a Map-Reply's record.
static const uint16_t LCAF_AFI=16387;
static size_t addr_len(uint16_t afi){ return afi==1?4:afi==2?16:0; }
// At o: an EID (AFI + address). Returns the plain replacement bytes and the bytes consumed; iid set if LCAF type 2.
// [MULTICAST_SG_V1] (S,G) EIDs: LCAF type 9 (RFC 8060 Multicast Info) -- Instance ID, 16 reserved bits, source and group
// mask lengths, source AFI + address, group AFI + address. The source becomes the plain EID; the group is returned
// as "address/mask". (lispers.net writes this LCAF with Python's native struct alignment -- two pad bytes after the
// length -- so it and an RFC 8060 speaker do not read each other's (S,G) EIDs; FINDINGS-FOR-DINO.md.)
static std::string format_addr(uint16_t afi,const unsigned char*a);
static bool&sg_padded(){ static thread_local bool v=false; return v; }   // the last (S,G) read on this thread was padded
static std::vector<unsigned char> plain_eid(const std::vector<unsigned char>&p,size_t o,size_t&used,std::string&iid,std::string*group=nullptr){
    if(p.size()<o+2)throw std::runtime_error("truncated eid");
    uint16_t afi=be16(p.data()+o);
    if(afi!=LCAF_AFI){ size_t al=addr_len(afi); if(p.size()<o+2+al)throw std::runtime_error("truncated eid"); used=2+al; return std::vector<unsigned char>(p.begin()+o,p.begin()+o+used); }
    if(p.size()<o+8)throw std::runtime_error("truncated lcaf");
    if(p[o+4]==9){
        // RFC 8060 layout first; else lispers.net's own (two pad bytes after the length), which its xTRs send --
        // accepted for interoperability, and answered in the same layout (sg_padded)
        size_t len=be16(p.data()+o+6);
        for(int pad=0;pad<=2;pad+=2){
            if(len<12||p.size()<o+8+pad+len)continue;
            const unsigned char*b=p.data()+o+8+pad;
            uint16_t safi=be16(b+8); size_t sal=addr_len(safi); if(!sal||len<10+sal+2)continue;
            uint16_t gafi=be16(b+10+sal); size_t gal=addr_len(gafi); if(!gal||len!=12+sal+gal)continue;
            if(!group)throw std::runtime_error("(S,G) EID not accepted here");
            iid=std::to_string(be32(b)); int gml=b[7];
            *group=format_addr(gafi,b+12+sal)+"/"+std::to_string(gml);
            sg_padded()=pad!=0;
            used=8+pad+len; return std::vector<unsigned char>(b+8,b+10+sal);
        }
        throw std::runtime_error("lcaf multicast-info: neither RFC 8060 nor lispers.net layout");
    }
    if(p[o+4]!=2)throw std::runtime_error("unsupported LCAF type "+std::to_string(p[o+4]));
    size_t len=be16(p.data()+o+6);
    if(len<6||p.size()<o+8+len)throw std::runtime_error("truncated lcaf instance-id");
    iid=std::to_string(be32(p.data()+o+8));
    uint16_t inner=be16(p.data()+o+12); size_t al=addr_len(inner);
    if(!al||len!=6+al)throw std::runtime_error("lcaf instance-id: unsupported inner AFI "+std::to_string(inner));
    used=8+len; return std::vector<unsigned char>(p.begin()+o+12,p.begin()+o+14+al);
}
static void one_iid(std::string&have,const std::string&got){
    if(have.empty())have=got; else if(have!=got)throw std::runtime_error("records of different instance-ids in one message");
}
static std::vector<unsigned char> strip_iid_register(const std::vector<unsigned char>&p,std::string&iid,std::string*group=nullptr){
    iid.clear(); if(p.size()<16)return p;
    std::string g0; bool first=true;
    int records=p[3]; size_t n=16+be16(p.data()+14); if(p.size()<n)return p;
    std::vector<unsigned char> o(p.begin(),p.begin()+n);
    for(int r=0;r<records;r++){
        if(p.size()<n+10)return p;                              // malformed: the decoder reports it
        int rc=p[n+4]; o.insert(o.end(),p.begin()+n,p.begin()+n+10); n+=10;
        size_t used; std::string i,g; auto e=plain_eid(p,n,used,i,&g); if(!i.empty())one_iid(iid,i); else one_iid(iid,"0");
        if(first){ g0=g; first=false; } else if(g!=g0)throw std::runtime_error("records of different groups in one message");
        o.insert(o.end(),e.begin(),e.end()); n+=used;
        for(int k=0;k<rc;k++){
            if(p.size()<n+8)return p;
            size_t al=addr_len(be16(p.data()+n+6)); if(p.size()<n+8+al)return p;
            o.insert(o.end(),p.begin()+n,p.begin()+n+8+al); n+=8+al;
        }
    }
    o.insert(o.end(),p.begin()+n,p.end());                     // the xTR-ID / site-ID trailer, if any
    if(iid.empty())iid="0";
    if(group)*group=g0; else if(!g0.empty())throw std::runtime_error("(S,G) EID not accepted here");
    return o;
}
static std::vector<unsigned char> strip_iid_request(const std::vector<unsigned char>&p,std::string&iid,std::string*group=nullptr){
    iid="0"; if(p.size()<14)return p;
    size_t n=12; size_t sa=addr_len(be16(p.data()+n)); n+=2+sa;
    int irc=(p[1]&0x1f)+1;
    for(int i=0;i<irc;i++){ if(p.size()<n+2)return p; n+=2+addr_len(be16(p.data()+n)); }
    if(p.size()<n+4)return p;
    std::vector<unsigned char> o(p.begin(),p.begin()+n+2);    // record: reserved, mask length
    size_t used; std::string i,g; auto e=plain_eid(p,n+2,used,i,&g); if(!i.empty())iid=i;
    if(group)*group=g; else if(!g.empty())throw std::runtime_error("(S,G) EID not accepted here");
    o.insert(o.end(),e.begin(),e.end()); o.insert(o.end(),p.begin()+n+2+used,p.end());
    return o;
}
static std::vector<unsigned char> add_sg_to_reply(const std::vector<unsigned char>&r,const std::string&iid,const std::string&group){
    if(group.empty()||r.size()<12+10+2)return r;
    size_t n=12; uint16_t afi=be16(r.data()+n+10); size_t al=addr_len(afi);
    if(!al||r.size()<n+12+al)return r;
    Prefix gp=prefix_parse(group); size_t gal=gp.family==AF_INET?4:16;
    std::vector<unsigned char> o(r.begin(),r.begin()+n+10);
    put16(o,LCAF_AFI); o.push_back(0); o.push_back(0); o.push_back(9); o.push_back(0); put16(o,uint16_t(8+2+al+2+gal));
    if(sg_padded()){ o.push_back(0); o.push_back(0); }       // answer a lispers.net requester in its own layout
    put32(o,(uint32_t)std::stoul(iid)); put16(o,0); o.push_back(r[n+5]); o.push_back((unsigned char)gp.bits);
    o.insert(o.end(),r.begin()+n+10,r.begin()+n+12+al);
    put16(o,gp.family==AF_INET?1:2); o.insert(o.end(),gp.a,gp.a+gal);
    o.insert(o.end(),r.begin()+n+12+al,r.end());
    return o;
}
static std::vector<unsigned char> add_iid_to_reply(const std::vector<unsigned char>&r,const std::string&iid){
    if(iid=="0"||r.size()<12+10+2)return r;
    size_t n=12; uint16_t afi=be16(r.data()+n+10); size_t al=addr_len(afi);
    if(!al||r.size()<n+12+al)return r;
    std::vector<unsigned char> o(r.begin(),r.begin()+n+10);
    put16(o,LCAF_AFI); o.push_back(0); o.push_back(0); o.push_back(2); o.push_back(0); put16(o,uint16_t(6+al));
    put32(o,(uint32_t)std::stoul(iid)); o.insert(o.end(),r.begin()+n+10,r.begin()+n+12+al);
    o.insert(o.end(),r.begin()+n+12+al,r.end());
    return o;
}
static size_t lcaf_locator(const std::vector<unsigned char>&p,size_t n,Rloc&r);
static std::vector<WireReg> decode_register4(const std::vector<unsigned char>&p){
    if(p.size()<16)throw std::runtime_error("truncated map-register");
    uint32_t f=be32(p.data());
    if((f>>28)!=3)throw std::runtime_error("not map-register");
    int records=f&0xff;
    if(records<1)throw std::runtime_error("map-register requires records");
    uint64_t nonce=0; std::memcpy(&nonce,p.data()+4,8);
    bool use_ttl=f&0x800,refresh=f&0x1000,merge=f&0x400,notify=f&0x100;
    uint16_t auth=be16(p.data()+14);
    size_t n=16+auth;
    if(p.size()<n)throw std::runtime_error("truncated auth");
    std::vector<WireReg> out; out.reserve(records);
    for(int record=0;record<records;record++){
        if(p.size()<n+16)throw std::runtime_error("truncated eid-record");
        WireReg w; w.nonce=nonce; w.use_ttl=use_ttl; w.refresh=refresh; w.merge=merge; w.notify=notify;
        w.ttl=be32(p.data()+n);
        int rc=p[n+4];
        w.mask=p[n+5];
        uint16_t afi=be16(p.data()+n+10);
        if(afi!=1)throw std::runtime_error("wire slice requires IPv4 EID");
        w.prefix=addr4(p.data()+n+12)+"/"+std::to_string(w.mask);
        n+=16;
        for(int i=0;i<rc;i++){
            if(p.size()<n+12)throw std::runtime_error("truncated rloc-record");
            Rloc r; r.priority=p[n]; r.weight=p[n+1];
            uint16_t ra=be16(p.data()+n+6);
            if(ra==16387){ n+=lcaf_locator(p,n,r); w.rlocs.push_back(r); continue; }     // [LCAF_LOCATOR_V1]
            if(ra!=1)throw std::runtime_error("wire slice requires IPv4 RLOC");
            r.address=addr4(p.data()+n+8); w.rlocs.push_back(r); n+=12;
        }
        out.push_back(w);
    }
    // I bit (0x02000000): 128-bit xTR-ID and 64-bit site-ID follow the last record (control encode_xtr_id).
    if(f&0x02000000){
        if(p.size()!=n+24)throw std::runtime_error("map-register xtr-id trailer malformed");
        std::string xtr=hex(std::vector<unsigned char>(p.begin()+n,p.begin()+n+16));
        for(auto&w:out)w.xtr_id=xtr;
        n+=24;
    }
    if(n!=p.size())throw std::runtime_error("trailing map-register data");
    return out;
}
struct WireReq{
    std::string iid="0",group;
    uint64_t nonce=0;
    std::string target;
};
static WireReq decode_request4(const std::vector<unsigned char>&p){
    if(p.size()<26)throw std::runtime_error("truncated map-request");
    uint32_t f=be32(p.data());
    if((f>>28)!=1)throw std::runtime_error("not map-request");
    if((f&0xff)!=1)throw std::runtime_error("wire slice requires one record");
    int irc=((f>>8)&0x1f)+1;
    WireReq w;
    std::memcpy(&w.nonce,p.data()+4,8);
    size_t n=12;
    uint16_t safi=be16(p.data()+n);
    n+=2;
    if(safi==1)n+=4;
    else if(safi==2)n+=16;
    else if(safi!=0)throw std::runtime_error("unsupported source AFI");
    for(int i=0;i<irc;i++){
        if(p.size()<n+2)throw std::runtime_error("truncated itr-rloc");
        uint16_t a=be16(p.data()+n);
        n+=2;
        if(a==1)n+=4;
        else if(a==2)n+=16;
        else throw std::runtime_error("unsupported itr AFI");
    }
    if(p.size()<n+8)throw std::runtime_error("truncated request record");
    int mask=p[n+1];
    uint16_t afi=be16(p.data()+n+2);
    if(afi!=1)throw std::runtime_error("wire slice requires IPv4 target");
    w.target=addr4(p.data()+n+4)+"/"+std::to_string(mask);
    return w;
}
static std::vector<unsigned char> encode_negative_reply(uint64_t nonce,const std::string&prefix,uint32_t ttl,int action=1){
    std::vector<unsigned char>o;
    put32(o,(2u<<28)|1u);
    put64_native(o,nonce);
    put32(o,ttl);
    o.push_back(0);
    Prefix ep=prefix_parse(prefix);
    o.push_back((unsigned char)ep.bits);
    put16(o,((uint16_t)action<<13)|0x1000);
    put16(o,0);
    put16(o,ep.family==AF_INET?1:2);
    o.insert(o.end(),ep.a,ep.a+(ep.family==AF_INET?4:16));
    return o;
}
static std::string addr6(const unsigned char*p){
    char b[INET6_ADDRSTRLEN];
    if(!inet_ntop(AF_INET6,p,b,sizeof(b)))throw std::runtime_error("bad ipv6");
    return b;
}
// An LCAF RLOC record at p[n]: priority, weight, m-priority, m-weight, flags, AFI 16387, then Rsvd1 Flags Type Rsvd2
// Length and Length bytes. The AFI-list form (type 1) names the locator's address first.
static size_t lcaf_locator(const std::vector<unsigned char>&p,size_t n,Rloc&r){
    if(p.size()<n+14)throw std::runtime_error("truncated lcaf rloc-record");
    size_t len=be16(p.data()+n+12), total=14+len;
    if(p.size()<n+total)throw std::runtime_error("truncated lcaf rloc-record");
    if(p[n+10]!=1)throw std::runtime_error("unsupported LCAF locator type "+std::to_string(p[n+10])+" (an AFI-list is required)");
    if(len<6)throw std::runtime_error("empty LCAF AFI-list");
    uint16_t a=be16(p.data()+n+14);
    if(a==1)r.address=addr4(p.data()+n+16); else if(a==2&&len>=18)r.address=addr6(p.data()+n+16);
    else throw std::runtime_error("LCAF AFI-list without an IPv4/IPv6 address first");
    r.priority=p[n]; r.weight=p[n+1];
    r.lcaf=hex(std::vector<unsigned char>(p.begin()+n,p.begin()+n+total));
    return total;
}
static std::string format_addr(uint16_t afi,const unsigned char*a){ return afi==1?addr4(a):addr6(a); }
static void append6(std::vector<unsigned char>&o,const std::string&s){
    unsigned char a[16];
    if(inet_pton(AF_INET6,s.c_str(),a)!=1)throw std::runtime_error("ipv6 required");
    o.insert(o.end(),a,a+16);
}
static std::vector<WireReg> decode_register6(const std::vector<unsigned char>&p){
    if(p.size()<16)throw std::runtime_error("truncated map-register");
    uint32_t f=be32(p.data());
    if((f>>28)!=3)throw std::runtime_error("unsupported map-register");
    int records=f&0xff;
    if(records<1)throw std::runtime_error("map-register requires records");
    uint64_t nonce=0; std::memcpy(&nonce,p.data()+4,8);
    bool use_ttl=f&0x800,refresh=f&0x1000,merge=f&0x400,notify=f&0x100;
    size_t n=16+be16(p.data()+14);
    if(p.size()<n)throw std::runtime_error("truncated auth");
    std::vector<WireReg> out; out.reserve(records);
    for(int record=0;record<records;record++){
        if(p.size()<n+28)throw std::runtime_error("truncated eid-record");
        WireReg w; w.nonce=nonce; w.use_ttl=use_ttl; w.refresh=refresh; w.merge=merge; w.notify=notify;
        w.ttl=be32(p.data()+n); int rc=p[n+4]; w.mask=p[n+5];
        if(be16(p.data()+n+10)!=2)throw std::runtime_error("IPv6 EID required");
        w.prefix=addr6(p.data()+n+12)+"/"+std::to_string(w.mask); n+=28;
        for(int i=0;i<rc;i++){                // [LOCATOR_ANY_AFI_V1] an EID of one family, locators of either
            if(p.size()<n+8)throw std::runtime_error("truncated rloc-record");
            Rloc r; r.priority=p[n]; r.weight=p[n+1]; int ra=be16(p.data()+n+6);
            if(ra==16387){ n+=lcaf_locator(p,n,r); w.rlocs.push_back(r); continue; }     // [LCAF_LOCATOR_V1]
            if(ra!=1&&ra!=2)throw std::runtime_error("unsupported RLOC AFI "+std::to_string(ra));
            size_t al=ra==1?4:16; if(p.size()<n+8+al)throw std::runtime_error("truncated rloc-record");
            r.address=ra==1?addr4(p.data()+n+8):addr6(p.data()+n+8); w.rlocs.push_back(r); n+=8+al;
        }
        out.push_back(w);
    }
    // I bit (0x02000000): 128-bit xTR-ID and 64-bit site-ID follow the last record (control encode_xtr_id).
    if(f&0x02000000){
        if(p.size()!=n+24)throw std::runtime_error("map-register xtr-id trailer malformed");
        std::string xtr=hex(std::vector<unsigned char>(p.begin()+n,p.begin()+n+16));
        for(auto&w:out)w.xtr_id=xtr;
        n+=24;
    }
    if(n!=p.size())throw std::runtime_error("trailing map-register data");
    return out;
}
static WireReq decode_request6(const std::vector<unsigned char>&p){
    if(p.size()<38)throw std::runtime_error("truncated map-request");
    uint32_t f=be32(p.data());
    if((f>>28)!=1||(f&0xff)!=1)throw std::runtime_error("unsupported map-request");
    int irc=((f>>8)&0x1f)+1;
    WireReq w;
    std::memcpy(&w.nonce,p.data()+4,8);
    size_t n=12;
    uint16_t safi=be16(p.data()+n);
    n+=2;
    if(safi==1)n+=4;
    else if(safi==2)n+=16;
    else if(safi!=0)throw std::runtime_error("unsupported source AFI");
    for(int i=0;i<irc;i++){
        if(p.size()<n+2)throw std::runtime_error("truncated itr-rloc");
        uint16_t a=be16(p.data()+n);
        n+=2;
        if(a==1)n+=4;
        else if(a==2)n+=16;
        else throw std::runtime_error("unsupported itr AFI");
    }
    if(p.size()<n+20)throw std::runtime_error("truncated request record");
    int mask=p[n+1];
    if(be16(p.data()+n+2)!=2)throw std::runtime_error("IPv6 target required");
    w.target=addr6(p.data()+n+4)+"/"+std::to_string(mask);
    return w;
}
// ETR Map-Register (control: lisp-etr.py lisp_build_map_register / lisp_build_map_register_records,
// lisp.py lisp_map_register.encode, encode_xtr_id, lisp_compute_auth). Records are IPv4, authoritative,
// R bit, L bit clear. HMAC over the whole packet with the auth field zeroed.
struct EtrRegisterConfig{
    int key_id=0,alg=0; std::string password; bool notify=false,merge=false,proxy=false,refresh=false;
    std::vector<unsigned char> xtr_id,site_id; uint64_t nonce=0; uint32_t ttl=3;
};
static std::vector<unsigned char> encode_etr_register4(const EtrRegisterConfig&c,const std::vector<Mapping>&ms){
    std::vector<unsigned char>o;
    uint32_t f=(3u<<28)|0x02000000u|0x800u|(uint32_t)ms.size();
    if(c.proxy)f|=0x08000000u;
    if(c.refresh)f|=0x1000u;
    if(c.merge)f|=0x400u;
    if(c.notify)f|=0x100u;
    put32(o,f); put64_native(o,c.nonce); o.push_back((unsigned char)c.key_id); o.push_back((unsigned char)c.alg);
    uint16_t alen=c.alg==1?20:c.alg==2?32:0; put16(o,alen); o.insert(o.end(),alen,0);
    for(auto&m:ms){
        Prefix ep=prefix_parse(m.prefix);
        put32(o,c.ttl); o.push_back((unsigned char)m.rlocs.size()); o.push_back((unsigned char)ep.bits);
        put16(o,0x1000); put16(o,0); put16(o,1); o.insert(o.end(),ep.a,ep.a+4);
        for(auto&r:m.rlocs){
            o.push_back(r.priority); o.push_back(r.weight); o.push_back(0); o.push_back(0);
            put16(o,1); put16(o,1); append4(o,r.address);
        }
    }
    o.insert(o.end(),c.xtr_id.begin(),c.xtr_id.end()); o.insert(o.end(),c.site_id.begin(),c.site_id.end());
    if(alen){
        unsigned char md[EVP_MAX_MD_SIZE]; unsigned int n=0;
        const EVP_MD*evp=c.alg==1?EVP_sha1():EVP_sha256();
        if(!HMAC(evp,c.password.data(),(int)c.password.size(),o.data(),o.size(),md,&n)||n!=alen)
            throw std::runtime_error("map-register hmac failed");
        std::copy(md,md+alen,o.begin()+16);
    }
    return o;
}
static std::vector<unsigned char> encode_reply6(uint64_t nonce,const Mapping&m,int action=0){
    std::vector<unsigned char>o;
    put32(o,(2u<<28)|1u);
    put64_native(o,nonce);
    put32(o,m.ttl);
    o.push_back((unsigned char)m.rlocs.size());
    Prefix ep=prefix_parse(m.prefix);
    o.push_back((unsigned char)ep.bits);
    put16(o,(uint16_t)((action<<13)|0x1000));
    put16(o,0);
    put16(o,2);
    o.insert(o.end(),ep.a,ep.a+16);
    for(auto&r:m.rlocs){
        if(!r.lcaf.empty()){ auto b=unhex(r.lcaf); o.insert(o.end(),b.begin(),b.end()); continue; }   // [LCAF_LOCATOR_V1]
        o.push_back(r.priority);
        o.push_back(r.weight);
        o.push_back(0);
        o.push_back(0);
        put16(o,1);
        // [LOCATOR_ANY_AFI_V1] each locator in its own family: an IPv6 EID's locators may be IPv4
        unsigned char a4[4];
        if(inet_pton(AF_INET,r.address.c_str(),a4)==1){ put16(o,1); o.insert(o.end(),a4,a4+4); }
        else { put16(o,2); append6(o,r.address); }
    }
    return o;
}
static std::vector<unsigned char> encode_reply4(uint64_t nonce,const Mapping&m,int action=0){
    std::vector<unsigned char>o;
    put32(o,(2u<<28)|1u);
    put64_native(o,nonce);
    put32(o,m.ttl);
    o.push_back((unsigned char)m.rlocs.size());
    Prefix ep=prefix_parse(m.prefix);
    o.push_back((unsigned char)ep.bits);
    put16(o,(uint16_t)((action<<13)|0x1000));
    put16(o,0);
    put16(o,1);
    o.insert(o.end(),ep.a,ep.a+4);
    for(auto&r:m.rlocs){
        if(!r.lcaf.empty()){ auto b=unhex(r.lcaf); o.insert(o.end(),b.begin(),b.end()); continue; }   // [LCAF_LOCATOR_V1]
        o.push_back(r.priority);
        o.push_back(r.weight);
        o.push_back(0);
        o.push_back(0);
        put16(o,1);
        put16(o,1);
        append4(o,r.address);
    }
    return o;
}
class Engine{
    std::map<std::string,Mapping> cache,db,ddt;
    std::map<std::string,Mapping> etr_db;   // ETR-local database mapping (local backend); never the Map-Server's db
    std::map<std::string,std::string> resolvers;
    std::map<std::string,bool> sites;
    std::map<std::string,std::pair<std::string,std::string>> registration_governance;
    std::map<std::string,std::string> site_keys;
    // [MAP_REGISTER_ENCRYPTION_V1] lispers.net's `lisp encryption-keys { map-register-key = [id]key }`: a Map-Register
    // with the E bit (0x2000) and a 3-bit key-id (bits 14-16) has everything after its first 4 bytes encrypted with
    // ChaCha20 (Bernstein's, 64-bit nonce): key = the key string left-padded with '0' to 32 characters, nonce = eight
    // ASCII '0's, 20 rounds; authentication was computed over the plaintext. Keys are held here, never in memory.
    std::map<int,std::string> ms_encryption_keys;
    using MappingTable=std::map<std::string,Mapping>;
    using GovernanceTable=std::map<std::string,std::pair<std::string,std::string>>;
    // [SITE_OPTIONS_V1] a held site: first = active, second = accept-more-specifics (the names older code reads), and
    // lispers.net's site options, found missing by the acceptance test (L2.15): shutdown, allowed-rloc, force-ttl,
    // proxy-reply-action, echo-nonce-capable, force-proxy-reply, pitr-proxy-reply-drop.
    struct SitePol{ bool first=false,second=false,shutdown=false,echo=false,force_proxy=true,pitr_drop=false;
                    int force_ttl=-1; std::string pra,policy; std::vector<std::string> allowed_rlocs; };
    using SiteTable=std::map<std::string,SitePol>;
    using ResolverTable=std::map<std::string,bool>;
    // ETR-owned map-server configuration (control: lisp_map_server_command / class lisp_ms). Public policy
    // only; the authentication password lives in ms_keys, process-private, never in a cell.
    struct MapServerCfg{
        std::string address,ms_name="all",alg="none"; int key_id=0; uint64_t site_id=0;
        bool proxy_reply=false,merge=false,refresh=false,want_map_notify=false,active=true;
    };
    using MapServerTable=std::map<std::string,MapServerCfg>;
    using KeyTable=std::map<std::string,std::string>;
    std::atomic<const KeyTable*> ms_keys{nullptr};
    std::map<std::string,uint64_t> notifies_received;   // request thread only      // process-private; written only by the request thread
    MapServerTable ms_local;
    ribbit::Ebr ebr;
    template<class T> using Snapshot=const T*;
    template<class T> Snapshot<T> snap_load(const std::atomic<const T*>& p, ribbit::Ebr::Guard& g) {
        return g.protect(p);
    }
    template<class T> void snap_store(std::atomic<const T*>& p,T* next) {
        auto* old=p.exchange(next,std::memory_order_seq_cst);
        ebr.retire(old);
    }
    struct HeldResolverView {
        std::array<std::atomic<HeldSlot<Mapping>*>,129> registrations{};                        // [HELD_TUPLES_V1]
        std::array<std::atomic<HeldSlot<std::pair<std::string,std::string>>*>,129> governance{};
        std::array<std::atomic<const SiteTable*>,129> site_policy{};
        std::array<std::atomic<const MappingTable*>,129> ddt_delegations{};
        std::array<bool,129> registration_started{},site_started{},ddt_started{};
        std::array<std::thread,129> registration_threads,governance_threads,site_threads,ddt_threads;
        std::thread manifest_thread,site_manifest_thread,ddt_manifest_thread;
        uint64_t manifest_watermark=0,site_manifest_watermark=0,ddt_manifest_watermark=0;
        HeldResolverView(){
            for(auto&x:registrations)x.store(nullptr);
            for(auto&x:governance)x.store(nullptr);                  // allocated when their length is first held
            for(auto&x:site_policy)x.store(nullptr);
            for(auto&x:ddt_delegations)x.store(nullptr);
        }
    };
    // [ONE_SET_OF_VIEWS_PER_PARTICIPANT_V1] A participant is a process, and it holds each view ONCE, however many
    // Engines serve its requests: an Engine made with share_views_of(owner) reads the owner's resolver views instead of
    // arming its own. Views are armed on first use by whichever thread needs one: an insert-only lock-free table; the
    // thread that wins the insert builds the view and marks it ready, any other waits (briefly) for ready. Before
    // this, a Map-Server front with 10 Engines held 10 copies of every view: every write woke all 10, and each re-read
    // and wrote its own applied cell -- 15 of the 19 frames one Map-Register cost.
    struct ResolverRegistry{
        struct Node{ std::string key; std::unique_ptr<HeldResolverView> view; std::atomic<int> ready{0}; Node*next=nullptr; };
        static constexpr size_t B=256; std::atomic<Node*> b[B]={};
        template<class F> void each(F f){ for(auto&slot:b)for(Node*n=slot.load(std::memory_order_acquire);n;n=n->next)if(n->ready.load(std::memory_order_acquire))f(*n->view); }
        size_t size(){ size_t k=0; each([&](HeldResolverView&){++k;}); return k; }
        ~ResolverRegistry(){ for(auto&slot:b){ Node*n=slot.load(); while(n){ Node*x=n->next; delete n; n=x; } } }
    };
    ResolverRegistry held_resolvers;
    std::atomic<std::string*> failed_{nullptr};
    void fail(const std::string&why){ auto*m=new std::string(why); std::string*z=nullptr; if(!failed_.compare_exchange_strong(z,m))delete m; }
    template<class F> std::thread spawn(F f){
        return std::thread([this,f]()mutable{
            try{ f(); }
            catch(const std::exception&x){ if(!stopping)fail(std::string("an Engine thread stopped: ")+x.what()); }
        });
    }
    Engine*view_owner=this;
    struct HeldMapCacheView {
        std::array<std::atomic<const MappingTable*>,129> mappings{};
        std::array<bool,129> started{};
        std::array<std::thread,129> threads;
        std::thread manifest_thread;
        uint64_t manifest_watermark=0;
        HeldMapCacheView(){for(auto&x:mappings)x.store(nullptr);}
    };
    std::map<std::string,std::unique_ptr<HeldMapCacheView>> held_map_caches;
    struct HeldDatabaseView { std::atomic<const MappingTable*> mappings{nullptr}; std::thread thread; uint64_t watermark=0; };
    std::map<std::string,std::unique_ptr<HeldDatabaseView>> held_databases;
    struct HeldMapResolverView {
        std::atomic<const ResolverTable*> active{nullptr};
        std::thread thread;
        uint64_t watermark=0;
    };
    std::unique_ptr<HeldMapResolverView> held_map_resolvers;
    using LiveTable=std::map<std::string,int64_t>;
    struct HeldLivenessView { std::atomic<const LiveTable*> table{nullptr}; std::thread thread; uint64_t watermark=0; };
    std::unique_ptr<HeldLivenessView> held_liveness_view;
    struct HeldMapServerView { std::atomic<const MapServerTable*> table{nullptr}; std::thread thread; uint64_t watermark=0; };
    std::unique_ptr<HeldMapServerView> held_map_servers;
    std::atomic<bool> stopping{false};
    uint64_t request_reads_last=0;
    uint64_t request_reads_total=0;
    uint64_t authorization_reads_last=0;
    uint64_t authorization_reads_total=0;
    uint64_t ddt_reads_last=0;
    uint64_t ddt_reads_total=0;
    uint64_t map_cache_reads_last=0;
    uint64_t map_cache_reads_total=0;
    uint64_t database_mapping_reads_last=0;
    uint64_t map_server_reads_last=0,map_server_reads_total=0; bool in_map_server_request=false;
    uint64_t database_mapping_reads_total=0;
    bool in_resolution_request=false;
    bool in_authorization_request=false;
    bool in_ddt_request=false;
    bool in_map_cache_request=false;
    bool in_database_mapping_request=false;
    std::unique_ptr<frogram::Session> session;
    std::string ram_api="/ram.php";
    std::unique_ptr<frogram::MemoryApi> ram;
    // [RESIDENT_REGION_V1] John 2026-09-28: the RAM server running a region operation through Engines that were
    // clients of itself -- loopback sessions, copied views of its own rows, a mutex per Engine -- was "absolutely
    // unacceptable". A resident Engine is the region's code running IN the memory: ram is the memory itself, every read
    // is of the row it names, nothing is held, copied or waited for, and one is made per operation, so nothing is
    // shared between operations but the rows. Site and encryption keys, the process's secrets, live in private rows
    // (service RESIDENT_SECRETS) that the RAM server's network API refuses to serve.
    bool resident=false;
    static constexpr const char* RESIDENT_SECRETS="#lisp-secrets";
    static std::string ram_var(const std::string& kind,const std::string& iid,const std::string& group){
        return kind+"|"+iid+"|"+group;
    }
    static std::string ram_var_len(const std::string& kind,const std::string& iid,const std::string& group,int bits){
        return ram_var(kind,iid,group)+"|/"+std::to_string(bits);
    }
    static Mapping from_cell(const frogram::Cell& c){
        Mapping m;
        auto& b=c.bag;
        m.iid=arg(b,"iid","0"); m.prefix=arg(b,"prefix",c.instance); m.group=arg(b,"group");
        if(b["ttl"].type==Json::Num)m.ttl=(uint32_t)b["ttl"].n;
        if(b["last_registered"].type==Json::Num)m.last_registered=(int64_t)b["last_registered"].n;
        if(b["expires_at"].type==Json::Num)m.expires_at=(int64_t)b["expires_at"].n;
        if(b["live_key"].type==Json::Str)m.live_key=b["live_key"].s;
        if(b["use_register_ttl"].type==Json::Bool)m.use_register_ttl=b["use_register_ttl"].b;
        if(b["registered"].type==Json::Bool)m.registered=b["registered"].b;
        m.xtr_id=arg(b,"xtr_id"); m.site_id=arg(b,"site_id");
        if(b["merge"].type==Json::Bool)m.merge=b["merge"].b;
        auto& x=b["rlocs"];
        if(x.type==Json::Arr)for(auto&v:x.a){ Rloc r; r.address=arg(v,"address");
            if(v["priority"].type==Json::Num)r.priority=(int)v["priority"].n;
            if(v["weight"].type==Json::Num)r.weight=(int)v["weight"].n;
            if(v["lcaf"].type==Json::Str)r.lcaf=v["lcaf"].s;                  // [LCAF_LOCATOR_V1]
            if(!r.address.empty())m.rlocs.push_back(r); }
        return m;
    }
    static std::string bag_json(const Mapping&m){ return mapping_json(m); }
    static std::string key(const std::string&iid,const std::string&p,const std::string&g){ return iid+"|"+p+"|"+g; }
    static std::string view_key(const std::string&iid,const std::string&g){ return iid+"|"+g; }
    std::vector<frogram::Cell> read_ram(const std::string& variable,const std::string& instance="", int64_t after=-1,double wait_s=0){
        if(in_resolution_request){++request_reads_last;++request_reads_total;}
        if(in_authorization_request){++authorization_reads_last;++authorization_reads_total;}
        if(in_ddt_request){++ddt_reads_last;++ddt_reads_total;}
        if(in_map_cache_request){++map_cache_reads_last;++map_cache_reads_total;}
        if(in_database_mapping_request){++database_mapping_reads_last;++database_mapping_reads_total;}
        if(in_map_server_request){++map_server_reads_last;++map_server_reads_total;}
        return ram->read("lisp",variable,instance,after,wait_s);
    }
    // [READ_YOUR_OWN_WRITE_V1] John 2026-09-27: put() is complete when THIS participant's own consequences of the
    // write are observable. Every held view reads its variable in a loop -- read, apply everything read, read again --
    // so when a watcher comes back to read, it has applied every cell up to its `after`. held_read() records that
    // watermark per variable in a lock-free table; await_own() waits until the watermark of each variable the write
    // touched has reached the written cell's id. Only this participant's own views are waited for; nobody else is.
    // [AWAIT_ON_THE_MARK_V1] a waiter sleeps on the mark's own futex word and the watch that advances the mark wakes it
    // (only when someone waits there) -- it used to poll the mark every 20 microseconds for as long as the wait lasted
    struct AppliedMark{ std::atomic<uint64_t> key{0}, id{0}; std::atomic<uint32_t> seq{0}, waiters{0}; };
    static constexpr size_t APPLIED_SLOTS=4096;
    std::unique_ptr<AppliedMark[]> applied_marks{new AppliedMark[APPLIED_SLOTS]};
    static uint64_t var_hash(const std::string&v){ uint64_t h=1469598103934665603ull; for(unsigned char c:v){h^=c;h*=1099511628211ull;} return h|1; }
    AppliedMark* mark_find(const std::string&v,bool create){
        const uint64_t h=var_hash(v);
        for(size_t n=0,i=h%APPLIED_SLOTS;n<APPLIED_SLOTS;++n,i=(i+1)%APPLIED_SLOTS){
            uint64_t k=applied_marks[i].key.load(std::memory_order_acquire);
            if(k==h)return &applied_marks[i];
            if(k==0){ if(!create)return nullptr;
                uint64_t z=0; if(applied_marks[i].key.compare_exchange_strong(z,h,std::memory_order_acq_rel))return &applied_marks[i];
                if(z==h)return &applied_marks[i]; }
        }
        throw std::runtime_error("applied-mark table full ("+std::to_string(APPLIED_SLOTS)+" held variables)");
    }
    std::vector<frogram::Cell> held_read(const std::string&variable,uint64_t after){
        AppliedMark*m=mark_find(variable,true); uint64_t cur=m->id.load(std::memory_order_acquire);
        bool advanced=false;
        while(after>cur){ if(m->id.compare_exchange_weak(cur,after,std::memory_order_acq_rel)){ advanced=true; break; } }
        if(advanced){ m->seq.fetch_add(1,std::memory_order_seq_cst);
            if(m->waiters.load(std::memory_order_seq_cst))
                ::syscall(SYS_futex,reinterpret_cast<uint32_t*>(&m->seq),FUTEX_WAKE_PRIVATE,INT32_MAX,nullptr,nullptr,0); }
        return ram->read("lisp",variable,"",(int64_t)after,3600.0);
    }
    public:
    // Wait until this participant's held views have applied every (variable, id) a write produced. A variable no view
    // of ours holds needs no waiting. Views created while waiting (a new prefix length) are waited for too.
    void await_own(const std::vector<std::pair<std::string,uint64_t>>&written,double timeout_s=10.0){
        if(view_owner!=this){ view_owner->await_own(written,timeout_s); return; }
        auto deadline=std::chrono::steady_clock::now()+std::chrono::duration<double>(timeout_s);
        for(int pass=0;pass<2;++pass)for(auto&w:written){
            AppliedMark*m=mark_find(w.first,false); if(!m)continue;
            while(m->id.load(std::memory_order_acquire)<w.second){
                auto now=std::chrono::steady_clock::now();
                if(now>deadline)
                    throw std::runtime_error("read-your-own-write: held view of "+w.first+" did not apply cell "+std::to_string(w.second)+" within "+std::to_string(timeout_s)+" s");
                const uint32_t seen=m->seq.load(std::memory_order_seq_cst);
                m->waiters.fetch_add(1,std::memory_order_seq_cst);
                if(m->id.load(std::memory_order_seq_cst)<w.second){
                    auto left=std::chrono::duration_cast<std::chrono::nanoseconds>(deadline-now).count();
                    timespec ts{time_t(left/1000000000),long(left%1000000000)};
                    ::syscall(SYS_futex,reinterpret_cast<uint32_t*>(&m->seq),FUTEX_WAIT_PRIVATE,seen,&ts,nullptr,0);
                }
                m->waiters.fetch_sub(1,std::memory_order_seq_cst);
            }
        }
    }
    private:
    static int checked_bits(const frogram::Cell&c){ int b=std::stoi(c.instance); if(b<0||b>128)throw std::runtime_error("bad prefix length manifest"); return b; }
    // [APPLIED_CELL_HAS_ONE_WRITER_V1] An applied cell is written by the one participant whose progress it reports
    // (PROGRAMMING-RIBBIT-BEST-PRACTICES: "an applied cell that it alone writes"). The participant is the process:
    // its identity -- host, pid and a random number drawn once at start -- is part of the cell's address, so no other
    // participant's progress can ever satisfy this participant's wait.
    static const std::string&participant_id(){
        static const std::string id=[]{
            char host[256]={0}; gethostname(host,sizeof host-1);
            std::random_device rd; uint64_t r=(uint64_t(rd())<<32)^rd(); char b[64];
            snprintf(b,sizeof b,"%s:%d:%016llx",host,(int)getpid(),(unsigned long long)r); return std::string(b);
        }();
        return id;
    }
    static std::string own(const std::string&applied_var){ return applied_var+"|"+participant_id(); }
    void applied(const std::string&variable,const std::string&instance,uint64_t source_id){
        if(ram)ram->write_nowait("lisp",own(variable),instance,"{\"source_id\":"+std::to_string(source_id)+"}");
    }
    // Wait on the participant's applied truth until ready() holds. timeout_s < 0 waits indefinitely; otherwise
    // the held reads are bounded so the caller gets "timeout" rather than a value that is not there.
    bool wait_applied(const std::string&variable,const std::function<bool()>&ready,double timeout_s=-1){
        auto deadline=std::chrono::steady_clock::now()+std::chrono::duration<double>(timeout_s<0?0:timeout_s);
        for(;;){
            auto seen=ram->read("lisp",own(variable)); uint64_t after=0; for(auto&c:seen)if(c.id>after)after=c.id;
            if(ready())return true;
            double wait_s=5.0;
            if(timeout_s>=0){
                double left=std::chrono::duration<double>(deadline-std::chrono::steady_clock::now()).count();
                if(left<=0)return false;
                if(left<wait_s)wait_s=left;
            }
            try{ ram->read("lisp",own(variable),"",(int64_t)after,wait_s); }
            catch(const frogram::Unreachable&){ return ready(); }
            if(ready())return true;
        }
    }
    void apply_mapping_slot(std::atomic<const MappingTable*>&slot,const std::vector<frogram::Cell>&cells){
        auto g=ebr.guard(); auto old=snap_load(slot,g); auto next=new MappingTable(old?*old:MappingTable{});
        for(auto&c:cells)(*next)[c.instance]=from_cell(c);
        snap_store(slot,next);
    }
    // [HELD_TUPLES_V1] a registration cell becomes the tuple held for its instance; the one it replaces goes to the EBR
    using GovPair=std::pair<std::string,std::string>;
    void hold_registrations(HeldSlot<Mapping>&slot,const std::vector<frogram::Cell>&cells){
        for(auto&c:cells){ auto bar=c.instance.find('|');
            ebr.retire(slot.put(prefix_key(bar==std::string::npos?c.instance:c.instance.substr(0,bar)),c.instance,
                                new HeldSlot<Mapping>::Version{c.id,from_cell(c)})); }
    }
    void hold_governance(HeldSlot<GovPair>&slot,const std::vector<frogram::Cell>&cells){
        for(auto&c:cells)ebr.retire(slot.put(prefix_key(c.instance),c.instance,new HeldSlot<GovPair>::Version{c.id,GovPair(arg(c.bag,"mode"),arg(c.bag,"site_id"))}));
    }
    // [OWN_WRITES_HELD_V1] The view that would hold this variable's cells, if this participant holds it already.
    HeldResolverView* held_resolver_if_any(const std::string&iid,const std::string&group){
        if(view_owner!=this)return view_owner->held_resolver_if_any(iid,group);
        auto vk=view_key(iid,group);
        for(auto*n=held_resolvers.b[std::hash<std::string>()(vk)%ResolverRegistry::B].load(std::memory_order_acquire);n;n=n->next)
            if(n->key==vk&&n->ready.load(std::memory_order_acquire))return n->view.get();
        return nullptr;
    }
    // [OWN_WRITES_HELD_V1] John 2026-09-27: a put is complete when its own consequences are observable to the
    // participant. The region operation returns the cells it wrote; the participant holds them in its views at once --
    // the same tuples, by the same ids, that its watches bring later (the newer id wins, the same cell is held once).
    // It used to wait for its watch to bring them back, and a watch whose held read was already on its way back could
    // take a whole further round trip to the memory. Returns the written cells it held; the caller waits for the rest.
    std::set<std::pair<std::string,uint64_t>> hold_own_cells(const frogram::Json&cells){
        std::set<std::pair<std::string,uint64_t>> held;
        for(auto&e:cells.a){
            const std::string var=arg(e,"variable"); const uint64_t id=uint64_t(e["id"].n);
            // registration|iid|group|/bits  or  registration-governance|iid|group|/bits
            const bool reg=var.rfind("registration|",0)==0, gov=var.rfind("registration-governance|",0)==0;
            if(!reg&&!gov)continue;
            auto p1=var.find('|'), p2=var.find('|',p1+1), p3=var.rfind("|/");
            if(p2==std::string::npos||p3==std::string::npos||p3<p2)continue;
            const std::string iid=var.substr(p1+1,p2-p1-1), group=var.substr(p2+1,p3-p2-1);
            const int bits=std::atoi(var.c_str()+p3+2); if(bits<0||bits>128)continue;
            HeldResolverView*v=held_resolver_if_any(iid,group); if(!v)continue;
            frogram::Cell c; c.id=id; c.service="lisp"; c.variable=var; c.instance=arg(e,"instance"); c.bag=e["bag"];
            if(reg){ auto*rs=v->registrations[bits].load(std::memory_order_acquire); if(!rs)continue; hold_registrations(*rs,{c}); }
            else { auto*gs=v->governance[bits].load(std::memory_order_acquire); if(!gs)continue; hold_governance(*gs,{c}); }
            held.insert({var,id});
        }
        return held;
    }
    template<class T,class Apply> void watch_held(HeldSlot<T>*slot,Apply apply,const std::string variable,const std::string applied_var,
                                                  const std::string applied_instance,uint64_t after){
        applied(applied_var,applied_instance,after);
        while(!stopping)try{ auto cells=held_read(variable,after); if(stopping)break;
            for(auto&c:cells)if(c.id>after)after=c.id;
            if(!cells.empty()){ (this->*apply)(*slot,cells); applied(applied_var,applied_instance,after); } }
        catch(const frogram::Unreachable&){if(stopping)break;return;}
    }
    void apply_governance_slot(std::atomic<const GovernanceTable*>&slot,const std::vector<frogram::Cell>&cells){
        auto g=ebr.guard(); auto old=snap_load(slot,g); auto next=new GovernanceTable(old?*old:GovernanceTable{});
        for(auto&c:cells)(*next)[c.instance]={arg(c.bag,"mode"),arg(c.bag,"site_id")};
        snap_store(slot,next);
    }
    static SitePol site_pol_of(const Json&bag){
        // (force-nat-proxy-reply proxy-replies too: lispers.net's NAT path narrows the locator set only for xTRs
        // registered behind NAT through RTRs -- none here -- and otherwise answers as a forced proxy)
        auto flag=[&](const char*k,bool d){ return bag[k].type==Json::Bool?bag[k].b:d; };
        SitePol sp; sp.first=flag("active",true); sp.second=flag("accept_more_specifics",false);
        sp.shutdown=flag("shutdown",false); sp.echo=flag("echo_nonce_capable",false);
        sp.force_proxy=flag("force_proxy_reply",true)||flag("force_nat_proxy_reply",false); sp.pitr_drop=flag("pitr_proxy_reply_drop",false);
        if(bag["force_ttl"].type==Json::Num)sp.force_ttl=(int)bag["force_ttl"].n;
        if(bag["proxy_reply_action"].type==Json::Str)sp.pra=bag["proxy_reply_action"].s;
        if(bag["policy_name"].type==Json::Str)sp.policy=bag["policy_name"].s;
        for(auto&r:bag["allowed_rlocs"].a)if(r.type==Json::Str)sp.allowed_rlocs.push_back(r.s);
        return sp;
    }
    // [RESIDENT_REGION_V1] The site rows that could cover a prefix, read where they are: for each site length the region
    // has (its site-lengths manifest) no longer than the prefix, that length's site rows. Sites are configuration.
    template<class F> void each_site_resident(const std::string&iid,const std::string&group,int bits,F f){
        for(auto&lc:ram->read("lisp","site-lengths|"+iid+"|"+group)){
            int L=lc.bag["bits"].type==Json::Num?(int)lc.bag["bits"].n:-1;
            if(L<0||L>bits)continue;
            for(auto&c:ram->read("lisp",ram_var_len("site",iid,group,L)))f(c.instance,site_pol_of(c.bag));
        }
    }
    void apply_site_slot(std::atomic<const SiteTable*>&slot,const std::vector<frogram::Cell>&cells){
        auto g=ebr.guard(); auto old=snap_load(slot,g); auto next=new SiteTable(old?*old:SiteTable{});
        for(auto&c:cells)(*next)[c.instance]=site_pol_of(c.bag);
        snap_store(slot,next);
    }
    void watch_mapping_slot(std::atomic<const MappingTable*>*slot,const std::string variable,const std::string applied_var,
                            const std::string applied_instance,uint64_t after){
        applied(applied_var,applied_instance,after);
        while(!stopping)try{ auto cells=held_read(variable,after); if(stopping)break;
            for(auto&c:cells)if(c.id>after)after=c.id;
            if(!cells.empty()){apply_mapping_slot(*slot,cells);applied(applied_var,applied_instance,after);} }
        catch(const frogram::Unreachable&){if(stopping)break;return;}
    }
    void watch_governance_slot(std::atomic<const GovernanceTable*>*slot,const std::string variable,const std::string applied_var,
                               const std::string applied_instance,uint64_t after){
        applied(applied_var,applied_instance,after);
        while(!stopping)try{ auto cells=held_read(variable,after); if(stopping)break;
            for(auto&c:cells)if(c.id>after)after=c.id;
            if(!cells.empty()){apply_governance_slot(*slot,cells);applied(applied_var,applied_instance,after);} }
        catch(const frogram::Unreachable&){if(stopping)break;return;}
    }
    void watch_site_slot(std::atomic<const SiteTable*>*slot,const std::string variable,const std::string applied_var,
                         const std::string applied_instance,uint64_t after){
        applied(applied_var,applied_instance,after);
        while(!stopping)try{ auto cells=held_read(variable,after); if(stopping)break;
            for(auto&c:cells)if(c.id>after)after=c.id;
            if(!cells.empty()){apply_site_slot(*slot,cells);applied(applied_var,applied_instance,after);} }
        catch(const frogram::Unreachable&){if(stopping)break;return;}
    }
    void materialize_map_cache_length(HeldMapCacheView&v,const std::string&iid,const std::string&group,int bits){
        if(v.started[bits])return;
        v.started[bits]=true;
        auto cells=ram->read("lisp",ram_var_len("map-cache",iid,group,bits)); uint64_t after=0; for(auto&c:cells)if(c.id>after)after=c.id;
        apply_mapping_slot(v.mappings[bits],cells); auto av="map-cache-applied|"+iid+"|"+group;
        v.threads[bits]=spawn([this,&v,iid,group,bits,after,av]{watch_mapping_slot(&v.mappings[bits],ram_var_len("map-cache",iid,group,bits),av,"map-cache|"+std::to_string(bits),after);});
    }
    void watch_map_cache_manifest(HeldMapCacheView*vp,const std::string iid,const std::string group,uint64_t after){
        while(!stopping)try{ auto cells=held_read("map-cache-lengths|"+iid+"|"+group,after); if(stopping)break;
            for(auto&c:cells){if(c.id>after)after=c.id;materialize_map_cache_length(*vp,iid,group,checked_bits(c));} }
        catch(const frogram::Unreachable&){if(stopping)break;return;}
    }
    HeldMapCacheView& held_map_cache(const std::string&iid,const std::string&group){
        auto vk=view_key(iid,group); auto it=held_map_caches.find(vk); if(it!=held_map_caches.end())return *it->second;
        auto owned=std::make_unique<HeldMapCacheView>(); HeldMapCacheView&v=*owned;
        auto manifest=read_ram("map-cache-lengths|"+iid+"|"+group); for(auto&c:manifest)if(c.id>v.manifest_watermark)v.manifest_watermark=c.id;
        held_map_caches[vk]=std::move(owned); for(auto&c:manifest)materialize_map_cache_length(v,iid,group,checked_bits(c));
        auto after=v.manifest_watermark; v.manifest_thread=spawn([this,&v,iid,group,after]{watch_map_cache_manifest(&v,iid,group,after);}); return v;
    }
    void watch_database(HeldDatabaseView*vp,const std::string iid,const std::string group,uint64_t after){
        auto av="database-mapping-applied"+etr_tag()+"|"+iid+"|"+group; applied(av,"database-mapping",after);
        while(!stopping)try{ auto cells=held_read(db_var(iid,group),after); if(stopping)break;
            for(auto&c:cells)if(c.id>after)after=c.id;
            if(!cells.empty()){apply_mapping_slot(vp->mappings,cells);applied(av,"database-mapping",after);} }
        catch(const frogram::Unreachable&){if(stopping)break;return;}
    }
    HeldDatabaseView& held_database(const std::string&iid,const std::string&group){
        auto vk=view_key(iid,group); auto it=held_databases.find(vk); if(it!=held_databases.end())return *it->second;
        auto owned=std::make_unique<HeldDatabaseView>(); HeldDatabaseView&v=*owned;
        auto cells=read_ram(db_var(iid,group)); for(auto&c:cells)if(c.id>v.watermark)v.watermark=c.id;
        apply_mapping_slot(v.mappings,cells); held_databases[vk]=std::move(owned); auto after=v.watermark;
        v.thread=spawn([this,&v,iid,group,after]{watch_database(&v,iid,group,after);}); return v;
    }
    void apply_map_resolver_changes(HeldMapResolverView&v,const std::vector<frogram::Cell>&cells){
        auto g=ebr.guard(); auto old=snap_load(v.active,g); auto next=new ResolverTable(old?*old:ResolverTable{});
        for(auto&c:cells){bool active=true;if(c.bag["active"].type==Json::Bool)active=c.bag["active"].b;(*next)[c.instance]=active;} snap_store(v.active,next);
    }
    void watch_map_resolvers(HeldMapResolverView*vp,uint64_t after){
        applied("map-resolver-applied","map-resolver",after);
        while(!stopping)try{auto cells=held_read("map-resolver",after);if(stopping)break;
            for(auto&c:cells)if(c.id>after)after=c.id;
            if(!cells.empty()){apply_map_resolver_changes(*vp,cells);applied("map-resolver-applied","map-resolver",after);} }
        catch(const frogram::Unreachable&){if(stopping)break;return;}
    }
    static std::string ms_bag(const MapServerCfg&c){
        std::ostringstream o;
        o<<"{\"address\":"<<q(c.address)<<",\"ms_name\":"<<q(c.ms_name)<<",\"alg\":"<<q(c.alg)<<",\"key_id\":"<<c.key_id
         <<",\"site_id\":"<<c.site_id<<",\"proxy_reply\":"<<(c.proxy_reply?"true":"false")<<",\"merge\":"<<(c.merge?"true":"false")
         <<",\"refresh\":"<<(c.refresh?"true":"false")<<",\"want_map_notify\":"<<(c.want_map_notify?"true":"false")
         <<",\"active\":"<<(c.active?"true":"false")<<"}";
        return o.str();
    }
    static MapServerCfg ms_from(const Json&b,const std::string&address){
        MapServerCfg c; c.address=address;
        auto str=[&](const char*k,std::string&v){ if(b[k].type==Json::Str)v=b[k].s; };
        auto boo=[&](const char*k,bool&v){ if(b[k].type==Json::Bool)v=b[k].b; };
        str("ms_name",c.ms_name); str("alg",c.alg);
        if(b["key_id"].type==Json::Num)c.key_id=(int)b["key_id"].n;
        if(b["site_id"].type==Json::Num)c.site_id=(uint64_t)b["site_id"].n;
        boo("proxy_reply",c.proxy_reply); boo("merge",c.merge); boo("refresh",c.refresh);
        boo("want_map_notify",c.want_map_notify); boo("active",c.active);
        return c;
    }
    void apply_map_server_changes(HeldMapServerView&v,const std::vector<frogram::Cell>&cells){
        auto g=ebr.guard(); auto old=snap_load(v.table,g); auto next=new MapServerTable(old?*old:MapServerTable{});
        for(auto&c:cells)(*next)[c.instance]=ms_from(c.bag,c.instance);
        snap_store(v.table,next);
    }
    void watch_map_servers(HeldMapServerView*vp,uint64_t after){
        applied("etr-map-server-applied","etr-map-server",after);
        while(!stopping)try{
            auto cells=held_read("etr-map-server",after); if(stopping)break;
            for(auto&c:cells)if(c.id>after)after=c.id;
            if(!cells.empty()){ apply_map_server_changes(*vp,cells); applied("etr-map-server-applied","etr-map-server",after); }
        } catch(const frogram::Unreachable&){ if(stopping)break; return; }
    }
    HeldMapServerView& held_map_server_view(){
        if(held_map_servers)return *held_map_servers;
        held_map_servers=std::make_unique<HeldMapServerView>();
        auto cells=read_ram("etr-map-server");
        for(auto&c:cells)if(c.id>held_map_servers->watermark)held_map_servers->watermark=c.id;
        apply_map_server_changes(*held_map_servers,cells); auto after=held_map_servers->watermark;
        held_map_servers->thread=spawn([this,after]{watch_map_servers(held_map_servers.get(),after);});
        return *held_map_servers;
    }
    // the ETR's view of one map-server: held truth (FNW1) or the local table; nullopt-like via bool
    bool map_server_config(const std::string&address,MapServerCfg&out){
        if(ram){
            map_server_reads_last=0; in_map_server_request=true; HeldMapServerView*vp=nullptr;
            try{ vp=&held_map_server_view(); in_map_server_request=false; } catch(...){ in_map_server_request=false; throw; }
            auto&v=*vp; auto g=ebr.guard(); auto t=snap_load(v.table,g);
            if(!t)return false;
            auto it=t->find(address); if(it==t->end()||!it->second.active)return false;
            out=it->second; return true;
        }
        auto it=ms_local.find(address); if(it==ms_local.end()||!it->second.active)return false; out=it->second; return true;
    }
    void set_ms_key(const std::string&address,const std::string&pw){
        auto g=ebr.guard(); auto old=snap_load(ms_keys,g); auto next=new KeyTable(old?*old:KeyTable{});
        if(pw.empty())next->erase(address); else (*next)[address]=pw;
        snap_store(ms_keys,next);
    }
    bool ms_key(const std::string&address,std::string&pw){
        auto g=ebr.guard(); auto t=snap_load(ms_keys,g); if(!t)return false;
        auto it=t->find(address); if(it==t->end())return false; pw=it->second; return true;
    }
    // Build one ETR Map-Register for a map-server from its configuration and a database snapshot.
    std::vector<unsigned char> etr_register_for(const MapServerCfg&m,const MappingTable*db,const std::vector<unsigned char>&xtr,
                                                uint64_t nonce,bool periodic,size_t&records){
        EtrRegisterConfig c; c.key_id=m.key_id; c.alg=m.alg=="sha1"?1:m.alg=="sha256"?2:0;
        c.notify=m.want_map_notify; c.merge=m.merge; c.proxy=m.proxy_reply; c.refresh=m.refresh&&periodic;
        if(c.alg&&!ms_key(m.address,c.password))throw std::runtime_error("no key for map-server "+m.address);
        c.xtr_id=xtr; c.site_id.assign(8,0); for(int i=0;i<8;i++)c.site_id[7-i]=(unsigned char)(m.site_id>>(8*i));
        c.nonce=nonce;
        std::vector<Mapping> ms; if(db)for(auto&kv:*db)
            if(kv.second.registered&&kv.second.prefix.find(':')==std::string::npos)ms.push_back(kv.second);
        records=ms.size(); if(ms.empty())return {};
        return encode_etr_register4(c,ms);
    }
    // The registrar participant (control: lisp_process_register_timer / lisp_etr_map_server_command).
    // Its clock is a bounded held read on the map-server configuration: a timeout is the periodic send; a wake
    // carrying a (re)published active map-server entry is the immediate, non-refresh register (the control
    // triggers a register whenever the map-server command is applied). Memory holds current values, so a
    // delete-then-add seen in one wake is just the add — which is still a trigger under this rule. It holds its own copy of
    // the configuration it read, writes only its own cells (etr-register-sent|<ms>), and sends each register as
    // a UDP datagram to the map-server. Stop is a flag honoured at the next wake; the registrar publishes
    // state "stopped" when it has actually stopped.
    struct Registrar{ std::thread thread,receiver; std::atomic<bool> stop{false}; std::vector<unsigned char> xtr;
                      int fd=-1; uint16_t port=0; HeldMapServerView*msv=nullptr;
                      double first_s=5,interval_s=60; int udp_port=4342; HeldDatabaseView*db=nullptr;
                      std::vector<frogram::Cell> baseline; };
    std::unique_ptr<Registrar> registrar;
    void registrar_send(Registrar&r,const MapServerCfg&m,uint64_t nonce,bool periodic,std::map<std::string,uint64_t>&sends){
        size_t records=0; std::vector<unsigned char> pkt;
        try{ auto g=ebr.guard(); auto t=snap_load(r.db->mappings,g); pkt=etr_register_for(m,t?&*t:nullptr,r.xtr,nonce,periodic,records); }
        catch(const std::exception&x){
            // a failure is a truth, not a throw: e.g. a map-server published by another process whose key this
            // ETR process does not hold. The registrar records it and carries on with the others.
            ram->write_nowait("lisp","etr-register-sent",m.address,"{\"address\":"+q(m.address)+",\"sends\":"
                              +std::to_string(sends[m.address])+",\"error\":"+q(x.what())+"}");
            return;
        }
        if(pkt.empty())return;
        if(r.udp_port>0&&r.fd>=0){                 // from the registrar's control socket: notifies come back to it
            sockaddr_in to{}; to.sin_family=AF_INET; to.sin_port=htons((uint16_t)r.udp_port);
            if(inet_pton(AF_INET,m.address.c_str(),&to.sin_addr)==1)::sendto(r.fd,pkt.data(),pkt.size(),0,(sockaddr*)&to,sizeof to);
        }
        uint64_t n=++sends[m.address]; std::ostringstream o;
        o<<"{\"address\":"<<q(m.address)<<",\"sends\":"<<n<<",\"records\":"<<records<<",\"refresh\":"<<((m.refresh&&periodic)?"true":"false")
         <<",\"nonce\":"<<q([&]{std::ostringstream h;h<<std::hex<<nonce;return h.str();}())<<",\"bytes\":"<<pkt.size()<<"}";
        ram->write_nowait("lisp","etr-register-sent",m.address,o.str());
    }
    // Map-Notify verification and Map-Notify-Ack (control: lisp_process_map_notify / lisp_send_map_notify_ack).
    // lookup(cfg) finds the map-server for the notify's source; the key comes from the process-private snapshot.
    std::string etr_notify_ack(const std::vector<unsigned char>&raw,const std::string&src,
                               const std::function<bool(MapServerCfg&)>&lookup,std::vector<unsigned char>&ack){
        if(raw.size()<16)throw std::runtime_error("truncated map-notify");
        uint32_t f=be32(raw.data()); if((f>>28)!=4)throw std::runtime_error("not map-notify");
        int alg=raw[13]; size_t alen=be16(raw.data()+14);
        if(raw.size()<16+alen)throw std::runtime_error("truncated map-notify auth");
        std::string pw;
        if(alg!=0||alen!=0){
            MapServerCfg m; if(!lookup(m)||!ms_key(src,pw))return "unknown-map-server";
            if(!verify_register_hmac(raw,pw))return "auth-failed";
        }
        ack=raw; ack[0]=(unsigned char)((5u<<28)>>24); ack[1]=ack[2]=ack[3]=0;   // type 5, record count 0 (control)
        if(alen){
            std::fill(ack.begin()+16,ack.begin()+16+alen,0);
            unsigned char md[EVP_MAX_MD_SIZE]; unsigned int n=0;
            const EVP_MD*evp=alg==1?EVP_sha1():EVP_sha256();
            if(!HMAC(evp,pw.data(),(int)pw.size(),ack.data(),ack.size(),md,&n)||n!=alen)
                throw std::runtime_error("map-notify-ack hmac failed");
            std::copy(md,md+alen,ack.begin()+16);
        }
        return "good";
    }
    // The registrar's receiver: notifies arrive on the control socket; each is verified against the held map-server
    // view and acknowledged to the map-server's control port from the same socket. Writes only its own cells.
    void run_registrar_receiver(Registrar*rp){
        auto&r=*rp; std::map<std::string,std::pair<uint64_t,uint64_t>> counts;   // acks, rejected
        std::vector<unsigned char> buf(65536);
        while(!r.stop&&!stopping){
            sockaddr_in from{}; socklen_t fl=sizeof from;
            ssize_t n=::recvfrom(r.fd,buf.data(),buf.size(),0,(sockaddr*)&from,&fl);
            if(r.stop||stopping)break;
            if(n<16)continue;
            std::vector<unsigned char> raw(buf.begin(),buf.begin()+n); if((raw[0]>>4)!=4)continue;
            char ip[INET_ADDRSTRLEN]; inet_ntop(AF_INET,&from.sin_addr,ip,sizeof ip); std::string src(ip);
            std::vector<unsigned char> ack; std::string res;
            try{
                res=etr_notify_ack(raw,src,[&](MapServerCfg&m){
                    auto g=ebr.guard(); auto t=snap_load(r.msv->table,g); if(!t)return false;
                    auto it=t->find(src); if(it==t->end()||!it->second.active)return false; m=it->second; return true; },ack);
            } catch(const std::exception&){ res="malformed"; }
            auto&c=counts[src];
            if(res=="good"){
                sockaddr_in to{}; to.sin_family=AF_INET; to.sin_port=htons((uint16_t)r.udp_port); to.sin_addr=from.sin_addr;
                ::sendto(r.fd,ack.data(),ack.size(),0,(sockaddr*)&to,sizeof to); ++c.first;
            } else ++c.second;
            try{ ram->write_nowait("lisp","etr-registrar-notify",src,"{\"address\":"+q(src)+",\"acks\":"+std::to_string(c.first)
                                   +",\"rejected\":"+std::to_string(c.second)+"}"); }
            catch(const frogram::Unreachable&){ break; }
        }
    }
    void stop_registrar(){
        if(!registrar)return;
        registrar->stop=true;
        if(registrar->fd>=0&&registrar->port){      // wake the receiver with a datagram to itself (no polling)
            sockaddr_in me{}; me.sin_family=AF_INET; me.sin_port=htons(registrar->port); me.sin_addr.s_addr=htonl(INADDR_LOOPBACK);
            ::sendto(registrar->fd,"",0,0,(sockaddr*)&me,sizeof me);
        }
    }
    void run_registrar(Registrar*rp){
        auto&r=*rp; std::map<std::string,MapServerCfg> cfg; std::map<std::string,uint64_t> sends; uint64_t after=0;
        auto absorb=[&](const std::vector<frogram::Cell>&cells,std::vector<MapServerCfg>&fresh){
            for(auto&c:cells){ if(c.id>after)after=c.id; auto m=ms_from(c.bag,c.instance);
                cfg[c.instance]=m; if(m.active)fresh.push_back(m); }
        };
        try{
            std::vector<MapServerCfg> ignore; absorb(r.baseline,ignore); r.baseline.clear();
            ram->write_nowait("lisp","etr-registrar","state","{\"state\":\"running\"}");
            auto next=std::chrono::steady_clock::now()+std::chrono::duration<double>(r.first_s);
            const uint64_t base=0xaabbccdddfdfdf00ULL;
            while(!r.stop&&!stopping){
                double wait=std::chrono::duration<double>(next-std::chrono::steady_clock::now()).count();
                if(wait>0){
                    auto cells=ram->read("lisp","etr-map-server","",(int64_t)after,wait<3600?wait:3600);
                    if(r.stop||stopping)break;
                    std::vector<MapServerCfg> fresh; absorb(cells,fresh);
                    uint64_t nonce=base; for(auto&m:fresh)registrar_send(r,m,++nonce,false,sends);   // immediate, no refresh
                    continue;
                }
                uint64_t nonce=base; for(auto&kv:cfg)if(kv.second.active)registrar_send(r,kv.second,++nonce,true,sends);
                next+=std::chrono::duration_cast<std::chrono::steady_clock::duration>(std::chrono::duration<double>(r.interval_s));
            }
            ram->write_nowait("lisp","etr-registrar","state","{\"state\":\"stopped\"}");
        } catch(const frogram::Unreachable&){}
    }
    // ---- Native registration between Ribbit participants (v0.43): no Map-Register, no Map-Notify, no UDP ----------
    // An ETR's database mapping is its own truth under its identity (database-mapping@<name>|iid|group). The ETR
    // publishes its identity (etr-identity/<name>: xTR-ID). The Map-Server's governor participant holds every ETR's
    // database variable directly — no copy — applies site policy from its held site view, and publishes, as its own
    // truth, the governed registration (merge instance <prefix>|<xtr>), the governance mode, and its decision
    // (etr-decision@<governor>|iid|group / <etr>|<prefix>). Resolvers read governed truth as before.
    std::string etr_name,etr_xtr;
    std::string etr_tag()const{ return etr_name.empty()?std::string():"@"+etr_name; }
    std::string db_var(const std::string&iid,const std::string&group)const{ return ram_var("database-mapping"+etr_tag(),iid,group); }
    struct Governor{
        std::thread thread; std::atomic<bool> stop{false}; std::string name,iid,group;
        HeldResolverView*policy=nullptr; std::vector<std::thread> children;   // children: written only by the governor thread
    };
    std::unique_ptr<Governor> governor;
    // Read-time governance (v0.45): a native registration resolves only while a site in the held site policy
    // authorizes its prefix. Nothing is rewritten when a site changes; the answer is derived at read.
    bool site_authorizes(HeldResolverView&v,const std::string&prefix){
        Prefix rp=prefix_parse(prefix); bool ok=false;
        each_site(v.site_policy,[&](const auto&kv){
            if(ok||!kv.second.first)return;
            Prefix sp=prefix_parse(kv.first);
            if(sp.family==rp.family&&contains(sp,rp)&&(sp.bits==rp.bits||kv.second.second))ok=true; });
        return ok;
    }
    bool governor_authorizes(Governor&g,const Mapping&m){
        Prefix rp=prefix_parse(m.prefix); bool ok=false;
        each_site(g.policy->site_policy,[&](const auto&kv){
            if(ok||!kv.second.first)return;
            Prefix sp=prefix_parse(kv.first);
            if(sp.family==rp.family&&contains(sp,rp)&&(sp.bits==rp.bits||kv.second.second))ok=true; });
        return ok;
    }
    void govern_etr_entry(Governor&g,const std::string&etr,const std::string&xtr,const frogram::Cell&c){
        Mapping m=from_cell(c); m.iid=g.iid; m.group=g.group; m.prefix=c.instance; m.merge=true; m.xtr_id=xtr;
        std::string decision; Prefix rp=prefix_parse(m.prefix);
        auto regvar=ram_var_len("registration",g.iid,g.group,rp.bits); auto inst=m.prefix+"|"+xtr;
        if(!m.registered){
            m.last_registered=now_ms(); m.expires_at=0; ram->write("lisp",regvar,inst,bag_json(m)); decision="withdrawn";
        } else if(governor_authorizes(g,m)){
            m.last_registered=now_ms(); m.expires_at=0; m.live_key=xtr;   // alive only while the ETR's liveness cell is fresh
            ram->write("lisp",regvar,inst,bag_json(m));
            publish_known_length("registration",g.iid,g.group,rp.bits);
            ram->write("lisp",ram_var_len("registration-governance",g.iid,g.group,rp.bits),m.prefix,"{\"mode\":\"merge\",\"site_id\":\"\"}");
            decision="accepted";
        } else decision="rejected";
        ram->write("lisp","etr-decision@"+g.name+"|"+g.iid+"|"+g.group,etr+"|"+m.prefix,
                   "{\"decision\":"+q(decision)+",\"source_id\":"+std::to_string(c.id)+"}");
    }
    // one child per ETR: holds that ETR's database variable (the ETR's own truth) with a held read
    void govern_etr(Governor*gp,std::string etr,std::string xtr){
        auto&g=*gp; auto var=ram_var("database-mapping@"+etr,g.iid,g.group); uint64_t after=0;
        try{
            for(auto&c:ram->read("lisp",var)){ if(c.id>after)after=c.id; govern_etr_entry(g,etr,xtr,c); }
            while(!g.stop&&!stopping){
                auto cells=held_read(var,after); if(g.stop||stopping)break;
                for(auto&c:cells){ if(c.id>after)after=c.id;
                    try{ govern_etr_entry(g,etr,xtr,c); }
                    catch(const std::exception&x){
                        ram->write_nowait("lisp","etr-decision@"+g.name+"|"+g.iid+"|"+g.group,etr+"|"+c.instance,
                                          "{\"decision\":\"error\",\"reason\":"+q(x.what())+"}"); } }
            }
        } catch(const frogram::Unreachable&){}
    }
    // the governor thread holds the ETR identities; each new identity gets its own child (manifest pattern)
    void run_governor(Governor*gp){
        auto&g=*gp; std::set<std::string> known; uint64_t after=0;
        auto absorb=[&](const std::vector<frogram::Cell>&cells){
            for(auto&c:cells){ if(c.id>after)after=c.id; if(known.count(c.instance))continue;
                if(c.bag["xtr_id"].type!=Json::Str||c.bag["xtr_id"].s.empty())continue;
                known.insert(c.instance); auto xtr=c.bag["xtr_id"].s; auto etr=c.instance;
                g.children.emplace_back([this,gp,etr,xtr]{govern_etr(gp,etr,xtr);}); }
        };
        try{
            absorb(ram->read("lisp","etr-identity"));
            ram->write_nowait("lisp","ms-governor@"+g.name,"state","{\"state\":\"running\"}");
            while(!g.stop&&!stopping){
                auto cells=held_read("etr-identity",after); if(g.stop||stopping)break; absorb(cells);
            }
        } catch(const frogram::Unreachable&){}
        for(auto&t:g.children)if(t.joinable())t.join();
    }
    // ETR liveness (v0.44): one cell per ETR, etr-live/<xtr_id> {alive_until}. Death is derived from time at read;
    // nothing is written when an ETR dies.
    void apply_liveness(HeldLivenessView&v,const std::vector<frogram::Cell>&cells){
        auto g=ebr.guard(); auto old=snap_load(v.table,g); auto next=new LiveTable(old?*old:LiveTable{});
        for(auto&c:cells)if(c.bag["alive_until"].type==Json::Num)(*next)[c.instance]=(int64_t)c.bag["alive_until"].n;
        snap_store(v.table,next);
    }
    void watch_liveness(HeldLivenessView*vp,uint64_t after){
        applied("etr-live-applied","etr-live",after);
        while(!stopping)try{ auto cells=held_read("etr-live",after); if(stopping)break;
            for(auto&c:cells)if(c.id>after)after=c.id;
            if(!cells.empty()){apply_liveness(*vp,cells);applied("etr-live-applied","etr-live",after);} }
        catch(const frogram::Unreachable&){ if(stopping)break; return; }
    }
    HeldLivenessView& held_liveness(){
        if(held_liveness_view)return *held_liveness_view;
        held_liveness_view=std::make_unique<HeldLivenessView>(); auto&v=*held_liveness_view;
        auto cells=read_ram("etr-live"); for(auto&c:cells)if(c.id>v.watermark)v.watermark=c.id;
        apply_liveness(v,cells); auto after=v.watermark;
        v.thread=spawn([this,after]{watch_liveness(held_liveness_view.get(),after);}); return v;
    }
    struct Heartbeat{ std::thread thread; std::atomic<bool> stop{false}; double interval_s=20,lifetime_s=60; };
    std::unique_ptr<Heartbeat> heartbeat;
    // The ETR's heartbeat participant: writes its one liveness cell, then waits on its own control cell for the
    // interval (a bounded held read: the timeout is the clock, a stop wakes it at once).
    void run_heartbeat(Heartbeat*hp,std::string xtr,std::string name){
        auto&h=*hp; uint64_t after=0;
        try{
            for(auto&c:ram->read("lisp","etr-live-control",name))if(c.id>after)after=c.id;
            while(!h.stop&&!stopping){
                int64_t until=now_ms()+(int64_t)(h.lifetime_s*1000);
                ram->write("lisp","etr-live",xtr,"{\"etr\":"+q(name)+",\"alive_until\":"+std::to_string(until)+"}");
                auto cells=ram->read("lisp","etr-live-control",name,(int64_t)after,h.interval_s);
                for(auto&c:cells)if(c.id>after)after=c.id;
            }
        } catch(const frogram::Unreachable&){}
    }
    HeldMapResolverView& held_map_resolver_view(){
        if(held_map_resolvers)return *held_map_resolvers;
        held_map_resolvers=std::make_unique<HeldMapResolverView>();
        auto cells=ram->read("lisp","map-resolver");for(auto&c:cells)if(c.id>held_map_resolvers->watermark)held_map_resolvers->watermark=c.id;
        apply_map_resolver_changes(*held_map_resolvers,cells);auto after=held_map_resolvers->watermark;
        held_map_resolvers->thread=spawn([this,after]{watch_map_resolvers(held_map_resolvers.get(),after);});return *held_map_resolvers;
    }
    void materialize_length(HeldResolverView&v,const std::string&iid,const std::string&group,int bits){
        if(v.registration_started[bits])return;
        v.registration_started[bits]=true;
        auto regs=ram->read("lisp",ram_var_len("registration",iid,group,bits));auto govs=ram->read("lisp",ram_var_len("registration-governance",iid,group,bits));
        uint64_t ra=0,ga=0;for(auto&c:regs)if(c.id>ra)ra=c.id;for(auto&c:govs)if(c.id>ga)ga=c.id;
        auto*rs=new HeldSlot<Mapping>; auto*gs=new HeldSlot<GovPair>;
        hold_registrations(*rs,regs); hold_governance(*gs,govs);
        v.registrations[bits].store(rs,std::memory_order_release); v.governance[bits].store(gs,std::memory_order_release);
        auto av="resolver-applied|"+iid+"|"+group;
        v.registration_threads[bits]=spawn([this,rs,iid,group,bits,ra,av]{
            watch_held(rs,&Engine::hold_registrations,ram_var_len("registration",iid,group,bits),av,"registration|"+std::to_string(bits),ra);
        });
        v.governance_threads[bits]=spawn([this,gs,iid,group,bits,ga,av]{
            watch_held(gs,&Engine::hold_governance,ram_var_len("registration-governance",iid,group,bits),av,"governance|"+std::to_string(bits),ga);
        });
    }
    void materialize_site_length(HeldResolverView&v,const std::string&iid,const std::string&group,int bits){
        if(v.site_started[bits])return;
        v.site_started[bits]=true;auto cells=ram->read("lisp",ram_var_len("site",iid,group,bits));uint64_t after=0;for(auto&c:cells)if(c.id>after)after=c.id;
        apply_site_slot(v.site_policy[bits],cells);auto av="resolver-applied|"+iid+"|"+group;
        v.site_threads[bits]=spawn([this,&v,iid,group,bits,after,av]{watch_site_slot(&v.site_policy[bits],ram_var_len("site",iid,group,bits),av,"site|"+std::to_string(bits),after);});
    }
    void materialize_ddt_length(HeldResolverView&v,const std::string&iid,const std::string&group,int bits){
        if(v.ddt_started[bits])return;
        v.ddt_started[bits]=true;auto cells=ram->read("lisp",ram_var_len("ddt-delegation",iid,group,bits));uint64_t after=0;for(auto&c:cells)if(c.id>after)after=c.id;
        apply_mapping_slot(v.ddt_delegations[bits],cells);auto av="resolver-applied|"+iid+"|"+group;
        v.ddt_threads[bits]=spawn([this,&v,iid,group,bits,after,av]{
            watch_mapping_slot(&v.ddt_delegations[bits],ram_var_len("ddt-delegation",iid,group,bits),
                               av,"ddt|"+std::to_string(bits),after);
        });
    }
    void watch_manifest(HeldResolverView*vp,const std::string iid,const std::string group,uint64_t after){
        while(!stopping)try{auto cells=held_read("registration-lengths|"+iid+"|"+group,after);if(stopping)break;
            for(auto&c:cells){if(c.id>after)after=c.id;materialize_length(*vp,iid,group,checked_bits(c));}}
        catch(const frogram::Unreachable&){if(stopping)break;return;}
    }
    void watch_site_manifest(HeldResolverView*vp,const std::string iid,const std::string group,uint64_t after){
        while(!stopping)try{auto cells=held_read("site-lengths|"+iid+"|"+group,after);if(stopping)break;
            for(auto&c:cells){if(c.id>after)after=c.id;materialize_site_length(*vp,iid,group,checked_bits(c));}}
        catch(const frogram::Unreachable&){if(stopping)break;return;}
    }
    void watch_ddt_manifest(HeldResolverView*vp,const std::string iid,const std::string group,uint64_t after){
        while(!stopping)try{auto cells=held_read("ddt-delegation-lengths|"+iid+"|"+group,after);if(stopping)break;
            for(auto&c:cells){if(c.id>after)after=c.id;materialize_ddt_length(*vp,iid,group,checked_bits(c));}}
        catch(const frogram::Unreachable&){if(stopping)break;return;}
    }
    HeldResolverView& held_resolver(const std::string&iid,const std::string&group){
        if(view_owner!=this)return view_owner->held_resolver(iid,group);
        auto vk=view_key(iid,group);
        auto&slot=held_resolvers.b[std::hash<std::string>()(vk)%ResolverRegistry::B];
        for(;;){
            ResolverRegistry::Node*head=slot.load(std::memory_order_acquire);
            for(auto*n=head;n;n=n->next)if(n->key==vk){
                while(!n->ready.load(std::memory_order_acquire))std::this_thread::yield();   // its builder is arming it
                return *n->view;
            }
            auto*fresh=new ResolverRegistry::Node; fresh->key=vk; fresh->view=std::make_unique<HeldResolverView>(); fresh->next=head;
            if(!slot.compare_exchange_strong(head,fresh,std::memory_order_acq_rel)){ delete fresh; continue; }
            HeldResolverView&v=*fresh->view;          // this thread won the insert: it arms the view
            auto manifest=read_ram("registration-lengths|"+iid+"|"+group),site_manifest=read_ram("site-lengths|"+iid+"|"+group),ddt_manifest=read_ram("ddt-delegation-lengths|"+iid+"|"+group);
            for(auto&c:manifest)if(c.id>v.manifest_watermark)v.manifest_watermark=c.id;
            for(auto&c:site_manifest)if(c.id>v.site_manifest_watermark)v.site_manifest_watermark=c.id;
            for(auto&c:ddt_manifest)if(c.id>v.ddt_manifest_watermark)v.ddt_manifest_watermark=c.id;
            for(auto&c:manifest)materialize_length(v,iid,group,checked_bits(c));
            for(auto&c:site_manifest)materialize_site_length(v,iid,group,checked_bits(c));
            for(auto&c:ddt_manifest)materialize_ddt_length(v,iid,group,checked_bits(c));
            auto ma=v.manifest_watermark,sa=v.site_manifest_watermark,da=v.ddt_manifest_watermark;
            v.manifest_thread=spawn([this,&v,iid,group,ma]{watch_manifest(&v,iid,group,ma);});
            v.site_manifest_thread=spawn([this,&v,iid,group,sa]{watch_site_manifest(&v,iid,group,sa);});
            v.ddt_manifest_thread=spawn([this,&v,iid,group,da]{watch_ddt_manifest(&v,iid,group,da);});
            fresh->ready.store(1,std::memory_order_release);
            return v;
        }
    }
    template<class F> void each_mapping(const std::array<std::atomic<const MappingTable*>,129>&slots,F f){
        auto g=ebr.guard();
        for(auto&slot:slots){auto p=snap_load(slot,g);if(p)for(auto&kv:*p)f(kv);}
    }
    template<class F> void each_governance(const std::array<std::atomic<const GovernanceTable*>,129>&slots,F f){
        auto g=ebr.guard();
        for(auto&slot:slots){auto p=snap_load(slot,g);if(p)for(auto&kv:*p)f(kv);}
    }
    template<class F> void each_site(const std::array<std::atomic<const SiteTable*>,129>&slots,F f){
        auto g=ebr.guard();
        for(auto&slot:slots){auto p=snap_load(slot,g);if(p)for(auto&kv:*p)f(kv);}
    }
    std::string authoritative_site_for(const std::string&iid,const std::string&target,const std::string&group){
        Prefix tp=prefix_parse(target);
        std::string best; int bits=-1;
        if(ram){
            auto&v=held_resolver(iid,group);
            each_site(v.site_policy,[&](const auto&kv){
                if(!kv.second.first)return;
                Prefix sp=prefix_parse(kv.first);
                if(sp.family==tp.family&&contains(sp,tp)&&sp.bits>bits){best=kv.first;bits=sp.bits;}
            });
        } else {
            for(auto&kv:sites){
                auto bar=kv.first.find('|'),bar2=kv.first.rfind('|');
                if(bar==std::string::npos||bar2==bar)continue;
                if(kv.first.substr(0,bar)!=iid||kv.first.substr(bar2+1)!=group)continue;
                std::string p=kv.first.substr(bar+1,bar2-bar-1);
                Prefix sp=prefix_parse(p);
                if(sp.family==tp.family&&contains(sp,tp)&&sp.bits>bits){best=p;bits=sp.bits;}
            }
        }
        return best;
    }
    static std::string arg(const Json&a,const std::string&k,const std::string&d=""){
        auto&v=a[k];
        return v.type==Json::Str?v.s:d;
    }
    static std::vector<Rloc> rlocs(const Json&a){
        std::vector<Rloc> out;
        auto&x=(a["rloc_set"].type==Json::Arr?a["rloc_set"]:a["rlocs"]);
        if(x.type!=Json::Arr)return out;
        for(auto&v:x.a){
            Rloc r;
            if(v.type==Json::Str)r.address=v.s;
            else if(v.type==Json::Obj){
                r.address=arg(v,"address");
                if(v["priority"].type==Json::Num)r.priority=(int)v["priority"].n;
                if(v["weight"].type==Json::Num)r.weight=(int)v["weight"].n;
                if(v["lcaf"].type==Json::Str)r.lcaf=v["lcaf"].s;
            }
            if(!r.address.empty())out.push_back(r);
        }
        return out;
    }
    static std::string mapping_json(const Mapping&m){
        std::ostringstream o;
        o<<"{\"iid\":"<<q(m.iid)<<",\"prefix\":"<<q(m.prefix)<<",\"group\":"<<q(m.group)<<",\"rlocs\":[";
        for(size_t i=0;i<m.rlocs.size();++i){
            if(i)o<<',';
            o<<"{\"address\":"<<q(m.rlocs[i].address)<<",\"priority\":"<<m.rlocs[i].priority<<",\"weight\":"<<m.rlocs[i].weight
             <<(m.rlocs[i].lcaf.empty()?std::string():",\"lcaf\":"+q(m.rlocs[i].lcaf))<<'}';
        }
        o<<"],\"ttl\":"<<m.ttl
         <<",\"last_registered\":"<<m.last_registered
         <<",\"expires_at\":"<<m.expires_at
         <<",\"use_register_ttl\":"<<(m.use_register_ttl?"true":"false")
         <<",\"registered\":"<<(m.registered?"true":"false")
         <<",\"merge\":"<<(m.merge?"true":"false")
         <<",\"xtr_id\":"<<q(m.xtr_id)
         <<",\"site_id\":"<<q(m.site_id);
        if(!m.live_key.empty())o<<",\"live_key\":"<<q(m.live_key);
        return o.str()+"}";
    }
    static Mapping make_mapping(const Json&a){
        Mapping m;
        m.iid=arg(a,"iid","0");
        m.prefix=arg(a,"prefix");
        m.group=arg(a,"group");
        m.xtr_id=arg(a,"xtr_id");
        m.site_id=arg(a,"site_id");
        m.merge=(a["merge"].type==Json::Bool&&a["merge"].b);
        if(m.prefix.empty())throw std::runtime_error("no prefix supplied");
        prefix_parse(m.prefix);
        m.rlocs=rlocs(a);
        // A withdrawal (TTL 0) may carry no locators: lispers.net Map-Notifies a TTL-0 Map-Register with an empty
        // locator set and deregisters (compare_lispers.py, withdraw phase). Anything else needs a locator set.
        const bool withdrawal=a["ttl"].type==Json::Num&&a["ttl"].n==0;
        if(m.rlocs.empty()&&!withdrawal)throw std::runtime_error("no rloc-set supplied");
        for(auto&r:m.rlocs){
            unsigned char z[16];
            int fam=r.address.find(':')==std::string::npos?AF_INET:AF_INET6;
            if(inet_pton(fam,r.address.c_str(),z)!=1)throw std::runtime_error("bad address syntax in rloc-set");
        }
        return m;
    }
    std::string site_password(const std::string& iid, const std::string& prefix, const std::string& group, int keyid) {
        Prefix rp=prefix_parse(prefix);
        int best=-1;
        std::string pass;
        if(resident){                                    // the key rows, read where they are
            for(auto&c:ram->read(RESIDENT_SECRETS,"site-key")){
                if(arg(c.bag,"iid")!=iid||arg(c.bag,"group")!=group||(int)c.bag["key_id"].n!=keyid)continue;
                auto pw=arg(c.bag,"password"); if(pw.empty())continue;
                Prefix x=prefix_parse(arg(c.bag,"prefix"));
                if(x.family==rp.family&&contains(x,rp)&&x.bits>best){ pass=pw; best=x.bits; }
            }
            return pass;
        }
        for(auto&kv:sites){
            auto b1=kv.first.find('|'),b2=kv.first.find('|',b1+1);
            if(kv.first.substr(0,b1)!=iid||kv.first.substr(b2+1)!=group)continue;
            auto sp=kv.first.substr(b1+1,b2-b1-1);
            Prefix x=prefix_parse(sp);
            auto ki=site_keys.find(kv.first+"|"+std::to_string(keyid));
            if(ki!=site_keys.end()&&contains(x,rp)&&x.bits>best){
                pass=ki->second;
                best=x.bits;
            }
        }
        return pass;
    }
    std::string wire_registration_json(const WireReg&w,const std::string&source,bool validate_only=false){
        std::ostringstream j;
        j<<"{\"iid\":"<<q(w.iid)<<",\"prefix\":"<<q(w.prefix)
         <<",\"group\":"<<q(w.group)<<",\"ttl\":"<<w.ttl
         <<",\"use_register_ttl\":"<<(w.use_ttl?"true":"false")
         <<",\"refresh\":"<<(w.refresh?"true":"false")
         <<",\"merge\":"<<(w.merge?"true":"false")
         <<",\"xtr_id\":"<<q(w.xtr_id)                      // [WIRE_XTR_ID_V1] merge keys each xTR's own part by it
         <<",\"source\":"<<q(source)
         <<",\"validate_only\":"<<(validate_only?"true":"false")
         <<",\"rloc_set\":[";
        for(size_t i=0;i<w.rlocs.size();++i){
            if(i)j<<',';
            j<<"{\"address\":"<<q(w.rlocs[i].address)<<",\"priority\":"<<w.rlocs[i].priority<<",\"weight\":"<<w.rlocs[i].weight
             <<(w.rlocs[i].lcaf.empty()?std::string():",\"lcaf\":"+q(w.rlocs[i].lcaf))<<'}';
        }
        j<<"]}"; return j.str();
    }
    // [KEYED_SITE_REQUIRES_AUTH_V1] Found by the acceptance test (L3.5): a Map-Register with NO authentication
    // (alg-id 0) for a prefix whose site has a key was accepted -- verification only ran when the packet said it was
    // authenticated. A site with any key accepts only authenticated registers.
    bool packet_needs_auth(const std::vector<WireReg>&records){
        if(resident){
            auto keys=ram->read(RESIDENT_SECRETS,"site-key");
            for(auto&r:records){
                Prefix rp=prefix_parse(r.prefix);
                for(auto&c:keys){
                    if(arg(c.bag,"password").empty()||arg(c.bag,"iid")!=r.iid)continue;
                    Prefix sp=prefix_parse(arg(c.bag,"prefix"));
                    if(sp.family==rp.family&&contains(sp,rp))return true;
                }
            }
            return false;
        }
        for(auto&r:records){
            Prefix rp=prefix_parse(r.prefix);
            for(auto&kv:site_keys){
                auto b1=kv.first.find('|'),b2=kv.first.find('|',b1+1),b3=kv.first.rfind('|');
                if(b1==std::string::npos||b2==std::string::npos||b3<=b2)continue;
                if(kv.first.substr(0,b1)!=r.iid)continue;
                Prefix sp=prefix_parse(kv.first.substr(b1+1,b2-b1-1));
                if(sp.family==rp.family&&contains(sp,rp))return true;
            }
        }
        return false;
    }
    static int register_eid_afi(const std::vector<unsigned char>&raw){
        if(raw.size()<16)return 0;
        size_t alen=be16(raw.data()+14), o=16+alen+10;
        return raw.size()>=o+2?be16(raw.data()+o):0;
    }
    // Decrypt an encrypted Map-Register in place (lisp_decrypt_map_register); unencrypted ones pass through.
    std::vector<unsigned char> decrypt_register(const std::vector<unsigned char>&raw){
        if(raw.size()<4)return raw;
        uint32_t h=be32(raw.data());
        if(!((h>>13)&1))return raw;
        int kid=(h>>14)&7; std::string k;
        if(resident){ auto c=ram->read(RESIDENT_SECRETS,"ms-encryption-key",std::to_string(kid)); if(!c.empty())k=arg(c.front().bag,"key"); }
        else { auto it=ms_encryption_keys.find(kid); if(it!=ms_encryption_keys.end())k=it->second; }
        if(k.empty())throw std::runtime_error("encrypted Map-Register with key-id "+std::to_string(kid)+": no such key");
        if(k.size()<32)k=std::string(32-k.size(),'0')+k;
        // OpenSSL's ChaCha20 takes the 16-byte IETF IV (32-bit counter, 96-bit nonce); Bernstein's 64-bit counter and
        // 64-bit nonce map onto it as counter-low, counter-high (0), then the nonce.
        unsigned char iv[16]={0}; std::memcpy(iv+8,"00000000",8);
        std::vector<unsigned char> out(raw.begin(),raw.begin()+4); out.resize(raw.size());
        EVP_CIPHER_CTX*c=EVP_CIPHER_CTX_new(); int n=0;
        bool ok=c&&EVP_DecryptInit_ex(c,EVP_chacha20(),nullptr,(const unsigned char*)k.data(),iv)==1
               &&EVP_DecryptUpdate(c,out.data()+4,&n,raw.data()+4,(int)raw.size()-4)==1;
        EVP_CIPHER_CTX_free(c);
        if(!ok||n!=(int)raw.size()-4)throw std::runtime_error("Map-Register decryption failed");
        return out;
    }
    std::string packet_site_password(const std::vector<WireReg>&records,int keyid){
        if(records.empty())return std::string();
        auto grp=[&](const WireReg&r){ return r.group.empty()?std::string():site_group_for(r.iid,r.group); };   // [MULTICAST_SG_V1]
        auto pass=site_password(records.front().iid,records.front().prefix,grp(records.front()),keyid);
        if(pass.empty())return std::string();
        for(size_t i=1;i<records.size();++i)if(site_password(records[i].iid,records[i].prefix,grp(records[i]),keyid)!=pass)return std::string();
        return pass;
    }
    // A multi-record Map-Register is authorized ONCE, in the validation pass, against one site-policy snapshot. The
    // apply pass must not re-read site policy: a site published between the passes let the apply pass reject a
    // later record after an earlier one was applied (v0.40a: 8 partial applications in one 600-round FNW1 run).
    bool apply_preauthorized=false;       // request thread only; never settable from an operation
    std::string apply_wire_registrations(const std::vector<WireReg>&records,const std::string&source){
        for(auto&w:records){
            auto r=call("registration.put",Json::parse(wire_registration_json(w,source,true)));
            if(r!=q("good"))return r;
        }
        struct Reset{ bool&f; ~Reset(){ f=false; } } reset{apply_preauthorized};
        apply_preauthorized=true;
        for(auto&w:records){
            auto r=call("registration.put",Json::parse(wire_registration_json(w,source,false)));
            if(r!=q("good"))return r;
        }
        return q("good");
    }
    void publish_known_length(const std::string& kind,const std::string& iid,const std::string& group,int bits){
        if(!ram)return;
        std::string variable=kind+"-lengths|"+iid+"|"+group;
        std::string instance=std::to_string(bits);
        if(ram->read("lisp",variable,instance).empty())
            ram->write("lisp",variable,instance,"{\"bits\":"+std::to_string(bits)+"}");
    }
    std::pair<std::string,std::string> governance_for(const std::string& iid,const std::string& prefix,const std::string& group){
        auto it=registration_governance.find(key(iid,prefix,group));
        return it==registration_governance.end()?std::make_pair(std::string(),std::string()):it->second;
    }
    void govern(const Mapping&m){
        std::string mode=m.merge?"merge":"direct";
        if(ram){
            Prefix rp=prefix_parse(m.prefix);
            ram->write("lisp",ram_var_len("registration-governance",m.iid,m.group,rp.bits),m.prefix,
                "{\"mode\":"+q(mode)+",\"site_id\":"+q(m.site_id)+"}");
        } else registration_governance[key(m.iid,m.prefix,m.group)]={mode,m.site_id};
    }
    static bool governed_in(const Mapping&m,const std::pair<std::string,std::string>&g){
        if(g.first.empty())return true;
        if(g.first=="direct")return !m.merge;
        return m.merge && (g.second.empty() || m.site_id==g.second);
    }
    public:
    // [BOUNDARY_NEVER_WAITS_V1] A site's key is this process's secret: site.add keeps it in site_keys and never writes
    // it into memory. An Engine that verifies Map-Registers for a site another Engine of the same process added is
    // given the key here -- the same arguments as site.add / site.delete, nothing written anywhere.
    void share_views_of(Engine&owner){ view_owner=owner.view_owner; }
    static std::string participant(){ return participant_id(); }      // this process's identity in the region
    // [SITE_OPTIONS_V1] The Map-Server's answer to a Map-Request (plain or from an ECM), with the covering site's
    // options applied as lispers.net's lisp_ms_process_map_request applies them: force-ttl (seconds encoding) on
    // positive and negative answers; not-registered-yet -> the requested EID, action 7; a site that does not force
    // proxy replies answers a PITR with drop (pitr-proxy-reply-drop) or with its proxy-reply-action (drop / native-
    // forward), TTL 1440, locators kept; echo-nonce-capable sets the E bit.
    std::string answer_request(const std::vector<unsigned char>&req,bool v6){
        sg_padded()=false;
        std::string wiid,wgroup; auto plain=strip_iid_request(req,wiid,&wgroup);   // [LCAF_INSTANCE_ID_V1] [MULTICAST_SG_V1]
        auto w=v6?decode_request6(plain):decode_request4(plain); w.iid=wiid; w.group=wgroup;
        const bool pitr=req.size()>1&&(req[1]&0x80);                     // the Map-Request's P bit
        std::string sgroup=w.group.empty()?std::string():site_group_for(w.iid,w.group);
        std::string site=(w.group.empty()||!sgroup.empty())?authoritative_site_for(w.iid,w.target,sgroup):std::string(); SitePol pol; bool have=false;
        if(!site.empty()&&ram){ auto&v=held_resolver(w.iid,sgroup); each_site(v.site_policy,[&](const auto&kv){ if(kv.first==site){pol=kv.second;have=true;} }); }
        auto out=[&](std::vector<unsigned char> b){ if(have&&pol.echo&&!b.empty())b[0]|=0x04;
            return q(hex(w.group.empty()?add_iid_to_reply(b,w.iid):add_sg_to_reply(b,w.iid,w.group))); };
        Json qa=Json::parse("{\"iid\":"+q(w.iid)+",\"prefix\":"+q(w.target)+",\"group\":"+q(w.group)+"}");
        auto r=call("resolution.get",qa);
        if(r=="[]"){
            if(site.empty())return out(encode_negative_reply(w.nonce,w.target,15));
            uint32_t ttl=(have&&pol.force_ttl>=0)?(0x80000000u|(uint32_t)pol.force_ttl):1;
            if(have&&pol.pra=="not-registered-yet")return out(encode_negative_reply(w.nonce,w.target,ttl,7));
            // [NEGATIVE_PREFIX_V1] lispers.net (lisp_ms_process_map_request): an accept-more-specifics site answers for
            // the requested EID itself; a site that does not accept more-specifics answers with its own prefix.
            // Found on hardware (L1.5 evidence: lispers.net 198.18.250.9/32, Ribbit 198.18.0.0/16; the test was loose).
            return out(encode_negative_reply(w.nonce,(have&&pol.second)?w.target:site,ttl));
        }
        auto jr=Json::parse(r); Mapping m=make_mapping(jr);
        // [PROXY_REPLY_TTL_V1] a proxy Map-Reply carries TTL 1440 whatever the registration's TTL (lispers.net:
        // "ttl = 1440" when it proxy-replies; acceptance L1.14 found Ribbit returning the registration's TTL)
        m.ttl=1440;
        int action=0;
        if(have&&pol.force_proxy&&!pol.policy.empty()){                   // [MS_POLICY_V1]
            auto pc=ram->read("lisp","ms-policy",pol.policy);
            if(!pc.empty()){
                const Json&P=pc.front().bag; bool match=false;
                std::string srloc=request_itr_rloc(plain);
                for(auto&c:P["match"].a){
                    auto inside=[&](const char*k,const std::string&t){
                        if(c[k].type!=Json::Str||t.empty())return true;
                        Prefix cp=prefix_parse(c[k].s), tp=prefix_parse(t); return cp.family==tp.family&&contains(cp,tp); };
                    if(!inside("destination_eid",w.target))continue;
                    if(!inside("source_rloc",srloc))continue;
                    match=true; break;           // (a request carries no source EID here: source_eid clauses pass)
                }
                if(!match){ action=4; m.rlocs.clear(); }                   // no clause matched: implied drop
                else {
                    if(P["set_record_ttl"].type==Json::Num)m.ttl=(uint32_t)P["set_record_ttl"].n;
                    if(P["set_action"].type==Json::Str&&P["set_action"].s=="drop"){ action=4; m.rlocs.clear(); }
                    else if(P["set_rloc_address"].type==Json::Str){
                        // [NAMED_LOCATORS_V1] lispers.net's policy locator: priority 255, weight 0, m-priority 255,
                        // m-weight 0, R bit; with named objects, an AFI-list LCAF of the address and each object
                        Rloc x; x.address=P["set_rloc_address"].s; x.priority=255; x.weight=0;
                        std::vector<unsigned char> inner;
                        for(auto kn:{std::make_pair("geo","set_geo_name"),std::make_pair("elp","set_elp_name"),std::make_pair("rle","set_rle_name"),std::make_pair("json","set_json_name")})
                            if(P[kn.second].type==Json::Str){ auto oc=ram->read("lisp","ms-named",std::string(kn.first)+"|"+P[kn.second].s);
                                if(!oc.empty()){ auto ob=unhex(oc.front().bag["lcaf"].s); inner.insert(inner.end(),ob.begin(),ob.end()); } }
                        unsigned char ad[4]; bool v4=inet_pton(AF_INET,x.address.c_str(),ad)==1;
                        std::vector<unsigned char> rec={0xff,0,0xff,0,0,1};
                        if(!inner.empty()&&v4){
                            put16(rec,16387); rec.push_back(0); rec.push_back(0); rec.push_back(1); rec.push_back(0); put16(rec,(uint16_t)(6+inner.size()));
                            put16(rec,1); rec.insert(rec.end(),ad,ad+4); rec.insert(rec.end(),inner.begin(),inner.end());
                        } else if(v4){ put16(rec,1); rec.insert(rec.end(),ad,ad+4); }
                        if(v4)x.lcaf=hex(rec);
                        m.rlocs={x};
                    }
                }
            }
        }
        if(have&&!pol.force_proxy){
            if(pitr&&pol.pitr_drop){ action=3; m.ttl=1440; }
            else if(!pol.pra.empty()){ action=pol.pra=="drop"?3:1; m.ttl=1440; }
        }
        if(have&&pol.force_ttl>=0)m.ttl=0x80000000u|(uint32_t)pol.force_ttl;
        return out(v6?encode_reply6(w.nonce,m,action):encode_reply4(w.nonce,m,action));
    }
    // The first ITR-RLOC of a (plain) Map-Request, as text; "" if none can be read.
    static std::string request_itr_rloc(const std::vector<unsigned char>&m){
        if(m.size()<14)return "";
        size_t o=12; o+=2+addr_len(be16(m.data()+o));
        if(m.size()<o+2)return "";
        uint16_t afi=be16(m.data()+o); size_t al=addr_len(afi);
        if(!al||m.size()<o+2+al)return "";
        return format_addr(afi,m.data()+o+2);
    }
    // [MULTICAST_SG_V1] The configured site group-prefix that covers an (S,G)'s group (the longest), "" if none.
    // A group is an address prefix when written "addr/len" (it must then be valid), otherwise a plain name (the API's
    // group namespace), whose sites are its own.
    std::string site_group_for(const std::string&iid,const std::string&group){
        if(!ram)return "";
        if(group.find('/')==std::string::npos)return group;          // a name: its sites are its own (exact)
        Prefix gp=prefix_parse(group);                                   // "addr/len": must be a valid prefix
        std::string best; int bits=-1;
        for(auto&c:ram->read("lisp","site-groups|"+iid)){
            Prefix sp=prefix_parse(c.instance);
            if(sp.family==gp.family&&contains(sp,gp)&&sp.bits>bits){best=c.instance;bits=sp.bits;}
        }
        return best;
    }
    // The site prefixes this participant holds for an instance (held view: no round trip).
    std::vector<std::string> site_prefixes(const std::string&iid){
        std::vector<std::string> out; auto&v=held_resolver(iid,"");
        each_site(v.site_policy,[&](const auto&kv){ out.push_back(kv.first); });
        return out;
    }
    // Plain cells in the LISP service's own variables (its capability tuple): the participant's own truth, written
    // and read as any cell is. write_cell returns when written; read_cells is a plain read, never a held one.
    uint64_t write_cell(const std::string&variable,const std::string&instance,const std::string&bag){
        if(!ram)throw std::runtime_error("write_cell requires the shared-memory backend");
        return ram->write("lisp",variable,instance,bag);
    }
    std::vector<frogram::Cell> read_cells(const std::string&variable){
        if(!ram)throw std::runtime_error("read_cells requires the shared-memory backend");
        return ram->read("lisp",variable);
    }
    // [FAIL_ONCE_LOUDLY_V1] An exception on one of this Engine's own threads (a held view's watcher, the registrar,
    // the heartbeat, the governor) used to escape the thread and std::terminate the process: one lost connection
    // became 49 "adapter exited" errors with the cause buried. Now the first such failure is recorded, the thread
    // ends, and the next operation on the Engine throws it -- with the cause -- instead of answering from a view
    // that has stopped. ribbit-lisp then reports it once and exits.
    std::string failure(){ auto*p=failed_.load(std::memory_order_acquire); return p?*p:std::string(); }
    void adopt_site_key(const Json&a){
        auto iid=arg(a,"iid","0"),p=arg(a,"prefix"),g=arg(a,"group"); prefix_parse(p);
        int kid=a["key_id"].type==Json::Num?(int)a["key_id"].n:0; auto pass=arg(a,"password");
        bool ams=(a["accept_more_specifics"].type==Json::Bool&&a["accept_more_specifics"].b);
        sites[key(iid,p,g)]=ams;                          // site_password() finds the key through the local site list
        if(!pass.empty())site_keys[key(iid,p,g)+"|"+std::to_string(kid)]=pass;
    }
    void forget_site_keys(const Json&a){
        auto sk=key(arg(a,"iid","0"),arg(a,"prefix"),arg(a,"group"));
        sites.erase(sk);
        for(auto it=site_keys.begin();it!=site_keys.end();)if(it->first.rfind(sk+"|",0)==0)it=site_keys.erase(it); else ++it;
    }
    Engine()=default;
    explicit Engine(std::unique_ptr<frogram::MemoryApi> memory_itself){ ram=std::move(memory_itself); resident=true; }
    Engine(const std::string& host,int port,const std::string& api="/ram.php"){
        session.reset(new frogram::Session(host,port,api));
        ram.reset(new frogram::Memory(*session,api));
        ram_api=api;
    }
    // [VENDOR_API_V1] John 2026-09-26: "take advantage of the fact that you own api.php and make the compound
    // operations happen in one call ... throughout". A mutating LISP operation goes to the LISP region's own RAM
    // interface as ONE request (op=lisp); the RAM host runs this same Engine code against its memory, where every
    // round trip the operation makes is local to the host. The answer is the operation's own result, verbatim.
    // args_json is the operation's arguments as the caller wrote them.
    // The RAM host's version (op=version); a host from before v0.53 has no such op and says so.
    std::string ram_version(){
        if(!session)return "(no RAM host: in-process memory)";
        try{ frogram::Json r=session->call("GET",ram_api+"?op=version","");
             return r["version"].type==frogram::Json::Str?r["version"].s:std::string("(host answered without a version)"); }
        catch(const std::exception&x){ return std::string("(host older than v0.53: ")+x.what()+")"; }
    }
    std::string remote_call(const std::string& op,const std::string& args_json){
        if(!session)throw std::runtime_error("remote_call: no RAM session");
        if(auto*p=failed_.load(std::memory_order_acquire))throw frogram::Unreachable(*p);
        frogram::Json r=session->call("POST",ram_api+"?op=lisp","{\"operation\":"+frogram::Json::quote(op)+",\"args\":"+args_json+"}");
        if(r["ok"].type!=frogram::Json::Bool||!r["ok"].b)throw std::runtime_error(r["error"].type==frogram::Json::Str?r["error"].s:std::string("lisp op refused"));
        std::vector<std::pair<std::string,uint64_t>> written;          // [READ_YOUR_OWN_WRITE_V1]
        std::set<std::pair<std::string,uint64_t>> held;                 // [OWN_WRITES_HELD_V1]
        if(r["cells_json"].type==frogram::Json::Str&&!r["cells_json"].s.empty())held=hold_own_cells(frogram::Json::parse(r["cells_json"].s));
        for(auto&e:r["written"].a)if(e.type==frogram::Json::Arr&&e.a.size()==2&&!held.count({e.a[0].s,uint64_t(e.a[1].n)}))
            written.emplace_back(e.a[0].s,uint64_t(e.a[1].n));
        await_own(written);
        return r["result_json"].s;
    }
    ~Engine(){
        stopping=true; stop_registrar(); if(governor)governor->stop=true; if(heartbeat)heartbeat->stop=true; if(session)session->shutdown();
        if(heartbeat&&heartbeat->thread.joinable())heartbeat->thread.join();
        if(held_liveness_view&&held_liveness_view->thread.joinable())held_liveness_view->thread.join();
        if(governor&&governor->thread.joinable())governor->thread.join();
        if(registrar&&registrar->thread.joinable())registrar->thread.join();
        if(registrar&&registrar->receiver.joinable())registrar->receiver.join();
        if(registrar&&registrar->fd>=0)::close(registrar->fd);
        if(held_map_resolvers&&held_map_resolvers->thread.joinable())held_map_resolvers->thread.join();
        if(held_map_servers&&held_map_servers->thread.joinable())held_map_servers->thread.join();
        for(auto&x:held_databases)if(x.second->thread.joinable())x.second->thread.join();
        for(auto&x:held_map_caches)if(x.second->manifest_thread.joinable())x.second->manifest_thread.join();
        held_resolvers.each([&](HeldResolverView&v){
            if(v.manifest_thread.joinable())v.manifest_thread.join();
            if(v.site_manifest_thread.joinable())v.site_manifest_thread.join();
            if(v.ddt_manifest_thread.joinable())v.ddt_manifest_thread.join();
        });
        for(auto&x:held_map_caches)for(auto&t:x.second->threads)if(t.joinable())t.join();
        held_resolvers.each([&](HeldResolverView&v){
            for(auto&t:v.registration_threads)if(t.joinable())t.join();
            for(auto&t:v.governance_threads)if(t.joinable())t.join();
            for(auto&t:v.site_threads)if(t.joinable())t.join();
            for(auto&t:v.ddt_threads)if(t.joinable())t.join();
        });
        if(held_map_resolvers)delete held_map_resolvers->active.exchange(nullptr);
        if(held_map_servers)delete held_map_servers->table.exchange(nullptr);
        if(held_liveness_view)delete held_liveness_view->table.exchange(nullptr);
        delete ms_keys.exchange(nullptr);
        for(auto&x:held_databases)delete x.second->mappings.exchange(nullptr);
        for(auto&x:held_map_caches)for(auto&s:x.second->mappings)delete s.exchange(nullptr);
        held_resolvers.each([&](HeldResolverView&v){
            for(auto&s:v.registrations)delete s.exchange(nullptr);
            for(auto&s:v.governance)delete s.exchange(nullptr);            // a held table deletes its current tuples
            for(auto&s:v.site_policy)delete s.exchange(nullptr);
            for(auto&s:v.ddt_delegations)delete s.exchange(nullptr);
        });
        ebr.drain();
    }
    // [READ_YOUR_OWN_WRITE_V1] for EVERY operation: the cells it wrote (on this thread) are logged, and it returns only
    // when this participant's own views have applied them -- local operations as well as hosted ones. The log nests: a
    // caller already logging (the RAM host's executor, an outer operation) receives this operation's writes too.
    std::string call(const std::string&op,const Json&a){
        if(auto*p=failed_.load(std::memory_order_acquire))throw frogram::Unreachable(*p);
        auto*outer=frogram::current_write_log();
        std::vector<std::pair<std::string,uint64_t>> mine;
        frogram::log_writes_to(&mine);
        std::string r;
        try{ r=dispatch(op,a); } catch(...){ frogram::log_writes_to(outer); if(outer)outer->insert(outer->end(),mine.begin(),mine.end()); throw; }
        frogram::log_writes_to(outer);
        if(outer)outer->insert(outer->end(),mine.begin(),mine.end());
        if(!mine.empty()&&!resident)await_own(mine);   // resident: the write IS the row; there is no view to wait for
        return r;
    }
    std::string dispatch(const std::string&op,const Json&a){
        if(op=="ms.named_locator"){                                         // [NAMED_LOCATORS_V1]
            // lispers.net's geo-coordinates / explicit-locator-path / replication-list-entry / json objects, encoded as
            // its lisp_geo.encode_geo / rloc-record encode_lcaf write them (acceptance L2.18 compares the bytes)
            if(!ram)throw std::runtime_error("ms.named_locator requires the shared-memory backend");
            auto kind=arg(a,"kind"),name=arg(a,"name"); if(name.empty())throw std::runtime_error("no name");
            std::vector<unsigned char> b; auto p16=[&](uint16_t v){ put16(b,v); };
            auto lcaf=[&](int type,size_t len){ p16(16387); b.push_back(0); b.push_back(0); b.push_back((unsigned char)type); b.push_back(0); p16((uint16_t)len); };
            if(kind=="geo"){
                auto t=arg(a,"geo_tag"); std::vector<std::string> f; std::string cur;
                std::string body=t.substr(t.find(']')==std::string::npos?0:t.find(']')+1); int radius=0;
                auto sl=body.find('/'); if(sl!=std::string::npos){ radius=std::stoi(body.substr(sl+1)); body=body.substr(0,sl); }
                for(char ch:body){ if(ch=='-'){f.push_back(cur);cur.clear();} else cur+=ch; } f.push_back(cur);
                if(f.size()<8)throw std::runtime_error("geo_tag: d-m-s-N|S-d-m-s-E|W[-altitude][/km]");
                int lat=std::stoi(f[0]),lon=std::stoi(f[4]); bool north=f[3]=="N",east=f[7]=="E";
                uint32_t lat_ms=(std::stoi(f[1])*60+std::stoi(f[2]))*1000, lon_ms=(std::stoi(f[5])*60+std::stoi(f[6]))*1000;
                bool alt=f.size()>8; uint32_t altitude=alt?(uint32_t)std::stol(f[8]):0;
                unsigned char flags=(north?0x40:0)|(east?0x20:0)|(alt?0x10:0)|(radius?0x06:0);
                lcaf(5,22); b.push_back(flags); b.push_back(0); p16(0); b.push_back((unsigned char)lat); b.push_back((unsigned char)(lat_ms>>16)); p16((uint16_t)(lat_ms&0xffff));
                b.push_back((unsigned char)lon); b.push_back((unsigned char)(lon_ms>>16)); p16((uint16_t)(lon_ms&0xffff)); put32(b,altitude); p16((uint16_t)radius); p16(0); p16(0);
            } else if(kind=="elp"||kind=="rle"){
                std::vector<unsigned char> nodes;
                for(auto&n:a["nodes"].a){
                    unsigned char ad[4]; if(inet_pton(AF_INET,arg(n,"address").c_str(),ad)!=1)throw std::runtime_error("node address must be IPv4");
                    if(kind=="elp"){ auto fl=[&](const char*k){ return n[k].type==Json::Bool&&n[k].b; };
                        uint16_t f=(fl("eid")?4:0)|(fl("probe")?2:0)|(fl("strict")?1:0); nodes.push_back(f>>8); nodes.push_back(f&255); }
                    else { nodes.push_back(0); nodes.push_back(0); nodes.push_back(0); nodes.push_back(n["level"].type==Json::Num?(unsigned char)n["level"].n:0); }
                    nodes.push_back(0); nodes.push_back(1); nodes.insert(nodes.end(),ad,ad+4);
                }
                lcaf(kind=="elp"?10:13,nodes.size()); b.insert(b.end(),nodes.begin(),nodes.end());
            } else if(kind=="json"){
                auto j=arg(a,"json_string");
                lcaf(14,j.size()+6); p16((uint16_t)j.size()); b.insert(b.end(),j.begin(),j.end()); p16(0);   // lispers.net's length: +6 for +4 bytes
            } else throw std::runtime_error("kind: geo, elp, rle or json");
            ram->write("lisp","ms-named",kind+"|"+name,"{\"lcaf\":"+q(hex(b))+"}");
            return q("good");
        }
        if(op=="ms.policy"){                                                // [MS_POLICY_V1]
            if(!ram)throw std::runtime_error("ms.policy requires the shared-memory backend");
            auto name=arg(a,"name"); if(name.empty())throw std::runtime_error("no policy name");
            std::string m="[";
            for(auto&c:a["match"].a){ m+=(m.size()>1?",":"")+std::string("{");
                bool f=true; for(const char*k:{"source_eid","destination_eid","source_rloc"})
                    if(c[k].type==Json::Str){ prefix_parse(c[k].s); m+=(f?"":",")+std::string("\"")+k+"\":"+q(c[k].s); f=false; }
                m+="}"; }
            m+="]";
            std::string b="{\"name\":"+q(name)+",\"match\":"+m;
            auto act=arg(a,"set_action"); if(!act.empty()){ if(act!="drop"&&act!="process")throw std::runtime_error("set_action: process or drop"); b+=",\"set_action\":"+q(act); }
            if(a["set_record_ttl"].type==Json::Num)b+=",\"set_record_ttl\":"+std::to_string((uint32_t)a["set_record_ttl"].n);
            auto sra=arg(a,"set_rloc_address"); if(!sra.empty()){ prefix_parse(sra); b+=",\"set_rloc_address\":"+q(sra); }
            for(const char*k:{"set_geo_name","set_elp_name","set_rle_name","set_json_name"}){ auto v=arg(a,k); if(!v.empty())b+=std::string(",\"")+k+"\":"+q(v); }
            ram->write("lisp","ms-policy",name,b+"}");
            return q("good");
        }
        if(op=="ms.encryption_key"){                                        // [MAP_REGISTER_ENCRYPTION_V1]
            int kid=a["key_id"].type==Json::Num?(int)a["key_id"].n:0; auto k=arg(a,"key");
            if(kid<0||kid>7)throw std::runtime_error("encryption key-id is 0-7");
            if(resident)ram->write(RESIDENT_SECRETS,"ms-encryption-key",std::to_string(kid),"{\"key\":"+q(k)+"}");
            else if(k.empty())ms_encryption_keys.erase(kid); else ms_encryption_keys[kid]=k;
            return q("good");
        }
        if(op=="wire.auth_verify"){
            return verify_register_hmac(unhex(arg(a,"hex")),arg(a,"password"))?"true":"false";
        }
        if(op=="wire.register6"){
            auto raw=decrypt_register(unhex(arg(a,"hex"))); std::string wiid; auto records=decode_register6(strip_iid_register(raw,wiid));
            for(auto&w:records)w.iid=wiid;                          // [LCAF_INSTANCE_ID_V1]
            int alg=raw.size()>13?raw[13]:0,keyid=raw.size()>12?raw[12]:0;
            if(alg==0&&packet_needs_auth(records))return q("auth-failed");
            if(alg!=0){ auto pass=packet_site_password(records,keyid); if(pass.empty()||!verify_register_hmac(raw,pass))return q("auth-failed"); }
            return apply_wire_registrations(records,arg(a,"source"));
        }
        if(op=="wire.request6"||op=="wire.request4")return answer_request(unhex(arg(a,"hex")),op=="wire.request6");
        if(op=="wire.register4_notify"||op=="wire.register_notify"){
            // [REGISTER_ANY_AFI_V1] wire.register_notify: the Map-Server front's register path for EITHER address
            // family -- the acceptance test (L1.8) found the front refusing IPv6 EID registers, because it only ever
            // called the IPv4 decoder. The records' AFI chooses the decoder; auth, apply and Map-Notify are the same.
            auto raw=decrypt_register(unhex(arg(a,"hex")));                // [MAP_REGISTER_ENCRYPTION_V1]
            std::string wiid,wgroup; auto plain=strip_iid_register(raw,wiid,&wgroup);   // [LCAF_INSTANCE_ID_V1] [MULTICAST_SG_V1]
            std::vector<WireReg> records;
            if(op=="wire.register_notify"&&register_eid_afi(plain)==2)records=decode_register6(plain); else records=decode_register4(plain);
            for(auto&w:records){ w.iid=wiid; w.group=wgroup; }
            int alg=raw.size()>13?raw[13]:0,keyid=raw.size()>12?raw[12]:0;
            auto pass=alg?packet_site_password(records,keyid):std::string();
            if(!alg&&packet_needs_auth(records))return "{\"result\":\"auth-failed\",\"notify_hex\":\"\"}";
            if(alg&&(pass.empty()||!verify_register_hmac(raw,pass)))return "{\"result\":\"auth-failed\",\"notify_hex\":\"\"}";
            auto result=apply_wire_registrations(records,arg(a,"source"));
            std::string nh;
            if(result==q("good")&&!records.empty()&&records.front().notify)nh=hex(make_notify_from_register(raw,pass));
            return "{\"result\":"+result+",\"notify_hex\":"+q(nh)+"}";
        }
        if(op=="wire.register4"){
            auto raw=decrypt_register(unhex(arg(a,"hex"))); std::string wiid; auto records=decode_register4(strip_iid_register(raw,wiid));
            for(auto&w:records)w.iid=wiid;                          // [LCAF_INSTANCE_ID_V1]
            int alg=raw.size()>13?raw[13]:0,keyid=raw.size()>12?raw[12]:0;
            if(alg==0&&packet_needs_auth(records))return q("auth-failed");
            if(alg!=0){ auto pass=packet_site_password(records,keyid); if(pass.empty()||!verify_register_hmac(raw,pass))return q("auth-failed"); }
            return apply_wire_registrations(records,arg(a,"source"));
        }
        if(op=="wire.etr_request4"||op=="wire.etr_request6"){
            // ETR role (RFC 9301): answer from this ETR's own held database mapping, best-matching EID-prefix.
            // Map-Server registration truth and the ITR map-cache are deliberately not consulted.
            bool v6=op=="wire.etr_request6"; auto raw=unhex(arg(a,"hex"));
            auto w=v6?decode_request6(raw):decode_request4(raw);
            Json qa=Json::parse("{\"iid\":\"0\",\"prefix\":"+q(w.target)+",\"group\":\"\"}");
            auto r=call("database_mapping.get",qa);
            if(r=="[]")throw std::runtime_error("etr database-mapping miss: reply behaviour not established");
            Mapping m=make_mapping(Json::parse(r));
            m.ttl=1440;                          // control: lisp_etr_process_map_request -> record TTL 1440
            return q(hex(v6?encode_reply6(w.nonce,m):encode_reply4(w.nonce,m)));
        }
        if(op=="etr_map_server.add"){
            MapServerCfg c; c.address=arg(a,"address"); if(c.address.empty())throw std::runtime_error("no address supplied");
            c.ms_name=arg(a,"ms_name","all"); auto pw=arg(a,"password");
            auto alg=arg(a,"alg"); if(alg=="sha2")alg="sha256";
            if(alg.empty())alg=pw.empty()?"none":"sha256";               // control: a key without a type is SHA-256
            if(alg!="none"&&alg!="sha1"&&alg!="sha256")throw std::runtime_error("unknown alg");
            c.alg=alg; c.key_id=(int)(a["key_id"].type==Json::Num?a["key_id"].n:0);
            c.site_id=(uint64_t)(a["site_id"].type==Json::Num?a["site_id"].n:0);
            auto flag=[&](const char*k){return a[k].type==Json::Bool&&a[k].b;};
            c.proxy_reply=flag("proxy_reply"); c.merge=flag("merge"); c.refresh=flag("refresh"); c.want_map_notify=flag("want_map_notify");
            set_ms_key(c.address,pw);
            if(ram)ram->write("lisp","etr-map-server",c.address,ms_bag(c)); else ms_local[c.address]=c;
            return q("good");
        }
        if(op=="etr_map_server.delete"){
            auto ad=arg(a,"address"); MapServerCfg c; c.address=ad; c.active=false; set_ms_key(ad,"");
            if(ram)ram->write("lisp","etr-map-server",ad,ms_bag(c)); else ms_local.erase(ad);
            return q("good");
        }
        if(op=="etr_map_server.get"){
            MapServerCfg c; if(!map_server_config(arg(a,"address"),c))return "[]";
            return ms_bag(c);
        }
        if(op=="etr_map_server.wait"){
            auto ad=arg(a,"address"); bool want=!(a["present"].type==Json::Bool&&!a["present"].b);
            if(!ram){ MapServerCfg c; return (map_server_config(ad,c)==want)?q("good"):q("timeout"); }
            double timeout_s=a["timeout_s"].type==Json::Num?a["timeout_s"].n:-1;
            auto&v=held_map_server_view();
            auto ready=[&](){ auto g=ebr.guard(); auto t=snap_load(v.table,g);
                auto it=t?t->find(ad):MapServerTable::const_iterator{}; return (t&&it!=t->end()&&it->second.active)==want; };
            return wait_applied("etr-map-server-applied",ready,timeout_s)?q("good"):q("timeout");
        }
        if(op=="etr_liveness.start"){
            if(!ram||etr_name.empty())throw std::runtime_error("etr liveness requires the shared-memory backend and an etr identity");
            if(heartbeat&&heartbeat->thread.joinable()){ if(!heartbeat->stop)throw std::runtime_error("heartbeat already running"); heartbeat->thread.join(); }
            auto h=std::make_unique<Heartbeat>();
            if(a["interval_s"].type==Json::Num)h->interval_s=a["interval_s"].n;
            if(a["lifetime_s"].type==Json::Num)h->lifetime_s=a["lifetime_s"].n;
            if(h->interval_s<=0||h->lifetime_s<=h->interval_s)throw std::runtime_error("need 0 < interval_s < lifetime_s");
            heartbeat=std::move(h); auto*hp=heartbeat.get(); auto xtr=etr_xtr,name=etr_name;
            heartbeat->thread=spawn([this,hp,xtr,name]{run_heartbeat(hp,xtr,name);});
            return q("good");
        }
        if(op=="etr_liveness.stop"){
            if(heartbeat&&!heartbeat->stop){ heartbeat->stop=true;
                ram->write("lisp","etr-live-control",etr_name,"{\"stop\":true}"); }   // wakes the heartbeat now
            return q("good");
        }
        if(op=="etr.identity"){
            if(!ram)throw std::runtime_error("etr identity requires the shared-memory backend");
            auto n=arg(a,"name"),x=arg(a,"xtr_id");
            if(n.empty()||x.empty()||n.find('|')!=std::string::npos||n.find('@')!=std::string::npos)throw std::runtime_error("name and xtr_id required; no '|' or '@'");
            if(!held_databases.empty()||registrar)throw std::runtime_error("set the ETR identity before using the database");
            etr_name=n; etr_xtr=x;
            ram->write("lisp","etr-identity",n,"{\"name\":"+q(n)+",\"xtr_id\":"+q(x)+"}");
            return q("good");
        }
        if(op=="ms_governor.start"){
            if(!ram)throw std::runtime_error("the governor requires the shared-memory backend");
            if(governor)throw std::runtime_error("governor already started in this process");
            auto g=std::make_unique<Governor>(); g->name=arg(a,"name"); g->iid=arg(a,"iid","0"); g->group=arg(a,"group");
            if(g->name.empty()||g->name.find('|')!=std::string::npos)throw std::runtime_error("governor name required; no '|'");
            g->policy=&held_resolver(g->iid,g->group);      // site policy held on the request thread before the participant runs
            governor=std::move(g); auto*gp=governor.get(); governor->thread=spawn([this,gp]{run_governor(gp);});
            return q("good");
        }
        if(op=="etr_decision.wait"){
            if(!ram)throw std::runtime_error("decisions require the shared-memory backend");
            if(etr_name.empty())throw std::runtime_error("etr identity not set");
            auto var="etr-decision@"+arg(a,"governor")+"|"+arg(a,"iid","0")+"|"+arg(a,"group");
            auto inst=etr_name+"|"+arg(a,"prefix"); auto want=arg(a,"decision","accepted");
            double timeout_s=a["timeout_s"].type==Json::Num?a["timeout_s"].n:-1;
            auto deadline=std::chrono::steady_clock::now()+std::chrono::duration<double>(timeout_s<0?0:timeout_s);
            for(;;){
                auto cells=ram->read("lisp",var,inst); uint64_t after=0;
                for(auto&c:cells){ if(c.id>after)after=c.id; if(c.bag["decision"].s==want)return q("good"); }
                double w=5.0;
                if(timeout_s>=0){ double left=std::chrono::duration<double>(deadline-std::chrono::steady_clock::now()).count();
                                  if(left<=0)return q("timeout");
                                  if(left<w)w=left; }
                ram->read("lisp",var,inst,(int64_t)after,w);
            }
        }
        if(op=="ram.count"){                               // observation: how many cells a variable holds
            if(!ram)return "0";
            return std::to_string(ram->read("lisp",arg(a,"variable")).size());
        }
        if(op=="etr_registrar.start"){
            if(!ram)throw std::runtime_error("etr registrar requires the shared-memory backend");
            if(registrar&&registrar->thread.joinable()){
                if(!registrar->stop)throw std::runtime_error("etr registrar already running");
                registrar->thread.join(); if(registrar->receiver.joinable())registrar->receiver.join();
                if(registrar->fd>=0)::close(registrar->fd);
            }
            auto x=arg(a,"xtr_id"); if(x.size()>32)throw std::runtime_error("xtr_id too long");
            auto r=std::make_unique<Registrar>(); r->xtr=unhex(std::string(32-x.size(),'0')+x);
            if(a["first_s"].type==Json::Num)r->first_s=a["first_s"].n;
            if(a["interval_s"].type==Json::Num)r->interval_s=a["interval_s"].n;
            if(a["udp_port"].type==Json::Num)r->udp_port=(int)a["udp_port"].n;
            if(r->interval_s<=0)throw std::runtime_error("interval_s must be positive");
            r->db=&held_database("0","");                 // created here, on the request thread, before the participant runs
            // baseline read here too: map-servers configured before start returns get the first periodic send;
            // any configured after it are new and get an immediate register (no race with the thread start)
            r->baseline=ram->read("lisp","etr-map-server");
            r->msv=&held_map_server_view();
            r->fd=::socket(AF_INET,SOCK_DGRAM,0); if(r->fd<0)throw std::runtime_error("registrar socket failed");
            sockaddr_in me{}; me.sin_family=AF_INET; me.sin_addr.s_addr=htonl(INADDR_ANY);
            me.sin_port=htons((uint16_t)(a["listen_port"].type==Json::Num?a["listen_port"].n:0));   // control: ephemeral port
            if(::bind(r->fd,(sockaddr*)&me,sizeof me)!=0){ ::close(r->fd); throw std::runtime_error("registrar bind failed"); }
            socklen_t ml=sizeof me; ::getsockname(r->fd,(sockaddr*)&me,&ml); r->port=ntohs(me.sin_port);
            registrar=std::move(r); auto*rp=registrar.get();
            registrar->thread=spawn([this,rp]{run_registrar(rp);});
            registrar->receiver=spawn([this,rp]{run_registrar_receiver(rp);});
            return q("good");
        }
        if(op=="etr_registrar.stop"){
            stop_registrar();                              // receiver wakes now; the clock at its next wake, then "stopped"
            return q("good");
        }
        if(op=="etr_registrar.notified"){
            if(!ram)return "[]";
            auto cells=ram->read("lisp","etr-registrar-notify",arg(a,"address")); if(cells.empty())return "[]";
            auto&b=cells[0].bag;
            return "{\"address\":"+q(b["address"].s)+",\"acks\":"+std::to_string((uint64_t)b["acks"].n)
                   +",\"rejected\":"+std::to_string((uint64_t)b["rejected"].n)+"}";
        }
        if(op=="etr_registrar.notify_wait"||op=="etr_registrar.state_wait"){
            if(!ram)throw std::runtime_error("etr registrar requires the shared-memory backend");
            bool st=op=="etr_registrar.state_wait"; auto var=st?std::string("etr-registrar"):std::string("etr-registrar-notify");
            auto inst=st?std::string("state"):arg(a,"address"); auto want_state=arg(a,"state","stopped");
            double want=a["min_acks"].type==Json::Num?a["min_acks"].n:1;
            double timeout_s=a["timeout_s"].type==Json::Num?a["timeout_s"].n:-1;
            auto deadline=std::chrono::steady_clock::now()+std::chrono::duration<double>(timeout_s<0?0:timeout_s);
            for(;;){
                auto cells=ram->read("lisp",var,inst); uint64_t after=0;
                for(auto&c:cells){ if(c.id>after)after=c.id;
                    if(st?c.bag["state"].s==want_state:(c.bag["acks"].type==Json::Num&&c.bag["acks"].n>=want))return q("good"); }
                double w=5.0;
                if(timeout_s>=0){ double left=std::chrono::duration<double>(deadline-std::chrono::steady_clock::now()).count();
                                  if(left<=0)return q("timeout");
                                  if(left<w)w=left; }
                ram->read("lisp",var,inst,(int64_t)after,w);
            }
        }
        if(op=="etr_registrar.sent"){
            if(!ram)return "[]";
            auto cells=ram->read("lisp","etr-register-sent",arg(a,"address"));
            if(cells.empty())return "[]";
            auto&b=cells[0].bag; std::ostringstream o;
            if(b["error"].type==Json::Str)
                return "{\"address\":"+q(b["address"].s)+",\"sends\":"+std::to_string((uint64_t)b["sends"].n)+",\"error\":"+q(b["error"].s)+"}";
            o<<"{\"address\":"<<q(b["address"].s)<<",\"sends\":"<<(uint64_t)b["sends"].n<<",\"records\":"<<(uint64_t)b["records"].n
             <<",\"refresh\":"<<(b["refresh"].b?"true":"false")<<",\"nonce\":"<<q(b["nonce"].s)<<",\"bytes\":"<<(uint64_t)b["bytes"].n<<"}";
            return o.str();
        }
        if(op=="etr_registrar.wait"){
            if(!ram)throw std::runtime_error("etr registrar requires the shared-memory backend");
            auto ad=arg(a,"address"); double want=a["min_sends"].type==Json::Num?a["min_sends"].n:1;
            double timeout_s=a["timeout_s"].type==Json::Num?a["timeout_s"].n:-1;
            auto deadline=std::chrono::steady_clock::now()+std::chrono::duration<double>(timeout_s<0?0:timeout_s);
            for(;;){
                auto cells=ram->read("lisp","etr-register-sent",ad); uint64_t after=0;
                for(auto&c:cells){ if(c.id>after)after=c.id; if(c.bag["sends"].type==Json::Num&&c.bag["sends"].n>=want)return q("good"); }
                double w=5.0;
                if(timeout_s>=0){ double left=std::chrono::duration<double>(deadline-std::chrono::steady_clock::now()).count();
                                  if(left<=0)return q("timeout");
                                  if(left<w)w=left; }
                ram->read("lisp","etr-register-sent",ad,(int64_t)after,w);
            }
        }
        if(op=="wire.etr_notify4"){
            // ETR side of Map-Notify (control: lisp_process_map_notify, lisp_send_map_notify_ack).
            auto raw=unhex(arg(a,"hex")); auto src=arg(a,"source"); std::vector<unsigned char> ack;
            auto res=etr_notify_ack(raw,src,[&](MapServerCfg&m){return map_server_config(src,m);},ack);
            if(res!="good")return "{\"result\":"+q(res)+"}";
            if(ram&&!src.empty()){                        // this ETR's own truth: notifies received from each map-server
                uint64_t c=++notifies_received[src];
                ram->write_nowait("lisp","etr-notify-received",src,"{\"address\":"+q(src)+",\"count\":"+std::to_string(c)+"}");
            }
            return "{\"result\":\"good\",\"ack_hex\":"+q(hex(ack))+"}";
        }
        if(op=="wire.etr_register4"){
            // ETR role: one Map-Register for this ETR's held database mapping (IID 0, IPv4 entries).
            EtrRegisterConfig c;
            auto fixed=[&](const std::string&h,size_t len){
                if(h.size()>len*2)throw std::runtime_error("xtr_id/site_id/nonce too long");
                return unhex(std::string(len*2-h.size(),'0')+h);
            };
            auto algno=[&](const std::string&alg){ int n=alg=="sha1"?1:alg=="sha256"||alg=="sha2"?2:alg=="none"?0:-1;
                if(n<0)throw std::runtime_error("unknown alg");
                return n; };
            auto msa=arg(a,"map_server");
            if(!msa.empty()){                     // held map-server configuration; password process-private
                MapServerCfg m; if(!map_server_config(msa,m))throw std::runtime_error("map-server not configured: "+msa);
                c.key_id=m.key_id; c.alg=algno(m.alg); c.notify=m.want_map_notify; c.merge=m.merge; c.proxy=m.proxy_reply;
                c.refresh=m.refresh&&a["refresh"].type==Json::Bool&&a["refresh"].b;     // refresh only on the periodic timer
                if(c.alg){ if(!ms_key(msa,c.password))throw std::runtime_error("no key for map-server "+msa+" in this process"); }
                std::ostringstream sh; sh<<std::hex<<m.site_id; c.site_id=fixed(sh.str(),8);
            } else {
                c.key_id=(int)(a["ms_key_id"].type==Json::Num?a["ms_key_id"].n:0);
                c.alg=algno(arg(a,"ms_alg","none"));
                c.password=arg(a,"ms_password");
                auto flag=[&](const char*k){return a[k].type==Json::Bool&&a[k].b;};
                c.notify=flag("want_map_notify"); c.merge=flag("merge"); c.proxy=flag("proxy_reply"); c.refresh=flag("refresh");
                c.site_id=fixed(arg(a,"site_id","0"),8);
            }
            c.xtr_id=fixed(arg(a,"xtr_id"),16);
            auto nb=fixed(arg(a,"nonce","aabbccdddfdfdf01"),8); c.nonce=0; for(auto b:nb)c.nonce=(c.nonce<<8)|b;
            if(a["ttl"].type==Json::Num)c.ttl=(uint32_t)a["ttl"].n;
            std::vector<Mapping> ms;
            auto take=[&](const Mapping&m){ if(m.registered&&m.prefix.find(':')==std::string::npos)ms.push_back(m); };
            if(ram){
                database_mapping_reads_last=0; in_database_mapping_request=true;
                HeldDatabaseView*vp=nullptr;
                try{vp=&held_database("0","");in_database_mapping_request=false;}
                catch(...){in_database_mapping_request=false;throw;}
                auto g=ebr.guard(); auto p=snap_load(vp->mappings,g); if(p)for(auto&kv:*p)take(kv.second);
            } else for(auto&kv:etr_db)if(kv.second.iid=="0"&&kv.second.group=="")take(kv.second);
            if(ms.empty())throw std::runtime_error("etr has no active IPv4 database mapping to register");
            return q(hex(encode_etr_register4(c,ms)));
        }
        if(op=="transport.stats"){
            // this participant's own FNW1 session counters: the evidence for any bytes-on-the-wire claim
            if(!session)return "{\"backend\":\"local\",\"bytes_out\":0,\"bytes_in\":0,\"raw\":0,\"repeat\":0,\"same\":0,\"miss\":0}";
            auto st=session->stats(); std::ostringstream o;
            o<<"{\"backend\":\"fnw1\",\"bytes_out\":"<<st.bytes_out<<",\"bytes_in\":"<<st.bytes_in<<",\"raw\":"<<st.raw
             <<",\"repeat\":"<<st.repeat<<",\"same\":"<<st.same<<",\"miss\":"<<st.miss
             <<",\"calls\":"<<frogram::calls_this_thread()<<"}";
            return o.str();
        }
        if(op=="system.get")return "{\"implementation\":\"ribbit-lisp\",\"status\":\"running\"}";
        if(op=="map_resolver.add"){
            auto ad=arg(a,"address");
            if(ad.empty())throw std::runtime_error("no address supplied");
            if(ram)ram->write("lisp","map-resolver",ad,"{\"address\":"+q(ad)+",\"active\":true}");
            else resolvers[ad]=ad;
            return q("good");
        }
        if(op=="map_resolver.get"){
            auto ad=arg(a,"address");
            if(ram){
                auto&v=held_map_resolver_view(); auto g=ebr.guard(); auto active=snap_load(v.active,g);
                auto it=active?active->find(ad):ResolverTable::const_iterator{};
                return !active||it==active->end()||!it->second?"[]":"{\"address\":"+q(ad)+"}";
            }
            auto it=resolvers.find(ad);
            return it==resolvers.end()?"[]":"{\"address\":"+q(ad)+"}";
        }
        if(op=="map_resolver.delete"){
            auto ad=arg(a,"address");
            if(ram)ram->write("lisp","map-resolver",ad,"{\"address\":"+q(ad)+",\"active\":false}");
            else resolvers.erase(ad);
            return q("good");
        }
        if(op=="map_resolver.wait"){
            if(!ram)return q("good");
            auto ad=arg(a,"address");
            bool want=!(a["present"].type==Json::Bool&&!a["present"].b);
            auto&v=held_map_resolver_view();
            auto ready=[&](){
                auto g=ebr.guard();
                auto active=snap_load(v.active,g);
                auto it=active?active->find(ad):ResolverTable::const_iterator{};
                return (active&&it!=active->end()&&it->second)==want;
            };
            return wait_applied("map-resolver-applied",ready)?q("good"):q("timeout");
        }
        if(op=="map_cache.add"||op=="database_mapping.add"){
            auto m=make_mapping(a);
            if(ram&&op=="map_cache.add"){
                int bits=prefix_parse(m.prefix).bits;
                ram->write("lisp",ram_var_len("map-cache",m.iid,m.group,bits),m.prefix,bag_json(m));
                publish_known_length("map-cache",m.iid,m.group,bits);
            } else if(ram)ram->write("lisp",db_var(m.iid,m.group),m.prefix,bag_json(m));
            else{
                auto&tab=op[0]=='m'?cache:etr_db;
                tab[key(m.iid,m.prefix,m.group)]=m;
            }
            return q("good");
        }
        if(op=="ddt.add"){
            auto m=make_mapping(a);
            if(ram){
                int bits=prefix_parse(m.prefix).bits;
                ram->write("lisp",ram_var_len("ddt-delegation",m.iid,m.group,bits),m.prefix,bag_json(m));
                publish_known_length("ddt-delegation",m.iid,m.group,bits);
            } else ddt[key(m.iid,m.prefix,m.group)]=m;
            return q("good");
        }
        if(op=="ddt.delete"){
            auto iid=arg(a,"iid","0"),p=arg(a,"prefix"),g=arg(a,"group");
            if(ram){
                Mapping withdrawn;
                withdrawn.iid=iid; withdrawn.prefix=p; withdrawn.group=g; withdrawn.registered=false;
                int bits=prefix_parse(p).bits;
                ram->write("lisp",ram_var_len("ddt-delegation",iid,g,bits),p,bag_json(withdrawn));
                publish_known_length("ddt-delegation",iid,g,bits);
            } else ddt.erase(key(iid,p,g));
            return q("good");
        }
        if(op=="ddt.get"){
            auto iid=arg(a,"iid","0"),target=arg(a,"prefix"),g=arg(a,"group");
            Prefix tp=prefix_parse(target);
            std::vector<Mapping> all;
            if(ram){
                ddt_reads_last=0;
                in_ddt_request=true;
                try{
                    auto&v=held_resolver(iid,g); in_ddt_request=false;
                    each_mapping(v.ddt_delegations,[&](const auto&kv){all.push_back(kv.second);});
                } catch(...){
                    in_ddt_request=false;
                    throw;
                }
            } else for(auto&kv:ddt)if(kv.second.iid==iid&&kv.second.group==g)all.push_back(kv.second);
            Mapping*best=nullptr,sel;
            int bits=-1;
            for(auto&m:all){
                if(!m.registered)continue;
                Prefix mp=prefix_parse(m.prefix);
                if(contains(mp,tp)&&mp.bits>bits){
                    sel=m;
                    best=&sel;
                    bits=mp.bits;
                }
            }
            return best?mapping_json(*best):"[]";
        }
        if(op=="site.add"){
            auto iid=arg(a,"iid","0"),p=arg(a,"prefix"),g=arg(a,"group");
            bool ams=(a["accept_more_specifics"].type==Json::Bool&&a["accept_more_specifics"].b);
            int kid=a["key_id"].type==Json::Num?(int)a["key_id"].n:0;
            auto pass=arg(a,"password");
            prefix_parse(p);
            Prefix sp=prefix_parse(p);
            std::string opts;                                   // [SITE_OPTIONS_V1] the site's options, as given
            for(const char*k:{"shutdown","echo_nonce_capable","force_proxy_reply","force_nat_proxy_reply","pitr_proxy_reply_drop"})
                if(a[k].type==Json::Bool)opts+=std::string(",\"")+k+"\":"+(a[k].b?"true":"false");
            if(a["force_ttl"].type==Json::Num)opts+=",\"force_ttl\":"+std::to_string((int)a["force_ttl"].n);
            if(a["policy_name"].type==Json::Str)opts+=",\"policy_name\":"+q(a["policy_name"].s);
            if(a["proxy_reply_action"].type==Json::Str){ auto v=a["proxy_reply_action"].s;
                if(v!="native-forward"&&v!="drop"&&v!="not-registered-yet")throw std::runtime_error("proxy_reply_action: native-forward, drop or not-registered-yet");
                opts+=",\"proxy_reply_action\":"+q(v); }
            if(a["allowed_rlocs"].type==Json::Arr){ std::string l; for(auto&r:a["allowed_rlocs"].a)if(r.type==Json::Str)l+=(l.empty()?"":",")+q(r.s); opts+=",\"allowed_rlocs\":["+l+"]"; }
            if(ram)ram->write("lisp",ram_var_len("site",iid,g,sp.bits),p,"{\"prefix\":"+q(p)+",\"active\":true,\"accept_more_specifics\":"+(ams?"true":"false")+",\"key_id\":"+std::to_string(kid)+opts+"}");
            const bool g_is_prefix=g.find('/')!=std::string::npos; if(g_is_prefix)prefix_parse(g);   // "addr/len" or a name
            if(ram&&g_is_prefix)ram->write("lisp","site-groups|"+iid,g,"{\"group\":"+q(g)+"}");   // [MULTICAST_SG_V1]
            publish_known_length("site",iid,g,sp.bits);
            if(resident){ if(!pass.empty())ram->write(RESIDENT_SECRETS,"site-key",key(iid,p,g)+"|"+std::to_string(kid),
                "{\"iid\":"+q(iid)+",\"prefix\":"+q(p)+",\"group\":"+q(g)+",\"key_id\":"+std::to_string(kid)+",\"password\":"+q(pass)+"}"); }
            else { sites[key(iid,p,g)]=ams; if(!pass.empty())site_keys[key(iid,p,g)+"|"+std::to_string(kid)]=pass; }
            return q("good");
        }
        if(op=="site.delete"){
            auto iid=arg(a,"iid","0"),p=arg(a,"prefix"),g=arg(a,"group");
            if(ram){
                Prefix sp=prefix_parse(p);
                ram->write("lisp",ram_var_len("site",iid,g,sp.bits),p,
                    "{\"prefix\":"+q(p)+",\"active\":false,\"accept_more_specifics\":false}");
            }
            auto sk=key(iid,p,g);
            if(resident){                                  // the site's key rows, emptied
                for(auto&c:ram->read(RESIDENT_SECRETS,"site-key"))if(c.instance.rfind(sk+"|",0)==0&&!arg(c.bag,"password").empty())
                    ram->write(RESIDENT_SECRETS,"site-key",c.instance,"{\"iid\":"+q(iid)+",\"prefix\":"+q(p)+",\"group\":"+q(g)+",\"key_id\":"+std::to_string((int)c.bag["key_id"].n)+",\"password\":\"\"}");
                return q("good");
            }
            sites.erase(sk);
            for(auto it=site_keys.begin();it!=site_keys.end();)if(it->first.rfind(sk+"|",0)==0)it=site_keys.erase(it);
            else ++it;
            return q("good");
        }
        if(op=="registration.put"){
            auto m=make_mapping(a);
            if(!apply_preauthorized){
                std::vector<std::pair<std::string,bool>> auth;
                if(resident){
                    const std::string sgroup=m.group.empty()?m.group:site_group_for(m.iid,m.group);   // [MULTICAST_SG_V1]
                    if(!m.group.empty()&&sgroup.empty())return q("unauthorized");
                    each_site_resident(m.iid,sgroup,prefix_parse(m.prefix).bits,[&](const std::string&sp,const SitePol&pol){
                        if(!pol.first||pol.shutdown)return;
                        if(!pol.allowed_rlocs.empty())for(auto&r:m.rlocs)
                            if(std::find(pol.allowed_rlocs.begin(),pol.allowed_rlocs.end(),r.address)==pol.allowed_rlocs.end())return;
                        auth.push_back({sp,pol.second}); });
                } else if(ram){
                    authorization_reads_last=0;
                    in_authorization_request=true;
                    HeldResolverView*vp=nullptr;
                    const std::string sgroup=m.group.empty()?m.group:site_group_for(m.iid,m.group);   // [MULTICAST_SG_V1]
                    if(!m.group.empty()&&sgroup.empty()){ in_authorization_request=false; return q("unauthorized"); }
                    try{ vp=&held_resolver(m.iid,sgroup); }
                    catch(...){ in_authorization_request=false; throw; }
                    in_authorization_request=false;
                    each_site(vp->site_policy,[&](const auto&kv){
                        if(!kv.second.first||kv.second.shutdown)return;                 // [SITE_OPTIONS_V1] shutdown
                        if(!kv.second.allowed_rlocs.empty())for(auto&r:m.rlocs)
                            if(std::find(kv.second.allowed_rlocs.begin(),kv.second.allowed_rlocs.end(),r.address)==kv.second.allowed_rlocs.end())return;
                        auth.push_back({kv.first,kv.second.second});});
                } else for(auto&kv:sites){
                    auto bar=kv.first.find('|');
                    auto bar2=kv.first.find('|',bar+1);
                    if(kv.first.substr(0,bar)==m.iid&&kv.first.substr(bar2+1)==m.group)auth.push_back({
                        kv.first.substr(bar+1,bar2-bar-1),kv.second
                    }
                    );
                }
                // Every record needs an authorizing site, including when no site is configured at all (CONTRACT v0.6;
                // control: lisp_process_map_register skips a record with no site). Until v0.42 an empty site table
                // skipped this check ("open mode"): a bug that also let deleting the last site open the Map-Server.
                {
                    Prefix rp=prefix_parse(m.prefix);
                    bool ok=false;
                    for(auto&x:auth){
                        Prefix sp=prefix_parse(x.first);
                        if(sp.family==rp.family&&contains(sp,rp)&&(sp.bits==rp.bits||x.second)){
                            ok=true;
                            break;
                        }
                    }
                    if(!ok)return q("unauthorized");
                }
            }
            if(a["ttl"].type==Json::Num)m.ttl=(uint32_t)a["ttl"].n;
            m.use_register_ttl=(a["use_register_ttl"].type==Json::Bool&&a["use_register_ttl"].b);
            bool refresh=(a["refresh"].type==Json::Bool&&a["refresh"].b);
            auto k=key(m.iid,m.prefix,m.group);
            std::vector<Mapping> old;
            auto put_guard=ebr.guard();                    // [HELD_TUPLES_V1] held tuples read below stay valid for the put
            std::vector<const Mapping*> held_prior;
            if(resident){                                  // [RESIDENT_REGION_V1] the prior is the row this put addresses
                auto c=ram->read("lisp",ram_var_len("registration",m.iid,m.group,prefix_parse(m.prefix).bits),m.prefix+(m.merge?("|"+m.xtr_id):""));
                if(!c.empty())old.push_back(from_cell(c.front()));
            } else if(ram){
                // [PRIOR_FROM_THE_HELD_VIEW_V1] (v0.46) The prior is found in this participant's held registration view
                // -- a lookup, no round trip -- not by reading the whole per-length table from the memory on every
                // put (a register cost grew with the table: refresh 237 ms median at 128 rows, compare_lispers.py).
                // The view is current for this participant's own writes: every operation returns only after its own
                // views have applied what it wrote ([READ_YOUR_OWN_WRITE_V1]), and every registration write in a
                // Ribbit-LISP region goes through the region's own API.
                // [HELD_TUPLES_V1] the tuples held for this prefix, where they are -- not a copy of the whole table
                Prefix rp=prefix_parse(m.prefix);
                auto&v=held_resolver(m.iid,m.group);
                if(auto*rs=v.registrations[rp.bits].load(std::memory_order_acquire))
                    rs->each_in(prefix_key(m.prefix),[&](const std::string&,const Mapping&x){ held_prior.push_back(&x); });
            }  else for(auto&kv:db)old.push_back(kv.second);
            const Mapping* prior=nullptr;
            for(auto*x:held_prior)if(x->prefix==m.prefix && (!m.merge || x->xtr_id==m.xtr_id)){ prior=x; break; }
            if(!prior)for(auto&x:old)if(x.prefix==m.prefix && (!m.merge || x.xtr_id==m.xtr_id)){
                prior=&x;
                break;
            }
            if(m.ttl==0){
                if(prior){
                    auto src=arg(a,"source");
                    bool authorized=false;
                    for(auto&r:prior->rlocs)if(r.address==src)authorized=true;
                    if(!authorized)return q("ignored");
                }
                if(a["validate_only"].type==Json::Bool&&a["validate_only"].b)return q("good");
                if(prior){
                    Mapping withdrawn=*prior;
                    withdrawn.registered=false;
                    withdrawn.last_registered=now_ms();
                    std::string instance=withdrawn.prefix+(withdrawn.merge?("|"+withdrawn.xtr_id):"");
                    if(ram)ram->write("lisp",ram_var_len("registration",withdrawn.iid,withdrawn.group,prefix_parse(withdrawn.prefix).bits),instance,bag_json(withdrawn));
                    else db[key(withdrawn.iid,instance,withdrawn.group)]=withdrawn;
                }
                return q("good");
            }
            if(refresh&&prior){
                bool same=prior->rlocs.size()==m.rlocs.size();
                if(same)for(size_t i=0;i<m.rlocs.size();++i)if(prior->rlocs[i].address!=m.rlocs[i].address||prior->rlocs[i].priority!=m.rlocs[i].priority||prior->rlocs[i].weight!=m.rlocs[i].weight){
                    same=false;
                    break;
                }
                if(!same)return q("rejected");
            }
            if(a["validate_only"].type==Json::Bool&&a["validate_only"].b)return q("good");
            m.last_registered=now_ms();
            m.expires_at=m.last_registered+registration_lifetime(m.ttl,m.use_register_ttl)*1000;
            std::string instance=m.prefix+(m.merge?("|"+m.xtr_id):"");
            if(ram)ram->write("lisp",ram_var_len("registration",m.iid,m.group,prefix_parse(m.prefix).bits),instance,bag_json(m));
            else db[key(m.iid,instance,m.group)]=m;
            publish_known_length("registration",m.iid,m.group,prefix_parse(m.prefix).bits);
            govern(m);
            return q("good");
        }
        if(op=="registration.delete"){
            auto iid=arg(a,"iid","0"),p=arg(a,"prefix"),g=arg(a,"group");
            if(ram){
                Prefix rp=prefix_parse(p);
                ram->write("lisp",ram_var_len("registration-governance",iid,g,rp.bits),p,
                    "{\"mode\":\"inactive\",\"site_id\":\"\"}");
            } else{
                for(auto it=db.begin();it!=db.end();){
                    if(it->second.iid==iid&&it->second.prefix==p&&it->second.group==g)it=db.erase(it);
                    else ++it;
                }
            }
            return q("good");
        }
        if(op=="resolver.stats"){
            std::ostringstream o;
            o<<"{\"request_reads\":"<<request_reads_last
             <<",\"request_reads_total\":"<<request_reads_total
             <<",\"authorization_reads\":"<<authorization_reads_last
             <<",\"authorization_reads_total\":"<<authorization_reads_total
             <<",\"ddt_reads\":"<<ddt_reads_last
             <<",\"ddt_reads_total\":"<<ddt_reads_total
             <<",\"map_cache_reads\":"<<map_cache_reads_last
             <<",\"map_cache_reads_total\":"<<map_cache_reads_total
             <<",\"database_mapping_reads\":"<<database_mapping_reads_last
             <<",\"database_mapping_reads_total\":"<<database_mapping_reads_total
             <<",\"map_server_reads\":"<<map_server_reads_last<<",\"map_server_reads_total\":"<<map_server_reads_total
             <<",\"views\":"<<held_resolvers.size()<<"}";
            return o.str();
        }
        if(op=="database_mapping.wait"){
            auto iid=arg(a,"iid","0"),prefix=arg(a,"prefix"),group=arg(a,"group"),rloc=arg(a,"rloc");
            bool want=!(a["present"].type==Json::Bool&&!a["present"].b);
            double timeout_s=a["timeout_s"].type==Json::Num?a["timeout_s"].n:-1;
            // optional rloc: present means "present AND carrying this locator" (observe a replacement, as map_cache.wait)
            auto matches=[&](const Mapping&m){
                if(!m.registered)return false;
                if(rloc.empty())return true;
                for(auto&r:m.rlocs)if(r.address==rloc)return true;
                return false;
            };
            if(!ram){ auto it=etr_db.find(key(iid,prefix,group)); return ((it!=etr_db.end()&&matches(it->second))==want)?q("good"):q("timeout"); }
            auto&v=held_database(iid,group);
            auto ready=[&](){
                auto g=ebr.guard(); auto p=snap_load(v.mappings,g);
                auto it=p?p->find(prefix):MappingTable::const_iterator{};
                return (p&&it!=p->end()&&matches(it->second))==want;
            };
            return wait_applied("database-mapping-applied"+etr_tag()+"|"+iid+"|"+group,ready,timeout_s)?q("good"):q("timeout");
        }
        if(op=="map_cache.wait"){
            auto iid=arg(a,"iid","0"),prefix=arg(a,"prefix"),group=arg(a,"group"),rloc=arg(a,"rloc");
            bool want=!(a["present"].type==Json::Bool&&!a["present"].b);
            double timeout_s=a["timeout_s"].type==Json::Num?a["timeout_s"].n:-1;
            // optional rloc: present means "present AND carrying this locator" (replacement convergence)
            auto matches=[&](const Mapping&m){
                if(!m.registered)return false;
                if(rloc.empty())return true;
                for(auto&r:m.rlocs)if(r.address==rloc)return true;
                return false;
            };
            if(!ram){ auto it=cache.find(key(iid,prefix,group)); bool ok=(it!=cache.end()&&matches(it->second))==want;
                      return ok?q("good"):q("timeout"); }
            auto&v=held_map_cache(iid,group); int bits=prefix_parse(prefix).bits;
            auto ready=[&](){
                auto g=ebr.guard(); auto p=snap_load(v.mappings[bits],g);
                auto it=p?p->find(prefix):MappingTable::const_iterator{};
                return (p&&it!=p->end()&&matches(it->second))==want;
            };
            return wait_applied("map-cache-applied|"+iid+"|"+group,ready,timeout_s)?q("good"):q("timeout");
        }
        if(op=="resolver.wait_ddt"){
            if(!ram)return q("good");
            auto iid=arg(a,"iid","0"),prefix=arg(a,"prefix"),group=arg(a,"group");bool want=!(a["present"].type==Json::Bool&&!a["present"].b);
            auto&v=held_resolver(iid,group); int bits=prefix_parse(prefix).bits;
            auto ready=[&](){
                auto g=ebr.guard(); auto p=snap_load(v.ddt_delegations[bits],g);
                auto it=p?p->find(prefix):MappingTable::const_iterator{};
                return (p&&it!=p->end()&&it->second.registered)==want;
            };
            return wait_applied("resolver-applied|"+iid+"|"+group,ready)?q("good"):q("timeout");
        }
        if(op=="resolver.wait_site"){
            if(!ram)return q("good");
            auto iid=arg(a,"iid","0"),prefix=arg(a,"prefix"),group=arg(a,"group");bool want=!(a["active"].type==Json::Bool&&!a["active"].b);
            auto&v=held_resolver(iid,group); int bits=prefix_parse(prefix).bits;
            auto ready=[&](){
                auto g=ebr.guard(); auto p=snap_load(v.site_policy[bits],g);
                auto it=p?p->find(prefix):SiteTable::const_iterator{};
                return (p&&it!=p->end()&&it->second.first)==want;
            };
            return wait_applied("resolver-applied|"+iid+"|"+group,ready)?q("good"):q("timeout");
        }
        if(op=="resolver.wait"){
            if(!ram)return q("good");
            auto iid=arg(a,"iid","0"),prefix=arg(a,"prefix"),group=arg(a,"group");bool want=!(a["present"].type==Json::Bool&&!a["present"].b);auto want_rloc=arg(a,"rloc");
            // optional absent=true with rloc: wait until the prefix is present WITHOUT that locator (one ETR's
            // contribution withdrawn while others remain); optional timeout_s
            bool absent=a["absent"].type==Json::Bool&&a["absent"].b&&!want_rloc.empty();
            double timeout_s=a["timeout_s"].type==Json::Num?a["timeout_s"].n:-1;
            auto&v=held_resolver(iid,group); auto&lv=held_liveness(); int bits=prefix_parse(prefix).bits;
            auto ready=[&](){
                auto g=ebr.guard(); auto now=now_ms();
                auto*regs=v.registrations[bits].load(std::memory_order_acquire);            // [HELD_TUPLES_V1]
                auto*gov=v.governance[bits].load(std::memory_order_acquire);
                auto lt=snap_load(lv.table,g);
                // what resolution would answer: unexpired, and a native registration only while its ETR is alive
                auto live=[&](const Mapping&m){
                    if(m.expires_at!=0&&m.expires_at<=now)return false;
                    if(m.live_key.empty())return true;
                    if(!lt)return false;
                    auto it=lt->find(m.live_key);
                    return it!=lt->end()&&it->second>now&&site_authorizes(v,m.prefix); };
                std::pair<std::string,std::string> gv;
                if(gov)if(auto*x=gov->get(prefix_key(prefix),prefix))gv=*x;
                bool present=false,rloc_seen=want_rloc.empty();
                if(regs)regs->each_in(prefix_key(prefix),[&](const std::string&,const Mapping&m){
                    if(m.prefix==prefix&&m.registered&&governed_in(m,gv)&&live(m)){
                        present=true;
                        if(!want_rloc.empty())for(auto&r:m.rlocs)
                            if(r.address==want_rloc)rloc_seen=true;
                    }
                });
                if(absent)return present&&!rloc_seen;
                return present==want&&(!want||rloc_seen);
            };
            return wait_applied("resolver-applied|"+iid+"|"+group,ready,timeout_s)?q("good"):q("timeout");
        }
        if(op=="resolution.get"){
            auto iid=arg(a,"iid","0"),target=arg(a,"prefix"),group=arg(a,"group");
            if(target.empty())throw std::runtime_error("no prefix supplied");
            Prefix tp=prefix_parse(target);
            auto now=now_ms();
            if(ram){
                // [HELD_TUPLES_V1] longest-prefix match over the held tuples: for each length, longest first, the one
                // key the target falls under at that length -- the tuples there are read where they are. The first
                // length with a registration that resolves now wins; its tuples are merged in instance order.
                request_reads_last=0;
                in_resolution_request=true;
                HeldResolverView*vp=nullptr; HeldLivenessView*lvp=nullptr;
                try{ vp=&held_resolver(iid,group); lvp=&held_liveness(); } catch(...){ in_resolution_request=false; throw; }
                in_resolution_request=false;
                auto&v=*vp; auto g=ebr.guard(); auto lt=snap_load(lvp->table,g);
                const int maxb=tp.family==AF_INET?32:128;
                for(int L=std::min(tp.bits,maxb);L>=0;--L){
                    auto*rs=v.registrations[L].load(std::memory_order_acquire); if(!rs)continue;
                    auto*gs=v.governance[L].load(std::memory_order_acquire);
                    const std::string k=masked_key(tp,L);
                    std::vector<const Mapping*> hit;
                    rs->each_in(k,[&](const std::string&,const Mapping&m){
                        std::pair<std::string,std::string> gv; if(gs)if(auto*x=gs->get(k,m.prefix))gv=*x;
                        if(!m.registered||!governed_in(m,gv)||(m.expires_at!=0&&m.expires_at<=now))return;
                        if(!m.live_key.empty()){                      // native: its ETR must be alive now,
                            if(!lt)return;
                            auto it=lt->find(m.live_key); if(it==lt->end()||it->second<=now)return;
                            if(!site_authorizes(v,m.prefix))return;   // and a site must authorize it now
                        }
                        Prefix mp=prefix_parse(m.prefix); if(!contains(mp,tp))return;
                        hit.push_back(&m); });
                    if(hit.empty())continue;
                    Mapping out=*hit.front(); out.rlocs.clear();      // the answer is a new value built from the tuples
                    std::set<std::string> seen;
                    for(auto*m:hit)for(auto&r:m->rlocs)
                        if(seen.insert(r.address+"|"+std::to_string(r.priority)+"|"+std::to_string(r.weight)).second)out.rlocs.push_back(r);
                    return mapping_json(out);
                }
                return "[]";
            }
            std::vector<Mapping> all; int bits=-1; std::string bp;
            for(auto&kv:db)if(kv.second.iid==iid&&kv.second.group==group)all.push_back(kv.second);
            for(auto&m:all){
                Prefix mp=prefix_parse(m.prefix);
                auto gv=governance_for(iid,m.prefix,group);
                if(m.registered&&governed_in(m,gv)&&(m.expires_at==0||m.expires_at>now)&&contains(mp,tp)&&mp.bits>bits){ bits=mp.bits; bp=m.prefix; }
            }
            if(bits<0)return "[]";
            Mapping out; bool have=false; std::set<std::string> seen;
            for(auto&m:all){
                auto gv=governance_for(iid,m.prefix,group);
                if(!m.registered||!governed_in(m,gv)||(m.expires_at!=0&&m.expires_at<=now)||m.prefix!=bp)continue;
                if(!have){ out=m; out.rlocs.clear(); have=true; }
                for(auto&r:m.rlocs)if(seen.insert(r.address+"|"+std::to_string(r.priority)+"|"+std::to_string(r.weight)).second)out.rlocs.push_back(r);
            }
            return mapping_json(out);
        }
        if(op=="map_cache.delete"||op=="database_mapping.delete"){
            std::string kind=op[0]=='m'?"map-cache":"database-mapping";
            auto iid=arg(a,"iid","0"),p=arg(a,"prefix"),g=arg(a,"group");
            if(ram&&op=="map_cache.delete"){
                Mapping withdrawn; withdrawn.iid=iid; withdrawn.prefix=p; withdrawn.group=g; withdrawn.registered=false;
                int bits=prefix_parse(p).bits;
                ram->write("lisp",ram_var_len("map-cache",iid,g,bits),p,bag_json(withdrawn));
                publish_known_length("map-cache",iid,g,bits);
            } else if(ram){
                Mapping withdrawn; withdrawn.iid=iid; withdrawn.prefix=p; withdrawn.group=g; withdrawn.registered=false;
                ram->write("lisp",db_var(iid,g),p,bag_json(withdrawn));
            }
            else{
                auto&tab=op[0]=='m'?cache:etr_db;
                tab.erase(key(iid,p,g));
            }
            return q("good");
        }
        if(op=="map_cache.list"){
            std::string s="[";
            bool first=true;
            if(ram){
                map_cache_reads_last=0; in_map_cache_request=true;
                HeldMapCacheView*vp=nullptr;
                try{ vp=&held_map_cache("0",""); in_map_cache_request=false; }
                catch(...){ in_map_cache_request=false; throw; }
                each_mapping(vp->mappings,[&](const auto&kv){auto&m=kv.second;if(!m.registered)return;if(!first)s+=",";first=false;s+=mapping_json(m);});
            } else for(auto&kv:cache){
                if(!first)s+=",";
                first=false;
                s+=mapping_json(kv.second);
            }
            return s+"]";
        }
        if(op=="database_mapping.get"){
            auto iid=arg(a,"iid","0"),target=arg(a,"prefix"),group=arg(a,"group"); if(target.empty())throw std::runtime_error("no prefix supplied"); Prefix tp=prefix_parse(target);
            const Mapping*best=nullptr; Mapping selected; int bits=-1;
            if(ram){
                database_mapping_reads_last=0; in_database_mapping_request=true;
                HeldDatabaseView*vp=nullptr;
                try{vp=&held_database(iid,group);in_database_mapping_request=false;}
                catch(...){in_database_mapping_request=false;throw;}
                auto g=ebr.guard(); auto p=snap_load(vp->mappings,g);
                if(p)for(auto&kv:*p){
                    auto&m=kv.second; if(!m.registered)continue; Prefix mp=prefix_parse(m.prefix);
                    if(contains(mp,tp)&&mp.bits>bits){selected=m;best=&selected;bits=mp.bits;}
                }
            }
            else for(auto&kv:etr_db){auto&m=kv.second;if(m.iid!=iid||m.group!=group||!m.registered)continue;Prefix mp=prefix_parse(m.prefix);if(contains(mp,tp)&&mp.bits>bits){best=&m;bits=mp.bits;}}
            return best?mapping_json(*best):"[]";
        }
        if(op=="map_cache.get"){
            auto iid=arg(a,"iid","0"), target=arg(a,"prefix"), group=arg(a,"group");
            if(target.empty())throw std::runtime_error("no prefix supplied");
            Prefix tp=prefix_parse(target);
            const Mapping*best=nullptr;
            int bits=-1;
            Mapping selected;
            if(ram){
                map_cache_reads_last=0; in_map_cache_request=true;
                HeldMapCacheView*vp=nullptr;
                try{ vp=&held_map_cache(iid,group); in_map_cache_request=false; }
                catch(...){ in_map_cache_request=false; throw; }
                each_mapping(vp->mappings,[&](const auto&kv){
                    auto&m=kv.second; if(!m.registered)return; Prefix mp=prefix_parse(m.prefix);
                    if(contains(mp,tp)&&mp.bits>bits){selected=m;best=&selected;bits=mp.bits;}
                });
            }
            else for(auto&kv:cache){
                auto&m=kv.second;
                if(m.iid!=iid||m.group!=group)continue;
                Prefix mp=prefix_parse(m.prefix);
                if(contains(mp,tp)&&mp.bits>bits){
                    best=&m;
                    bits=mp.bits;
                }
            }
            return best?mapping_json(*best):"[]";
        }
        throw std::runtime_error("unsupported operation: "+op);
    }
};
