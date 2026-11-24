# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
#
# ribbit_claim_store.py -- WorldShepherd SARA's shared ticket-claim store, as memory. [A_TICKET_IS_ITS_CELLS_V1]
#
# It is a SharedClaimStore (worldshepherd_sara/connector_ticket_shared.py): register / claim / status, the contract
# Brandon's PostgresClaimStore and ThreadSafeSharedClaimStore implement. Nothing here asks anything of anyone. A SARA
# writes its own cell and reads the ticket's cells; what is true of the ticket is what those cells say, and every
# reader derives the same thing from them. The RAM manager runs no operation of WorldShepherd's and keeps no file.
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
# [THE_RAM_IS_A_SERVICE_V1] WorldShepherd's memory is an app's RAM like any other on FrogNet: the app "worldshepherd",
# its one RAM "general" (fdf-app config worldshepherd general). fdf-app serves it with fdf-ram on the machine the
# election names, on the port in the app's one configuration tuple, and holds its listening socket. A SARA finds it
# where it is now (fdf_appram.where: the AppHost line in /etc/hosts and that port). There is no WorldShepherd
# executable. A cell stays for the life of that memory, not of its writer's session.
#
# [A_SARA_REASSERTS_ITS_OWN_V1] The operating rule (opt/fdf/docs/APP-SETUP-AND-OPERATION.md): find the current RAM,
# reassert your own truth, then learn the world again. A SARA owns two kinds of truth and keeps both for as long as
# it runs: the tickets it issued, and the claims it won. It holds one read, on the SARAs' family (worldshepherd, sara, *):
# each SARA's own cell there says it is here and goes with its session. When that read ends the
# memory it was reading is gone (its AppHost moved, or its fdf-ram ended). Then this SARA's one attaching thread
#     finds the RAM where it is now -- and when nothing answers there yet, holds a read on what fdf-app says it
#       serves (fdf-app, serving, worldshepherd.general.*) in the SystemRam, which also ends when /etc/hosts is written,
#     writes its issues and its won claims into that memory (each won claim saying "won", so it stands whichever
#       of the issue and the claim arrives first; a claim that lost is not its writer's truth and is not written),
#     writes its own cell in the SARAs' family after them, and holds a read on that family until every SARA that was
#       in the memory before is in this one ([THEN_LEARN_THE_WORLD_AGAIN_V1]): each has written its own truth first,
#     holds its read again,
# and the SARA's calls proceed: a call that found the memory gone waits for that and is made again in the new one.
# A ticket issued before is claimable after; a ticket consumed before is consumed after; a ticket whose issuer is
# gone is absent, and a claim on it is refused. Only the attaching thread replaces the session, so no lock guards it.
#
# RibbitClaimStore() is a SARA on a node. RibbitClaimStore("HOST:PORT") names one fdf-ram directly, for the simulator
# and the tests; when nothing answers there it says so and its calls raise.
#
# A failure raises (ribbit.RibbitError names the RAM manager, the call and the cause). There is no second way to claim.
import itertools
import os
import secrets
import sys
import threading
import time

_OPT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", ".."))   # /opt on a node
sys.path.insert(0, os.environ.get("FDF_PY") or os.path.join(_OPT, "fdf", "lib", "python"))   # fdfram, fdf_appram: the node's own
sys.path.insert(0, os.environ.get("FROGNET_CORE") or os.path.join(_OPT, "frognet_semantic", "core"))   # frognet_held: held reads at a role
import fdf_appram
import fdfram
import frognet_held
import ribbit
from worldshepherd_sara.connector_ticket_lifecycle import MAX_TTL_SECONDS, TicketClaim
from worldshepherd_sara.connector_ticket_shared import SharedClaimRecord

BUILD = "worldshepherd-ribbit 0.8 [A_TICKET_IS_ITS_CELLS_V1] [I_READ_UNTIL_MY_OWN_CELL_IS_THERE_V1] [A_SARA_REASSERTS_ITS_OWN_V1] [THEN_LEARN_THE_WORLD_AGAIN_V1] [THE_RAM_IS_A_SERVICE_V1]"
APP, RAM = "worldshepherd", "general"
API = fdfram.API
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


class NoRamManager(Exception):
    pass


