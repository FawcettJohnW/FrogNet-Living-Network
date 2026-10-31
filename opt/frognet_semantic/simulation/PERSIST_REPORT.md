# How Long an IS-IS Inconsistency Lives, and What SPB Pays to Close It

John Day, 2026-10-30: *"Your estimate the loops exist only between updates is optimistic. There is no guarantee that
the inconsistent data used in the last update will be consistent in the next ... the approximate upper bound for IS-IS
is somewhere around 100,000 nodes. Across a network that large inconsistencies could easily persist for several
updates."* And on 802.1aq: *"The main concern is how many times the few who detect inconsistencies have to do it to get
consistent."*

Two experiments. E1 measures how long an inconsistency, and any forwarding loop it causes, lasts: in seconds and in
SPF runs. E2 counts the SPB agreement exchanges. Both run on generic graphs (ring, grid, random mesh), not the bed's
hub shape, with three events: one link goes silent, one node fails, the network partitions.

**Measured** means real FRR 8.4.4 (zebra + isisd), one Linux network namespace per router, real kernel FIBs, every
route change timestamped by the kernel, every SPF run timestamped by isisd. 72 runs, 16 to 96 routers.
**Modeled** means FRR's own timer rules transcribed from its source and calibrated against those 72 runs, carried to
1,000–100,000 routers. Every number below says which it is.

---

## The answer

**Day is right that loops outlive updates, and the measurement says why: the rate-limiting timers, not the size.**

| | Measured, 96 routers | Modeled, 1k–100k routers |
|---|---|---|
| One link goes silent | loops ≤ 0.21 s; none outlives an SPF run | same |
| One node fails, stock timers | ≤ 1.1 s | ≤ 1.1 s |
| One node fails, RFC 8405 tuning | ≤ 0.09 s | ≤ 0.25 s |
| Partition, stock timers, mesh | **30.3 s**; loops stand through **4 SPF runs** at the router that finally breaks them | 31.5 s at 1k |
| Partition, RFC 8405 tuning, mesh | 5.4 s; through 1 run at the fixing router (3 at other members) | 5.6 s at 1k |
| Reconverged | stock 60 s, tuned 8 s | stock 60 s, tuned 8 s at 30k; 11 s at 100k (SPF time itself) |

The lifetimes sit on plateaus, and each plateau is a timer in FRR's configuration:

- **30 s** is `lsp-gen-interval 30` (stock). A router whose links fail over 1–2 s (hold timers fire at different
  moments) floods one LSP at the first detection and is not allowed another for 30 s. For those 30 s every other
  router has its word that the remaining dead links are up, and routes through them. Traced on router 3 of the 64-mesh
  partition: 4 links down, LSPs at +27.8 s and +57.8 s, exactly 30.000 s apart; the loop through it lived 30.47 s.
- **5 s** is RFC 8405's `long-delay 5000` (tuned profile). Detections spread over a second put the later LSPs past
  `time-to-learn 500`, so the next SPF is held 5 s while the loop stands.
- **1 s** is `spf-interval 1`: a second SPF within the same second waits for the next one.
- **36 ms** is one WAN link delay (35 ms): the gap between one router installing and its neighbour installing. The
  loops Day's TTL argument was about; they are the floor, not the ceiling.

Size moves none of these. Flooding costs 0.8 ms per hop (measured), so going from 96 to 30,000 routers adds tens of
milliseconds to a loop's life, not seconds. What size does move is SPF time: FRR's own SPF measured 0.15 ms at 16
routers and 0.7 ms at 96; at 100k that extrapolates to 1.8 s, which is why modeled reconvergence goes from 8 s to 11 s.

**"Updates every 30 seconds"** is not how IS-IS works but it is how it behaves here: a router may not re-announce for
30 s after its last LSP, which has the same effect on a loop as a 30 s refresh would.

---

## E1, measured: real FRR, 16–96 routers

Columns: loops toward destinations still reachable after the event (*live*) and toward destinations the event cut
off (*gone*). A loop is a cycle in the per-destination next-hop graph at an instant; a next hop over a dead link is
not a loop (the packet is lost, not returned). *past ≥1 SPF* counts loops that stood through at least one SPF run at
the router whose FIB change finally broke them; *most* is the largest such count.

### Stock FRR timers (hold 30 s, lsp-gen-interval 30 s, spf-interval 1 s, no RFC 8405)

