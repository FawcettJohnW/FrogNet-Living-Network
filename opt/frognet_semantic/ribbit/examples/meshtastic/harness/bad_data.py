#!/usr/bin/env python3
# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
"""bad_data.py -- Phase 1 step 4 (CONVERTING §3.5): good and bad data against N nodes whose only path is the shared RAM.

Every check is what a stock client observes on the client API (meshtastic Python library over TCP). Nodes are the
firmware under test, launched by meshtastic-mcp's own lab (native_node.build_lab, its supervisor relaunching a node after
the config reboot). Multicast must be blackholed by the caller (step4-bad-data.sh does it).

Scenarios, each timed and logged:
  S1 views       every node's client lists every other node (from the owners' user cells)
  S2 broadcast   a broadcast from each node reaches every other node's client
  S3 dm-ack      a DM with want_ack to a live node: the sender's client gets ROUTING error NONE
  S4 kill        SIGKILL one node and stop its supervisor: every other client's node list loses it (gone row)
  S5 dm-dead     a DM with want_ack to the dead node: what the sender's client gets, and when
  S6 restart     relaunch it: it is back in every other client's list, and how fast
  S7 malformed   a user cell with garbage bytes and one with a bad instance: no node falls over (all still answer)
  S8 position    a fixed position set on one node is in a fresh client of another node (dump or replay)
Writes results.json beside the logs. Exit 0 = every scenario PASS.
"""
import argparse
import importlib.util
import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from pubsub import pub
from meshtastic.tcp_interface import TCPInterface

MCP = Path(os.environ.get("MCP", "/home/claude/mesh/mcp"))
sys.path.insert(0, str(MCP / "src"))
from meshtastic_mcp.emulator import native_node  # noqa: E402

spec = importlib.util.spec_from_file_location("ci", MCP / "scripts" / "ci_device_mesh_e2e.py")
ci = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ci)

T0 = time.time()


class Relaunch(ci._Supervisor):
    """The lab's supervisor, but every launch keeps the node's state (no erase): a restart, not a new node."""

    def _run(self):
        while not self._stop.is_set():
            self.node.start(erase=False)
            proc = self.node._proc
            if proc is None:
                return
            while proc.poll() is None and not self._stop.is_set():
                time.sleep(0.5)
            if self._stop.is_set():
                return
            time.sleep(1)


def log(*a):
    print(f"[{time.time() - T0:8.3f}] [BAD_DATA]", *a, flush=True)


def connect(port, tries=60):
    for _ in range(tries):
        try:
            return TCPInterface(hostname="127.0.0.1", portNumber=port)
        except Exception as e:  # node rebooting
            last = e
            time.sleep(1)
    raise RuntimeError(f"cannot connect :{port}: {last!r}")


def others_seen(port, expect):
    i = connect(port)
    try:
        nums = {n["num"] for n in i.nodes.values()} - {i.myInfo.my_node_num}
        return nums & expect, i.myInfo.my_node_num
    finally:
        i.close()


