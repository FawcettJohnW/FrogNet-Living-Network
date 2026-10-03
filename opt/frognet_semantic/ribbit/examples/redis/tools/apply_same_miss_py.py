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
"""[SAME_MISS_FROM_CACHE_V1], Python reference (frognet_semantic_ref) -- the same fix as tools/apply_same_miss.py makes to
the C++ engines. John 2026-09-29: "If a response had previously been sent as RESP_X and the other side said it didn't
have the answer, it is not necessary to run the prompt again. The correct full RESP can be constructed from the cache."

Before: the proxy, given RESP_SAME naming an answer its SAME store no longer held (256 there, 8192 in the daemon's
REPEAT cache), cleared its references and re-sent the request raw ([SAME_MISS_AUTORETRY_V1]) -- the daemon ran it a
second time. On the bootstrap path it answered 503 instead.
After:
  core/semcache_wire.py   OP_REQ_SAME_MISS = 0x22, wrap_req_same_miss(same_id) = [MAGIC][0x22][same_id:16], parsed.
  daemon/cache/semcache_db.py
                          the answers, by same_id, beside the REPEAT cache: raw answers as (status, headers, body),
                          semantic ones as the answer's values (opcode, instance, dyn_vals, type map) or, when the answer
                          had no values, the encoded response. Answers over SAME_BODY_MAX are not kept.
  daemon/engine/session.py
                          every answer that gets a same_id is kept; SAME is said only when the answer is held; a
                          REQ_SAME_MISS is answered from it, running nothing: RESP_RAW for a raw answer; for a semantic
                          one, RESP_DIFF encoded against NO reference (the daemon clears its response reference for the
                          instance first, in wire order) so the proxy decodes it against none -- both references then
                          stand at the answer's values. An answer no longer held is ERROR 410, never a re-run.
  proxy/transport_semantic.py
                          on a SAME it cannot resolve, the proxy clears its response reference for the instance, sends
                          REQ_SAME_MISS on the same worker, and hands the reply to its existing RESP_DIFF / RESP_RAW
                          handling. The request is never sent again.
Usage: apply_same_miss_py.py <frognet_semantic_ref dir>   (edits in place; refuses on any anchor not found exactly once)
"""
import os, sys
root = sys.argv[1]
def patch(rel, pairs):
    p = os.path.join(root, rel); s = open(p).read()
    for old, new in pairs:
        n = s.count(old)
        if n != 1: sys.exit("apply_same_miss_py: %s: anchor found %d times: %r" % (rel, n, old[:80]))
        s = s.replace(old, new)
    open(p, "w").write(s)

# ---------------------------------------------------------------- the frame
patch("core/semcache_wire.py", [
    ("OP_REQ_MISS   = 0x21\n",
     "OP_REQ_MISS   = 0x21\n"
     "OP_REQ_SAME_MISS = 0x22   # [SAME_MISS_FROM_CACHE_V1] the proxy does not hold the answer a RESP_SAME named\n"),
    ("def wrap_req_miss(req_hash: bytes) -> bytes:",
     "def wrap_req_same_miss(same_id: bytes) -> bytes:\n"
     "    \"\"\"[SAME_MISS_FROM_CACHE_V1] Ask for the answer a RESP_SAME named: [MAGIC][0x22][same_id:16]. Never a re-run.\"\"\"\n"
     "    if len(same_id) != SAME_ID_LEN:\n"
     "        raise ValueError(f\"same_id must be {SAME_ID_LEN} bytes, got {len(same_id)}\")\n"
     "    return MAGIC + bytes([OP_REQ_SAME_MISS]) + same_id\n\n\n"
     "def wrap_req_miss(req_hash: bytes) -> bytes:"),
    ("    if op == OP_REQ_MISS:\n        if len(frame) < off + H:",
     "    if op == OP_REQ_SAME_MISS:\n"
     "        if len(frame) < off + SAME_ID_LEN:\n"
     "            raise ValueError(\"short REQ_SAME_MISS frame\")\n"
     "        return WireMsg(op=op, same_id=frame[off:off+SAME_ID_LEN])\n\n"
     "    if op == OP_REQ_MISS:\n        if len(frame) < off + H:"),
    ("        OP_REQ_MISS: \"REQ_MISS\",",
     "        OP_REQ_MISS: \"REQ_MISS\",\n        OP_REQ_SAME_MISS: \"REQ_SAME_MISS\","),
])