| topo | N | event | live loops | longest live | past ≥1 SPF | most | gone loops | longest gone | reconverged |
|---|---|---|---|---|---|---|---|---|---|
| mesh | 64 | partition | 85 | **30.0 s** | 30 | **4** | 801 | 30.5 s | 60.7 s |
| mesh | 96 | partition | 392 | **30.3 s** | 168 | **4** | 2188 | 30.3 s | 60.0 s |
| mesh | 32 | partition | 0 | | | | 130 | 30.0 s | 59.4 s |
| grid | 96 | partition | 0 | | | | 1250 | 1.1 s | 59.8 s |
| grid | 96 | nodefail | 75 | 1.1 s | 0 | 0 | 14 | 1.1 s | 58.8 s |
| mesh | 96 | nodefail | 25 | 1.0 s | 0 | 0 | 20 | 1.0 s | 58.2 s |
| ring | 96 | nodefail | 1898 | 37 ms | 0 | 0 | 81 | 1.0 s | 30.3 s |
| ring | 64 | nodefail | 847 | 1.1 s | 0 | 0 | 30 | 37 ms | 58.7 s |
| mesh | 96 | silent | 14 | 1.0 s | 2 | 1 | 0 | | 31.1 s |
| ring | 96 | silent | 2210 | 36 ms | 0 | 0 | 0 | | 28.6 s |
| grid | 96 | silent | 52 | 12 ms | 0 | 0 | 0 | | 30.8 s |

### Tuned (hold 3 s, lsp-gen-interval 1 s, spf-delay-ietf 50/200/5000/10000/500)

| topo | N | event | live loops | longest live | past ≥1 SPF | most | gone loops | longest gone | reconverged |
|---|---|---|---|---|---|---|---|---|---|
| mesh | 64 | partition | 82 | **5.2 s** | 3 | 1 | 305 | 5.4 s | 8.9 s |
| mesh | 96 | partition | 346 | **5.4 s** | 24 | 1 | 925 | 5.4 s | 8.1 s |
| grid | 96 | partition | 0 | | | | 932 | 5.3 s | 8.1 s |
| grid | 96 | nodefail | 59 | 44 ms | 0 | 0 | 7 | 32 ms | 8.6 s |
| mesh | 96 | nodefail | 29 | 89 ms | 0 | 0 | 8 | 55 ms | 8.2 s |
| ring | 96 | nodefail | 1912 | 41 ms | 0 | 0 | 41 | 37 ms | 8.1 s |
| ring | 96 | silent | 1874 | 210 ms | 0 | 0 | 0 | | 3.3 s |
| mesh | 96 | silent | 17 | 150 ms | 0 | 0 | 0 | | 2.9 s |
| grid | 96 | silent | 51 | 17 ms | 0 | 0 | 0 | | 3.3 s |

The full 72-run table is `tables.md` in the tarball. Why rings loop so much and grids so little: on a ring one cut
reroutes every path the long way round, and each pair of neighbours flips at slightly different moments (one WAN
delay); a grid has many equal-cost paths and ECMP spreads the change. Why only meshes partition into live loops: a
random mesh's cut set is 40–60 links spread over many routers, so routers with several failed links (the
`lsp-gen-interval` case) are common; a grid's cut is one straight line of single-link routers.

Two readings of "how many SPF runs did the loop survive" are in the result files: `spf_runs_survived` (runs at the
fixing router that did not fix it; the tables above) and `spf_runs_during_max` (runs by any member while it stood).
The second is larger: in the tuned 96-mesh partition, 254 of the 346 live loops saw one member run SPF and 25 saw two
while they stood. Both are honest; the first is the one that answers "did the next update fix it".

Load: one machine runs every router. The 1-minute load average at the end of each run is in the table; most are under
1.5 on 2 CPUs. The ring-96 node-failure run (tuned) hit 4.9 and its timings are the least clean.

---

## E1, modeled: 1k–100k routers

