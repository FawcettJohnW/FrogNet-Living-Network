################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#  SPDX-License-Identifier: GPL-2.0-only                       #
################################################################
"""fdf_regions -- [REGIONS_PER_MACHINE_V1] the regions this machine holds. John, 2026-11-01: "A machine may host more
than one ram segment, as long as they're on different ports." "I *WANT* a second region."

Each region is a directory, FDF_HUB_ROOT/<AREA>/ (default /opt/fdf/hub/<AREA>/), made by fdf-region-create:
  hub.env       the region's settings (RINA_RAM, FDF_AREA, FDF_ENDPOINT, FDF_LISTEN, FDF_IFACE, ...)
  admitted/     one file per admitted member (fdf-invite, fdf-org)
  org-key       the credential key (fdf-org)
and its own units, fdf-hub@AREA, fdf-identity@AREA, fdf-org@AREA, beside fdf-ram-AREA and fdf-client-AREA.

Edge addresses (10.253.X.Y) are one pool per machine: every region's tunnel ends on this machine, so two regions must
never give the same edge, or this machine would route one member's packets into the other region's tunnel.

[HUB_ADDRESS_FROM_ITS_HOST_V1] (John 2026-11-02: "Why isn't the hub using the IP address of its host?" "Use RAM.")
Every hub was 10.253.253.254, so a device holding two regions' tunnels (the WAN's and an EOC's) had two routes to one
address. A hub on a FrogNet node that serves 10.A.B.0/24 takes 10.253.A.B: unique (one owner per /24), derived from
its host, and never a WireGuard endpoint (the host's own 10.A.B.1 would be, on its LAN, and loop -- John chose
10.253.A.B over that). A hub on a machine that serves no FrogNet LAN (StreamingFrog) keeps 10.253.253.254. The hub
says its address in its row in the region's RAM ((fdf-org, hub, AREA) {addr}, and its facts' addr); members take it
from there. hub.env records it as FDF_HUB_ADDR; a hub.env without it was made when every hub was 10.253.253.254.
"""
import ipaddress, json, os, subprocess

OLD_HUB_ADDR = "10.253.253.254"                 # a hub on a machine with no FrogNet LAN, and every hub.env before r22
OVERLAY = ("252", "253", "254")                 # 10.252 leaves, 10.253 edges and hubs, 10.254 Chorus: not a LAN


def served_lans():
    """the FrogNet LANs this machine serves: every 10.A.B.1 on an interface that is not WireGuard, as (A, B)"""
    out = set()
    try:
        txt = subprocess.run(["ip", "-o", "-4", "address", "show"], capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return []
    for ln in txt.splitlines():
        f = ln.split()
        if len(f) < 4 or "inet" not in f:
            continue
        dev, ip = f[1], f[f.index("inet") + 1].split("/")[0]
        o = ip.split(".")
        if dev.startswith("wg") or len(o) != 4 or o[0] != "10" or o[1] in OVERLAY or o[3] != "1":
            continue
        if os.path.isdir("/sys/class/net/%s/wireguard" % dev) or os.path.exists("/sys/class/net/%s/tun_flags" % dev):
            continue
        out.add((int(o[1]), int(o[2])))
    return sorted(out)


def own_hub_addr():
    """(address, why) for a new hub here: 10.253.A.B from the LAN this node serves; 10.253.253.254 when it serves none.
    A node serving several LANs takes the lowest, and says so."""
    lans = served_lans()
    if not lans:
        return OLD_HUB_ADDR, "this machine serves no FrogNet LAN (no 10.A.B.1 on a LAN interface)"
    a, b = lans[0]
    why = "from the LAN this node serves, 10.%d.%d.0/24" % (a, b)
    if len(lans) > 1:
        why += " (the lowest of %s)" % ", ".join("10.%d.%d.0/24" % x for x in lans)
    return "10.253.%d.%d" % (a, b), why


def hub_addr(env):
    """the hub address a region's hub.env (a dict) records"""
    v = (env or {}).get("FDF_HUB_ADDR", "").strip()
    return v if v else OLD_HUB_ADDR


def env_file(path):
    out = {}
    try:
        for ln in open(path):
            ln = ln.strip()
            if ln and not ln.startswith("#") and "=" in ln:
                k, v = ln.split("=", 1)
                out[k.strip()] = v.strip()
    except OSError:
        pass
    return out


def hub_addrs_here():
    """every hub address a region on this machine uses: no edge is ever given one of them"""
    return {hub_addr(env_file(os.path.join(d, "hub.env"))) for d in areas().values()}

ROOT = os.environ.get("FDF_HUB_ROOT", "/opt/fdf/hub")
LEGACY = os.path.join(ROOT, "hub.env")          # a region made by an earlier build, before regions had directories


def areas():
    """the regions held here: {area: its directory}"""
    out = {}
    try:
        for n in sorted(os.listdir(ROOT)):
            d = os.path.join(ROOT, n)
            if os.path.isfile(os.path.join(d, "hub.env")):
                out[n] = d
    except FileNotFoundError:
        pass
    return out


def edges_taken():
    """every edge any region on this machine has given"""
    taken = set()
    for d in areas().values():
        a = os.path.join(d, "admitted")
        for fn in os.listdir(a) if os.path.isdir(a) else []:
            if fn.endswith(".json"):
                try:
                    e = json.load(open(os.path.join(a, fn))).get("edge")
                except (OSError, ValueError):
                    continue
                if e:
                    taken.add(e)
    return taken


def free_edge(taken):
    """the first edge no region here holds: 10.253.X.Y, X 1..252, Y 1..254 (10.253.253.0/24 is the hubs')"""
    taken = set(taken) | edges_taken() | hub_addrs_here()        # [HUB_ADDRESS_FROM_ITS_HOST_V1] never a hub's address
    for x in range(1, 253):
        for y in range(1, 255):
            a = "10.253.%d.%d" % (x, y)
            if a not in taken:
                return a
    raise RuntimeError("no edge address left in 10.253.0.0/16")


def free_generic():
    """[REGIONS_PER_MACHINE_V1] a region's generic credential edge (fdf-org init): 10.253.253.N, the first no region here
    holds (the first region keeps 10.253.253.1)"""
    taken = edges_taken()
    for n in range(1, 250):
        a = "10.253.253.%d" % n
        if a not in taken:
            return a
    raise RuntimeError("no generic edge left in 10.253.253.0/24")


def pick(area, prog=""):
    """(hub.env, admitted dir) of AREA, or of the one region held here when AREA is empty; the reason when neither"""
    have = areas()
    if os.path.isfile(LEGACY):
        return None, None, ("%s: this machine's region was made by an earlier build; move it into its own directory "
                            "first: fdf-region-create --migrate" % LEGACY)
    if area:
        if area not in have:
            return None, None, "this machine holds no region %s (it holds: %s)" % (area, ", ".join(have) or "none")
        d = have[area]
    elif len(have) == 1:
        d = list(have.values())[0]
    elif not have:
        return None, None, "this machine holds no region (fdf-setup makes one)"
    else:
        return None, None, "this machine holds %s: say which with --area AREA" % ", ".join(have)
    return os.path.join(d, "hub.env"), os.path.join(d, "admitted"), ""
