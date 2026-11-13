# Performance lessons

These lessons come from the November 2026 work that made the Psychedelic PyTorch backend faster than gloo across the
FrogNet mesh. That work is on branch `revive-september-20261112`. Each lesson is shown applied to Ribbit-LISP, on
branch `ribbit-lisp-perf-20261112`.

The document stands on its own. It covers three things:

1. what measured better;
2. what measured worse and was taken back out;
3. how to measure, so the next change is judged the same way.

Every number comes from a run. A number that is an estimate says so.

## The result these lessons produced

The measure was whole-campaign time on the five-node mesh. Both arms ran in the same session on the same network:

| arm | run | measured steps | min ms/step | held-out loss |
|---|---|---|---|---|
| gloo (control) | ddp4320 | 1,480 s | 18,713 | 0.4947 |
| Psychedelic, family link | ddp4321 | 1,059 s | 18,802 | 0.4947 |

Psychedelic finished in 1,059 s against gloo's 1,480 s. That is 1.40× faster on the whole run. The best single
steps are equal. The gain came from the steps that were not the best ones: waits that overlapped instead of
queueing.

---

## 1. What measured better

### 1.1 The presence of the data is the flag

**The ruling:** "There is no ready flag. The presence of the data is the flag."

- **How it works.** A reader holds one standing read on a peer's family, for example `<peer>.ars.\d+.<me>`. The
  owner's write fires that read, and the bytes go out on the link.
- **What it replaced.** A request and reply per cell, and the retry loops that waited for a ready flag. Each cell
  cost a round trip before any byte moved.
- **Measured.**
  - The no-hook arm: warmup in 137–163 s, against 271 s on the r10 link shape.
  - The DDP comm hook was still on per-peer request and reply when ddp4322 ran. Its warmup took 10 m 46 s, against
    3 m 27 s for the no-hook arm in the same campaign.
  - On loopback (4 ranks, hidden 1024), moving the hook to the family link took it from 459–514 to 168–203 min
    ms/step. The parameters were bit-identical.

### 1.2 Nobody waits a round trip they don't need

- **The problem.** A synchronous all_reduce made the training thread wait for every round. Rounds now run on a pool,
  and the Work comes back from a future.
- **The rule.** A caller blocks only where it needs the answer, and then only on its own thread. Anything that can be
  in flight is in flight.
- **The bug this exposed.** With two rounds in flight, a staging buffer shared between rounds (chosen by `seq % K`)
  gave a wrong sum. Each round now checks out its own buffer and returns it.
- **The general lesson.** Overlap exposes every piece of state that was shared only because nothing ran at the same
  time.

### 1.3 Exit ends waits

- **The incident.** ddp5744 hung at exit. A read-ahead joined a retry loop that could wait 3 × 300 s.
- **The fix.**
  - `_abort_io` runs on shutdown and at interpreter exit, and shuts down every socket.
  - The retry count went to 1.
  - Pool threads are daemons.
- **The rule.** Every wait has to be ended by the thing that ends the program.

### 1.4 Every participant states its build, and mixed builds refuse

- **How it works.** Each rank puts its build string in its rendezvous cell. The group line lists all of them, and a
  group with more than one build refuses to form.
- **Why.** One node had been left on an older build, and the group ran slowly and wrongly until this check named it.
- **The rule.** The check costs one cell. The alternative is a campaign spent on numbers that mean nothing.

### 1.5 Diagnostics that answer the next question without another run

Per round, per cell, per link and per peer:

- **Per round:** `ROUND` / `ROUND_DONE`, with each peer's wait, whether the cell landed before it was asked for, and
  whether it landed straight into place.
- **Per cell:** `SENT` / `LANDED`, with TCP_INFO.
- **Per link, once:** `LINK`, with `ip route`.
- **Per peer:** a STALL line when a wait passes that peer's own EWMA of wire time. The EWMA uses only the local
  clock, so there is no skew.
- **At the top of every log:** a build and configuration line.

These found the Seattle7 ↔ Seattle3B path. Seattle5 relays it on wlan1, and the path shows 10–66 s
retransmit-timeout stalls. That is a property of the network, and the logs showed it without a second run. It was
left to FrogNet's routing. Topology-aware relaying was rejected: "That is not adaptable … the network may split or
merge."

### 1.6 Retire what has been consumed

A cell is published with `readers = k` and dropped after its k-th send.

A skipping comm hook doesn't know its reader count, so its cells are retired by age instead. The age is bounded by
the hook's own drift limit.

### 1.7 Caches that survive the machine

A five-minute start-up after a network reboot was the Work shim recompiling into a `/tmp` that the reboot had
cleared. The build cache moved to `/var/cache/frognet`, and the shim now logs `BUILT` / `loaded in N s`.

**The rule.** A cost that happens only after a reboot is invisible in every run but the first.

### 1.8 Compress only when it shrinks

LZ4 is applied only at ≤ 0.95 of the input size, with a reprobe every 64 cells.

Gradients measured 1.000: they don't compress. LZ4 stayed on for the cells it helps, and was skipped for
gradients.

---

## 2. What measured worse, and was taken back out

| change | measured | outcome |
|---|---|---|
| r9/r10: 3 channel sets per link, `TCP_NOTSENT_LOWAT`, records sized to 20 ms | warmup 271 s against r7's 137 s on the same mesh | defaults reverted to the r8 shape in r11: 1 set, no unsent cap, records ≥ 256 KB |
| Native C++ link (`frognet_link.cpp`) | one unexplained segfault in 11 runs | kept, opt-in only (`FROGNET_PLANE_NATIVE=1`) |
| Topology-aware relaying | not run | rejected: routing belongs to FrogNet |

