#!/usr/bin/env python3
# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
#
# bed.py -- the observer-visibility bed: real-kernel network namespaces in a star through one router.
#
#   ov-p1 .. ov-p8   participants        eth0 10.77.K.2/29 (K = 1..8), default route via 10.77.K.1
#   ov-srv           the server under test eth0 10.77.20.2/29
#   ov-ctl           the controller and the control region's chat-ram, eth0 10.77.30.2/29
#   ov-rt            the router: one veth r<K> per namespace (10.77.K.1/29), ip_forward on
#
# A /29 per link leaves 10.77.K.3 free for the address-change stress phase. netem goes on the router's side of
# p5..p8 (the Internet-side participants). WireGuard (wireguard-go: the same implementation on every machine) is set up
# only when the condition asks: srv wg0 10.99.0.254/24 on UDP 51820, each pN wg0 10.99.0.N/24 with srv its one peer.
#
#   bed.py up [--wg] [--netem "delay 100ms" ...]     bed.py down
import argparse, ctypes, ctypes.util, os, subprocess, sys

BUILD = "obsvis-bed-2026-11-06-r2"
PARTS = ["p%d" % i for i in range(1, 9)]
K = dict({p: int(p[1:]) for p in PARTS}, srv=20, ctl=30)
NS = lambda n: "ov-" + n
ADDR = lambda n, h=2: "10.77.%d.%d" % (K[n], h)
WG = lambda n: "10.99.0.254" if n == "srv" else "10.99.0.%d" % K[n]
CTL = ADDR("ctl")
WGIF = lambda n: "wg-" + n    # wireguard-go's control socket (/var/run/wireguard/<if>.sock) is shared by every namespace
INTERNET = ["p5", "p6", "p7", "p8"]


class Inotify:
    """inotify on one directory: read() blocks until something in it is created, written, moved or removed"""
    def __init__(self, d):
        self.libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6", use_errno=True)
        self.fd = self.libc.inotify_init1(0o2000000)
        if self.fd < 0 or self.libc.inotify_add_watch(self.fd, d.encode(), 0x2 | 0x8 | 0x80 | 0x100 | 0x200 | 0x40) < 0:
            raise OSError(ctypes.get_errno(), "inotify " + d)
    def read(self):
        os.read(self.fd, 65536)
    def close(self):
        os.close(self.fd)


def sh(*a, ns=None, check=True, quiet=False):
    cmd = (["ip", "netns", "exec", NS(ns)] if ns else []) + [str(x) for x in a]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError("%s: %s" % (" ".join(cmd), (r.stderr or r.stdout).strip()))
    return r.stdout


def exists():
    return NS("rt") in sh("ip", "netns", "list")


def down():
    for n in PARTS + ["srv", "ctl", "rt"]:
        if NS(n) in sh("ip", "netns", "list"):
            for pid in sh("ip", "netns", "pids", NS(n), check=False).split():
                subprocess.run(["kill", "-9", pid], capture_output=True)
            sh("ip", "netns", "del", NS(n), check=False)
        if os.path.exists("/var/run/wireguard/%s.sock" % WGIF(n)):
            os.unlink("/var/run/wireguard/%s.sock" % WGIF(n))


def up(wg=False, netem=None):
    if exists():
        down()
    print("[bed] %s: star of %d namespaces through ov-rt%s%s" % (BUILD, len(PARTS) + 2, ", wireguard-go" if wg else "",
                                                                    ", netem %r on p5..p8" % netem if netem else ""), flush=True)
    sh("ip", "netns", "add", NS("rt")); sh("ip", "link", "set", "lo", "up", ns="rt")
    sh("sysctl", "-qw", "net.ipv4.ip_forward=1", ns="rt")
    for n in PARTS + ["srv", "ctl"]:
        sh("ip", "netns", "add", NS(n)); sh("ip", "link", "set", "lo", "up", ns=n)
        r = "r%d" % K[n]
        sh("ip", "link", "add", r, "netns", NS("rt"), "type", "veth", "peer", "name", "eth0", "netns", NS(n))
        sh("ip", "addr", "add", ADDR(n, 1) + "/29", "dev", r, ns="rt"); sh("ip", "link", "set", r, "up", ns="rt")
        sh("ip", "addr", "add", ADDR(n) + "/29", "dev", "eth0", ns=n); sh("ip", "link", "set", "eth0", "up", ns=n)
        sh("ip", "route", "add", "default", "via", ADDR(n, 1), ns=n)
        sh("ethtool", "-K", "eth0", "tso", "off", "gso", "off", "gro", "off", ns=n, check=False)   # packets as sent
    if netem:
        for p in INTERNET:
            sh("tc", "qdisc", "add", "dev", "r%d" % K[p], "root", "netem", *netem.split(), ns="rt")
    if wg:
        wg_up()


