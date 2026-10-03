################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#                                                              #
#  SPDX-License-Identifier: GPL-2.0-only                       #
#                                                              #
#  This program is free software; you can redistribute it      #
#  and/or modify it under the terms of the GNU General Public  #
#  License as published by the Free Software Foundation;       #
#  version 2 of the License, and no other version.             #
#                                                              #
#  This program is distributed in the hope that it will be     #
#  useful, but WITHOUT ANY WARRANTY; without even the implied  #
#  warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR     #
#  PURPOSE.  See the GNU General Public License for details.   #
#                                                              #
#  See COPYRIGHT and LICENSE at the root of this tree.         #
################################################################
"""
identity_preflight.py - front-of-runMerge gate.

Verify the interface that is supposed to bear this node's identity is actually up
and carrying the identity addresses (.1 AND .2), per the system's CONFIGURED mode.
If not, the node cannot function and the merge must FAIL LOUDLY (nonzero rc) before
any route work - instead of the old silent degrade (doc-5: identity iface down ->
src-pinned defaults installed then rejected -> broken node, rc=0).

Modes (config-considered):
  wired    : identity on the interface that bears the served /24 (discovered via
             mapInterfaces - NOT assumed to be eth0; e.g. NY1 uses eth1).
  wireless : AP mode - identity on the AP interface bearing the served /24.
  soft     : soft stand-alone (FROGNET_SOFT_STANDALONE=1) - the EXCEPTION: no real
             interface required; a synthetic (dummy) interface is created to host
             the presented identity .1/.2. Nobody can connect inbound; that's OK.

The pure decide() is the regression-suite core (test_identity_preflight_oracle.py:
the decision table = common breakages + recovery). The real-system gatherers in
main() are UNVALIDATED in-container (no box) and flagged for on-box verification.
"""
from __future__ import annotations

import os
import subprocess
import sys

PROCEED, FAIL, SOFT = "proceed", "fail", "soft"

SOFT_CONF = "/etc/frognet/soft_standalone.conf"
SOFT_IFACE = "frognet-soft0"
OVERRIDE_CONF = "/etc/frognet/interfaces_override.conf"


# ---- pure decision (unit-tested; no I/O) ----------------------------------

def decide(*, mode, soft, soft_ip, ident_prefix, ident_iface, iface_up, iface_addrs):
    """Returns (action, reason). action in {PROCEED, FAIL, SOFT}."""
    # soft stand-alone is the explicit exception to the real-interface requirement.
    if soft:
        ip = soft_ip or (f"{ident_prefix}.1" if ident_prefix else "")
        if not ip:
            return (FAIL, "soft mode enabled but no identity IP presented "
                          "(set FROGNET_SOFT_IDENTITY or a dnsmasq subnet)")
        return (SOFT, f"soft stand-alone: host {ip} (+ .2) on synthetic {SOFT_IFACE}")

    # real mode (wired or AP): the configured identity iface must exist, be up, and
    # bear BOTH .1 and .2 of the served /24.
    if not ident_iface:
        return (FAIL, f"no identity interface resolved for mode={mode}")
    if not iface_up:
        return (FAIL, f"identity interface {ident_iface} is DOWN (mode={mode}); "
                      f"cannot bear identity - bring it up, or enable soft mode "
                      f"(frognet-soft-standalone enable)")
    if ident_prefix:
        need = {f"{ident_prefix}.1", f"{ident_prefix}.2"}
        missing = sorted(need - set(iface_addrs))
        if missing:
            return (FAIL, f"identity interface {ident_iface} (mode={mode}) is missing "
                          f"{missing} - it must bear BOTH .1 and .2 of "
                          f"{ident_prefix}.0/24")
    return (PROCEED, f"identity OK on {ident_iface} bearing .1/.2 (mode={mode})")


# ---- real-system gatherers (UNVALIDATED on-box) ---------------------------

