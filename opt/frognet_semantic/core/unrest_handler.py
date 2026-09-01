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
core/unrest_handler.py - the ONE UnREST handler interface. No special-casing.

Every semantic handler - JSON, XML, HTML, text, SotF media, the role handlers - is the
SAME shape: a class exposing the complete method surface below. Dispatch is uniform:
get JSON, call the JSON handler; get XML, call the XML handler; get SotF or a game, call
the SotF or game handler. The proxy/daemon never branch on type - they look the handler up
and call the interface.

A handler implements only the methods that mean something for it; everything else is the
empty-return default this base provides. So:
  * a FORMAT handler (JSON/XML/HTML/text) overrides only the codec slot
    (learn_*/extract_*/rebuild_reply/decode_payload) - compress and decompress - and
    inherits `return`-style no-ops for election and lifecycle.
  * the SotF handler overrides the SAME codec slot, where for it that slot manages the
    media/chat stream rather than compressing a body, PLUS the role slot.
  * a pure ROLE handler (databasehost/boardgame) overrides election (score/evaluate) and
    inherits the real lifecycle (advertise/hostReset) below; its codec slot is `return`.

THE LIFECYCLE IS REAL ON ANY HANDLER THAT NAMES A ROLE. `advertise()` dual-writes this
handler's <role>/capability tuple (perf inside, from frognet_capability_probe.sh) to BOTH
databasehost_control.frognet (authoritative election input) and databasehost.frognet
(mirror for normal consumers). `hostReset()` re-advertises. A handler with ROLE_NAME=None
(a pure format handler) returns from both - empty, by design.
"""
from __future__ import annotations

import json
import subprocess
from typing import Any, Dict, List, Optional, Tuple

CONTROL_DBHOST = "databasehost_control.frognet"   # deterministic, authoritative election input
DATA_DBHOST = "databasehost.frognet"              # elected/floating mirror for normal consumers
CAPABILITY_PROBE = "/usr/local/bin/frognet_capability_probe.sh"


def _tuples():
    """Locate the tuple client (put/get/role_scope/prune). Works in any context: it is on
    PYTHONPATH=/opt/frognet_semantic when present, else the bundle path is added by the
    caller (discovery._ensure_bundle_on_path). Returns the module or None - advertise then
    degrades to a no-op rather than raising into a reconcile."""
    try:
        from core import frognet_tuples as T  # type: ignore
        return T
    except Exception:
        try:
            import frognet_tuples as T  # type: ignore
            return T
        except Exception:
            return None


class UnRESTHandler:
    """The single interface every semantic handler conforms to. Subclasses override only
    what they implement; the rest are the empty-return defaults here."""

    # ---- identity -----------------------------------------------------------
    ROLE_NAME: Optional[str] = None        # a content-only handler names no role
    CANDIDATE_TYPE: Optional[str] = None
    DEFAULT_PORT: Optional[int] = None

    @property
    def mode(self) -> str:
        return self.ROLE_NAME or ""

    # ---- codec / stream slot (compress & decompress; for SotF, stream mgmt) -
    # Defaults are typed empties so a non-codec handler is inert on this path, never
    # an AttributeError and never a wrong-typed return that breaks a caller.
    def learn_request_template(self, body_text: Any) -> Dict[str, Any]:
        return {}

    def learn_reply_template(self, body_text: Any) -> Dict[str, Any]:
        return {}

    def extract_request_dynamic(self, body_text: Any,
                                fragment: Dict[str, Any]) -> List[Tuple[str, Any]]:
        return []

    def extract_reply_dynamic(self, body_text: Any,
                              fragment: Dict[str, Any]) -> List[Tuple[str, Any]]:
        return []

    def rebuild_reply(self, fragment: Dict[str, Any], values: List[Any]) -> str:
        return ""

    def decode_payload(self, rebuilt_body: Any) -> bytes:
        return b""

    # ---- election slot ------------------------------------------------------
    def score(self, cand: Dict[str, Any]) -> float:
        return -1.0        # ineligible by default - a content handler never wins a role

    def evaluate(self, hosts_list, lan_list):
        return None

    # ---- lifecycle slot (REAL for any role-naming handler) ------------------
    def advertise(self, blob: Optional[Dict[str, Any]] = None,
                  control: str = CONTROL_DBHOST, data: str = DATA_DBHOST,
                  probe: str = CAPABILITY_PROBE,
                  logger=lambda s: None) -> Dict[str, Any]:
        """Dual-write THIS handler's <role>/capability (perf inside the blob) to BOTH the
        control DB (election input) and the data DB (mirror). No-op for a handler with no
        ROLE_NAME. own=False: a refresh that must outlive this call and age out by ts.
        `blob` may be passed in so a driver probes once and fans it across roles."""
        rep: Dict[str, Any] = {"handler": type(self).__name__, "role": self.ROLE_NAME,
                               "written": [], "consistency": "ok"}
        if not self.ROLE_NAME:
            return rep                                  # content-only handler: return()
        T = _tuples()
        if T is None:
            rep["consistency"] = "tuples_unavailable"
            return rep
        if blob is None:
            blob = self._probe_capability(probe, logger)
        if blob is None:
            rep["consistency"] = "probe_unavailable"
            return rep
        try:
            scope = T.role_scope(self.ROLE_NAME)
        except Exception as e:
            rep["consistency"] = f"scope_error:{e}"
            return rep
        for dbhost in (control, data):                  # BOTH - control authoritative, data mirror
            try:
                ok = T.put(self.ROLE_NAME, "capability", scope, blob, dbhost=dbhost, own=False)
            except Exception as e:
                logger(f"ADVERTISE put_exc role={self.ROLE_NAME} dbhost={dbhost} err={e}")
                continue
            if ok:
                rep["written"].append(f"{self.ROLE_NAME}@{dbhost}")
                try:
                    T.prune_self_stale_capability(self.ROLE_NAME, "capability", dbhost=dbhost)
                except Exception:
                    pass
        return rep

    def hostReset(self) -> Dict[str, Any]:
        """Trigger-agnostic reconcile (boot / merge / float): re-advertise to both DBs so a
        floated host carries this node's current capability for the next election round."""
        return self.advertise()

    # ---- shared helper ------------------------------------------------------
    @staticmethod
    def _probe_capability(probe: str, logger) -> Optional[Dict[str, Any]]:
        try:
            out = subprocess.run([probe], capture_output=True, text=True, timeout=10)
            if out.returncode != 0 or not out.stdout.strip():
                logger(f"ADVERTISE probe_fail rc={out.returncode}")
                return None
            return json.loads(out.stdout)
        except Exception as e:
            logger(f"ADVERTISE probe_exc={e}")
            return None
