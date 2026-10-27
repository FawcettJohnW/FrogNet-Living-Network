#!/usr/bin/env python3
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#                                                              #
#  SPDX-License-Identifier: GPL-2.0-only                       #
#                                                              #
#  See COPYRIGHT and LICENSE at the root of this tree.         #
################################################################
"""
s4_isis.py - [S4_ISIS_ARM_V1] the IS-IS arm on the TIER 4 bed (s4_bed.py): one real FRR zebra + isisd per node, in
the node's own namespace, configured from the plan. Level 2 only, one area; every LAN and hub link is a circuit
(LAN: broadcast, with a DIS; hub link: point-to-point); each served /24 is advertised from a passive lan0 (children)
or from the LAN itself (egress). The hub is a router like any other node.

Two timer profiles, both reported (FRR's defaults are not what a tuned network runs):
  default   FRR 9.1 as shipped (lsp-gen-interval 30 s, spf-interval 1 s, no spf-delay-ietf)
  tuned     lsp-gen-interval 1, spf-interval 1, spf-delay-ietf 50/200/5000/10000/500 ms (RFC 8405 backoff),
            hello-interval 1 s x multiplier 3 on every circuit (hold 3 s; default is 10 s x 3)

Binaries: FRR_BIN (default: the day-claims package's isis/bin/<arch>), yang models from the path compiled into them.
"""
import os
import subprocess
import time

from s4_bed import NS, sh

# per-circuit lines each profile adds (tuned: hold time 3 s instead of 30 s; BFD would be faster still, and is not built)
CIRCUIT = {"default": [], "tuned": [" isis hello-interval 1", " isis hello-multiplier 3"]}
PROFILES = {
    "default": [],
    "tuned": [" lsp-gen-interval 1", " spf-interval 1",
              " spf-delay-ietf init-delay 50 short-delay 200 long-delay 5000 holddown 10000 time-to-learn 500"],
}


