#!/usr/bin/env python3
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
"""bg_assets.py -- the descriptor-tuple asset layer (general pattern, board-game instance).

The model (generalized): at INSTALL a bundle publishes DESCRIPTOR tuples about itself into
shared memory under the key family <Domain>.<Bundle>.<facet> -- assets, requirements, version,
capabilities, arbitrary declarative facts. Clients READ those facts and decide locally. This
file implements the ASSET facet plus a REQUIREMENTS facet for the board-game bundle.

Asset manifest key:   <domain>.<game>.<asset>  -> {hash, bytes, content_type, version}
Requirements key:     <domain>.<bundle>.requirements -> {python, needs_gpu, min_mem_mb, ...}

LOCALITY RULE (decided): the CANONICAL copy lives on the FrogNetHost .1 (every FNH is a
server). The WORKING copy lives ON THE DEVICE in the Communicator Assets directory. Art is
PRE-STAGED on a fast network; in the field nothing transfers -- the device renders from local
art and only the tiny live GAME object crosses the (BLDC-1/FNWP-1) wire. At load the device
computes local hashes and compares to the manifest tuple; it pulls ONLY stale/missing assets,
and only when it can (fast network). Never over the WAN by design -- the source is your own .1.
"""
import os, json, hashlib, time
from typing import Dict, Optional

ASSET_FACET = "asset"           # <domain>.<game>.<asset name> carries one asset descriptor
REQ_FACET   = "requirements"    # <domain>.<bundle>.requirements

def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()

def communicator_assets_dir(base: Optional[str] = None) -> str:
    """Device-local working copy: <communicator>/Assets/<...>. Keyed so bundles coexist."""
    base = base or os.environ.get("FROGNET_COMMUNICATOR_HOME") or os.path.expanduser("~/.frognet/communicator")
    d = os.path.join(base, "Assets")
    os.makedirs(d, exist_ok=True)
    return d

# ---------- INSTALL SIDE: publish the descriptor tuples (runs on the .1 at install) ----------
def publish_asset_manifest(space, domain: str, game: str, assets_dir: str) -> Dict[str, dict]:
    """For each file in assets_dir, write a descriptor tuple <domain>.<game>.<name> carrying
    its hash + version (and, for small assets, the bytes inline so the device can pull from
    the same shared memory; large assets would carry a fetch path instead). Returns the
    manifest it published. The SensorType region is the bundle/game; module=<game>, var=asset.
    'space' honors put(typ, module, var, obj)."""
    published = {}
    for fn in sorted(os.listdir(assets_dir)):
        fp = os.path.join(assets_dir, fn)
        if not os.path.isfile(fp): continue
        b = open(fp, "rb").read()
        ct = "image/svg+xml" if fn.endswith(".svg") else "application/json" if fn.endswith(".json") else "application/octet-stream"
        desc = {"name": fn, "hash": _sha(b), "version": 1, "content_type": ct,
                "bytes_b64": b.decode("latin1").encode("latin1").hex(),  # transport-safe inline
                "ts": int(time.time())}
        space.put(domain, f"{game}.{ASSET_FACET}", fn, desc)
        published[fn] = {k: desc[k] for k in ("hash", "version", "content_type")}
    # an index tuple lists the set (one cheap read tells the device what to check)
    space.put(domain, f"{game}.{ASSET_FACET}", "_index", {"assets": list(published), "ts": int(time.time())})
    return published

def publish_requirements(space, domain: str, bundle: str, requirements: dict) -> None:
    """Publish what a device needs to run this bundle's client, under <domain>.<bundle>.requirements."""
    space.put(domain, bundle, REQ_FACET, {**requirements, "ts": int(time.time())})

# ---------- DEVICE SIDE: decide + pull-if-stale into the local Assets dir ----------
def device_can_run(space, domain: str, bundle: str, local_caps: dict) -> (bool, list):
    """Read <domain>.<bundle>.requirements and compare to THIS device's capabilities. Returns
    (ok, unmet). Device-side qualification mirrors host-side election: read a fact, decide
    locally -- no negotiation round-trip."""
    req = space.get(domain, bundle, REQ_FACET) or {}
    unmet = []
    if req.get("needs_gpu") and not local_caps.get("gpu"): unmet.append("gpu")
    if req.get("min_mem_mb", 0) > int(local_caps.get("mem_mb", 0)): unmet.append(f"mem>={req['min_mem_mb']}MB")
    if req.get("python") and not local_caps.get("python"): unmet.append("python3")
    return (not unmet, unmet)

def sync_assets(space, domain: str, game: str, assets_dir: Optional[str] = None,
                allow_pull: bool = True) -> dict:
    """LOAD-time: compute local hashes, compare to the manifest tuples, pull ONLY stale/missing.
    allow_pull=False (field) => report what's missing but transfer nothing. Returns a report."""
    local_dir = assets_dir or communicator_assets_dir()
    game_dir = os.path.join(local_dir, domain, game)
    os.makedirs(game_dir, exist_ok=True)
    idx = space.get(domain, f"{game}.{ASSET_FACET}", "_index") or {"assets": []}
    report = {"ok": [], "pulled": [], "stale_skipped": [], "missing": []}
    for name in idx.get("assets", []):
        desc = space.get(domain, f"{game}.{ASSET_FACET}", name)
        if not desc: report["missing"].append(name); continue
        lp = os.path.join(game_dir, name)
        local_hash = _sha(open(lp, "rb").read()) if os.path.isfile(lp) else None
        if local_hash == desc["hash"]:
            report["ok"].append(name); continue          # current -> no transfer (the field win)
        if not allow_pull:
            report["stale_skipped"].append(name); continue
        # pull: inline bytes from the descriptor (small assets) -- from OUR domain's shared mem
        raw = bytes.fromhex(desc["bytes_b64"])
        open(lp, "wb").write(raw)
        report["pulled"].append(name)
    return report

def local_asset_path(domain: str, game: str, name: str, assets_dir: Optional[str] = None) -> str:
    return os.path.join(assets_dir or communicator_assets_dir(), domain, game, name)
