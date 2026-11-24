# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
#
# ribbit_claim_store.py -- WorldShepherd SARA's shared ticket-claim store, as memory. [A_TICKET_IS_ITS_CELLS_V1]
#
# It is a SharedClaimStore (worldshepherd_sara/connector_ticket_shared.py): register / claim / status, the contract
# Brandon's PostgresClaimStore and ThreadSafeSharedClaimStore implement. Nothing here asks anything of anyone. A SARA
# writes its own cell and reads the ticket's cells; what is true of the ticket is what those cells say, and every
# reader derives the same thing from them. The RAM manager (worldshepherd_ram.cpp) has no operations and no file.
#
#   one family per ticket: service "worldshepherd", variable "ticket.<ticket_id>"
#     instance "issue.<who>.<n>"   {"sha": <ticket sha256>, "exp": <expires_at>}       written by the issuing SARA
#     instance "claim.<who>.<n>"   {"sha": <the sha256 the claimant holds>, "at": <the claimant's now>}   by a claimant
#                                  with "won": 1 when its writer writes it again as the claim that consumed the ticket
#
#   the ticket   the issue with the lowest id (a later issue of the same ticket_id is a duplicate; its writer is told)
#   the winner   the claim that says "won", if there is one. Otherwise, of the claims written after the ticket that
#                name its sha with an "at" not past its "exp", the one with the lowest id.
#   consumed     there is a winner.  consumed_at: its "at".
#
# The id is the memory's write order in the family: the one place every write to the ticket passes. Nothing is
# locked, compared-and-set, retried or acknowledged, and nobody writes a cell that is not its own: <who> is this
# store's name plus a random token made when it opens, <n> its own count.
#
# [I_READ_UNTIL_MY_OWN_CELL_IS_THERE_V1] A write is posted and not answered. To know how the ticket stands with its
# own cell in it, a SARA reads the family; if its cell is not in what it read, it holds a read past the last id it
# saw, and the write that answers that read is its own or one after which it holds again. When its cell is there,
# every cell written before it is there too, so what it derives cannot change. (The session may answer a read with
# an identical read already in flight, which can predate the post: that read simply does not hold the cell yet.)
#
# [A_SARA_REASSERTS_ITS_OWN_V1] The operating rule (opt/fdf/docs/APP-SETUP-AND-OPERATION.md): find the current RAM,
# reassert your own truth, then learn the world again. A SARA owns two kinds of truth and keeps both for as long as
# it runs: the tickets it issued, and the claims it won. It holds one read, on its own cell. When that read ends
# because the memory is gone, this SARA's one attaching thread opens a session to the RAM manager at its address and
# writes its issues and its won claims into the memory that answers -- each won claim saying "won", so it stands
# whichever of the issue and the claim arrives first. A claim that lost is not its writer's truth and is not written
# again. A ticket issued before the memory was lost is claimable in the new one; a ticket consumed before is consumed
# in it. A ticket whose issuer is gone is absent, and a claim on it is refused.
#   What this does not close: after its issuer has reasserted a consumed ticket and before its winner has, the
#   ticket reads as not yet consumed, and a claim made in that interval wins it.
# A call that finds the memory gone raises: nothing here retries it, and what to do is the caller's. Only the
# attaching thread replaces the session, so no lock guards it.
#
# A failure raises (ribbit.RibbitError names the RAM manager, the call and the cause). There is no second way to claim.
import itertools
import secrets
import sys
import threading

import ribbit
from worldshepherd_sara.connector_ticket_lifecycle import TicketClaim
from worldshepherd_sara.connector_ticket_shared import SharedClaimRecord

BUILD = "worldshepherd-ribbit 0.7 [A_TICKET_IS_ITS_CELLS_V1] [I_READ_UNTIL_MY_OWN_CELL_IS_THERE_V1] [A_SARA_REASSERTS_ITS_OWN_V1]"
API = "/worldshepherd-api"
SERVICE = "worldshepherd"


class Ticket:
    """how a ticket stands, derived from its family's cells"""
    __slots__ = ("issue", "winner", "ids")

    def __init__(self, cells):
        cells = [c for c in cells if c["bag"]]                 # a cell with no bag is gone
        self.ids = {c["instance"]: c["id"] for c in cells}
        issues = [c for c in cells if c["instance"].startswith("issue.")]
        claims = [c for c in cells if c["instance"].startswith("claim.")]
        self.issue = min(issues, key=lambda c: c["id"]) if issues else None
        won = [c for c in claims if c["bag"].get("won")]
        if self.issue is not None and not won:
            won = [c for c in claims if c["id"] > self.issue["id"] and c["bag"]["sha"] == self.issue["bag"]["sha"]
                   and float(c["bag"]["at"]) <= float(self.issue["bag"]["exp"])]
        self.winner = min(won, key=lambda c: c["id"]) if won else None


