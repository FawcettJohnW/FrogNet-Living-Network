# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
#
# test_one_session_many_threads.py -- many threads of ONE process, on its one session, claiming the same tickets.
# The session answers an identical request already in flight with that request's one answer ([CLIENT_COALESCES_V1]),
# so a claimant's read after its own post must carry its own value, or it can be handed an answer from before its
# post. Brandon's own test of this (16 threads, 64 claims, one ticket) hits that about one run in six; this one
# makes it certain: 300 tickets, each claimed by 16 threads at once.
# Run for both stores on the memory: RibbitClaimStore, and Brandon's PostgresClaimStore on ticket_sql.py.
#   WS_RAM=host:port python3 -m pytest -q test_one_session_many_threads.py
import hashlib, os, secrets, threading

import pytest

import ribbit
from ribbit_claim_store import RibbitClaimStore

from ribbit_claim_store import API


def _stores():
    import ticket_sql
    from worldshepherd_sara.connector_ticket_postgres import PostgresClaimStore
    return {"memory": lambda m: RibbitClaimStore(m, name="threads"),
            "sql": lambda m: PostgresClaimStore(ticket_sql.connection_factory(m, name="threads"))}


@pytest.mark.parametrize("kind", ["memory", "sql"])
def test_every_ticket_is_won_exactly_once_by_threads_sharing_a_session(kind):
    host, port = os.environ["WS_RAM"].rsplit(":", 1)
    store = _stores()[kind](ribbit.Memory(host, int(port), API))
    run = secrets.token_hex(4); tickets = ["%s-%04d" % (run, k) for k in range(300)]
    for t in tickets:
        store.register(ticket_id=t, ticket_sha256=hashlib.sha256(t.encode()).hexdigest(), expires_at=2000.0)
    wins = {t: 0 for t in tickets}; errors = []; start = threading.Barrier(16)

    def claimant():
        start.wait()
        for t in tickets:
            try:
                if store.claim(ticket_id=t, ticket_sha256=hashlib.sha256(t.encode()).hexdigest(), now=1000.0).ok:
                    wins[t] += 1                     # each ticket's count is only ever raised by its one winner
            except Exception as e:                   # noqa: BLE001 -- collected and asserted on, not swallowed
                errors.append(repr(e))
    threads = [threading.Thread(target=claimant) for _ in range(16)]
    for th in threads: th.start()
    for th in threads: th.join()
    assert errors == []
    assert [t for t, n in wins.items() if n != 1] == []
