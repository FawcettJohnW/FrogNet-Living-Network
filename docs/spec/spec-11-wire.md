<!-- Generated from the site page spec-11-wire.html (https://fawcettinnovations.com/spec-11-wire.html) on 2026-11-15. The site is the rendered edition; this is the same text for reading in the repository. -->

Specification §11

# FNWP-1 and BLDC-1.

BLDC-1 decides what to send; FNWP-1 decides how it travels. Key words are as defined in §1.2 \[RFC2119\] \[RFC8174\].

Author commentary

The author walking through this material on camera. It is commentary, not specification: where the video and this document disagree, this document governs (§18.8). The full series is at [watch](https://fawcettinnovations.com/watch.html).

BLDC-1 and FNWP-1, with live measurements

## Current implementation clauses: October 12, 2026

## FULL, SAME and DIFF

A semantic engine retains learned structure and prior values. A full representation establishes state; an unchanged answer can use SAME; changes can be encoded against shared structure. This is not encryption.

## Frame accounting

The historical 21-byte SAME figure names the protocol frame. TCP/IP headers, framing, acknowledgments and the concrete exchange add wire cost. Published workload ratios use the harness’s accounting and payload conditions.

## Session paths

The current region platform has request, return and data sockets, with markers and segmented responses. Do not mix the older FNWP-1 HTTP intercept description with FNW1 session framing as if they were one byte layout.

## Independent member

Kotlin’s member sends REQ_RAW and reads RESP_RAW or OP_ERROR and the session’s marker/data behavior. Its interoperability is evidence of the raw wire contract, not evidence that Kotlin runs the semantic compression engine.

## Related current material.

[FD&F](https://fawcettinnovations.com/fdf.html) · [Programming](https://fawcettinnovations.com/programming.html) · [Memory contract](https://fawcettinnovations.com/memory-contract.html) · [Evidence](https://fawcettinnovations.com/evidence.html) · [Source baseline](https://fawcettinnovations.com/implementation.html)

The detailed clauses, rationale, source citations and author commentary below remain the mechanism reference. Where the region or the modern Ribbit contract differs, use the implementation-specific scope stated above. Clauses below that name a broker describe the mechanism the region replaced on 12 October 2026 (§7; docs/cleanup-2026-10/T3b.md); the node has two modes, LAN-only and region, and no third (internet_tunnels_v3/config.py \[NO_BROKER_V1\]).

FrogNet Specification — Draft 0.9, revision 2026-08-29. Cite a conformance claim against this revision, not against a section number alone.

In this document

1.  [1Division of labour](#w1)
2.  [2The pathway](#w2)
3.  [3Frame format](#w3)
4.  [4The semantic packet](#w4)
5.  [5Engagement](#w5)
6.  [6Request identity](#w6)
7.  [7The first exchange](#w7)
8.  [7aTemplate lifecycle](#w7a)
9.  [8The reference, and its three states](#w8)
10. [9How SAME is proven](#w9)
11. [10Twenty-one bytes](#w10)
12. [11Sequencing, fan-in and fan-out](#w11)
13. [12Collapsing duplicate work](#w12)
14. [13Change propagation](#w13)
15. [14What each side retains](#w14)
16. [15Non-goals](#w15)
17. [16References](#w16)

## 1. Division of labour

Croakus: [§26](https://fawcettinnovations.com/magnum-croakus.html#26-the-accelerant-semantic-compression), [§27](https://fawcettinnovations.com/magnum-croakus.html#27-fnwp-1-the-wire), [§28](https://fawcettinnovations.com/magnum-croakus.html#28-the-object-oriented-handler), Appendix F

Two mechanisms are specified here and MUST NOT be conflated.

| Name     | Stands for                                     | Decides                                                                |
|----------|------------------------------------------------|------------------------------------------------------------------------|
| `BLDC-1` | Bullfrog Long Distance Communications Protocol | *What* to send: a full body, a difference, or an assertion of identity |
| `FNWP-1` | FrogNet Wire Protocol                          | *How* it travels: framing, multiplexing, and reply matching            |

BLDC-1 is the language; FNWP-1 is the envelope. A third protocol, **SotF** — the Song of the Frogs Adaptive Communications Protocol — is the Communicator's media plane and is specified in §15; its control plane rides BLDC-1 and FNWP-1 like any other exchange — a session envelope that reaches SAME against a learned template — while its media frames carry opaque payloads that never reach SAME and are sent in full every time (§15).

The verdict is reached at the handler on each side, by the codec, over canonical values — **never on the wire**. FNWP-1's involvement begins only after the verdict exists.

## 2. The pathway

``` code
THIS NODE
  1  app  --HTTP :80-->  proxy  -->  handler (BLDC-1)
  2  handler: compare canonical answer against reference
             -> SAME | DIFF | FULL   (+ request hash)
  3  FNWP-1 frame  -->  established socket  -->

PEER NODE
  4  FNWP-1 reader  -->  handler (BLDC-1)
  5  handler: rebuild full request from reference + diff
  6  origin (Apache -> MySQL) produces the answer
  7  handler: compare canonical answer against reference
             -> SAME | DIFF | FULL
  8  FNWP-1 frame  -->  back over the return socket

THIS NODE
  9  handler: rebuild full reply from reference + diff
 10  proxy  --HTTP-->  app
```

Every hop is one handler turning a full message into a verdict against a shared reference, and the handler on the far side turning that verdict back into the full message. Reconstruction is exact because both sides retain the reference. The application above the line makes ordinary HTTP calls and MUST require no modification.

## 3. Frame format

**Do not coordinate the writers. Preserve their independent truths and let the reader interpret the state (§10.3b).** The request/reply framing here is transport between a proxy and a daemon. It is not the application’s programming model, and an application MUST NOT be written as request/reply on top of it (§10.2).

Every FNWP-1 message is a length-prefixed frame. A reader therefore always knows how many bytes to expect before the next frame begins, which is what permits many frames to share one stream unambiguously.

The transport-framed unit is the four-byte length followed by the frame it describes. All multi-byte integers in the frame header are unsigned and big-endian.

``` code
 0                   1                   2                   3
 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|                    LENGTH (of all that follows)               |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|         MAGIC = 0x46 0x4E 0x57 0x31  ("FNW1")                 |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|    OPCODE     |                                               |
+-+-+-+-+-+-+-+-+                                               |
|         IDENTIFIER (16 octets: req_hash or same_id)           |
|                                                               |
|                                                               |
|                               +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|                               |     PAYLOAD LENGTH (variable  |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+     opcodes only)             |
|                               +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|                    PAYLOAD (variable opcodes only)            |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
```

| Field            | Offset | Width    | Encoding             | Sender                                                                          | Receiver                                                                                               |
|------------------|--------|----------|----------------------|---------------------------------------------------------------------------------|--------------------------------------------------------------------------------------------------------|
| `LENGTH`         | −4     | 4        | unsigned, big-endian | MUST equal the octet count of everything after it in this frame                 | MUST read exactly that many octets before parsing; MUST NOT assume a frame boundary at a read boundary |
| `MAGIC`          | 0      | 4        | literal `FNW1`       | MUST emit literally                                                             | MUST reject the frame with `OP_ERROR` 400 on mismatch; MUST NOT attempt resynchronisation              |
| `OPCODE`         | 4      | 1        | unsigned             | MUST be one of the values in the table below                                    | MUST answer an unknown opcode with `OP_ERROR` 400                                                      |
| `IDENTIFIER`     | 5      | 16       | opaque octets        | `req_hash` on requests, `same_id` on responses (§11.6, §11.9)                   | MUST NOT interpret the octets other than by equality                                                   |
| `PAYLOAD LENGTH` | 21     | 4        | unsigned, big-endian | present only for opcodes carrying a payload; MUST equal the payload octet count | MUST reject a frame whose payload length disagrees with `LENGTH`                                       |
| `PAYLOAD`        | 25     | variable | per opcode           | —                                                                               | —                                                                                                      |

The fixed-size opcodes — `REQ_REPEAT`, `RESP_SAME`, `REQ_MISS` — carry no payload length and no payload, and are therefore exactly **21 octets** of frame, 25 with the transport length (§11.10).

**Malformed frames.** A receiver answers a frame it cannot parse with `OP_ERROR` and reads the next one: the four-byte length has already fixed where that frame ends. Recovery by resynchronisation inside a frame is not defined and MUST NOT be attempted: the identifier space is opaque, so a receiver has no way to distinguish a frame boundary from payload that resembles one.

``` code
MAGIC       b"FNW1"          4 bytes, literal, every frame
opcode      1 byte

REQ_FULL    [MAGIC][0x01][req_hash:16][len:4][semantic_packet:len]
REQ_REPEAT  [MAGIC][0x02][req_hash:16]
REQ_RAW     [MAGIC][0x03][req_hash:16][len:4][http_request:len]
REQ_DIFF    [MAGIC][0x04][req_hash:16][len:4][semantic_diff:len]
RESP_SAME   [MAGIC][0x13][same_id:16]
RESP_DIFF   [MAGIC][0x11][same_id:16][len:4][sem_blob:len]
RESP_RAW    [MAGIC][0x14][same_id:16][len:4][status:2][hdrs_len:2][hdrs][body]
```

The magic value is the ASCII string `FNW1` and MUST be emitted literally. **It is a wire constant, not the protocol name.** The protocol is FNWP-1; published material naming the protocol “FNW1” is using an obsolete name for it. Hash and identifier fields are 16 bytes (`REQ_HASH_LEN`, `core/codec.py`:51); `SAME_ID_LEN` is likewise 16 (`core/semcache_wire.py`:101).

**Byte order differs between the two layers and MUST NOT be assumed uniform.** FNWP-1 frame length and status prefixes are **big-endian** (network order): `struct.pack("!I", ...)` for payload lengths, `"!HH"` for status and header length. The semantic packet inside them is **little-endian** throughout: `"<BBIH"` for the header, `"<HB"` per field, `"<q"` and `"<d"` for scalars (`core/codec.py`). An implementation MUST encode the envelope big-endian and the contents little-endian.

The complete opcode set, verified in `core/semcache_wire.py`:

``` code
OP_LEGACY      0x00      pre-FNWP-1 framing
OP_REQ_FULL    0x01      OP_RESP_DIFF   0x11
OP_REQ_REPEAT  0x02      OP_RESP_SAME   0x13
OP_REQ_RAW     0x03      OP_RESP_RAW    0x14
OP_REQ_DIFF    0x04
OP_REQ_MISS    0x21      the hash you sent means nothing to me
OP_ERROR       0x30      [MAGIC][0x30][status:2][msg_len:2][msg]
OP_SEQ_RESET   0x40      [MAGIC][0x40]                 — no payload
OP_HELLO       0x50      [MAGIC][0x50][ip_len:1][ip_str]
OP_RTT_PING    0x60      OP_RTT_PONG    0x61      OP_RTT_LOOP  0x62
```

`REQ_MISS` carries the request hash: `[MAGIC][0x21][req_hash:16]`. Hash truncation to `REQ_HASH_LEN` on the wire is a v4.1 change and saves 16 bytes per REQ_FULL, REQ_REPEAT, REQ_RAW and REQ_MISS; internally the hash is a full 32-byte SHA-256.

## 4. The semantic packet

The packet carried inside FULL and DIFF frames has its own header, `<BBIH>`: wire version, flags, opcode, field count. `WIRE_VERSION` is currently 5 (`core/codec.py`).

| Flag              | Value | Meaning                                                                                                                                                                                               |
|-------------------|-------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `FLAG_COMPRESSED` | 0x01  | LZ4 applied. Applied only where it actually shrinks the payload — a general-purpose compressor's own framing runs to a dozen bytes, which on a fifty-byte sensor reading is an expense, not a saving. |
| `FLAG_DIFF`       | 0x02  | Not all fields are present.                                                                                                                                                                           |

Field types are one byte per field alongside a two-byte index (`"<HB"`):

``` code
TYPE_NULL  0    TYPE_RAW   1    TYPE_STR   2    TYPE_INT   3
TYPE_FLOAT 4    TYPE_BOOL  5    TYPE_JSON  7    (dict or list only)
in a DIFF only, against the reference:
TYPE_SPLICE 8   TYPE_INT_DELTA 9   TYPE_FLOAT_DELTA 10   TYPE_DECSTR_DELTA 11
```

`WIRE_VERSION = 5` widened `TYPE_INT` from int32 to int64. **A version mismatch is a hard failure, not a negotiation**: v4 peers are rejected by `_assert_version`. An implementation MUST NOT attempt to interoperate across wire versions.

## 5. Engagement

Compression is not free and MUST NOT be unconditional. The decision is made **per hop**, about the destination identity (the kernel does the routing), and the default is the fast path everywhere. Semantic mode engages when the next hop measures slow, or when policy names it explicitly.

The measurement MUST be smoothed and the switch MUST be sticky: a running average rather than the last sample, distinct thresholds for entering and leaving semantic mode, and a minimum dwell time in a mode before it may change again. A link hovering near a single threshold would otherwise flap and spend more effort switching than either mode saves.

Verified in `proxy/decision.py`. Semantic-ness is evaluated at the destination identity; the kernel does the actual routing:

``` code
ENTER_SEM_MS            250.0    enter semantic when EWMA RTT >= this
EXIT_SEM_MS             200.0    leave when EWMA RTT <= this
EWMA_ALPHA              0.25     smoothing; higher is more responsive
HOLD_DOWN_SEC           30.0     minimum dwell after any mode switch
PROBE_MIN_INTERVAL_SEC   2.0     maximum active probe rate per next hop
PROBE_TIMEOUT_SEC        2.0     TCP probe timeout
```

`EXIT_SEM_MS` MUST be strictly below `ENTER_SEM_MS`; the gap is the hysteresis. A mode change MUST NOT be considered while inside the hold-down. Explicit policy — a next hop named in the semantic override address or network sets, or reachable as a `via` for any semantic subnet — forces semantic mode regardless of RTT; there is no policy that forces the fast path. The fast path is allowed across every interface; RTT governs semantic mode only.

## 6. Request identity

A template is useful only if the correct one is selected every time, so a request MUST be reduced to an identity. The URL is split in two: a small set of stable keys — the operation, the entity, the kind of thing asked for — which form the identity, and everything else, which is a value.

``` code
WORKED EXAMPLE — three URLs reduced to identity and values

  /propogateNotification.php?event=abc
      template: /propogateNotification.php
      values:   event

  /api.php?entity=sensors&action=values&SensorName=X&parse=1
      template: /api.php?entity=sensors&action=values
      values:   SensorName, parse

  /frognet_echo.php
      template: /frognet_echo.php
      values:   (none)
```

**The identity is the shape of the answer, not the contents of it.** Two consequences of that rule are normative because both were established by defect:

A doubled slash in a path MUST be collapsed before the URL is parsed. A standard parser reads a leading double slash as the start of a host name and returns an empty path; without the collapse, one endpoint reached two ways forks into two templates and compression halves with no visible cause.

Parameters that change how many rows return, or in what order, MUST be treated as values and MUST NOT enter the identity. They change the quantity and sequence of the answer, not its shape; keying on them produces a separate template for every value anyone ever asks for.

Where the body determines the shape, the body participates in the identity. A sensor upsert folds the sensor type and metric name into it, so every reading of that kind across the mesh shares one template regardless of which node emitted it. A previously unseen sensor therefore rides an existing template immediately: the template was learned for the shape, not for the sensor.

## 7. The first exchange

The first exchange of a given kind to a given peer has no template. It MUST be sent uncompressed, once, and both ends learn the shape from it. Learning the template and sending the exchange are the same act.

**This path MUST NOT be fail-closed.** An implementation that refuses a request for want of a template creates a trap with no exit: the first request fails, so no uncompressed request is ever sent, so no template is ever learned, so every subsequent request fails identically and permanently. One bootstrap costs the size of one message; refusing to pay it costs the endpoint forever. The answer to “I do not understand this yet” MUST be reduced compression, never a refusal.

## 7a. Template lifecycle

A template is **deterministic by the key**: the same key yields the same template at both ends, derived rather than negotiated. It is **reciprocal** — each end holds the same template for an exchange — and **independent of host**, so it does not move or expire when a role does (§8).

**Capture.** A template is established by running the URL. The proxy holds port 80, so a first exchange through it produces the template as a side effect; there is no registration step and no schema to declare.

**Retention.** Once learned, a template persists. The reference implementation never ages one out; the tables are emptied only by hand (`clear_tables.sh`). Volume is not an expiry condition.

PENDING SOURCE — the disuse interval before a template is discarded. Stated by the author as “a while” and not located as a constant in the source available for this pass. Named here rather than given a plausible figure.

**The reference cache is keyed by `(destination, templateId)`**, and is filled on receipt: the message arrives, the template is loaded, the difference is computed. Keying on the destination is what makes the cache per-peer and per-direction; the template itself is neither.

**A `REQ_REPEAT` always re-executes against the backend.** “Repeat” describes the request, not the work: the daemon runs the origin request again and compares the new response against the cached one to decide SAME or DIFF. An implementation that answers a repeat from cache without re-executing is not conforming, and is the reading this document is most often given (§11.8).

## 8. The reference, and its three states

Each side keeps a reference per peer per exchange kind: the last set of values successfully exchanged. What goes on the wire is decided by comparing new values against that reference. **The reference has three states, not two.**

``` code
reference is UNSET     never sent to this peer          ->  FULL
reference is EMPTY     sent before, no values, still none ->  SAME
reference has VALUES   some of them changed              ->  DIFF
```

An implementation MUST distinguish UNSET from EMPTY. Collapsing them means every exchange carrying no varying data at all — the echo, the host list, the health probe — can never reach SAME, because “nothing to send” reads as “nothing sent yet.” Those are the highest-frequency exchanges on the mesh, and losing SAME on exactly them loses most of the benefit.

Values MUST be compared exactly, so that the receiver rebuilds the value the sender now holds: a change in the low bits of a floating-point number is a change.

Verified in `core/codec.py`: floats compare equal only bit for bit (NaN equals the same NaN; 0.0 is not -0.0), recursively through dicts (in key order) and lists. `1` and `1.0`, and `True` and `1`, are unequal; values of differing type are unequal without further comparison. LZ4 is applied only where the compressed output is smaller than the raw payload (`_lz4_smart`); LZ4 frame overhead is about 15 bytes, which on a fifty-byte reading is an expense rather than a saving.

**The list-length threshold described in the build manual is not in force.** `DEFAULT_MAX_ITEMS = 64` and its accessor `_max_items()` exist in `core/json_handler.py` (overridable by `FROGNET_JSON_MAX_ITEMS`), but nothing in the tree calls the accessor. A collection of any length is therefore handled the same way. An implementation MUST NOT document a length-dependent behaviour that the code does not perform; the constant is dead until something calls it.

## 9. How SAME is proven

**This is the mechanism most often misread as a cache. It is not one.**

On a repeated request the far end MUST re-execute it against the real origin, obtain a fresh answer, and compare that answer to the one it sent last time. It MUST reply SAME only on genuine identity, and MUST NOT answer SAME from a stored copy. “Repeat” describes the request, never the work: it means the question is the same, not that the answer may be reused unexamined. A stale answer is therefore not possible, which is precisely what a cache cannot promise.

Comparison is over the **canonical value**, not the raw bytes. Two replies differing only in incidental formatting compare equal and cross as SAME.

The identifier returned with a SAME is an **HMAC-SHA-256 token truncated to 16 bytes** (\[RFC2104\], \[RFC6234\]), keyed on a shared secret and computed over the request hash concatenated with the SHA-256 of the response's status and canonical values (`core/semcache_id.py`):

``` code
same_id = HMAC-SHA256( secret, req_hash || raw_hash )[:16]

  secret     /etc/frognet/sem_cache_secret, reloaded on mtime change
             (rotatable without restarting the daemon or proxy)
  req_hash   16 bytes on the wire, 32 internally
  raw_hash   SHA-256 of the response's status and canonical values
             (of the body itself on the raw path)
             32 bytes, internal to the daemon, never on the wire
```

The token is verifiable (`verify_same_id`, constant-time compare) and is cryptographically bound to both the request and the response content. What that buys is that the two ends never disagree about which “last time” is meant, and that an identifier cannot outlive the content it names: change the data and the identifier changes with it.

An implementation MUST NOT operate without a secret in place. Where the file is absent the reference implementation falls back to a key derived from a compiled-in default and `/etc/machine-id`, logging CRITICAL; that is a development convenience and MUST NOT be relied on, since with a known key the token is forgeable.

Two rules bound the mechanism. An error response MUST NOT be recorded as a reference: a failed response is not a legitimate thing to later call unchanged. And the reference MUST be advanced only after the far side has accepted the exchange — if the answering node failed, neither side moves and both still refer to the same prior state.

## 10. Twenty-one bytes

A repeat of an unchanged request for an unchanged resource is twenty-one bytes out and twenty-one bytes back: four of magic, one of opcode, sixteen of identifier, each way, in place of a full HTTP request and a full JSON reply.

**Three numbers are quoted for this exchange in different material, and all three are correct at different layers. State which one you mean.** The four-byte prefix exists because TCP \[RFC9293\] is a byte stream with no message boundaries: a framed protocol carried over it MUST supply its own, and this one does so with a big-endian length.

| Layer                   | Unchanged exchange    | What it counts                                                                                                                                       |
|-------------------------|-----------------------|------------------------------------------------------------------------------------------------------------------------------------------------------|
| **Semantic payload**    | ~2 bytes              | What the codec represents — the verdict “nothing changed”. Not what travels.                                                                         |
| **FNWP-1 frame**        | **21 bytes** each way | `[MAGIC:4][op:1][identifier:16]`. The figure quoted throughout this site.                                                                            |
| **On the wire**         | 25 bytes out, 29 back | The frame plus the transport’s four-byte big-endian length prefix (§11.2); the reply also carries the receiver’s four-byte sequence number (§11.11). |
| **Complete round trip** | 54 bytes              | 25 out, 29 back.                                                                                                                                     |

An implementation or a document quoting a byte count for an unchanged exchange **MUST** name the layer it is counting. The unqualified figure on this site is the FNWP-1 frame: twenty-one bytes. `RESP_SAME` is the smallest frame that answers a request, and a conforming implementation MUST NOT emit a shorter SAME. (`SEQ_RESET` and `RTT_LOOP` are five bytes, but answer nothing.)

It is worth being exact about why, because the obvious reading is wrong. **Nothing is being squeezed.** There is no coding scheme doing the work. The body is not sent because both ends already hold it and can prove cheaply that they hold the same one. Twenty-one bytes is not a small payload; it is the absence of one.

Compression ratio is therefore the wrong measure of this mechanism, and no ratio is claimed for the codec itself (§14). The right observation is that most traffic on most networks is repetition, and repetition can be named instead of transmitted.

## 11. Sequencing, fan-in and fan-out

A node does not open a socket per request. Concurrent exchanges toward one peer fan in to that peer's single established socket; the writer sends frames one at a time and MUST NOT wait for a reply before sending the next.

**A request frame carries no sequence number**, and the obvious mental model is wrong on this point. The sender assigns a number locally and files the waiting caller under it; the receiver assigns its own number from its own arrival counter and stamps that onto the reply. The two agree because they are counting the same events in the same order on the same socket, not because the number crossed the wire.

Because two independent counters can drift, `SEQ_RESET` exists to put them back in step. The reference implementation defines it but does not send it: it puts the counters back in step by reconnecting, which restarts both at zero. The need for one or the other is the direct consequence of the number not riding the request.

``` code
writer -> seq 41  REQ_FULL     (dashboard poll)
writer -> seq 42  REQ_REPEAT   (sensor read)
writer -> seq 43  REQ_FULL     (file list)
pending = { 41:dashboard, 42:sensor, 43:filelist }

reader <- seq 42  RESP_SAME    -> wake the sensor caller  (first back)
reader <- seq 43  RESP_DIFF    -> file-list caller
reader <- seq 41  RESP_DIFF    -> dashboard caller
```

Replies arrive interleaved and MAY complete out of order; a small SAME may return before a larger body. An implementation MUST NOT hold a completed reply behind an outstanding one.

## 12. Collapsing duplicate work

Two distinct collapses operate, at different places, protecting different scarce resources. They are not the same mechanism and neither substitutes for the other.

| Side      | Keyed on          | Effect                                                                                                                                                                                                                             | Protects   |
|-----------|-------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|------------|
| Sending   | request and peer  | Several callers wanting the identical thing from the identical peer become one frame. The first caller owns the exchange; the others wait and receive the same bytes.                                                              | The link   |
| Receiving | request and asker | One node asking the same question several times in the same instant costs the answering node one origin execution, not several. Different askers are not collapsed: each reply is a difference against that asker’s own reference. | The origin |

Two rules constrain the mechanism. Health probes MUST NOT be handed another caller's result: a probe given somebody else's answer has measured nothing. And a waiter that has waited too long MUST proceed to do the work itself — collapsing duplicate effort may cost duplicate effort, but it MUST NOT cost a request.

Probes MUST NOT be exempted from the collapse on the grounds of being cheap. This was learned by removing it: with probes exempt, an unreachable downstream node gave every probe its own timeout slot, the pending queue grew past four hundred, and one unreachable box became a fleet-wide failure. Exempting the highest-frequency traffic from the mechanism that bounds concurrency is how a local outage becomes a general one.

## 13. Change propagation

Topology change notification is carried as ordinary FNWP-1 traffic and MUST be bounded three ways. All three are required.

``` code
1  every wave carries an identity
   a node that has already seen it drops it, silently.
   this is what makes the epidemic terminate.

2  a node notifies only its DIRECT neighbours
   LAN next-hops and tunnel peers, not everything it knows.
   fan-out per node: O(neighbours), not O(fleet).
   re-propagation from each receiver still reaches everyone.

3  arrivals are debounced
   a burst collapses into one reconciliation, not one per message.
```

A node MUST skip **all** of its own addresses when notifying, not merely its primary one. A FrogNet host appears under several — tunnel endpoints, secondary aliases, transit addresses — and skipping only the obvious one leaves the rest eligible, so the node notifies itself repeatedly, each self-notification starting a process whose only work is to notice it has already seen the message.

**A wave carries a change notification, never a value.** Tuple values do not gossip; there is only one copy of them (§2a). Because notifications are ordinary requests they pass through the codec like everything else, and the only field that differs between successive waves is the wave identity — a value, not part of the template identity — so after the first notification to a peer, each subsequent one carries a single changed field.

## 14. What each side retains

Every node carries a local database. **It serves two unrelated roles, and conflating them is the error that produces every “FrogNet replicates its memory” reading.**

| Role                         | Present on                                                       | Holds                                                                                                              |
|------------------------------|------------------------------------------------------------------|--------------------------------------------------------------------------------------------------------------------|
| Semantic store               | **Every node**, always                                           | Templates and per-peer references. Nothing to do with FrogNet Memory.                                              |
| FrogNet Memory backing store | **The elected database host only**, while it holds the role (§8) | The tuple space (§9). A node acquires this role at election and loses it at the next one, including after a merge. |

The same database instance serves both. An implementation MUST NOT infer from the presence of a database on every node that the tuple space is present on every node: it is not, and **being elected is what adds it**.

### 14.1 The semantic store has three layers, at three different scopes

The scope of each is normative, because it decides what has to agree with what.

[TABLE]

**Response-body retention is least-recently-*read*, not least-recently-written.** \[SEMCACHE_PROXY_LRU_V1\] The clock advances only on a real read hit, so a row being served stays regardless of age and a row nobody asks for expires after a disuse window — twenty-four hours in the reference implementation, overridable by `FROGNET_CACHE_EVICT_HOURS`.

Keying eviction on write time would discard exactly the entries worth keeping: on a slow link the hot rows are the ones never worth re-fetching over the wire. **An implementation MUST provide eviction on this layer** — without it the table grows without bound, which is what happened while the daemon side had eviction and the proxy side did not.

Templates and references are not evicted on this schedule. A template does not age out (§11.7a); a reference is corrected by `REQ_MISS` rather than by expiry.

**References are the only layer that must agree across the wire, and they must agree pairwise.** Two nodes exchanging traffic hold one reference each for that pairing, and the two rows must describe the same last exchange or a DIFF applied at the far end reconstructs the wrong value.

The agreement is maintained by the rule already stated in §11.9: **a reference advances only after the far side has accepted the exchange.** Neither side moves unilaterally, so a pair cannot drift by one. Where they have drifted anyway — a restart, a lost frame, a peer that never held the reference — the holder answers `REQ_MISS` (§11.3) and the exchange falls back to FULL, which re-establishes both sides from one message. An implementation MUST provide that path: without it a single divergence is permanent.

Templates need no such agreement. Being machine-independent, a template the far end lacks is learned on the next FULL, and two nodes holding different template sets are not in an error state.

\[NEG_CACHE_TTL_V1\] A negative lookup — “no template for this opcode yet” — MUST be cached only briefly, and the window MUST be shorter than one user-visible exchange. Otherwise a node that asked before the template was written keeps answering “no template” from its own negative cache after the template exists.

The store is local: it is reached on the loopback address, and the semantic layer creates its own tables rather than relying on an installer, because a plain proxy node never runs the database-host install and would otherwise have nowhere to learn a template.

That the meaning lives in this local store and not on the wire is why a tap on the link yields little: the wire carries references and differences, not messages (§12).

## 15. Non-goals

**Not a cache** (§11.9). **Not a compression product**: the saving is structural rather than statistical, and no ratio is claimed for the codec (§11.10, §14). **Not encryption**: the SAME identifier is an authentication tag, not a cipher. It proves that a party holding the secret asserted this response for this request; it conceals nothing. Confidentiality on the link is WireGuard's (§7, §12). **Not content-negotiated**: body handling is selected by a declared JSON or XML `Content-Type`, else by inspection of the body, into one of five modes (json, xml, html, text, raw); a body classified raw learns no template and every send of it is FULL (§17.7).

## 16. References

Specification [§2](spec-02-invariants.md), [§9](spec-09-memory.md), [§10](spec-10-unrest.md), [§12 (Security)](README.md#s12), [§14 (Performance)](README.md#s14), [§17](spec-17-limits.md). Source: `core/semcache_wire.py`, `core/codec.py`. Build manual: Croakus [§26](https://fawcettinnovations.com/magnum-croakus.html#26-the-accelerant-semantic-compression), [§27](https://fawcettinnovations.com/magnum-croakus.html#27-fnwp-1-the-wire), [§28](https://fawcettinnovations.com/magnum-croakus.html#28-the-object-oriented-handler), Appendix F (Inside the Compression Engine), Appendix G (Inside a Handler). Measured results: [the simulator](https://fawcettinnovations.com/simulator.html) and §14.

[← §10 UnREST](spec-10-unrest.md) [Index](README.md) [§11a The Proxy and the Daemon →](spec-11a-proxy-daemon.md)
