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
"""
launcher.py - the bundle launcher (headless core of family_hub.py).

Discovery is by BEACON: each bundle ships a bundle.json (id/module/title/hub/app/...);
the launcher reads the beacons, groups them by hub (the axis), and exposes a launch
list. The SOURCE of beacons is swappable and that is the whole design point:

  - LocalFileBeacons : scan a bundles root for bundle.json  (the install-time stand-in)
  - TransientBeacons : read installed_family_plugin sensors from the transient DB
                       (mesh-wide discovery; same shape - module/title/hub)

Moving from one node to the whole pond swaps the SOURCE, not the launcher.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List

HUB_ORDER = ["communications", "family-management", "games"]    # +unknown -> "more"


class BeaconSource:
    def beacons(self) -> List[Dict[str, Any]]: raise NotImplementedError


class LocalFileBeacons(BeaconSource):
    """Scan a bundles root; each <dir>/bundle.json is one beacon."""
    def __init__(self, root: str): self.root = root
    def beacons(self):
        found = []
        if not os.path.isdir(self.root):
            return found
        for name in sorted(os.listdir(self.root)):
            man = os.path.join(self.root, name, "bundle.json")
            if not os.path.isfile(man):
                continue
            try:
                with open(man) as f:
                    b = json.load(f)
            except Exception:
                continue
            b["_dir"] = os.path.join(self.root, name)
            b["_runnable"] = bool(b.get("app"))
            found.append(b)
        return found


class TransientBeacons(BeaconSource):
    """Read installed_family_plugin beacons from the transient DB (same shape)."""
    def __init__(self, transient, prefix="installed_family_plugin."):
        self.t, self.prefix = transient, prefix
    def beacons(self):
        rows = getattr(self.t, "scan", lambda p: [])(self.prefix)
        return [r for r in rows if isinstance(r, dict)]


def grouped(source: BeaconSource) -> Dict[str, List[Dict[str, Any]]]:
    """Beacons grouped by hub, in display order, unknown hubs under 'more'."""
    by_hub: Dict[str, List[Dict[str, Any]]] = {}
    for b in source.beacons():
        by_hub.setdefault(b.get("hub", "more"), []).append(b)
    order = HUB_ORDER + [h for h in by_hub if h not in HUB_ORDER]
    return {h: by_hub[h] for h in order if h in by_hub}
