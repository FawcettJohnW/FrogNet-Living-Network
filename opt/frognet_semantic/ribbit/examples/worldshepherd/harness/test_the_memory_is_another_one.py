# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
#
# test_the_memory_is_another_one.py -- the RAM manager is killed and another one, empty, answers at its address.
# [A_SARA_REASSERTS_ITS_OWN_V1]: each SARA writes its own truth into the new memory, and nothing else is in it.
#
# The test keeps the listening socket and hands it to each worldshepherd-ram it starts (RIBBIT_LISTEN_FD, the
# platform's [LISTENING_SOCKET_OUTLIVES_THE_HOST_V1]), so a SARA's connect made while there is no RAM manager waits in
# the socket's queue. Nothing here sleeps: the observer holds a read on each ticket until what it expects is there.
#   PYTHONPATH=<example>:<example>/lib:<Sara tree> KARTRAM_LIB=<example>/lib python3 -m pytest -q <this file>
import hashlib, os, signal, socket, subprocess, time

import pytest

import ribbit
import ticket_sql
from ribbit_claim_store import API, SERVICE, RibbitClaimStore, Ticket
from worldshepherd_sara.connector_ticket_postgres import PostgresClaimStore

HERE = os.path.dirname(os.path.abspath(__file__))
RAM = os.path.join(HERE, "..", "bin", "worldshepherd-ram")


def sha(t):
    return hashlib.sha256(t.encode()).hexdigest()


class RamOnAKeptSocket:
    def __init__(self):
        self.sock = socket.socket(); self.sock.bind(("127.0.0.1", 0)); self.sock.listen(64); self.sock.set_inheritable(True)
        self.where = "127.0.0.1:%d" % self.sock.getsockname()[1]; self.proc = None

    def start(self):
        self.proc = subprocess.Popen([RAM, "--listen", self.where], pass_fds=[self.sock.fileno()],
                                     env=dict(os.environ, RIBBIT_LISTEN_FD=str(self.sock.fileno())),
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def kill(self):
        self.proc.send_signal(signal.SIGKILL); self.proc.wait()


def read_until(where, ticket_id, there):
    """a new observer's session: the ticket's cells, held until `there(cells)` is true"""
    host, port = where.rsplit(":", 1); m = ribbit.Memory(host, int(port), API)
    cells = {c["instance"]: c for c in m.read(SERVICE, "ticket." + ticket_id)}
    while not there(cells):
        top = max([c["id"] for c in cells.values()] + [0])
        for c in m.read(SERVICE, "ticket." + ticket_id, after=top, wait_s=ribbit.HOLD_S):
            cells[c["instance"]] = c
    return cells


@pytest.mark.parametrize("kind", ["memory", "sql"])
def test_each_sara_reasserts_its_own_truth_into_the_new_memory(kind):
    make = {"memory": lambda w, n: RibbitClaimStore(w, name=n),
            "sql": lambda w, n: PostgresClaimStore(ticket_sql.connection_factory(w, name=n))}[kind]
    ram = RamOnAKeptSocket(); ram.start()
    try:
        issuer, winner, loser = make(ram.where, "issuer"), make(ram.where, "winner"), make(ram.where, "loser")
        now = time.time(); used, unused = "used-" + os.urandom(6).hex(), "unused-" + os.urandom(6).hex()
        for t in (used, unused):
            issuer.register(ticket_id=t, ticket_sha256=sha(t), expires_at=now + 300)
        assert winner.claim(ticket_id=used, ticket_sha256=sha(used), now=now + 1).ok
        assert loser.claim(ticket_id=used, ticket_sha256=sha(used), now=now + 2).reason == "ticket already consumed"

        ram.kill(); ram.start()                               # that memory is gone; another, empty, answers there

        cells = read_until(ram.where, used, lambda c: any(i.startswith("issue.") for i in c) and any(i.startswith("claim.") for i in c))
        read_until(ram.where, unused, lambda c: any(i.startswith("issue.") for i in c))
        # the new memory holds what their owners wrote again: the issue and the claim that won. The claim that lost is nobody's truth.
        assert sorted(i.split(".")[0] for i in cells) == ["claim", "issue"]
        assert [c["bag"].get("won") for i, c in cells.items() if i.startswith("claim.")] == [1]

        late = make(ram.where, "late")
        assert late.status(used)["consumed"] is True and late.status(used)["consumed_at"] == now + 1
        assert late.claim(ticket_id=used, ticket_sha256=sha(used), now=now + 3).reason == "ticket already consumed"
        got = late.claim(ticket_id=unused, ticket_sha256=sha(unused), now=now + 3)      # issued before, claimable after
        assert got.ok and got.reason == "ticket claimed for one-time host execution"
        assert loser.claim(ticket_id=unused, ticket_sha256=sha(unused), now=now + 4).reason == "ticket already consumed"
    finally:
        ram.kill()


def test_a_won_claim_stands_whichever_of_it_and_the_issue_is_written_first():
    issue = {"instance": "issue.a.1", "bag": {"sha": "s", "exp": 100.0}}
    won = {"instance": "claim.b.1", "bag": {"sha": "s", "at": 5.0, "won": 1}}
    later = {"instance": "claim.c.1", "bag": {"sha": "s", "at": 6.0}}
    for order in ([issue, won, later], [won, issue, later], [won, later, issue], [later, won, issue], [issue, later, won]):
        t = Ticket([dict(c, id=k + 1) for k, c in enumerate(order)])
        assert t.winner["instance"] == "claim.b.1", [c["instance"] for c in order]


def test_a_claim_written_before_the_ticket_does_not_count():
    early = {"id": 1, "instance": "claim.c.1", "bag": {"sha": "s", "at": 5.0}}
    issue = {"id": 2, "instance": "issue.a.1", "bag": {"sha": "s", "exp": 100.0}}
    assert Ticket([early, issue]).winner is None
