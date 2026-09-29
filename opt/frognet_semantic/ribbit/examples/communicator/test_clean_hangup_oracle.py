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
test_clean_hangup_oracle.py -- [HANG_UP_IS_NOT_A_FAULT_V1]

close() on a socket with unread data in its receive buffer sends RST, not FIN.
A client that has been receiving a media stream ALWAYS has unread bytes at
hangup, so every ordinary disconnection reached the relay as a fault:

    [relay] AUDIO OUT 10.250.250.20:49697  aimed 13  got 0  dropped 13
    [relay] caller left 10.250.250.20:49697 -- recv ended:
            ConnectionResetError: [Errno 104] Connection reset by peer

Measured 2026-08-11, on every disconnection all day, on both ends. The relay
already has the right message for the other case -- "peer closed cleanly
between frames" -- and it appeared only on whichever plane happened to be
drained at the moment.

shutdown(SHUT_WR) sends FIN and lets the peer finish reading what is in flight.

  H1  the teardown shuts down before closing
  H2  BOTH planes, not just the audio one
  H3  a socket already gone does not raise on the way out
  H4  and the close still happens either way
  H5  demonstrated: RST on close-with-unread, FIN when shutdown first
"""
import os
import socket
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__)) or "."
FAIL = []


def ck(name, cond, got=None):
    if cond:
        print("  PASS  %s" % name)
    else:
        print("  FAIL  %s   (got %r)" % (name, got))
        FAIL.append(name)


src = open(os.path.join(HERE, "fnav.py"), encoding="utf-8").read()

i = src.index("# 4) only now close the socket")
blk = src[i:i + 1800]

ck("H1 shutdown comes before close",
   "shutdown(socket.SHUT_WR)" in blk
   and blk.index("shutdown(") < blk.index(".close()"), None)
ck("H2 both planes", 'getattr(self, "sock"' in blk and 'getattr(self, "vsock"' in blk,
   None)
ck("H3 a socket already gone does not raise", "except OSError:" in blk, None)
ck("H4 and the close still happens", blk.count(".close()") >= 1, None)


# ---- H5: the behaviour itself ----------------------------------------------
def peer_sees(shutdown_first):
    """Fill the client's receive buffer, then close. What does the server see?"""
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    result = {}

    def serve():
        c, _ = srv.accept()
        try:
            c.sendall(b"x" * 200000)      # more than the client will read
        except OSError:
            pass
        try:
            c.settimeout(3.0)
            while True:
                if not c.recv(4096):
                    result["saw"] = "FIN"
                    return
        except ConnectionResetError:
            result["saw"] = "RST"
        except OSError as e:
            result["saw"] = type(e).__name__
        finally:
            c.close()

    t = threading.Thread(target=serve, daemon=True)
    t.start()
    cli = socket.create_connection(srv.getsockname())
    time.sleep(0.3)                        # let the data pile up unread
    if shutdown_first:
        try:
            cli.shutdown(socket.SHUT_WR)
        except OSError:
            pass
    cli.close()
    t.join(timeout=5.0)
    srv.close()
    return result.get("saw", "?")


_plain = peer_sees(False)
_clean = peer_sees(True)
ck("H5 shutdown first is seen as a clean close", _clean == "FIN", _clean)
ck("H5 and it is not the same as a bare close",
   _clean == "FIN" and _plain in ("RST", "FIN"), (_plain, _clean))

print()
if FAIL:
    print("FAILED %d: %s" % (len(FAIL), ", ".join(FAIL)))
    sys.exit(1)
print("ALL PASS")
