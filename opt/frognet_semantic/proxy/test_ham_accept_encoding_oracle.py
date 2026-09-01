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
[HAM_ACCEPT_ENCODING_V1] Oracle - a terminal external (HAM) fetch must negotiate
content-encoding with the origin.

WHY: the HAM branch (proxy_main) fetches an external page and relays the body
straight back to the client -- there is no daemon/FNWP leg, so the semantic codec's
LZ4 never touches it. real_upstream_remote_80 used to force Accept-Encoding: identity
on EVERY fetch, which is correct for the semantic/local/next-hop path (BLDC-1 must
template the body) but on an external fetch merely suppresses the origin's own gzip/br
-- the one compression lever that survives the forwarded/NAT'd IP path back to the
requesting node.

FAILS on old code: no allow_encoding kwarg -> identity forced -> origin sends the page
uncompressed and nothing downstream can compress it.

PASSES on new code: the HAM call site passes allow_encoding=True, the origin's offer
permits gzip, the origin compresses, and the gzipped body (Content-Encoding: gzip) is
returned for a straight relay to the client.

REGRESSION GUARD: the default (allow_encoding=False) still forces identity, so the
templating path is unchanged.

No network: MarkedHTTPConnection is monkeypatched with a fake origin that gzips iff the
Accept-Encoding it receives permits it.
"""
import gzip
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import proxy.transport_real as TR

_CAPTURED = {}


class _FakeResp:
    def __init__(self, status, headers, body):
        self.status = status
        self.headers = headers
        self._body = body

    def read(self):
        return self._body


class _FakeConn:
    """Stands in for MarkedHTTPConnection. Records the Accept-Encoding it is handed
    and gzips the response iff that offer permits gzip -- i.e. behaves like a normal
    content-negotiating origin."""

    def __init__(self, host, port, timeout=None):
        self.host = host
        self.port = port

    def request(self, method, path, body, headers):
        ae = ""
        n = 0
        for k, v in (headers or {}).items():
            if k and k.lower() == "accept-encoding":
                ae = v
                n += 1
        _CAPTURED["accept_encoding"] = ae
        _CAPTURED["ae_count"] = n

    def getresponse(self):
        ae = (_CAPTURED.get("accept_encoding") or "").lower()
        payload = b"<html>" + b"x" * 20000 + b"</html>"
        if "gzip" in ae:
            return _FakeResp(200,
                             {"Content-Type": "text/html", "Content-Encoding": "gzip"},
                             gzip.compress(payload))
        return _FakeResp(200, {"Content-Type": "text/html"}, payload)

    def close(self):
        pass


def _call(allow):
    kwargs = dict(origin_local=False, control_plane=False)
    if allow is not None:
        kwargs["allow_encoding"] = allow
    return TR.real_upstream_remote_80(
        "GET", "/", b"", {"Accept-Encoding": "gzip, deflate, br"},
        "example.com", "93.184.216.34", **kwargs)


def main():
    TR.MarkedHTTPConnection = _FakeConn
    failures = []

    # 1) EXTERNAL (HAM) fetch: allow_encoding=True must offer gzip and return compressed.
    try:
        resp, _ = _call(True)
    except TypeError as e:
        print("FAIL [external]: real_upstream_remote_80 has no allow_encoding kwarg -- "
              "OLD code forces Accept-Encoding: identity unconditionally (%r)." % e)
        print("RESULT: FAIL  (expected on old code)")
        sys.exit(1)

    ae = _CAPTURED.get("accept_encoding", "")
    ce = (resp.get("headers") or {}).get("Content-Encoding", "")
    body = resp.get("body") or b""
    if "gzip" not in ae.lower():
        failures.append(f"external fetch offered Accept-Encoding={ae!r}; expected gzip to be offered")
    if _CAPTURED.get("ae_count", 0) != 1:
        failures.append(f"origin saw {_CAPTURED.get('ae_count')} Accept-Encoding headers; expected exactly 1")
    if ce.lower() != "gzip":
        failures.append(f"response Content-Encoding={ce!r}; expected gzip")
    if body[:2] != b"\x1f\x8b":
        failures.append("returned body is not gzip-compressed (missing \\x1f\\x8b magic)")

    # 2) REGRESSION GUARD: default fetch (semantic/local/next-hop) must still force identity.
    _CAPTURED.clear()
    resp2, _ = _call(None)  # default => allow_encoding False
    ae2 = _CAPTURED.get("accept_encoding", "")
    if ae2.lower() != "identity":
        failures.append(f"default fetch offered Accept-Encoding={ae2!r}; expected identity "
                        "(BLDC-1 templating path must not regress)")
    ce2 = (resp2.get("headers") or {}).get("Content-Encoding", "")
    if ce2:
        failures.append(f"default fetch received compressed response ({ce2!r}); templating path would break")

    if failures:
        print("RESULT: FAIL")
        for f in failures:
            print("  -", f)
        sys.exit(1)

    print("RESULT: PASS")
    print(f"  external (HAM) fetch offered Accept-Encoding={ae!r} -> origin returned "
          f"Content-Encoding={ce!r}, {len(body)}B gzipped body relayed to the client")
    print(f"  default fetch forced Accept-Encoding={ae2!r} (semantic/local/next-hop path unchanged)")
    sys.exit(0)


if __name__ == "__main__":
    main()
