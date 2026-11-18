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
template_order.py - [THE_FRAME_SAYS_ITS_FIELD_ORDER_V1] the fingerprint of a template's field order.

John, 2026-11-17: "Fingerprint or hash, either one".

A semantic frame carries values by POSITION: the encoder writes them in its template's field order and the decoder
reads them by its own. Each machine has one template store, learned from what that machine has seen, and an opcode
is the path's, so two machines can hold different field orders under one opcode. Found between machines on
2026-11-17 (simulation/wire_between_machines_tier.py, battery (c)): one machine's store had 63 fields for a reply
and the other's 23; the reply was encoded by one and decoded by the other, and reached the client as HTTP 200 with
its values in the wrong fields. Nothing in the frame said which order it was encoded with.

So the asker says it. Every REQ_FULL, REQ_DIFF and REQ_REPEAT ends with ORDER_MAGIC and two fingerprints: of the
asker's REQUEST template (the order it encoded this request in) and of its REPLY template (the order it will decode
the answer in). The daemon compares each with its own template's:

  request order differs   the request is NOT decoded and NOT run. The daemon answers as it does when it has no
                          template for the opcode, and the proxy sends the raw request, from which both learn.
  reply order differs     the request is run, and the answer goes back RAW (RESP_RAW: its own status, headers and
                          body), from which the proxy learns. Never positional values the asker would misread.

Nothing is run twice and nothing is guessed. A frame without the trailer (a node whose code predates this, or the
C++ engine in ribbit_cpp/, which does not send it yet) is handled as it always was: with no check.

The fingerprint is 8 bytes of SHA-256 over what the position of a value depends on: the URL keys in order, the
fields in order, and each one's type.
"""
import hashlib
import json

ORDER_MAGIC = b"FO"
PRINT_LEN = 8
TRAILER_LEN = len(ORDER_MAGIC) + 2 * PRINT_LEN


def order_print(tpl) -> bytes:
    """The 8-byte fingerprint of a request or reply template's field order (either side's template object)."""
    frag = getattr(tpl, "fragment", None) or {}
    url_keys = list(getattr(tpl, "url_query_keys", None) or [])
    fields = list(frag.get("field_order") or [])
    types = getattr(tpl, "type_map", None) or {}
    text = json.dumps([url_keys, fields, [str(types.get(f)) for f in url_keys + fields]], separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).digest()[:PRINT_LEN]


def trailer(req_tpl, resp_tpl) -> bytes:
    return ORDER_MAGIC + order_print(req_tpl) + order_print(resp_tpl)


def read_trailer(rest: bytes):
    """(request fingerprint, reply fingerprint) from what follows a frame's payload, or None if it is not there."""
    if len(rest) == TRAILER_LEN and rest[:len(ORDER_MAGIC)] == ORDER_MAGIC:
        return rest[2:2 + PRINT_LEN], rest[2 + PRINT_LEN:]
    return None
