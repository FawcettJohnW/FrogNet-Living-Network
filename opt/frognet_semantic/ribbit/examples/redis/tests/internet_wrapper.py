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
"""Redis's suite across a simulated Internet: install as src/redis-server (via RIBBIT_REDIS). Starts a fresh RAM host
and a latency relay in front of it (RIBBIT_ONE_WAY_MS, default 12: a 24 ms round trip, as AI-Host to streamingfrog),
then becomes the front, connected up to the host only through the relay. Host and relay die with the front
(PR_SET_PDEATHSIG, set for them before this process execs into the front: same pid)."""
import os, sys, socket, subprocess, ctypes, time, secrets
HERE = os.path.dirname(os.path.abspath(__file__)); B = os.environ.get("RIBBIT_BUILD", os.path.join(HERE, "..", "build"))
libc = ctypes.CDLL("libc.so.6", use_errno=True)
def die_with_parent(): libc.prctl(1, 15)                      # PR_SET_PDEATHSIG, SIGTERM
def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p
hp, rp = free_port(), free_port(); admin = secrets.token_hex(16)
blobs = "/tmp/ribbit-inet-blobs-%d" % os.getpid(); os.makedirs(blobs, exist_ok=True)
env = dict(os.environ, FROGNET_BLOB_ROOT=blobs, RIBBIT_REDIS_ADMIN=admin)
subprocess.Popen([os.path.join(B, "ribbit-redis-host"), "--listen", "127.0.0.1:%d" % hp, "--api", "/Fawcett.Redis.ram_interface.php", "--quiet"],
                 env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, preexec_fn=die_with_parent)
subprocess.Popen([sys.executable, os.path.join(HERE, "..", "tools", "latency_relay.py"), "--listen", "127.0.0.1:%d" % rp, "--to", "127.0.0.1:%d" % hp,
                  "--delay-ms", os.environ.get("RIBBIT_ONE_WAY_MS", "12")], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, preexec_fn=die_with_parent)
for port in (hp, rp):
    t0 = time.time()
    while True:
        try: socket.create_connection(("127.0.0.1", port), timeout=1).close(); break
        except OSError:
            if time.time() - t0 > 10: sys.exit("internet_wrapper: port %d never opened" % port)
os.environ.update(RIBBIT_RAM="127.0.0.1:%d" % rp, FROGNET_BLOB_ROOT=blobs, RIBBIT_REDIS_ADMIN=admin)
os.execv(os.path.join(B, "ribbit-redis-front"), [os.path.join(B, "ribbit-redis-front")] + sys.argv[1:])
