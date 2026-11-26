# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
"""[THE_MEMORY_MOVES_V1] Where the Communicator's memory (comms-ram) is, for this process: src/comms/ram_at.hpp.

A program is started with the memory's address. The Role's setting may name another one: its owner moved the memory.
The process's read on the setting fires, the address here changes, and everything in the process that holds a session
to the memory is told: it ends that session, attaches to the new address, asserts its own tuples there and sets up
its held reads again. A call's audio and video go to the media server, not to the memory, so they are not touched."""
import threading

_mu = threading.Lock()              # this process's copy of the address and its list of who to tell
_at = None
_moves = 0
_told = {}
_next = 0


def start(host, port):
    """What the program was started with. The first call says where the memory is; later ones change nothing."""
    global _at
    with _mu:
        if _at is None:
            _at = (host, int(port))


def at():
    with _mu:
        return _at


def moves():
    with _mu:
        return _moves


def move(host, port):
    """The memory is at another address now. False when it is the address this process already has."""
    global _at, _moves
    with _mu:
        if _at == (host, int(port)):
            return False
        _at = (host, int(port))
        _moves += 1
        tell = list(_told.values())
    for fn in tell:                 # each ends its own session; none is called under the guard
        fn()
    return True


def when_moved(fn):
    global _next
    with _mu:
        _next += 1
        _told[_next] = fn
        return _next


def forget(i):
    with _mu:
        _told.pop(i, None)