class RibbitClaimStore:
    def __init__(self, where, name="sara"):
        """where: worldshepherd-ram's HOST:PORT. This store is one SARA: one session, one attaching thread."""
        self.where = where
        self.who = "%s-%s" % (name, secrets.token_hex(6))     # this participant's own space in every family
        self._n = itertools.count(1)                          # its own cells' numbers; shared with nobody
        self._mine = {}                                       # the truth this SARA owns: (ticket_id, instance) -> bag
        self.memory = None
        self._attach("opened")
        threading.Thread(target=self._hold_my_own_cell, daemon=True, name="worldshepherd-attach-" + self.who).start()

    # ---- the session, and this SARA's own truth in whatever memory answers ------------------------------------
    def _attach(self, why):
        host, port = self.where.rsplit(":", 1)
        memory = ribbit.Memory(host, int(port), API)          # waits in the socket's queue when the socket outlives the host
        memory.write(SERVICE, "sara." + self.who, "here", {"build": BUILD}, own=True)   # goes with this session
        mine = list(self._mine.items())
        for (ticket_id, instance), bag in mine:
            memory.write(SERVICE, "ticket." + ticket_id, instance, bag)
        self._refused = memory.refused()
        self.memory = memory                                  # one store, replaced whole, by this thread only
        print("[WS-ATTACH] %s: %s, RAM manager %s%s (%s); %d of its own cell(s) written into this memory"
              % (self.who, BUILD, self.where, API, why, len(mine)), file=sys.stderr, flush=True)

    def _hold_my_own_cell(self):
        """one read held on this SARA's own cell. It ends when the memory is gone; that is the event."""
        while True:
            memory = self.memory
            try:
                last = max([c["id"] for c in memory.read(SERVICE, "sara." + self.who)] + [0])
                while True:
                    for c in memory.read(SERVICE, "sara." + self.who, after=last, wait_s=ribbit.HOLD_S):
                        last = max(last, c["id"])
            except ribbit.RibbitError as gone:
                try:
                    self._attach("the memory it was reading is gone: %s" % str(gone).split(": ", 1)[-1])
                except ribbit.RibbitError as e:               # no RAM manager answers there: said, and this SARA's calls say it too
                    print("[WS-ATTACH] %s: no RAM manager at %s (%s). Its %d own cell(s) are in no memory; nothing more is tried"
                          % (self.who, self.where, str(e).split(": ", 1)[-1], len(self._mine)), file=sys.stderr, flush=True)
                    return

    # ---- a ticket -------------------------------------------------------------------------------------------
    def _with_mine(self, memory, ticket_id, mine):
        """the ticket's cells, read until my own cell `mine` is among them"""
        family = "ticket." + ticket_id
        cells = {c["instance"]: c for c in memory.read(SERVICE, family)}
        while mine not in cells:
            if memory.refused() != self._refused:             # a posted write the RAM manager would not take
                raise RuntimeError("%s refused a write of this session (%d so far): %s of ticket %s is not in the memory"
                                   % (memory.where, memory.refused(), mine, ticket_id))
            top = max([c["id"] for c in cells.values()] + [0])
            for c in memory.read(SERVICE, family, after=top, wait_s=ribbit.HOLD_S):
                cells[c["instance"]] = c
        return Ticket(cells.values())

    def register(self, *, ticket_id, ticket_sha256, expires_at):
        memory = self.memory; mine = "issue.%s.%d" % (self.who, next(self._n))
        bag = {"sha": ticket_sha256, "exp": float(expires_at)}
        memory.write(SERVICE, "ticket." + ticket_id, mine, bag)
        if self._with_mine(memory, ticket_id, mine).issue["instance"] != mine:
            raise ValueError("duplicate ticket_id")
        self._mine[(ticket_id, mine)] = bag                   # a ticket I issued: mine to write again

    def claim_as_it_stands(self, ticket_id, ticket_sha256, now):
        """(TicketClaim, the Ticket as read with this claim in it)"""
        memory = self.memory; mine = "claim.%s.%d" % (self.who, next(self._n))
        memory.write(SERVICE, "ticket." + ticket_id, mine, {"sha": ticket_sha256, "at": float(now)})
        t = self._with_mine(memory, ticket_id, mine)
        if t.issue is None or (t.ids[mine] < t.issue["id"] and not (t.winner and t.winner["bag"].get("won"))):
            return TicketClaim(False, ticket_id, "ticket not registered in this ledger"), t   # no ticket when I claimed
        if t.issue["bag"]["sha"] != ticket_sha256:
            return TicketClaim(False, ticket_id, "ticket digest does not match registered ticket"), t
        if float(now) > float(t.issue["bag"]["exp"]):
            return TicketClaim(False, ticket_id, "ticket expired"), t
        if t.winner is None or t.winner["instance"] != mine:
            return TicketClaim(False, ticket_id, "ticket already consumed"), t
        self._mine[(ticket_id, mine)] = {"sha": ticket_sha256, "at": float(now), "won": 1}   # a claim I won: mine to write again
        return TicketClaim(True, ticket_id, "ticket claimed for one-time host execution", float(now)), t

    def claim(self, *, ticket_id, ticket_sha256, now):
        return self.claim_as_it_stands(ticket_id, ticket_sha256, now)[0]

    def status(self, ticket_id):
        t = Ticket(self.memory.read(SERVICE, "ticket." + ticket_id))
        if t.issue is None:
            return {"known": False, "ticket_id": ticket_id}
        return {"known": True, "ticket_id": ticket_id, "ticket_sha256": t.issue["bag"]["sha"],
                "expires_at": float(t.issue["bag"]["exp"]), "consumed": t.winner is not None,
                "consumed_at": None if t.winner is None else float(t.winner["bag"]["at"])}

    def record(self, ticket_id):
        """the ticket as the SharedClaimRecord the contract names: derived from the cells, kept nowhere"""
        s = self.status(ticket_id)
        if not s["known"]:
            raise KeyError(ticket_id)
        return SharedClaimRecord(ticket_sha256=s["ticket_sha256"], expires_at=s["expires_at"],
                                 consumed_at=s["consumed_at"])