class Isis:
    def __init__(self, bed, work, profile="tuned", frr_bin=None):
        self.bed, self.work, self.profile = bed, work, profile
        arch = os.uname().machine
        self.bin = frr_bin or os.environ.get("FRR_BIN", f"/opt/fdf/experiments/day-claims/isis/bin/{arch}")
        self.pids = {}
        # [S4_SOCK_SHORT_V1] zebra's API and the vty sockets are unix sockets: their paths must fit in 108 bytes, so they
        # live in /run/s4q/<node>/ (removed at stop), the logs and configs in the work directory
        self.sock = os.environ.get("S4_SOCK", "/run/s4q")

    def sysid(self, n):
        # a unique 6-byte system id from the node's index: hub 9999, egress i -> i, child k -> 1000 + k
        v = 9999 if n == "hub" else int(n[1:]) + (0 if n.startswith("e") else 1000)
        h = f"{v:012d}"
        return f"{h[0:4]}.{h[4:8]}.{h[8:12]}"

    def conf(self, n):
        d = self.bed.p["nodes"][n]
        # [S4_TIMERS_FIRST_V1] the timers go BEFORE is-type: setting is-type schedules an LSP regeneration at the
        # lsp-gen-interval in force at that moment (30 s if the timers come after), and every trigger after it (circuit
        # up, adjacency up) finds that one pending and waits for it. FRR's own config writer puts is-type right after
        # net, so the default profile keeps that order and that 30 s: it is how FRR as shipped comes up.
        dbg = os.environ.get("S4_ISIS_DEBUG", "")         # e.g. "adj-packets update-packets": logged at debugging
        lines = [f"hostname {n}", f"log file {self.work}/{n}/isisd.log {'debugging' if dbg else 'informational'}"] + \
                [f"debug isis {x}" for x in dbg.split()] + ["router isis D",
                 f" net 49.0001.{self.sysid(n)}.00"] + PROFILES[self.profile] + [" is-type level-2-only", " metric-style wide", "!"]
        for i in d["ifaces"]:
            lines += [f"interface {i}", " ip router isis D"]
            if i in ("lan0", "ram0"):
                lines.append(" isis passive")
            elif i.startswith(("hl", "rn")):
                lines.append(" isis network point-to-point")
            if i not in ("lan0", "ram0"):
                lines += CIRCUIT[self.profile]
            lines.append("!")
        return "\n".join(lines) + "\n"

    def start(self):
        t0 = time.time()
        for n in self.bed.mine:
            d = os.path.join(self.work, n)
            os.makedirs(d, exist_ok=True)
            s = os.path.join(self.sock, n)
            os.makedirs(s, exist_ok=True)
            open(f"{d}/zebra.conf", "w").write(f"hostname {n}\nlog file {d}/zebra.log informational\n")
            open(f"{d}/isisd.conf", "w").write(self.conf(n))
            common = ["-d", "-u", "root", "-g", "root", "-z", f"{s}/zserv.api", "--vty_socket", s]
            if self._daemon(n, "zebra", common + ["-f", f"{d}/zebra.conf", "-i", f"{d}/zebra.pid"]) != 0:
                raise RuntimeError(f"zebra did not start on {n}: see {d}/zebra.out")
        for n in self.bed.mine:
            d, s = os.path.join(self.work, n), os.path.join(self.sock, n)
            for _ in range(250):                                      # zebra's API socket, before isisd connects
                if os.path.exists(f"{s}/zserv.api"):
                    break
                time.sleep(0.02)
            else:
                raise RuntimeError(f"zebra on {n} made no API socket in 5 s: see {d}/zebra.log")
            common = ["-d", "-u", "root", "-g", "root", "-z", f"{s}/zserv.api", "--vty_socket", s]
            if self._daemon(n, "isisd", common + ["-f", f"{d}/isisd.conf", "-i", f"{d}/isisd.pid"]) != 0:
                raise RuntimeError(f"isisd did not start on {n}: see {d}/isisd.out")
        for n in self.bed.mine:
            d = os.path.join(self.work, n)
            # [S4_PIDFILE_NONEMPTY_V1] a -d daemon creates its pidfile, then writes the pid a moment later: wait for
            # CONTENT, not mere existence, or read() returns '' and int('') crashes the whole run (seen on a loaded box)
            self.pids[n] = [self._pidfile(f"{d}/{x}.pid", n, d) for x in ("zebra", "isisd")]
        return t0

    def _pidfile(self, path, n, d):
        for _ in range(250):
            try:
                s = open(path).read().strip()
                if s:
                    return int(s)
            except OSError:
                pass
            time.sleep(0.02)
        raise RuntimeError(f"{os.path.basename(path)} on {n} wrote no pid in 5 s: see {d}/isisd.log, {d}/zebra.log")

    def _daemon(self, n, prog, args):
        """FRR daemonises: its output goes to a file, never to a pipe this process would wait on until the daemon ends."""
        out = open(os.path.join(self.work, n, f"{prog}.out"), "w")
        return subprocess.run(["ip", "netns", "exec", NS + n, f"{self.bin}/{prog}"] + args, stdout=out,
                              stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL).returncode

    def usage(self):
        """{node: (cpu_ms, rss_kb)} of its zebra + isisd."""
        tick = os.sysconf("SC_CLK_TCK")
        out = {}
        for n, ps in self.pids.items():
            cpu = rss = 0
            for pid in ps:
                try:
                    f = open(f"/proc/{pid}/stat").read().rsplit(")", 1)[1].split()
                    cpu += (int(f[11]) + int(f[12])) * 1000 // tick
                    rss += int(next(l for l in open(f"/proc/{pid}/status") if l.startswith("VmRSS")).split()[1])
                except (OSError, StopIteration):
                    pass
            out[n] = (cpu, rss)
        return out

    def stop(self):
        import shutil
        for ps in self.pids.values():
            for pid in ps:
                subprocess.run(["kill", str(pid)], capture_output=True)
        time.sleep(0.5)
        shutil.rmtree(self.sock, ignore_errors=True)
