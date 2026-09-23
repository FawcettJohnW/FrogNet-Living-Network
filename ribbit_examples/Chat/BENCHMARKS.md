# Benchmarking the FrogChat memory

FrogChat is small, but the memory underneath it is the real contract: write, read, blocking read and remove, reached over FNW1. These tools answer two questions about any machine or path:

1. **Qualification:** does the shared-memory contract hold here? Every value that must survive survives, in order, exactly once, and blocking reads wake when they should. This passes or fails.
2. **Characterization:** how well does it hold? How many writes per second, at what delivery latency, before things start to queue.

The two are kept separate. A slow machine can qualify; a fast one that drops a value doesn't.

## Build the tools

```
cd tools
make
```

This builds three programs next to the scripts:

| Tool | What it does |
|---|---|
| `frogbench` | everything in one executable: brings up its own memory on loopback, qualifies, characterizes |
| `flood` | one writer, one reader, as fast as possible |
| `mesh` | N clients all writing to each other at once, each reading its own buffer |

The Python scripts need only Python 3.

## Quickest: frogbench

```
tools/frogbench
```

It starts a memory inside itself, runs the qualification suite, then the characterization ladders, and prints a verdict. Nothing is installed and no server is needed. It takes a few minutes.

Useful options:

```
tools/frogbench --seconds 5 --json results/     # longer tests; machine-readable results
tools/frogbench --serve --port 8788             # be a memory other frogbenches can test against
tools/frogbench --peer OTHERHOST:8788           # also test across the network to that machine
tools/frogbench --demo WORD                     # share one memory over the Internet with anyone
                                                # else who runs the same WORD at the same time
```

`--demo` uses the rendezvous broker built into frogbench to hand both sides one shared memory, so two people anywhere can try it with no firewall changes. `--broker URL --pass WORD` points it at your own.

A Windows build of frogbench is available with `make frogbench.exe` (needs mingw-w64).

## Against a real server over a real path: run_bench.sh

To measure a deployed memory, for example `ram_server` on an Internet host, run the suite from a client machine:

```
tools/run_bench.sh SERVER_HOST 8788 --out bench_cpp --label "ram_server on droplet"
```

At the same time, on the server, record what the server was doing:

```
tools/server_monitor.sh 8788 --out server_cpp      # Ctrl-C when run_bench.sh finishes
```

`--quick` runs a shorter matrix (2-second tests); `--seconds N` changes the test length.

`run_bench.sh` records everything needed to interpret the numbers later:

| File | Contents |
|---|---|
| `env.json` | this machine: CPU, cores, memory, kernel, the target |
| `path.json` | ping round-trip time and 30 timed TCP connects to the port |
| `runs/NNN_*.json`, `.log` | one result per test |
| `client_metrics.csv` | once a second: client CPU, load, memory, TCP segments sent and retransmitted |
| `summary.csv` | one line per test |

The client metrics exist so that a bad number can be told apart from a busy client or a lossy path.

## Turn the results into guidance

**The operating envelope** for one server:

```
python3 tools/bench_envelope.py bench_cpp server_cpp
```

This writes `envelope.txt` and `envelope.json` into the results directory: the recommended limits for that host, each derived by an explicit rule from a named run, with headroom applied. It also works on a frogbench `--json` directory.

**A report comparing servers, machines or days**, as one self-contained HTML page:

```
python3 tools/bench_report.py --out report.html "droplet"=bench_cpp:server_cpp "home Pi"=bench_pi
```

The first one listed is the baseline; every bar says how many times the baseline it is.

## Reading the results

**Distinct state vs latest value.** These are the two ways an application can use the memory, and they're measured separately.

- **Distinct state:** every value has its own address and must survive. The contention test (`mesh --every`) gives every message its own cell. **One dropped sequence number anywhere is a failure.** `run_bench.sh` says so loudly and exits non-zero if it happens.
- **Latest value:** one cell is rewritten, and a reader always gets the newest value. A reader slower than the writer skips values **by design**; that's the point of the pattern, and "dropped" in these runs is reported, not failed. What must never happen: a value out of order, a value delivered twice, or the reader not ending on the writer's final value.

**Every run reports:** writes and reads per second; out-of-order, duplicate and missing counts; delivery latency (from the writer's clock to the reader's hands) at median, p99 and max; and PASS or FAIL.

**The envelope, line by line:**

| Line | Meaning |
|---|---|
| Distinct state | the sustained write rate at which every value is still kept within the latency policy |
| Latest value, every generation seen | the per-pair rate below which a reader sees every value |
| Latest value, freshness | the per-pair rate at which readers still get fresh values within the policy |
| Blocking readers | how many readers were held open at once while staying within policy |
| Payload threshold | the value size above which large, fast-changing data belongs on a separate fast path rather than in memory cells |

A boundary the tests never reached is reported as "not reached (≥ X)". Nothing is extrapolated. The limits are half of each measured boundary by default (`headroom_fraction = 0.5`); pass `--policy policy.json` to change the rules.

**What the numbers belong to.** Every figure describes the machine, path and server that produced it. A memory on loopback, a memory across a LAN, and a memory across the Internet give different numbers, and all of them can qualify. Compare like with like, and publish the machine and path with the result.

**What a good result looks like:** qualification PASS on every case; zero out-of-order, zero duplicates, zero dropped in distinct-state runs; and a latency curve that stays flat until the rate ladder reaches the machine's limit, then rises. The envelope tells you where that knee is.
