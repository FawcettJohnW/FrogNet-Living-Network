# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
#
# on_the_memory.py -- a pytest plugin that runs Brandon's own connector-ticket tests, unmodified, against the memory.
#
#   WS_RAM=host:port PYTHONPATH=<this dir>:<the example dir>:<ribbit.py's dir>:<Sara tree> \
#       python -m pytest -p on_the_memory <Sara tree>/tests/test_connector_ticket_shared.py ...
#
# The tests build their store by the name ThreadSafeSharedClaimStore. Here that name makes a RibbitClaimStore on one
# session to the RAM manager at WS_RAM, so every test that used the in-process reference store now uses the memory.
# One test reads the reference store's private dict (store._records[ticket_id]); the view below answers it with the
# record derived from the cells, which is what that test is about (only the three claim fields are kept).
# WS_RAM unset is an error: a run that silently used the in-process store would be a green that proves nothing.
import os

import ribbit
import worldshepherd_sara.connector_ticket_shared as shared
from ribbit_claim_store import BUILD, RibbitClaimStore

from ribbit_claim_store import API
if not os.environ.get("WS_RAM"):  # worldshepherd-ram, HOST:PORT
    raise RuntimeError("on_the_memory: WS_RAM=host:port is not set (the RAM manager these tests run against)")
_host, _port = os.environ["WS_RAM"].rsplit(":", 1)
_memory = ribbit.Memory(_host, int(_port), API)
print("on_the_memory: %s, RAM manager %s%s" % (BUILD, os.environ["WS_RAM"], API))


class _Records:
    def __init__(self, store):
        self.store = store

    def __getitem__(self, ticket_id):
        return self.store.record(ticket_id)


class OnTheMemory(RibbitClaimStore):
    made = 0

    def __init__(self):
        super().__init__(_memory, name="test")
        OnTheMemory.made += 1
        self._records = _Records(self)


shared.ThreadSafeSharedClaimStore = OnTheMemory


def pytest_terminal_summary(terminalreporter):
    terminalreporter.write_line("on_the_memory: %d store(s) made on the memory at %s" % (OnTheMemory.made, os.environ["WS_RAM"]))
