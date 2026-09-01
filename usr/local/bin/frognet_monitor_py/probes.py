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
frognet_monitor/probes.py — Network connectivity probes.

Architecture:
  Echo probe → port 80 → local proxy → local daemon → remote daemon → Apache
  A successful echo PROVES the remote daemon is alive.

  NO direct TCP probes to port 9009.  EVER.
  Only the proxy talks to the daemon.

Internet canary → direct HTTP to google.com (not through proxy).
"""

import threading
import time
import urllib.request
from typing import Dict, List, Tuple

from .config import ECHO_TIMEOUT, INET_TIMEOUT, ECHO_STAGGER_SEC
from .identity import LOCAL_LOWER, LOCAL_IP
from .trace import trace


def probe_echo(ip: str) -> Tuple[str, int]:
    """Semantic echo through the proxy.  Tests full path including daemon."""
    try:
        t0 = time.monotonic()
        req = urllib.request.Request(
            f"http://{ip}/frognet_echo.php",
            headers={"Host": ip},
        )
        with urllib.request.urlopen(req, timeout=ECHO_TIMEOUT) as resp:
            _ = resp.read()
            ms = int((time.monotonic() - t0) * 1000)
            code = resp.status
        if 200 <= code < 400:
            return ("OK", ms)
        return (f"HTTP_{code}", ms)
    except urllib.error.HTTPError as e:
        return (f"HTTP_{e.code}", 0)
    except Exception as e:
        trace(f"[ECHO] {ip} failed: {e}")
        return ("FAIL", 0)


def probe_internet() -> Tuple[str, int]:
    """Internet canary — direct HTTP, not through proxy."""
    try:
        t0 = time.monotonic()
        req = urllib.request.Request("http://www.google.com/")
        with urllib.request.urlopen(req, timeout=INET_TIMEOUT) as resp:
            _ = resp.read(1024)
            ms = int((time.monotonic() - t0) * 1000)
        return ("OK", ms)
    except Exception:
        return ("FAIL", 0)


def probe_all(hosts: List[Tuple[str, str]]) -> Dict[str, Dict]:
    """Probe all remote hosts and internet.  Returns {ip: {"echo": (status, ms)}, ...}."""
    results = {}
    threads = []

    def _echo_staggered(ip, delay):
        if delay > 0:
            time.sleep(delay)
        results.setdefault(ip, {})["echo"] = probe_echo(ip)

    def _inet():
        results["__internet__"] = {"result": probe_internet()}

    remote_hosts = [(ip, name) for ip, name in hosts
                    if name.lower() != LOCAL_LOWER and ip != LOCAL_IP]

    # Internet canary fires immediately
    ti = threading.Thread(target=_inet, daemon=True)
    threads.append(ti)
    ti.start()

    # Echo probes staggered to avoid head-of-line blocking on constrained links.
    # Each probe transits through the daemon — spacing prevents queue pileup.
    for i, (ip, name) in enumerate(remote_hosts):
        t = threading.Thread(target=_echo_staggered,
                             args=(ip, i * ECHO_STAGGER_SEC), daemon=True)
        threads.append(t)
        t.start()

    for t in threads:
        t.join(timeout=ECHO_TIMEOUT + len(remote_hosts) * ECHO_STAGGER_SEC + 2)
    return results
