#!/usr/bin/env python3
"""shaped_relay.py -- a shaped TCP wire between a client and a RAM server, for the data-plane tests.

Every connection through it is rate-limited (bytes per second, each direction, a token bucket refilled every 5 ms)
and delayed (one-way latency). That is the whole simulation: the client, the server and every frame are real.

    python3 tools/shaped_relay.py --listen 127.0.0.1:8790 --to 127.0.0.1:8789 --rate 4000000 --delay-ms 20

Prints one line per connection when it closes: bytes each way and the seconds it was open.
"""
import argparse, asyncio, sys, time

REFILL_S = 0.005


async def pump(src, dst, rate, delay, stats, key):
    bucket = rate * REFILL_S; last = time.monotonic()
    try:
        while True:
            data = await src.read(65536)
            if not data:
                break
            if delay:
                await asyncio.sleep(delay)
            off = 0
            while off < len(data):
                now = time.monotonic(); bucket = min(rate * 0.05, bucket + (now - last) * rate); last = now
                if bucket < 1:
                    await asyncio.sleep(REFILL_S); continue
                n = min(int(bucket), len(data) - off)
                dst.write(data[off:off + n]); await dst.drain()
                bucket -= n; off += n; stats[key] += n
    except (ConnectionResetError, BrokenPipeError, asyncio.CancelledError):
        pass
    finally:
        try:
            dst.close()
        except Exception:
            pass


async def handle(cr, cw, to, rate, delay):
    t0 = time.monotonic(); host, port = to
    try:
        sr, sw = await asyncio.open_connection(host, port)
    except OSError as e:
        print("relay: cannot reach %s:%d: %s" % (host, port, e), flush=True); cw.close(); return
    stats = {"up": 0, "down": 0}
    await asyncio.gather(pump(cr, sw, rate, delay, stats, "up"), pump(sr, cw, rate, delay, stats, "down"))
    print("relay: connection closed up=%d down=%d bytes in %.2fs" % (stats["up"], stats["down"], time.monotonic() - t0), flush=True)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--listen", required=True); ap.add_argument("--to", required=True)
    ap.add_argument("--rate", type=int, required=True, help="bytes per second, each direction, per connection")
    ap.add_argument("--delay-ms", type=float, default=0.0)
    a = ap.parse_args()
    lh, lp = a.listen.rsplit(":", 1); th, tp = a.to.rsplit(":", 1)
    server = await asyncio.start_server(lambda r, w: handle(r, w, (th, int(tp)), a.rate, a.delay_ms / 1000.0), lh, int(lp))
    print("relay: %s -> %s at %d B/s each way, %.0f ms one-way" % (a.listen, a.to, a.rate, a.delay_ms), flush=True)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
