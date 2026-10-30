#!/usr/bin/env python3
"""baseline: stock-meshtasticd Phase 0 baseline numbers from captures already taken.

A. Over-the-air exchanges (RepeaterTastic capture, 2 nodes, UDP multicast sim radio):
   per exchange, transmissions, MeshPacket datagram bytes, LoRa frame bytes
   (16-byte radio header + encrypted payload), LongFast airtime (Semtech formula,
   SF11 / BW 250 kHz / CR 4:5 / 16-symbol preamble, explicit header, CRC on,
   low-data-rate optimisation off), and wall time first->last datagram.
B. Client API session cost (all oracle pcaps): per TCP API connection, the
   want_config_id -> config_complete_id dump: FromRadio frames, bytes, duration.

Usage: baseline.py OUT.json RT_VECTOR_DIR PCAP...
"""
import collections
import json
import math
import statistics
import sys
from pathlib import Path

import dpkt
from meshtastic.protobuf import mesh_pb2


def lora_airtime_ms(payload_len, sf=11, bw=250e3, cr=1, preamble=16, crc=1, ih=0, de=0):
    tsym = (2 ** sf) / bw
    npay = 8 + max(math.ceil((8 * payload_len - 4 * sf + 28 + 16 * crc - 20 * ih) / (4 * (sf - 2 * de))) * (cr + 4), 0)
    return ((preamble + 4.25) + npay) * tsym * 1000


EXCHANGES = {
    "text_broadcast": ["text_broadcast", "text_broadcast_relayed"],
    "dm_want_ack (PKI)": ["dm_text", "routing_ack", "routing_ack_zero_hop"],
    "position_request -> NAK": ["position_request", "routing_nak"],
    "telemetry request -> reply (PKI)": ["telemetry_request", "telemetry_reply"],
    "traceroute request -> reply": ["traceroute_request", "traceroute_reply"],
    "nodeinfo_broadcast": ["nodeinfo_broadcast"],
}


def ota(vdir):
    vdir = Path(vdir)
    times = collections.defaultdict(list)
    for line in open(vdir / "capture.jsonl"):
        d = json.loads(line)
        times[(d["from"], d["id"], d.get("relay_node"))].append(d["t"])
    out = {}
    for name, parts in EXCHANGES.items():
        rows = []
        for p in parts:
            f = vdir / f"{p}.json"
            if not f.exists():
                rows = None
                break
            v = json.loads(f.read_text())
            dg = bytes.fromhex(v["udp"]["datagram_hex"])
            enc = bytes.fromhex(v["encrypted_hex"])
            lora = 16 + len(enc)
            t = times.get((v["from"], v["id"], v["relay_node"])) or [None]
            rows.append({"vector": p, "datagram_bytes": len(dg), "lora_bytes": lora,
                         "longfast_airtime_ms": round(lora_airtime_ms(lora), 1), "t": t[0]})
        if rows is None:
            out[name] = "not captured"
            continue
        out[name] = {
            "transmissions": len(rows),
            "datagram_bytes": sum(r["datagram_bytes"] for r in rows),
            "lora_bytes": sum(r["lora_bytes"] for r in rows),
            "longfast_airtime_ms": round(sum(r["longfast_airtime_ms"] for r in rows), 1),
            "per_tx": rows,
        }
    return out


def frames_ts(segs):
    """segs: sorted list of (seq, ts, data). Returns [(ts_of_frame_end, payload)]."""
    buf = bytearray()
    owners = []  # (end_offset, ts)
    nxt = None
    for seq, ts, data in segs:
        if nxt is not None and seq < nxt:
            ov = nxt - seq
            if ov >= len(data):
                continue
            data = data[ov:]
            seq = nxt
        buf += data
        owners.append((len(buf), ts))
        nxt = seq + len(data)
    out, i, oi = [], 0, 0
    while i + 4 <= len(buf):
        if buf[i] == 0x94 and buf[i + 1] == 0xC3:
            ln = (buf[i + 2] << 8) | buf[i + 3]
            end = i + 4 + ln
            if end > len(buf):
                break
            while owners[oi][0] < end:
                oi += 1
            out.append((owners[oi][1], bytes(buf[i + 4:end])))
            i = end
        else:
            i += 1
    return out


