# FrogNet RAM — sizing and administration guidance

18 September 2026. From `tools/flood` (one writer, one reader) and `tools/mesh`
(N clients, every one cross-posting to every other, every one in a blocking
read), run against both server implementations of the same contract:

- **PHP stack** — `ram_listener.py` → Apache/mod_php `ram.php` → MariaDB.
  A held read re-checks the table every 50 ms.
- **C++ server** — `ram_server`: FNW1 and the memory in one process, cells in
  RAM. A held read parks on a condition variable and is woken by the write.

**The absolute numbers are for reference only.** They were taken on loopback,
on ONE CPU core that the load generator, the server and (for the PHP stack)
Apache and MariaDB all shared. **The ratios are the guidance.** Re-run both
tools on the target hardware to get its own reference numbers:

    cd tools && make
    ./flood HOST PORT --seconds 5 [--ack] [--rate N]
    ./mesh  HOST PORT --clients 10 --seconds 5 [--ack] [--rate PER_PAIR] [--server-pid PID]

## Running it on physical machines

    client$  cd tools && ./run_bench.sh HOST PORT [--label TEXT] [--server-pid PID]
    server$  cd tools && ./server_monitor.sh PORT            # at the same time; Ctrl-C when the client finishes
    client$  ./bench_to_sim.py CLIENT_DIR [SERVER_DIR]

`run_bench.sh` records the machine, characterises the path (ping and timed TCP
connects), runs every test below with one JSON result each, and samples the
client at 1 Hz (CPU, load, TCP segments out and retransmitted) so a bad number
can be told apart from a busy client or a lossy path. `server_monitor.sh`
samples the server at 1 Hz: CPU and iowait, the port's established connections
and **the bytes waiting in their receive queues (that is the un-acknowledged
backlog, directly)**, and per process — `ram_server`, `ram_listener.py`, Apache
(and how many workers), MariaDB — CPU, memory and threads, plus database
queries per second. Everything is stamped in UTC epoch seconds so the two
directories line up run by run.

`bench_to_sim.py` turns that into `sim_profile.env` — the simulator's own
`FROGNET_SIM_LATENCY_MS / JITTER_MS / BANDWIDTH_BPS / OUTAGE_PROB / OUTAGE_MS`,
which `NetworkParams.from_env()` already reads — and `sim_profile.json`, the
server model the measurements support, each figure naming the run it came
from and anything not measured listed as not measured:

    set -a; . CLIENT_DIR/sim_profile.env; set +a      # the shaped wire is now the measured path

## The fast-plane reflector

For high-rate current-value traffic (a kart at 60 Hz, a video frame), the memory
is the wrong plane: queuing live state means a slow reader accumulates stale
work. `reflector/reflector_server` is the fast plane — a sender-facing process
that fans one publisher's stream out to every subscriber over kept connections,
send-or-drop per subscriber, storing nothing. A subscriber that cannot take a
frame now is shed for that frame; the next is newer. The slow-plane memory holds
the descriptor saying where the reflector is; the reflector carries the bytes.
The rendezvous launches a reflector beside each benchmark RAM and returns both
ports. Run `make -C reflector test_reflector` for its oracle.

## frogbench: one executable that asks "can this machine FrogNet?"

    ./frogbench                       bring up a RAM target on loopback, qualify + characterise THIS machine
    ./frogbench --peer HOST[:PORT]    also run against another frogbench (repeatable): an ephemeral test mesh
    ./frogbench --serve [--port N]    just be a RAM target other frogbenches point at
    ./frogbench --seconds N --json D  set the per-test length; keep machine-readable results in D

One self-contained binary. The RAM server, the clients, the workloads, the
qualification suite and the profiler are all in-process, linking the same
frogram + ram_server used everywhere else, so it exercises the real contract.
Nothing is installed. A bare run brings up its own loopback target; --peer makes
two (or more) frogbenches form a temporary FrogNet and measure the actual hosts
and the actual path between them.

It runs two halves. QUALIFICATION -- does the shared-memory contract hold here?
-- prints PASS/FAIL for distinct-state (every sequence seen once, in order, none
missing), latest-value (replacement, ordering, final value), the blocking read
(held, woken, expiring) and removal, then says "this machine CAN/CANNOT
FrogNet". CHARACTERISATION -- how well? -- runs the paced ladders. Its --json
directory is exactly what bench_envelope.py, bench_report.py, bench_to_sim.py and
lilypad_plan.py consume:

    ./frogbench --seconds 5 --json myhost
    ./bench_envelope.py myhost
    ./lilypad_plan.py workload.json --memory myhost/envelope.json