**The lesson of the first row.** "The buffer is the latency" is right for the semantic socket. A short queue at the
wire keeps later messages from waiting behind earlier ones.

On a bulk data link across a Wi-Fi relay, the same short queue starved the link. It is a rule about a socket's job,
not about every socket.

So measure the socket you are changing, on the path it runs on. TCP_INFO says what held it back: `busy`,
`rwnd_limited` and `sndbuf_limited` times.

---

## 3. How to measure

1. **Measure the whole run.** "I DNGAF about an individual single run being faster or slower, but I want the overall
   to be faster." A best step is not the result. The total is.
2. **Run the control in the same session.** gloo's own times halved between days on the same mesh. A comparison
   across sessions measures the network. A comparison within one measures the code.
3. **Look up the control's numbers; don't estimate them.** The runs exist. "Why are you estimating gloo? You have
   plenty of runs."
4. **Check the measuring tool before the code.** The register benchmark below first went through `shaped_relay.py`.
   That tool sleeps after each read before reading again, so under load a chunk waits for the previous chunk's delay
   and then its own. It reported two round trips per register where there is one. `delay_line.py` stamps each chunk
   and forwards it when its time comes, so chunks in flight overlap. Use it for latency; use `shaped_relay.py` for
   rate.
5. **Find the cause in the layer it lives in.** The Wi-Fi relay's retransmit timeouts were not in the backend, and
   the kernel's counters said so.
6. **Name which number is which.**
   - The unit of a closed-loop client's rate is its round trip.
   - An open-loop burst from one sender measures whether the code waits a round trip per item.
   - A 2-core box that runs the load generator, the front, a delay line and the RAM host measures the box.

---

## 4. Applied to Ribbit-LISP (v0.66-registers-in-flight)

| lesson | Ribbit-LISP before | done | measured |
|---|---|---|---|
| 1.2 no round trip waited for needlessly | a sender's strand waited for each Map-Register's answer before sending the next | **[REGISTERS_IN_FLIGHT_V1]** the strand only sends, in order. The session keeps that order and the memory runs the operations in it. Each Map-Notify goes back as its answer lands. Own cells from the answer are applied before the notify ([OWN_WRITES_HELD_V1]), so put() is complete by the same rule. | one sender, 300 registers at once, 24 ms RAM round trip: **36.6 → 1,756 registers/s** (8.2 s → 0.17 s) |
| 1.2, closed loop | one outstanding register per sender is one round trip | unchanged; that is the round trip | 39/s at 25.7 ms, as in the report |
| 1.3 exit ends waits | — | `LispBoundary::stop()` waits out registers in flight. The bound comes from the session, which fails every call it carries when it ends. | — |
| 1.4 mixed builds refuse | the front could talk to a RAM host of any version | **[MIXED_BUILDS_REFUSED_V1]** the boundary, `ribbit-lisp --ram` and `lisp-service` refuse a host whose LISP region is another version, naming both. `RIBBIT_LISP_ALLOW_MIXED=1` lets it through, saying so. | — |
| 1.5 diagnostics | no account of what held a session's sockets | **[SOCKET_SAYS_WHAT_HELD_IT_V1]** (`cpp/include/tcpdiag.hpp`) every frogram session prints TCP_INFO for its three sockets when it ends. The RAM host adds the same for request and return sockets to its `session down` line. The boundary counts `in_flight_peak`. | loopback, 26k registers: client request socket 190 B/register, host return socket 550 B/register. At those sizes a 64 KB send buffer on a 25 ms path carries about 13,000 registers/s, so the buffer is not what limits registers. The droplet run's `sndbuf_limited_ms` will confirm or refute that. |
| 1.8 compress only when it shrinks | small frames (65–256 B) are tried and mostly lose | not changed | the cost is a few µs at about 1,700 registers/s, under 1% of a core. Not worth a change to the shared codec. |
| 2, the buffer is the latency | 32 KB `SO_SNDBUF` on the session | not changed | see the TCP_INFO row: measure it on the droplet path before touching it |

The bench, on one 2-core machine (`tools/bench_register_rtt.py`, RAM link through `tools/delay_line.py` at 12 ms
each way):

| | v0.65 | v0.66 |
|---|---|---|
| 1 closed-loop sender | 39.0 /s, p50 25.7 ms | 39.2 /s, p50 25.6 ms |
| 4 | 152.5 /s, p50 26.3 ms | 154.0 /s, p50 26.0 ms |
| 16 | 595 /s, p50 26.8 ms | 593 /s, p50 27.0 ms |
| 64 | 1,638 /s, p50 37.5 ms | 1,517–1,943 /s, p50 30–41 ms (box-bound; see 3.6) |
| burst of 300 from one sender | **36.6 /s** | **1,756 /s** |

**What this does not change.** A single closed-loop client is still bounded by the round trip to the RAM host:
39/s at 25 ms, as in the report.

To change that, the front would have to answer a Map-Register before the region has run it. That means holding the
site keys at the front and deciding there. Today the decision runs in the memory, as one call
([VENDOR_API_V1], [RESIDENT_REGION_V1]). That is a decision about where authority lives, not a performance fix, and
it is not made here.

A real ETR doesn't run closed-loop. It sends its mappings and refreshes them, and that open-loop case is the one this
change speeds up.

Qualification (`tools/qualify.sh`, every stage) passes on v0.66.
