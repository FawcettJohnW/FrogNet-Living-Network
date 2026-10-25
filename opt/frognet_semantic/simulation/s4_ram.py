#!/usr/bin/env python3
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#                                                              #
#  SPDX-License-Identifier: GPL-2.0-only                       #
#                                                              #
#  See COPYRIGHT and LICENSE at the root of this tree.         #
################################################################
"""
s4_ram.py - [S4_RAM_ARM_V1] the shared-RAM arm on the TIER 4 bed (s4_bed.py), the same physical graph IS-IS runs on.

What runs, all of it the real thing except the one line marked:
  hub      rina-host (the region's RAM manager) on 198.51.100.1:8840, the region's site key (map_site), fdf-hub (the
           WireGuard concentrator: one wireguard-go interface wg-D, a peer per live member, allowed-ips from the
           verified mappings) and the hub's own fdf-client
  every    fdf-facts --watch (the node's rows from its own kernel), fdf-client (announce, re-announce on change, maps
  node     signed and registered through its session), s4_ram_agent.py (region.py + graph_apply: the plan into the
           kernel on every held-read event; an egress also brings up its hub channel from region.my_channels() --
           THE STAND-IN for the tunnel daemon's bring-up)

The underlay, as FrogNet sees it: an egress's hub link is its Internet uplink (default route via the hub's address
on it; the egress NATs its LAN out of it, as a FrogNet gateway does); a child's default is its egress (what the DHCP
lease gives); the RAM manager's address is reached over that underlay. The hub's machine forwards nothing from the
underlay into 10/8 (the Internet does not route RFC 1918): a /24 is reached through the tunnels or not at all.
A lease is in the egress's dnsmasq lease file (written here: the bed has no dnsmasq), so fdf-facts writes the given
rows and the child's held row agrees on (ip, mac).
"""
import os
import secrets
import subprocess
import time

from s4_bed import NS, sh

RAM_IP = "198.51.100.1"
RAM_PORT = 8840
API = "/FawcettInnovations.RINA.ram_interface.php"
AREA = "D"
HUB_NAME = "hub"
WG_PORT = 51820
_HERE = os.path.dirname(os.path.abspath(__file__))


def wg_if(n):
    return f"wg-{n}"