def api(pcaps, ports=range(4403, 4521)):
    sessions = []
    for path in pcaps:
        streams = collections.defaultdict(dict)
        gen = {}
        with open(path, "rb") as f:
            rd = dpkt.pcap.Reader(f)
            for ts, raw in rd:
                try:
                    ip = dpkt.sll2.SLL2(raw).data if rd.datalink() == 276 else dpkt.sll.SLL(raw).data
                except Exception:
                    continue
                l4 = getattr(ip, "data", None)
                if not isinstance(l4, dpkt.tcp.TCP):
                    continue
                if l4.dport in ports:
                    d, conn = "to", (ip.src, l4.sport, l4.dport)
                elif l4.sport in ports:
                    d, conn = "from", (ip.dst, l4.dport, l4.sport)
                else:
                    continue
                if l4.flags & dpkt.tcp.TH_SYN:
                    gen[(d, conn)] = gen.get((d, conn), 0) + 1
                    continue
                if l4.data:
                    streams[(conn, gen.get((d, conn), 0), d)].setdefault(l4.seq, (ts, bytes(l4.data)))
        conns = {(k[0], k[1]) for k in streams}
        for c in conns:
            to = frames_ts(sorted((s, t, b) for s, (t, b) in streams.get((c[0], c[1], "to"), {}).items()))
            fr = frames_ts(sorted((s, t, b) for s, (t, b) in streams.get((c[0], c[1], "from"), {}).items()))
            for ts, b in to:
                m = mesh_pb2.ToRadio()
                try:
                    m.ParseFromString(b)
                except Exception:
                    continue
                if m.WhichOneof("payload_variant") != "want_config_id":
                    continue
                nonce = m.want_config_id
                n = nbytes = 0
                done = None
                for fts, fb in fr:
                    if fts < ts:
                        continue
                    fm = mesh_pb2.FromRadio()
                    try:
                        fm.ParseFromString(fb)
                    except Exception:
                        continue
                    n += 1
                    nbytes += len(fb) + 4
                    if fm.WhichOneof("payload_variant") == "config_complete_id" and fm.config_complete_id == nonce:
                        done = fts
                        break
                if done is not None:
                    sessions.append({"pcap": Path(path).stem, "frames": n, "bytes": nbytes,
                                     "ms": round((done - ts) * 1000, 1), "nonce": nonce})
    by = collections.defaultdict(list)
    for s in sessions:
        by[s["pcap"]].append(s)
    summ = {}
    for p, ss in sorted(by.items()):
        summ[p] = {k: {"min": min(x[k] for x in ss), "median": statistics.median(x[k] for x in ss),
                       "max": max(x[k] for x in ss)} for k in ("frames", "bytes", "ms")}
        summ[p]["dumps"] = len(ss)
    return summ


def main():
    out, vdir, *pcaps = sys.argv[1:]
    res = {"ota_exchanges_rt_capture": ota(vdir), "client_api_config_dump": api(pcaps)}
    json.dump(res, open(out, "w"), indent=1)
    for k, v in res["ota_exchanges_rt_capture"].items():
        if isinstance(v, dict):
            print(f"OTA {k}: tx={v['transmissions']} lora_bytes={v['lora_bytes']} LongFast_airtime={v['longfast_airtime_ms']}ms")
        else:
            print(f"OTA {k}: {v}")
    for k, v in res["client_api_config_dump"].items():
        print(f"API {k}: dumps={v['dumps']} frames(med)={v['frames']['median']} bytes(med)={v['bytes']['median']} ms(min/med/max)={v['ms']['min']}/{v['ms']['median']}/{v['ms']['max']}")


if __name__ == "__main__":
    main()
