#!/usr/bin/env python3
"""l2delay.py IF_A IF_B DELAY_MS [JITTER_MS] -- [L2_DELAY_V1] a transparent layer-2 delay bridge: every frame that
arrives on one interface leaves on the other DELAY_MS later (each direction), so everything crossing the leg pays it --
ARP, WireGuard, the RAM sessions, ping -- with nothing in FrogNet changed. The netns tests' WAN: one per site leg,
in a namespace of its own between the site and the "Internet" bridge. Needs root (AF_PACKET). Frames keep their order
(a constant delay; JITTER_MS, if given, is added per frame but never lets a frame overtake the one before it).
Writes one line per 10 s: frames each way, the largest queue.
Every interface whose frames cross the bridge needs checksum and segmentation offload off (ethtool -K IF tx off tso off
gso off gro off), as on a real wire: a frame read here with its checksum still to be filled in is dropped at the far
end, so only ICMP would cross ([L2_DELAY_NO_OFFLOAD_V1])."""
import heapq, random, select, socket, sys, time
ETH_P_ALL = 0x0003
PACKET_OUTGOING = 4
a, b, delay = sys.argv[1], sys.argv[2], float(sys.argv[3]) / 1000.0
jitter = float(sys.argv[4]) / 1000.0 if len(sys.argv) > 4 else 0.0
def sock(ifn):
    s = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(ETH_P_ALL)); s.bind((ifn, 0)); s.setblocking(False); return s
sa, sb = sock(a), sock(b); other = {sa.fileno(): sb, sb.fileno(): sa}; last_due = {sa.fileno(): 0.0, sb.fileno(): 0.0}
q = []; seq = 0; count = {a: 0, b: 0}; maxq = 0; t_report = time.monotonic() + 10
print(f"l2delay {a} <-> {b}: {delay * 1000:.1f} ms each way" + (f" + up to {jitter * 1000:.1f} ms jitter" if jitter else ""), flush=True)
while True:
    now = time.monotonic()
    while q and q[0][0] <= now:
        _, _, out, frame = heapq.heappop(q)
        try: out.send(frame)
        except OSError: pass
    timeout = max(0.0, q[0][0] - now) if q else 1.0
    r, _, _ = select.select([sa, sb], [], [], timeout)
    for s in r:
        while True:
            try: frame, addr = s.recvfrom(65535)
            except BlockingIOError: break
            if addr[2] == PACKET_OUTGOING: continue            # our own transmission on this interface, not an arrival
            due = time.monotonic() + delay + (random.uniform(0, jitter) if jitter else 0.0)
            due = max(due, last_due[s.fileno()]); last_due[s.fileno()] = due   # never reorder a direction
            seq += 1; heapq.heappush(q, (due, seq, other[s.fileno()], frame))
            count[a if s is sa else b] += 1
    maxq = max(maxq, len(q))
    if time.monotonic() >= t_report:
        print(f"l2delay {a}->{b} {count[a]} frames, {b}->{a} {count[b]} frames, largest queue {maxq}", flush=True); t_report += 10
