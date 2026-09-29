# Porting the FrogNet semantic engine to C++ (Ribbit-LISP client and RAM server)

Claude, 2026-09-24. Source: frognet-source-20260914_happydog.tgz, `/opt/frognet_semantic`. Everything below is read
from that code.

## The defect

`ribbit_cpp/frogram.cpp`, and the `ram_server.cpp` it pairs with, speak a reduced FNW1: REQ_RAW, REQ_REPEAT,
RESP_RAW, RESP_SAME, REQ_MISS, ERROR, HELLO. The FrogNet contract is larger. Every byte and WAN figure measured with
the reduced client is a reduced-client figure. The correctness results don't depend on the encoding, so they stand.
The application layer needs no change: participants use only `frogram::Memory`.

## The contract, as implemented in Python

**Wire: `core/semcache_wire.py` (465 lines), FNW1 v4.1, frames `[MAGIC "FNW1"][op:1][payload]`, request hash 16 bytes.**

| Direction | Opcodes |
|---|---|
| Proxy → daemon | REQ_FULL 0x01 (semantic request), REQ_REPEAT 0x02, REQ_RAW 0x03, REQ_DIFF 0x04 (changed fields only), HELLO 0x50 |
| Daemon → proxy | RESP_DIFF 0x11 (same_id + semantic blob), RESP_SAME 0x13, RESP_RAW 0x14, REQ_MISS 0x21, ERROR 0x30, SEQ_RESET 0x40 |
| Either way | RTT_PING, RTT_PONG, RTT_LOOP |

**Codec: `core/codec.py` (487 lines), `SemanticCodec`, WIRE_VERSION 5.**

- Typed values: NULL 0, RAW 1, STR 2, INT 3 (int64 since v5), FLOAT 4, BOOL 5, JSON 7.
- `encode_request_diff` / `encode_reply_diff` compare against a per-(target, opcode) reference:
  - `None` means never sent, so the whole thing is sent;
  - identical to the reference means REPEAT or SAME;
  - otherwise only the changed fields are sent, as `[idx:u16][type:u8][value]`.
- Flags: FLAG_DIFF, and FLAG_COMPRESSED from `_lz4_smart`, which applies LZ4 only when it shrinks the payload.

**Templates: `core/template.py`, plus `proxy/templates.py` and `daemon/engine/template.py`.**

- `RequestTemplate` and `ReplyTemplate`, learned from raw exchanges (`_handle_raw_and_learn`,
  `_handle_resp_raw_and_learn`).
- They separate the stable structure from the dynamic fields: `extract_dynamic`, `build_url`, `rebuild_body`,
  `rebuild`.

**Handlers: `core/json_handler.py` (653), `xml_handler`, `html_handler`, `text_handler`, `unrest_handler`,
`type_detect`, `format_registry`.** These are the BLDC wire-language handlers.

**Client side (proxy): `proxy/transport_semantic.py` (3,064 lines).**

- Per-target daemon worker with a permanent socket.
- Request and response references.
- SAME LRU and RPC coalescing.
- `_handle_semantic_request`, and RESP_DIFF application (`_handle_resp_diff`).
- The media planes (`proxy/frognet_media_planes.py`, 693 lines) are the high-speed data socket side.

**Server side (daemon): `daemon/engine/session.py` (1,657), `server.py`, `execution.py`, `data_cache.py`, `template.py`,
`daemon/cache/semcache_db.py`.** It decodes REQ_FULL/DIFF against its own reference, executes against api.php, and
answers SAME, DIFF or RAW.

## Plan: each slice red first, with his Python as the oracle

Each slice's oracle is the Python module itself, imported and run, in the same way the Dino byte oracle runs
lispers.net. Byte-identical output from the same input is the acceptance test.

| Slice | Contents | Oracle |
|---|---|---|
| S1 | Wire framing, every opcode (C++ encode/parse); RTT ping/pong | `core/semcache_wire.py` wrap_* / try_parse byte-identical, round trip both ways |
| S2 | Codec v5: typed values, full and diff encode/decode, `_lz4_smart` (needs liblz4) | `core/codec.py` byte-identical on a corpus of request/reply field sets, including int64 edges and compression thresholds |
| S3 | Templates: learn from RAW, extract dynamic fields, rebuild; the JSON handler first (Ribbit bags are JSON) | `core/template.py` + `core/json_handler.py` on the same bodies |
| S4 | Client engine in `frogram`: per-target references, REQ_FULL/REPEAT/DIFF, RESP_SAME/DIFF/RAW application, REQ_MISS recovery, SAME LRU. `frogram::Memory` unchanged | Against the real Python daemon (`daemon_main.py`) serving a RAM; the Ribbit-LISP suite and all independent tests must stay green |
| S5 | Server side in the C++ RAM server: session references, SAME/DIFF replies | The real Python proxy against the C++ server, plus the C++ client against both servers |
| S6 | High-speed data socket: the optional permanent data connection (media planes / Plane contract) | `proxy/frognet_media_planes.py` behaviour; characterized before coded |
| S7 | Re-measure every byte and WAN figure (native vs UDP, per change, heartbeat, streamingfrog) on the full engine; retire the reduced-client numbers | — |

## Duplicate code

As agreed, the C++ engine duplicates the Python for now. The Python stays the reference, and the oracles tie the two
together, so a change on either side fails the other's oracle until both agree.

## Open questions, answered from the source before each slice rather than assumed

- S4: which HTTP-shaped request a memory call becomes on the semantic path. The reduced client wraps it as
  `{method, path, headers, body, host, port}`; the real proxy's `_serialize_http_request` defines this.
- S6: whether the RAM path uses the data socket at all, or only media and tensor traffic does. The architecture says
  Memory does not traverse it; the port must preserve that.
