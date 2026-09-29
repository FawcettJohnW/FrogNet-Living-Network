# Performance -- lispers.net 0.643 vs Ribbit-LISP v0.63-bounded

perf v2 (package v0.63-bounded). Load generator 10.250.250.1; monitors: control, ram; warmup 5s, measured 20s per run.

## Throughput and latency (load generator)

| system | workload | clients | state | ops/s | p50 ms | p95 ms | p99 ms | errors | unanswered |
|---|---|---|---|---|---|---|---|---|---|
| lispers.net | reg | 1 | 1000 | 357 | 2.84 | 3.31 | 3.96 | 0 | 0 |
| lispers.net | reg | 4 | 1000 | 793 | 4.84 | 6.06 | 6.72 | 0 | 0 |
| lispers.net | reg | 16 | 1000 | 769 | 20.23 | 24.95 | 28.68 | 0 | 0 |
| lispers.net | reg | 64 | 1000 | 718 | 85.21 | 149.33 | 203.02 | 0 | 0 |
| lispers.net | req | 1 | 1000 | 334 | 2.91 | 3.45 | 4.39 | 0 | 0 |
| lispers.net | req | 4 | 1000 | 516 | 7.61 | 9.44 | 10.69 | 0 | 0 |
| lispers.net | req | 16 | 1000 | 484 | 31.87 | 41.22 | 44.93 | 0 | 0 |
| lispers.net | req | 64 | 1000 | 484 | 130.20 | 155.14 | 168.19 | 0 | 0 |
| lispers.net | mixed | 1 | 1000 | 334 | 2.97 | 3.53 | 4.35 | 0 | 0 |
| lispers.net | mixed | 4 | 1000 | 491 | 8.06 | 9.30 | 10.91 | 0 | 0 |
| lispers.net | mixed | 16 | 1000 | 491 | 32.79 | 36.41 | 39.50 | 0 | 0 |
| lispers.net | mixed | 64 | 1000 | 495 | 132.27 | 142.80 | 153.47 | 0 | 0 |
| lispers.net | req-host | 16 | 1 | 938 | 16.15 | 22.28 | 26.50 | 0 | 0 |
| lispers.net | req-host | 16 | 100 | 877 | 17.87 | 20.47 | 23.51 | 0 | 0 |
| lispers.net | req-host | 16 | 1000 | 508 | 29.81 | 40.77 | 46.46 | 0 | 0 |
| lispers.net | req-host | 16 | 10000 | 93 | 175.18 | 206.91 | 244.12 | 0 | 0 |
| lispers.net | req-nested | 16 | 1 | 924 | 16.59 | 22.10 | 25.73 | 0 | 0 |
| lispers.net | req-nested | 16 | 100 | 821 | 19.07 | 23.66 | 28.29 | 0 | 0 |
| lispers.net | req-nested | 16 | 1000 | 486 | 31.06 | 42.17 | 46.83 | 0 | 0 |
| lispers.net | req-nested | 16 | 10000 | 114 | 136.90 | 181.30 | 204.74 | 0 | 0 |
| ribbit | reg | 1 | 1000 | 39 | 25.10 | 29.18 | 34.45 | 0 | 0 |
| ribbit | reg | 4 | 1000 | 161 | 24.39 | 27.52 | 32.85 | 0 | 0 |
| ribbit | reg | 16 | 1000 | 591 | 26.28 | 32.71 | 45.18 | 0 | 0 |
| ribbit | reg | 64 | 1000 | 1565 | 38.06 | 63.01 | 74.59 | 0 | 0 |
| ribbit | req | 1 | 1000 | 891 | 1.02 | 1.55 | 2.10 | 0 | 0 |
| ribbit | req | 4 | 1000 | 3014 | 1.21 | 1.97 | 2.51 | 0 | 0 |
| ribbit | req | 16 | 1000 | 9398 | 1.61 | 2.44 | 2.99 | 0 | 0 |
| ribbit | req | 64 | 1000 | 20407 | 2.94 | 4.51 | 5.89 | 0 | 0 |
| ribbit | mixed | 1 | 1000 | 161 | 1.26 | 25.96 | 27.73 | 0 | 0 |
| ribbit | mixed | 4 | 1000 | 581 | 1.36 | 25.66 | 30.45 | 0 | 1 |
| ribbit | mixed | 16 | 1000 | 2326 | 1.89 | 27.75 | 32.74 | 0 | 0 |
| ribbit | mixed | 64 | 1000 | 5125 | 2.67 | 62.24 | 83.08 | 0 | 0 |
| ribbit | req-host | 16 | 1 | 9210 | 1.63 | 2.54 | 3.15 | 0 | 0 |
| ribbit | req-host | 16 | 100 | 9213 | 1.62 | 2.55 | 3.23 | 0 | 0 |
| ribbit | req-host | 16 | 1000 | 9044 | 1.66 | 2.58 | 3.20 | 0 | 0 |
| ribbit | req-host | 16 | 10000 | 9123 | 1.65 | 2.53 | 3.12 | 0 | 0 |
| ribbit | req-nested | 16 | 1 | 9346 | 1.60 | 2.47 | 3.06 | 0 | 0 |
| ribbit | req-nested | 16 | 100 | 9272 | 1.61 | 2.53 | 3.15 | 0 | 0 |
| ribbit | req-nested | 16 | 1000 | 9063 | 1.66 | 2.52 | 3.07 | 0 | 0 |
| ribbit | req-nested | 16 | 10000 | 9164 | 1.63 | 2.53 | 3.17 | 0 | 0 |

