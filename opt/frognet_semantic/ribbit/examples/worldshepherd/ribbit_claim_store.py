# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
#
# ribbit_claim_store.py -- WorldShepherd SARA's shared ticket-claim store, on WorldShepherd's own RAM manager.
#
# It is a SharedClaimStore (worldshepherd_sara/connector_ticket_shared.py): register / claim / status, the contract
# Brandon's PostgresClaimStore and ThreadSafeSharedClaimStore implement. Here it is three lines of work: the tickets
# are cells in worldshepherd-ram (worldshepherd_ram.cpp), which owns them and takes one ask at a time per ticket.
#
#   register, claim   one ask on this SARA's permanent session (op=ticket), answered on that session
#   status            a read of the ticket's two cells: (worldshepherd, ticket.<id>, issue) and (..., claim)
#
# Every ask carries this store's own name and its own count ("who", "n"), so it is this SARA's request and nobody
# else's: the session answers an identical request in flight with that request's one answer ([CLIENT_COALESCES_V1]),
# and two threads claiming one ticket would otherwise both be told they won.
#
# A failure raises (ribbit.RibbitError names the RAM manager, the call and the cause; a region error is raised with
# what it said). There is no second way to claim.
import itertools
import json
import secrets

from worldshepherd_sara.connector_ticket_lifecycle import TicketClaim
from worldshepherd_sara.connector_ticket_shared import SharedClaimRecord

BUILD = "worldshepherd-ribbit 0.4 [THE_REGION_OWNS_THE_TICKETS_V1]"
API = "/worldshepherd-api"
SERVICE = "worldshepherd"


class RibbitClaimStore:
    def __init__(self, memory, name="sara"):
        self.memory = memory                                  # ribbit.Memory: this participant's one session
        self.who = "%s-%s" % (name, secrets.token_hex(6))
        self._n = itertools.count(1)

    def _ask(self, **ask):
        ask.update(who=self.who, n=next(self._n))
        reply = self.memory.call("POST", self.memory.api + "?op=ticket", json.dumps(ask))
        result = json.loads(reply["result_json"]) if reply.get("ok") else reply
        if "error" in result:
            raise RuntimeError("%s ticket %s %s: %s" % (self.memory.where, ask["do"], ask["id"], result["error"]))
        return result

    def register(self, *, ticket_id, ticket_sha256, expires_at):
        r = self._ask(do="register", id=ticket_id, sha=ticket_sha256, exp=float(expires_at))
        if not r["ok"]:
            raise ValueError(r["reason"])

    def claim(self, *, ticket_id, ticket_sha256, now):
        r = self._ask(do="claim", id=ticket_id, sha=ticket_sha256, now=float(now))
        if not r["ok"]:
            return TicketClaim(False, ticket_id, r["reason"])
        return TicketClaim(True, ticket_id, "ticket claimed for one-time host execution", float(r["consumed_at"]))

    def status(self, ticket_id):
        cells = {c["instance"]: c["bag"] for c in self.memory.read(SERVICE, "ticket." + ticket_id)}
        if "issue" not in cells:
            return {"known": False, "ticket_id": ticket_id}
        claim = cells.get("claim")
        return {"known": True, "ticket_id": ticket_id, "ticket_sha256": cells["issue"]["sha"],
                "expires_at": float(cells["issue"]["exp"]), "consumed": claim is not None,
                "consumed_at": None if claim is None else float(claim["at"])}

    def record(self, ticket_id):
        """the ticket as the SharedClaimRecord the contract names: derived from the cells, kept nowhere"""
        s = self.status(ticket_id)
        if not s["known"]:
            raise KeyError(ticket_id)
        return SharedClaimRecord(ticket_sha256=s["ticket_sha256"], expires_at=s["expires_at"],
                                 consumed_at=s["consumed_at"])