What the model is: FRR 8.4.4's own rules, transcribed from `isis_lsp.c` (`_lsp_regenerate_schedule`),
`isis_spf.c` (`_isis_spf_schedule`), `lib/spf_backoff.c` (RFC 8405 states) and the adjacency hold timer; flooding
as least (link delay + per-hop processing) over surviving links; next hops as every equal-cost neighbour from each
router's LSDB view at each SPF run; FIB installs at SPF + a base + a per-route cost. Four constants are free and were
fitted to the 72 measured runs: per-hop flooding 0.8 ms (median error 2.4 ms), SPF-to-first-install 12.7 ms, per-route
7.6 µs, SPF time 1.09 µs × N log₂N (from FRR's own `last-run-duration` at 64 and 96 routers). Two things were found
by the calibration and are now in the model: FRR's SPF has no two-way check (each end's LSP removes one direction of a
link), and a router's own adjacency loss does not schedule SPF, its LSP regeneration does.

How well it matches the bed, over all 72 runs (median of 3 model seeds against each run):

- reconvergence time: model/bed median 1.00, p10 0.92, p90 2.32 (the p90 is stock-timer node failures where the bed's
  isolated router finished at 30 s and the model has it at 58 s: it carries the failed router's own second LSP)
- live loop count, runs with ≥10: median 1.15, p10 0.89, p90 1.76
- every bed case with a live loop over 2 s, the model has within 5%: 30.0/30.4, 30.3/30.5, 5.2/5.4, 5.4/5.3 s
- what it misses: the 1 s `spf-interval` plateau in node-failure cases (bed 1.0–1.1 s, model 36 ms); it under-counts
  gone-destination loops in stock partitions by about 2×.

The model's loop detector was checked against the tier 4 detector on the same timelines (380 loops in the 64-mesh
partition, identical start and end times); that check is gated in `run_all.py`.

Sampled: 64 destinations per run (all of them when N ≤ 64), 3 seeds per case (the seed sets where in the hello
interval each hold timer fires). Partitions were modeled to 10k (grid) and 1k (mesh); above that a partition's cut set
gives every router a distinct view and the shortest-path work per view exceeds the machine.

| timers | topo | N | event | live loops /64 dests | longest live | most SPF survived | reconverged | SPF time |
|---|---|---|---|---|---|---|---|---|
| stock | mesh | 1,000 | partition | 4449 | **31.5 s** | **9** | 60.9 s | 10 ms |
| stock | grid | 10,000 | partition | 0 | | | 31.6 s | 146 ms |
| stock | mesh | 1,000 | nodefail | 22 | 1.1 s | 1 | 58.1 s | |
| stock | grid | 3,000 | nodefail | 156 | 1.1 s | 0 | 58.8 s | |
| stock | mesh | 100,000 | nodefail | 0 | 36 ms | 0 | 61 s | 1.8 s |
| tuned | mesh | 1,000 | partition | 3615 | **5.6 s** | **5** | 9.0 s | |
| tuned | grid | 3,000 | nodefail | 146 | 5.0 s | 0 | 8.7 s | |
| tuned | mesh | 30,000 | nodefail | 8 | 245 ms | 0 | 9.7 s | 0.5 s |
| tuned | mesh | 100,000 | nodefail | 0 | 36 ms | 0 | 11.3 s | 1.8 s |
| tuned | grid | 100,000 | silent | 0 | | | 4.9 s | 1.8 s |

Same plateaus, same causes, at 1,000 as at 96. The one new thing at 100k is SPF time: 1.8 s per run, which lengthens
reconvergence and would lengthen any loop whose fix waits on an SPF.

---

## E2, modeled: SPB agreement (802.1aq), 1k–100k bridges

Day: *"while everyone exchanges hashes with its neighbors, a small fraction would find inconsistencies, if at all ...
The main concern is how many times the few who detect inconsistencies have to do it to get consistent."*

The protocol as modeled (Seaman, *aq-seaman-agreement-protocol-0910-v2*): one participant per port; a bridge's digest
summarises its topology view; on each change it sends the new digest on every port with agree=false and, once its own
forwarding matches, agree=true ("two messages, one from each participant"); a port is agreed when both ends hold the
same digest and both have agreed; until then forwarding on the changed trees over that port is restricted (SPBM
unicast with no permitted FDB entry is discarded, not flooded). Run on the same timelines as E1: a bridge's view
changes when its LSDB does, coalesced to its SPF runs.

