# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
#
# ticket_sql.py -- SARA's PostgresClaimStore, unmodified, with no PostgreSQL. [THE_SQL_CALLS_ARE_CELLS_V1]
#
# Brandon's distributed store (worldshepherd_sara/connector_ticket_postgres.py) does not know PostgreSQL: it is
# handed a connection factory and makes four calls on what the factory returns. Those four calls are the whole of
# its conversation with a database, so they are converted here and the store itself is left exactly as written:
#
#     store = PostgresClaimStore(ticket_sql.connection_factory("HOST:PORT"))      # one fdf-ram; None on a node: the app's RAM
#
#   its statement                                                 here (ribbit_claim_store.py does each)
#   CREATE_TABLE_SQL   CREATE TABLE IF NOT EXISTS ...             nothing: cells need no table
#   REGISTER_SQL       INSERT ... ON CONFLICT DO NOTHING          this SARA's issue cell, written; rowcount 1 when it is
#                                                                 the ticket, 0 when another issue was there first
#   CLAIM_SQL          UPDATE ... WHERE consumed_at IS NULL ...   this SARA's claim cell, written; the row
#                      RETURNING expires_at, consumed_at          (expires_at, consumed_at) when it won, no row when not
#   STATUS_SQL         SELECT ticket_sha256, expires_at, ...      the ticket's cells, read; the row, or no row
#
# A statement is recognized by being one of those four, as the store's own module spells them. Anything else is
# refused by name: this is not a SQL engine and does not pretend to be one. connection(), commit(), rollback() and
# close() send nothing: the store's "connection per call" is an object over the one permanent session. Nothing is
# kept here between statements: a refused claim's status query is a read of the memory, as the store wrote it.
from worldshepherd_sara import connector_ticket_postgres as stock

from ribbit_claim_store import RibbitClaimStore

BUILD = "worldshepherd-ribbit 0.7 [THE_SQL_CALLS_ARE_CELLS_V1]"


class NotOneOfTheFour(Exception):
    pass


class _Cursor:
    def __init__(self, cells):
        self._cells = cells; self._row = None; self.rowcount = -1

    def execute(self, sql, params=()):
        self._row = None; self.rowcount = -1
        if sql == stock.CREATE_TABLE_SQL:
            return
        if sql == stock.REGISTER_SQL:
            ticket_id, sha, exp = params
            try:
                self._cells.register(ticket_id=ticket_id, ticket_sha256=sha, expires_at=exp); self.rowcount = 1
            except ValueError:                   # "duplicate ticket_id": the statement's ON CONFLICT DO NOTHING
                self.rowcount = 0
            return
        if sql == stock.CLAIM_SQL:
            now, ticket_id, sha, _now_again = params
            claim, ticket = self._cells.claim_as_it_stands(ticket_id, sha, now)
            self._row = (float(ticket.issue["bag"]["exp"]), claim.consumed_at) if claim.ok else None
            self.rowcount = 1 if claim.ok else 0
            return
        if sql == stock.STATUS_SQL:
            (ticket_id,) = params
            s = self._cells.status(ticket_id)
            self._row = (s["ticket_sha256"], s["expires_at"], s["consumed_at"]) if s["known"] else None
            return
        raise NotOneOfTheFour("ticket_sql: not one of PostgresClaimStore's four statements: %r" % " ".join(sql.split())[:120])

    def fetchone(self):
        return self._row


class _Connection:
    def __init__(self, cells):
        self._cells = cells

    def cursor(self):
        return _Cursor(self._cells)

    def commit(self): pass
    def rollback(self): pass
    def close(self): pass


def connection_factory(where=None, name="sara"):
    """what PostgresClaimStore is constructed with: called for a connection, it gives one over this SARA's session to
    the app's RAM (where: one fdf-ram's HOST:PORT, or None on a node). The SARA's own truth is reasserted as ribbit_claim_store.py says."""
    cells = RibbitClaimStore(where, name=name)

    def factory():
        return _Connection(cells)
    factory.cells = cells                      # the SARA's store itself, for sara_ui.router(factory.cells)
    return factory