def _source_conf(path, keys):
    """Minimal `KEY=val`/`export KEY=val` reader (no shell eval)."""
    out = {}
    try:
        with open(path) as f:
            for ln in f:
                ln = ln.strip()
                if ln.startswith("export "):
                    ln = ln[7:]
                if "=" in ln and not ln.startswith("#"):
                    k, v = ln.split("=", 1)
                    out[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return {k: out.get(k, "") for k in keys}


def _iface_up(iface):
    try:
        with open(f"/sys/class/net/{iface}/operstate") as f:
            return f.read().strip() in ("up", "unknown")
    except OSError:
        return False


def _iface_addrs(iface):
    try:
        r = subprocess.run(["ip", "-4", "-o", "addr", "show", "dev", iface],
                           capture_output=True, text=True)
    except OSError:
        return set()
    out = set()
    for ln in r.stdout.splitlines():
        p = ln.split()
        if "inet" in p:
            out.add(p[p.index("inet") + 1].split("/")[0])
    return out


def _ident_prefix_from_dnsmasq():
    """dhcp-range first octet-triple (matches frognet-netstart get_subnet)."""
    for path in ("/etc/dnsmasq.d/opts_only.conf", "/etc/dnsmasq.conf"):
        try:
            with open(path) as f:
                for ln in f:
                    ln = ln.strip()
                    if ln.startswith("dhcp-range="):
                        first = ln.split("=", 1)[1].split(",", 1)[0]
                        return ".".join(first.split(".")[:3])
        except OSError:
            continue
    return ""


def _addr_ips(ifc):
    return {a.split("/")[0] for a in ifc.addrs}


def _served_prefix_of(addrs):
    """The served /24 = the prefix of the 10.x.1 this iface bears (mapInterfaces
    served-/24 rule), excluding the 10.253.253 transit space."""
    for ip in addrs:
        o = ip.split(".")
        if (len(o) == 4 and ip.startswith("10.") and o[3] == "1"
                and not ip.startswith("10.253.253.")):
            return ".".join(o[:3])
    return ""


def resolve_identity_iface(ifaces, ident_prefix=""):
    """Find the interface that ACTUALLY bears this node's identity, using the
    mapInterfaces served-/24 rule (_owns_served24) - never a hardwired eth0. Any
    physical interface can be the identity bearer (NY1 = eth0, but it could be eth1
    or wlan0) and several interfaces can be DHCP clients, so the name cannot be
    assumed.

    Returns (dev, addr_ips, served_prefix). served_prefix is DERIVED from the .1 the
    iface bears - the node's served subnet is whatever its identity interface
    carries, NOT a separate (and possibly stale/foreign) dnsmasq dhcp-range. A hint
    prefix only biases WHICH iface to prefer when several bear a served .1.
    Returns ("", set(), "") when no interface bears an identity (-> decide FAILs).
    """
    from .mapinterfaces import _owns_served24, _is_physical
    want1 = f"{ident_prefix}.1" if ident_prefix else ""
    chosen = None
    if want1:
        chosen = next((i for i in ifaces if want1 in _addr_ips(i)), None)
    if chosen is None:
        chosen = next((i for i in ifaces
                       if _is_physical(i.dev) and _owns_served24(i)), None)
    if chosen is None:
        return ("", set(), "")
    addrs = _addr_ips(chosen)
    return (chosen.dev, addrs, _served_prefix_of(addrs))


def _gather():
    soft_cfg = _source_conf(SOFT_CONF, ["FROGNET_SOFT_STANDALONE", "FROGNET_SOFT_IDENTITY"])
    soft = soft_cfg["FROGNET_SOFT_STANDALONE"] in ("1", "true", "yes", "on")
    soft_ip = soft_cfg["FROGNET_SOFT_IDENTITY"]

    mode = subprocess.run(["/usr/local/bin/frognet-netmode"],
                          capture_output=True, text=True).stdout.strip() or "wireless"
    # dnsmasq/soft give only a HINT for which iface to prefer; the authoritative
    # served subnet is read off the identity interface itself (below).
    hint = (soft_ip and ".".join(soft_ip.split(".")[:3])) or _ident_prefix_from_dnsmasq()

    from .mapinterfaces import RealInterfaceMap
    try:
        ifaces, _leases = RealInterfaceMap().gather()
    except Exception:
        ifaces = []
    ident_iface, addrs, served = resolve_identity_iface(ifaces, hint)
    # iface-borne prefix wins; fall back to the hint only when no iface bears an
    # identity (soft mode / fully-unconfigured), so a prefix is still reported.
    ident_prefix = served or hint

    return dict(mode=mode, soft=soft, soft_ip=soft_ip, ident_prefix=ident_prefix,
                ident_iface=ident_iface,
                iface_up=_iface_up(ident_iface) if ident_iface else False,
                iface_addrs=addrs)


def _make_soft_iface(ident_prefix, soft_ip):
    ip1 = soft_ip or f"{ident_prefix}.1"
    pfx = ".".join(ip1.split(".")[:3])
    ip2 = f"{pfx}.2"
    subprocess.run(["ip", "link", "add", SOFT_IFACE, "type", "dummy"],
                   capture_output=True)
    subprocess.run(["ip", "link", "set", SOFT_IFACE, "up"], capture_output=True)
    for ip in (ip1, ip2):
        subprocess.run(["ip", "addr", "add", f"{ip}/24", "dev", SOFT_IFACE],
                       capture_output=True)


def main():
    st = _gather()
    action, reason = decide(**st)
    if action == FAIL:
        sys.stderr.write(f"[IDENTITY-PREFLIGHT] FAIL: {reason}\n")
        print(f"IDENTITY_PREFLIGHT action=fail reason={reason}")
        return 1
    if action == SOFT:
        print(f"IDENTITY_PREFLIGHT action=soft reason={reason}")
        _make_soft_iface(st["ident_prefix"], st["soft_ip"])
        return 0
    print(f"IDENTITY_PREFLIGHT action=proceed reason={reason}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