## Work per successful operation -- CPU-seconds per 1M operations, by component

control = the systems' machine (lispers.net's lisp-* processes, or Ribbit's front); shared memory = the RAM server's machine; load = the generator.

| system | workload | clients | state | control: lispers.net | control: ribbit-front | shared memory: ram-server | system total | load-gen |
|---|---|---|---|---|---|---|---|---|
| lispers.net | reg | 1 | 1000 | 1772.9 | 0.0 | 12.6 | 1772.9 | 45.7 |
| lispers.net | reg | 4 | 1000 | 1552.6 | 0.0 | 4.4 | 1552.6 | 51.6 |
| lispers.net | reg | 16 | 1000 | 1587.0 | 0.0 | 4.6 | 1587.0 | 65.4 |
| lispers.net | reg | 64 | 1000 | 1650.3 | 0.0 | 4.2 | 1650.3 | 69.3 |
| lispers.net | req | 1 | 1000 | 2022.2 | 0.0 | 10.5 | 2022.2 | 33.5 |
| lispers.net | req | 4 | 1000 | 2399.6 | 0.0 | 4.8 | 2399.6 | 45.1 |
| lispers.net | req | 16 | 1000 | 2559.9 | 0.0 | 6.2 | 2559.9 | 60.9 |
| lispers.net | req | 64 | 1000 | 2585.8 | 0.0 | 7.2 | 2585.8 | 64.9 |
| lispers.net | mixed | 1 | 1000 | 2029.1 | 0.0 | 7.5 | 2029.1 | 42.7 |
| lispers.net | mixed | 4 | 1000 | 2511.7 | 0.0 | 8.1 | 2511.7 | 68.8 |
| lispers.net | mixed | 16 | 1000 | 2514.8 | 0.0 | 9.2 | 2514.8 | 62.3 |
| lispers.net | mixed | 64 | 1000 | 2510.4 | 0.0 | 8.1 | 2510.4 | 55.4 |
| lispers.net | req-host | 16 | 1 | 1579.3 | 0.0 | 2.7 | 1579.3 | 48.4 |
| lispers.net | req-host | 16 | 100 | 1653.6 | 0.0 | 3.4 | 1653.6 | 45.7 |
| lispers.net | req-host | 16 | 1000 | 2458.2 | 0.0 | 4.9 | 2458.2 | 56.9 |
| lispers.net | req-host | 16 | 10000 | 11373.2 | 0.0 | 37.7 | 11373.2 | 72.3 |
| lispers.net | req-nested | 16 | 1 | 1617.5 | 0.0 | 3.8 | 1617.5 | 53.7 |
| lispers.net | req-nested | 16 | 100 | 1751.7 | 0.0 | 3.7 | 1751.7 | 53.3 |
| lispers.net | req-nested | 16 | 1000 | 2574.8 | 0.0 | 5.1 | 2574.8 | 63.6 |
| lispers.net | req-nested | 16 | 10000 | 9394.2 | 0.0 | 30.7 | 9394.2 | 69.7 |
| ribbit | reg | 1 | 1000 | 0.0 | 2340.2 | 3503.8 | 5844.0 | 116.2 |
| ribbit | reg | 4 | 1000 | 0.0 | 1067.0 | 1296.5 | 2363.5 | 76.5 |
| ribbit | reg | 16 | 1000 | 0.0 | 748.2 | 997.5 | 1745.7 | 85.1 |
| ribbit | reg | 64 | 1000 | 0.0 | 679.9 | 815.1 | 1495.0 | 74.2 |
| ribbit | req | 1 | 1000 | 0.0 | 154.9 | 3.9 | 158.8 | 28.5 |
| ribbit | req | 4 | 1000 | 0.0 | 77.3 | 1.2 | 78.5 | 31.7 |
| ribbit | req | 16 | 1000 | 0.0 | 43.2 | 0.4 | 43.6 | 47.4 |
| ribbit | req | 64 | 1000 | 0.0 | 29.1 | 0.2 | 29.3 | 72.4 |
| ribbit | mixed | 1 | 1000 | 0.0 | 586.8 | 664.4 | 1251.2 | 47.9 |
| ribbit | mixed | 4 | 1000 | 0.0 | 314.0 | 327.7 | 641.7 | 43.6 |
| ribbit | mixed | 16 | 1000 | 0.0 | 224.6 | 227.2 | 451.8 | 48.0 |
| ribbit | mixed | 64 | 1000 | 0.0 | 189.9 | 167.6 | 357.6 | 61.3 |
| ribbit | req-host | 16 | 1 | 0.0 | 43.7 | 0.4 | 44.1 | 52.2 |
| ribbit | req-host | 16 | 100 | 0.0 | 42.2 | 0.3 | 42.6 | 49.9 |
| ribbit | req-host | 16 | 1000 | 0.0 | 45.6 | 0.3 | 45.9 | 50.8 |
| ribbit | req-host | 16 | 10000 | 0.0 | 46.8 | 0.4 | 47.2 | 48.9 |
| ribbit | req-nested | 16 | 1 | 0.0 | 41.1 | 0.3 | 41.5 | 55.0 |
| ribbit | req-nested | 16 | 100 | 0.0 | 41.8 | 0.4 | 42.2 | 57.3 |
| ribbit | req-nested | 16 | 1000 | 0.0 | 45.8 | 0.5 | 46.3 | 51.4 |
| ribbit | req-nested | 16 | 10000 | 0.0 | 46.4 | 0.6 | 47.0 | 56.8 |

