#!/usr/bin/env python3
"""apitap: tally the Meshtastic client API and OTA traffic seen in a loopback pcap.

Client API: TCP streams with one end on a meshtasticd API port. Stream framing is
0x94 0xC3 <len16 BE> <protobuf>. Client->daemon bytes are ToRadio, daemon->client
bytes are FromRadio. Each MeshPacket carried in either direction is decoded far
enough to name its portnum, and ADMIN_APP payloads are decoded to their
AdminMessage variant.

OTA: UDP datagrams to port 4403 (multicast group 224.0.0.69 / 239.0.0.69). Each
datagram is one bare MeshPacket. Channel traffic is decrypted with the default
LongFast key (AES-CTR, nonce = id u64 LE | from u32 LE | 0 u32) to get the
portnum; PKI packets are counted as pki (not decryptable without node keys).

Usage: apitap.py OUT.json PCAP [PCAP...] [--ports 4403-4420]
Output: JSON with per-variant counts, plus decode-error counts (never dropped silently).
"""
import argparse
import base64
import collections
import json
import socket
import struct
import sys

import dpkt
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from meshtastic.protobuf import admin_pb2, mesh_pb2, portnums_pb2

DEFAULT_PSK = base64.b64decode("1PG7OiApB1nwvP+rz05pAQ==")


def portname(n):
    try:
        return portnums_pb2.PortNum.Name(n)
    except ValueError:
        return f"PORT_{n}"


class Tally:
    def __init__(self):
        self.c = collections.defaultdict(collections.Counter)

    def add(self, k, v, n=1):
        self.c[k][v] += n

    def packet(self, where, mp):
        """mp: MeshPacket. where: 'to_radio' | 'from_radio' | 'ota'."""
        if mp.HasField("decoded"):
            self.data(where, mp.decoded)
        elif mp.HasField("encrypted"):
            if mp.pki_encrypted or mp.channel == 0 and where == "ota":
                self.add(f"{where}.portnum", "ENCRYPTED_PKI")
                return
            data = self.try_decrypt(mp)
            if data is None:
                self.add(f"{where}.portnum", "ENCRYPTED_UNDECODED")
            else:
                self.data(where, data)
        else:
            self.add(f"{where}.portnum", "NO_PAYLOAD")

    def data(self, where, d):
        pn = portname(d.portnum)
        self.add(f"{where}.portnum", pn)
        if d.portnum == portnums_pb2.ADMIN_APP:
            am = admin_pb2.AdminMessage()
            try:
                am.ParseFromString(d.payload)
                v = am.WhichOneof("payload_variant") or "NONE"
                self.add(f"{where}.admin", v)
            except Exception:
                self.add("errors", f"{where}.admin_decode")

    def try_decrypt(self, mp):
        nonce = struct.pack("<QII", mp.id, getattr(mp, "from"), 0)
        dec = Cipher(algorithms.AES(DEFAULT_PSK), modes.CTR(nonce)).decryptor()
        pt = dec.update(mp.encrypted) + dec.finalize()
        d = mesh_pb2.Data()
        try:
            d.ParseFromString(pt)
        except Exception:
            return None
        if d.portnum == 0 and not d.payload:
            return None
        return d


def frames(buf):
    """Yield protobuf payloads from a 0x94C3 framed byte stream; skip log text between frames."""
    i = 0
    out = []
    n = len(buf)
    while i + 4 <= n:
        if buf[i] == 0x94 and buf[i + 1] == 0xC3:
            ln = (buf[i + 2] << 8) | buf[i + 3]
            if i + 4 + ln > n:
                break
            out.append(bytes(buf[i + 4:i + 4 + ln]))
            i += 4 + ln
        else:
            i += 1
    return out


def run(pcaps, ports):
    t = Tally()
    streams = collections.defaultdict(dict)  # key -> {seq: data}
    gen = {}
    for path in pcaps:
        with open(path, "rb") as f:
            try:
                rd = dpkt.pcap.Reader(f)
            except ValueError:
                f.seek(0)
                rd = dpkt.pcapng.Reader(f)
            link = rd.datalink()
            for ts, raw in rd:
                try:
                    if link == dpkt.pcap.DLT_LINUX_SLL:
                        ip = dpkt.sll.SLL(raw).data
                    elif link == 276:  # LINUX_SLL2
                        ip = dpkt.sll2.SLL2(raw).data
                    elif link == dpkt.pcap.DLT_NULL or link == dpkt.pcap.DLT_LOOP:
                        ip = dpkt.loopback.Loopback(raw).data
                    else:
                        ip = dpkt.ethernet.Ethernet(raw).data
                except Exception:
                    t.add("errors", "link_decode")
                    continue
                if not isinstance(ip, (dpkt.ip.IP, dpkt.ip6.IP6)):
                    continue
                l4 = ip.data
                if isinstance(l4, dpkt.udp.UDP) and l4.dport == 4403:
                    mp = mesh_pb2.MeshPacket()
                    try:
                        mp.ParseFromString(bytes(l4.data))
                    except Exception:
                        t.add("errors", "ota_meshpacket_decode")
                        continue
                    t.add("ota.datagrams", "total")
                    t.packet("ota", mp)
                elif isinstance(l4, dpkt.tcp.TCP):
                    if l4.dport in ports:
                        d = "to_radio"
                        conn = (ip.src, l4.sport, ip.dst, l4.dport)
                    elif l4.sport in ports:
                        d = "from_radio"
                        conn = (ip.dst, l4.dport, ip.src, l4.sport)
                    else:
                        continue
                    if l4.flags & dpkt.tcp.TH_SYN:
                        # new connection on a possibly reused 4-tuple: new stream generation
                        gen[(d, conn)] = gen.get((d, conn), 0) + 1
                        continue
                    if not l4.data:
                        continue
                    key = (d, conn, gen.get((d, conn), 0))
                    streams[key].setdefault(l4.seq, bytes(l4.data))
    for key, segs in streams.items():
        d = key[0]
        buf = bytearray()
        nxt = None
        for seq in sorted(segs):
            data = segs[seq]
            if nxt is not None and seq < nxt:
                ov = nxt - seq
                if ov >= len(data):
                    continue
                data = data[ov:]
            elif nxt is not None and seq > nxt:
                t.add("errors", f"{d}.tcp_gap")
            buf += data
            nxt = seq + len(segs[seq])
        t.add("streams", d)
        msg = mesh_pb2.ToRadio if d == "to_radio" else mesh_pb2.FromRadio
        for fr in frames(buf):
            m = msg()
            try:
                m.ParseFromString(fr)
            except Exception:
                t.add("errors", f"{d}.frame_decode")
                continue
            v = m.WhichOneof("payload_variant") or "NONE"
            t.add(f"{d}.variant", v)
            if v == "packet":
                t.packet(d, m.packet)
            elif v == "config":
                t.add(f"{d}.config", m.config.WhichOneof("payload_variant") or "NONE")
            elif v == "moduleConfig":
                t.add(f"{d}.moduleConfig", m.moduleConfig.WhichOneof("payload_variant") or "NONE")
    return {k: dict(sorted(v.items())) for k, v in sorted(t.c.items())}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("pcaps", nargs="+")
    ap.add_argument("--ports", default="4403-4420")
    a = ap.parse_args()
    lo, hi = (int(x) for x in a.ports.split("-"))
    res = run(a.pcaps, set(range(lo, hi + 1)))
    with open(a.out, "w") as f:
        json.dump(res, f, indent=1)
    for k, v in res.items():
        print(k, sum(v.values()), "distinct", len(v))


if __name__ == "__main__":
    main()
