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
"""[CELL_V1] Oracle for the cell wire.

Part 1 -- format: round-trip, and every malformed cell raises rather than
parsing into something plausible. A fallback here would produce a cell that
parses and a picture that decodes and no line in any log.

Part 2 -- latency: the thing the relay measured. A rate-limited reader stands in
for the tunnel, a 48 KB keyframe lands mid-stream, and every 20 ms audio frame
is timestamped from enqueue to arrival. Worst case is the number; mean hides a
lump followed by a surplus, which is exactly how the 2-second relay windows read
1.00 s/s on either side of a gap.

Link rates come from ss on the real tunnels: cwnd:3 x mss:1368 / rtt:148ms is
27.7 KB/s on the collapsed paths, cwnd:10 is ~92 KB/s on the healthy ones.
"""

import socket
import struct
import threading
import time

import fnav
from fnav import _LEN, KIND_AUDIO, KIND_VIDEO, pack_typed, pack_video, unpack_typed
import interleave
from interleave import (
    CellWire, CellReader, CellProtocolError, pack_cell, unpack_cell,
    CELL_BYTES, KIND_MASK, E_CONT, E_LAST, E_ABORT, MAX_ENTRIES,
    _CELL_HDR, _ENTRY, _HDR_BYTES,
)

FAILURES = []


def check(name, fn):
    try:
        fn()
    except AssertionError as e:
        FAILURES.append("%s: %s" % (name, e))
        print("  FAIL %-46s %s" % (name, e))
        return
    print("  ok   %s" % name)


def raises(exc, fn, what):
    try:
        fn()
    except exc:
        return
    except Exception as e:
        raise AssertionError("raised %s, expected %s" % (type(e).__name__, exc.__name__))
    raise AssertionError("did NOT raise on %s" % what)


# ---- part 1: format ------------------------------------------------------

def t_roundtrip():
    ents = [(KIND_AUDIO | E_LAST, b"\x78" + b"\x11" * 54),
            (KIND_VIDEO | E_CONT, b"\x22" * 900)]
    got = unpack_cell(pack_cell(ents))
    assert got == ents, "round trip changed the entries"


def t_mixed_boundary():
    """The map is what makes audio-then-video in one cell readable."""
    a = b"\x78" + b"\xaa" * 59
    v = b"\xbb" * 300
    got = unpack_cell(pack_cell([(KIND_AUDIO | E_LAST, a), (KIND_VIDEO, v)]))
    assert got[0][1] == a and got[1][1] == v, "payload split at the wrong offset"


def t_empty_cell():
    raises(CellProtocolError, lambda: pack_cell([]), "an empty cell")


def t_count_overflow():
    raises(CellProtocolError,
           lambda: pack_cell([(KIND_AUDIO | E_LAST, b"x")] * (MAX_ENTRIES + 1)),
           "more entries than the ceiling")


def t_oversize_cell():
    raises(CellProtocolError,
           lambda: pack_cell([(KIND_VIDEO, b"x" * (CELL_BYTES + 1))]),
           "a cell over the MSS ceiling")


def t_lying_count():
    """Count says 15, cell holds one entry. Must raise, not read past the end."""
    good = pack_cell([(KIND_AUDIO | E_LAST, b"\x78abc")])
    bad = _CELL_HDR.pack(15) + good[_HDR_BYTES:]
    raises(CellProtocolError, lambda: unpack_cell(bad), "a count the cell cannot hold")


def t_length_sum_short():
    """Lengths must account for EVERY payload byte -- not 'at least'."""
    good = pack_cell([(KIND_VIDEO, b"y" * 100)])
    bad = good + b"\x00" * 8
    raises(CellProtocolError, lambda: unpack_cell(bad), "trailing unaccounted bytes")


def t_length_sum_long():
    good = pack_cell([(KIND_VIDEO, b"y" * 100)])
    bad = good[:-10]
    raises(CellProtocolError, lambda: unpack_cell(bad), "lengths exceeding the payload")


def t_zero_count():
    raises(CellProtocolError, lambda: unpack_cell(_CELL_HDR.pack(0)), "a zero count")


def t_unknown_kind():
    r = CellReader()
    cell = pack_cell([(0x2F | E_LAST, b"junk")])
    raises(CellProtocolError, lambda: r.feed(cell), "an unregistered kind")


def t_fragmented_audio():
    r = CellReader()
    cell = pack_cell([(KIND_AUDIO | E_CONT, b"half")])
    raises(CellProtocolError, lambda: r.feed(cell), "a fragmented Opus frame")


def t_cont_with_nothing_open():
    r = CellReader()
    cell = pack_cell([(KIND_VIDEO | E_CONT, b"tail")])
    raises(CellProtocolError, lambda: r.feed(cell), "a continuation with no frame open")


def t_restart_over_open_frame():
    r = CellReader()
    r.feed(pack_cell([(KIND_VIDEO, b"head")]))
    raises(CellProtocolError, lambda: r.feed(pack_cell([(KIND_VIDEO, b"new")])),
           "a new frame over an open one")


def t_abort_with_nothing_open():
    r = CellReader()
    raises(CellProtocolError,
           lambda: r.feed(pack_cell([(KIND_VIDEO | E_ABORT, b"")])),
           "an abort with no frame open")


