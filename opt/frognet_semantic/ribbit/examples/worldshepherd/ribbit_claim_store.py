# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
#
# ribbit_claim_store.py -- WorldShepherd SARA's shared ticket-claim store, as memory. [A_TICKET_IS_ITS_CELLS_V1]
#
# It is a SharedClaimStore (worldshepherd_sara/connector_ticket_shared.py): register / claim / status, the contract
# Brandon's PostgresClaimStore and ThreadSafeSharedClaimStore implement. Nothing here asks anything of anyone. A SARA
# writes its own cell, once, and reads the ticket's cells; what is true of the ticket is what those cells say, and
# every reader derives the same thing from them. The RAM manager (worldshepherd_ram.cpp) has no operations.
#
#   one family per ticket: service "worldshepherd", variable "ticket.<ticket_id>"
#     instance "issue.<who>.<n>"   {"sha": <ticket sha256>, "exp": <expires_at>}       written by the issuing SARA
#     instance "claim.<who>.<n>"   {"sha": <the sha256 the claimant holds>, "at": <the claimant's now>}   by a claimant
#
#   the ticket   the issue with the lowest id (a later issue of the same ticket_id is a duplicate; its writer is told)
#   a claim counts   when it was written after the ticket, names the ticket's sha, and its "at" is not past "exp"
#   the winner   the claim that counts with the lowest id.  consumed: there is one.  consumed_at: its "at".
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
# Cells are Permanent: the RAM manager files each before publishing it, and writes them back in the order filed.
# After a restart: two cells of one family that were in flight together can come back with their ids exchanged. A
# ticket that was consumed is still consumed and no claim made after the restart can win it. If the exchanged pair
# were two claims that both counted, status() can report the other one's "at". If they were two issues of one
# ticket_id, the other issue is the ticket afterwards, and claims holding the first one's sha are refused.
#
# A failure raises (ribbit.RibbitError names the RAM manager, the call and the cause). There is no second way to claim.
import itertools
import secrets

import ribbit
from worldshepherd_sara.connector_ticket_lifecycle import TicketClaim
from worldshepherd_sara.connector_ticket_shared import SharedClaimRecord

BUILD = "worldshepherd-ribbit 0.6 [A_TICKET_IS_ITS_CELLS_V1] [I_READ_UNTIL_MY_OWN_CELL_IS_THERE_V1]"
API = "/worldshepherd-api"
SERVICE = "worldshepherd"


class Ticket:
    """how a ticket stands, derived from its family's cells"""
    __slots__ = ("issue", "winner", "ids")

    def __init__(self, cells):
        cells = [c for c in cells if c["bag"]]                 # a cell with no bag is gone
        self.ids = {c["instance"]: c["id"] for c in cells}
        issues = [c for c in cells if c["instance"].startswith("issue.")]
        self.issue = min(issues, key=lambda c: c["id"]) if issues else None
        self.winner = None
        if self.issue is not None:
            counts = [c for c in cells if c["instance"].startswith("claim.") and c["id"] > self.issue["id"]
                      and c["bag"]["sha"] == self.issue["bag"]["sha"] and float(c["bag"]["at"]) <= float(self.issue["bag"]["exp"])]
            self.winner = min(counts, key=lambda c: c["id"]) if counts else None


class RibbitClaimStore:
    def __init__(self, memory, name="sara"):
        self.memory = memory                                  # ribbit.Memory: this participant's one session
        self.who = "%s-%s" % (name, secrets.token_hex(6))     # this participant's own space in every family
        self._n = itertools.count(1)                          # its own cells' numbers; shared with nobody
        self._refused = memory.refused()                      # posts refused before this store opened: not its own

    def _with_mine(self, ticket_id, mine):
        """the ticket's cells, read until my own cell `mine` is among them"""
        family = "ticket." + ticket_id
        cells = {c["instance"]: c for c in self.memory.read(SERVICE, family)}
        while mine not in cells:
            if self.memory.refused() != self._refused:        # a posted write the RAM manager would not take
                raise RuntimeError("%s refused a write of this session (%d so far): %s of ticket %s is not in the memory"
                                   % (self.memory.where, self.memory.refused(), mine, ticket_id))
            top = max([c["id"] for c in cells.values()] + [0])
            for c in self.memory.read(SERVICE, family, after=top, wait_s=ribbit.HOLD_S):
                cells[c["instance"]] = c
        return Ticket(cells.values())

    def register(self, *, ticket_id, ticket_sha256, expires_at):
        mine = "issue.%s.%d" % (self.who, next(self._n))
        self.memory.write(SERVICE, "ticket." + ticket_id, mine, {"sha": ticket_sha256, "exp": float(expires_at)}, permanent=True)
        if self._with_mine(ticket_id, mine).issue["instance"] != mine:
            raise ValueError("duplicate ticket_id")

    def claim_as_it_stands(self, ticket_id, ticket_sha256, now):
        """(TicketClaim, the Ticket as read with this claim in it)"""
        mine = "claim.%s.%d" % (self.who, next(self._n))
        self.memory.write(SERVICE, "ticket." + ticket_id, mine, {"sha": ticket_sha256, "at": float(now)}, permanent=True)
        t = self._with_mine(ticket_id, mine)
        if t.issue is None or t.ids[mine] < t.issue["id"]:       # no ticket, or none yet when my claim was written
            return TicketClaim(False, ticket_id, "ticket not registered in this ledger"), t
        if t.issue["bag"]["sha"] != ticket_sha256:
            return TicketClaim(False, ticket_id, "ticket digest does not match registered ticket"), t
        if float(now) > float(t.issue["bag"]["exp"]):
            return TicketClaim(False, ticket_id, "ticket expired"), t
        if t.winner["instance"] != mine:
            return TicketClaim(False, ticket_id, "ticket already consumed"), t
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