That is the whole loop in three commands, from one binary: measure -> envelope
-> plan. Two people can put frogbench on their laptops, point them at each other
over Wi-Fi, and get a qualification receipt and an envelope for their own
machines and their own link -- not a published benchmark to be taken on trust.

## From profile to prescription: the operating envelope

    client$  ./bench_envelope.py CLIENT_DIR [SERVER_DIR] [--policy policy.json] [--name "this host"]

The hardware characterises itself. `bench_envelope.py` turns one profiled host
into a **recommended operating envelope** by explicit rules, not by reading the
graphs: a policy states what is acceptable (p99 delivery, freshness, skip
fraction, CPU, receive-queue backlog, payload budget, headroom), each rule finds
the highest *tested* load that stays inside the policy with every lower load
also inside it, and the recommendation is the headroom fraction of that
boundary. Distinct state, latest-value (every generation seen, and freshness),
blocking readers, the payload size above which to use the Plane, and the
"queue grows before CPU does" pattern each have their own rule.

Every line names the runs and the policy values it came from. A boundary the
tests never reached is reported as "not reached (>= X)"; what was not measured
(today: maximum cell population) is listed as not measured. Change the policy
and re-run it against the same measurements to see what a stricter latency
target or less headroom costs -- no re-benchmarking needed.

## The contention test

`mesh --every --ack` is pure contention for the memory: every message is its
own cell, so nothing is ever replaced, and every reader checks every sequence
number from every writer — exactly once, in order, none missing. **One dropped
sequence number anywhere fails the run, and `run_bench.sh` exits 1.** Readers
remove what they consume, so deletes contend too. Reference (10 clients, 90
pairs, one shared core):

| | writes/s | dropped | delivery median / p99 | note |
|---|---|---|---|---|
| C++ server, acknowledged | 13,974 | **0** of 56,052 | 1.0 ms / 2.4 ms | 53% of a core |
| C++ server, not waiting | 37,425 | **0** of 153,837 | 370 ms / 2.4 s | a backlog, not a loss |
| PHP stack, acknowledged | 618 | **0** of 2,619 | 1.1 s / 2.2 s | readers take 75 cells per read |
| PHP stack, not waiting | 571 | **0** of 4,770 | 4.1 s / 7.6 s | |

Nothing is ever dropped; what load changes is how long a message waits. The
C++ server needed its memory indexed by write order to get there: with 400,000
cells, a linear scan per read and per remove starved the readers until reads
and removes cost what they return rather than what the memory holds.

## What held in every run, on both servers, at every load

For every (writer, reader) pair: **never out of order, never delivered twice,
and the reader always ends on the writer's last value.** A reader that is
slower than a writer SKIPS — a cell holds one value and writing replaces — and
that is the model, not a loss. Everything below is about how much is skipped
and what it costs, never about whether what arrives is right.

(One defect was found and fixed by `mesh`: the Python listener ran each request
on its own thread, so un-acknowledged writes from one session could be applied
out of order — 636 regressions in 7,290 writes. Requests on a session are now
executed in arrival order; only a held read is parked aside. **Rule 9.**)

## Reference measurements

| | PHP stack | C++ server | ratio |
|---|---|---|---|
| one acknowledged writer, flat out | 681 writes/s | 30,739 writes/s | 45× |
| its reader (one read per round trip) | 34 reads/s | 24,359 reads/s | ~700× |
| one pair, highest paced rate with nothing skipped | ~20/s | ≥ 5,000/s (not reached) | ≥ 250× |
| 10 clients / 90 pairs, acknowledged, flat out | 782 writes/s, 0 skipped | 21,037 writes/s, 0 skipped | 27× |
| 10 clients / 90 pairs, paced, nothing skipped up to | 10/s per pair (900/s) | 200/s per pair (18,000/s); 0.04% skipped at 400/s per pair (36,000/s) | 20–40× |
| aggregate ceiling, writers not waiting | ~800–1,000 writes/s | ~128,000 writes/s (93,000 with 32 KiB buffers) | ~100× |
| server CPU per write, paced | ~0.7 ms of a core (whole machine 66% busy at 900/s) | 17.8 µs | ~40× |
| server memory | listener 49 MiB + Apache workers + MariaDB | 9 MiB | |