# ---------------------------------------------------------------- the daemon's answers
patch("daemon/cache/semcache_db.py", [
    ("def _lru_invalidate(req_hash: bytes) -> None:",
     "# [SAME_MISS_FROM_CACHE_V1] The answers RESP_SAME may name, by same_id: a proxy that does not hold one asks, and is\n"
     "# answered from here -- the request is never run again. Kept beside the REPEAT cache, as many entries as it, and\n"
     "# only answers up to SAME_BODY_MAX: SAME is said only for an answer held here (answer_held).\n"
     "SAME_BODY_MAX = 64 * 1024\n"
     "_answers: \"OrderedDict[bytes, tuple]\" = OrderedDict()\n\n\n"
     "def answer_put(same_id: bytes, answer: tuple, size: int) -> None:\n"
     "    \"\"\"answer: ('raw', status, headers_bytes, body) | ('sem', opcode, inst, dyn_vals, type_map) | ('blob', sem_resp).\"\"\"\n"
     "    with _lru_lock:\n"
     "        if size > SAME_BODY_MAX:\n"
     "            _answers.pop(same_id, None); return\n"
     "        _answers[same_id] = answer\n"
     "        _answers.move_to_end(same_id)\n"
     "        while len(_answers) > _LRU_MAX:\n"
     "            _answers.popitem(last=False)\n\n\n"
     "def answer_get(same_id: bytes):\n"
     "    with _lru_lock:\n"
     "        a = _answers.get(same_id)\n"
     "        if a is not None:\n"
     "            _answers.move_to_end(same_id)\n"
     "        return a\n\n\n"
     "def answer_held(same_id: bytes) -> bool:\n"
     "    with _lru_lock:\n"
     "        return same_id in _answers\n\n\n"
     "def _lru_invalidate(req_hash: bytes) -> None:"),
])

