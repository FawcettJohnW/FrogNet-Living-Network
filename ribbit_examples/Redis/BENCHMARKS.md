# Benchmarking Redis-on-Ribbit

The benchmark compares Ribbit with stock Redis 7.2.11 on the same machine, with the same `redis-benchmark` line, then adds cores to Ribbit one at a time. It answers two questions:

1. With one core each, how does Ribbit compare with stock Redis?
2. What happens when Ribbit is given more cores?

## What you need

- `ribbit-redis`, built (see BUILDING.md)
- Redis 7.2.11 built from source, for `redis-benchmark`, `redis-cli` and the stock server (see BUILDING.md)
- `taskset` (part of util-linux, installed almost everywhere)
- Python 3 for the summary

## Choose your cores

The server and the benchmark client must not share a core, and every core given to the server should be a real physical core. Hyperthread siblings share one core's execution units, so adding a sibling doesn't add a core.

See how your logical CPUs map to physical cores:

```
lscpu -e
```

Logical CPUs with the same `CORE` number are siblings on one physical core.

Some tested layouts:

| Machine | SERVER_CORES | CLIENT_CORES |
|---|---|---|
| Raspberry Pi 5 (4 cores) | `"0 1 2"` | `3` |
| 2 cores / 4 threads (siblings 0–2 and 1–3) | `"0 1"` | `2,3` |
| 8 or more physical cores | `"0 1 2 3"` | `4-7` |

`SERVER_CORES` is the order in which cores are added: the first row uses only the first core, the second row the first two, and so on. Stock Redis always runs on the first one.

## Run it

```
REDIS=~/redis-7.2.11 SERVER_CORES="0 1 2" CLIENT_CORES=3 bench/scale.sh | tee scale.log
python3 bench/summarize.py scale.log
```

`scale.sh` finds `ribbit-redis` in the directory above `bench/`; set `RIBBIT=/path/to/ribbit-redis` if it's elsewhere. If you've installed the test-suite wrapper, the script uses `src/redis-server.stock` as the stock server automatically.

Other settings, all optional:

| Variable | Default | Meaning |
|---|---|---|
| `N` | 1000000 | requests per test |
| `C` | 200 | client connections |
| `P` | 16 | pipeline depth |
| `TESTS` | `set,get,lpush,sadd` | redis-benchmark `-t` list |

A full run on a Pi 5 takes a few minutes.

## What it does

For stock Redis on one core, then for Ribbit on 1, 2, 3 … cores, it runs `redis-benchmark` twice:

- **Keys spread over a million-key range** (`-r 1000000`). This is the normal case: many different keys, little contention on any one of them.
- **100 hot keys** (`-r 100`). Every connection hammers the same 100 keys. This is the contention case: many writers on the same cells at once.

After each Ribbit row it prints three lines from `INFO ribbit`, prefixed with `#`.

## Reading the results

`summarize.py` prints four tables: requests per second, and the same numbers divided by stock Redis's, for each key pattern.

**The one-core row is the like-for-like comparison.** Both servers get exactly one core. Results within about ±10% of stock are parity. On a Raspberry Pi 5 and on a desktop i7, Ribbit's one-core numbers were at parity with stock Redis.

**The rows after it are what Ribbit does with more hardware.** Stock Redis executes commands on a single thread, so a second core doesn't raise its row; that's why the table has only one stock row. Ribbit's memory has no lock, so each additional physical core adds throughput. On a Pi 5, two cores gave about 2.0× stock and three gave 2.2–2.6×.

**Hot keys test the memory under contention.** With 100 keys and many connections, many writers land on the same cells at the same moment. A lock-based design slows down here. The thing to look for is that Ribbit's hot-key rows still rise with cores, and that no errors or stalls appear.

**Machine characteristics show up.** On machines with a small last-level cache (for example a 4 MB L3 laptop), Ribbit's one-core numbers come in below stock, because Ribbit's per-key layout uses more allocations than Redis's does. That's a known, measured cost of the current layout, not a limit of the model. A second core still takes Ribbit past stock on most commands.

**The `#` lines after each Ribbit row:**

| Line | Meaning |
|---|---|
| `rss_kb` | resident memory of the server process |
| `live_objects_total` | objects currently held by the memory |
| `ebr_retired_pending` | memory retired but not yet reclaimed by epoch-based reclamation; it should stay small (hundreds, not millions) |

**p50 latency.** Each line also shows median latency. With 200 connections and a pipeline of 16, a request waits behind others in its pipeline, so milliseconds are normal here; compare the servers with each other rather than reading these as round-trip times.

## Getting trustworthy numbers

- **Run it more than once.** Expect a few percent of variation between runs; report the median of three if you're comparing closely.
- **Watch the client.** If the client cores are at 100% in `top`, the benchmark is measuring the client, not the server. Give the client more cores or reduce `C`.
- **Keep the machine quiet.** Other services on the box (databases, containers, a browser) take cycles from whichever core they land on.
- **Don't count hyperthreads as cores.** A row that adds a sibling thread instead of a physical core will look flat. That's the hardware, not the software.
- **Publish the machine with the numbers.** `scale.sh` prints the CPU model, core layout, benchmark line and date at the top of its output. Keep them with any result you share.