class Ram:
    def __init__(self, bed, work, bins=None, fdf=None):
        """bins: rina-host, fdf-client, fdf-events, wireguard-go. fdf: the FD&F tree (bin/fdf-hub, bin/fdf-facts,
        lib/python)."""
        self.bed, self.work = bed, work
        arch = os.uname().machine
        self.bins = bins or os.environ.get("S4_RAM_BIN", f"/opt/fdf/experiments/day-claims/ram/bin/{arch}")
        self.fdf = fdf or os.environ.get("S4_FDF", "/opt/fdf/experiments/day-claims/ram/fdf")
        self.frognet = os.path.dirname(_HERE)
        self.key = secrets.token_hex(16)
        self.procs = []
        self.stopping = False
        self.restarts = {}
        for b in ("rina-host", "fdf-client", "fdf-events", "wireguard-go"):
            if not os.access(os.path.join(self.bins, b), os.X_OK):
                raise RuntimeError(f"{self.bins}/{b} is missing: the RAM arm needs it")
        for f in ("bin/fdf-hub", "bin/fdf-facts", "lib/python/fdf_graph.py"):
            if not os.path.exists(os.path.join(self.fdf, f)):
                raise RuntimeError(f"{self.fdf}/{f} is missing: the RAM arm needs it")
        for t in ("wg", "nft"):
            if subprocess.run(["sh", "-c", f"command -v {t}"], capture_output=True).returncode != 0:
                raise RuntimeError(f"`{t}` is not installed here: the RAM arm needs it")

    def env(self, n, **more):
        d = os.path.join(self.work, n)
        e = {k: v for k, v in os.environ.items() if not k.startswith(("RINA_", "FDF_"))}
        e.update(PATH=f"{self.bins}:{self.fdf}/bin:" + os.environ.get("PATH", "/usr/sbin:/usr/bin:/sbin:/bin"),
                 RINA_RAM=f"{RAM_IP}:{RAM_PORT}", RINA_AREA=AREA, RINA_SITE_KEY=self.key, FDF_BIN=self.bins,
                 FDF_PY=f"{self.fdf}/lib/python", FDF_FACTS=f"{d}/facts.json", FDF_VAR=d, FDF_WG_IMPL="wireguard-go",
                 FROGNET_DIR=self.frognet, FROGNET_REGION_HOSTS=f"{d}/region_hosts", FROGNET_GEN=f"{d}/gen",
                 FROGNET_WORLD_GEN=f"{d}/world_gen", FDF_NAME=n, PYTHONUNBUFFERED="1")
        e.update(more)
        return e

    # [S4_RESTART_AS_SYSTEMD_V1] each process restarts as its unit does on a FrogNet node (fdf-join: fdf-client
    # Restart=always RestartSec=2, fdf-facts 5; fdf-hub.service 2; the tunnel daemon's unit, for the agent, 5)
    RESTART = {"fdf-client": 2, "fdf-facts": 5, "fdf-hub": 2, "agent": 5, "rina-host": 2}

    def spawn(self, n, name, argv, **more):
        import threading
        d = os.path.join(self.work, n)
        out = open(os.path.join(d, f"{name}.log"), "a")
        env = self.env(n, **more)

        def run():
            p = subprocess.Popen(["ip", "netns", "exec", NS + n] + argv, env=env, stdout=out,
                                 stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
            self.procs.append((n, name, p))
            return p
        p = run()

        def supervise(p=p):
            while True:
                rc = p.wait()
                if self.stopping:
                    return
                out.write(f"[S4-SUPERVISOR] {time.strftime('%H:%M:%S')} {name} ended rc={rc}; restarting in "
                          f"{self.RESTART.get(name, 2)} s\n"); out.flush()
                self.restarts[name] = self.restarts.get(name, 0) + 1
                time.sleep(self.RESTART.get(name, 2))
                if self.stopping:
                    return
                p = run()
        threading.Thread(target=supervise, daemon=True).start()
        return p

    def call(self, body):
        import json
        r = sh("curl", "-s", "-m", "10", "-X", "POST", "-d", json.dumps(body),
               f"http://{RAM_IP}:{RAM_PORT}{API}?op=rina", ns="hub")
        return json.loads(r.stdout or "{}")

    def underlay(self):
        p = self.bed.p
        sh("ip", "addr", "add", f"{RAM_IP}/32", "dev", "lo", ns="hub")
        sh("nft", "add", "table", "inet", "s4u", ns="hub")
        sh("nft", "add", "chain", "inet", "s4u", "s4fwd", "{ type filter hook forward priority 0 ; }", ns="hub")
        sh("nft", "add", "rule", "inet", "s4u", "s4fwd", "iifname", "hl*", "ip", "daddr", "10.0.0.0/8", "drop", ns="hub")
        for n in self.bed.mine:
            d = p["nodes"][n]
            os.makedirs(os.path.join(self.work, n), exist_ok=True)
            if d["role"] == "egress":
                i = int(n[1:])
                sh("ip", "route", "add", "default", "via", f"172.30.{i}.1", "dev", "hl0", ns=n)
                sh("nft", "add", "table", "ip", "s4nat", ns=n)
                sh("nft", "add", "chain", "ip", "s4nat", "s4post", "{ type nat hook postrouting priority 100 ; }", ns=n)
                sh("nft", "add", "rule", "ip", "s4nat", "s4post", "oifname", "hl0", "ip", "saddr", "10.0.0.0/8",
                   "masquerade", ns=n)
            elif d["role"] == "child":
                e = d["egress"]
                sh("ip", "route", "add", "default", "via", f"10.20.{e[1:]}.1", "dev", "eth1", "metric", "600", ns=n)
        # the leases each egress gave (its dnsmasq's file, as fdf-facts reads it)
        exp = int(time.time()) + 86400
        for n in self.bed.mine:
            d = p["nodes"][n]
            if d["role"] != "egress":
                continue
            rows = []
            for c, cd in p["nodes"].items():
                if cd.get("egress") == n and c in self.bed.mine:
                    mac = sh("cat", "/sys/class/net/eth1/address", ns=c).stdout.strip()
                    rows.append(f"{exp} {mac} {cd['ifaces']['eth1'].split('/')[0]} {c} *\n")
            open(os.path.join(self.work, n, "leases"), "w").write("".join(rows))
        for n in self.bed.mine:
            if not os.path.exists(os.path.join(self.work, n, "leases")):
                open(os.path.join(self.work, n, "leases"), "w").close()

    def start(self):
        t0 = time.time()
        self.underlay()
        h = os.path.join(self.work, "hub")
        self.spawn("hub", "rina-host", [f"{self.bins}/rina-host", "--listen", f"{RAM_IP}:{RAM_PORT}", "--api", API],
                   RINA_REGION_FILE=f"{h}/region.perm", RINA_NET_CONFIG=f"{h}/net.json")   # none: this machine's own is not the bed's
        for _ in range(250):
            if sh("bash", "-c", f"exec 3<>/dev/tcp/{RAM_IP}/{RAM_PORT}", ns="hub", check=False).returncode == 0:
                break
            time.sleep(0.02)
        else:
            raise RuntimeError(f"rina-host did not listen in 5 s: see {h}/rina-host.log")
        if not self.call({"op": "map_site", "name": AREA, "key": self.key}).get("ok"):
            raise RuntimeError("map_site refused: see the hub's rina-host.log")
        self.spawn("hub", "fdf-hub", ["python3", f"{self.fdf}/bin/fdf-hub", "--area", AREA, "--iface", "wg-D",
                                      "--endpoint", f"{RAM_IP}:{WG_PORT}", "--name", HUB_NAME, "--key", f"{h}/wg.key",
                                      "--listen", str(WG_PORT), "--facts", f"{h}/facts.json"])
        for _ in range(500):
            if os.path.exists(f"{h}/facts.json"):
                break
            time.sleep(0.02)
        else:
            raise RuntimeError(f"fdf-hub wrote no facts in 10 s: see {h}/fdf-hub.log")
        self.spawn("hub", "fdf-client", [f"{self.bins}/fdf-client"])
        for n in self.bed.mine:
            if n == "hub":
                continue
            d = os.path.join(self.work, n)
            more = {}
            if self.bed.p["nodes"][n]["role"] == "egress":
                key = sh("wg", "genkey").stdout.strip()
                open(f"{d}/wg.key", "w").write(key + "\n")
                os.chmod(f"{d}/wg.key", 0o600)
                more = dict(S4_WG=wg_if(n), S4_WG_KEY=f"{d}/wg.key")
            ping = f"172.30.{n[1:]}.1" if more else ""
            env = dict(FDF_LEASES=f"{d}/leases", **({"FDF_UPLINK_PING": ping} if ping else {}))
            self.spawn(n, "fdf-facts", ["python3", f"{self.fdf}/bin/fdf-facts", "--watch"], **env)
            self.spawn(n, "agent", ["python3", os.path.join(_HERE, "s4_ram_agent.py")], S4_ME=n, **more)
            self.spawn(n, "fdf-client", [f"{self.bins}/fdf-client"])
        return t0

    def usage(self):
        """{node: (cpu_ms, rss_kb)} over every process in the node's namespace (this arm's processes, nothing else)."""
        tick = os.sysconf("SC_CLK_TCK")
        out = {}
        for n in self.bed.mine:
            cpu = rss = 0
            for pid in sh("ip", "netns", "pids", NS + n, check=False).stdout.split():
                try:
                    f = open(f"/proc/{pid}/stat").read().rsplit(")", 1)[1].split()
                    cpu += (int(f[11]) + int(f[12])) * 1000 // tick
                    rss += int(next(l for l in open(f"/proc/{pid}/status") if l.startswith("VmRSS")).split()[1])
                except (OSError, StopIteration):
                    pass
            out[n] = (cpu, rss)
        return out

    def stop(self):
        self.stopping = True
        for _n, _name, p in self.procs:
            if p.poll() is None:
                p.terminate()
        for _n, _name, p in self.procs:
            try:
                p.wait(5)
            except subprocess.TimeoutExpired:
                p.kill()