S = "daemon/engine/session.py"
patch(S, [
    # the frame reaches its handler
    ("        elif msg.op == OP_REQ_RAW:\n            return self._handle_req_raw(msg.req_hash, msg.payload)\n        else:\n            return wrap_error(400, f\"unknown FNW1 op: {msg.op}\")",
     "        elif msg.op == OP_REQ_RAW:\n            return self._handle_req_raw(msg.req_hash, msg.payload)\n"
     "        elif msg.op == OP_REQ_SAME_MISS:\n            return self._handle_req_same_miss(msg.same_id, emit)\n"
     "        else:\n            return wrap_error(400, f\"unknown FNW1 op: {msg.op}\")\n\n"
     "    def _handle_req_same_miss(self, same_id: bytes, emit=_NO_EMIT) -> bytes:\n"
     "        \"\"\"[SAME_MISS_FROM_CACHE_V1] The proxy does not hold the answer a RESP_SAME named. Answer it from the cache;\n"
     "        run nothing. A semantic answer goes as RESP_DIFF against NO reference: this side's response reference for the\n"
     "        instance is not moved: the proxy clears its own and decodes this, which is the move a SAME hit makes.\"\"\"\n"
     "        a = semcache_db.answer_get(same_id)\n"
     "        if a is None:\n"
     "            return wrap_error(410, \"REQ_SAME_MISS: the answer RESP_SAME named is no longer held; the request was not run again\")\n"
     "        if a[0] == \"raw\":\n"
     "            _, status, headers_bytes, body = a\n"
     "            return wrap_resp_raw(same_id, status, headers_bytes, body)\n"
     "        if a[0] == \"sem\":\n"
     "            # encoded against NO reference, and the daemon's own reference left exactly as it is: later replies of\n"
     "            # this instance may already have been encoded against it (the proxy applies them after this, in turn)\n"
     "            _, resp_opcode, inst, dyn_vals, type_map = a\n"
     "            _cur = _get_response_reference(self.peer_ip, resp_opcode, inst)\n"
     "            _clear_response_reference(self.peer_ip, resp_opcode, inst)\n"
     "            try:\n"
     "                wire_resp = _diff_encode_response(self.peer_ip, resp_opcode, inst, dyn_vals, type_map or {})\n"
     "            finally:\n"
     "                if _cur is None: _clear_response_reference(self.peer_ip, resp_opcode, inst)\n"
     "                else: _set_response_reference(self.peer_ip, resp_opcode, inst, _cur)\n"
     "        else:\n"
     "            wire_resp = a[1]\n"
     "        return wrap_resp_diff(same_id, wire_resp)"),
    # imports
    ("    OP_REQ_FULL, OP_REQ_REPEAT, OP_REQ_RAW, OP_REQ_DIFF, OP_ERROR,",
     "    OP_REQ_FULL, OP_REQ_REPEAT, OP_REQ_RAW, OP_REQ_DIFF, OP_ERROR, OP_REQ_SAME_MISS,"),
    # SAME only for a held answer -- the four decisions
    ("            if (old_raw_hash and new_raw_hash == old_raw_hash\n                    and old_same_id and len(old_same_id) == 16):\n                _debug(f\"{req_type}->RESP_SAME(raw) for {req_hash.hex()}\")",
     "            if (old_raw_hash and new_raw_hash == old_raw_hash\n                    and old_same_id and len(old_same_id) == 16\n"
     "                    and semcache_db.answer_held(old_same_id)):   # [SAME_MISS_FROM_CACHE_V1]\n"
     "                _debug(f\"{req_type}->RESP_SAME(raw) for {req_hash.hex()}\")"),
    ("        if new_raw_hash == old_raw_hash:\n            # Response unchanged - RESP_SAME\n",
     "        if new_raw_hash == old_raw_hash and semcache_db.answer_held(old_same_id):   # [SAME_MISS_FROM_CACHE_V1]\n            # Response unchanged - RESP_SAME\n"),
    ("        if new_raw_hash == old_raw_hash:\n            _debug(f\"REQ_REPEAT(raw)->RESP_SAME for {req_hash.hex()}\")",
     "        if new_raw_hash == old_raw_hash and semcache_db.answer_held(old_same_id):   # [SAME_MISS_FROM_CACHE_V1]\n            _debug(f\"REQ_REPEAT(raw)->RESP_SAME for {req_hash.hex()}\")"),
    ("                    and old_same_id and len(old_same_id) == 16):\n                emit.begin()                             # [REFERENCES_MOVE_IN_WIRE_ORDER_V1]",
     "                    and old_same_id and len(old_same_id) == 16\n"
     "                    and semcache_db.answer_held(old_same_id)):   # [SAME_MISS_FROM_CACHE_V1]\n"
     "                emit.begin()                             # [REFERENCES_MOVE_IN_WIRE_ORDER_V1]"),
    # every answer that gets a same_id is kept -- the five writers
    ("            semcache_db._lru_put(req_hash, (sem_req, new_raw_hash, new_same_id, True))",
     "            semcache_db._lru_put(req_hash, (sem_req, new_raw_hash, new_same_id, True))\n"
     "            semcache_db.answer_put(new_same_id, (\"raw\", status, headers_bytes, body), len(body))   # [SAME_MISS_FROM_CACHE_V1]"),
    ("                semcache_db._lru_put(req_hash, (sem_req, new_raw_hash, new_same_id, False))",
     "                semcache_db._lru_put(req_hash, (sem_req, new_raw_hash, new_same_id, False))\n"
     "                semcache_db.answer_put(new_same_id, (\"sem\", resp_opcode, inst, dyn_vals, type_map) if (dyn_vals is not None and resp_opcode is not None) else (\"blob\", sem_resp), len(sem_resp))   # [SAME_MISS_FROM_CACHE_V1]"),
    ("                semcache_db._lru_put(req_hash, (http_req, new_raw_hash, new_same_id, True))",
     "                semcache_db._lru_put(req_hash, (http_req, new_raw_hash, new_same_id, True))\n"
     "                semcache_db.answer_put(new_same_id, (\"raw\", status, headers_bytes, resp_body_bytes), len(resp_body_bytes))   # [SAME_MISS_FROM_CACHE_V1]"),
    ("            semcache_db._lru_put(req_hash, (sem_req, new_raw_hash, sid, False))",
     "            semcache_db._lru_put(req_hash, (sem_req, new_raw_hash, sid, False))\n"
     "            semcache_db.answer_put(sid, (\"sem\", resp_opcode, inst, dyn_vals, type_map) if (dyn_vals is not None and resp_opcode is not None) else (\"blob\", sem_resp), len(sem_resp))   # [SAME_MISS_FROM_CACHE_V1]"),
    ("            semcache_db._lru_put(req_hash, (_req_blob, new_raw_hash, sid, False))",
     "            semcache_db._lru_put(req_hash, (_req_blob, new_raw_hash, sid, False))\n"
     "            semcache_db.answer_put(sid, (\"sem\", resp_opcode, inst, dyn_vals, type_map) if (dyn_vals is not None and resp_opcode is not None) else (\"blob\", sem_resp), len(sem_resp))   # [SAME_MISS_FROM_CACHE_V1]"),
])

