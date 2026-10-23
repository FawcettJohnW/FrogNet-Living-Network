#!/usr/bin/env python3
"""wg_bearer_relay.py CONFIG.json -- [WG_BEARER_RELAY_V1] the bearer under each simulated WireGuard tunnel, with a
delay. netns_backend's tunnels run over 127.0.0.1 ports in the root namespace; there is no wire to delay. This sits
between the two ends of every tunnel:

    end A (listens pa) --sends to pb+OFF--> relay --after d_ab, from pa+OFF--> end B (listens pb)
    end B (listens pb) --sends to pa+OFF--> relay --after d_ba, from pb+OFF--> end A (listens pa)

Each end sees the other's packets arrive from the port it sends to, so WireGuard's endpoint roaming keeps the path
through the relay. CONFIG: {"offset": OFF, "links": [[pa, pb, d_ab_ms, d_ba_ms], ...]}. Order is kept per direction
(a constant delay never reorders). One line per 10 s: datagrams each way per link."""
import heapq, json, select, socket, sys, time

cfg = json.load(open(sys.argv[1]))
OFF = int(cfg["offset"])
socks = {}          # relay port -> socket
route = {}          # fileno -> (send-from socket, dest port, delay s, label)
count = {}
for pa, pb, dab, dba in cfg["links"]:
    for port in (pa + OFF, pb + OFF):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.bind(("127.0.0.1", port)); s.setblocking(False)
        socks[port] = s
    sa, sb = socks[pa + OFF], socks[pb + OFF]
    route[sb.fileno()] = (sa, pb, dab / 1000.0, f"{pa}->{pb}")      # from A (sent to pb+OFF): deliver to B from pa+OFF
    route[sa.fileno()] = (sb, pa, dba / 1000.0, f"{pb}->{pa}")      # from B (sent to pa+OFF): deliver to A from pb+OFF
    count[f"{pa}->{pb}"] = 0; count[f"{pb}->{pa}"] = 0
print(f"wg_bearer_relay: {len(cfg['links'])} tunnels, offset {OFF}: " +
      ", ".join(f"{pa}<->{pb} {dab:g}/{dba:g} ms" for pa, pb, dab, dba in cfg["links"]), flush=True)
q = []; seq = 0; t_report = time.monotonic() + 10
allsocks = list(socks.values())
while True:
    now = time.monotonic()
    while q and q[0][0] <= now:
        _, _, out, port, data = heapq.heappop(q)
        try: out.sendto(data, ("127.0.0.1", port))
        except OSError: pass
    r, _, _ = select.select(allsocks, [], [], max(0.0, q[0][0] - now) if q else 1.0)
    for s in r:
        out, port, d, label = route[s.fileno()]
        while True:
            try: data, _src = s.recvfrom(65535)
            except BlockingIOError: break
            seq += 1; heapq.heappush(q, (time.monotonic() + d, seq, out, port, data)); count[label] += 1
    if time.monotonic() >= t_report:
        print("wg_bearer_relay " + " ".join(f"{k}:{v}" for k, v in count.items()), flush=True); t_report += 10