def wg_up():
    keys = {}
    for n in PARTS + ["srv"]:
        priv = subprocess.run(["wg", "genkey"], capture_output=True, text=True, check=True).stdout.strip()
        pub = subprocess.run(["wg", "pubkey"], input=priv, capture_output=True, text=True, check=True).stdout.strip()
        keys[n] = (priv, pub)
    for n in PARTS + ["srv"]:
        # wireguard-go in the foreground, its own session: its daemon mode (WG_PROCESS_FOREGROUND=0) leaves no device
        # behind in this kernel's namespaces. Ready when it says its control socket listens ("UAPI listener started":
        # the socket file exists a moment before it listens) -- its log watched with inotify, no polling.
        sock = "/var/run/wireguard/%s.sock" % WGIF(n)
        if os.path.exists(sock):
            os.unlink(sock)
        os.makedirs("/var/run/wireguard", exist_ok=True)
        lf = "/run/ov-wg-%s.log" % n
        open(lf, "w").close()
        w = Inotify("/run")
        log = open(lf, "a")
        pr = subprocess.Popen(["ip", "netns", "exec", NS(n), "wireguard-go", "-f", WGIF(n)], stdin=subprocess.DEVNULL,
                              stdout=log, stderr=log, start_new_session=True, env=dict(os.environ, LOG_LEVEL="verbose"))
        while "UAPI listener started" not in open(lf).read():
            if pr.poll() is not None:
                raise RuntimeError("wireguard-go in %s ended: %s" % (n, open(lf).read().strip()))
            w.read()
        w.close()
        kf = "/run/ov-wg-%s.key" % n
        with open(kf, "w") as f:
            f.write(keys[n][0])
        os.chmod(kf, 0o600)
        if n == "srv":
            sh("wg", "set", WGIF(n), "listen-port", "51820", "private-key", kf, ns=n)
            for p in PARTS:
                sh("wg", "set", WGIF(n), "peer", keys[p][1], "allowed-ips", WG(p) + "/32", ns=n)
        else:
            sh("wg", "set", WGIF(n), "private-key", kf, "peer", keys["srv"][1], "endpoint", ADDR("srv") + ":51820",
               "allowed-ips", WG("srv") + "/32", "persistent-keepalive", "0", ns=n)
        os.unlink(kf)
        sh("ip", "addr", "add", WG(n) + "/24", "dev", WGIF(n), ns=n); sh("ip", "link", "set", WGIF(n), "up", ns=n)
    with open("/run/ov-wg-pubs", "w") as f:
        for n, (_, pub) in keys.items():
            f.write("%s %s\n" % (n, pub))


def wg_pub(n):
    for line in open("/run/ov-wg-pubs"):
        a, b = line.split()
        if a == n:
            return b
    raise KeyError(n)


def move(p):
    """[stress 3] p's address changes from 10.77.K.2 to 10.77.K.3 (its WireGuard endpoint roams with it)"""
    sh("sysctl", "-qw", "net.ipv4.conf.eth0.promote_secondaries=1", ns=p)   # the new address stays when the old goes
    sh("ip", "addr", "add", ADDR(p, 3) + "/29", "dev", "eth0", ns=p)
    sh("ip", "addr", "del", ADDR(p) + "/29", "dev", "eth0", ns=p)
    sh("ip", "route", "replace", "default", "via", ADDR(p, 1), "src", ADDR(p, 3), ns=p)
    sh("ss", "-K", "src", ADDR(p), ns=p, check=False)
    if os.path.exists("/var/run/wireguard/%s.sock" % WGIF(p)):            # its tunnel's socket bound again: from the new address
        sh("wg", "set", WGIF(p), "listen-port", str(51900 + K[p]), ns=p)


def revoke(p):
    """[stress 4] the server's WireGuard drops p's peer: p loses access"""
    sh("wg", "set", WGIF("srv"), "peer", wg_pub(p), "remove", ns="srv")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("what", choices=["up", "down"])
    ap.add_argument("--wg", action="store_true")
    ap.add_argument("--netem")
    a = ap.parse_args()
    up(a.wg, a.netem) if a.what == "up" else down()