# ---------------------------------------------------------------- the proxy
# Each old miss branch is replaced whole (from its start marker through its end marker, each found exactly once).
P = "proxy/transport_semantic.py"
def replace_span(rel, start, end, new):
    p = os.path.join(root, rel); s = open(p).read()
    if s.count(start) != 1: sys.exit("apply_same_miss_py: %s: start marker found %d times: %r" % (rel, s.count(start), start[:80]))
    a = s.index(start); b = s.find(end, a)
    if b < 0: sys.exit("apply_same_miss_py: %s: end marker not found after %r" % (rel, start[:60]))
    s = s[:a] + new + s[b + len(end):]
    open(p, "w").write(s)
patch(P, [("    try_parse, wrap_req_full, wrap_req_repeat, wrap_req_raw,\n",
           "    try_parse, wrap_req_full, wrap_req_repeat, wrap_req_raw, wrap_req_same_miss,\n")])
# semantic path
replace_span(P,
    "        if cached_body is None:\n            # [SAME_MISS_AUTORETRY_V1] LRU + DB miss. Drop both",
    "                headers=req_headers, body=body,\n            )\n",
    "        if cached_body is None:\n"
    "            # [SAME_MISS_FROM_CACHE_V1] (replaces [SAME_MISS_AUTORETRY_V1], which re-sent the request raw: the daemon\n"
    "            # ran it a second time). The daemon already ran this request and holds the answer it called SAME: ask for\n"
    "            # it on the same worker. A semantic answer comes as RESP_DIFF against no reference, so this side clears\n"
    "            # its own first; a raw one comes as RESP_RAW. Each is applied exactly as a first answer would be.\n"
    "            _clear_response_reference(target_ip, opcode, inst)\n"
    "            _worker = _ignored.get(\"worker\") or _get_worker(target_ip, _DAEMON_PORT)\n"
    "            try:\n"
    "                wire_reply = _worker.call(wrap_req_same_miss(msg.same_id))\n"
    "                msg = try_parse(wire_reply)\n"
    "            except Exception as e:\n"
    "                return send_error_reply(handler, 502, f\"REQ_SAME_MISS to {target_ip} failed: {e!r}\", ctx=ctx, where=\"same_miss_rpc\")\n"
    "            if msg is not None and msg.op == OP_RESP_DIFF:\n"
    "                _handle_resp_diff(handler, codec, resp_tpl, msg.same_id, msg.payload, req_hash, target_ip, opcode, inst)\n"
    "                return\n"
    "            if msg is not None and msg.op == OP_RESP_RAW:\n"
    "                return _handle_resp_raw_and_learn(\n"
    "                    handler, ctx, store, msg.same_id, msg.status, msg.headers, msg.body,\n"
    "                    req_hash, target_ip, method, path, headers={}, req_body=b\"\",\n"
    "                )\n"
    "            _why = getattr(msg, \"message\", None) if msg is not None else None\n"
    "            return send_error_reply(handler, 502, f\"REQ_SAME_MISS answered {_why or msg!r}; the request was not run again\", ctx=ctx, where=\"same_miss_answer\")\n")
# bootstrap (raw) path
replace_span(P,
    "        if cached_body is None:\n            # [SAME_MISS_AUTORETRY_V1] LRU + DB miss on the RAW bootstrap",
    "                        \"same_id\": msg.same_id.hex()})\n",
    "        if cached_body is None:\n"
    "            # [SAME_MISS_FROM_CACHE_V1] (replaces the 503): ask the daemon for the answer it called SAME; serve it as\n"
    "            # the RESP_RAW it is. The request is not sent again.\n"
    "            try:\n"
    "                wire_reply = worker.call(wrap_req_same_miss(msg.same_id))\n"
    "                msg = try_parse(wire_reply)\n"
    "            except Exception as e:\n"
    "                return send_error_reply(handler, 502, f\"REQ_SAME_MISS to {target_ip} failed: {e!r}\", ctx=ctx, where=\"bootstrap_same_miss_rpc\")\n"
    "            if msg is not None and msg.op == OP_RESP_RAW:\n"
    "                return _handle_resp_raw_and_learn(\n"
    "                    handler, ctx, store, msg.same_id, msg.status, msg.headers, msg.body,\n"
    "                    req_hash, target_ip, method, path, headers, body,\n"
    "                )\n"
    "            _why = getattr(msg, \"message\", None) if msg is not None else None\n"
    "            return send_error_reply(handler, 502, f\"REQ_SAME_MISS answered {_why or msg!r}; the request was not run again\", ctx=ctx, where=\"bootstrap_same_miss_answer\")\n")
print("apply_same_miss_py: applied")
