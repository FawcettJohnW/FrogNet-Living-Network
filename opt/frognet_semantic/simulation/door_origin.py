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
door_origin.py - [THE_DOOR_ON_A_MACHINE_V1] the door tier's door, running ON a machine of wire_between_machines_tier.

John, 2026-11-17: "If there are scenarios that have not been run, then we are not done."

simulation/door_semantic_tier.py has the scenarios the semantic wire was debugged against (OpenEOC's door: the
phone's and the admin app's calls, a refusal first, the origin learning first, the configuration changing between
the two nodes' learning). There they run in one process, the frame handed from the proxy's engine to the daemon's.
Between machines the door has to be where a node's door is: behind the origin machine's web server, at
/dc/ram.php on port 8080, reached by the origin's daemon. This is that door:

  * the SAME function, door_semantic_tier.door(), with its CONFIG; nothing about what it answers is rewritten here;
  * served on 127.0.0.1:<port> on the origin machine; the machine's web root gets dc/ram.php, a few lines of PHP
    (RAM_PHP below) that hand each request to it and return its status and body, so the request arrives through
    php on 8080 exactly as a node's ram.php is reached;
  * it records what it was GIVEN for login and enroll (op, user, password), as the door tier's origin does, so
    the tier can check the door was given what the app sent;
  * the tier's own controls, on a query a node's door does not have, so that they too arrive through php on 8080:
    /dc/ram.php?__door=reset, ?__door=add_role&name=<role>, ?__door=received.

Two more ops, for the large-frame battery, which a real door does not have and which are not door scenarios:

  op=blob&n=<bytes>&seed=<k>   GET   {"ok":true,"n":n,"seed":k,"data":{"p000":"...","p001":"..."}}: n hex characters
                                     from SHA-256 in counter mode over the seed, in fields of at most 50,000 (the
                                     codec's string field holds 65,535 bytes): a reply of any size whose contents
                                     the asker can recompute, different for every seed, and which does not compress
  op=echo                      POST  {"ok":true,"n":<length>,"sha":"<SHA-256>"} of the body's JSON in canonical form:
                                     the origin's own account of the VALUES it was given in a request of any size
                                     (the semantic wire carries JSON by its values, not its bytes)
"""
import hashlib
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

RAM_PHP = r'''<?php
// [THE_DOOR_ON_A_MACHINE_V1] simulation/door_origin.py's door, reached where a node's door is. Hands the request on
// and returns the door's status and body. Written by wire_between_machines_tier.py into a machine's web root.
$url = 'http://127.0.0.1:%d' . $_SERVER['REQUEST_URI'];
$body = file_get_contents('php://input');
$ctx = stream_context_create(['http' => ['method' => $_SERVER['REQUEST_METHOD'], 'content' => $body, 'ignore_errors' => true,
    'timeout' => 120, 'header' => "Content-Type: application/json\r\nContent-Length: " . strlen($body) . "\r\n"]]);
$out = file_get_contents($url, false, $ctx);
$code = 502;
if (isset($http_response_header[0]) && preg_match('#\s(\d{3})\s#', $http_response_header[0], $m)) { $code = (int)$m[1]; }
http_response_code($code);
header('Content-Type: application/json');
echo $out === false ? '{"ok":false,"why":"the door on this machine did not answer"}' : $out;
'''


PART = 50000        # characters in one field. The semantic codec's string field holds at most 65,535 bytes (a 16-bit
                    # length: "TYPE_STR over 65535-byte wire field"), so a large frame is many fields, as a large
                    # reply from a node is many rows; one field over that is refused by the codec with that reason.


def blob(n, seed):
    """n hex characters, recomputable from the seed, different for every seed."""
    out, i, have = [], 0, 0
    while have < n:
        out.append(hashlib.sha256(("%s:%d" % (seed, i)).encode()).hexdigest())
        i += 1
        have += 64
    return "".join(out)[:n]


def parts(n, seed):
    """blob(n, seed) as fields of at most PART characters: {"p000": ..., "p001": ...}."""
    whole = blob(n, seed)
    return {"p%03d" % k: whole[i:i + PART] for k, i in enumerate(range(0, n, PART))}


def canonical(text):
    """A JSON text by its meaning: the semantic wire carries JSON as values, so what the origin is given is the same
    values, not necessarily the same bytes (a space after a colon is not carried)."""
    return json.dumps(json.loads(text), sort_keys=True, separators=(",", ":"))


def serve(port):
    import door_semantic_tier as D
    lock = threading.Lock()

    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def _send(self, status, text):
            b = text.encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def _do(self, method):
            n = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(n) if n else b""
            u = urlsplit(self.path)
            q = {k: v[-1] for k, v in parse_qs(u.query).items()}
            ctl = q.get("__door", "")
            if ctl == "reset":
                with lock:
                    D.CONFIG.clear(); D.CONFIG.update(json.loads(json.dumps(D.CONFIG0))); del D.RECEIVED[:]
                return self._send(200, '{"ok":true}')
            if ctl == "add_role":
                with lock:
                    D.CONFIG["roles"][q["name"]] = {"default": True}; D.CONFIG["rams"].append(q["name"])
                return self._send(200, '{"ok":true}')
            if ctl == "received":
                with lock:
                    return self._send(200, json.dumps(D.RECEIVED))
            op = q.get("op", "")
            if op == "blob" and method == "GET":
                size = int(q.get("n", "0"))
                return self._send(200, json.dumps({"ok": True, "n": size, "seed": q.get("seed", ""), "data": parts(size, q.get("seed", ""))}))
            if op == "echo" and method == "POST":
                try:
                    c = canonical(body.decode("utf-8", "replace"))
                except ValueError:
                    return self._send(400, '{"ok":false,"why":"the body is not JSON"}')
                return self._send(200, json.dumps({"ok": True, "n": len(c), "sha": hashlib.sha256(c.encode()).hexdigest()}))
            text = body.decode("utf-8", "replace")
            with lock:
                if method == "POST" and op in ("login", "enroll"):      # what the door was GIVEN, as it arrived
                    try:
                        b = json.loads(text or "")
                        D.RECEIVED.append((op, b.get("user"), b.get("password")))
                    except ValueError:
                        D.RECEIVED.append(("?", None, text))
                status, answer = D.door(method, self.path, text)
            return self._send(status, answer)

        def do_GET(self): self._do("GET")
        def do_POST(self): self._do("POST")

    s = ThreadingHTTPServer(("127.0.0.1", port), H)
    print("[DOOR-ORIGIN] [THE_DOOR_ON_A_MACHINE_V1] door_semantic_tier's door on 127.0.0.1:%d" % port, flush=True)
    s.serve_forever()


if __name__ == "__main__":
    serve(int(sys.argv[1]) if len(sys.argv) > 1 else 8091)
