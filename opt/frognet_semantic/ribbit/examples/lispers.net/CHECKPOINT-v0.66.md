# CHECKPOINT v0.66: registers in flight (2026-11-12)

John said on 2026-11-12: "Now take all those lessons and apply them to ribbit-lisp. May as well write them all down
too."

The lessons are written down in `../../docs/PERFORMANCE-LESSONS.md`. Section 4 of that document holds this
checkpoint's table and numbers.

| Was | Now | Tag |
|---|---|---|
| A sender's strand called the region and waited for each Map-Register's answer before sending the next. One ETR sending 300 mappings at once paid 300 round trips. | The strand sends in order and does not wait. The session keeps the order, and the memory runs the operations in it. Each Map-Notify goes when its answer lands. Own cells from the answer are applied first, so put() is complete as before. | [REGISTERS_IN_FLIGHT_V1] |
| `LispBoundary::stop()` waited for strands only. | It also waits out every register in flight. The wait is bounded, because a session that ends fails every call it carries. | same |
| The front, `ribbit-lisp --ram` and `lisp-service` would talk to a RAM host of any version. | They refuse a LISP region of another version and name both versions. `RIBBIT_LISP_ALLOW_MIXED=1` allows it, and says so. | [MIXED_BUILDS_REFUSED_V1] |
| Nothing said what held a session's sockets back. | Every frogram session prints TCP_INFO for its three sockets when it ends: rtt, cwnd, retransmits, and the busy, rwnd-limited and sndbuf-limited times. The RAM host adds the same for its request and return sockets to `session down`. | [SOCKET_SAYS_WHAT_HELD_IT_V1] (`cpp/include/tcpdiag.hpp`) |
| — | `tools/bench_register_rtt.py` measures closed-loop senders and an open-loop burst. `tools/delay_line.py` is a delay line whose chunks in flight overlap. | |

## Measured

One 2-core container, with the RAM link at 12 ms each way:

| Load | v0.65 | v0.66 |
|---|---|---|
| Burst of 300 registers from one sender | 36.6 /s | 1,756 /s |
| 1 closed-loop sender | 39 /s | 39 /s (one round trip, unchanged) |
| 4 closed-loop senders | 152 /s | 154 /s |
| 16 closed-loop senders | 595 /s | 593 /s |
| 64 closed-loop senders | 1,638 /s | 1,517–1,943 /s (the box is the limit) |

## Not changed

These were measured, or are decisions rather than fixes:

- **The single closed-loop client.** It is bounded by the round trip. Answering earlier would move the
  authentication and site decision to the front, which is a different trust arrangement.
- **The 32 KB send buffer.** Replies cost 550 B per register and requests 190 B, which is well inside what the buffer
  carries at 25 ms. The DIAG-SOCK lines from the droplet will settle it.
- **LZ4 on small frames.** It costs under 1% of a core at these rates.

## Qualification

Qualification 20261112-155556 ran every stage on x86-64 locally, and all passed:

- build
- local OK, fnw1 OK
- independent 10/10, plus cross-process PASS
- oracle 4/4
- handler PASS
- stress: 0 partial
- lock-free gates
- performance
- client C++ and Python PASS
- boundary PASS
- read-your-write PASS
- fail-loudly PASS
- service PASS (15 checks)
- bytes

## v0.67 (same day): the version check accepts the RAM hosts whose region the front speaks

v0.66 refused any RAM host that was not v0.66, but nothing lisper-ram runs changed in v0.66: the change was all in
the front, plus one diagnostic line in ram_host.cpp. So it would have forced a pointless reinstall on the droplet.
v0.67 accepts the versions listed in `RIBBIT_LISP_REGION_COMPAT` (version.hpp): v0.65, v0.66 and v0.67.

Checked against a v0.65 lisper-ram built from b4dfaed:
- `version` answers client v0.67-region-compat and ram_host v0.65-held-by-the-session, with exit 0;
- the burst of 300 from one sender is notified in 0.066 s.
