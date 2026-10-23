netns-egress-test.sh, sim on one machine (real kernel namespaces, wireguard-go, the real hub, RAM manager, fdf-facts,
fdf-client, apply_once): y on z's closed LAN; z and a each tunnelled to the hub on the RAM manager's machine; WAN delay
by tools/l2delay.py on each site's leg (z 10 ms each way, a 35 ms each way). Build fdf-2026-10-22-r25 (rina-host);
e2-dist as committed with each run.
run1: node reports and link writes were calls (each waited a round trip): E2 convergence p50 285 ms.
run2: node reports posted: p50 214 ms.
run3: link writes posted too: p50 169 ms (one-way chain floor on a's leg: 140 ms). Every run: all route, name and
      map checks pass; E2 120 of 120 steps match the reference.