def wait_until(fn, timeout, step=0.5):
    end = time.time() + timeout
    while time.time() < end:
        r = fn()
        if r:
            return r, time.time()
        time.sleep(step)
    return None, time.time()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--binary", required=True)
    ap.add_argument("--workdir", required=True)
    ap.add_argument("--count", type=int, default=4)
    ap.add_argument("--probe", required=True, help="bin/mtr-probe")
    ap.add_argument("--ram", required=True, help="HOST:PORT of the memory")
    a = ap.parse_args()
    wd = Path(a.workdir)
    res = {}
    nodes = native_node.build_lab(Path(a.binary), wd, count=a.count)
    sups = [ci._Supervisor(n) for n in nodes]
    try:
        for s in sups:
            s.start()
        log(f"launched {a.count} nodes on ports {[n.tcp_port for n in nodes]}")
        time.sleep(12)
        for n in nodes:
            n.configure()
        log("configured region (+UDP, blackholed); waiting for the config reboot")
        time.sleep(14)
        nums = {}
        for n in nodes:
            i = connect(n.tcp_port)
            nums[n.tcp_port] = i.myInfo.my_node_num
            i.close()
        allnums = set(nums.values())
        log("node numbers", {p: f"!{v:08x}" for p, v in nums.items()})

        # S1 views
        t = time.time()
        def all_views():
            return all(others_seen(n.tcp_port, allnums)[0] == allnums - {nums[n.tcp_port]} for n in nodes)
        ok, t1 = wait_until(all_views, 30)
        res["S1_views"] = {"pass": bool(ok), "seconds": round(t1 - t, 2),
                           "views": {f"!{nums[n.tcp_port]:08x}": sorted(f"!{x:08x}" for x in others_seen(n.tcp_port, allnums)[0]) for n in nodes}}
        log("S1", res["S1_views"])

        # S2 broadcast from each node to every other client
        got = {n.tcp_port: set() for n in nodes}
        ifaces = {n.tcp_port: connect(n.tcp_port) for n in nodes}
        lock = threading.Lock()
        def on_text(packet=None, interface=None, **_):
            txt = packet.get("decoded", {}).get("text", "")
            if txt.startswith("BD-"):
                with lock:
                    for p, i in ifaces.items():
                        if i is interface:
                            got[p].add(txt)
        pub.subscribe(on_text, "meshtastic.receive.text")
        time.sleep(1)
        sent = []
        for n in nodes:
            tok = f"BD-{nums[n.tcp_port]:08x}"
            sent.append(tok)
            ifaces[n.tcp_port].sendText(tok, wantAck=False)
            time.sleep(2.2)  # stock's 2 s client text rate limit
        def all_got():
            with lock:
                return all(got[n.tcp_port] >= set(sent) - {f"BD-{nums[n.tcp_port]:08x}"} for n in nodes)
        ok, _ = wait_until(all_got, 20)
        res["S2_broadcast"] = {"pass": bool(ok), "received": {f"!{nums[p]:08x}": sorted(v) for p, v in got.items()}}
        log("S2", res["S2_broadcast"])
        pub.unsubscribe(on_text, "meshtastic.receive.text")
        for i in ifaces.values():
            i.close()   # meshtasticd serves one TCP client at a time: nothing stays open across checks
        ifaces = {}

        def dm(src_port, dest, payload, wait_s):
            """One connection for the send and the wait; returns (routing error or None, seconds, from)."""
            i = connect(src_port)
            r, e = {}, threading.Event()
            def cb(p):
                r["p"] = p; r["t"] = time.time(); e.set()
            t = time.time()
            i.sendData(payload, destinationId=dest, portNum=1, wantAck=True, onResponse=cb, onResponseAckPermitted=True)
            e.wait(wait_s)
            i.close()
            if not r:
                return None, round(time.time() - t, 2), None
            return (r["p"].get("decoded", {}).get("routing", {}).get("errorReason", "NONE"), round(r["t"] - t, 2),
                    f"!{r['p'].get('from', 0):08x}")

        # S3 DM with want_ack to a live node
        src, dst = nodes[0], nodes[1]
        err, secs, frm = dm(src.tcp_port, nums[dst.tcp_port], b"dm-live", 60)
        res["S3_dm_ack"] = {"pass": err == "NONE", "error": err, "seconds": secs, "from": frm}
        log("S3", res["S3_dm_ack"])

        # S8 a fixed position set on one node reaches a fresh client of another (config dump / replay)
        # position is shared only on a channel with a position precision; the lab's primary has none (stock skips the
        # broadcast: "position sharing disabled on all channels"), so give it full precision and a 30 s interval
        i = connect(src.tcp_port)
        ln = i.localNode
        ln.channels[0].settings.module_settings.position_precision = 32
        ln.writeChannel(0)
        ln.localConfig.position.position_broadcast_secs = 30
        ln.localConfig.position.position_broadcast_smart_enabled = False
        ln.writeConfig("position")
        ln.setFixedPosition(47.4668, -122.3405, 120)
        i.close()
        t = time.time()
        def pos_seen():
            j = connect(dst.tcp_port)
            try:
                time.sleep(1.5)  # the satellite replay follows config_complete_id, as stock's does
                p = (j.nodesByNum.get(nums[src.tcp_port]) or {}).get("position") or {}
                return ({k: p.get(k) for k in ("latitude", "longitude", "altitude", "precisionBits")}
                        if abs(p.get("latitude", 0) - 47.4668) < 0.05 else None)
            finally:
                j.close()
        p, t1 = wait_until(pos_seen, 120, step=2)
        # then a fresh client of the other node, 20 s later (after the config reboot), still gets it at connect
        time.sleep(20)
        again = pos_seen()
        res["S8_position"] = {"pass": bool(p) and bool(again), "seconds": round(t1 - t, 2), "position": p,
                              "fresh_client_20s_later": again}
        log("S8", res["S8_position"])

        # S4 kill one node for good
        victim = nodes[-1]
        vnum = nums[victim.tcp_port]
        sups[-1].stop()
        if victim._proc and victim._proc.poll() is None:
            victim._proc.send_signal(signal.SIGKILL)
        t = time.time()
        log(f"S4 killed !{vnum:08x} (SIGKILL, supervisor stopped)")
        rest = nodes[:-1]
        def gone_everywhere():
            return all(vnum not in others_seen(n.tcp_port, allnums)[0] for n in rest)
        ok, t1 = wait_until(gone_everywhere, 30)
        res["S4_kill"] = {"pass": bool(ok), "seconds": round(t1 - t, 2)}
        log("S4", res["S4_kill"])

        # S5 DM with want_ack to the dead node: what the sender's client gets, and when
        err, secs, frm = dm(src.tcp_port, vnum, b"dm-dead", 180)
        res["S5_dm_dead"] = {"observed": err, "seconds": secs, "from": frm, "pass": err is not None and err != "NONE"}
        log("S5", res["S5_dm_dead"])

        # S6 restart it
        sups[-1] = Relaunch(victim)   # same state, no erase: the same node comes back
        t = time.time()
        sups[-1].start()
        ok, t1 = wait_until(lambda: all(vnum in others_seen(n.tcp_port, allnums)[0] for n in rest), 60)
        res["S6_restart"] = {"pass": bool(ok), "seconds": round(t1 - t, 2)}
        log("S6", res["S6_restart"])

        # S7 malformed cells (not applicable to stock: no memory)
        writes = [] if not a.ram else [["write", a.ram, "deadbeef", "user", "ffffffffffff"], ["write", a.ram, "0", "pos", "zz"]]
        if not a.ram:
            log("S7 no cells to write (stock, no memory): only the answering check runs")
        for args in writes:
            r = subprocess.run([a.probe] + args, capture_output=True, text=True, timeout=30)
            log("S7 wrote", args, r.stdout.strip(), r.stderr.strip()[-200:])
        time.sleep(3)
        alive = {}
        for n in nodes:
            try:
                i = connect(n.tcp_port, tries=10)
                alive[f"!{nums[n.tcp_port]:08x}"] = True
                i.close()
            except Exception as e:
                alive[f"!{nums[n.tcp_port]:08x}"] = repr(e)
        res["S7_malformed"] = {"pass": all(v is True for v in alive.values()), "answering": alive}
        log("S7", res["S7_malformed"])
    finally:
        for s in sups:
            s.stop()
        (wd / "results.json").write_text(json.dumps(res, indent=1))
    verdict = all(v.get("pass") for v in res.values()) and len(res) == 8
    log("VERDICT", "PASS" if verdict else "FAIL", {k: v.get("pass") for k, v in res.items()})
    return 0 if verdict else 1


if __name__ == "__main__":
    sys.exit(main())