C++ server, aggregate ceiling against client count (flat out, not waiting):
2 clients 47k/s · 5 clients 127k/s · 10 clients 128k/s · 20 clients 119k/s.

## The rules

**1. Skipped fraction = 1 − (reader rate ÷ writer rate), per pair.**
Measured three ways: 22.9k/33.3k → predicts 31.3%, measured 31.4%; 24.4k/30.7k
→ 20.6%, measured 20.8%; 34/681 → 95.0%, measured 95.0%. If nothing may be
skipped, the writer's rate to a pair must stay under that reader's rate. If
only the latest matters, skipping is free and this is just arithmetic.

**2. A reader's rate is one read per round trip — unless the server naps.**
On a server that re-checks every T, a reader that arrives just after the newest
value waits up to T, so the highest rate at which a pair loses nothing is about
**1/T**: 20/s at 50 ms (measured: clean at 10/s, 1 of 60 at 20/s, 49% at 40/s),
and clean through 100/s at 2 ms. A condition-woken server has no T: a single
pair was clean at 5,000/s with the limit not reached. Shortening T costs
**1/T queries per second per parked reader**, all the time, whether or not
anything is being written: 20/s each at 50 ms, 500/s each at 2 ms.

**3. The aggregate ceiling belongs to the server, not to the clients.** It was
flat from 5 to 20 clients. Per-client share = ceiling ÷ clients. Size for the
aggregate.

**4. Plan to run the server at or under about half its ceiling.** C++ server:
nothing skipped through 18,000 writes/s at 32% of the core; 0.04% skipped at
36,000/s and 64%. PHP stack: nothing skipped at 900/s with the machine 66%
busy; past its ceiling it cannot keep up at all. Above roughly 60% busy,
readers start losing turns to writers.

**5. Acknowledgement is the flow control.** Ten clients writing flat out and
waiting for each acknowledgement: 21,037 writes/s and NOTHING skipped — a
writer that waits cannot outrun the memory. The same ten not waiting: 132,189
writes/s, of which 98.5% were never seen by anyone, and readers starved to 86
reads/s each. Six times the writes, none of the benefit. **Do not wait for
acknowledgement only when the writer is paced under rule 1, or when only the
latest value matters** (a kart's position) — and then use the fast plane, which
sheds at the sender instead of queueing.

**6. The buffer is the latency.** A writer that does not wait is held back
only by TCP, and only when the socket buffers are full; everything in them is
a write not yet applied, and mostly already out of date.
Backlog in seconds ≈ clients × (send buffer + receive buffer) ÷ frame size ÷
server capacity. With autotuned buffers the PHP stack was handed 18,000 writes
it could apply at ~950/s: a 15-second backlog, and the final acknowledged write
timed out. With 32 KiB on the client's request socket and on the server's
listening socket (both now set in code) the same overload was simply slowed to
the server's pace and passed. On the C++ server the same change took the
after-the-writer-stopped drain from 1.15 s to 0 and raised each reader from 86
to 309 reads/s, at the cost of 30% of the flat-out aggregate. Smaller buffers
trade peak throughput for bounded staleness. That is the right trade for state.

**7. Threads and workers.** C++ server: one thread per session plus one per
read that is actually parked (about two per chat client: its session and its
parked read) — 31 threads for 10 mesh clients using two sessions each. PHP
stack: **one Apache worker per parked read, for as long as it is parked**, plus
the writers' concurrency — 46 workers busy for 10 clients flat out.
`MaxRequestWorkers` must exceed parked readers + concurrent writers with room
to spare, and those workers come out of whatever else that Apache serves.

**8. Idle cost.** A client with nothing to read re-asks an unchanged question
every 25 s: REQ_REPEAT out, RESP_SAME back, 21 bytes each way — about 1.7
bytes/s per idle client on the wire. On the PHP stack the same idle client also
costs 20 queries/s (rule 2). On the C++ server it costs a parked thread.

**9. Order within a session is part of the contract.** A server must apply a
session's requests in the order they arrive. Only a read that asks to be held
may be set aside.

## Choosing a server

The PHP stack is the one to reach for when the database is the point: it is
already there, it survives a restart, and at human rates — tens of writes per
second in aggregate, a few per pair — it skips nothing. The C++ server is the
one for machine rates: two orders of magnitude more throughput, no nap to tune,
9 MiB, and an empty memory after a restart. Same clients, same wire, same
contract; the choice is per application and can be changed without touching a
client.