| timers | topo | N | event | views per bridge, median (max) | bridges seeing a mismatch | mismatches per detecting bridge, median (max) | msgs per port | ports agreed by | held per port |
|---|---|---|---|---|---|---|---|---|---|
| tuned | grid | 1,000 | silent | 2 (2) | 100% | 4 (4) | 8 | 3.0 s | 0.26 s |
| tuned | grid | 100,000 | silent | 2 (2) | 100% | 4 (4) | 8 | 5.4 s | 0.26 s |
| tuned | mesh | 1,000 | nodefail | 2 (3) | 100% | 3 (16) | 12 | 7.9 s | 0.60 s |
| tuned | mesh | 100,000 | nodefail | 2 (2) | 100% | 2 (10) | 8 | 10.1 s | 0.57 s |
| tuned | grid | 1,000 | partition | 4 (5) | 100% | 8 (13) | 20 | 8.1 s | 5.6 s |
| tuned | mesh | 1,000 | partition | 5 (5) | 96% | 10 (37) | 20 | 8.9 s | 5.6 s |
| stock | grid | 1,000 | partition | 6 (6) | 100% | 11 (17) | 24 | 59.4 s | 30.8 s |
| stock | grid | 10,000 | partition | 4 (5) | 100% | 8 (14) | 20 | 31.6 s | 3.2 s |
| stock | grid | 100,000 | silent | 2 (2) | 100% | 4 (6) | 8 | 32.3 s | 1.3 s |

Two things come out of it:

- **It is not a few.** Every bridge sees a neighbour's digest differ from its own at least once, because every
  bridge's view changes and no two neighbours change at the same instant. 96–100% at every size and event.
- **But each one does it a small, fixed number of times.** A bridge passes through 2 views for a link failure, 2–3 for
  a node failure, 4–6 for a partition; one exchange per view per port, so 8 to 24 messages per port in total; and a
  bridge sees 2–11 mismatches (median) before its ports agree. None of these numbers moves between 1,000 and 100,000
  bridges. Agreement is settled between neighbours, not across the network, which is what the IEEE design says.

What grows with size is the same thing that grows for IS-IS: how long the ports stay held, which is the window until
every bridge has run SPF on the final topology: 3 s (hold timer) for a link, 8 s (RFC 8405) for a node, 30–60 s with
stock timers. SPB turns IS-IS's loop into a hold of the same length.

Not modeled: the per-tree restriction (which of the 16 ECT trees a port blocks), misordering recovery, and the digest
itself; the earlier loop report measured the SPB SPF cost (2.7× one tree, 31× all 16).

---

## What is measured, and what is not

- **Measured:** every row in the E1 measured tables: real FRR 8.4.4 routing processes, real kernel FIBs, real packets
  on TAP links with real delays. The detector found every injected loop in its selftest and reads zero on a clean
  timeline. Run outputs include every route change and SPF run; `s4_persist.py reanalyse DIR` recomputes a run.
- **Modeled, calibrated:** the E1 1k–100k rows and all of E2. The model carries FRR's timer rules, not FRR.
- **Not done:** mesh partitions above 1k and grid partitions above 10k in the model (memory). A 100k-router FRR run
  (one machine cannot hold 100k isisd processes).
- **Corrections during the work, all in the git log:** the first detector lost one of two loops that shared nodes
  (fixed, every run recomputed); the first stock-timer runs stopped watching 15 s after the last change and missed the
  60 s wave (rerun, now 45 s); the model first used `hello 10 × 3` for stock where FRR's default is `3 × 10`.

---

## Reproduce

Build `s4-persist-2026-10-31-r5` / `s4-persist-model-2026-10-31-r4`. Needs Linux with `ip netns`, FRR 8.x
(`zebra`, `isisd`, `vtysh`), root, Python 3 with numpy and scipy. One machine runs every router; 96 routers took
about 1 GB and a load average of 1–3 on 2 CPUs.

```
cd /opt/frognet_semantic/simulation
python3 s4_persist.py selftest
python3 s4_persist.py run --topo mesh --n 96 --event partition --profile default --out /tmp/p-mesh96-part-default
python3 s4_persist.py run --topo mesh --n 96 --event partition --profile tuned   --out /tmp/p-mesh96-part-tuned
python3 s4_persist_model.py check
python3 s4_persist_model.py calibrate /tmp/p-* --out /tmp/cal.json
python3 s4_persist_model.py run --topo mesh --n 100000 --event nodefail --cal /tmp/cal.json
python3 s4_persist_report.py --tier4 /tmp/p-* > /tmp/tables.md
```

`run_all.py` gates the two detectors on every run and, under `FROGNET_SIM_BACKEND=real` as root, one small real case.
