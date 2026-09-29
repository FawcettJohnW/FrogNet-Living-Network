################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#                                                              #
#  SPDX-License-Identifier: GPL-2.0-only                       #
#                                                              #
#  This program is free software; you can redistribute it      #
#  and/or modify it under the terms of the GNU General Public  #
#  License as published by the Free Software Foundation;       #
#  version 2 of the License, and no other version.             #
#                                                              #
#  This program is distributed in the hope that it will be     #
#  useful, but WITHOUT ANY WARRANTY; without even the implied  #
#  warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR     #
#  PURPOSE.  See the GNU General Public License for details.   #
#                                                              #
#  See COPYRIGHT and LICENSE at the root of this tree.         #
################################################################
"""games_lobby.py - the Games hub data layer. Games are TUPLES in the shared DB; listing
games in progress is a select across each game's dimension (SensorType=SERVICE, var="state"),
filtered client-side. No server op: reading the board tuple IS watching; join is the codex's
join op. Lobby chat is a tuple too (chat_send/chat_read, same as everywhere)."""
from __future__ import annotations
import time

# Each installed game's SERVICE (SensorType bucket) + display title + the board var.
# These mirror what each game's tuple store writes (bg_tuple_store: SERVICE="backgammon",
# var="state"/"presence"). The card games via games-common use SERVICE=<game name>.
GAME_SERVICES = {
    "backgammon":  "Backgammon",
    "connectfour": "Connect Four",
    "hearts":      "Hearts",
    "liarsdice":   "Liar's Dice",
    "reversi":     "Reversi",
    "boardgame":   "Board Games",
}
PRESENCE_WINDOW_MS = 60_000
TABLE_FRESH_S = 1800   # a table not touched in 30 min has aged out of the lobby


def _open_seats(g: dict) -> int:
    seats = g.get("seats") or []
    mx = g.get("max_players")
    if mx is None:
        # boardgame/backgammon: 2-seat games key seats as a dict {w:..,b:..} or list
        mx = 2
    return max(0, int(mx) - len(seats))


def _ply(g: dict) -> int:
    return len(g.get("history") or [])


def _live_presence(g: dict) -> list:
    pres = g.get("presence") or {}
    now = int(time.time() * 1000)
    out = []
    for who, info in pres.items():
        try:
            if now - int(info.get("ts", 0)) <= PRESENCE_WINDOW_MS:
                out.append({"who": who, "role": info.get("role", "watcher")})
        except Exception:
            pass
    return out


def list_tables(T, dbhost) -> list:
    """SELECT * across all game services, classify each live table. Returns a list of:
       {service, title, table, phase, seats, open_seats, ply, turn, winner, here[]}.
    Joinable = phase 'lobby' with open_seats>0. Watchable = phase 'play'. Over = 'gameover'."""
    rows = []
    for service, title in GAME_SERVICES.items():
        try:
            for r in T.get(service, "state", dbhost=dbhost, fresh_s=TABLE_FRESH_S):  # select, freshness-filtered
                g = r.get("value") or {}
                if not isinstance(g, dict):
                    continue
                table = (r.get("scope") or "").replace("session:", "")
                phase = g.get("phase") or ("play" if g.get("turn") else "lobby")
                rows.append({
                    "service": service, "title": title, "table": table,
                    "phase": phase,
                    "seats": list(g.get("seats") or []),
                    "open_seats": _open_seats(g),
                    "ply": _ply(g),
                    "turn": g.get("turn"),
                    "winner": g.get("winner"),
                    "here": _live_presence(g),
                })
        except Exception:
            continue
    return rows


def classify(rows: list) -> dict:
    """Split into the three hub sections."""
    joinable = [r for r in rows if r["phase"] == "lobby" and r["open_seats"] > 0]
    watchable = [r for r in rows if r["phase"] == "play"]
    over = [r for r in rows if r["phase"] == "gameover"]
    return {"joinable": joinable, "watching": watchable, "over": over}
