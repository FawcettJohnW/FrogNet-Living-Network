#!/usr/bin/env python3
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#                                                              #
#  SPDX-License-Identifier: GPL-2.0-only                       #
#                                                              #
#  See COPYRIGHT and LICENSE at the root of this tree.         #
################################################################
"""delay_line.py -- a TCP delay LINE between a client and a RAM host: every byte arrives DELAY after it was sent.

    python3 tools/delay_line.py --listen 127.0.0.1:8790 --to 127.0.0.1:8788 --delay-ms 12

Each chunk read is stamped with its arrival time and queued; a separate writer forwards it when its time comes. So
chunks in flight overlap, as on a real path. (redis/tools/shaped_relay.py sleeps after each read before reading again:
under load a chunk waits for the previous chunk's delay and then its own, which measured close to two round trips per
request where there is one. Use this one for latency; that one for rate.)
"""
import argparse, asyncio, time


async def pump(src, dst, delay):
    q = asyncio.Queue()

    async def writer():
        while True:
            due, data = await q.get()
            if data is None:
                break
            wait = due - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            dst.write(data)
            await dst.drain()
        dst.close()

    w = asyncio.ensure_future(writer())
    try:
        while True:
            data = await src.read(65536)
            if not data:
                break
            q.put_nowait((time.monotonic() + delay, data))
    except (ConnectionResetError, BrokenPipeError):
        pass
    q.put_nowait((0, None))
    try:
        await w
    except (ConnectionResetError, BrokenPipeError):
        pass


async def handle(cr, cw, to, delay):
    try:
        sr, sw = await asyncio.open_connection(*to)
    except OSError as e:
        print("delay_line: cannot reach %s:%d: %s" % (to[0], to[1], e), flush=True); cw.close(); return
    await asyncio.gather(pump(cr, sw, delay), pump(sr, cw, delay))


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--listen", required=True); ap.add_argument("--to", required=True)
    ap.add_argument("--delay-ms", type=float, required=True, help="one way")
    a = ap.parse_args()
    lh, lp = a.listen.rsplit(":", 1); th, tp = a.to.rsplit(":", 1)
    srv = await asyncio.start_server(lambda r, w: handle(r, w, (th, int(tp)), a.delay_ms / 1000.0), lh, int(lp))
    async with srv:
        await srv.serve_forever()

asyncio.run(main())
