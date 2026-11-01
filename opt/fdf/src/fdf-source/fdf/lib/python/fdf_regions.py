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
"""
import json, os

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
    taken = set(taken) | edges_taken()
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