## Machines -- CPU utilization and network per operation

| system | workload | clients | state | control busy % | shared-memory busy % | load busy % | control B/op | shared-memory B/op | control pk/op | shared-memory pk/op | ram-server RSS MB |
|---|---|---|---|---|---|---|---|---|---|---|---|
| lispers.net | reg | 1 | 1000 | 17.0 | 5.5 | 5.4 | 279 | 168 | 2.02 | 1.05 | 51 |
| lispers.net | reg | 4 | 1000 | 32.4 | 6.1 | 3.7 | 277 | 74 | 2.01 | 0.45 | 51 |
| lispers.net | reg | 16 | 1000 | 32.3 | 3.8 | 7.5 | 277 | 59 | 2.01 | 0.37 | 51 |
| lispers.net | reg | 64 | 1000 | 31.1 | 3.2 | 5.2 | 276 | 84 | 2.00 | 0.51 | 51 |
| lispers.net | req | 1 | 1000 | 18.2 | 2.8 | 3.4 | 215 | 163 | 2.02 | 1.05 | 51 |
| lispers.net | req | 4 | 1000 | 33.5 | 2.3 | 3.2 | 214 | 83 | 2.02 | 0.53 | 51 |
| lispers.net | req | 16 | 1000 | 33.1 | 3.0 | 7.8 | 214 | 139 | 2.02 | 0.80 | 51 |
| lispers.net | req | 64 | 1000 | 33.0 | 3.9 | 9.0 | 213 | 158 | 2.01 | 0.89 | 51 |
| lispers.net | mixed | 1 | 1000 | 18.4 | 3.9 | 4.8 | 228 | 225 | 2.03 | 1.26 | 51 |
| lispers.net | mixed | 4 | 1000 | 32.9 | 4.2 | 11.2 | 227 | 121 | 2.02 | 0.73 | 51 |
| lispers.net | mixed | 16 | 1000 | 32.5 | 4.3 | 6.0 | 227 | 184 | 2.02 | 1.07 | 51 |
| lispers.net | mixed | 64 | 1000 | 32.7 | 4.9 | 3.6 | 226 | 94 | 2.01 | 0.56 | 51 |
| lispers.net | req-host | 16 | 1 | 39.2 | 2.6 | 4.7 | 213 | 40 | 2.01 | 0.27 | 51 |
| lispers.net | req-host | 16 | 100 | 39.0 | 2.8 | 3.9 | 213 | 48 | 2.01 | 0.30 | 51 |
| lispers.net | req-host | 16 | 1000 | 33.2 | 3.2 | 5.3 | 214 | 117 | 2.02 | 0.73 | 51 |
| lispers.net | req-host | 16 | 10000 | 27.5 | 5.6 | 7.1 | 225 | 745 | 2.10 | 4.37 | 51 |
| lispers.net | req-nested | 16 | 1 | 39.7 | 3.3 | 4.5 | 214 | 46 | 2.01 | 0.28 | 52 |
| lispers.net | req-nested | 16 | 100 | 38.6 | 3.1 | 4.5 | 213 | 50 | 2.01 | 0.32 | 52 |
| lispers.net | req-nested | 16 | 1000 | 33.4 | 4.0 | 6.1 | 215 | 106 | 2.02 | 0.64 | 52 |
| lispers.net | req-nested | 16 | 10000 | 28.2 | 3.1 | 3.3 | 220 | 408 | 2.06 | 2.46 | 52 |
| ribbit | reg | 1 | 1000 | 4.4 | 15.2 | 14.9 | 3394 | 5367 | 18.52 | 30.24 | 56 |
| ribbit | reg | 4 | 1000 | 6.6 | 15.4 | 6.5 | 2173 | 2266 | 9.41 | 10.14 | 56 |
| ribbit | reg | 16 | 1000 | 14.4 | 35.9 | 14.4 | 1804 | 1612 | 6.15 | 4.88 | 57 |
| ribbit | reg | 64 | 1000 | 31.4 | 68.9 | 11.8 | 1725 | 1475 | 4.39 | 2.53 | 59 |
| ribbit | req | 1 | 1000 | 4.7 | 3.5 | 3.8 | 213 | 54 | 2.01 | 0.34 | 59 |
| ribbit | req | 4 | 1000 | 9.8 | 2.9 | 5.3 | 212 | 15 | 2.00 | 0.09 | 59 |
| ribbit | req | 16 | 1000 | 14.2 | 2.4 | 13.8 | 212 | 4 | 2.00 | 0.03 | 59 |
| ribbit | req | 64 | 1000 | 18.3 | 3.6 | 43.1 | 212 | 2 | 2.00 | 0.01 | 59 |
| ribbit | mixed | 1 | 1000 | 4.3 | 10.1 | 6.4 | 862 | 955 | 5.39 | 5.61 | 59 |
| ribbit | mixed | 4 | 1000 | 7.8 | 16.7 | 4.4 | 699 | 518 | 3.76 | 2.18 | 59 |
| ribbit | mixed | 16 | 1000 | 17.8 | 31.2 | 8.8 | 604 | 389 | 3.02 | 1.14 | 59 |
| ribbit | mixed | 64 | 1000 | 29.4 | 47.9 | 16.5 | 544 | 322 | 2.55 | 0.56 | 59 |
| ribbit | req-host | 16 | 1 | 13.4 | 3.5 | 23.2 | 212 | 9 | 2.00 | 0.05 | 61 |
| ribbit | req-host | 16 | 100 | 13.0 | 3.5 | 20.3 | 212 | 9 | 2.00 | 0.05 | 64 |
| ribbit | req-host | 16 | 1000 | 14.0 | 2.6 | 21.1 | 212 | 10 | 2.00 | 0.06 | 66 |
| ribbit | req-host | 16 | 10000 | 14.3 | 6.2 | 16.6 | 212 | 5 | 2.00 | 0.03 | 73 |
| ribbit | req-nested | 16 | 1 | 12.5 | 3.7 | 16.5 | 212 | 4 | 2.00 | 0.03 | 92 |
| ribbit | req-nested | 16 | 100 | 12.9 | 3.8 | 21.3 | 212 | 7 | 2.00 | 0.04 | 108 |
| ribbit | req-nested | 16 | 1000 | 14.4 | 4.1 | 14.8 | 211 | 7 | 1.99 | 0.04 | 125 |
| ribbit | req-nested | 16 | 10000 | 14.5 | 6.2 | 20.8 | 212 | 6 | 2.00 | 0.04 | 145 |