def t_abort_clears():
    r = CellReader()
    r.feed(pack_cell([(KIND_VIDEO, b"partial")]))
    assert r.feed(pack_cell([(KIND_VIDEO | E_ABORT, b"")])) == [], "abort emitted a frame"
    assert r.video_aborted == 1, "abort not counted"
    out = r.feed(pack_cell([(KIND_VIDEO | E_LAST, b"clean")]))
    assert out == [(KIND_VIDEO, b"clean")], "reader did not recover after abort"


def t_reassembly():
    """Segments in, the original payload out, byte for byte."""
    r = CellReader()
    payload = bytes(range(256)) * 40          # 10240 bytes
    step = 900
    for off in range(0, len(payload), step):
        chunk = payload[off:off + step]
        k = KIND_VIDEO
        if off:
            k |= E_CONT
        if off + step >= len(payload):
            k |= E_LAST
        out = r.feed(pack_cell([(k, chunk)]))
    assert out == [(KIND_VIDEO, payload)], "reassembled payload differs from the original"


# ---- part 2: latency -----------------------------------------------------

KEYFRAME_BYTES = 48 * 1024
AUDIO_BYTES = 60
AUDIO_INTERVAL_S = 0.020
AUDIO_FRAMES = 50


def _reader(sock, drain_bps, out, stop, reader):
    budget = 0.0
    last = time.monotonic()
    buf = b""
    sock.setblocking(True)
    sock.settimeout(0.2)
    while not stop.is_set():
        now = time.monotonic()
        budget = min(max(drain_bps * 0.05, 4096.0), budget + (now - last) * drain_bps)
        last = now
        if budget < 1368:
            time.sleep(0.002)
            continue
        try:
            chunk = sock.recv(int(min(budget, 65536)))
        except socket.timeout:
            continue
        except OSError:
            return
        if not chunk:
            return
        budget -= len(chunk)
        buf += chunk
        while len(buf) >= 4:
            (n,) = _LEN.unpack(buf[:4])
            if len(buf) < 4 + n:
                break
            body = buf[4:4 + n]
            buf = buf[4 + n:]
            if reader is None:
                kind, _src, _p = unpack_typed(body)
                out.append((time.monotonic(), kind))
            else:
                for kind, _p in reader.feed(body):
                    out.append((time.monotonic(), kind))


def _run(cells, drain_bps, sndbuf):
    a, b = socket.socketpair()
    a.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, sndbuf)
    arrivals = []
    stop = threading.Event()
    rdr = CellReader() if cells else None
    th = threading.Thread(target=_reader,
                          args=(b, drain_bps, arrivals, stop, rdr), daemon=True)
    th.start()

    kf = pack_video(7, b"\x00" * KEYFRAME_BYTES, 0, is_key=True)
    if cells:
        w = CellWire(a, "Dave")
        w.start()
        put_a, put_v = w.put_audio, w.put_video
    else:
        q = fnav.SotFDataPlane(a, sndbuf=sndbuf)
        put_a = lambda p: q.put(pack_typed(KIND_AUDIO, "Dave", p), droppable=False)
        put_v = lambda p: q.put(pack_typed(KIND_VIDEO, "Dave", p),
                                droppable=True, is_key=True)

    sent = []
    t0 = time.monotonic()
    for i in range(AUDIO_FRAMES):
        if i == 5:
            put_v(kf)
        sent.append(time.monotonic())
        put_a(b"\x78" + b"\x00" * (AUDIO_BYTES - 1))
        time.sleep(max(0.0, (i + 1) * AUDIO_INTERVAL_S - (time.monotonic() - t0)))

    time.sleep(max(3.0, KEYFRAME_BYTES / float(drain_bps) * 1.6))
    if cells:
        w.stop()
        time.sleep(0.2)
    stop.set(); th.join(timeout=1.0)
    a.close(); b.close()

    aa = [t for (t, k) in arrivals if k == KIND_AUDIO]
    lat = [aa[i] - sent[i] for i in range(min(len(aa), len(sent)))]
    vv = sum(1 for (_t, k) in arrivals if k == KIND_VIDEO)
    return (max(lat) * 1000 if lat else float("nan"), len(aa), vv)


def main():
    print("[CELL_V1] format\n")
    for name, fn in sorted((k[2:], v) for k, v in globals().items()
                           if k.startswith("t_") and callable(v)):
        check(name, fn)

    print("\n[CELL_V1] audio latency behind a 48 KB keyframe")
    print("  (rates from the MEDIA path: 145 KB/s measured by [VID-TX], "
          "340 KB/s = the 2.72Mbps delivery_rate on :53136)\n")
    print("  %-22s %-10s %11s  %-9s %s"
          % ("path", "SNDBUF", "worst audio", "audio", "video"))
    for rate_name, rate in (("media    145 KB/s", 145 * 1024),
                            ("media    340 KB/s", 340 * 1024)):
        for sb in (1 << 17, 1 << 14):
            for cells in (False, True):
                worst, na, nv = _run(cells, rate, sb)
                print("  %-22s %-10s %8.1f ms  %2d/%-6d %d %s"
                      % (rate_name if not cells else "  -> cells",
                         "%dK" % (sb >> 10), worst, na, AUDIO_FRAMES, nv,
                         "frames"))

    print()
    if FAILURES:
        print("FAILURES: %d" % len(FAILURES))
        for f in FAILURES:
            print("  " + f)
        raise SystemExit(1)
    print("format: all pass")


if __name__ == "__main__":
    main()
