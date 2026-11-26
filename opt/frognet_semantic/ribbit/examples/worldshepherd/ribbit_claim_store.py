# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
#
# ribbit_claim_store.py -- WorldShepherd SARA's shared ticket-claim store, as memory. [A_TICKET_IS_ONE_RECORD_V1]
#
# It is a SharedClaimStore (worldshepherd_sara/connector_ticket_shared.py): register / claim / status, the contract
# Brandon's PostgresClaimStore and ThreadSafeSharedClaimStore implement.
#
# A ticket is one record in WorldShepherd's RAM host (worldshepherd_ram.cpp), which says how it works. Its two
# methods are that host's operations, run in the memory itself; each is one call:
#   register   op=issue {"who","ticket","sha","exp","n"} -> {"state"}. The state names my issue ("by", "bn"), or it
#              is another's ticket: a duplicate.
#   claim      op=claim {"who","ticket","sha","at","n"[,"held":1]} -> {"outcome", "state"}. The outcome: claimed,
#              consumed, digest, expired or absent.
#   status     two reads of one cell each: (worldshepherd, ticket, <ticket>), then (worldshepherd, won.<its issuer>, <ticket>).
# [EACH_FACT_ITS_OWN_CELL_V1] The ticket writes each of its two facts once, in its own cell. The issuer holds a read on
# won.<itself>, sees each of its tickets won, and says so when it makes them again in a new memory
# ([THE_ISSUER_SAYS_WHO_WON_V1]).
# The only cell this SARA writes itself is (worldshepherd, sara, <who>): it is here; it goes with its session.
#
# <who> is this store's name plus a random token made when it opens; <n> is the number of the call. A call that is
# made again -- its session ended under it -- carries the same <n>, and the ticket answers it as it answered before.
#
# [THE_RAM_IS_A_SERVICE_V1] WorldShepherd's memory is an app's RAM like any other on FrogNet: the app "worldshepherd",
# its one RAM "general" (fdf-app config worldshepherd general --host general=worldshepherd-ram). fdf-app serves it with
# the app's own RAM host (worldshepherd_ram.cpp: the platform's, with WorldShepherd's rules about its writes) on the machine the
# election names, on the port in the app's one configuration tuple, and holds its listening socket. A SARA finds it
# where it is now (fdf_appram.where: the AppHost line in /etc/hosts and that port). The RAM host runs no ticket
# operation. A cell stays for the life of that memory, not of its writer's session.
#
# [A_SARA_REASSERTS_ITS_OWN_V1] The operating rule (opt/fdf/docs/APP-SETUP-AND-OPERATION.md): find the current RAM,
# reassert your own truth, then learn the world again. A SARA owns two kinds of truth and keeps both for as long as
# it runs: the tickets it issued, and the claims it won (its own space, as it stands). It holds one read, on the SARAs' family (worldshepherd, sara, *):
# each SARA's own cell there says it is here and goes with its session. When that read ends the
# memory it was reading is gone (its AppHost moved, or its RAM host ended). Then this SARA's one attaching thread
#     finds the RAM where it is now -- and when nothing answers there yet, holds a read on what fdf-app says it
#       serves (fdf-app, serving, worldshepherd.general.*) in the SystemRam, which also ends when /etc/hosts is written,
#     writes its issues and its won claims into that memory (each won claim saying "held", so the ticket takes it
#       whichever of the issue and the claim arrives first; a claim that lost was withdrawn and is not written),
#     writes its own cell in the SARAs' family after them, and holds a read on that family until every SARA that was
#       in the memory before is in this one ([THEN_LEARN_THE_WORLD_AGAIN_V1]): each has written its own truth first,
#     holds its read again,
# and the SARA's calls proceed: a call that found the memory gone waits for that and is made again in the new one.
# A ticket issued before is claimable after; a ticket consumed before is consumed after; a ticket whose issuer is
# gone is absent, and a claim on it is refused. Only the attaching thread replaces the session, so no lock guards it.
#
# RibbitClaimStore() is a SARA on a node. RibbitClaimStore("HOST:PORT") names one RAM host directly, for the simulator
# and the tests; when nothing answers there it says so and its calls raise.
#
# A failure raises (ribbit.RibbitError names the RAM manager, the call and the cause). There is no second way to claim.
import itertools
import json
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

