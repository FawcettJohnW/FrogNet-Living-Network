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
# core/game_role.py - gamehost role election criteria (score/evaluate ONLY).
#
# A gamehost serves the UnREST game origin: the proxy answers POST /game on port 80
# from working memory (turn-based, tiny state). There is NO separate daemon and no
# hard codec/library gate the way mediahost needs ffmpeg+libvpx or databasehost needs
# mysql - every FrogNet node whose proxy carries the game-origin hook can host a table.
# So eligibility is trivial (reachable FrogNet host) and the pond elects ONE gamehost
# so every Communicator resolves the SAME table host; it floats like databasehost.
#
# Pure: no I/O, no probes. The gather-and-apply mechanics live in frognet_role_elect;
# this object only scores a handed-in candidate list. Memory, not messages.
from .unrest_handler import UnRESTHandler  # the one UnREST handler interface


class GameRoleHandler(UnRESTHandler):
    ROLE_NAME = "boardgame"
    CANDIDATE_TYPE = "GameCandidate"
    DEFAULT_PORT = 80                 # the proxy serves /game on 80, same as any node

    @staticmethod
    def _ip_to_int(ip):
        try:
            a, b, c, d = (int(x) for x in ip.split("."))
            return (a << 24) | (b << 16) | (c << 8) | d
        except Exception:
            return 0

    def score(self, cand):
        """Eligibility ONLY - a flat constant for every reachable candidate. Game
        hosting needs no special hardware and the state is tiny, so there is no real
        fitness difference between nodes to rank on. Returning load-weighted scores
        here would let loadavg jitter outrank the IP tiebreak and FLAP the role
        between nodes every pass (the hysteresis failure mode). Flat score => the
        highest-IP tiebreak in evaluate() is the SOLE decider: one stable gamehost
        that only moves when the current holder actually leaves. Float by IP, exactly
        like databasehost."""
        return -1.0 if not cand.get("lan_ip") else 1.0

    def evaluate(self, hosts_list, lan_list):
        """Elected pond-wide over the WAN-inclusive hosts_list (like databasehost): one
        gamehost for the whole pond so every Communicator, on any LAN, resolves the same
        table host over the wg overlay. Pure: score each, drop ineligible, pick best,
        tiebreak highest IP. lan_list accepted for signature uniformity, ignored."""
        best = None
        for cand in hosts_list or []:
            ip = cand.get("lan_ip", "")
            if not ip:
                continue
            sc = self.score(cand)
            if sc < 0:
                continue
            key = (sc, self._ip_to_int(ip))
            if best is None or key > best[0]:
                best = (key, cand)
        return best[1] if best else None
