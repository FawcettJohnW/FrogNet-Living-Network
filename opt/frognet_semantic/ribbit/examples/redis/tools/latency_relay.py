#!/usr/bin/env python3
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#                                                              #
#  SPDX-License-Identifier: GPL-2.0-only                       #
#                                                              #
#  This program is free software; you can redistribute it      #
#  and/or modify it under the terms of the GNU General Public  #
#  License as published by the Free Software Foundation;       #
#  version 2 of the License, and no other version.             #
#                                                              #
#  This program is distributed in the hope that it will be     #
#  useful, but WITHOUT ANY WARRANTY; without even the implied  #
#  warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR     #
#  PURPOSE.  See the GNU General Public License for details.   #
#                                                              #
#  See COPYRIGHT and LICENSE at the root of this tree.         #
################################################################
"""latency_relay.py -- a TCP relay that models a network path's one-way delay, and nothing else.
Every chunk is forwarded exactly --delay-ms after it arrived, in order; reading never pauses while earlier chunks wait
(shaped_relay.py sleeps per chunk, so a frame arriving during another's sleep waits for it too -- up to 2x the delay).
Usage: latency_relay.py --listen HOST:PORT --to HOST:PORT --delay-ms MS   (per direction; the round trip is 2x)"""
import argparse, asyncio, time

async def pump(reader, writer, delay):
    q: asyncio.Queue = asyncio.Queue()
    async def sender():
        while True:
            due, data = await q.get()
            if data is None: break
            wait = due - time.monotonic()
            if wait > 0: await asyncio.sleep(wait)
            writer.write(data)
            await writer.drain()
        try: writer.close()
        except Exception: pass
    task = asyncio.create_task(sender())
    try:
        while True:
            data = await reader.read(1 << 16)
            if not data: break
            q.put_nowait((time.monotonic() + delay, data))
    finally:
        q.put_nowait((time.monotonic() + delay, None))
        await task

async def handle(cr, cw, to, delay):
    h, p = to.rsplit(":", 1)
    try: sr, sw = await asyncio.open_connection(h, int(p))
    except OSError: cw.close(); return
    await asyncio.gather(pump(cr, sw, delay), pump(sr, cw, delay), return_exceptions=True)

async def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--listen", required=True); ap.add_argument("--to", required=True)
    ap.add_argument("--delay-ms", type=float, required=True); a = ap.parse_args()
    h, p = a.listen.rsplit(":", 1)
    srv = await asyncio.start_server(lambda r, w: handle(r, w, a.to, a.delay_ms / 1000.0), h, int(p))
    async with srv: await srv.serve_forever()

if __name__ == "__main__":
    asyncio.run(main())
