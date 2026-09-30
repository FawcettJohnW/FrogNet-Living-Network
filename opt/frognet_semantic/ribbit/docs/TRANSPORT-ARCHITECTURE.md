# Ribbit-LISP transport architecture

Decided 2026-09-24 (John Fawcett, with Claude). Governs every slice after v0.40.

## Three FrogNet facilities, kept separate

| Facility | Job | Not for |
|---|---|---|
| FrogNet Memory (FNW1 now; FNWP with BLDC in the full stack) | Shared state and participant truth. Structured information travels as semantic deltas against established context. | Bulk payload. Memory traffic does not traverse the high-speed connection. |
| High-speed connection | A separate permanent TCP data path (from the Communicator work), multiplexing logical channels, for high-rate or perishable traffic and large objects: video, real-time position, tensors. | State that can be published. |
| UDP LISP | The standards boundary: full RFC 9301 messages to and from conventional LISP peers. | Traffic between Ribbit participants. |

## Routing rule

1. Structured information goes to BLDC on the Memory/FNWP path. If it has semantic structure BLDC can learn, send the
   semantic delta, not the full representation again.
2. Small, unreducible data goes on the ordinary control path. With no redundancy to remove and nothing large, data-plane
   machinery buys nothing.
3. Large, unreducible data goes on the high-speed connection. Payload big enough to matter uses the permanent data path.

## LISP exchanges classified

| LISP exchange | Between Ribbit participants | Toward conventional LISP |
|---|---|---|
| Map-Register | Structured registration truth (EIDs, RLOCs, priorities, weights, flags, IDs): the ETR publishes it; the Map-Server governs it and publishes its decision. On the wire: BLDC deltas, not repeated full packets. | Full Map-Register over UDP (v0.36–v0.38, byte-identical to the control). |
| Map-Notify / Map-Notify-Ack | Acceptance is the Map-Server's governance truth; the ETR observes it. The ack disappears. | UDP (v0.39–v0.40). |
| Periodic re-register, TTL, expiry | Liveness as read-time freshness of the ETR's truth. | Periodic UDP registers (v0.38). |
| Map-Request / Map-Reply, negative reply, proxy-reply | Reads of governed truth by the ITR's held map-cache. No request exists. | UDP (`wire.request*`, `wire.etr_request*`). |
| Map-Referral / DDT | Delegation truth; recursive resolution becomes reads. | UDP (not yet claimed). |
| SMR, pub/sub notifications | Held reads. | UDP (not claimed). |
| Data plane (UDP 4341) | Payload: the high-speed connection between Ribbit sites. | UDP. |
| RLOC probing, echo-nonce | Measures a path, so it travels that path; the result is published as state. | UDP. |
| Info-Request / Info-Reply (NAT) | Depends on what the network does to the packet, so it is transport; the discovered translated RLOC is state. | UDP. |

## What replaces the Map-Register HMAC between Ribbit participants

The HMAC answers "may this sender claim these EIDs?". Between Ribbit participants the question becomes:

- Who may write the ETR's truth? That is a property of FrogNet Memory itself (writer authentication), not of this
  conversion.
- Does the Map-Server's governance accept it? That is Map-Server-owned governance truth, which exists since v0.10–v0.12.

This is recorded so that the absence of HMAC on the native path is not read as a gap in this conversion.

## Measurement obligation

The bandwidth claim for the native path must be measured as actual bytes on the wire and compared with repeated full
UDP Map-Registers, covering:

- initial establishment;
- steady-state refresh with no change;
- one change (an RLOC moves), for a range of database sizes.

The measurement layer available in this environment is the FNW1 client session: `bytes_out` and `bytes_in`, and
REQ_RAW / REPEAT / SAME counts, exposed by `transport.stats` from v0.41. BLDC runs in the FrogNet proxy/daemon, which
is not part of this tree. FNW1 numbers are therefore reported as FNW1 numbers, and the BLDC measurement stays open
until it runs on a FrogNet node.
