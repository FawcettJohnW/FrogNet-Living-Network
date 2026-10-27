# S: 200 subnets across E egress points, memory arm (FD&F region), real daemon (2026-10-23, netns sandbox)

`tools/netns-scale-test.sh` (S=200, N=4 changes), build fdf-2026-10-23-r27, one machine: 2 CPUs, 7 GB, network
namespaces. Every egress node is a full daemon node (tunnel daemon, fdf-client, fdf-facts, hub tunnel); every other
subnet a child on an egress node's LAN. Simulated, not hardware.

| E | cold: last egress node fully correct | change -> last daemon (p50 / max) | daemon's own total per event (p50) | participants / mappings in the region | bed RSS |
|---|---|---|---|---|---|
| 1 | 13.9 s | 211 / 291 ms | 129 ms | 164 KB / 16.5 KB | 5.3 GB |
| 2 | 14.8 s | 276 / 336 ms | 145 ms | 164 KB / 16.5 KB | 5.4 GB |
| 5 | 21.3 s | 348 / 625 ms | 200 ms | 166 KB / 16.5 KB | 5.6 GB |
| 10 | 36.7 s | 417 / 688 ms | 302 ms | 168 KB / 16.5 KB | 5.9 GB |
| 20 | 73.0 s | 697 / 1679 ms | 487 ms | 173 KB / 16.5 KB | 6.6 GB |
| 50 | 219.1 s | 1532 / 2305 ms | 1115 ms | 187 KB / 16.5 KB | 8.7 GB |
| 100 | not run: the bed ran out of memory (the kernel's OOM killer ended rina-host) | | | | |

Every run that ran: all egress nodes correct (each route on its expected next hop, every name), 10/10 pings across
egress nodes, every daemon applied every change.

What the region holds does not grow with E: mappings are 16.5 KB at every E (200 subnets' mappings), participants
grow by the egress nodes' extra rows (~0.4 KB each). The times grow with E because every daemon on this machine
shares two CPUs: one event wakes all E daemons at once. The daemon's own `total` is wall time inside the daemon and
includes waiting for a CPU, so it is not the per-node cost beyond small E. Next measurement: each daemon's CPU time
per event (/proc), which the sharing does not inflate.

Failed and superseded runs (kept): `FAILED-S200-E1-inotify-limit.txt` (the bed's shared inotify limit; fixed in the
bed, and r26 makes fdf-client/fdf-facts say so); `r26/` (E=1,2,5 on r26; `FAILED-S200-E5-run1.txt` and
`FAILED-S200-E10-r26-baseline-race.txt`: one child's facts never re-announced, fixed in r27
[FACTS_BASELINE_IS_WHAT_WAS_ANNOUNCED_V1]); `FAILED-S200-E100-bed-oom.txt`.
