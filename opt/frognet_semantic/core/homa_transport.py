#!/usr/bin/env python3
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#  SPDX-License-Identifier: GPL-2.0-only                       #
#  See COPYRIGHT and LICENSE at the root of this tree.         #
################################################################
"""
core/homa_transport.py -- [HOMA_TRANSPORT_V1] which transport the FNW1 semantic wire runs on, on this node.

Configured, never probed. One word -- "tcp", "homa" (the kernel module) or "homasim" (core/homa_sim.py, the Homa
simulator: the same driver over Homa's protocol in user space, for the sandbox and the simulator) -- read once at start from the deployment file

    /etc/frognet/semantic_transport          (FROGNET_SEM_TRANSPORT_FILE names another)

or from FROGNET_SEM_TRANSPORT in the process's own environment, which a harness sets for one process. No file and no
variable is "tcp": the wire as it has always been. Any other word stops the process with the word in the message.

[NO_FALLBACK_V1] The answer is not reconsidered. A node configured "homa" whose Homa socket will not open stops with
the kernel's reason (core/homa_socket.HomaUnavailable); it does not carry on over TCP. Both ends of a link must be
configured alike: a "homa" proxy's RPCs to a "tcp" daemon end in ETIMEDOUT and are reported as that.
"""
from __future__ import annotations

import os

TRANSPORT_FILE = os.environ.get("FROGNET_SEM_TRANSPORT_FILE", "/etc/frognet/semantic_transport")
VALID = ("tcp", "homa", "homasim")
_chosen = None


def semantic_transport() -> str:
    global _chosen
    if _chosen is not None:
        return _chosen
    word, src = os.environ.get("FROGNET_SEM_TRANSPORT", "").strip().lower(), "FROGNET_SEM_TRANSPORT"
    if not word:
        try:
            with open(TRANSPORT_FILE) as f:
                word, src = f.read().strip().lower(), TRANSPORT_FILE
        except FileNotFoundError:
            word, src = "tcp", "no %s (the default)" % TRANSPORT_FILE
    if word not in VALID:
        raise SystemExit("[SEM-TRANSPORT] %s says %r; the semantic wire's transport is one of %s"
                         % (src, word, ", ".join(VALID)))
    _chosen = word
    print("[SEM-TRANSPORT] [HOMA_TRANSPORT_V1] semantic wire transport=%s (from %s)" % (word, src), flush=True)
    return _chosen


def on_homa_driver() -> bool:
    """True when the semantic wire runs on the Homa driver (over the module or over the simulator)."""
    return semantic_transport() in ("homa", "homasim")


def open_homa_socket(who: str, bind_ip: str = "0.0.0.0", server_port=None):
    """The socket the Homa driver runs on, as configured: the kernel module's, or the simulator's. Logs what it is."""
    if semantic_transport() == "homasim":
        from core import homa_sim
        s = homa_sim.HomaSimSocket(bind_ip=bind_ip, server_port=server_port)
        print(homa_sim.startup_line(who, s), flush=True)
        return s
    from core import homa_socket
    s = homa_socket.HomaSocket(bind_ip=bind_ip, server_port=server_port)
    print(homa_socket.startup_line(who), flush=True)
    return s