class RibbitClaimStore:
    def __init__(self, where=None, name="sara"):
        """This store is one SARA: one session, one attaching thread. where: None on a node (the app's RAM, wherever
        it is served now), or one fdf-ram's HOST:PORT."""
        self.fixed = where
        self.who = "%s-%s" % (name, secrets.token_hex(6))     # this participant's own space in every family
        self._n = itertools.count(1)                          # its own cells' numbers; shared with nobody
        self._mine = {}                                       # the truth this SARA owns: (ticket_id, instance) -> bag
        self._saras = set()                                   # the SARAs in the memory this one is on; the attaching thread's
        self.memory = None
        self._attach("opened", time.monotonic())
        threading.Thread(target=self._hold_the_saras, daemon=True, name="worldshepherd-attach-" + self.who).start()

    # ---- the session, and this SARA's own truth in whatever memory answers ------------------------------------
    def _attach(self, why, lost_at):
        """find the RAM, write my own truth into it, and only then let this SARA's calls use it"""
        seen, tried = None, None
        while True:
            if not self.fixed:                                # held: what fdf-app says is served, and /etc/hosts written
                system = fdfram.system_ram().host
                said = frognet_held.wait_change("fdf-app", system, seen, name_like="serving.%s.%s.%%" % (APP, RAM), wait_s=ribbit.HOLD_S)
                seen = frognet_held.signature(said)
            where = self.fixed or fdf_appram.where(APP, RAM)
            if (where, seen) == tried:
                continue                                      # the hold ended with nothing written: held again
            tried = (where, seen)
            try:
                if not where:
                    raise ribbit.RibbitError("%s names no AppHost for %s, or the app has no port for its RAM" % (fdf_appram.HOSTS, APP))
                host, port = where.rsplit(":", 1)
                memory = ribbit.Memory(host, int(port), API)
                break
            except ribbit.RibbitError as e:
                if self.fixed:
                    raise
                print("[WS-ATTACH] %s: nothing answers for %s's RAM at %s (%s); held until fdf-app says it is served or %s is written"
                      % (self.who, APP, where or "no address", str(e).split(": ", 1)[-1], fdf_appram.HOSTS), file=sys.stderr, flush=True)
        memory.replaced = threading.Event()                   # set when this SARA is on the memory after this one
        mine = list(self._mine.items())
        for (ticket_id, instance), bag in mine:
            memory.write(SERVICE, "ticket." + ticket_id, instance, bag)
        memory.write(SERVICE, "sara", self.who, {"build": BUILD, "own": len(mine)}, own=True)   # after them, on the same session: where
        #                                                       this cell is, this SARA's own cells are. It goes with the session.
        there = self._learn_the_saras(memory, self._saras, lost_at)
        self._refused = memory.refused()
        was, self.memory = self.memory, memory                # one store, replaced whole, by this thread only
        print("[WS-ATTACH] %s: %s, %s's RAM at %s (%s); %d of its own cell(s) written into this memory; SARAs there: %d%s"
              % (self.who, BUILD, APP, where, why, len(mine), len(there),
                 "" if self._saras <= there else "; not there, their tickets past their longest life: %s" % sorted(self._saras - there)),
              file=sys.stderr, flush=True)
        self._saras = there
        if was is not None:
            was.replaced.set()                                # this SARA's calls proceed

    def _learn_the_saras(self, memory, before, lost_at):
        """[THEN_LEARN_THE_WORLD_AGAIN_V1] hold a read on the SARAs' family in this memory until every SARA that was in the
        memory before is in this one: each wrote its own tickets and won claims first. A SARA that is not coming is
        waited for no longer than a ticket lives (the contract's MAX_TTL_SECONDS): after that nothing it issued or
        consumed before can be claimed by anyone, so what it would have written decides nothing."""
        cells = memory.read(SERVICE, "sara")
        there = {c["instance"] for c in cells if c["bag"]}
        last = max([c["id"] for c in cells] + [0])
        while not before <= there:
            left = lost_at + MAX_TTL_SECONDS - time.monotonic()
            if left <= 0:
                break
            for c in memory.read(SERVICE, "sara", after=last, wait_s=min(ribbit.HOLD_S, left)):
                last = max(last, c["id"])
                (there.add if c["bag"] else there.discard)(c["instance"])
        self._last = last
        return there

    def _hold_the_saras(self):
        """the one read this SARA holds: the SARAs' family. It says who is here, and it ends when the memory is gone."""
        while True:
            memory, last = self.memory, self._last
            try:
                while True:
                    for c in memory.read(SERVICE, "sara", after=last, wait_s=ribbit.HOLD_S):
                        last = max(last, c["id"])
                        (self._saras.add if c["bag"] else self._saras.discard)(c["instance"])
            except ribbit.RibbitError as gone:
                try:
                    self._attach("the memory it was reading is gone: %s" % str(gone).split(": ", 1)[-1], time.monotonic())
                except ribbit.RibbitError as e:               # a named address with nothing there: said, and this SARA's calls say it too
                    print("[WS-ATTACH] %s: no RAM manager at %s (%s). Its %d own cell(s) are in no memory; nothing more is tried"
                          % (self.who, self.fixed, str(e).split(": ", 1)[-1], len(self._mine)), file=sys.stderr, flush=True)
                    memory.nothing_there = str(e); memory.replaced.set()
                    return

    def _in_the_memory(self, call):
        """make `call(memory)`. If the memory is gone under it, wait for this SARA to be on the next one and make it there."""
        while True:
            memory = self.memory
            try:
                return call(memory)
            except ribbit.RibbitError:
                if "ESTALE" not in str(sys.exc_info()[1]) and memory is self.memory and not self._session_ended(memory):
                    raise                                     # the memory is there and refused this call: the caller's to know
                memory.replaced.wait()
                if getattr(memory, "nothing_there", None):
                    raise NoRamManager("%s: no RAM manager at %s: %s" % (self.who, self.fixed, memory.nothing_there))

    @staticmethod
    def _session_ended(memory):
        try:
            memory.read(SERVICE, "sara", "-")       # any read: a session that has ended says so
            return False
        except ribbit.RibbitError:
            return True

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
        bag = {"sha": ticket_sha256, "exp": float(expires_at)}

        def issue(memory):
            mine = "issue.%s.%d" % (self.who, next(self._n))
            memory.write(SERVICE, "ticket." + ticket_id, mine, bag)
            return mine, self._with_mine(memory, ticket_id, mine)
        mine, t = self._in_the_memory(issue)
        if t.issue["instance"] != mine:
            raise ValueError("duplicate ticket_id")
        self._mine[(ticket_id, mine)] = bag                   # a ticket I issued: mine to write again

    def claim_as_it_stands(self, ticket_id, ticket_sha256, now):
        """(TicketClaim, the Ticket as read with this claim in it)"""
        def claim(memory):
            mine = "claim.%s.%d" % (self.who, next(self._n))
            memory.write(SERVICE, "ticket." + ticket_id, mine, {"sha": ticket_sha256, "at": float(now)})
            return mine, self._with_mine(memory, ticket_id, mine)
        mine, t = self._in_the_memory(claim)
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
        t = Ticket(self._in_the_memory(lambda memory: memory.read(SERVICE, "ticket." + ticket_id)))
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