BUILD = "worldshepherd-ribbit 0.11 2026-11-26j [A_TICKET_IS_ONE_RECORD_V1] [A_SARA_BRINGS_ITS_OWN_BACK_IN_ONE_CALL_V1] [THE_ISSUER_SAYS_WHO_WON_V1] [A_SESSION_I_OPENED_AND_DROP_IS_CLOSED_V1] [AN_ANSWER_THAT_REFUSES_IS_WAITED_ON_V1] [A_SARA_WRITES_ONLY_ITS_OWN_SPACE_V1] [A_SARA_REASSERTS_ITS_OWN_V1] [THEN_LEARN_THE_WORLD_AGAIN_V1] [THE_RAM_IS_A_SERVICE_V1] [A_MEMORY_GONE_WHILE_I_WRITE_MY_OWN_V1] [A_CALL_HAS_ONE_CELL_V1] [A_HELD_CLAIM_AGES_V1]"
APP, RAM = "worldshepherd", "general"
API = fdfram.API
SERVICE = "worldshepherd"


class NoRamManager(Exception):
    pass


class RibbitClaimStore:
    def __init__(self, where=None, name="sara"):
        """This store is one SARA: one session, one attaching thread. where: None on a node (the app's RAM, wherever
        it is served now), or one RAM host's HOST:PORT."""
        self.fixed = where
        self.who = "%s-%s" % (name, secrets.token_hex(6))     # this participant's own space in every family
        self._n = itertools.count(1)                          # its own cells' numbers; shared with nobody
        self._mine = {}                                       # the truth this SARA owns: ("issue" | "claim", ticket_id) -> the call that wrote it
        self._watching = threading.Lock()                     # taken once: this SARA holds a read on its own tickets
        self._saras = set()                                   # the SARAs in the memory this one is on; the attaching thread's
        self._left = {}                                       # a SARA that left it, and when (this clock): [A_SARA_THAT_LEFT_IS_WAITED_FOR_V1]
        self.memory = None
        self._attach("opened", time.monotonic())
        threading.Thread(target=self._hold_the_saras, daemon=True, name="worldshepherd-attach-" + self.who).start()

    # ---- the session, and this SARA's own truth in whatever memory answers ------------------------------------
    def _attach_once(self, why, lost_at):
        """one try: (the memory, where, my own, the SARAs there, those absent, why) -- the memory None when it is to be tried again"""
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
                if self.fixed and "far end closed" in str(e):  # [A_MEMORY_GONE_WHILE_I_WRITE_MY_OWN_V1] something answered there and
                    tried = None                              # went as the session opened: a memory that is gone, not an address
                    continue                                  # with nothing at it. The next connect is to whatever is there next
                if self.fixed:
                    raise
                print(time.strftime("%H:%M:%S ") + "[WS-ATTACH] %s: nothing answers for %s's RAM at %s (%s); held until fdf-app says it is served or %s is written"
                      % (self.who, APP, where or "no address", str(e).split(": ", 1)[-1], fdf_appram.HOSTS), file=sys.stderr, flush=True)
        memory.replaced = threading.Event()                   # set when this SARA is on the memory after this one
        mine = list(self._mine.items())
        try:
            if mine:                                          # [A_SARA_BRINGS_ITS_OWN_BACK_IN_ONE_CALL_V1] all of it, in one call:
                # a line for this SARA, then a line for each issue and each won claim (issues first). Lines, not one
                # JSON document with lists in it: the wire keeps a list out of line, in a file on the RAM host's disk.
                r = memory.call("POST", memory.api + "?op=again", "\n".join(
                    [json.dumps({"who": self.who})] + [json.dumps(b) for (op, _), b in mine if op == "issue"]
                    + [json.dumps(b) for (op, _), b in mine if op == "claim"]))
                if not r.get("ok"):
                    raise RuntimeError("%s refused this SARA's own issues and claims: %s" % (memory.where, r.get("error", r)))
            memory.write(SERVICE, "sara", self.who, {"build": BUILD, "own": len(mine)}, own=True)   # after them, on the same session: where
            #                                                   this cell is, this SARA's own cells are. It goes with the session.
            there, absent = self._learn_the_saras(memory, lost_at)
        except ribbit.RibbitError as e:
            # [A_MEMORY_GONE_WHILE_I_WRITE_MY_OWN_V1] (the acceptance study, restart:reassertion, 2026-11-25: every SARA ended
            # here with "nothing more is tried") the memory this SARA was writing its own truth into is gone too. Nothing
            # of that attempt stands; it is done again, whole, in the memory that answers next.
            print(time.strftime("%H:%M:%S ") + "[WS-ATTACH] %s: the memory at %s went while this SARA was writing its own truth into it (%s); done again in the next one"
                  % (self.who, where, str(e).split(": ", 1)[-1]), file=sys.stderr, flush=True)
            # [A_SESSION_I_OPENED_AND_DROP_IS_CLOSED_V1] (the acceptance study on 0.11, 2026-11-25: this session was left open,
            # so the host went on seeing this SARA's name as that session's, "which is up", and refused every session
            # this SARA opened after it: it tried 290 times in 10 s and never got in.)
            # [AN_ANSWER_THAT_REFUSES_IS_WAITED_ON_V1] (the app-host test on 0.11, 2026-11-25: a RAM host that answered every
            # try with the same error was tried again at once, without end, until Python ran out of stack.) A memory
            # that is gone is tried again at once: what is there next is another memory. A memory that ANSWERED and
            # refused is still there, so this SARA holds a read on the SARAs present until that family changes -- the
            # session its name belongs to going is such a change -- and tries then.
            if "HTTP " in str(e) or "ERROR" in str(e):
                try:
                    last = max([c["id"] for c in memory.read(SERVICE, "sara")] or [0])
                    memory.read(SERVICE, "sara", "", after=last, wait_s=ribbit.HOLD_S)
                except ribbit.RibbitError:
                    pass
            try: memory.close()
            except Exception: pass
            return None, where, mine, None, None, "%s; and the memory after it went during the writing" % why
        return memory, where, mine, there, absent, why

    def _attach(self, why, lost_at):
        """find the RAM, write my own truth into it, and only then let this SARA's calls use it"""
        while True:
            memory, where, mine, there, absent, why = self._attach_once(why, lost_at)
            if memory is not None:
                break
        self._refused = memory.refused()
        was, self.memory = self.memory, memory                # one store, replaced whole, by this thread only
        print(time.strftime("%H:%M:%S ") + "[WS-ATTACH] %s: %s, %s's RAM at %s (%s); %d of its own cell(s) written into this memory; SARAs there: %d%s"
              % (self.who, BUILD, APP, where, why, len(mine), len(there),
                 "" if not absent else "; not there, their tickets past their longest life: %s" % sorted(absent)),
              file=sys.stderr, flush=True)
        self._saras = there
        if was is not None:
            was.replaced.set()                                # this SARA's calls proceed

    def _seen(self, cells, there):
        """the SARAs' family, as read: who is in this memory, and who has left it and when"""
        for c in cells:
            if c["bag"]:
                there.add(c["instance"]); self._left.pop(c["instance"], None)
            elif c["instance"] in there:
                there.discard(c["instance"]); self._left[c["instance"]] = time.monotonic()

    def _learn_the_saras(self, memory, lost_at):
        """[THEN_LEARN_THE_WORLD_AGAIN_V1] hold a read on the SARAs' family in this memory until every SARA that may hold
        truth about a live ticket is in this one: each wrote its own tickets and won claims first. That is every SARA
        that was in the memory when it was lost, and
        [A_SARA_THAT_LEFT_IS_WAITED_FOR_V1] (the acceptance study, split:winner+restart, 2026-11-25: 240 tickets were won
        twice) every SARA that LEFT it less than a ticket's life before. A SARA whose link went down a moment before the
        memory was replaced was not in it at the end, and still holds the claims it won; nobody waited for it, and
        its tickets were claimed again in the new memory.
        Each is waited for no longer than a ticket lives (the contract's MAX_TTL_SECONDS) from when it was last in the
        memory: after that nothing it issued or consumed can be claimed by anyone, so what it would have written
        decides nothing. Returns (who is here, who was waited for and is not)."""
        now = time.monotonic()
        self._left = {w: t for w, t in self._left.items() if t + MAX_TTL_SECONDS > now}
        until = {w: lost_at + MAX_TTL_SECONDS for w in self._saras}
        until.update({w: t + MAX_TTL_SECONDS for w, t in self._left.items()})
        left_before = dict(self._left)                        # _seen forgets one that is here again
        cells = memory.read(SERVICE, "sara")
        there = set(); self._seen([c for c in cells if c["bag"]], there)
        last = max([c["id"] for c in cells] + [0])
        while True:
            waiting = {w: t - time.monotonic() for w, t in until.items() if w not in there and t > time.monotonic()}
            if not waiting:
                break
            cells = memory.read(SERVICE, "sara", after=last, wait_s=min(ribbit.HOLD_S, max(waiting.values())))
            last = max([last] + [c["id"] for c in cells])
            self._seen(cells, there)
        self._last = last
        absent = {w for w in until if w not in there}
        self._left = {w: t for w, t in left_before.items() if w in absent and t + MAX_TTL_SECONDS > time.monotonic()}
        return there, absent

    def _hold_the_saras(self):
        """the one read this SARA holds: the SARAs' family. It says who is here, and it ends when the memory is gone."""
        while True:
            memory, last = self.memory, self._last
            try:
                while True:
                    for c in memory.read(SERVICE, "sara", after=last, wait_s=ribbit.HOLD_S):
                        last = max(last, c["id"])
                        self._seen([c], self._saras)
            except ribbit.RibbitError as gone:
                try:
                    self._attach("the memory it was reading is gone: %s" % str(gone).split(": ", 1)[-1], time.monotonic())
                except ribbit.RibbitError as e:               # a named address with nothing there: said, and this SARA's calls say it too
                    print(time.strftime("%H:%M:%S ") + "[WS-ATTACH] %s: no RAM manager at %s (%s). Its %d own cell(s) are in no memory; nothing more is tried"
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
    @staticmethod
    def _ticket(memory, op, body):
        """one of the ticket's two operations, in the memory itself: {"state": ...} and, for a claim, {"outcome": ...}"""
        r = memory.call("POST", memory.api + "?op=" + op, json.dumps(body))
        if not r.get("ok"):
            raise RuntimeError("%s refused %s of %s: %s" % (memory.where, op, body.get("ticket", "this SARA's own"), r.get("error", r)))
        return r

    def register(self, *, ticket_id, ticket_sha256, expires_at):
        mine = ("issue", ticket_id)
        if mine in self._mine:
            raise ValueError("duplicate ticket_id")
        body = {"who": self.who, "ticket": ticket_id, "sha": ticket_sha256, "exp": float(expires_at), "n": next(self._n)}
        state = self._in_the_memory(lambda memory: self._ticket(memory, "issue", body))["state"]
        if state is None or state["by"] != self.who or state["bn"] != body["n"]:
            raise ValueError("duplicate ticket_id")
        self._mine[mine] = body                               # a ticket I issued: mine to write again
        if self._watching.acquire(False):                     # [THE_ISSUER_SAYS_WHO_WON_V1] from its first ticket on
            threading.Thread(target=self._see_my_tickets_won, daemon=True, name="worldshepherd-issuer-" + self.who).start()

    def _see_my_tickets_won(self):
        """[THE_ISSUER_SAYS_WHO_WON_V1] One held read on (worldshepherd, won.<who>): each ticket this SARA issued writes
        there, once, when it is won. What is seen is kept with the issue, and said when the issue is made again in a
        new memory -- so a ticket this SARA saw won comes back consumed by its winner, whoever claims to have held it."""
        seen_in, last = None, 0
        while True:
            def read(memory):
                return memory, memory.read(SERVICE, "won." + self.who, "", after=(last if memory is seen_in else 0), wait_s=ribbit.HOLD_S)
            try:
                memory, cells = self._in_the_memory(read)
            except (ribbit.RibbitError, NoRamManager) as e:
                print(time.strftime("%H:%M:%S ") + "[WS-ISSUER] %s: the read held on its own tickets ended (%s); what it saw won so far is kept" % (self.who, e),
                      file=sys.stderr, flush=True)
                return
            if memory is not seen_in:
                seen_in, last = memory, 0
            for c in cells:
                last = max(last, c["id"])
                mine, state = ("issue", c["instance"]), c["bag"]
                if state and state.get("won") and mine in self._mine and "won" not in self._mine[mine]:
                    self._mine[mine] = dict(self._mine[mine], won=state["won"], wn=state["wn"], at=state["at"])

    def claim_as_it_stands(self, ticket_id, ticket_sha256, now):
        """(TicketClaim, the ticket's state as it answered this claim)"""
        n, made, began = next(self._n), [], time.monotonic()
        # [A_HELD_CLAIM_AGES_V1] (the acceptance study, long:two-memories, 2026-11-25: a claim held 310 s by a link that
        # was down was answered "claimed" 10 s after its ticket expired.) `now` is the caller's clock when it called. A
        # call that is made again -- its session ended under it -- has been held, and is that much older: what it says
        # then says so. A call made once is `now`, exactly.
        def call(memory):
            made.append(1)
            body = {"who": self.who, "ticket": ticket_id, "sha": ticket_sha256, "n": n,
                    "at": float(now) + (time.monotonic() - began if len(made) > 1 else 0.0)}
            return self._ticket(memory, "claim", body), body
        r, body = self._in_the_memory(call)
        state, outcome = r["state"], r["outcome"]
        if outcome == "claimed":
            self._mine[("claim", ticket_id)] = dict(body, at=float(state["at"]), held=1)   # a claim I won: mine to write again
            return TicketClaim(True, ticket_id, "ticket claimed for one-time host execution", float(state["at"])), state
        return TicketClaim(False, ticket_id, {"absent": "ticket not registered in this ledger",
                                              "digest": "ticket digest does not match registered ticket",
                                              "expired": "ticket expired", "consumed": "ticket already consumed"}[outcome]), state

    def claim(self, *, ticket_id, ticket_sha256, now):
        return self.claim_as_it_stands(ticket_id, ticket_sha256, now)[0]

    def status(self, ticket_id):
        def read(memory):                                     # [EACH_FACT_ITS_OWN_CELL_V1] two reads of one cell each: its issue, then its win
            for c in memory.read(SERVICE, "ticket", ticket_id):
                if c["bag"]:
                    for w in memory.read(SERVICE, "won." + c["bag"]["by"], ticket_id):
                        if w["bag"]: return dict(c["bag"], **w["bag"])
                    return dict(c["bag"], won="", wn=0, at=0)
        return self.status_of(ticket_id, self._in_the_memory(read))

    @staticmethod
    def status_of(ticket_id, state):
        """the contract's status, from a ticket's state (read from its cells, or as its claim operation answered)"""
        if state is None:
            return {"known": False, "ticket_id": ticket_id}
        return {"known": True, "ticket_id": ticket_id, "ticket_sha256": state["sha"],
                "expires_at": float(state["exp"]), "consumed": bool(state["won"]),
                "consumed_at": float(state["at"]) if state["won"] else None}

    def record(self, ticket_id):
        """the ticket as the SharedClaimRecord the contract names: read from the ticket, kept nowhere"""
        s = self.status(ticket_id)
        if not s["known"]:
            raise KeyError(ticket_id)
        return SharedClaimRecord(ticket_sha256=s["ticket_sha256"], expires_at=s["expires_at"],
                                 consumed_at=s["consumed_at"])
